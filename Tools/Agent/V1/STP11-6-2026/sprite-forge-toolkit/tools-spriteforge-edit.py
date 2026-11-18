#!/usr/bin/env python3
"""spriteforge edit: local edits, cleanup, repair, recolour and compositing.

Every operation accepts a still image, an animated GIF/APNG/WebP, or a folder of
frames, and writes the same shape it was given (file -> file, folder -> folder).
Per-frame timing is preserved for animated inputs and written to
``edit-manifest.json`` for folders.

Operations
----------
  crop              crop by absolute or relative box
  pad               pad/centre on a new canvas
  offset            shift pixels with wrap or fill
  resize            resize with nearest/bilinear/lanczos
  rotate            rotate about the centre
  transform         rotate + scale + pad in one pass
  outline           add an inner or outer outline
  threshold-alpha   harden or cut out alpha
  decontaminate     remove matte colour spill at soft edges
  remove-bg         key out a flat background colour
  palette-map       remap colours onto a palette
  quantize          reduce the colour count
  swap-color        replace one colour range within tolerance
  hue-shift         shift hue/saturation/value
  pixelate          downsample then upsample for clean pixel clusters
  despill           neutralise chroma fringes
  despeckle         drop isolated stray pixels
  alpha-bleed       fill RGB under transparent pixels
  clean-edges       drop hidden RGB noise and harden alpha
  normalize-size    put every frame on one canvas with a shared anchor
  symmetry          mirror one half then re-apply asymmetry
  upscale           nearest / pixel-aware upscale
  downscale         silhouette-preserving downscale
  recolor-series    batch recolour across a folder
  composite         flatten layered parts (back to front)
"""

from __future__ import annotations

import argparse
import colorsys
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_edit", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_edit"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageChops = core.ImageChops
ImageDraw = core.ImageDraw
ImageFilter = core.ImageFilter
ImageOps = core.ImageOps


# --------------------------------------------------------------------------- #
# Frame I/O wrappers
# --------------------------------------------------------------------------- #

