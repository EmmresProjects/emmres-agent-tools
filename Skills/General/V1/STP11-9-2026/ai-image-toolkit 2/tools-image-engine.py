#!/usr/bin/env python3
"""Image operations for ai-image-toolkit (requires Pillow; see tools-requirements.txt).

Deterministic, local image work: inspection, QA metrics, comparison, export, cropping,
layout composition, masks, compositing, colour adjustment, restoration helpers, privacy
redaction, dataset curation and provenance sidecars. Nothing here calls a generative model.
Heuristic results (artifact scan, authenticity, quality flags) are indicators, not verdicts.
"""
import datetime as _dt
import hashlib
import importlib.util
import io
import json
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent


def _load_common():
    name = "aiit_common"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _HERE / "tools-common.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


C = _load_common()
ToolError, P, op, write_json, slugify, sha256_file = C.ToolError, C.P, C.op, C.write_json, C.slugify, C.sha256_file
LAYOUTS = C.load_asset("asset-layout-presets.json")
SPECS = sorted(LAYOUTS["delivery_specs"].keys())
LAYOUT_NAMES = sorted(LAYOUTS["layouts"].keys())
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
BLEND_MODES = ["normal", "multiply", "screen", "overlay", "darken", "lighten", "difference", "add"]


def _mods():
    C.require_pillow()
    from PIL import Image, ImageFilter, ImageChops, ImageDraw, ImageFont, ImageStat, ImageEnhance, ImageOps, features
    try:
        from PIL import ImageCms
    except ImportError:
        ImageCms = None
    return SimpleNamespace(Image=Image, ImageFilter=ImageFilter, ImageChops=ImageChops, ImageDraw=ImageDraw,
                           ImageFont=ImageFont, ImageStat=ImageStat, ImageEnhance=ImageEnhance, ImageOps=ImageOps,
                           features=features, ImageCms=ImageCms)


def _open(path, cfg):
    M = _mods()
    C.check_image_file(path, cfg)
    try:
        im = M.Image.open(path)  # header only; pixels are not decoded yet
    except Exception as e:  # corrupt or unsupported file
        raise ToolError("invalid_input", f"cannot read image '{Path(path).name}': {type(e).__name__}",
                        field="image", hint="Check the file opens in an image viewer; re-export if corrupt.")
    if im.width * im.height > cfg["limits"]["max_pixels"]:
        raise ToolError("invalid_input", f"image '{Path(path).name}' has more pixels than limits.max_pixels",
                        field="image", hint="Downscale first or raise limits.max_pixels in the config.")
    try:
        im.load()
    except Exception as e:
        raise ToolError("invalid_input", f"cannot decode image '{Path(path).name}': {type(e).__name__}",
                        field="image", hint="The file may be truncated or corrupt.")
    return im


