#!/usr/bin/env python3
"""spriteforge convert: format conversion, export, naming and handoff packaging.

Operations
----------
  image         convert still images between PNG/WebP/BMP/TGA/TIFF/JPEG/QOI
  animated      GIF / APNG / animated WebP with timing, loop count, scale
  vector        raster -> SVG (implemented subset) or SVG -> raster
  alpha-check   inspect alpha mode, edges, spill and transparency risks
  colormode     convert indexed/grey/RGB/RGBA and report bit depth
  rename        apply naming and folder standards, emit a rename map
  split-source  separate editable sources from final exports
  batch         batch export by scale/format/palette across a folder
  preview-video sprite preview via animated GIF/APNG (no ffmpeg available)

SVG support is a deliberate, documented subset: SVG *input* handles
``<rect>``, ``<circle>``, ``<ellipse>``, ``<polygon>``, ``<polyline>``,
``<line>`` and ``<path>`` with fill/stroke on a viewBox canvas. SVG *output*
emits one ``<rect>`` per opaque pixel run (exact, not hand-editable vector art).
Anything else fails loudly instead of silently degrading.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_convert", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_convert"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageDraw = core.ImageDraw


STILL_FORMATS = {".png", ".webp", ".bmp", ".tga", ".tiff", ".tif", ".jpg", ".jpeg", ".qoi", ".ppm"}
ANIMATED_FORMATS = {".gif", ".png", ".webp"}


def _out_path(args, root: Path, *, default_ext: str | None = None) -> Path:
    out = Path(args.output) if Path(args.output).is_absolute() else root / args.output
    if default_ext and out.suffix.lower() != default_ext:
        out = out.with_suffix(default_ext)
    return core.guard_output(out, root, args.allow_outside_root)


# --------------------------------------------------------------------------- #
# still / animated conversion
# --------------------------------------------------------------------------- #

def op_image(args, root: Path, rep: core.Reporter):
    """Convert still images between supported raster formats."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels)
    if args.scale != 1.0:
        im = im.resize((max(1, round(im.width * args.scale)), max(1, round(im.height * args.scale))),
                       core.Image.Resampling.NEAREST if args.nearest else core.Image.Resampling.LANCZOS)
    if args.background:
        bg = core.composite_on(im, tuple(int(args.background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)))
        im = bg.convert("RGB") if args.flatten else bg
    out = _out_path(args, root)
    if out.suffix.lower() not in STILL_FORMATS:
        raise SFError("BAD_EXTENSION", f"unsupported still format '{out.suffix}'",
                      hint=f"use one of {sorted(STILL_FORMATS)}")
    if not args.dry_run:
        core.save_image(im, out, quality=args.quality)
        rep.add_output(out, "image", size=list(im.size))
    rep.stat("bytes", out.stat().st_size if out.exists() else 0)
    return {"summary": {"output": str(out), "size": list(im.size),
                        "bytes": out.stat().st_size if out.exists() else 0,
                        "format": out.suffix.lstrip(".").upper()}}