def run_frames(args, root: Path, rep: core.Reporter, fn, *, describe: str,
               ext_override: str | None = None):
    """Apply ``fn(image, index) -> image`` to a file, animation or folder."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.exists():
        raise SFError("INPUT_NOT_FOUND", f"input not found: {src}")
    out = Path(args.output) if Path(args.output).is_absolute() else root / args.output
    is_dir_input = src.is_dir()
    if is_dir_input:
        out_dir = core.guard_output(out, root, args.allow_outside_root, as_dir=True)
        fs = core.load_sequence(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
        results = []
        for i, frame in enumerate(fs.frames):
            rep.deadline.check()
            results.append(fn(frame, i))
        if not args.dry_run:
            for i, im in enumerate(results):
                core.save_image(im, out_dir / f"{src.name}_{i:04d}.png")
            core.save_json({"operation": describe, "source": str(src), "frames": len(results),
                            "files": [f"{src.name}_{i:04d}.png" for i in range(len(results))]},
                           out_dir / "edit-manifest.json")
            rep.add_output(out_dir, "directory", frames=len(results))
        rep.stat("frames", len(results))
        return {"summary": {"frames": len(results), "out": str(out_dir)}}
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    results = [fn(frame, i) for i, frame in enumerate(fs.frames)]
    rep.deadline.check()
    animated = len(results) > 1 and src.suffix.lower() in {".gif", ".png", ".webp", ".apng"}
    target_ext = ext_override or out.suffix.lower()
    core.ensure_ext(out, [".png", ".gif", ".webp", ".bmp", ".tga", ".tiff", ".jpg", ".jpeg"],
                    name="--output")
    # single-image branch still has to respect the workspace root
    out = core.guard_output(out, root, args.allow_outside_root)
    if animated and target_ext in {".gif", ".png", ".webp"}:
        if not args.dry_run:
            core.save_animated(results, out, fs.durations_ms, loop=fs.loop)
            rep.add_output(out, "animation", frames=len(results))
    else:
        if not args.dry_run:
            core.save_image(results[0], out)
            rep.add_output(out, "image")
    rep.stat("frames", len(results))
    return {"summary": {"frames": len(results), "size": list(results[0].size), "output": str(out)}}


def _resample(name: str):
    return {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR,
            "bicubic": Image.Resampling.BICUBIC, "lanczos": Image.Resampling.LANCZOS}[name]


def _colour(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) == 8:
        v = v[:6]
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        raise SFError("BAD_COLOR", f"expected a hex colour like #aabbcc, got '{value}'")


# --------------------------------------------------------------------------- #
# Pure image operations (each takes an RGBA image and returns an RGBA image)
# --------------------------------------------------------------------------- #

def do_crop(im, box: tuple[int, int, int, int]):
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= im.width and 0 <= y0 < y1 <= im.height):
        raise SFError("BAD_BOX", f"crop box {box} is outside the {im.width}x{im.height} image")
    return im.crop(box)


def do_pad(im, size: tuple[int, int], anchor: str = "center", fill=(0, 0, 0, 0)):
    if size[0] < im.width or size[1] < im.height:
        raise SFError("BAD_SIZE", f"pad target {size} is smaller than {im.size}")
    dx, dy = size[0] - im.width, size[1] - im.height
    positions = {
        "center": (dx // 2, dy // 2), "top-left": (0, 0), "top": (dx // 2, 0),
        "top-right": (dx, 0), "left": (0, dy // 2), "right": (dx, dy // 2),
        "bottom-left": (0, dy), "bottom": (dx // 2, dy), "bottom-right": (dx, dy),
        "bottom-center": (dx // 2, dy), "top-center": (dx // 2, 0),
    }
    if anchor not in positions:
        raise SFError("BAD_ANCHOR", f"anchor must be one of {sorted(positions)}")
    return core.pad_to(im, size, positions[anchor], fill)


def do_offset(im, dx: int, dy: int, wrap: bool):
    if wrap:
        out = Image.new("RGBA", im.size)
        w, h = im.size
        for y in range(h):
            for x in range(w):
                out.putpixel(((x + dx) % w, (y + dy) % h), im.getpixel((x, y)))
        return out
    canvas = Image.new("RGBA", im.size, (0, 0, 0, 0))
    canvas.paste(im, (dx, dy))
    return canvas


def do_outline(im, *, colour=(16, 16, 24), width=1, mode="outer"):
    alpha = im.split()[-1]
    mask = alpha.point(lambda v: 255 if v > 0 else 0)
    if mode == "outer":
        grown = mask.filter(ImageFilter.MaxFilter(max(3, width * 2 + 1)))
        halo = Image.new("RGBA", im.size, tuple(colour) + (255,))
        halo.putalpha(grown)
        halo.alpha_composite(im)
        return halo
    # inner outline: keep the border between the eroded mask and the body
    eroded = mask.filter(ImageFilter.MinFilter(max(3, width * 2 + 1)))
    ring = ImageChops.subtract(mask, eroded)
    out = im.copy()
    col = Image.new("RGBA", im.size, tuple(colour) + (255,))
    out.paste(col, mask=ring)
    return out


def do_threshold_alpha(im, threshold: int):
    r, g, b, a = im.split()
    a = a.point(lambda v: 255 if v >= threshold else 0)
    return Image.merge("RGBA", (r, g, b, a))


def do_decontaminate(im, iterations: int = 2, threshold: int = 250):
    """Pull colour from nearby opaque pixels into semi transparent edge pixels."""
    out = im.copy()
    arr = out.load()
    for _ in range(max(1, iterations)):
        src = out.copy().load()
        coords = [(x, y) for y in range(out.height) for x in range(out.width) if src[x, y][3] < threshold]
        for x, y in coords:
            r, g, b, a = src[x, y]
            if a == 0:
                continue
            acc = [0, 0, 0]
            n = 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < out.width and 0 <= ny < out.height:
                        pr, pg, pb, pa = src[nx, ny]
                        if pa > a:
                            acc[0] += pr
                            acc[1] += pg
                            acc[2] += pb
                            n += 1
            if n:
                arr[x, y] = (acc[0] // n, acc[1] // n, acc[2] // n, a)
    return out


def do_remove_bg(im, colour=(255, 255, 255), tolerance: int = 12, feather: int = 0):
    target = tuple(colour)
    data = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = data[x, y]
            if abs(r - target[0]) <= tolerance and abs(g - target[1]) <= tolerance and abs(b - target[2]) <= tolerance:
                data[x, y] = (r, g, b, 0)
    if feather > 0:
        alpha = im.split()[-1].filter(ImageFilter.GaussianBlur(feather))
        r, g, b, _ = im.split()
        im = Image.merge("RGBA", (r, g, b, alpha))
    return im


def do_palette_map(im, palette: list[tuple[int, int, int]], *, dither: bool = False):
    if not palette:
        raise SFError("EMPTY_PALETTE", "palette-map needs at least one colour")
    pal_img = Image.new("P", (1, 1))
    flat = []
    for c in palette:
        flat.extend(c)
    flat += [0] * (768 - len(flat))
    pal_img.putpalette(flat)
    rgb = im.convert("RGB")
    quant = rgb.quantize(palette=pal_img,
                         dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    out = quant.convert("RGBA")
    out.putalpha(im.split()[-1])
    return out


def do_quantize(im, colours: int, dither: bool):
    if not 2 <= colours <= 256:
        raise SFError("BAD_ARGUMENT", "--colours must be between 2 and 256")
    alpha = im.split()[-1]
    quant = im.convert("RGB").quantize(colors=colours,
                                       method=Image.Quantize.MEDIANCUT,
                                       dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    out = quant.convert("RGBA")
    out.putalpha(alpha)
    return out


def do_swap_color(im, old: tuple[int, int, int], new: tuple[int, int, int], tolerance: int):
    data = im.load()
    changed = 0
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = data[x, y]
            if a and abs(r - old[0]) <= tolerance and abs(g - old[1]) <= tolerance and abs(b - old[2]) <= tolerance:
                data[x, y] = (new[0], new[1], new[2], a)
                changed += 1
    return im, changed


def do_hue_shift(im, dh: float, ds: float, dl: float, *, protect_neutral: bool = True):
    data = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = data[x, y]
            if a == 0:
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if protect_neutral and s < 0.06:
                continue
            h = (h + dh) % 1.0
            s = max(0.0, min(1.0, s + ds))
            l = max(0.0, min(1.0, l + dl))
            nr, ng, nb = colorsys.hls_to_rgb(h, l, s)
            data[x, y] = (int(nr * 255), int(ng * 255), int(nb * 255), a)
    return im


def do_pixelate(im, block: int, *, method: str = "nearest"):
    if block < 1:
        raise SFError("BAD_ARGUMENT", "--block must be >= 1")
    small = im.resize((max(1, im.width // block), max(1, im.height // block)), Image.Resampling.BOX)
    return small.resize(im.size, _resample(method))


def do_despill(im, strength: float = 0.6):
    data = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = data[x, y]
            if a == 0:
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            if mx == 0:
                continue
            sat = (mx - mn) / mx
            if sat > 0.5 and mx in (g, b) and r == mn:
                # green/blue fringe: pull the dominant channel toward the average
                avg = (r + g + b) // 3
                data[x, y] = (r, int(g + (avg - g) * strength), int(b + (avg - b) * strength), a)
    return im


def do_despeckle(im, min_neighbours: int = 1):
    mask = im.split()[-1].point(lambda v: 1 if v > 0 else 0)
    src = mask.load()
    out = im.copy()
    data = out.load()
    for y in range(im.height):
        for x in range(im.width):
            if not src[x, y]:
                continue
            n = 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < im.width and 0 <= ny < im.height and src[nx, ny]:
                        n += 1
            if n <= min_neighbours:
                r, g, b, _ = data[x, y]
                data[x, y] = (r, g, b, 0)
    return out


def do_alpha_bleed(im, radius: int = 2):
    """Fill RGB of transparent pixels with nearby opaque colour to stop edge bleed."""
    out = im.copy()
    rgb = out.convert("RGB")
    alpha = out.split()[-1]
    solid = alpha.point(lambda v: 255 if v > 0 else 0)
    fill = rgb.copy()
    fill.paste((0, 0, 0), mask=solid.point(lambda v: 255 - v))
    for _ in range(max(1, radius)):
        blurred = fill.filter(ImageFilter.GaussianBlur(1.2))
        solid = solid.filter(ImageFilter.MaxFilter(3))
        fill = Image.composite(rgb, blurred, solid)
    r, g, b = fill.split()
    return Image.merge("RGBA", (r, g, b, alpha))


def do_clean_edges(im, hard_threshold: int = 8):
    r, g, b, a = im.split()
    a = a.point(lambda v: 0 if v <= hard_threshold else (255 if v >= 247 else v))
    out = Image.merge("RGBA", (r, g, b, a))
    return do_alpha_bleed(out, 1) if hard_threshold >= 0 else out


def do_normalize(frames, size: tuple[int, int], anchor: str):
    out = []
    anchor_map = {"bottom-center": (0.5, 1.0), "center": (0.5, 0.5), "top-center": (0.5, 0.0),
                  "bottom-left": (0.0, 1.0), "bottom-right": (1.0, 1.0)}
    if anchor not in anchor_map:
        raise SFError("BAD_ANCHOR", f"anchor must be one of {sorted(anchor_map)}")
    ax, ay = anchor_map[anchor]
    for f in frames:
        if f.size == size:
            out.append(f.copy())
            continue
        scaled = ImageOps.contain(f, size, Image.Resampling.NEAREST)
        x = int((size[0] - scaled.width) * ax)
        y = int((size[1] - scaled.height) * ay)
        out.append(core.pad_to(scaled, size, (x, y)))
    return out


def do_symmetry(im, keep: str = "left", regain: float = 0.0):
    w, h = im.size
    half = w // 2
    left = im.crop((0, 0, half, h))
    right = left.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    base = Image.new("RGBA", (half * 2, h))
    base.paste(left, (0, 0))
    base.paste(right, (half, 0))
    if keep == "left":
        base.paste(im.crop((half, 0, w, h)), (half, 0))
    elif keep == "right":
        base.paste(im.crop((0, 0, half, h)), (0, 0))
    if regain > 0:
        mask = Image.new("L", im.size, 0)
        d = ImageDraw.Draw(mask)
        d.rectangle([half, 0, w, h], fill=int(255 * regain))
        base = Image.composite(im, base, mask)
    return base


def do_composite(layers: list[Image.Image]):
    w = max(l.width for l in layers)
    h = max(l.height for l in layers)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for l in layers:
        out.alpha_composite(l, ((w - l.width) // 2, (h - l.height) // 2))
    return out


# --------------------------------------------------------------------------- #
# CLI operations
# --------------------------------------------------------------------------- #

def op_crop(args, root, rep):
    """Crop by absolute or relative box."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    probe = core.load_image(src, max_pixels=args.max_pixels)
    if args.box:
        box = tuple(core.parse_int_list(args.box, name="box"))
        if len(box) != 4:
            raise SFError("BAD_BOX", "--box needs 4 integers x0,y0,x1,y1")
    else:
        parts = [v for v in args.relative.replace(",", " ").split() if v]
        if len(parts) != 4:
            raise SFError("BAD_BOX", "--relative needs 4 floats x,y,w,h in 0..1")
        try:
            rx, ry, rw, rh = (float(v) for v in parts)
        except ValueError:
            raise SFError("BAD_BOX", f"--relative expects numbers, got '{args.relative}'")
        if not all(0.0 <= v <= 1.0 for v in (rx, ry, rw, rh)) or rx + rw > 1.0001 or ry + rh > 1.0001:
            raise SFError("BAD_BOX", "--relative values must be in 0..1 and x+w / y+h must not exceed 1")
        box = (int(rx * probe.width), int(ry * probe.height),
               int((rx + rw) * probe.width), int((ry + rh) * probe.height))
    return run_frames(args, root, rep, lambda im, i: do_crop(im, box), describe="crop")