def _save(im, path, **kw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        im.save(path, **kw)
    except (ValueError, KeyError) as e:  # Pillow: unknown extension or unsupported mode
        raise ToolError("invalid_input", f"cannot save '{path.name}': {e}", field="output",
                        hint="Use a supported extension such as .png, .jpg, .webp or .tiff.")
    return path


def _stats(im):
    M = _mods()
    s = M.ImageStat.Stat(im)
    return s


def _luma_mean_std(im):
    M = _mods()
    s = M.ImageStat.Stat(im.convert("L"))
    return s.mean[0], s.stddev[0]


def _thumb(im, side=512):
    t = im.convert("RGB")
    t.thumbnail((side, side))
    return t


def _dhash(im):
    M = _mods()
    g = im.convert("L").resize((9, 8), M.Image.Resampling.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
    return bits


def _hamming(a, b):
    return bin(a ^ b).count("1")


def _meta(im):
    info = dict(im.info)
    exif = {}
    try:
        ex = im.getexif()
        exif = {k: v for k, v in ex.items()}
    except Exception:
        exif = {}
    gps = 34853 in exif
    software = exif.get(305)
    text_keys = [k for k in info.keys() if isinstance(info.get(k), str)]
    return {"exif_present": bool(exif), "gps_present": gps, "software": software,
            "icc_present": "icc_profile" in info, "png_text_keys": sorted(text_keys),
            "dpi": tuple(round(float(x), 1) for x in info["dpi"]) if "dpi" in info else None}


def _inspect(path, cfg):
    im = _open(path, cfg)
    m = _meta(im)
    return {"file": Path(path).name, "format": im.format, "mode": im.mode, "width": im.width, "height": im.height,
            "has_alpha": im.mode in ("RGBA", "LA") or "transparency" in im.info,
            "bytes": Path(path).stat().st_size, **m}


# ----------------------------------------------------------------- image.inspect (utility)

def op_inspect(p, ctx):
    files = p["images"]
    return {"images": [_inspect(f, ctx.cfg) for f in files]}


# ----------------------------------------------------------------- delivery audit

def _spec(name, custom, ctx):
    spec = dict(LAYOUTS["delivery_specs"].get(name, {})) if name else {}
    if custom:
        spec.update(custom)
    return spec


def _delivery_checks(path, spec, cfg):
    im = _open(path, cfg)
    info = _inspect(path, cfg)
    checks = []
    def add(name, ok, detail, sev="fail"):
        checks.append({"check": name, "status": "pass" if ok else sev, "detail": detail})
    if "min_width" in spec:
        add("min_width", im.width >= spec["min_width"], f"{im.width}px (min {spec['min_width']})")
    if "min_height" in spec:
        add("min_height", im.height >= spec["min_height"], f"{im.height}px (min {spec['min_height']})")
    if spec.get("square"):
        add("square", im.width == im.height, f"{im.width}x{im.height}")
    if "formats" in spec:
        fmt = (im.format or "").lower()
        allowed = [f.lower() for f in spec["formats"]]
        ok = fmt in allowed or (fmt == "jpeg" and "jpg" in allowed)
        add("format", ok, f"{im.format} (allowed {', '.join(spec['formats'])})")
    if "max_bytes" in spec:
        add("max_bytes", info["bytes"] <= spec["max_bytes"], f"{info['bytes']} bytes (max {spec['max_bytes']})")
    if spec.get("require_srgb"):
        if not info["icc_present"]:
            add("colour_space", True, "no embedded profile; treated as sRGB", sev="warn")
        else:
            prof = im.info.get("icc_profile", b"")
            ok = b"sRGB" in prof or b"sRGB" in prof.lower()
            add("colour_space", ok, "embedded profile looks like sRGB" if ok else "embedded profile is not labelled sRGB; convert with image.color_convert", sev="warn")
    if "min_dpi" in spec:
        dpi = info["dpi"]
        ok = bool(dpi) and min(dpi) >= spec["min_dpi"]
        add("min_dpi", ok, f"dpi {dpi}" if dpi else "no DPI metadata", sev="warn")
    leak = info["gps_present"]
    add("no_gps_metadata", not leak, "GPS tag present" if leak else "no GPS tag", sev="warn")
    return checks, info


def op_delivery_audit(p, ctx):
    spec = _spec(p.get("spec"), p.get("custom"), ctx)
    if not spec:
        raise ToolError("invalid_input", "provide 'spec' or 'custom' with at least one rule", field="spec")
    out = []
    for f in p["images"]:
        checks, info = _delivery_checks(f, spec, ctx.cfg)
        out.append({"file": Path(f).name, "checks": checks,
                    "result": "fail" if any(c["status"] == "fail" for c in checks) else
                    ("warn" if any(c["status"] == "warn" for c in checks) else "pass"),
                    "info": {k: info[k] for k in ("format", "width", "height", "bytes")}})
    summary = {s: sum(1 for r in out if r["result"] == s) for s in ("pass", "warn", "fail")}
    path = ctx.out_path(p.get("output"), "delivery-audit.json")
    write_json(path, {"spec": spec, "results": out, "summary": summary})
    ctx.record(path)
    return {"spec": spec, "summary": summary, "results": out, "report_file": str(path)}


# ----------------------------------------------------------------- quality metrics and artifact scan

def _quality(path, cfg):
    M = _mods()
    im = _thumb(_open(path, cfg), 1024)
    L = im.convert("L")
    mean, std = _luma_mean_std(im)
    hist = L.histogram()
    total = sum(hist)
    clipped = (sum(hist[:6]) + sum(hist[250:])) / total * 100
    sharp = _stats(L.filter(M.ImageFilter.FIND_EDGES)).var[0]
    blur = L.filter(M.ImageFilter.GaussianBlur(1))
    noise = _stats(M.ImageChops.difference(L, blur)).stddev[0]
    px = list(im.getdata())
    rg = [r - g for r, g, _ in px]
    yb = [0.5 * (r + g) - b for r, g, b in px]
    def sd(x):
        m = sum(x) / len(x)
        return math.sqrt(sum((v - m) ** 2 for v in x) / len(x)), m
    srg, mrg = sd(rg)
    syb, myb = sd(yb)
    colourful = math.sqrt(srg ** 2 + syb ** 2) + 0.3 * math.sqrt(mrg ** 2 + myb ** 2)
    flags = []
    if mean < 40:
        flags.append("too_dark")
    if mean > 215:
        flags.append("too_bright")
    if clipped > 2.0:
        flags.append("clipped_highlights_or_shadows")
    if sharp < 50:
        flags.append("soft_or_low_detail")
    if noise > 6:
        flags.append("noisy")
    return {"file": Path(path).name, "mean_luminance": round(mean, 2), "contrast_std": round(std, 2),
            "clipped_percent": round(clipped, 2), "sharpness_edge_variance": round(sharp, 2),
            "noise_estimate": round(noise, 3), "colourfulness": round(colourful, 2), "flags": flags}


def op_quality_metrics(p, ctx):
    rows = [_quality(f, ctx.cfg) for f in p["images"]]
    return {"metrics": rows,
            "note": "Objective measurements with rule-of-thumb thresholds (dark <40, bright >215, clipped >2%, soft <50, noisy >6). They do not judge composition or aesthetics; a person must."}


def _seam_scan(L):
    w, h = L.size
    px = L.load()
    col_d = []
    for x in range(w - 1):
        s = sum(abs(px[x + 1, y] - px[x, y]) for y in range(h))
        col_d.append(s / h)
    row_d = []
    for y in range(h - 1):
        s = sum(abs(px[x, y + 1] - px[x, y]) for x in range(w))
        row_d.append(s / w)
    med_c = sorted(col_d)[len(col_d) // 2] if col_d else 0
    med_r = sorted(row_d)[len(row_d) // 2] if row_d else 0
    wrap_c = sum(abs(px[0, y] - px[w - 1, y]) for y in range(h)) / h
    wrap_r = sum(abs(px[x, 0] - px[x, h - 1]) for x in range(w)) / w
    return col_d, row_d, med_c, med_r, wrap_c, wrap_r


def _artifact_report(path, cfg):
    M = _mods()
    im = _open(path, cfg)
    L = _thumb(im, 512).convert("L")
    t = cfg["thresholds"]
    mean, std = _luma_mean_std(L)
    findings = []
    if std < t["blank_stddev"]:
        findings.append({"type": "blank", "severity": "fail", "detail": f"luminance stddev {std:.2f}"})
    col_d, row_d, mc, mr, wc, wr = _seam_scan(L)
    seams = []
    for x, d in enumerate(col_d):
        if mc > 0 and d / mc >= t["seam_ratio"] and d > 2:
            seams.append({"axis": "vertical", "position": x, "ratio": round(d / mc, 2)})
    for y, d in enumerate(row_d):
        if mr > 0 and d / mr >= t["seam_ratio"] and d > 2:
            seams.append({"axis": "horizontal", "position": y, "ratio": round(d / mr, 2)})
    seams = sorted(seams, key=lambda s: -s["ratio"])[:10]
    if seams:
        findings.append({"type": "seam_candidates", "severity": "warn",
                         "detail": f"{len(seams)} strong discontinuities; they may be real edges", "items": seams})
    w, h = L.size
    px = L.load()
    flat = total = 0
    for by in range(0, h - 15, 16):
        for bx in range(0, w - 15, 16):
            total += 1
            region = L.crop((bx, by, bx + 16, by + 16))
            if _stats(region).stddev[0] < 1.0:
                flat += 1
    flat_pct = round(100.0 * flat / total, 2) if total else 0.0
    if flat_pct > 30:
        findings.append({"type": "flat_regions", "severity": "warn",
                         "detail": f"{flat_pct}% of 16px blocks are almost uniform (possible smearing or missing texture)"})
    if mc and wc / mc >= t["seam_ratio"]:
        findings.append({"type": "tiling_seam_lr", "severity": "warn", "detail": f"left-right wrap ratio {wc / mc:.2f}"})
    if mr and wr / mr >= t["seam_ratio"]:
        findings.append({"type": "tiling_seam_tb", "severity": "warn", "detail": f"top-bottom wrap ratio {wr / mr:.2f}"})
    return {"file": Path(path).name, "findings": findings, "flat_block_percent": flat_pct,
            "left_right_wrap_ratio": round(wc / mc, 2) if mc else None,
            "top_bottom_wrap_ratio": round(wr / mr, 2) if mr else None}


def op_artifact_scan(p, ctx):
    rows = [_artifact_report(f, ctx.cfg) for f in p["images"]]
    return {"reports": rows,
            "note": "Heuristic scan for blank frames, seams, tiling wrap and flat regions. It cannot find malformed hands or warped text; review those by eye."}


# ----------------------------------------------------------------- compare and regression

def op_compare(p, ctx):
    M = _mods()
    a = _open(p["image_a"], ctx.cfg).convert("RGB")
    b = _open(p["image_b"], ctx.cfg).convert("RGB")
    resized = False
    if a.size != b.size:
        b = b.resize(a.size, M.Image.Resampling.LANCZOS)
        resized = True
        ctx.warn("sizes differ; image_b was resized to image_a for comparison")
    diff = M.ImageChops.difference(a, b)
    mad = sum(_stats(diff).mean) / 3.0
    gray = diff.convert("L")
    thr = p["threshold"]
    mask = gray.point(lambda v: 255 if v > thr else 0)
    changed = sum(1 for v in mask.getdata() if v) if a.size[0] * a.size[1] <= 1_000_000 else None
    if changed is None:
        hist = mask.histogram()
        changed = hist[255]
    pct = round(100.0 * changed / (a.size[0] * a.size[1]), 3)
    bbox = mask.getbbox()
    hd = _hamming(_dhash(a), _dhash(b))
    heat = gray.point(lambda v: min(255, v * 4))
    heat_path = ctx.out_path(p.get("output"), "diff-heatmap.png", inputs=[p["image_a"], p["image_b"]])
    _save(heat, heat_path)
    ctx.record(heat_path)
    result = {"size": list(a.size), "resized_b": resized, "mean_abs_diff": round(mad, 3),
              "changed_percent": pct, "changed_threshold": thr, "changed_bbox": list(bbox) if bbox else None,
              "dhash_distance": hd, "heatmap_file": str(heat_path)}
    if p["mode"] == "regression":
        t = ctx.cfg["thresholds"]
        checks = {
            "mean_abs_diff": mad <= t["regression_mean_abs_diff"],
            "changed_percent": pct <= t["regression_changed_percent"],
            "perceptual_hash": hd <= t["regression_hash_distance"],
        }
        result["regression"] = {"pass": all(checks.values()), "checks": checks,
                                "limits": {k: t[v] for k, v in [("mean_abs_diff", "regression_mean_abs_diff"),
                                                                 ("changed_percent", "regression_changed_percent"),
                                                                 ("perceptual_hash", "regression_hash_distance")]}}
    result["note"] = "Pixel and perceptual difference only. Intended changes and unintended changes look identical here; review the heatmap."
    return result


# ----------------------------------------------------------------- contact sheet and before/after

def _font(M, size, cfg):
    path = cfg.get("fonts", {}).get("truetype") or ""
    try:
        if path:
            return M.ImageFont.truetype(path, size)
        return M.ImageFont.load_default(size=size)
    except (OSError, TypeError):
        return M.ImageFont.load_default()


def op_contact_sheet(p, ctx):
    M = _mods()
    files = p["images"]
    if len(files) > ctx.limits["max_contact_sheet_items"]:
        raise ToolError("invalid_input", f"too many images for a contact sheet (limit {ctx.limits['max_contact_sheet_items']})",
                        field="images")
    cols = p["columns"]
    thumb = p["thumb"]
    label_h = 34 if p["labels"] else 0
    rows = math.ceil(len(files) / cols)
    bg = C.parse_hex(p["background"], "background")
    sheet = M.Image.new("RGB", (cols * (thumb + 16) + 16, rows * (thumb + label_h + 16) + 16), bg)
    draw = M.ImageDraw.Draw(sheet)
    font = _font(M, 16, ctx.cfg)
    for i, f in enumerate(files):
        im = _thumb(_open(f, ctx.cfg), thumb)
        r, c = divmod(i, cols)
        x = 16 + c * (thumb + 16)
        y = 16 + r * (thumb + label_h + 16)
        ox = x + (thumb - im.width) // 2
        oy = y + (thumb - im.height) // 2
        sheet.paste(im, (ox, oy))
        if p["labels"]:
            draw.text((x, y + thumb + 6), Path(f).stem[:36], fill=(230, 230, 230), font=font)
    out = ctx.out_path(p.get("output"), "contact-sheet.png", inputs=files)
    _save(sheet, out)
    ctx.record(out)
    return {"sheet_file": str(out), "items": len(files), "grid": [rows, cols], "size": list(sheet.size)}


def op_before_after(p, ctx):
    M = _mods()
    before = _open(p["before"], ctx.cfg).convert("RGB")
    after = _open(p["after"], ctx.cfg).convert("RGB")
    if after.size != before.size:
        after = after.resize(before.size, M.Image.Resampling.LANCZOS)
        ctx.warn("after image resized to the before image size")
    div = p["divider"]
    label_h = 44
    W = before.width * 2 + div
    sheet = M.Image.new("RGB", (W, before.height + label_h), (20, 20, 20))
    sheet.paste(before, (0, label_h))
    sheet.paste(after, (before.width + div, label_h))
    draw = M.ImageDraw.Draw(sheet)
    font = _font(M, 22, ctx.cfg)
    draw.text((12, 10), p["label_before"], fill=(240, 240, 240), font=font)
    draw.text((before.width + div + 12, 10), p["label_after"], fill=(240, 240, 240), font=font)
    out = ctx.out_path(p.get("output"), "before-after.png", inputs=[p["before"], p["after"]])
    _save(sheet, out)
    ctx.record(out)
    return {"file": str(out), "size": list(sheet.size)}


# ----------------------------------------------------------------- export and optimise

def _fmt_key(name):
    n = name.lower()
    return {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "tif": "TIFF", "tiff": "TIFF", "avif": "AVIF"}.get(n)


def op_export(p, ctx):
    M = _mods()
    src = _open(p["image"], ctx.cfg)
    formats = [f.strip().lower() for f in p["formats"]]
    bad = [f for f in formats if _fmt_key(f) is None]
    if bad:
        raise ToolError("invalid_input", f"unsupported format(s) {bad}", field="formats")
    sizes = p.get("sizes") or [None]
    bg = C.parse_hex(p["background"], "background")
    results, warnings = [], []
    stem = Path(p["image"]).stem
    for fmt in formats:
        key = _fmt_key(fmt)
        if key == "AVIF" and not M.features.check("avif"):
            warnings.append("AVIF is not supported by this Pillow build; skipped")
            continue
        for s in sizes:
            im = src
            if s:
                scale = s / max(src.size)
                im = src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))),
                                M.Image.Resampling.LANCZOS)
            lossy = key in ("JPEG", "WEBP", "AVIF")
            if key == "JPEG" and im.mode in ("RGBA", "LA", "P"):
                base = M.Image.new("RGB", im.size, bg)
                base.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3])
                im = base
            elif key == "JPEG" and im.mode != "RGB":
                im = im.convert("RGB")
            name = f"{stem}-{im.width}x{im.height}.{fmt if fmt != 'jpeg' else 'jpg'}"
            out = ctx.out_path(None, name)
            kw = {}
            if key == "PNG":
                kw = {"optimize": bool(p.get("optimize"))}
            elif key == "JPEG":
                kw = {"quality": p["quality"], "optimize": bool(p.get("optimize")), "progressive": bool(p.get("optimize"))}
            elif key in ("WEBP", "AVIF"):
                kw = {"quality": p["quality"]}
                if key == "WEBP" and p.get("optimize"):
                    kw["method"] = 6
            elif key == "TIFF":
                kw = {"compression": "tiff_deflate"}
            if not p.get("strip_metadata", True) and src.info.get("icc_profile"):
                kw["icc_profile"] = src.info["icc_profile"]
            if p.get("dpi"):
                kw["dpi"] = (p["dpi"], p["dpi"])
            used_q = p["quality"] if lossy else None
            if p.get("max_bytes") and lossy:
                lo, hi, best = 10, 95, None
                while lo <= hi:
                    q = (lo + hi) // 2
                    kw["quality"] = q
                    buf = io.BytesIO()
                    im.save(buf, format=key, **kw)
                    size = buf.tell()
                    if size <= p["max_bytes"]:
                        best = (q, buf.getvalue())
                        lo = q + 1
                    else:
                        hi = q - 1
                if best is None:
                    warnings.append(f"{name}: could not reach max_bytes even at quality 10; kept quality 10")
                    kw["quality"] = 10
                    buf = io.BytesIO()
                    im.save(buf, format=key, **kw)
                    out.write_bytes(buf.getvalue())
                    used_q = 10
                else:
                    used_q = best[0]
                    out.write_bytes(best[1])
            else:
                if lossy:
                    kw["quality"] = p["quality"]
                im.save(out, format=key, **kw)
            with M.Image.open(out) as chk:
                chk.load()
                verified = [chk.width, chk.height, chk.format]
            ctx.record(out)
            results.append({"file": str(out), "format": key, "size": verified[:2], "bytes": out.stat().st_size,
                            "quality": used_q})
    if p.get("max_bytes") and any(r["bytes"] > p["max_bytes"] for r in results if r["format"] in ("PNG", "TIFF")):
        warnings.append("lossless outputs exceed max_bytes; use a lossy format or smaller size")
    for w in warnings:
        ctx.warn(w)
    return {"outputs": results, "count": len(results)}