def op_animated(args, root: Path, rep: core.Reporter):
    """Export GIF, APNG or animated WebP with timing, loop and scale control."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    frames, durs = fs.frames, list(fs.durations_ms)
    if args.scale != 1.0:
        frames = [f.resize((max(1, round(f.width * args.scale)), max(1, round(f.height * args.scale))),
                           core.Image.Resampling.NEAREST if args.nearest else core.Image.Resampling.LANCZOS)
                  for f in frames]
    if args.fps:
        durs = [int(round(1000 / args.fps))] * len(frames)
    if args.speed and args.speed != 1.0:
        durs = [max(10, int(round(d / args.speed))) for d in durs]
    out = _out_path(args, root)
    fmt = args.format or out.suffix.lstrip(".").lower()
    if fmt == "apng":
        out = out.with_suffix(".png")
        fmt = "png"
    if out.suffix.lower() not in ANIMATED_FORMATS:
        raise SFError("BAD_EXTENSION", f"animated output must be .gif/.png/.webp, got '{out.suffix}'")
    background = tuple(int(args.background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) if args.background else None
    if not args.dry_run:
        core.save_animated(frames, out, durs, loop=args.loop, quality=args.quality, background=background)
        rep.add_output(out, fmt, frames=len(frames))
    rep.stat("bytes", out.stat().st_size if out.exists() else 0)
    return {"summary": {"format": fmt, "frames": len(frames), "total_ms": sum(durs),
                        "bytes": out.stat().st_size if out.exists() else 0, "output": str(out)}}


# --------------------------------------------------------------------------- #
# vector
# --------------------------------------------------------------------------- #

SVG_SHAPES = {"rect", "circle", "ellipse", "polygon", "polyline", "line", "path"}


def _parse_svg(path: Path) -> tuple[tuple[int, int], list[dict]]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise SFError("BAD_SVG", f"cannot parse SVG: {exc}")
    root = tree.getroot()
    if not root.tag.endswith("svg"):
        raise SFError("BAD_SVG", "root element is not <svg>")
    vb = root.get("viewBox")
    if vb:
        parts = [float(v) for v in re.split(r"[,\s]+", vb.strip()) if v]
        if len(parts) != 4:
            raise SFError("BAD_SVG", f"viewBox must have 4 numbers, got '{vb}'")
        w, h = int(round(parts[2])), int(round(parts[3]))
    else:
        w = int(float(root.get("width", "0").replace("px", "") or 0))
        h = int(float(root.get("height", "0").replace("px", "") or 0))
    if w <= 0 or h <= 0:
        raise SFError("BAD_SVG", "SVG needs a positive width/height or viewBox")
    shapes = []
    unsupported = []
    ns = {"svg": "http://www.w3.org/2000/svg"}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in SVG_SHAPES:
            shapes.append(el)
        elif tag in {"g", "svg", "title", "desc", "defs", "style", "metadata"}:
            continue
        else:
            unsupported.append(tag)
    if unsupported:
        raise SFError("UNSUPPORTED_SVG",
                      f"SVG uses elements this converter does not implement: {sorted(set(unsupported))}",
                      hint="flatten the art to rect/circle/ellipse/polygon/polyline/line/path first")
    if not shapes:
        raise SFError("EMPTY_SVG", "SVG has no drawable shapes")
    parsed = []
    for el in shapes:
        tag = el.tag.split("}")[-1]
        parsed.append({"tag": tag, "attrs": dict(el.attrib)})
    return (w, h), parsed


def _svg_colour(value: str | None, default=(0, 0, 0, 255)):
    if not value or value == "none":
        return None
    v = value.strip()
    if v.startswith("url("):
        raise SFError("UNSUPPORTED_SVG", f"gradient/paint server '{v}' is not supported")
    named = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
             "blue": "#0000ff", "yellow": "#ffff00", "grey": "#808080", "gray": "#808080"}
    v = named.get(v.lower(), v)
    if v.startswith("rgb"):
        nums = [int(float(x)) for x in re.findall(r"[\d.]+", v)][:3]
        return tuple(nums) + (255,)
    v = v.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) == 6:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    if len(v) == 8:
        # #rrggbbaa -> RGBA
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4, 6))
    raise SFError("UNSUPPORTED_SVG", f"cannot parse colour '{value}'")


def _path_points(d: str, steps: int = 12) -> list[tuple[float, float]]:
    """Flatten M/L/H/V/C/Q/Z paths into a polyline (arcs unsupported)."""
    tokens = re.findall(r"[MmLlHhVvCcQqZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    pts: list[tuple[float, float]] = []
    i = 0
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    cmd = None

    def num() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        if re.fullmatch(r"[MmLlHhVvCcQqZz]", tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                pts.append(start)
                cur = start
                continue
        if cmd is None:
            raise SFError("UNSUPPORTED_SVG", "path data must start with a command")
        try:
            if cmd in "Mm":
                x, y = num(), num()
                cur = (x, y) if cmd == "M" else (cur[0] + x, cur[1] + y)
                start = cur
                pts.append(cur)
                cmd = "L" if cmd == "M" else "l"
            elif cmd in "Ll":
                x, y = num(), num()
                cur = (x, y) if cmd == "L" else (cur[0] + x, cur[1] + y)
                pts.append(cur)
            elif cmd in "Hh":
                x = num()
                cur = (x, cur[1]) if cmd == "H" else (cur[0] + x, cur[1])
                pts.append(cur)
            elif cmd in "Vv":
                y = num()
                cur = (cur[0], y) if cmd == "V" else (cur[0], cur[1] + y)
                pts.append(cur)
            elif cmd in "Cc":
                pts_ctrl = [num() for _ in range(6)]
                p0 = cur
                if cmd == "c":
                    p1 = (p0[0] + pts_ctrl[0], p0[1] + pts_ctrl[1])
                    p2 = (p0[0] + pts_ctrl[2], p0[1] + pts_ctrl[3])
                    p3 = (p0[0] + pts_ctrl[4], p0[1] + pts_ctrl[5])
                else:
                    p1, p2, p3 = (pts_ctrl[0], pts_ctrl[1]), (pts_ctrl[2], pts_ctrl[3]), (pts_ctrl[4], pts_ctrl[5])
                for s in range(1, steps + 1):
                    t = s / steps
                    x = ((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0]
                         + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0])
                    y = ((1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1]
                         + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
                    pts.append((x, y))
                cur = p3
            elif cmd in "Qq":
                q = [num() for _ in range(4)]
                p0 = cur
                p1 = (q[0], q[1]) if cmd == "Q" else (p0[0] + q[0], p0[1] + q[1])
                p2 = (q[2], q[3]) if cmd == "Q" else (p0[0] + q[2], p0[1] + q[3])
                for s in range(1, steps + 1):
                    t = s / steps
                    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
                    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
                    pts.append((x, y))
                cur = p2
            else:
                raise SFError("UNSUPPORTED_SVG", f"path command '{cmd}' is not supported")
        except IndexError:
            raise SFError("BAD_SVG", f"path command '{cmd}' is missing arguments")
    return pts


def svg_to_image(path: Path, scale: float = 1.0) -> Image.Image:
    (w, h), shapes = _parse_svg(path)
    size = (max(1, int(w * scale)), max(1, int(h * scale)))
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for sh in shapes:
        a = {k: v for k, v in sh["attrs"].items() if k in
             {"x", "y", "cx", "cy", "r", "rx", "ry", "width", "height", "points", "d", "fill", "stroke",
              "stroke-width", "opacity", "fill-opacity"}}
        fill = _svg_colour(a.get("fill", "#000000"))
        stroke = _svg_colour(a.get("stroke"))
        width = max(1, int(round(float(a.get("stroke-width", 1)) * scale)))
        op = float(a.get("opacity", 1)) * float(a.get("fill-opacity", 1))
        tag = sh["tag"]

        def sc(v: float) -> float:
            return v * scale

        if tag == "rect":
            box = [sc(float(a.get("x", 0))), sc(float(a.get("y", 0))),
                   sc(float(a.get("x", 0))) + sc(float(a.get("width", 0))) - 1,
                   sc(float(a.get("y", 0))) + sc(float(a.get("height", 0))) - 1]
            f = tuple(int(c * op) for c in fill[:3]) + (int(255 * op),) if fill else None
            d.rectangle(box, fill=f, outline=stroke, width=width if stroke else 0)
        elif tag == "circle":
            cx, cy, r = sc(float(a.get("cx", 0))), sc(float(a.get("cy", 0))), sc(float(a.get("r", 0)))
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=stroke, width=width if stroke else 0)
        elif tag == "ellipse":
            cx, cy = sc(float(a.get("cx", 0))), sc(float(a.get("cy", 0)))
            rx, ry = sc(float(a.get("rx", 0))), sc(float(a.get("ry", 0)))
            d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill, outline=stroke, width=width if stroke else 0)
        elif tag in {"polygon", "polyline"}:
            nums = [float(v) for v in re.split(r"[,\s]+", a.get("points", "").strip()) if v]
            pts = [(sc(nums[i]), sc(nums[i + 1])) for i in range(0, len(nums) - 1, 2)]
            if len(pts) < 2:
                raise SFError("BAD_SVG", f"{tag} needs at least two points")
            if tag == "polygon":
                d.polygon(pts, fill=fill, outline=stroke)
            else:
                d.line(pts, fill=stroke or fill or (0, 0, 0, 255), width=width)
        elif tag == "line":
            d.line([sc(float(a.get("x1", 0))), sc(float(a.get("y1", 0))),
                    sc(float(a.get("x2", 0))), sc(float(a.get("y2", 0)))],
                   fill=stroke or fill or (0, 0, 0, 255), width=width)
        elif tag == "path":
            pts = _path_points(a.get("d", ""))
            if len(pts) < 2:
                continue
            pts = [(sc(x), sc(y)) for x, y in pts]
            closed = a.get("d", "").strip().upper().endswith("Z")
            if closed and fill:
                d.polygon(pts, fill=fill)
            if stroke:
                d.line(pts, fill=stroke, width=width)
    return im


def image_to_svg(im: Image.Image, *, tolerance: int = 0) -> tuple[str, int]:
    """Exact run-length SVG: one <rect> per horizontal run of identical colour."""
    rgba = im.convert("RGBA")
    px = rgba.load()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{rgba.width}" height="{rgba.height}" '
             f'viewBox="0 0 {rgba.width} {rgba.height}" shape-rendering="crispEdges">']
    rects = 0
    for y in range(rgba.height):
        x = 0
        while x < rgba.width:
            r, g, b, a = px[x, y]
            if a == 0:
                x += 1
                continue
            run = 1
            while x + run < rgba.width and px[x + run, y] == (r, g, b, a):
                run += 1
            opacity = "" if a == 255 else f' fill-opacity="{a/255:.3f}"'
            parts.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="#{r:02x}{g:02x}{b:02x}"{opacity}/>')
            rects += 1
            x += run
    parts.append("</svg>")
    return "\n".join(parts) + "\n", rects


def op_vector(args, root: Path, rep: core.Reporter):
    """Convert between a supported SVG subset and raster images."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.exists():
        raise SFError("INPUT_NOT_FOUND", f"input not found: {src}")
    if src.suffix.lower() == ".svg":
        if args.direction == "to-svg":
            raise SFError("BAD_ARGUMENT", "input is already SVG; use --direction to-png")
        im = svg_to_image(src, args.scale)
        out = _out_path(args, root, default_ext=".png")
        if not args.dry_run:
            core.save_image(im, out)
            rep.add_output(out, "raster", size=list(im.size))
        return {"summary": {"direction": "svg->raster", "size": list(im.size), "output": str(out)}}
    if args.direction == "to-png":
        raise SFError("BAD_ARGUMENT", "input is a raster image; use --direction to-svg")
    im = core.load_image(src, max_pixels=args.max_pixels)
    if args.scale != 1.0:
        im = im.resize((max(1, round(im.width * args.scale)), max(1, round(im.height * args.scale))),
                       core.Image.Resampling.NEAREST)
    if args.quantize:
        im = im.quantize(colors=args.quantize, method=Image.Quantize.MEDIANCUT).convert("RGBA")
    svg, rects = image_to_svg(im)
    out = _out_path(args, root, default_ext=".svg")
    if not args.dry_run:
        out.write_text(svg, encoding="utf-8")
        rep.add_output(out, "svg", rects=rects)
    rep.stat("rects", rects)
    return {"summary": {"direction": "raster->svg", "rects": rects,
                        "bytes": out.stat().st_size if out.exists() else 0, "output": str(out),
                        "note": "run-length rectangles are exact but not hand-editable vector art"}}