def op_pad(args, root, rep):
    """Pad or centre a sprite on a new canvas."""
    size = core.parse_size(args.size)
    return run_frames(args, root, rep, lambda im, i: do_pad(im, size, args.anchor),
                      describe="pad")


def op_offset(args, root, rep):
    """Shift pixels with wrap or transparent fill."""
    return run_frames(args, root, rep, lambda im, i: do_offset(im, args.dx, args.dy, args.wrap),
                      describe="offset")


def op_resize(args, root, rep):
    """Resize with nearest, bilinear, bicubic or lanczos resampling."""
    if args.factor:
        factor = args.factor
        return run_frames(args, root, rep,
                          lambda im, i: im.resize((max(1, round(im.width * factor)),
                                                   max(1, round(im.height * factor))),
                                                  _resample(args.resample)),
                          describe="resize")
    size = core.parse_size(args.size)
    return run_frames(args, root, rep, lambda im, i: im.resize(size, _resample(args.resample)),
                      describe="resize")


def op_rotate(args, root, rep):
    """Rotate about the centre with nearest or bicubic resampling."""
    resample = _resample(args.resample)
    return run_frames(args, root, rep,
                      lambda im, i: im.rotate(args.degrees, resample=resample, expand=args.expand),
                      describe="rotate")


def op_transform(args, root, rep):
    """Rotate, scale and pad an image in one declarative pass."""
    size = core.parse_size(args.size) if args.size else None

    def fn(im, i):
        out = im
        if args.rotate:
            out = out.rotate(args.rotate, resample=_resample(args.resample), expand=False)
        if args.scale != 1.0:
            out = out.resize((max(1, round(out.width * args.scale)),
                              max(1, round(out.height * args.scale))), _resample(args.resample))
        if size:
            out = do_pad(out, size, args.anchor)
        return out
    return run_frames(args, root, rep, fn, describe="transform")


