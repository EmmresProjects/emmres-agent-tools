#!/usr/bin/env python3
"""Deterministic offline image operations: convert, mask, canvas, composite.

Role: the pixel-level work that does not need a diffusion runtime. Format
export with metadata handling, alpha flattening, resize, mask generation and
mask cleanup (feather, island removal, hole filling), outpainting canvas
expansion with a matching inpaint mask, and alpha compositing.

All operations run through core.load_image / core.save_image, so they work
with Pillow when it is installed and with the bundled pure-stdlib PNG codec
when it is not (PNG output only in that case).
"""

from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_spec = importlib.util.spec_from_file_location("foic_core", os.path.join(_HERE, "tools-image-core.py"))
core = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("foic_core", core)
_spec.loader.exec_module(core)

ToolError = core.ToolError
LOG = core.LOG

SUPPORTED_OUTPUT_FORMATS = ("png", "jpeg", "webp", "tiff")


def _parse_color(text, default=(255, 255, 255)):
    if text is None:
        return default
    value = text.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ToolError("--background expects #RRGGBB, got %r" % text, code="usage", exit_code=core.EXIT_USAGE)
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ToolError("--background expects hex digits, got %r" % text, code="usage", exit_code=core.EXIT_USAGE)


def _require_pixels(img, path):
    if not img.has_pixels:
        raise ToolError(
            "pixel access unavailable for %s: install Pillow for non-PNG formats" % path,
            code="backend-unavailable",
        )
    return img


def _mask_from_image(img, mode, threshold, path="input"):
    """Return (w, h, gray_bytes) single-channel mask."""
    _require_pixels(img, path)
    w, h, px = img.width, img.height, img.pixels
    gray = bytearray(w * h)
    if mode == "alpha":
        if not img.has_alpha:
            raise ToolError("mask mode 'alpha' needs an image with an alpha channel", code="no-alpha")
        for i in range(w * h):
            gray[i] = px[i * 4 + 3]
    else:
        g = core.to_gray(img)
        if mode == "luma":
            gray[:] = g
        elif mode == "threshold":
            for i in range(w * h):
                gray[i] = 255 if g[i] >= threshold else 0
        else:
            raise ToolError("unknown mask mode %r" % mode, code="usage", exit_code=core.EXIT_USAGE)
    return w, h, bytes(gray)


def _box_blur(gray, w, h, radius, passes=2):
    if radius <= 0:
        return gray
    buf = bytearray(gray)
    for _ in range(passes):
        # horizontal pass with a running sum
        out = bytearray(len(buf))
        window = 2 * radius + 1
        for y in range(h):
            row = y * w
            total = 0
            for x in range(-radius, radius + 1):
                cx = min(max(x, 0), w - 1)
                total += buf[row + cx]
            for x in range(w):
                out[row + x] = total // window
                add = min(x + radius + 1, w - 1)
                sub = max(x - radius, 0)
                total += buf[row + add] - buf[row + sub]
        buf = out
        # vertical pass
        out = bytearray(len(buf))
        for x in range(w):
            total = 0
            for y in range(-radius, radius + 1):
                cy = min(max(y, 0), h - 1)
                total += buf[cy * w + x]
            for y in range(h):
                out[y * w + x] = total // window
                add = min(y + radius + 1, h - 1)
                sub = max(y - radius, 0)
                total += buf[add * w + x] - buf[sub * w + x]
        buf = out
    return bytes(buf)