# --------------------------------------------------------------------------- #
# alpha / colour mode
# --------------------------------------------------------------------------- #

def op_alpha_check(args, root: Path, rep: core.Reporter):
    """Inspect alpha mode, edge bleed, spill and transparency risks."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    files = core.list_images(src) if src.is_dir() else [src]
    findings = []
    for p in files[: args.max_files]:
        im = core.load_image(p, max_pixels=args.max_pixels)
        alpha = im.split()[-1]
        hist = alpha.histogram()
        total = sum(hist)
        transparent = hist[0] / total
        opaque = hist[255] / total
        semi = 1 - transparent - opaque
        # premultiplied hint: fully transparent pixels carrying colour
        px = im.load()
        coloured_transparent = 0
        for y in range(0, im.height, max(1, im.height // 64)):
            for x in range(0, im.width, max(1, im.width // 64)):
                r, g, b, a = px[x, y]
                if a == 0 and (r or g or b):
                    coloured_transparent += 1
        findings.append({
            "file": p.name, "mode": im.mode, "size": list(im.size),
            "transparent_share": round(transparent, 4), "opaque_share": round(opaque, 4),
            "semi_transparent_share": round(semi, 4),
            "coloured_transparent_pixels": coloured_transparent,
            "edge_bleed_risk": "low" if coloured_transparent > 0 else "possible",
            "needs_alpha_bleed": coloured_transparent <= 0 and transparent > 0,
            "has_transparency": transparent > 0,
        })
    payload = {"files": findings, "count": len(findings),
               "recommendations": [
                   "run edit.py alpha-bleed when needs_alpha_bleed is true to stop dark fringes",
                   "keep straight (non-premultiplied) alpha for PNG/WebP engine import",
                   "cutout sprites (threshold-alpha) compress better and avoid sorting artifacts",
               ]}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "alpha-report")
    risky = [f["file"] for f in findings if f["needs_alpha_bleed"]]
    if risky:
        rep.warn("ALPHA_BLEED_RISK", f"{len(risky)} files have transparent pixels with no RGB fill", files=risky[:5])
    return {"summary": {"files": len(findings), "bleed_risk": len(risky), "output": str(out)}}


def op_colormode(args, root: Path, rep: core.Reporter):
    """Convert colour modes and report bit depth without hidden damage."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels, alpha=args.mode in {"RGBA", "LA", "P"})
    target = args.mode
    if target == "P":
        colours = args.colours or 64
        alpha = im.split()[-1]
        quant = im.convert("RGB").quantize(colors=min(256, colours), method=Image.Quantize.MEDIANCUT)
        out_im = quant
    elif target == "L":
        out_im = im.convert("L")
    elif target == "LA":
        out_im = im.convert("LA")
    elif target == "RGB":
        out_im = im.convert("RGB")
    elif target == "RGBA":
        out_im = im.convert("RGBA")
    else:
        raise SFError("BAD_MODE", f"unsupported mode '{target}'", details={"allowed": ["P", "L", "LA", "RGB", "RGBA"]})
    out = _out_path(args, root)
    depth = 8
    if not args.dry_run:
        core.save_image(out_im if out_im.mode != "P" else out_im, out)
        rep.add_output(out, "image", mode=out_im.mode)
    if out_im.mode == "P":
        pal = out_im.getpalette() or []
        used = set(out_im.tobytes())
        colours = len(used)
    else:
        colours = len(core.color_counts(out_im))
    report = {"input_mode": im.mode, "output_mode": out_im.mode, "bit_depth": depth,
              "unique_colours": colours, "output": str(out),
              "notes": "indexed output uses a median-cut palette; keep the master RGBA file untouched"}
    outj = out.with_suffix(".colormode.json")
    if not args.dry_run:
        core.save_json(report, outj)
        rep.add_output(outj, "colormode-report")
    return {"summary": report}