def op_outline(args, root, rep):
    """Add an inner or outer outline in a chosen colour."""
    colour = _colour(args.color)
    return run_frames(args, root, rep,
                      lambda im, i: do_outline(im, colour=colour, width=args.width, mode=args.mode),
                      describe="outline")


def op_threshold_alpha(args, root, rep):
    """Harden or cut out alpha at a threshold (matte cleanup)."""
    return run_frames(args, root, rep, lambda im, i: do_threshold_alpha(im, args.threshold),
                      describe="threshold-alpha")


def op_decontaminate(args, root, rep):
    """Remove matte colour spill at soft edges."""
    return run_frames(args, root, rep,
                      lambda im, i: do_decontaminate(im, args.iterations, args.threshold),
                      describe="decontaminate")


def op_remove_bg(args, root, rep):
    """Key out a flat background colour with tolerance and feather."""
    colour = _colour(args.color)
    return run_frames(args, root, rep,
                      lambda im, i: do_remove_bg(im, colour, args.tolerance, args.feather),
                      describe="remove-bg")


def _load_palette(path: Path, rep: core.Reporter) -> list[tuple[int, int, int]]:
    data = core.load_json(path)
    colours: list[tuple[int, int, int]] = []
    if isinstance(data, list):
        for v in data:
            colours.append(_colour(str(v)))
    elif isinstance(data, dict):
        for key in ("roles", "colours", "colors", "palette"):
            block = data.get(key)
            if isinstance(block, dict):
                for v in block.values():
                    colours.append(_colour(str(v)))
            elif isinstance(block, list):
                for v in block:
                    colours.append(_colour(str(v)))
        if not colours and all(isinstance(v, str) for v in data.values()):
            colours = [_colour(str(v)) for v in data.values()]
    if not colours:
        raise SFError("EMPTY_PALETTE", f"no colours found in {path.name}")
    uniq = list(dict.fromkeys(colours))
    rep.stat("palette_colours", len(uniq))
    return uniq