def _components(gray, w, h, min_px, fill_holes):
    """Label connected white regions; optionally drop small ones and fill holes."""
    visited = bytearray(w * h)
    labels = [0] * (w * h)
    sizes = []
    current = 0
    for start in range(w * h):
        if visited[start] or gray[start] < 128:
            continue
        current += 1
        stack = [start]
        visited[start] = 1
        size = 0
        member_label = current
        while stack:
            idx = stack.pop()
            labels[idx] = member_label
            size += 1
            x, y = idx % w, idx // w
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    nidx = ny * w + nx
                    if not visited[nidx] and gray[nidx] >= 128:
                        visited[nidx] = 1
                        stack.append(nidx)
        sizes.append(size)
    # background (black) components are holes when enclosed
    holes_filled = 0
    if fill_holes:
        black_visited = bytearray(w * h)
        for start in range(w * h):
            if black_visited[start] or gray[start] >= 128:
                continue
            stack = [start]
            black_visited[start] = 1
            region = []
            touches_border = False
            while stack:
                idx = stack.pop()
                region.append(idx)
                x, y = idx % w, idx // w
                if x == 0 or y == 0 or x == w - 1 or y == h - 1:
                    touches_border = True
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        nidx = ny * w + nx
                        if not black_visited[nidx] and gray[nidx] < 128:
                            black_visited[nidx] = 1
                            stack.append(nidx)
            if not touches_border and 0 < len(region) <= fill_holes:
                for idx in region:
                    gray[idx] = 255
                holes_filled += 1
    removed = 0
    if min_px > 1:
        for idx in range(w * h):
            lab = labels[idx]
            if lab and sizes[lab - 1] < min_px:
                gray[idx] = 0
                removed += 1
    return removed, holes_filled, len(sizes)


def build_mask(src, dst, cfg, mode="alpha", threshold=128, invert=False, feather=0,
               min_island_px=0, fill_holes=0, overwrite=False):
    img = core.load_image(src, cfg)
    w, h, gray = _mask_from_image(img, mode, threshold, src)
    gray = bytearray(gray)
    if invert:
        for i in range(len(gray)):
            gray[i] = 255 - gray[i]
    if feather:
        gray = bytearray(_box_blur(bytes(gray), w, h, int(feather)))
    removed = holes = 0
    components = None
    if min_island_px or fill_holes:
        removed, holes, components = _components(gray, w, h, int(min_island_px or 0), int(fill_holes or 0))
    mask_img = core.ImageData(w, h, "L", bytes(gray), "PNG", img.backend)
    core.save_image(dst, mask_img, cfg, fmt="png", overwrite=overwrite)
    covered = sum(1 for v in gray if v >= 128)
    return {
        "schema": "foic.mask/1",
        "source": os.path.abspath(src),
        "output": os.path.abspath(dst),
        "mode": mode,
        "threshold": threshold,
        "invert": invert,
        "feather": feather,
        "width": w,
        "height": h,
        "coverage": round(covered / float(w * h), 4),
        "island_pixels_removed": removed,
        "holes_filled": holes,
        "components": components,
        "sha256": core.sha256_file(dst)[0],
    }


def composite_flatten(img, background):
    if not img.has_alpha:
        return img
    px = img.pixels
    out = bytearray(img.width * img.height * 3)
    br, bg_, bb = background
    for i in range(img.width * img.height):
        o = i * 4
        a = px[o + 3]
        if a == 255:
            out[i * 3 : i * 3 + 3] = px[o : o + 3]
        elif a == 0:
            out[i * 3 : i * 3 + 3] = bytes((br, bg_, bb))
        else:
            inv = 255 - a
            out[i * 3 + 0] = (px[o] * a + br * inv) // 255
            out[i * 3 + 1] = (px[o + 1] * a + bg_ * inv) // 255
            out[i * 3 + 2] = (px[o + 2] * a + bb * inv) // 255
    return core.ImageData(img.width, img.height, "RGB", bytes(out), img.format, img.backend, img.meta)