# --------------------------------------------------------------------------- #
# naming / split / batch
# --------------------------------------------------------------------------- #

def rename_target(stem: str, args) -> str:
    m = re.match(r"^(?P<unit>[a-z0-9]+)[_-](?P<action>[a-z]+)(?:[_-](?P<direction>[a-z]{1,2}))?"
                 r"(?:[_-]?(?P<index>\d+))?$", stem, re.I)
    if not m:
        if args.strict:
            raise SFError("BAD_NAME", f"'{stem}' does not match unit_action_direction_index")
        return core.slugify(stem)
    parts = {
        "unit": (m.group("unit") or "").lower(),
        "action": (m.group("action") or "").lower(),
        "direction": (m.group("direction") or "").lower(),
        "index": f"{int(m.group('index')):04d}" if m.group("index") else "0000",
    }
    name = args.pattern
    for key, value in parts.items():
        name = name.replace("{" + key + "}", value)
    return core.slugify(name)


def op_rename(args, root: Path, rep: core.Reporter):
    """Apply naming and folder standards and emit a reversible rename map."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    files = core.list_images(src, recursive=True) if src.is_dir() else [src]
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True) if args.output else None
    plan = []
    for p in files:
        new_stem = rename_target(p.stem, args)
        new_name = f"{args.prefix}{new_stem}{p.suffix.lower()}"
        plan.append({"from": p.relative_to(src).as_posix() if p.is_relative_to(src) else p.name,
                     "from_name": p.name, "to": new_name, "changed": new_name != p.name})
    duplicates = {}
    for item in plan:
        duplicates[item["to"]] = duplicates.get(item["to"], 0) + 1
    clashes = {k: v for k, v in duplicates.items() if v > 1}
    if clashes:
        raise SFError("NAME_CLASH", f"naming pattern produces duplicate names: {list(clashes)[:5]}",
                      hint="include {index} or {direction} in --pattern")
    if out_dir and not args.dry_run:
        for item in plan:
            source = src / item["from"] if src.is_dir() else src
            im = core.load_image(source, max_pixels=args.max_pixels)
            core.save_image(im, out_dir / item["to"])
        rep.add_output(out_dir, "directory", files=len(plan))
    map_path = core.guard_output(root / args.map_output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json({"pattern": args.pattern, "prefix": args.prefix, "renames": plan,
                        "changed": sum(1 for i in plan if i["changed"])}, map_path)
        rep.add_output(map_path, "rename-map")
    return {"summary": {"files": len(plan), "changed": sum(1 for i in plan if i["changed"]),
                        "map": str(map_path)}}


def op_split_source(args, root: Path, rep: core.Reporter):
    """Separate editable sources from final exports and preview files."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"--input must be a folder: {src}")
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    source_ext = {".psd", ".ase", ".aseprite", ".kra", ".blend", ".xcf", ".clip", ".svg", ".json", ".py"}
    preview_ext = {".gif", ".webp", ".mp4", ".mov", ".html"}
    moved = {"source": [], "export": [], "preview": []}
    for p in core.list_images(src, recursive=True) + [q for q in src.rglob("*") if q.suffix.lower() in source_ext]:
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        kind = ("source" if p.suffix.lower() in source_ext else
                "preview" if p.suffix.lower() in preview_ext or "preview" in p.name.lower() else "export")
        dest = out_dir / kind / rel
        moved[kind].append(str(dest.relative_to(out_dir)))
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(p, dest)
    if not args.dry_run:
        core.save_json({"root": str(src), "split": {k: len(v) for k, v in moved.items()},
                        "files": moved}, out_dir / "split-manifest.json")
        rep.add_output(out_dir, "directory")
    rep.stat("source", len(moved["source"]))
    return {"summary": {k: len(v) for k, v in moved.items()},
            "warning": None if moved["source"] else "no editable sources found"}