def op_palette_map(args, root, rep):
    """Remap colours onto a named palette while preserving alpha."""
    pal = _load_palette(Path(args.palette) if Path(args.palette).is_absolute() else root / args.palette, rep)
    return run_frames(args, root, rep,
                      lambda im, i: do_palette_map(im, pal, dither=args.dither),
                      describe="palette-map")


def op_quantize(args, root, rep):
    """Reduce the colour count for indexed or retro targets."""
    return run_frames(args, root, rep, lambda im, i: do_quantize(im, args.colours, args.dither),
                      describe="quantize")


def op_swap_color(args, root, rep):
    """Replace one colour range within a tolerance."""
    old, new = _colour(args.old), _colour(args.new)
    changed_total = {"n": 0}

    def fn(im, i):
        out, n = do_swap_color(im, old, new, args.tolerance)
        changed_total["n"] += n
        return out
    result = run_frames(args, root, rep, fn, describe="swap-color")
    result["summary"]["pixels_changed"] = changed_total["n"]
    if changed_total["n"] == 0:
        rep.warn("NO_MATCH", "no pixels matched the source colour; raise --tolerance")
    return result


def op_hue_shift(args, root, rep):
    """Shift hue, saturation and lightness while keeping value contrast."""
    dh, ds, dl = args.hue / 360.0, args.saturation, args.lightness
    return run_frames(args, root, rep,
                      lambda im, i: do_hue_shift(im, dh, ds, dl, protect_neutral=not args.force),
                      describe="hue-shift")