# ----------------------------------------------------------------- responsive crop

def op_responsive_crop(p, ctx):
    M = _mods()
    src = _open(p["image"], ctx.cfg)
    W, H = src.size
    fx, fy = p["focal_x"], p["focal_y"]
    crops = []
    for tgt in p["targets"]:
        try:
            aw, ah = [float(x) for x in tgt.split(":")]
        except ValueError:
            raise ToolError("invalid_input", f"target '{tgt}' must look like 4:5", field="targets")
        if aw <= 0 or ah <= 0:
            raise ToolError("invalid_input", "aspect parts must be positive", field="targets")
        a = aw / ah
        if W / H > a:
            ch, cw = H, H * a
        else:
            cw, ch = W, W / a
        cw, ch = int(round(cw)), int(round(ch))
        x0 = min(max(0, round(fx * W - cw / 2)), W - cw)
        y0 = min(max(0, round(fy * H - ch / 2)), H - ch)
        inside = x0 <= fx * W <= x0 + cw and y0 <= fy * H <= y0 + ch
        crop = src.crop((x0, y0, x0 + cw, y0 + ch))
        scale = min(1.0, 2048 / max(cw, ch))
        if scale < 1:
            crop = crop.resize((round(cw * scale), round(ch * scale)), M.Image.Resampling.LANCZOS)
        name = f"{Path(p['image']).stem}-{slugify(tgt.replace(':', 'x'), 20)}.png"
        out = ctx.out_path(None, name)
        _save(crop, out)
        ctx.record(out)
        crops.append({"target": tgt, "box": [x0, y0, x0 + cw, y0 + ch], "file": str(out), "focal_inside": inside,
                      "size": list(crop.size)})
    return {"source_size": [W, H], "crops": crops}


# ----------------------------------------------------------------- compositing helpers

def _resolve_layout(preset, custom):
    if custom:
        return custom
    if preset not in LAYOUTS["layouts"]:
        raise ToolError("invalid_input", f"unknown layout preset '{preset}'", field="preset")
    return LAYOUTS["layouts"][preset]


def _fill_placeholders(s, texts):
    out = s
    missing = []
    start = 0
    while True:
        i = out.find("{", start)
        if i < 0:
            break
        j = out.find("}", i)
        if j < 0:
            break
        key = out[i + 1:j]
        if key in texts:
            out = out[:i] + str(texts[key]) + out[j + 1:]
            start = i + len(str(texts[key]))
        else:
            missing.append(key)
            start = j + 1
    return out, missing