def op_batch(args, root: Path, rep: core.Reporter):
    """Batch export by scale, format and palette across a folder."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"--input must be a folder: {src}")
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    formats = [f.strip().lower().lstrip(".") for f in args.formats.split(",") if f.strip()]
    if not formats:
        raise SFError("BAD_ARGUMENT", "--formats needs at least one extension")
    if any(f not in {"png", "webp", "bmp", "tga", "tiff", "jpg", "jpeg", "qoi"} for f in formats):
        raise SFError("BAD_FORMAT", f"unsupported batch format in {formats}")
    files = core.list_images(src, recursive=True)
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    scales = []
    for item in args.scales:
        for piece in str(item).replace(",", " ").split():
            try:
                scales.append(float(piece))
            except ValueError:
                raise SFError("BAD_ARGUMENT", f"--scales must be numbers, got '{piece}'")
    if not scales or any(s <= 0 for s in scales):
        raise SFError("BAD_ARGUMENT", "--scales values must be > 0")
    palette = None
    if args.palette:
        data = core.load_json(Path(args.palette) if Path(args.palette).is_absolute() else root / args.palette)
        block = data.get("roles", data) if isinstance(data, dict) else data
        values = block.values() if isinstance(block, dict) else block
        palette = [tuple(int(str(v).lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)) for v in values]
    written = 0
    for p in files:
        rep.deadline.check()
        im = core.load_image(p, max_pixels=args.max_pixels)
        for scale in scales:
            scaled = im if scale == 1.0 else im.resize(
                (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                core.Image.Resampling.NEAREST)
            if palette:
                pal_img = Image.new("P", (1, 1))
                flat = [c for colour in palette for c in colour]
                pal_img.putpalette(flat + [0] * (768 - len(flat)))
                quant = scaled.convert("RGB").quantize(palette=pal_img)
                scaled = quant.convert("RGBA")
            for fmt in formats:
                dest = out_dir / f"x{scale:g}" / f"{core.slugify(p.stem)}.{fmt}"
                if not args.dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    core.save_image(scaled, dest, quality=args.quality)
                written += 1
    if not args.dry_run:
        core.save_json({"source": str(src), "scales": scales, "formats": formats,
                        "files": written, "palette_applied": bool(palette)}, out_dir / "batch-manifest.json")
        rep.add_output(out_dir, "directory", files=written)
    rep.stat("written", written)
    return {"summary": {"source_files": len(files), "written": written,
                        "scales": scales, "formats": formats, "out": str(out_dir)}}


def op_preview_video(args, root: Path, rep: core.Reporter):
    """Sprite preview as an animated GIF/APNG (video needs ffmpeg, which is not bundled)."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    out = _out_path(args, root)
    fmt = out.suffix.lower()
    if fmt not in {".gif", ".png", ".webp"}:
        raise SFError("BAD_EXTENSION", "preview output must be .gif, .png (APNG) or .webp",
                      hint="mp4 needs ffmpeg, which this Skill Tool does not bundle; export GIF instead")
    if args.scale != 1.0:
        frames = [f.resize((max(1, round(f.width * args.scale)), max(1, round(f.height * args.scale))),
                           core.Image.Resampling.NEAREST) for f in fs.frames]
    else:
        frames = fs.frames
    if args.labels:
        labelled = []
        for i, f in enumerate(frames):
            canvas = core.pad_to(f, (f.width, f.height + 13), (0, 13))
            d = ImageDraw.Draw(canvas)
            d.rectangle([0, 0, canvas.width, 12], fill=(20, 20, 24, 255))
            d.text((2, 1), f"f{i} {fs.durations_ms[i]}ms", fill=(225, 225, 232, 255),
                   font=core.ImageFont.load_default())
            labelled.append(canvas)
        frames = labelled
    if not args.dry_run:
        core.save_animated(frames, out, fs.durations_ms, loop=0)
        rep.add_output(out, "preview")
    return {"summary": {"frames": len(frames), "total_ms": fs.total_ms, "output": str(out),
                        "limitation": "MP4/WebM export requires ffmpeg, which is not part of this Skill Tool"}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _io(p, out_help="output file"):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True, help=out_help)