def op_pixelate(args, root, rep):
    """Downsample then upsample to clean pixel clusters."""
    return run_frames(args, root, rep, lambda im, i: do_pixelate(im, args.block, method=args.method),
                      describe="pixelate")


def op_despill(args, root, rep):
    """Neutralise green/blue fringes from chroma or alpha mattes."""
    return run_frames(args, root, rep, lambda im, i: do_despill(im, args.strength), describe="despill")


def op_despeckle(args, root, rep):
    """Remove isolated stray pixels."""
    return run_frames(args, root, rep, lambda im, i: do_despeckle(im, args.min_neighbours),
                      describe="despeckle")


def op_alpha_bleed(args, root, rep):
    """Fill RGB under transparent pixels so engines stop edge bleed."""
    return run_frames(args, root, rep, lambda im, i: do_alpha_bleed(im, args.radius),
                      describe="alpha-bleed")


def op_clean_edges(args, root, rep):
    """Drop hidden RGB noise and harden semi-transparent noise."""
    return run_frames(args, root, rep, lambda im, i: do_clean_edges(im, args.hard_threshold),
                      describe="clean-edges")


def op_normalize_size(args, root, rep):
    """Put every frame on one canvas with a shared anchor."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    fs = core.load_sequence(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    size = core.parse_size(args.size) if args.size else (
        max(f.width for f in fs.frames), max(f.height for f in fs.frames))
    results = do_normalize(fs.frames, size, args.anchor)
    if not args.dry_run:
        for i, im in enumerate(results):
            core.save_image(im, out_dir / f"{src.name}_{i:04d}.png")
        core.save_json({"operation": "normalize-size", "size": list(size), "anchor": args.anchor,
                        "frames": len(results)}, out_dir / "edit-manifest.json")
        rep.add_output(out_dir, "directory", frames=len(results))
    rep.stat("frames", len(results))
    return {"summary": {"frames": len(results), "size": list(size), "out": str(out_dir)}}


def op_symmetry(args, root, rep):
    """Mirror one half then re-apply asymmetric details."""
    return run_frames(args, root, rep, lambda im, i: do_symmetry(im, args.keep, args.regain),
                      describe="symmetry")


def op_upscale(args, root, rep):
    """Nearest or pixel-art aware upscale with optional edge cleanup."""
    def fn(im, i):
        if args.factor:
            target = (max(1, round(im.width * args.factor)), max(1, round(im.height * args.factor)))
        else:
            target = core.parse_size(args.size)
        if args.method == "nearest":
            out = im.resize(target, Image.Resampling.NEAREST)
        elif args.method == "smooth":
            out = im.resize(target, Image.Resampling.LANCZOS)
        else:  # pixel = nearest then a light cleanup pass
            out = im.resize(target, Image.Resampling.NEAREST)
            out = do_threshold_alpha(out, 1)
        if args.outline:
            out = do_outline(out, colour=_colour(args.outline), width=args.outline_width, mode="outer")
        return out
    return run_frames(args, root, rep, fn, describe="upscale")


def op_downscale(args, root, rep):
    """Downscale while protecting silhouettes and edges."""
    def fn(im, i):
        if args.factor:
            target = (max(1, round(im.width * args.factor)), max(1, round(im.height * args.factor)))
        else:
            target = core.parse_size(args.size)
        out = im.resize(target, Image.Resampling.LANCZOS)
        if args.harden:
            out = do_threshold_alpha(out, args.threshold)
            out = do_alpha_bleed(out, 1)
        return out
    return run_frames(args, root, rep, fn, describe="downscale")


def op_recolor_series(args, root, rep):
    """Batch recolour a whole folder into team/rarity/biome sets."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    sets = core.parse_kv_list(args.set, name="--set")
    if not sets:
        raise SFError("MISSING_INPUT", "provide at least one --set name=#hex")
    files = core.list_images(src, recursive=bool(args.recursive))
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    written = 0
    for name, hexv in sets.items():
        base = _colour(hexv)
        for f in files:
            rep.deadline.check()
            im = core.load_image(f, max_pixels=args.max_pixels)
            out = do_hue_shift(im, args.hue / 360.0, args.saturation, args.lightness, protect_neutral=True)
            out = do_swap_color(out, _colour(args.source), base, args.tolerance)[0] if args.source else out
            if not args.dry_run:
                dest = out_dir / core.slugify(name) / f"{f.stem}.png"
                core.save_image(out, dest)
            written += 1
    if not args.dry_run:
        rep.add_output(out_dir, "directory", variants=len(sets), files=written)
    rep.stat("files_written", written)
    return {"summary": {"variants": len(sets), "files": written, "out": str(out_dir)}}