def convert_image(src, dst, cfg, fmt="png", quality=95, strip_metadata=False,
                  background=None, flatten_alpha=False, max_width=None, max_height=None,
                  scale=None, overwrite=False):
    fmt = (fmt or "png").lower()
    if fmt not in SUPPORTED_OUTPUT_FORMATS:
        raise ToolError(
            "unsupported output format %r (supported: %s)" % (fmt, ", ".join(SUPPORTED_OUTPUT_FORMATS)),
            code="usage",
            exit_code=core.EXIT_USAGE,
        )
    img = core.load_image(src, cfg)
    img = _require_pixels(img, src)
    notes = []
    if strip_metadata:
        img.meta.pop("exif", None)
        img.meta.pop("icc", None)
        notes.append("metadata stripped on export")
    if flatten_alpha and img.has_alpha:
        img = composite_flatten(img, _parse_color(background))
        notes.append("alpha flattened over #%02x%02x%02x" % _parse_color(background))
    elif fmt == "jpeg" and img.has_alpha:
        img = composite_flatten(img, _parse_color(background, (255, 255, 255)))
        notes.append("alpha flattened over #%02x%02x%02x because JPEG has no alpha" % _parse_color(background, (255, 255, 255)))
    orig_w, orig_h = img.width, img.height
    new_w, new_h = img.width, img.height
    if scale and scale > 0:
        new_w = max(1, int(round(img.width * scale)))
        new_h = max(1, int(round(img.height * scale)))
    if max_width and new_w > max_width:
        ratio = max_width / float(new_w)
        new_w, new_h = max_width, max(1, int(round(new_h * ratio)))
    if max_height and new_h > max_height:
        ratio = max_height / float(new_h)
        new_h, new_w = max_height, max(1, int(round(new_w * ratio)))
    if (new_w, new_h) != (img.width, img.height):
        w, h, px = core.resize_rgba_nearest(img, new_w, new_h)
        img = core.ImageData(w, h, img.mode, px, img.format, img.backend, img.meta)
        notes.append("resized %dx%d -> %dx%d (nearest)" % (orig_w, orig_h, new_w, new_h))
    core.save_image(dst, img, cfg, fmt=fmt, quality=quality, overwrite=overwrite, strip_metadata=strip_metadata)
    return {
        "schema": "foic.convert/1",
        "source": os.path.abspath(src),
        "output": os.path.abspath(dst),
        "format": fmt.upper(),
        "quality": int(quality) if fmt in ("jpeg", "webp") else None,
        "width": img.width,
        "height": img.height,
        "mode": img.mode,
        "backend": img.backend,
        "bytes": os.path.getsize(dst),
        "sha256": core.sha256_file(dst)[0],
        "notes": notes,
    }


def expand_canvas(src, dst, cfg, left=0, right=0, top=0, bottom=0, fill="edge",
                  background="#ffffff", mask_out=None, overwrite=False):
    """Expand the canvas for outpainting and emit the matching inpaint mask."""
    img = core.load_image(src, cfg)
    img = _require_pixels(img, src)
    left, right, top, bottom = (max(0, int(v)) for v in (left, right, top, bottom))
    if left + right + top + bottom == 0:
        raise ToolError("canvas expansion requires at least one non-zero side", code="usage", exit_code=core.EXIT_USAGE)
    new_w, new_h = img.width + left + right, img.height + top + bottom
    ch = img.channels
    bg = _parse_color(background)
    out = bytearray(new_w * new_h * ch)
    src_px = img.pixels
    for y in range(new_h):
        sy = min(max(y - top, 0), img.height - 1)
        for x in range(new_w):
            sx = min(max(x - left, 0), img.width - 1)
            inside = left <= x < left + img.width and top <= y < top + img.height
            do = (y * new_w + x) * ch
            if inside:
                so = ((y - top) * img.width + (x - left)) * ch
                out[do : do + ch] = src_px[so : so + ch]
            elif fill == "edge":
                so = (sy * img.width + sx) * ch
                out[do : do + ch] = src_px[so : so + ch]
            else:
                out[do : do + ch] = bytes(bg) + ((b"\xff",) if ch == 4 else b"")
    canvas_img = core.ImageData(new_w, new_h, img.mode, bytes(out), "PNG", img.backend, img.meta)
    core.save_image(dst, canvas_img, cfg, fmt="png", overwrite=overwrite)
    result = {
        "schema": "foic.canvas/1",
        "source": os.path.abspath(src),
        "output": os.path.abspath(dst),
        "original": [img.width, img.height],
        "expanded": [new_w, new_h],
        "expand": {"left": left, "right": right, "top": top, "bottom": bottom},
        "fill": fill,
        "sha256": core.sha256_file(dst)[0],
    }
    if mask_out:
        mask = bytearray(new_w * new_h)
        for y in range(new_h):
            for x in range(new_w):
                if not (left <= x < left + img.width and top <= y < top + img.height):
                    mask[y * new_w + x] = 255
        core.save_image(
            mask_out, core.ImageData(new_w, new_h, "L", bytes(mask), "PNG", img.backend),
            cfg, fmt="png", overwrite=overwrite,
        )
        result["mask_output"] = os.path.abspath(mask_out)
        result["mask_region_ratio"] = round(sum(1 for v in mask if v) / float(new_w * new_h), 4)
    return result