def _fit(im, box_w, box_h, mode, M):
    if mode == "contain":
        scale = min(box_w / im.width, box_h / im.height)
        nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
        return im.resize((nw, nh), M.Image.Resampling.LANCZOS), ((box_w - nw) // 2, (box_h - nh) // 2)
    scale = max(box_w / im.width, box_h / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    r = im.resize((nw, nh), M.Image.Resampling.LANCZOS)
    x0, y0 = (nw - box_w) // 2, (nh - box_h) // 2
    return r.crop((x0, y0, x0 + box_w, y0 + box_h)), (0, 0)


def op_layout_compose(p, ctx):
    M = _mods()
    layout = _resolve_layout(p.get("preset"), p.get("layout"))
    canvas_name = layout.get("canvas")
    if canvas_name:
        if canvas_name not in LAYOUTS["canvases"]:
            raise ToolError("invalid_input", f"unknown canvas '{canvas_name}'", field="layout")
        cdef = LAYOUTS["canvases"][canvas_name]
        cw, ch = cdef["size"]
        bgc = layout.get("background", cdef.get("background", "#ffffff"))
    else:
        cw, ch = layout["size"]
        bgc = layout.get("background", "#ffffff")
    if cw * ch > ctx.limits["max_pixels"]:
        raise ToolError("invalid_input", "canvas exceeds limits.max_pixels", field="layout")
    texts = p.get("texts") or {}
    images = p.get("images") or {}
    for k, v in images.items():
        if not Path(str(v)).is_file():
            raise ToolError("not_found", f"image for '{k}' not found: {v}", field="images")
    canvas = M.Image.new("RGB", (cw, ch), C.parse_hex(bgc, "background"))
    draw = M.ImageDraw.Draw(canvas)
    missing_all = []
    for el in layout["elements"]:
        t = el["type"]
        bx, by, bw, bh = el.get("box", [0, 0, cw, ch])
        if t == "rect":
            fill, miss = _fill_placeholders(el.get("fill", "#000000"), texts)
            if miss:
                raise ToolError("invalid_input", f"missing placeholder(s) for rectangle fill: {sorted(set(miss))}",
                                field="texts", hint="Pass them in the texts JSON, e.g. {\"brand_color\": \"#1e88e5\"}.")
            draw.rectangle([bx, by, bx + bw - 1, by + bh - 1], fill=C.parse_hex(fill, "fill"))
        elif t == "image":
            src_key = el["src"]
            if src_key not in images:
                if src_key in ("logo",) or el.get("optional"):
                    continue
                raise ToolError("invalid_input", f"layout needs image '{src_key}' (pass images JSON)", field="images")
            im = _open(images[src_key], ctx.cfg).convert("RGBA")
            fitted, (ox, oy) = _fit(im, bw, bh, el.get("fit", "cover"), M)
            canvas.paste(fitted, (bx + ox, by + oy), fitted)
        elif t == "text":
            raw, miss = _fill_placeholders(el["text"], texts)
            missing_all += miss
            if not raw.strip():
                continue
            color_s, cmiss = _fill_placeholders(el.get("color", "#000000"), texts)
            if cmiss:
                raise ToolError("invalid_input", f"missing placeholder(s) for text colour: {sorted(set(cmiss))}",
                                field="texts")
            color = C.parse_hex(color_s, "color")
            size = int(el.get("font_size", 40))
            font = _font(M, size, ctx.cfg)
            while size > 10:
                l, t0, r, b = draw.textbbox((0, 0), raw, font=font)
                if r - l <= bw and b - t0 <= bh:
                    break
                size -= 2
                font = _font(M, size, ctx.cfg)
            l, t0, r, b = draw.textbbox((0, 0), raw, font=font)
            align = el.get("align", "left")
            tx = bx if align == "left" else (bx + (bw - (r - l)) // 2 if align == "center" else bx + bw - (r - l))
            ty = by + (bh - (b - t0)) // 2
            draw.text((tx - l, ty - t0), raw, fill=color, font=font)
        else:
            raise ToolError("invalid_input", f"unknown element type '{t}' in layout", field="layout")
    if missing_all:
        raise ToolError("invalid_input", f"missing placeholder value(s): {sorted(set(missing_all))}", field="texts",
                        hint="Pass every {key} used by the layout in the texts JSON.")
    out = ctx.out_path(p.get("output"), f"layout-{slugify(p.get('preset') or 'custom')}.png")
    _save(canvas, out)
    ctx.record(out)
    return {"file": str(out), "size": [cw, ch], "elements": len(layout["elements"]),
            "note": "Text is drawn with the configured font (fonts.truetype) or Pillow's default font. Check contrast with compute.contrast."}


# ----------------------------------------------------------------- masks

def op_mask_build(p, ctx):
    M = _mods()
    mode = p["mode"]
    if mode == "refine":
        if not p.get("source_mask"):
            raise ToolError("invalid_input", "refine needs source_mask", field="source_mask")
        base = _open(p["source_mask"], ctx.cfg).convert("L")
    else:
        if not p.get("image"):
            raise ToolError("invalid_input", f"mode '{mode}' needs image", field="image")
        src = _open(p["image"], ctx.cfg)
    if mode == "threshold":
        L = src.convert("L")
        mask = L.point(lambda v: 255 if v >= p["threshold"] else 0)
    elif mode in ("box", "ellipse"):
        box = p.get("box")
        if not box or len(box) != 4:
            raise ToolError("invalid_input", "box mode needs box [x, y, w, h]", field="box")
        mask = M.Image.new("L", src.size, 0)
        d = M.ImageDraw.Draw(mask)
        x, y, w, h = box
        if mode == "box":
            d.rectangle([x, y, x + w - 1, y + h - 1], fill=255)
        else:
            d.ellipse([x, y, x + w - 1, y + h - 1], fill=255)
    elif mode == "label-map":
        labels = p.get("label_colors")
        if not isinstance(labels, dict) or not labels:
            raise ToolError("invalid_input", "label-map needs label_colors: {name: '#hex'}", field="label_colors")
        rgb = src.convert("RGB")
        tol = p["tolerance"]
        outs = []
        for name, hx in labels.items():
            r, g, b = C.parse_hex(hx, f"label_colors.{name}")
            masks = []
            for ch, v in zip(rgb.split(), (r, g, b)):
                masks.append(ch.point(lambda x, v=v: 255 if abs(x - v) <= tol else 0))
            m = M.ImageChops.multiply(M.ImageChops.multiply(masks[0], masks[1]), masks[2]).point(lambda x: 255 if x else 0)
            if p.get("invert"):
                m = M.ImageOps.invert(m) if hasattr(M, "ImageOps") else m.point(lambda x: 255 - x)
            out = ctx.out_path(None, f"mask-{slugify(name)}.png")
            _save(m, out)
            ctx.record(out)
            outs.append({"label": name, "file": str(out), "coverage_percent": round(100 * sum(m.histogram()[255:]) / (m.width * m.height), 2)})
        return {"masks": outs, "note": "One binary mask per label. Use compute.region_layout to check overlaps."}
    elif mode == "refine":
        mask = base
    else:
        raise ToolError("invalid_input", f"unknown mode {mode}", field="mode")
    if p.get("erode"):
        k = 2 * p["erode"] + 1
        mask = mask.filter(M.ImageFilter.MinFilter(k))
    if p.get("dilate"):
        k = 2 * p["dilate"] + 1
        mask = mask.filter(M.ImageFilter.MaxFilter(k))
    if p.get("feather"):
        mask = mask.filter(M.ImageFilter.GaussianBlur(p["feather"]))
    if p.get("invert"):
        mask = mask.point(lambda v: 255 - v)
    out = ctx.out_path(p.get("output"), f"mask-{mode}.png")
    _save(mask, out)
    ctx.record(out)
    hist = mask.histogram()
    cov = round(100.0 * sum(hist[128:]) / (mask.width * mask.height), 2)
    return {"mask_file": str(out), "size": list(mask.size), "foreground_percent": cov}


# ----------------------------------------------------------------- compositing

def _blend(M, bg, fg, mode):
    C2 = M.ImageChops
    if mode == "normal":
        return fg
    if mode == "multiply":
        return C2.multiply(bg, fg)
    if mode == "screen":
        return C2.screen(bg, fg)
    if mode == "darken":
        return C2.darker(bg, fg)
    if mode == "lighten":
        return C2.lighter(bg, fg)
    if mode == "difference":
        return C2.difference(bg, fg)
    if mode == "add":
        return C2.add(bg, fg)
    if mode == "overlay":
        mult2 = C2.add(C2.multiply(bg, fg), C2.multiply(bg, fg))
        scr = C2.screen(bg, fg)
        sel = bg.convert("L").point(lambda v: 255 if v < 128 else 0)
        return M.Image.composite(mult2, scr, sel)
    raise ToolError("invalid_input", f"unknown blend mode {mode}", field="blend")


def op_composite(p, ctx):
    M = _mods()
    bg = _open(p["background"], ctx.cfg).convert("RGB")
    layers = p.get("layers") or []
    if len(layers) > ctx.limits["max_layers"]:
        raise ToolError("invalid_input", f"too many layers (limit {ctx.limits['max_layers']})", field="layers")
    default_blend = p.get("blend") or "normal"
    applied = []
    for i, lay in enumerate(layers, 1):
        if "file" not in lay:
            raise ToolError("invalid_input", f"layer {i} needs 'file'", field="layers")
        if not Path(lay["file"]).is_file():
            raise ToolError("not_found", f"layer {i} file not found: {lay['file']}", field="layers")
        fg = _open(lay["file"], ctx.cfg).convert("RGBA")
        scale = float(lay.get("scale", 1.0))
        if scale <= 0 or scale > 8:
            raise ToolError("invalid_input", f"layer {i} scale must be >0 and <=8", field="layers")
        if scale != 1.0:
            fg = fg.resize((max(1, round(fg.width * scale)), max(1, round(fg.height * scale))), M.Image.Resampling.LANCZOS)
        alpha = fg.split()[3]
        if lay.get("mask"):
            if not Path(lay["mask"]).is_file():
                raise ToolError("not_found", f"layer {i} mask not found", field="layers")
            mk = _open(lay["mask"], ctx.cfg).convert("L").resize(fg.size, M.Image.Resampling.LANCZOS)
            alpha = M.ImageChops.multiply(alpha, mk)
        opacity = float(lay.get("opacity", 1.0))
        if not (0 <= opacity <= 1):
            raise ToolError("invalid_input", f"layer {i} opacity must be 0-1", field="layers")
        alpha = alpha.point(lambda v, o=opacity: int(v * o))
        x, y = int(lay.get("x", 0)), int(lay.get("y", 0))
        mode = lay.get("blend") or default_blend
        if mode not in BLEND_MODES:
            raise ToolError("invalid_input", f"layer {i} blend must be one of {BLEND_MODES}", field="layers")
        region = bg.crop((x, y, x + fg.width, y + fg.height))
        blended = _blend(M, region, fg.convert("RGB"), mode)
        bg.paste(blended, (x, y), alpha)
        applied.append({"index": i, "blend": mode, "position": [x, y], "size": list(fg.size), "opacity": opacity})
    out = ctx.out_path(p.get("output"), "composite.png")
    _save(bg, out)
    ctx.record(out)
    return {"file": str(out), "size": list(bg.size), "layers_applied": applied}


def op_shadow_reflection(p, ctx):
    M = _mods()
    subj = _open(p["image"], ctx.cfg).convert("RGBA")
    mode = p["mode"]
    W, H = subj.size
    dx, dy = int(p["offset_x"]), int(p["offset_y"])
    pad = max(abs(dx), abs(dy)) + 2 * int(p["blur"])
    refl_h = int(H * p["reflection_height"]) if mode in ("reflection", "both") else 0
    cw = W + pad * 2
    ch = H + refl_h + pad * 2
    bgc = C.parse_hex(p["background"], "background") if p.get("background") else None
    canvas = M.Image.new("RGBA", (cw, ch), (255, 255, 255, 0) if bgc is None else bgc + (255,))
    ox, oy = pad, pad
    if mode in ("drop-shadow", "both"):
        a = subj.split()[3]
        sh_alpha = a.filter(M.ImageFilter.GaussianBlur(p["blur"])).point(lambda v: int(v * p["shadow_opacity"]))
        shadow = M.Image.new("RGBA", subj.size, (0, 0, 0, 0))
        shadow.putalpha(sh_alpha)
        canvas.alpha_composite(shadow, (ox + dx, oy + dy))
    canvas.alpha_composite(subj, (ox, oy))
    if refl_h > 0:
        flipped = M.ImageOps.flip(subj).crop((0, 0, W, refl_h))
        ramp = M.Image.linear_gradient("L").resize((W, refl_h))
        fade = ramp.point(lambda v: int((255 - v) * 0.5))
        fa = M.ImageChops.multiply(flipped.split()[3], fade)
        flipped.putalpha(fa)
        canvas.alpha_composite(flipped, (ox, oy + H))
    out = ctx.out_path(p.get("output"), f"shadow-{mode}.png")
    result_img = canvas if bgc is None else canvas.convert("RGB")
    _save(result_img, out)
    ctx.record(out)
    return {"file": str(out), "size": list(canvas.size), "mode": mode, "reflection_height_px": refl_h,
            "note": "Shadow is a blurred, offset copy of the alpha. Reflection is a fading flip. Physically exact shadows need scene geometry."}


def op_edge_decontam(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("RGBA")
    r, g, b, a = im.split()
    spill = p["spill_color"]
    if spill == "green":
        g = M.ImageChops.darker(g, M.ImageChops.lighter(r, b))
    elif spill == "blue":
        b = M.ImageChops.darker(b, M.ImageChops.lighter(r, g))
    if p["erode"] > 0:
        a = a.filter(M.ImageFilter.MinFilter(2 * p["erode"] + 1))
    if p["feather"] > 0:
        a = a.filter(M.ImageFilter.GaussianBlur(p["feather"]))
    out_img = M.Image.merge("RGBA", (r, g, b, a))
    out = ctx.out_path(p.get("output"), "decontaminated.png")
    _save(out_img, out)
    ctx.record(out)
    return {"file": str(out), "spill_color": spill, "erode": p["erode"], "feather": p["feather"]}


def op_outpaint_prepare(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("RGB")
    l, r_, t, b = p["left"], p["right"], p["top"], p["bottom"]
    if l + r_ + t + b == 0:
        raise ToolError("invalid_input", "set at least one of left, right, top, bottom", field="left")
    W, H = im.size
    cw, ch = W + l + r_, H + t + b
    if cw * ch > ctx.limits["max_pixels"]:
        raise ToolError("invalid_input", "extended canvas exceeds limits.max_pixels", field="left")
    canvas = M.Image.new("RGB", (cw, ch), (255, 255, 255))
    mask = M.Image.new("L", (cw, ch), 255)
    if p["fill"] == "mean":
        mean = tuple(int(v) for v in _stats(im).mean)
        canvas.paste(mean, (0, 0, cw, ch))
    elif p["fill"] == "white":
        pass
    canvas.paste(im, (l, t))
    mask.paste(0, (l, t, l + W, t + H))
    if p["fill"] == "edge":
        if l:
            canvas.paste(im.crop((0, 0, 1, H)).resize((l, H)), (0, t))
        if r_:
            canvas.paste(im.crop((W - 1, 0, W, H)).resize((r_, H)), (l + W, t))
        if t:
            canvas.paste(im.crop((0, 0, W, 1)).resize((W, t)), (l, 0))
        if b:
            canvas.paste(im.crop((0, H - 1, W, H)).resize((W, b)), (l, t + H))
        for (x0, y0, x1, y1, sx, sy) in [(0, 0, l, t, 0, 0), (l + W, 0, cw, t, W - 1, 0), (0, t + H, l, ch, 0, H - 1), (l + W, t + H, cw, ch, W - 1, H - 1)]:
            if x1 > x0 and y1 > y0:
                canvas.paste(im.getpixel((sx, sy)), (x0, y0, x1, y1))
    if p["feather"] > 0:
        mask = mask.filter(M.ImageFilter.GaussianBlur(p["feather"] / 2))
    out = ctx.out_path(None, "outpaint-canvas.png")
    mout = ctx.out_path(None, "outpaint-mask.png")
    _save(canvas, out)
    _save(mask, mout)
    ctx.record(out)
    ctx.record(mout)
    return {"canvas_file": str(out), "mask_file": str(mout), "canvas_size": [cw, ch],
            "new_area_percent": round(100.0 * (1 - (W * H) / (cw * ch)), 2),
            "note": "White in the mask marks the area to generate. Send canvas and mask to engine.job (job_type inpaint) for generative fill."}


# ----------------------------------------------------------------- upscale and restoration

def op_upscale(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg)
    s = int(p["scale"])
    nw, nh = im.width * s, im.height * s
    if nw * nh > ctx.limits["max_pixels"]:
        raise ToolError("invalid_input", "upscaled output exceeds limits.max_pixels", field="scale")
    meth = {"lanczos": M.Image.Resampling.LANCZOS, "bicubic": M.Image.Resampling.BICUBIC,
            "nearest": M.Image.Resampling.NEAREST}[p["method"]]
    out_img = im.resize((nw, nh), meth)
    if p["sharpen"]:
        out_img = out_img.filter(M.ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    out = ctx.out_path(p.get("output"), f"upscaled-{s}x.png")
    _save(out_img, out)
    ctx.record(out)
    return {"file": str(out), "size": [nw, nh], "method": p["method"],
            "note": "Classical resampling adds no new detail. For generative upscaling use engine.job with job_type upscale."}


def op_denoise_grain(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("RGB")
    mode = p["mode"]
    if mode in ("denoise", "both"):
        k = int(p["median_size"])
        if k % 2 == 0 or k < 1 or k > 7:
            raise ToolError("invalid_input", "median_size must be an odd number 1-7", field="median_size")
        if k > 1:
            im = im.filter(M.ImageFilter.MedianFilter(k))
    if mode in ("grain", "both") and p["grain_amount"] > 0:
        w, h = im.size
        rng = random.Random(p["grain_seed"])
        raw = rng.randbytes(2 * w * h)
        vals = bytes((raw[i] + raw[i + 1]) // 2 for i in range(0, len(raw), 2))
        noise = M.Image.frombytes("L", (w, h), vals)
        amt = p["grain_amount"]
        scaled = noise.point(lambda v: int(round(128 + (v - 128) * amt)))
        im = M.ImageChops.add(im, scaled.convert("RGB"), 1.0, -128)
    out = ctx.out_path(p.get("output"), f"{mode}.png")
    _save(im, out)
    ctx.record(out)
    return {"file": str(out), "mode": mode, "grain_seed": p["grain_seed"],
            "transform_log": [f"mode={mode}", f"median_size={p['median_size']}", f"grain_amount={p['grain_amount']}"],
            "note": "Denoise uses a median filter, which can soften fine texture. Compare with the source."}


def op_sharpen(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg)
    out_img = im.filter(M.ImageFilter.UnsharpMask(radius=p["radius"], percent=p["percent"], threshold=p["threshold"]))
    out = ctx.out_path(p.get("output"), "sharpened.png")
    _save(out_img, out)
    ctx.record(out)
    return {"file": str(out), "radius": p["radius"], "percent": p["percent"], "threshold": p["threshold"],
            "note": "Unsharp mask. High percent or low threshold creates halos; check edges at 100%."}


# ----------------------------------------------------------------- colour

_COLOR_PRESETS = {
    "night": {"brightness": 0.5, "contrast": 1.05, "saturation": 0.8, "temperature": -20},
    "cinematic": {"brightness": 1.0, "contrast": 1.15, "saturation": 0.9, "temperature": 8},
    "real-estate": {"brightness": 1.08, "contrast": 1.04, "saturation": 1.05, "temperature": 0},
}


def op_color_adjust(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("RGB")
    preset = p["preset"]
    if preset == "custom":
        b, c, s, t = p["brightness"], p["contrast"], p["saturation"], p["temperature"]
    else:
        pv = _COLOR_PRESETS[preset]
        b, c, s, t = pv["brightness"], pv["contrast"], pv["saturation"], pv["temperature"]
    before = _luma_mean_std(im)[0]
    im = M.ImageEnhance.Brightness(im).enhance(b)
    im = M.ImageEnhance.Contrast(im).enhance(c)
    im = M.ImageEnhance.Color(im).enhance(s)
    if t:
        r, g, bl = im.split()
        k = t / 200.0
        r = r.point(lambda v: min(255, int(v * (1 + k))))
        bl = bl.point(lambda v: min(255, int(v * (1 - k))))
        im = M.Image.merge("RGB", (r, g, bl))
    after = _luma_mean_std(im)[0]
    out = ctx.out_path(p.get("output"), f"color-{preset}.png")
    _save(im, out)
    ctx.record(out)
    return {"file": str(out), "preset": preset, "applied": {"brightness": b, "contrast": c, "saturation": s, "temperature": t},
            "mean_luminance_before": round(before, 2), "mean_luminance_after": round(after, 2),
            "note": "Global adjustments only. Night and real-estate presets are approximations, not relighting."}


def op_palette_transfer(p, ctx):
    M = _mods()
    tgt = _open(p["image"], ctx.cfg).convert("RGB")
    ref = _open(p["reference"], ctx.cfg).convert("RGB")
    s = p["strength"]
    ts, rs = _stats(tgt), _stats(ref)
    luts = []
    for c in range(3):
        mu_s, sd_s = ts.mean[c], max(ts.stddev[c], 1e-6)
        mu_r, sd_r = rs.mean[c], rs.stddev[c]
        k = 1 + s * (sd_r / sd_s - 1)
        shift = s * (mu_r - mu_s)
        luts.append([max(0, min(255, int(round((x - mu_s) * k + mu_s + shift)))) for x in range(256)])
    chans = tgt.split()
    out_img = M.Image.merge("RGB", tuple(chans[c].point(luts[c]) for c in range(3)))
    out = ctx.out_path(p.get("output"), "palette-transfer.png")
    _save(out_img, out)
    ctx.record(out)
    return {"file": str(out), "strength": s, "target_mean": [round(v, 2) for v in ts.mean],
            "reference_mean": [round(v, 2) for v in rs.mean], "result_mean": [round(v, 2) for v in _stats(out_img).mean],
            "warnings_list": ["Statistics-based transfer. Protect skin, product and brand colours by masking them out or re-checking them."]}


def _features(path, cfg, metric):
    M = _mods()
    im = _thumb(_open(path, cfg), 512)
    st = _stats(im)
    L = im.convert("L")
    lm, lsd = _luma_mean_std(im)
    w, h = L.size
    left = _stats(L.crop((0, 0, w // 2, h))).mean[0]
    right = _stats(L.crop((w // 2, 0, w, h))).mean[0]
    top = _stats(L.crop((0, 0, w, h // 2))).mean[0]
    bottom = _stats(L.crop((0, h // 2, w, h))).mean[0]
    if metric == "style":
        return {"mean_r": st.mean[0], "mean_g": st.mean[1], "mean_b": st.mean[2],
                "sd_r": st.stddev[0], "sd_g": st.stddev[1], "sd_b": st.stddev[2]}
    if metric == "lighting":
        return {"mean_luminance": lm, "contrast": lsd, "light_left_minus_right": left - right,
                "light_top_minus_bottom": top - bottom}
    return {"mean_r": st.mean[0], "mean_g": st.mean[1], "mean_b": st.mean[2], "mean_luminance": lm, "contrast": lsd}


def op_consistency(p, ctx):
    imgs = p["images"]
    if len(imgs) < 2:
        raise ToolError("invalid_input", "consistency needs at least two images", field="images")
    rows = {Path(f).name: _features(f, ctx.cfg, p["metric"]) for f in imgs}
    keys = list(next(iter(rows.values())).keys())
    z = ctx.cfg["thresholds"]["outlier_z"]
    outliers = []
    stats_out = {}
    for k in keys:
        vals = [r[k] for r in rows.values()]
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
        stats_out[k] = {"mean": round(m, 3), "stddev": round(sd, 3)}
        if sd > 1e-9:
            for name, r in rows.items():
                zs = (r[k] - m) / sd
                if abs(zs) > z:
                    outliers.append({"file": name, "feature": k, "z": round(zs, 2)})
    if len(imgs) < 3:
        ctx.warn("fewer than 3 images: z-scores are weak; treat outliers as hints only")
    return {"metric": p["metric"], "per_image": rows, "population": stats_out, "outliers": outliers,
            "note": "Compares images with each other, not with a standard. Brand-approved values need a separate reference."}


def op_color_convert(p, ctx):
    M = _mods()
    if M.ImageCms is None:
        raise ToolError("dependency_missing", "ImageCms (LittleCMS) is not available in this Pillow build")
    src = _open(p["image"], ctx.cfg)
    target = p["target"]
    icc = src.info.get("icc_profile")
    if icc:
        src_prof = M.ImageCms.ImageCmsProfile(io.BytesIO(icc))
    else:
        src_prof = M.ImageCms.createProfile("sRGB")
        ctx.warn("no embedded profile: source treated as sRGB")
    if target == "srgb":
        dst_prof = M.ImageCms.createProfile("sRGB")
        mode = "RGB"
    else:
        key = "display_p3" if target == "display-p3" else "cmyk"
        path = p.get("profile") or ctx.cfg["color_profiles"].get(key) or ""
        if not path or not Path(path).is_file():
            raise ToolError("unavailable", f"no {target} profile configured",
                            hint=f"Pass profile=/path/to/file.icc or set color_profiles.{key} in the config. No profile is bundled.")
        dst_prof = M.ImageCms.ImageCmsProfile(path)
        mode = "CMYK" if target == "cmyk" else "RGB"
    ext = Path(p.get("output") or "x.png").suffix.lower() if p.get("output") else (".tif" if mode == "CMYK" else ".png")
    if mode == "CMYK" and ext not in (".tif", ".tiff", ".jpg", ".jpeg"):
        raise ToolError("invalid_input", "CMYK output must be .tif, .tiff, .jpg or .jpeg", field="output")
    src_rgb = src.convert("RGB")
    conv = M.ImageCms.profileToProfile(src_rgb, src_prof, dst_prof, renderingIntent=0,
                                       outputMode=mode, flags=0)
    out = ctx.out_path(p.get("output"), f"converted-{target}{ext}")
    kw = {}
    if not p.get("strip_icc"):
        kw["icc_profile"] = M.ImageCms.ImageCmsProfile(dst_prof).tobytes()
    _save(conv, out, **kw)
    ctx.record(out)
    return {"file": str(out), "target": target, "mode": mode, "source_profile": "embedded" if icc else "assumed sRGB",
            "note": "Convert from the master file, not from a previous conversion. Soft-proof print targets before release."}


# ----------------------------------------------------------------- text overlay and disclosure

def op_text_overlay(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("RGBA")
    W, H = im.size
    mode = p["mode"]
    anchor = p["anchor"]
    size = p["font_size"]
    if mode == "disclosure":
        size = max(12, int(H * 0.022))
    font = _font(M, size, ctx.cfg)
    layer = M.Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = M.ImageDraw.Draw(layer)
    text = p["text"]
    l, t0, r, b = d.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t0
    pad = max(6, size // 3)
    margin = int(min(W, H) * 0.05)
    if p.get("fill_box"):
        bx, by, bw, bh = p["fill_box"]
        fill_color = _ring_color(im, bx, by, bw, bh, M)
        d.rectangle([bx, by, bx + bw - 1, by + bh - 1], fill=fill_color + (255,))
        x = bx + (bw - tw) // 2
        y = by + (bh - th) // 2
        box = (bx, by, bx + bw, by + bh)
    else:
        if "right" in anchor:
            x = W - margin - tw - pad
        elif anchor == "center":
            x = (W - tw) // 2
        else:
            x = margin
        if "bottom" in anchor:
            y = H - margin - th - pad
        elif anchor == "center":
            y = (H - th) // 2
        else:
            y = margin
        box = (x - pad, y - pad, x + tw + pad, y + th + pad)
    plate = p.get("plate") or mode == "disclosure"
    if plate and not p.get("fill_box"):
        d.rectangle(list(box), fill=(0, 0, 0, 160))
    color = C.parse_hex(p["color"], "color")
    d.text((x - l, y - t0), text, fill=color + (255,), font=font)
    out_img = M.Image.alpha_composite(im, layer).convert("RGB")
    safe_ok = box[0] >= margin * 0.5 and box[1] >= margin * 0.5 and box[2] <= W - margin * 0.5 and box[3] <= H - margin * 0.5
    warnings = [] if safe_ok or p.get("fill_box") else ["text box is inside the outer 2.5% edge; move it inward for crop safety"]
    out = ctx.out_path(p.get("output"), f"{mode}-text.png")
    _save(out_img, out)
    ctx.record(out)
    for w in warnings:
        ctx.warn(w)
    return {"file": str(out), "font_size": size, "text_box": [int(v) for v in box], "mode": mode,
            "note": "Text is rendered by Pillow. Check legibility at display size, and check contrast with compute.contrast."}


def _ring_color(im, bx, by, bw, bh, M):
    ox0, oy0 = max(0, bx - 4), max(0, by - 4)
    ox1, oy1 = min(im.width, bx + bw + 4), min(im.height, by + bh + 4)
    outer = im.crop((ox0, oy0, ox1, oy1)).convert("RGB")
    inner = im.crop((bx, by, bx + bw, by + bh)).convert("RGB")
    om, im_ = _stats(outer).mean, _stats(inner).mean
    oa = (ox1 - ox0) * (oy1 - oy0)
    ia = max(1, bw * bh)
    ra = max(1, oa - ia)
    return tuple(max(0, min(255, int(round((om[c] * oa - im_[c] * ia) / ra)))) for c in range(3))


# ----------------------------------------------------------------- pixel art, tiling, line art

def op_pixel_art(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("RGB")
    if p["grid"] > 0:
        gw = p["grid"]
        gh = max(1, round(im.height * gw / im.width))
        im = im.resize((gw, gh), M.Image.Resampling.BOX)
    q = im.quantize(colors=p["palette_size"], method=M.Image.Quantize.MEDIANCUT).convert("RGB")
    colors = sorted(q.getcolors(maxcolors=1 << 20), key=lambda t: -t[0])
    big = q.resize((q.width * p["scale"], q.height * p["scale"]), M.Image.Resampling.NEAREST)
    out = ctx.out_path(p.get("output"), "pixel-art.png")
    _save(big, out)
    ctx.record(out)
    return {"file": str(out), "grid_size": list(q.size), "output_size": list(big.size),
            "palette_used": len(colors), "palette": ["#%02x%02x%02x" % c[1] for c in colors[:32]],
            "note": "Median-cut palette reduction and nearest-neighbour scaling. Hand-cleanup is still needed for production sprites."}


def op_tiling(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg)
    if p["mode"] == "check":
        L = _thumb(im, 512).convert("L")
        col_d, row_d, mc, mr, wc, wr = _seam_scan(L)
        t = ctx.cfg["thresholds"]["seam_ratio"]
        lr = wc / mc if mc else None
        tb = wr / mr if mr else None
        passed = (lr is None or lr <= t) and (tb is None or tb <= t)
        return {"left_right_wrap_ratio": round(lr, 2) if lr is not None else None,
                "top_bottom_wrap_ratio": round(tb, 2) if tb is not None else None,
                "threshold": t, "seamless_estimate": passed,
                "note": "Wrap ratio compares the join seam with typical neighbouring pixels. Check the preview by eye."}
    n = p["repeats"]
    out_img = M.Image.new("RGB", (im.width * n, im.height * n))
    src = im.convert("RGB")
    for yy in range(n):
        for xx in range(n):
            out_img.paste(src, (xx * im.width, yy * im.height))
    out = ctx.out_path(p.get("output"), f"tiling-preview-{n}x{n}.png")
    _save(out_img, out)
    ctx.record(out)
    return {"file": str(out), "size": list(out_img.size), "repeats": n}


def op_edge_map(p, ctx):
    M = _mods()
    im = _open(p["image"], ctx.cfg).convert("L")
    edges = im.filter(M.ImageFilter.FIND_EDGES)
    thr = p["threshold"]
    m = edges.point(lambda v: 255 if v >= thr else 0)
    if p["mode"] == "line-drawing":
        m = M.ImageOps.invert(m)  # dark lines on white paper
    if p["invert"]:
        m = M.ImageOps.invert(m)
    out = ctx.out_path(p.get("output"), f"{p['mode']}.png")
    if not p["monochrome"]:
        out_img = m.convert("RGB")
    else:
        out_img = m
    _save(out_img, out)
    ctx.record(out)
    return {"file": str(out), "mode": p["mode"], "threshold": thr,
            "note": "Sobel-like edge filter with threshold, not a Canny detector. Tune threshold per image."}


# ----------------------------------------------------------------- dataset curation

def _folder_images(folder, limit):
    files = sorted([f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() in IMG_EXT])
    if len(files) > limit:
        raise ToolError("invalid_input", f"folder has {len(files)} images; limit is {limit}", field="folder")
    return files


def op_dataset_curate(p, ctx):
    M = _mods()
    files = _folder_images(p["folder"], ctx.limits["max_batch_items"] * 10)
    if not files:
        raise ToolError("invalid_input", "no images found in folder", field="folder")
    thr = p["dedupe_threshold"]
    items = []
    for f in files:
        try:
            im = _open(f, ctx.cfg)
        except ToolError as e:
            items.append({"file": f.name, "status": "unreadable", "reason": e.message})
            continue
        status = "keep"
        reason = None
        if im.width < p["min_width"] or im.height < p["min_height"]:
            status, reason = "too_small", f"{im.width}x{im.height} below {p['min_width']}x{p['min_height']}"
        sd = _luma_mean_std(im)[1]
        if status == "keep" and sd < ctx.cfg["thresholds"]["blank_stddev"]:
            status, reason = "blank", f"luminance stddev {sd:.2f}"
        items.append({"file": f.name, "width": im.width, "height": im.height, "status": status, "reason": reason,
                      "dhash": _dhash(im), "group": None})
    # group near-duplicates among readable images
    gid = 0
    usable = [it for it in items if "dhash" in it]
    for i, a in enumerate(usable):
        if a["group"] is not None:
            continue
        gid += 1
        a["group"] = gid
        for b in usable[i + 1:]:
            if b["group"] is None and _hamming(a["dhash"], b["dhash"]) <= thr:
                b["group"] = gid
                if b["status"] == "keep":
                    b["status"], b["reason"] = "duplicate", f"near-duplicate of {a['file']}"
    for it in items:
        it.pop("dhash", None)
    counts = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    out = ctx.out_path(p.get("output"), "dataset-manifest.json")
    write_json(out, {"folder": Path(p["folder"]).name, "thresholds": {"min_width": p["min_width"],
                     "min_height": p["min_height"], "dedupe_threshold": thr}, "items": items, "counts": counts})
    ctx.record(out)
    return {"manifest_file": str(out), "counts": counts, "total": len(items),
            "note": "Nothing is deleted or moved. Review duplicates and remove files yourself after approval."}


def op_near_duplicate(p, ctx):
    M = _mods()
    refs = _folder_images(p["reference_dir"], ctx.limits["max_batch_items"] * 10)
    cands = _folder_images(p["candidate_dir"], ctx.limits["max_batch_items"] * 10)
    thr = p["threshold"]
    rh = {}
    for f in refs:
        try:
            rh[f.name] = _dhash(_open(f, ctx.cfg))
        except ToolError:
            continue
    pairs = []
    for c in cands:
        try:
            h = _dhash(_open(c, ctx.cfg))
        except ToolError:
            continue
        for name, rv in rh.items():
            d = _hamming(h, rv)
            if d <= thr:
                pairs.append({"candidate": c.name, "reference": name, "distance": d})
    pairs.sort(key=lambda x: x["distance"])
    return {"reference_count": len(rh), "candidate_count": len(cands), "threshold": thr,
            "near_duplicate_pairs": pairs[:200], "pair_total": len(pairs),
            "note": "Perceptual hash (dHash) screen. A miss does not prove no memorisation; review generated images against training images by eye."}


# ----------------------------------------------------------------- provenance, authenticity, redaction

def op_metadata_sidecar(p, ctx):
    if p["mode"] == "write":
        im = _open(p["image"], ctx.cfg)
        digest = sha256_file(p["image"])
        rec = {"image_name": Path(p["image"]).name, "sha256": digest, "width": im.width, "height": im.height,
               "format": im.format, "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "prompt": p.get("prompt"), "model": p.get("model"), "seed": p.get("seed"),
               "edits": p.get("edits") or [], "license": p.get("license"), "creator": p.get("creator"),
               "toolkit_version": C.TOOLKIT_VERSION,
               "note": "Provenance sidecar written by a tool. It is not a cryptographic content credential."}
        out = ctx.out_path(p.get("sidecar"), f"{Path(p['image']).stem}.provenance.json")
        write_json(out, rec)
        ctx.record(out)
        return {"sidecar_file": str(out), "sha256": digest}
    # verify
    if not p.get("sidecar"):
        raise ToolError("invalid_input", "verify needs the sidecar path", field="sidecar")
    rec = C.read_json(p["sidecar"])
    digest = sha256_file(p["image"])
    matches = rec.get("sha256") == digest
    return {"verified": matches, "recorded_sha256": rec.get("sha256"), "current_sha256": digest,
            "reason": "image bytes match the sidecar" if matches else "image bytes differ from the sidecar (edited or re-encoded)"}


_AI_MARKERS = ["stable diffusion", "comfyui", "automatic1111", "midjourney", "dall-e", "dall·e", "firefly", "invokeai", "flux"]


def op_authenticity(p, ctx):
    rows = []
    M = _mods()
    for f in p["images"]:
        im = _open(f, ctx.cfg)
        meta = _meta(im)
        reasons = []
        blob = " ".join(str(v) for v in im.info.values() if isinstance(v, (str, bytes))).lower()
        if meta["software"]:
            reasons.append(f"software tag: {meta['software']}")
        marker_hits = [m for m in _AI_MARKERS if m in blob or (meta["software"] and m in str(meta["software"]).lower())]
        if marker_hits:
            reasons.append(f"generator text found: {', '.join(marker_hits)}")
        raw = Path(f).read_bytes()
        c2pa = b"c2pa" in raw or b"jumb" in raw
        if c2pa:
            reasons.append("possible content-credential structure present (not verified)")
        if not meta["exif_present"]:
            reasons.append("no EXIF camera data (common for generated or re-exported images; not proof)")
        ela = None
        if im.mode in ("RGB", "L", "RGBA"):
            rgb = im.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            re_im = M.Image.open(buf).convert("RGB")
            diff = M.ImageChops.difference(rgb, re_im)
            st = _stats(diff)
            ela = round(sum(st.mean) / 3, 3)
        rows.append({"file": Path(f).name, "signals": reasons, "error_level_mean": ela,
                     "conclusion": "uncertain - indicators only"})
    return {"results": rows,
            "note": "No single signal proves authenticity. Metadata can be stripped or forged, and error-level analysis is unreliable on re-compressed or flat images. Report uncertainty; do not claim detection."}


def op_redact(p, ctx):
    M = _mods()
    mode = p["mode"]
    src_path = p["image"]
    im_raw = _open(src_path, ctx.cfg)
    if mode == "scan":
        meta = _meta(im_raw)
        xmp = "XML:com.adobe.xmp" in im_raw.info or "xmp" in im_raw.info
        findings = []
        if meta["gps_present"]:
            findings.append("GPS location tag present")
        if meta["exif_present"]:
            findings.append("EXIF metadata present")
        if meta["icc_present"]:
            findings.append("embedded colour profile present (not private by itself)")
        if xmp:
            findings.append("XMP metadata present")
        if meta["png_text_keys"]:
            findings.append(f"text chunks: {meta['png_text_keys']}")
        return {"file": Path(src_path).name, "findings": findings,
                "faces_or_plates_detected": "not checked - no detector is bundled",
                "note": "Metadata scan only. Visual identifiers (faces, plates, screens) need a human review or a separate detector."}
    im = im_raw.convert("RGBA" if im_raw.mode in ("RGBA", "LA") else "RGB")
    boxes = p.get("boxes") or []
    for b in boxes:
        if not isinstance(b, list) or len(b) != 4:
            raise ToolError("invalid_input", "boxes must be a list of [x, y, w, h]", field="boxes")
    if mode in ("pixelate", "blur", "box-fill") and not boxes:
        raise ToolError("invalid_input", f"{mode} needs boxes", field="boxes")
    for x, y, w, h in boxes:
        x0, y0, x1, y1 = max(0, x), max(0, y), min(im.width, x + w), min(im.height, y + h)
        if x1 <= x0 or y1 <= y0:
            ctx.warn(f"box {[x, y, w, h]} is outside the image and was ignored")
            continue
        region = im.crop((x0, y0, x1, y1))
        if mode == "pixelate":
            bs = p["block"]
            small = region.resize((max(1, (x1 - x0) // bs), max(1, (y1 - y0) // bs)), M.Image.Resampling.BOX)
            region = small.resize((x1 - x0, y1 - y0), M.Image.Resampling.NEAREST)
        elif mode == "blur":
            region = region.filter(M.ImageFilter.GaussianBlur(p["blur_radius"]))
        elif mode == "box-fill":
            region = M.Image.new(im.mode, (x1 - x0, y1 - y0), (0, 0, 0, 255) if im.mode == "RGBA" else (0, 0, 0))
        im.paste(region, (x0, y0))
    out = ctx.out_path(p.get("output"), f"redacted-{mode}.png")
    _save(im, out)
    ctx.record(out)
    return {"file": str(out), "mode": mode, "boxes_applied": len(boxes), "metadata_stripped": True,
            "note": "A new file is written; the source is never modified. Metadata is not copied into the output."}


# ----------------------------------------------------------------- QA pipeline

QA_CHECKS = ["corrupt", "blank", "dimensions", "metadata", "quality", "artifacts", "delivery"]


def op_qa_pipeline(p, ctx):
    checks = p.get("checks") or QA_CHECKS
    bad = [c for c in checks if c not in QA_CHECKS]
    if bad:
        raise ToolError("invalid_input", f"unknown check(s) {bad}", field="checks", hint=f"Use: {', '.join(QA_CHECKS)}")
    spec = _spec(p.get("spec") or "generic-web", None, ctx)
    report = []
    for f in p["images"]:
        row = {"file": Path(f).name, "results": {}}
        try:
            im = _open(f, ctx.cfg)
        except ToolError as e:
            row["results"]["corrupt"] = {"status": "fail", "detail": e.message}
            report.append(row)
            continue
        if "corrupt" in checks:
            row["results"]["corrupt"] = {"status": "pass", "detail": "opens and decodes"}
        if "blank" in checks:
            sd = _luma_mean_std(im)[1]
            row["results"]["blank"] = {"status": "fail" if sd < ctx.cfg["thresholds"]["blank_stddev"] else "pass",
                                       "detail": f"stddev {sd:.2f}"}
        if "dimensions" in checks:
            small = min(im.size) < 64
            row["results"]["dimensions"] = {"status": "warn" if small else "pass", "detail": f"{im.width}x{im.height}"}
        if "metadata" in checks:
            m = _meta(im)
            row["results"]["metadata"] = {"status": "warn" if m["gps_present"] else "pass",
                                          "detail": "GPS present" if m["gps_present"] else "no GPS tag"}
        if "quality" in checks:
            q = _quality(f, ctx.cfg)
            row["results"]["quality"] = {"status": "warn" if q["flags"] else "pass", "detail": q["flags"] or "no flags"}
        if "artifacts" in checks:
            a = _artifact_report(f, ctx.cfg)
            sev = "warn" if a["findings"] else "pass"
            row["results"]["artifacts"] = {"status": sev, "detail": [x["type"] for x in a["findings"]] or "none"}
        if "delivery" in checks:
            dchecks, _ = _delivery_checks(f, spec, ctx.cfg)
            st = "fail" if any(c["status"] == "fail" for c in dchecks) else ("warn" if any(c["status"] == "warn" for c in dchecks) else "pass")
            row["results"]["delivery"] = {"status": st, "detail": spec}
        statuses = [v["status"] for v in row["results"].values()]
        row["overall"] = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")
        report.append(row)
    summary = {s: sum(1 for r in report if r["overall"] == s) for s in ("pass", "warn", "fail")}
    out = ctx.out_path(p.get("output"), "qa-report.json")
    write_json(out, {"checks": checks, "spec": p.get("spec") or "generic-web", "summary": summary, "results": report})
    ctx.record(out)
    return {"summary": summary, "report_file": str(out), "results": report,
            "note": "Automated gate only. A pass means the listed checks passed, not that the image is approved."}


# ----------------------------------------------------------------- registry

_CHOICE_SCALE = ["2", "3", "4"]

OPS = {
    "image.inspect": op(op_inspect, [P("images", "files", True, help="Image files to inspect")],
                        "Format, size, alpha, DPI, ICC and metadata flags (utility)", needs="pillow"),
    "image.delivery_audit": op(op_delivery_audit, [
        P("images", "files", True), P("spec", "choice", choices=SPECS, help="Named delivery spec"),
        P("custom", "json", help="Override or extend spec keys (min_width, max_bytes, formats...)"),
        P("output", "outfile"),
    ], "Check dimensions, format, size, colour space, DPI and GPS against a delivery spec", needs="pillow"),
    "image.qa_pipeline": op(op_qa_pipeline, [
        P("images", "files", True), P("checks", "list", help="Comma list: " + ", ".join(QA_CHECKS)),
        P("spec", "choice", default="generic-web", choices=SPECS), P("output", "outfile"),
    ], "Run corruption, blank, dimension, metadata, quality, artifact and delivery checks", needs="pillow"),
    "image.quality_metrics": op(op_quality_metrics, [P("images", "files", True)],
                                "Objective brightness, clipping, sharpness, noise and colourfulness metrics", needs="pillow"),
    "image.artifact_scan": op(op_artifact_scan, [P("images", "files", True)],
                              "Heuristic blank, seam, tiling and flat-region scan", needs="pillow"),
    "image.compare": op(op_compare, [
        P("image_a", "file", True), P("image_b", "file", True),
        P("threshold", "int", default=16, min=0, max=255),
        P("mode", "choice", default="diff", choices=["diff", "regression"]),
        P("output", "outfile"),
    ], "Pixel and perceptual difference with heatmap; regression mode gives pass or fail", needs="pillow"),
    "image.contact_sheet": op(op_contact_sheet, [
        P("images", "files", True), P("columns", "int", default=4, min=1, max=16),
        P("thumb", "int", default=256, min=64, max=1024), P("labels", "bool", default=True),
        P("background", "str", default="#202020"), P("output", "outfile"),
    ], "Labelled grid of thumbnails for review", needs="pillow"),
    "image.before_after": op(op_before_after, [
        P("before", "file", True), P("after", "file", True),
        P("label_before", "str", default="Before"), P("label_after", "str", default="After"),
        P("divider", "int", default=8, min=0, max=200), P("output", "outfile"),
    ], "Side-by-side comparison with labels", needs="pillow"),
    "image.export": op(op_export, [
        P("image", "file", True), P("formats", "list", default="png", help="Comma list: png, jpg, webp, avif, tiff"),
        P("sizes", "intlist", help="Max side lengths, e.g. 16,32,256"),
        P("max_bytes", "int", min=1024), P("quality", "int", default=90, min=1, max=100),
        P("dpi", "int", min=72, max=1200), P("strip_metadata", "bool", default=True),
        P("background", "str", default="#ffffff"), P("optimize", "bool", default=False),
    ], "Export to several formats and sizes with optional byte limit (metadata stripped by default)", needs="pillow"),
    "image.responsive_crop": op(op_responsive_crop, [
        P("image", "file", True), P("focal_x", "float", default=0.5, min=0, max=1),
        P("focal_y", "float", default=0.5, min=0, max=1),
        P("targets", "list", default="1:1,4:5,16:9", help="Comma list of aspect ratios like 4:5"),
    ], "Focal-point-aware crops for several aspect ratios", needs="pillow"),
    "image.layout_compose": op(op_layout_compose, [
        P("preset", "choice", choices=LAYOUT_NAMES), P("layout", "json", help="Custom layout JSON (overrides preset)"),
        P("images", "json", help="JSON object: source key -> image file"),
        P("texts", "json", help="JSON object: placeholder -> text or colour"),
        P("output", "outfile"),
    ], "Compose a poster, social, packaging or brand layout from a preset or JSON", needs="pillow"),
    "image.mask_build": op(op_mask_build, [
        P("mode", "choice", default="threshold", choices=["threshold", "box", "ellipse", "label-map", "refine"]),
        P("image", "file"), P("source_mask", "file"),
        P("threshold", "int", default=128, min=0, max=255), P("box", "intlist", help="[x, y, w, h]"),
        P("label_colors", "json", help="JSON object: label -> #hex colour"),
        P("tolerance", "int", default=12, min=0, max=128),
        P("feather", "int", default=0, min=0, max=50), P("erode", "int", default=0, min=0, max=20),
        P("dilate", "int", default=0, min=0, max=20), P("invert", "bool", default=False), P("output", "outfile"),
    ], "Create threshold, shape, colour-label or refined masks", needs="pillow"),
    "image.composite": op(op_composite, [
        P("background", "file", True),
        P("layers", "json", True, help="List of {file, x, y, scale, opacity, blend, mask}"),
        P("blend", "choice", default="normal", choices=BLEND_MODES), P("output", "outfile"),
    ], "Layer foreground images onto a background with blend modes and masks", needs="pillow"),
    "image.shadow_reflection": op(op_shadow_reflection, [
        P("image", "file", True, help="RGBA PNG with transparent background"),
        P("mode", "choice", default="both", choices=["drop-shadow", "reflection", "both"]),
        P("offset_x", "int", default=20, min=-500, max=500), P("offset_y", "int", default=20, min=-500, max=500),
        P("blur", "int", default=12, min=0, max=200), P("shadow_opacity", "float", default=0.5, min=0, max=1),
        P("reflection_height", "float", default=0.3, min=0.05, max=1),
        P("background", "str", help="Optional solid background hex; default transparent"),
        P("output", "outfile"),
    ], "Drop shadow and fading floor reflection for a cut-out subject", needs="pillow"),
    "image.edge_decontam": op(op_edge_decontam, [
        P("image", "file", True, help="RGBA cut-out"),
        P("spill_color", "choice", default="green", choices=["green", "blue", "none"]),
        P("erode", "int", default=1, min=0, max=5), P("feather", "int", default=0, min=0, max=5),
        P("output", "outfile"),
    ], "Remove colour spill and shave the alpha edge of a cut-out", needs="pillow"),
    "image.outpaint_prepare": op(op_outpaint_prepare, [
        P("image", "file", True), P("left", "int", default=0, min=0, max=4096),
        P("right", "int", default=0, min=0, max=4096), P("top", "int", default=0, min=0, max=4096),
        P("bottom", "int", default=0, min=0, max=4096),
        P("fill", "choice", default="edge", choices=["edge", "mean", "white"]),
        P("feather", "int", default=32, min=0, max=256),
    ], "Extend the canvas and write the matching outpaint mask", needs="pillow"),
    "image.upscale": op(op_upscale, [
        P("image", "file", True), P("scale", "choice", default="2", choices=_CHOICE_SCALE),
        P("method", "choice", default="lanczos", choices=["lanczos", "bicubic", "nearest"]),
        P("sharpen", "bool", default=False), P("output", "outfile"),
    ], "Classical (non-generative) upscaling", needs="pillow"),
    "image.denoise_grain": op(op_denoise_grain, [
        P("image", "file", True), P("mode", "choice", default="both", choices=["denoise", "grain", "both"]),
        P("median_size", "int", default=3, min=1, max=7), P("grain_amount", "float", default=0.05, min=0, max=0.5),
        P("grain_seed", "int", default=0, min=0, max=2**31 - 1), P("output", "outfile"),
    ], "Median denoise and seeded film-style grain", needs="pillow"),
    "image.sharpen": op(op_sharpen, [
        P("image", "file", True), P("radius", "float", default=2.0, min=0.1, max=20),
        P("percent", "int", default=120, min=0, max=500), P("threshold", "int", default=3, min=0, max=255),
        P("output", "outfile"),
    ], "Unsharp-mask sharpening", needs="pillow"),
    "image.color_adjust": op(op_color_adjust, [
        P("image", "file", True), P("preset", "choice", default="custom", choices=["custom", "night", "cinematic", "real-estate"]),
        P("brightness", "float", default=1.0, min=0, max=3), P("contrast", "float", default=1.0, min=0, max=3),
        P("saturation", "float", default=1.0, min=0, max=3), P("temperature", "int", default=0, min=-100, max=100),
        P("output", "outfile"),
    ], "Brightness, contrast, saturation and colour-temperature grade or preset", needs="pillow"),
    "image.palette_transfer": op(op_palette_transfer, [
        P("image", "file", True), P("reference", "file", True),
        P("strength", "float", default=1.0, min=0, max=1), P("output", "outfile"),
    ], "Transfer colour statistics from a reference image (Reinhard-style)", needs="pillow"),
    "image.consistency_report": op(op_consistency, [
        P("images", "files", True), P("metric", "choice", default="style", choices=["style", "lighting", "catalog"]),
    ], "Compare colour and lighting statistics across a set and flag outliers", needs="pillow"),
    "image.color_convert": op(op_color_convert, [
        P("image", "file", True), P("target", "choice", default="srgb", choices=["srgb", "display-p3", "cmyk"]),
        P("profile", "file", help="ICC profile file (required for display-p3 and cmyk unless configured)"),
        P("strip_icc", "bool", default=False), P("output", "outfile"),
    ], "Convert colour profile with LittleCMS (sRGB, Display P3, CMYK preview)", needs="pillow"),
    "image.text_overlay": op(op_text_overlay, [
        P("image", "file", True), P("text", "str", True), P("mode", "choice", default="overlay", choices=["overlay", "replace", "disclosure"]),
        P("anchor", "choice", default="top-left", choices=["top-left", "top-right", "bottom-left", "bottom-right", "center"]),
        P("font_size", "int", default=36, min=8, max=400), P("color", "str", default="#ffffff"),
        P("plate", "bool", default=False), P("fill_box", "intlist", help="[x, y, w, h] area to repaint before text"),
        P("output", "outfile"),
    ], "Overlay accurate text, repaint a box, or add a visible disclosure label", needs="pillow"),
    "image.pixel_art": op(op_pixel_art, [
        P("image", "file", True), P("palette_size", "int", default=16, min=2, max=256),
        P("scale", "int", default=4, min=1, max=16), P("grid", "int", default=0, min=0, max=4096),
        P("output", "outfile"),
    ], "Palette reduction, pixel grid downsample and nearest-neighbour scale-up", needs="pillow"),
    "image.tiling": op(op_tiling, [
        P("image", "file", True), P("mode", "choice", default="check", choices=["check", "preview"]),
        P("repeats", "int", default=2, min=1, max=6), P("output", "outfile"),
    ], "Seamless-wrap estimate or repeated tile preview", needs="pillow"),
    "image.edge_map": op(op_edge_map, [
        P("image", "file", True), P("mode", "choice", default="edges", choices=["edges", "line-drawing"]),
        P("threshold", "int", default=40, min=0, max=255), P("monochrome", "bool", default=True),
        P("invert", "bool", default=False), P("output", "outfile"),
    ], "Edge and line-drawing map for control inputs or monochrome figures", needs="pillow"),
    "image.dataset_curate": op(op_dataset_curate, [
        P("folder", "dir", True), P("min_width", "int", default=512, min=1), P("min_height", "int", default=512, min=1),
        P("dedupe_threshold", "int", default=5, min=0, max=32), P("output", "outfile"),
    ], "Manifest of size, blank and near-duplicate status for a training folder (no deletions)", needs="pillow"),
    "image.near_duplicate": op(op_near_duplicate, [
        P("reference_dir", "dir", True), P("candidate_dir", "dir", True),
        P("threshold", "int", default=8, min=0, max=32),
    ], "Find candidate images perceptually close to reference images", needs="pillow"),
    "image.metadata_sidecar": op(op_metadata_sidecar, [
        P("mode", "choice", default="write", choices=["write", "verify"]),
        P("image", "file", True), P("prompt", "str"), P("model", "str"), P("seed", "int", min=0),
        P("edits", "list", help="Comma list of edits applied"), P("license", "str"), P("creator", "str"),
        P("sidecar", "outfile", help="Sidecar JSON path (verify: path to read)"),
    ], "Write or verify a provenance sidecar with the image SHA-256", needs="pillow"),
    "image.authenticity": op(op_authenticity, [P("images", "files", True)],
                             "Uncertain provenance indicators: metadata, generator text, content-credential hints, error level", needs="pillow"),
    "image.redact": op(op_redact, [
        P("image", "file", True), P("mode", "choice", default="scan", choices=["scan", "strip-metadata", "pixelate", "blur", "box-fill"]),
        P("boxes", "json", help="List of [x, y, w, h] regions"), P("block", "int", default=16, min=2, max=256),
        P("blur_radius", "int", default=12, min=1, max=200), P("output", "outfile"),
    ], "Scan metadata, or write a redacted copy with boxed areas pixelated, blurred or filled", needs="pillow"),
}