def op_composite(args, root, rep):
    """Composite layered parts back-to-front into one sprite."""
    layer_args = list(args.layer or [])
    src = None
    if args.input:
        src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if src is not None and src.exists():
        if src.is_dir():
            layers = [core.load_image(p, max_pixels=args.max_pixels)
                      for p in core.list_images(src, recursive=False)]
        else:
            layers = [core.load_image(src, max_pixels=args.max_pixels)]
    elif layer_args:
        layers = [core.load_image(core.find_input(Path(p), root), max_pixels=args.max_pixels)
                  for p in layer_args]
    elif src is not None:
        raise SFError("INPUT_NOT_FOUND", f"composite input not found: {src}")
    else:
        raise SFError("MISSING_INPUT", "pass --input FOLDER or --layer FILE (repeatable)",
                      hint="layers are composited back to front in the order given")
    if not layers:
        raise SFError("NO_LAYERS", "no layers found", hint="check --input / --layer")
    out = do_composite(layers)
    target = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                               root, args.allow_outside_root)
    if args.size:
        out = do_pad(out, core.parse_size(args.size), args.anchor)
    if not args.dry_run:
        core.save_image(out, target)
        rep.add_output(target, "composite", layers=len(layers))
    return {"summary": {"layers": len(layers), "size": list(out.size), "output": str(target)}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _io(p, out_help="output file or folder"):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True, help=out_help)