def _p_image(p):
    _io(p)
    p.add_argument("--quality", type=int, default=92)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--nearest", action="store_true")
    p.add_argument("--background", help="hex background colour to composite onto")
    p.add_argument("--flatten", action="store_true", help="drop alpha after compositing")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_animated(p):
    _io(p)
    p.add_argument("--format", choices=["gif", "apng", "webp"], help="override from extension")
    p.add_argument("--fps", type=int, help="override frame rate")
    p.add_argument("--speed", type=float, help="speed multiplier (>1 faster)")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--nearest", action="store_true", default=True)
    p.add_argument("--loop", type=int, default=0)
    p.add_argument("--quality", type=int, default=90)
    p.add_argument("--background", help="hex matte for GIF output")
    p.add_argument("--max-frames", type=int, default=4096)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_vector(p):
    _io(p)
    p.add_argument("--direction", choices=["to-svg", "to-png"], default=None)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--quantize", type=int, help="reduce colours before raster->svg")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_alpha(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="alpha-report.json")
    p.add_argument("--max-files", type=int, default=500)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_colormode(p):
    _io(p)
    p.add_argument("--mode", required=True, choices=["P", "L", "LA", "RGB", "RGBA"])
    p.add_argument("--colours", type=int, default=64)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_rename(p):
    p.add_argument("--input", "-i", required=True, help="file or folder")
    p.add_argument("--output", "-o", help="optional folder to write renamed copies")
    p.add_argument("--pattern", default="{unit}_{action}_{direction}_{index}")
    p.add_argument("--prefix", default="")
    p.add_argument("--map-output", default="rename-map.json")
    p.add_argument("--strict", action="store_true", help="fail on names that do not match the standard")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_split(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True)


def _p_batch(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--formats", default="png,webp")
    p.add_argument("--scales", nargs="+", default=["1", "0.5"],
                   help="scale factors, space or comma separated")
    p.add_argument("--palette", help="optional palette JSON to quantise to")
    p.add_argument("--quality", type=int, default=92)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_preview(p):
    _io(p)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--labels", action="store_true", default=True)
    p.add_argument("--max-frames", type=int, default=4096)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


OPS = {
    "image": (_p_image, op_image),
    "animated": (_p_animated, op_animated),
    "vector": (_p_vector, op_vector),
    "alpha-check": (_p_alpha, op_alpha_check),
    "colormode": (_p_colormode, op_colormode),
    "rename": (_p_rename, op_rename),
    "split-source": (_p_split, op_split_source),
    "batch": (_p_batch, op_batch),
    "preview-video": (_p_preview, op_preview_video),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "convert",
        "spriteforge convert: format conversion, export, naming and handoff packaging",
        OPS,
    ))