def _p_crop(p):
    _io(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--box", help="x0,y0,x1,y1 in pixels")
    g.add_argument("--relative", help="x,y,w,h in 0..1 of the image")


def _p_pad(p):
    _io(p)
    p.add_argument("--size", required=True, help="target WxH")
    p.add_argument("--anchor", default="center")


def _p_offset(p):
    _io(p)
    p.add_argument("--dx", type=int, required=True)
    p.add_argument("--dy", type=int, required=True)
    p.add_argument("--wrap", action="store_true")


def _p_resize(p):
    _io(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--size", help="target WxH")
    g.add_argument("--factor", type=float, help="scale factor")
    p.add_argument("--resample", default="nearest", choices=["nearest", "bilinear", "bicubic", "lanczos"])


def _p_rotate(p):
    _io(p)
    p.add_argument("--degrees", type=float, required=True)
    p.add_argument("--resample", default="nearest", choices=["nearest", "bilinear", "bicubic", "lanczos"])
    p.add_argument("--expand", action="store_true")


def _p_transform(p):
    _io(p)
    p.add_argument("--rotate", type=float, default=0)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--size", help="final canvas WxH")
    p.add_argument("--anchor", default="center")
    p.add_argument("--resample", default="nearest", choices=["nearest", "bilinear", "bicubic", "lanczos"])


def _p_outline(p):
    _io(p)
    p.add_argument("--color", default="#101018")
    p.add_argument("--width", type=int, default=1)
    p.add_argument("--mode", default="outer", choices=["outer", "inner"])


def _p_threshold(p):
    _io(p)
    p.add_argument("--threshold", type=int, default=128)


def _p_decon(p):
    _io(p)
    p.add_argument("--iterations", type=int, default=2)
    p.add_argument("--threshold", type=int, default=250)


def _p_rmbg(p):
    _io(p)
    p.add_argument("--color", default="#ffffff")
    p.add_argument("--tolerance", type=int, default=12)
    p.add_argument("--feather", type=int, default=0)


def _p_palmap(p):
    _io(p)
    p.add_argument("--palette", required=True, help="palette JSON (roles/colours/list)")
    p.add_argument("--dither", action="store_true")


def _p_quant(p):
    _io(p)
    p.add_argument("--colours", type=int, default=16)
    p.add_argument("--dither", action="store_true")


def _p_swap(p):
    _io(p)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--tolerance", type=int, default=8)


def _p_hue(p):
    _io(p)
    p.add_argument("--hue", type=float, default=0.0, help="degrees -180..180")
    p.add_argument("--saturation", type=float, default=0.0)
    p.add_argument("--lightness", type=float, default=0.0)
    p.add_argument("--force", action="store_true", help="also shift near-neutral greys")


def _p_pixelate(p):
    _io(p)
    p.add_argument("--block", type=int, default=2)
    p.add_argument("--method", default="nearest", choices=["nearest", "bilinear", "bicubic", "lanczos"])


def _p_despill(p):
    _io(p)
    p.add_argument("--strength", type=float, default=0.6)


def _p_despeckle(p):
    _io(p)
    p.add_argument("--min-neighbours", type=int, default=1)


def _p_bleed(p):
    _io(p)
    p.add_argument("--radius", type=int, default=2)


def _p_cleanedges(p):
    _io(p)
    p.add_argument("--hard-threshold", type=int, default=8)


def _p_norm(p):
    p.add_argument("--input", "-i", required=True, help="folder of frames")
    p.add_argument("--output", "-o", required=True, help="output folder")
    p.add_argument("--size", help="target canvas (default: largest frame)")
    p.add_argument("--anchor", default="bottom-center",
                   choices=["bottom-center", "center", "top-center", "bottom-left", "bottom-right"])


def _p_sym(p):
    _io(p)
    p.add_argument("--keep", default="left", choices=["left", "right", "none"])
    p.add_argument("--regain", type=float, default=0.0, help="0..1 amount of original asymmetry restored")


def _p_up(p):
    _io(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--factor", type=float)
    g.add_argument("--size")
    p.add_argument("--method", default="nearest", choices=["nearest", "smooth", "pixel"])
    p.add_argument("--outline", help="optional outline colour applied after upscale")
    p.add_argument("--outline-width", type=int, default=1)


def _p_down(p):
    _io(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--factor", type=float)
    g.add_argument("--size")
    p.add_argument("--harden", action="store_true")
    p.add_argument("--threshold", type=int, default=128)


def _p_recolor(p):
    p.add_argument("--input", "-i", required=True, help="folder of images")
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--set", action="append", default=[], metavar="NAME=#hex")
    p.add_argument("--source", help="optional colour to swap toward the set colour")
    p.add_argument("--hue", type=float, default=0.0)
    p.add_argument("--saturation", type=float, default=0.0)
    p.add_argument("--lightness", type=float, default=0.0)
    p.add_argument("--tolerance", type=int, default=24)
    p.add_argument("--recursive", action="store_true")


def _p_composite(p):
    p.add_argument("--input", "-i", help="folder of layers (or omit and use --layer)")
    p.add_argument("--layer", action="append", default=[], help="layer file, back to front (repeatable)")
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--size", help="optional final canvas")
    p.add_argument("--anchor", default="center")


OPS = {
    "crop": (_p_crop, op_crop),
    "pad": (_p_pad, op_pad),
    "offset": (_p_offset, op_offset),
    "resize": (_p_resize, op_resize),
    "rotate": (_p_rotate, op_rotate),
    "transform": (_p_transform, op_transform),
    "outline": (_p_outline, op_outline),
    "threshold-alpha": (_p_threshold, op_threshold_alpha),
    "decontaminate": (_p_decon, op_decontaminate),
    "remove-bg": (_p_rmbg, op_remove_bg),
    "palette-map": (_p_palmap, op_palette_map),
    "quantize": (_p_quant, op_quantize),
    "swap-color": (_p_swap, op_swap_color),
    "hue-shift": (_p_hue, op_hue_shift),
    "pixelate": (_p_pixelate, op_pixelate),
    "despill": (_p_despill, op_despill),
    "despeckle": (_p_despeckle, op_despeckle),
    "alpha-bleed": (_p_bleed, op_alpha_bleed),
    "clean-edges": (_p_cleanedges, op_clean_edges),
    "normalize-size": (_p_norm, op_normalize_size),
    "symmetry": (_p_sym, op_symmetry),
    "upscale": (_p_up, op_upscale),
    "downscale": (_p_down, op_downscale),
    "recolor-series": (_p_recolor, op_recolor_series),
    "composite": (_p_composite, op_composite),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "edit",
        "spriteforge edit: local edits, cleanup, repair, recolour and compositing",
        OPS,
    ))
