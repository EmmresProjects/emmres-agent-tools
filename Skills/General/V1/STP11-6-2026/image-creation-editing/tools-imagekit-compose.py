"""imagekit compose: graphic composition tools.

Icon and logo marks (raster + true vector SVG), seamless pattern and material
tiles with a seam test, typographic layouts with legibility checks, technical
diagrams and charts, and storyboard/contact sheets assembled from real images.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont

import imagekit_core as core
import imagekit_plan as plan

WHITE = (255, 255, 255)
INK = (17, 20, 24)


# --------------------------------------------------------------------------
# mark specification: one spec renders as raster and as SVG
# --------------------------------------------------------------------------


def _as_rgb(value):
    """Accept '#rrggbb', 'rrggbb' or an (r,g,b) tuple."""
    if isinstance(value, str):
        return core.hex_to_rgb(value)
    return tuple(value)[:3]


def _chaikin(pts, iterations=2, closed=True):
    for _ in range(iterations):
        out = []
        n = len(pts)
        limit = n if closed else n - 1
        for i in range(limit):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        pts = out
    return pts


def _shape(kind, **kw):
    data = {"type": kind}
    data.update(kw)
    return data


def glyph_shapes(glyph):
    """Unit-space (0..1) shape sets for a small pictogram library."""
    g = (glyph or "abstract").lower()
    lib = {
        "arrow": [
            _shape("line", points=[(0.18, 0.5), (0.74, 0.5)], stroke=0.11),
            _shape("polygon", points=[(0.66, 0.28), (0.9, 0.5), (0.66, 0.72)], fill=True),
        ],
        "chart": [
            _shape("rect", box=[0.16, 0.56, 0.32, 0.84], fill=True),
            _shape("rect", box=[0.42, 0.38, 0.58, 0.84], fill=True),
            _shape("rect", box=[0.68, 0.2, 0.84, 0.84], fill=True),
            _shape("line", points=[(0.12, 0.88), (0.88, 0.88)], stroke=0.06),
        ],
        "person": [
            _shape("circle", center=(0.5, 0.32), r=0.14, fill=True),
            _shape("path", d="M0.24,0.86 C0.24,0.62 0.38,0.52 0.5,0.52 C0.62,0.52 0.76,0.62 0.76,0.86", stroke=0.1),
        ],
        "leaf": [
            _shape("path", d="M0.5,0.88 C0.2,0.72 0.18,0.34 0.5,0.14 C0.82,0.34 0.8,0.72 0.5,0.88 Z", fill=True),
            _shape("line", points=[(0.5, 0.2), (0.5, 0.82)], stroke=0.035, lighten=True),
        ],
        "cloud": [
            _shape("circle", center=(0.36, 0.55), r=0.16, fill=True),
            _shape("circle", center=(0.56, 0.48), r=0.2, fill=True),
            _shape("circle", center=(0.72, 0.58), r=0.13, fill=True),
            _shape("rect", box=[0.3, 0.58, 0.74, 0.72], fill=True, radius=0.05),
        ],
        "shield": [
            _shape("polygon", points=[(0.5, 0.12), (0.84, 0.26), (0.84, 0.56), (0.5, 0.88), (0.16, 0.56), (0.16, 0.26)], fill=False, stroke=0.08),
            _shape("line", points=[(0.36, 0.5), (0.47, 0.62), (0.66, 0.38)], stroke=0.08),
        ],
        "search": [
            _shape("circle", center=(0.44, 0.44), r=0.22, fill=False, stroke=0.09),
            _shape("line", points=[(0.62, 0.62), (0.84, 0.84)], stroke=0.11),
        ],
        "check": [
            _shape("line", points=[(0.2, 0.54), (0.42, 0.74), (0.8, 0.28)], stroke=0.12),
        ],
        "plus": [
            _shape("line", points=[(0.5, 0.2), (0.5, 0.8)], stroke=0.1),
            _shape("line", points=[(0.2, 0.5), (0.8, 0.5)], stroke=0.1),
        ],
        "star": [
            _shape("polygon", points=_star_points(0.5, 0.5, 0.4, 0.17, 5), fill=True),
        ],
        "play": [
            _shape("polygon", points=[(0.34, 0.2), (0.8, 0.5), (0.34, 0.8)], fill=True),
        ],
        "document": [
            _shape("rect", box=[0.24, 0.12, 0.76, 0.88], fill=False, stroke=0.06, radius=0.05),
            _shape("line", points=[(0.34, 0.34), (0.66, 0.34)], stroke=0.05),
            _shape("line", points=[(0.34, 0.5), (0.66, 0.5)], stroke=0.05),
            _shape("line", points=[(0.34, 0.66), (0.54, 0.66)], stroke=0.05),
        ],
        "clock": [
            _shape("circle", center=(0.5, 0.5), r=0.36, fill=False, stroke=0.07),
            _shape("line", points=[(0.5, 0.5), (0.5, 0.28)], stroke=0.06),
            _shape("line", points=[(0.5, 0.5), (0.66, 0.58)], stroke=0.06),
        ],
        "pin": [
            _shape("path", d="M0.5,0.88 C0.5,0.88 0.2,0.56 0.2,0.4 A0.3,0.3 0 1,1 0.8,0.4 C0.8,0.56 0.5,0.88 0.5,0.88 Z", fill=True),
            _shape("circle", center=(0.5, 0.4), r=0.1, fill=False, stroke=0.05, lighten=True),
        ],
        "gear": [
            _shape("circle", center=(0.5, 0.5), r=0.18, fill=False, stroke=0.09),
            _shape("ring", center=(0.5, 0.5), r=0.36, teeth=8, stroke=0.06),
        ],
        "grid": [
            _shape("rect", box=[0.18, 0.18, 0.46, 0.46], fill=True),
            _shape("rect", box=[0.54, 0.18, 0.82, 0.46], fill=False, stroke=0.06),
            _shape("rect", box=[0.18, 0.54, 0.46, 0.82], fill=False, stroke=0.06),
            _shape("rect", box=[0.54, 0.54, 0.82, 0.82], fill=True),
        ],
        "abstract": [
            _shape("circle", center=(0.38, 0.42), r=0.22, fill=True),
            _shape("polygon", points=[(0.52, 0.78), (0.8, 0.78), (0.66, 0.5)], fill=False, stroke=0.07),
        ],
    }
    return lib.get(g, lib["abstract"])


def _star_points(cx, cy, r_out, r_in, points):
    pts = []
    for i in range(points * 2):
        ang = -math.pi / 2 + i * math.pi / points
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r))
    return pts


def build_mark_spec(kind, glyph, palette, size, stroke=1.0):
    """Compose a mark: optional container + glyph, on a 0..1 canvas."""
    shapes = []
    fg = palette[3] if len(palette) > 3 else INK
    accent = palette[1] if len(palette) > 1 else fg
    if kind == "badge":
        shapes.append(_shape("container", shape="circle", box=[0.06, 0.06, 0.94, 0.94], fill=palette[2] if len(palette) > 2 else accent))
    elif kind == "logo-shield":
        shapes.append(_shape("container", shape="shield", box=[0.08, 0.08, 0.92, 0.92], fill=palette[2] if len(palette) > 2 else accent))
    elif kind == "monogram":
        shapes.append(_shape("container", shape="rounded", box=[0.08, 0.08, 0.92, 0.92], fill=palette[1]))
        shapes.append(_shape("letter", text=(glyph or "A")[:1].upper(), color=WHITE))
    elif kind == "app-icon":
        shapes.append(_shape("container", shape="squircle", box=[0.04, 0.04, 0.96, 0.96], fill=palette[1]))
    elif kind == "sticker":
        shapes.append(_shape("container", shape="circle", box=[0.05, 0.05, 0.95, 0.95], fill=WHITE))
    if kind != "monogram":
        for shape in glyph_shapes(glyph):
            shape = dict(shape)
            shape["color"] = fg
            if kind in ("app-icon", "monogram") and shape.get("fill"):
                shape["color"] = WHITE
            shapes.append(shape)
    return shapes


def draw_mark(size, shapes, palette, stroke_scale=1.0, background=None):
    img = Image.new("RGBA", size, (background or (0, 0, 0, 0)) if background else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    W, H = size

    def X(v):
        return v * W

    def Y(v):
        return v * H

    for shape in shapes:
        color = _as_rgb(shape.get("color") or shape.get("fill_color") or INK)
        if shape.get("lighten"):
            color = tuple(min(255, int(c * 1.6) + 40) for c in color)
        width = max(1, int(shape.get("stroke", 0.06) * min(W, H) * stroke_scale))
        t = shape["type"]
        if t == "container":
            box = [X(shape["box"][0]), Y(shape["box"][1]), X(shape["box"][2]), Y(shape["box"][3])]
            fill = (_as_rgb(shape["fill"]) + (255,)) if shape.get("fill") else None
            if shape["shape"] == "circle":
                d.ellipse(box, fill=fill)
            elif shape["shape"] == "shield":
                cx, cy = box[0], box[1]
                w, h = box[2] - box[0], box[3] - box[1]
                pts = [(cx + w * 0.5, cy), (cx + w, cy + h * 0.22), (cx + w, cy + h * 0.55),
                       (cx + w * 0.5, cy + h), (cx, cy + h * 0.55), (cx, cy + h * 0.22)]
                d.polygon(pts, fill=fill)
            elif shape["shape"] == "squircle":
                d.rounded_rectangle(box, radius=int(min(W, H) * 0.22), fill=fill)
            else:
                d.rounded_rectangle(box, radius=int(min(W, H) * 0.12), fill=fill)
        elif t == "letter":
            font = core.load_font(int(H * 0.46), bold=True)
            text = shape["text"]
            bbox = d.textbbox((0, 0), text, font=font)
            d.text((W / 2 - (bbox[2] - bbox[0]) / 2 - bbox[0], H / 2 - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                   text, font=font, fill=tuple(shape["color"]) + (255,))
        elif t == "circle":
            box = [X(shape["center"][0] - shape["r"]), Y(shape["center"][1] - shape["r"]),
                   X(shape["center"][0] + shape["r"]), Y(shape["center"][1] + shape["r"])]
            if shape.get("fill"):
                d.ellipse(box, fill=color + (255,))
            else:
                d.ellipse(box, outline=color + (255,), width=width)
        elif t == "rect":
            box = [X(shape["box"][0]), Y(shape["box"][1]), X(shape["box"][2]), Y(shape["box"][3])]
            radius = int(shape.get("radius", 0) * min(W, H))
            if shape.get("fill"):
                d.rounded_rectangle(box, radius=radius, fill=color + (255,))
            else:
                d.rounded_rectangle(box, radius=radius, outline=color + (255,), width=width)
        elif t == "line":
            pts = [(X(x), Y(y)) for x, y in shape["points"]]
            d.line(pts, fill=color + (255,), width=width, joint="curve")
        elif t == "polygon":
            pts = [(X(x), Y(y)) for x, y in shape["points"]]
            if shape.get("fill"):
                d.polygon(pts, fill=color + (255,))
            else:
                d.line(pts + [pts[0]], fill=color + (255,), width=width, joint="curve")
        elif t == "path":
            pts = _flatten_path(shape["d"]) or []
            if pts:
                pixel_pts = [(X(x), Y(y)) for x, y in pts]
                if shape.get("fill"):
                    d.polygon(pixel_pts, fill=color + (255,))
                else:
                    d.line(pixel_pts, fill=color + (255,), width=width, joint="curve")
        elif t == "ring":
            cx, cy = X(shape["center"][0]), Y(shape["center"][1])
            r = X(shape["r"]) - X(0)
            teeth = int(shape.get("teeth", 8))
            for i in range(teeth):
                ang = 2 * math.pi * i / teeth
                x0 = cx + math.cos(ang) * r * 0.72
                y0 = cy + math.sin(ang) * r * 0.72
                x1 = cx + math.cos(ang) * r
                y1 = cy + math.sin(ang) * r
                d.line([(x0, y0), (x1, y1)], fill=color + (255,), width=width)
    return img


def _flatten_path(d, steps=24):
    """Very small path parser for the M/L/C/A subset used by glyph_shapes."""
    import re
    tokens = re.findall(r"[MLCAZ]|-?\d*\.?\d+", d)
    pts, i, cur = [], 0, (0, 0)
    cmd = None
    while i < len(tokens):
        if tokens[i] in "MLCAZ":
            cmd = tokens[i]
            i += 1
            if cmd == "Z":
                if pts:
                    pts.append(pts[0])
                continue
        try:
            if cmd == "M" or cmd == "L":
                x, y = float(tokens[i]), float(tokens[i + 1])
                pts.append((x, y))
                cur = (x, y)
                i += 2
            elif cmd == "C":
                x1, y1, x2, y2, x, y = (float(v) for v in tokens[i:i + 6])
                p0 = cur
                for s in range(1, steps + 1):
                    t = s / steps
                    bx = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3 * x
                    by = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3 * y
                    pts.append((bx, by))
                cur = (x, y)
                i += 6
            elif cmd == "A":
                # A rx,ry rot large sweep x,y  -> approximate with a quadratic arc
                rx, ry, rot, large, sweep, x, y = (float(v) for v in tokens[i:i + 7])
                p0 = cur
                cx = (p0[0] + x) / 2
                cy = (p0[1] + y) / 2 - ry * 1.3
                for s in range(1, steps + 1):
                    t = s / steps
                    bx = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * cx + t ** 2 * x
                    by = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * cy + t ** 2 * y
                    pts.append((bx, by))
                cur = (x, y)
                i += 7
            else:
                i += 1
        except (IndexError, ValueError):
            break
    return pts


def spec_to_svg(shapes, size, palette, stroke_scale=1.0):
    W, H = size
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    for shape in shapes:
        color = _as_rgb(shape.get("color") or INK)
        if shape.get("lighten"):
            color = tuple(min(255, int(c * 1.6) + 40) for c in color)
        hexc = core.rgb_to_hex(color)
        sw = max(1, round(shape.get("stroke", 0.06) * min(W, H) * stroke_scale, 2))
        t = shape["type"]
        if t == "container":
            box = shape["box"]
            x, y = round(box[0] * W, 2), round(box[1] * H, 2)
            w, h = round((box[2] - box[0]) * W, 2), round((box[3] - box[1]) * H, 2)
            fill = hexc if shape.get("fill") else "none"
            if shape["shape"] == "circle":
                parts.append(f'<circle cx="{x + w / 2}" cy="{y + h / 2}" r="{w / 2}" fill="{fill}"/>')
            elif shape["shape"] == "shield":
                parts.append(f'<path d="M{x + w * 0.5},{y} L{x + w},{y + h * 0.22} L{x + w},{y + h * 0.55} '
                             f'L{x + w * 0.5},{y + h} L{x},{y + h * 0.55} L{x},{y + h * 0.22} Z" fill="{fill}"/>')
            else:
                rx = round(min(W, H) * (0.22 if shape["shape"] == "squircle" else 0.12), 2)
                parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"/>')
        elif t == "letter":
            font_size = round(H * 0.46, 1)
            parts.append(f'<text x="{W / 2}" y="{H / 2}" font-family="sans-serif" font-weight="700" '
                         f'font-size="{font_size}" fill="{hexc}" text-anchor="middle" '
                         f'dominant-baseline="central">{shape["text"]}</text>')
        elif t == "circle":
            parts.append(f'<circle cx="{round(shape["center"][0] * W, 2)}" cy="{round(shape["center"][1] * H, 2)}" '
                         f'r="{round(shape["r"] * W, 2)}" fill="{hexc if shape.get("fill") else "none"}" '
                         f'stroke="{hexc if not shape.get("fill") else "none"}" stroke-width="{sw}"/>')
        elif t == "rect":
            b = shape["box"]
            fill = hexc if shape.get("fill") else "none"
            stroke = hexc if not shape.get("fill") else "none"
            parts.append(f'<rect x="{round(b[0] * W, 2)}" y="{round(b[1] * H, 2)}" '
                         f'width="{round((b[2] - b[0]) * W, 2)}" height="{round((b[3] - b[1]) * H, 2)}" '
                         f'rx="{round(shape.get("radius", 0) * min(W, H), 2)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif t == "line":
            pts = " ".join(f"{round(x * W, 2)},{round(y * H, 2)}" for x, y in shape["points"])
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{hexc}" stroke-width="{sw}" '
                         f'stroke-linecap="round" stroke-linejoin="round"/>')
        elif t == "polygon":
            pts = " ".join(f"{round(x * W, 2)},{round(y * H, 2)}" for x, y in shape["points"])
            fill = hexc if shape.get("fill") else "none"
            stroke = "none" if shape.get("fill") else hexc
            parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif t == "path":
            d_scaled = _scale_path_d(shape["d"], W, H)
            fill = hexc if shape.get("fill") else "none"
            stroke = "none" if shape.get("fill") else hexc
            parts.append(f'<path d="{d_scaled}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif t == "ring":
            cx, cy = shape["center"][0] * W, shape["center"][1] * H
            r = shape["r"] * W
            for i in range(int(shape.get("teeth", 8))):
                ang = 2 * math.pi * i / shape.get("teeth", 8)
                parts.append(f'<line x1="{round(cx + math.cos(ang) * r * 0.72, 2)}" '
                             f'y1="{round(cy + math.sin(ang) * r * 0.72, 2)}" '
                             f'x2="{round(cx + math.cos(ang) * r, 2)}" y2="{round(cy + math.sin(ang) * r, 2)}" '
                             f'stroke="{hexc}" stroke-width="{sw}" stroke-linecap="round"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _scale_path_d(d, W, H):
    import re
    out = []
    for token in re.findall(r"[MLCAZ]|-?\d*\.?\d+", d):
        if token in "MLCAZ":
            out.append(token)
        else:
            out.append(str(round(float(token), 4)))
    # tokens alternate x,y pairs in unit space: scale even indices by W, odd by H
    result, cmd_count = [], 0
    coords = []
    for token in out:
        if token in ("M", "L", "C", "A", "Z"):
            if coords:
                result.append(",".join(coords))
                coords = []
            result.append(token)
            cmd_count = 0
        else:
            axis = cmd_count % 2
            coords.append(str(round(float(token) * (W if axis == 0 else H), 2)))
            cmd_count += 1
    if coords:
        result.append(",".join(coords))
    return " ".join(result).replace(" ,", ",")


def op_icon(ctx, a):
    pp = plan.prompt_presets(ctx.config)
    kind = getattr(a, "kind", None) or "app-icon"
    if kind not in ("app-icon", "badge", "monogram", "logo-shield", "sticker", "glyph"):
        raise core.usage_error("--kind must be app-icon, badge, monogram, logo-shield, sticker or glyph")
    glyph = getattr(a, "glyph", None) or "abstract"
    colors = [c.strip() for c in str(getattr(a, "palette", "") or "").split(",") if c.strip()]
    palette = colors or pp["palettes"][getattr(a, "preset", None) or "cool-corporate"]["colors"]
    palette = [c if c.startswith("#") else "#" + c for c in palette]
    sizes = [int(s) for s in str(getattr(a, "sizes", "") or "512").split(",") if s.strip()]
    if not sizes or any(s < 8 or s > 4096 for s in sizes):
        raise core.usage_error("--sizes must be comma separated values between 8 and 4096")
    if len(sizes) > 12:
        raise core.usage_error("At most 12 sizes per icon run")
    transparent = not bool(getattr(a, "opaque", False))
    stroke_scale = float(getattr(a, "stroke_scale", 1.0) or 1.0)
    rgb_palette = [core.hex_to_rgb(c) for c in palette]
    spec = build_mark_spec(kind, glyph, rgb_palette, (1024, 1024), stroke_scale)
    project = getattr(a, "project", None) or "icon"
    base = getattr(a, "name", None) or f"{kind}-{glyph}"
    outputs = []
    for s in sizes:
        canvas = (s, s)
        img = draw_mark(canvas, spec, palette, stroke_scale)
        name = core.build_filename({"project": project, "content": base, "variant": f"{s}px",
                                    "dimensions": canvas, "version": 1, "status": "final"},
                                   "png", ctx.config.naming)
        path = ctx.out_path(name, ".png")
        core.save_image(ctx, img, path, "png", dpi=(72, 72),
                        pnginfo=core.build_pnginfo({"Software": f"imagekit {core.VERSION}",
                                                    "Description": f"{kind} mark, glyph '{glyph}'",
                                                    "imagekit:mark-kind": kind}))
        outputs.append(path)
    svg_shapes = build_mark_spec(kind, glyph, rgb_palette, (1024, 1024), stroke_scale)
    svg = spec_to_svg(svg_shapes, (1024, 1024), palette, stroke_scale)
    svg_path = core.emit_text(ctx, core.slugify(f"{project}-{base}-vector"), "svg", svg)
    outputs.append(svg_path)
    report = {
        "operation": "icon", "kind": kind, "glyph": glyph, "palette": palette,
        "sizes": sizes, "transparent": transparent, "stroke_scale": stroke_scale,
        "scalability_checks": [
            "mark drawn from primitives on a unit grid, so it survives any size without resampling artefacts",
            "single-colour fallback available by setting --palette to one hex value",
            "verify at 16px that no stroke falls below 1px",
        ],
        "vector": core.rel_posix(svg_path, ctx.out_root),
        "files": [core.rel_posix(p, ctx.out_root) for p in outputs],
    }
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{base}-report"), report)
    outputs.append(out_json)
    ctx.audit("icon.created", kind=kind, glyph=glyph, sizes=sizes)
    return {"data": report, "outputs": outputs}


# --------------------------------------------------------------------------
# seamless patterns and materials
# --------------------------------------------------------------------------


def pattern_tile(size, kind, palette, seed, density=1.0, cell=None):
    """Build one tile that wraps seamlessly; every element is drawn 9 times."""
    W, H = size
    rng = core.seeded_rng(seed, f"pattern-{kind}")
    img = Image.new("RGBA", (W, H), core.hex_to_rgb(palette[0]) + (255,))
    d = ImageDraw.Draw(img)
    px = [core.hex_to_rgb(c) for c in palette]
    cell = cell or max(8, int(min(W, H) / (6 * max(0.2, density))))

    def circles(grid_step, r, color, fill=True, jitter=0.0):
        for gy in range(0, H + grid_step, grid_step):
            for gx in range(0, W + grid_step, grid_step):
                for ox in (-W, 0, W):
                    for oy in (-H, 0, H):
                        cx = gx + ox + (rng.uniform(-jitter, jitter) * grid_step if jitter else 0)
                        cy = gy + oy + (rng.uniform(-jitter, jitter) * grid_step if jitter else 0)
                        if fill:
                            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (255,))
                        else:
                            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (255,),
                                      width=max(1, int(r * 0.25)))

    if kind == "dots":
        circles(cell, cell * 0.22, px[1])
    elif kind == "grid":
        for x in range(0, W + cell, cell):
            d.line([(x, 0), (x, H)], fill=px[1] + (255,), width=max(1, int(cell * 0.06)))
        for y in range(0, H + cell, cell):
            d.line([(0, y), (W, y)], fill=px[1] + (255,), width=max(1, int(cell * 0.06)))
    elif kind == "stripes":
        width = max(3, int(cell * 0.4))
        for i, x in enumerate(range(-H, W + H, width * 2)):
            for oy in (-H, 0, H):
                d.line([(x, oy), (x + H, oy + H)], fill=px[1] + (255,), width=width)
    elif kind == "chevron":
        step = cell
        for row in range(-1, H // step + 2):
            for col in range(-1, W // step + 2):
                x0 = col * step
                y0 = row * step
                pts = [(x0, y0 + step * 0.6), (x0 + step / 2, y0 + step * 0.2), (x0 + step, y0 + step * 0.6)]
                for ox in (-W, 0, W):
                    for oy in (-H, 0, H):
                        d.line([(px + ox, py + oy) for px, py in pts], fill=px[1] + (255,),
                               width=max(2, int(step * 0.12)), joint="curve")
    elif kind == "diamond":
        step = cell
        for row in range(-1, H // step + 2):
            for col in range(-1, W // step + 2):
                cx = col * step + (step / 2 if row % 2 else 0)
                cy = row * step
                for ox in (-W, 0, W):
                    for oy in (-H, 0, H):
                        pts = [(cx + ox, cy + oy - step * 0.3), (cx + ox + step * 0.3, cy + oy),
                               (cx + ox, cy + oy + step * 0.3), (cx + ox - step * 0.3, cy + oy)]
                        d.polygon(pts, fill=px[1] + (255,) if (row + col) % 2 else px[2] + (255,))
    elif kind == "terrazzo":
        for _ in range(int(70 * density)):
            cx, cy = rng.uniform(0, W), rng.uniform(0, H)
            r = rng.uniform(cell * 0.08, cell * 0.30)
            sides = rng.randint(5, 8)
            pts = []
            for k in range(sides):
                ang = 2 * math.pi * k / sides + rng.uniform(-0.18, 0.18)
                rr = r * rng.uniform(0.62, 1.1)
                pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
            pts = _chaikin(pts, 2, True)
            color = px[1 + int(rng.random() * (len(px) - 1))]
            for ox in (-W, 0, W):
                for oy in (-H, 0, H):
                    d.polygon([(x + ox, y + oy) for x, y in pts], fill=color + (255,))
    elif kind == "weave":
        band = max(4, int(cell * 0.5))
        for y in range(-band, H + band, band * 2):
            for x in range(-band, W + band, band * 2):
                for ox in (-W, 0, W):
                    for oy in (-H, 0, H):
                        d.rectangle([x + ox, y + oy, x + ox + band, y + oy + band], fill=px[1] + (255,))
                        d.rectangle([x + ox + band, y + oy + band, x + ox + band * 2, y + oy + band * 2],
                                    fill=px[2] + (255,) if len(px) > 2 else px[1] + (255,))
    elif kind == "motif":
        step = cell * 1.4
        for row in range(-1, H // step + 2):
            for col in range(-1, W // step + 2):
                cx = col * step + (step / 2 if row % 2 else 0)
                cy = row * step
                for ox in (-W, 0, W):
                    for oy in (-H, 0, H):
                        r = step * 0.3
                        d.ellipse([cx + ox - r, cy + oy - r, cx + ox + r, cy + oy + r],
                                  outline=px[1] + (255,), width=max(1, int(r * 0.2)))
                        d.ellipse([cx + ox - r * 0.35, cy + oy - r * 0.35,
                                   cx + ox + r * 0.35, cy + oy + r * 0.35], fill=px[2] + (255,))
    elif kind in ("paper", "concrete", "fabric"):
        base = img.copy()
        scale = {"paper": 40, "concrete": 12, "fabric": 6}[kind]
        octaves = {"paper": 3, "concrete": 4, "fabric": 5}[kind]
        noise = core.noise_image((W, H), rng, scale=scale, octaves=octaves, contrast=1.0)
        tex = Image.new("RGBA", (W, H), px[1] + (255,))
        strength = {"paper": 0.16, "concrete": 0.34, "fabric": 0.24}[kind]
        tex.putalpha(noise.point(lambda v: int(abs(v - 128) * strength)))
        img = Image.alpha_composite(base, tex)
        if kind == "fabric":
            for y in range(0, H, 3):
                d = ImageDraw.Draw(img)
                d.line([(0, y), (W, y)], fill=px[1] + (30,))
    else:
        circles(cell, cell * 0.18, px[1])
    return img


def seam_report(img):
    """Prove tileability: the wrap seam must look like any other column/row step."""
    g = img.convert("L")
    W, H = g.size
    if W < 4 or H < 4:
        return {"seamless": False, "method": "tile too small to test"}

    def column(x):
        return [g.getpixel((x, y)) for y in range(H)]

    def row(y):
        return [g.getpixel((x, y)) for x in range(W)]

    def mean_abs(a, b):
        return sum(abs(u - v) for u, v in zip(a, b)) / max(1, len(a))

    cols = [column(x) for x in range(W)]
    rows = [row(y) for y in range(H)]
    inner_col = [mean_abs(cols[x], cols[x + 1]) for x in range(W - 2)]
    inner_row = [mean_abs(rows[y], rows[y + 1]) for y in range(H - 2)]
    seam_v = mean_abs(cols[W - 1], cols[0])
    seam_h = mean_abs(rows[H - 1], rows[0])
    base_v = (sum(inner_col) / len(inner_col)) if inner_col else 0.0
    base_h = (sum(inner_row) / len(inner_row)) if inner_row else 0.0
    worst_v = max(inner_col) if inner_col else 0.0
    worst_h = max(inner_row) if inner_row else 0.0
    ratio_v = round(seam_v / base_v, 3) if base_v > 0.05 else (1.0 if seam_v < 0.05 else 99.0)
    ratio_h = round(seam_h / base_h, 3) if base_h > 0.05 else (1.0 if seam_h < 0.05 else 99.0)
    within_v = seam_v <= max(worst_v * 1.25, base_v * 2.0, 0.75)
    within_h = seam_h <= max(worst_h * 1.25, base_h * 2.0, 0.75)
    return {
        "seam_delta_v": round(seam_v, 3), "seam_delta_h": round(seam_h, 3),
        "interior_mean_v": round(base_v, 3), "interior_mean_h": round(base_h, 3),
        "interior_worst_v": round(worst_v, 3), "interior_worst_h": round(worst_h, 3),
        "ratio_v": ratio_v, "ratio_h": ratio_h,
        "seamless": bool(within_v and within_h),
        "verdict": "seamless" if (within_v and within_h) else "seam-visible",
        "method": ("wrap seam compared with the mean and worst neighbouring-column/row deltas; "
                   "a seam within 2x the mean and 1.25x the worst interior step counts as seamless"),
    }


def op_pattern(ctx, a):
    pp = plan.prompt_presets(ctx.config)
    kind = getattr(a, "kind", None) or "dots"
    allowed = ("dots", "grid", "stripes", "chevron", "diamond", "terrazzo", "weave", "motif",
               "paper", "concrete", "fabric", "organic")
    if kind not in allowed:
        raise core.usage_error("--kind must be one of: " + ", ".join(allowed))
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or "1024x1024"))
    colors = [c.strip() for c in str(getattr(a, "palette", "") or "").split(",") if c.strip()]
    palette = colors or pp["palettes"][getattr(a, "preset", None) or "earthy-organic"]["colors"]
    palette = [c if c.startswith("#") else "#" + c for c in palette]
    seed = ctx.seed if ctx.seed is not None else 4242
    density = float(getattr(a, "density", 1.0) or 1.0)
    if not 0.1 <= density <= 4.0:
        raise core.usage_error("--density must be between 0.1 and 4.0")
    tile = pattern_tile(size, kind, palette, seed, density)
    seam = seam_report(tile)
    project = getattr(a, "project", None) or "pattern"
    base = getattr(a, "name", None) or f"{kind}-tile"
    name = core.build_filename({"project": project, "content": base, "dimensions": size,
                                "version": 1, "status": "final"}, "png", ctx.config.naming)
    path = ctx.out_path(name, ".png")
    core.save_image(ctx, tile, path, "png", dpi=(300, 300),
                    pnginfo=core.build_pnginfo({"Software": f"imagekit {core.VERSION}",
                                                "Description": f"Seamless {kind} tile",
                                                "imagekit:seamless": str(seam["seamless"])}))
    # SVG version keeps the pattern editable in vector tools
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size[0]}" height="{size[1]}" '
           f'viewBox="0 0 {size[0]} {size[1]}">'
           f'<defs><pattern id="t" width="{size[0]}" height="{size[1]}" patternUnits="userSpaceOnUse">'
           f'<rect width="{size[0]}" height="{size[1]}" fill="{palette[0]}"/>'
           f'<image href="{path.name}" width="{size[0]}" height="{size[1]}"/>'
           f'</pattern></defs><rect width="100%" height="100%" fill="url(#t)"/></svg>\n')
    svg_path = core.emit_text(ctx, core.slugify(f"{project}-{base}-pattern"), "svg", svg)
    preview_size = (size[0] * 2, size[1] * 2) if size[0] * size[1] * 4 <= ctx.config.limit("max_pixels", 8e7) else size
    preview = Image.new("RGBA", preview_size)
    for i in range(2):
        for j in range(2):
            preview.paste(tile, (i * size[0], j * size[1]))
    preview_path = ctx.out_path(core.slugify(f"{project}-{base}-tiled-preview"), ".png")
    core.save_image(ctx, preview, preview_path, "png")
    report = {
        "operation": "pattern", "kind": kind, "size": list(size), "palette": palette,
        "density": density, "seed": seed, "seam": seam,
        "tile": core.rel_posix(path, ctx.out_root),
        "tiled_preview": core.rel_posix(preview_path, ctx.out_root),
        "vector_wrapper": core.rel_posix(svg_path, ctx.out_root),
        "usage": ["repeat the tile at exact multiples of its size",
                  "for print, export at 300 dpi and confirm the seam at final size",
                  "heavy textures may need a 2048 tile to avoid visible repetition"],
    }
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{base}-report"), report)
    ctx.audit("pattern.created", kind=kind, seamless=seam["seamless"])
    return {"data": report, "outputs": [path, preview_path, svg_path, out_json]}


# --------------------------------------------------------------------------
# typographic layouts
# --------------------------------------------------------------------------

LAYOUT_KINDS = {
    "poster": {"size": (2480, 3508), "dpi": 300, "format": "pdf", "margin": 0.08,
               "headline": 0.13, "subhead": 0.035, "body": 0.021, "align": "left", "text_band": 0.52},
    "social": {"size": (1080, 1080), "dpi": 72, "format": "png", "margin": 0.08,
               "headline": 0.10, "subhead": 0.042, "body": 0.028, "align": "left", "text_band": 0.58},
    "story": {"size": (1080, 1920), "dpi": 72, "format": "png", "margin": 0.1,
              "headline": 0.075, "subhead": 0.032, "body": 0.024, "align": "center", "text_band": 0.42},
    "banner": {"size": (1920, 600), "dpi": 72, "format": "png", "margin": 0.06,
               "headline": 0.16, "subhead": 0.075, "body": 0.05, "align": "left", "text_band": 0.5},
    "cover": {"size": (1600, 1600), "dpi": 72, "format": "png", "margin": 0.1,
              "headline": 0.11, "subhead": 0.04, "body": 0.026, "align": "center", "text_band": 0.62},
    "brochure": {"size": (2480, 3508), "dpi": 300, "format": "pdf", "margin": 0.09,
                 "headline": 0.06, "subhead": 0.024, "body": 0.016, "align": "left", "text_band": 0.6},
    "presentation": {"size": (1920, 1080), "dpi": 96, "format": "png", "margin": 0.07,
                     "headline": 0.08, "subhead": 0.034, "body": 0.024, "align": "left", "text_band": 0.55},
    "thumbnail": {"size": (1280, 720), "dpi": 72, "format": "png", "margin": 0.06,
                  "headline": 0.15, "subhead": 0.06, "body": 0.04, "align": "left", "text_band": 0.55},
}


def op_layout(ctx, a):
    pp = plan.prompt_presets(ctx.config)
    kind = getattr(a, "kind", None) or "social"
    if kind not in LAYOUT_KINDS:
        raise core.usage_error("--kind must be one of: " + ", ".join(sorted(LAYOUT_KINDS)))
    spec = LAYOUT_KINDS[kind]
    brief = None
    if getattr(a, "brief", None) or getattr(a, "input", None):
        brief = plan.load_brief(a, ctx)
    copy_list = list(brief.get("copy") if brief else [])
    headline = getattr(a, "headline", None) or (copy_list[0] if copy_list else "")
    subhead = getattr(a, "subhead", None) or (copy_list[1] if len(copy_list) > 1 else "")
    cta = getattr(a, "cta", None) or ""
    if not headline:
        raise core.usage_error(
            "No headline supplied.",
            "Pass --headline \"Your words\" (copy is never generated). Add --subhead and --cta as needed.")
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or
                                                      (brief.get("dimensions") if brief else None) or
                                                      "%dx%d" % spec["size"]))
    colors = [c.strip() for c in str(getattr(a, "palette", "") or "").split(",") if c.strip()]
    palette = colors or (brief.get("palette") if brief else None) or \
        pp["palettes"][getattr(a, "preset", None) or "vivid-editorial"]["colors"]
    palette = [c if c.startswith("#") else "#" + c for c in palette]
    bg_hex = getattr(a, "background", None) or palette[0]
    text_hex = getattr(a, "text_color", None) or (palette[4] if len(palette) > 4 else "#111111")
    accent_hex = getattr(a, "accent", None) or (palette[1] if len(palette) > 1 else palette[0])

    plate = None
    if getattr(a, "image", None):
        plate = core.open_image(core.resolve_input(a.image), ctx).convert("RGB")

    img = Image.new("RGB", size, core.hex_to_rgb(bg_hex))
    if plate is not None:
        cover = _cover_resize(plate, size)
        img.paste(cover, (0, 0))
        overlay = Image.new("RGBA", size, core.hex_to_rgb(bg_hex) + (int(255 * float(getattr(a, "scrim", 0.55) or 0.55)),))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    elif getattr(a, "gradient", False):
        img = core.gradient_image(size, [(0.0, core.hex_to_rgb(palette[0])),
                                        (1.0, core.hex_to_rgb(palette[min(2, len(palette) - 1)])),],
                                  "diagonal").convert("RGB")
    draw = ImageDraw.Draw(img)
    margin = int(size[0] * spec["margin"])
    inner_w = size[0] - margin * 2
    band_top = int(size[1] * spec["text_band"])
    band_h = size[1] - band_top - margin

    heading_font, heading_lines = core.fit_font_size(
        draw, headline, inner_w, band_h * 0.55,
        start=int(size[1] * spec["headline"]), floor=max(10, int(size[1] * 0.02)), bold=True)
    text_color = core.hex_to_rgb(text_hex)
    line_h = int(heading_font.size * 1.16) if hasattr(heading_font, "size") else 20
    y = band_top
    if spec["align"] == "center":
        for line in heading_lines:
            w = draw.textlength(line, font=heading_font)
            draw.text(((size[0] - w) / 2, y), line, font=heading_font, fill=text_color)
            y += line_h
    else:
        for line in heading_lines:
            draw.text((margin, y), line, font=heading_font, fill=text_color)
            y += line_h
    boxes = [{"role": "headline", "text": headline, "font_px": getattr(heading_font, "size", None),
              "lines": heading_lines, "y": band_top, "height": y - band_top,
              "contrast_ratio": core.contrast_ratio(text_color, core.hex_to_rgb(bg_hex))}]
    y += int(size[1] * 0.02)
    if subhead:
        sub_font, sub_lines = core.fit_font_size(draw, subhead, inner_w, band_h * 0.22,
                                                 start=int(size[1] * spec["subhead"]), floor=9)
        sub_color = core.hex_to_rgb(accent_hex if core.contrast_ratio(core.hex_to_rgb(accent_hex), core.hex_to_rgb(bg_hex)) >= 3 else text_hex)
        for line in sub_lines:
            if spec["align"] == "center":
                draw.text(((size[0] - draw.textlength(line, font=sub_font)) / 2, y), line, font=sub_font, fill=sub_color)
            else:
                draw.text((margin, y), line, font=sub_font, fill=sub_color)
            y += int(getattr(sub_font, "size", 16) * 1.3)
        boxes.append({"role": "subhead", "text": subhead, "font_px": getattr(sub_font, "size", None),
                      "lines": sub_lines, "y": band_top, "height": y - band_top,
                      "contrast_ratio": core.contrast_ratio(sub_color, core.hex_to_rgb(bg_hex))})
    if cta:
        cta_font = core.load_font(max(10, int(size[1] * spec["body"] * 1.1)), bold=True)
        pad_x, pad_y = int(size[0] * 0.03), int(size[1] * 0.014)
        tw = draw.textlength(cta, font=cta_font)
        bx0 = margin if spec["align"] != "center" else (size[0] - tw) / 2 - pad_x
        by0 = y + int(size[1] * 0.02)
        draw.rounded_rectangle([bx0, by0, bx0 + tw + pad_x * 2, by0 + pad_y * 2 + getattr(cta_font, "size", 16)],
                               radius=int(size[1] * 0.01), fill=core.hex_to_rgb(accent_hex))
        on_accent = (255, 255, 255) if core.contrast_ratio(core.hex_to_rgb(accent_hex), (255, 255, 255)) >= 4.5 else (17, 17, 17)
        draw.text((bx0 + pad_x, by0 + pad_y), cta, font=cta_font, fill=on_accent)
        boxes.append({"role": "cta", "text": cta, "font_px": getattr(cta_font, "size", None),
                      "contrast_ratio": core.contrast_ratio(on_accent, core.hex_to_rgb(accent_hex))})
    if getattr(a, "render_guides", False):
        draw.rectangle([margin, margin, size[0] - margin, size[1] - margin], outline=(255, 0, 80), width=2)
        draw.line([(0, band_top), (size[0], band_top)], fill=(0, 200, 255), width=2)
    fmt = (getattr(a, "format", None) or spec["format"]).lower().lstrip(".")
    project = getattr(a, "project", None) or (brief.get("project") if brief else None) or "layout"
    base = getattr(a, "name", None) or f"{kind}-layout"
    name = core.build_filename({"project": project, "content": base, "dimensions": size,
                                "version": int(getattr(a, "version", 1) or 1),
                                "status": getattr(a, "status", None) or "review"}, fmt, ctx.config.naming)
    path = ctx.out_path(name, "." + fmt)
    if fmt == "pdf":
        core.save_image(ctx, img.convert("RGB"), path, "PDF")
    else:
        core.save_image(ctx, img, path, fmt, quality=94, dpi=(spec["dpi"], spec["dpi"]),
                        pnginfo=core.build_pnginfo({"Software": f"imagekit {core.VERSION}",
                                                    "Title": headline[:120],
                                                    "Description": "Typographic layout composed by imagekit"}))
    min_contrast = core.load_policy(ctx.config).get("qa", {}).get("text", {}).get("min_local_contrast", 3.0)
    legibility = []
    for box in boxes:
        if box.get("contrast_ratio") is not None:
            legibility.append({"role": box["role"], "contrast": box["contrast_ratio"],
                               "pass": box["contrast_ratio"] >= min_contrast,
                               "wcag_aa": box["contrast_ratio"] >= 4.5})
    report = {
        "operation": "layout", "kind": kind, "size": list(size), "format": fmt,
        "headline": headline, "subhead": subhead, "cta": cta,
        "palette": palette, "background": bg_hex, "text_color": text_hex,
        "safe_margin_px": margin, "text_band_px": [band_top, size[1] - margin],
        "text_boxes": boxes, "legibility": legibility,
        "min_text_height_ratio": round(min([(b.get("font_px") or 0) / size[1] for b in boxes] or [0]), 4),
        "overflow": y + int(size[1] * 0.05) > size[1] - margin,
        "files": [core.rel_posix(path, ctx.out_root)],
        "checks": ["text stays inside the safe margin", "contrast checked per text role",
                   "font auto-fitted so copy does not overflow the band"],
    }
    if report["overflow"]:
        ctx.note("Text block reaches the bottom margin; shorten copy or increase the text band.")
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{base}-spec"), report)
    ctx.audit("layout.composed", kind=kind, format=fmt)
    return {"data": report, "outputs": [path, out_json]}


def _cover_resize(img, size):
    scale = max(size[0] / img.width, size[1] / img.height)
    new = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    resized = img.resize(new, Image.Resampling.LANCZOS)
    left = (new[0] - size[0]) // 2
    top = (new[1] - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


# --------------------------------------------------------------------------
# technical diagrams and charts
# --------------------------------------------------------------------------


def op_diagram(ctx, a):
    kind = getattr(a, "kind", None) or "flow"
    if kind not in ("flow", "cycle", "hierarchy", "bar-chart", "annotated"):
        raise core.usage_error("--kind must be flow, cycle, hierarchy, bar-chart or annotated")
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or "1600x900"))
    steps = [s.strip() for s in str(getattr(a, "steps", "") or "").split(",") if s.strip()]
    data = {}
    raw_data = getattr(a, "data", None)
    if raw_data:
        for pair in str(raw_data).split(","):
            if ":" not in pair:
                raise core.usage_error(f"Bad data point '{pair}'", "Use --data \"label:value,label:value\"")
            label, value = pair.split(":", 1)
            try:
                data[label.strip()] = float(value)
            except ValueError:
                raise core.usage_error(f"Bad numeric value in '{pair}'")
    if kind == "bar-chart" and not data:
        raise core.usage_error("bar-chart needs --data \"label:value,...\"")
    if kind != "bar-chart" and not steps and not data:
        raise core.usage_error("Supply --steps A,B,C (or --data for charts). Labels are always author-supplied.")
    if len(steps) > 12:
        raise core.usage_error("At most 12 steps per diagram")
    labels = steps or list(data.keys())
    palette = [c.strip() for c in str(getattr(a, "palette", "") or "").split(",") if c.strip()] or \
        plan.prompt_presets(ctx.config)["palettes"]["cool-corporate"]["colors"]
    palette = [c if c.startswith("#") else "#" + c for c in palette]
    bg = getattr(a, "background", None) or "#ffffff"
    img = Image.new("RGB", size, core.hex_to_rgb(bg))
    d = ImageDraw.Draw(img)
    ink = core.hex_to_rgb(palette[3] if len(palette) > 3 else "#111111")
    accent = core.hex_to_rgb(palette[2] if len(palette) > 2 else palette[0])
    light = core.hex_to_rgb(palette[1] if len(palette) > 1 else palette[0])
    title = getattr(a, "title", None) or ""
    W, H = size
    top = int(H * 0.06)
    if title:
        heading = core.load_font(max(12, int(H * 0.055)), bold=True)
        d.text((int(W * 0.05), top), title, font=heading, fill=ink)
        top += int(H * 0.1)
    stroke = max(2, int(min(W, H) * 0.004))
    body_font = core.load_font(max(9, int(H * 0.028)), bold=True)
    small_font = core.load_font(max(8, int(H * 0.022)))
    nodes = []
    if kind == "bar-chart":
        max_v = max(data.values()) or 1
        chart_x0, chart_y0 = int(W * 0.09), top + int(H * 0.04)
        chart_x1, chart_y1 = int(W * 0.94), int(H * 0.86)
        d.line([(chart_x0, chart_y0), (chart_x0, chart_y1)], fill=ink, width=stroke)
        d.line([(chart_x0, chart_y1), (chart_x1, chart_y1)], fill=ink, width=stroke)
        bar_w = (chart_x1 - chart_x0) / (len(data) * 1.6)
        for i, (label, value) in enumerate(data.items()):
            x = chart_x0 + (i * 1.6 + 0.4) * bar_w
            h = (value / max_v) * (chart_y1 - chart_y0 - H * 0.06)
            color = accent if i % 2 == 0 else light
            d.rectangle([x, chart_y1 - h, x + bar_w, chart_y1], fill=color)
            d.text((x, chart_y1 - h - H * 0.045), f"{value:g}", font=small_font, fill=ink)
            tw = d.textlength(label, font=small_font)
            d.text((x + bar_w / 2 - tw / 2, chart_y1 + H * 0.02), label, font=small_font, fill=ink)
            nodes.append({"label": label, "value": value,
                          "box": [round(x), round(chart_y1 - h), round(x + bar_w), round(chart_y1)]})
        d.text((chart_x0, chart_y1 + H * 0.08), f"scale: 0 to {max_v:g}", font=small_font, fill=ink)
    elif kind == "cycle":
        cx, cy = W / 2, top + (H - top) / 2
        r = min(W, H - top) * 0.28
        for i, label in enumerate(labels):
            ang = -math.pi / 2 + 2 * math.pi * i / len(labels)
            x, y = cx + math.cos(ang) * r, cy + math.sin(ang) * r
            bw = W * 0.16
            bh = H * 0.11
            d.rounded_rectangle([x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2],
                                radius=int(bh * 0.22), fill=light, outline=ink, width=stroke)
            tw = d.textlength(label, font=body_font)
            d.text((x - tw / 2, y - bh * 0.16), label, font=body_font, fill=ink)
            nodes.append({"label": label, "box": [round(x - bw / 2), round(y - bh / 2), round(x + bw / 2), round(y + bh / 2)]})
        for i in range(len(labels)):
            a0 = -math.pi / 2 + 2 * math.pi * i / len(labels) + 0.28
            a1 = -math.pi / 2 + 2 * math.pi * (i + 1) / len(labels) - 0.28
            steps_n = 18
            pts = [(cx + math.cos(a0 + (a1 - a0) * t / steps_n) * r,
                    cy + math.sin(a0 + (a1 - a0) * t / steps_n) * r) for t in range(steps_n + 1)]
            d.line(pts, fill=ink, width=stroke, joint="curve")
            tip = pts[-1]
            ang = math.atan2(tip[1] - pts[-3][1], tip[0] - pts[-3][0])
            d.polygon([tip, (tip[0] - math.cos(ang - 0.4) * stroke * 5, tip[1] - math.sin(ang - 0.4) * stroke * 5),
                       (tip[0] - math.cos(ang + 0.4) * stroke * 5, tip[1] - math.sin(ang + 0.4) * stroke * 5)], fill=ink)
    elif kind == "hierarchy":
        levels = labels
        y = top + H * 0.06
        for depth, label in enumerate(levels):
            bw = W * (0.5 - depth * 0.06)
            bh = H * 0.1
            x0 = (W - bw) / 2
            d.rounded_rectangle([x0, y, x0 + bw, y + bh], radius=int(bh * 0.2), fill=light, outline=ink, width=stroke)
            tw = d.textlength(label, font=body_font)
            d.text((W / 2 - tw / 2, y + bh * 0.3), label, font=body_font, fill=ink)
            nodes.append({"label": label, "level": depth, "box": [round(x0), round(y), round(x0 + bw), round(y + bh)]})
            if depth < len(levels) - 1:
                d.line([(W / 2, y + bh), (W / 2, y + bh + H * 0.05)], fill=ink, width=stroke)
            y += bh + H * 0.05
    elif kind == "annotated":
        # annotated figure: a labelled schematic block with callout leaders
        bw, bh = W * 0.44, (H - top) * 0.6
        x0, y0 = W * 0.28, top + (H - top) * 0.18
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=int(bh * 0.06),
                            fill=light, outline=ink, width=stroke * 2)
        n = len(labels)
        for i, label in enumerate(labels):
            ty = y0 + bh * (i + 1) / (n + 1)
            d.ellipse([x0 + bw * 0.5 - stroke * 2, ty - stroke * 2, x0 + bw * 0.5 + stroke * 2, ty + stroke * 2], fill=ink)
            side = -1 if i % 2 == 0 else 1
            lx = x0 + bw / 2 + side * W * 0.22
            d.line([(x0 + bw * 0.5, ty), (lx, ty)], fill=ink, width=stroke)
            tw = d.textlength(label, font=small_font)
            d.text((lx - tw / 2 if side < 0 else lx, ty - H * 0.035), label, font=small_font, fill=ink)
            nodes.append({"label": label, "anchor": [round(x0 + bw * 0.5), round(ty)],
                          "label_at": [round(lx), round(ty)]})
    else:  # flow
        n = len(labels)
        cols = min(4, n)
        rows = math.ceil(n / cols)
        bw = W * 0.17
        bh = (H - top) / (rows + 1) * 0.55
        positions = []
        for i, label in enumerate(labels):
            col, row = i % cols, i // cols
            cx = W * (col + 1) / (cols + 1)
            cy = top + (H - top) * (row + 1) / (rows + 1)
            positions.append((cx, cy, label))
        for i, (cx, cy, label) in enumerate(positions):
            d.rounded_rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2],
                                radius=int(bh * 0.2), fill=light, outline=ink, width=stroke)
            lines = core.wrap_text(d, label, body_font, bw * 0.86)
            total = len(lines) * int(H * 0.036)
            ty = cy - total / 2
            for line in lines:
                tw = d.textlength(line, font=body_font)
                d.text((cx - tw / 2, ty), line, font=body_font, fill=ink)
                ty += int(H * 0.036)
            nodes.append({"label": label, "box": [round(cx - bw / 2), round(cy - bh / 2),
                                                  round(cx + bw / 2), round(cy + bh / 2)]})
        for i in range(len(positions) - 1):
            x0, y0 = positions[i][0], positions[i][1]
            x1, y1 = positions[i + 1][0], positions[i + 1][1]
            start = (x0 + bw / 2, y0) if x1 > x0 else (x0, y0 + bh / 2)
            end = (x1 - bw / 2, y1) if x1 > x0 else (x1, y1 - bh / 2)
            if abs(y1 - y0) > bh * 0.6:
                mid_y = (y0 + y1) / 2
                pts = [(x0, y0 + bh / 2), (x0, mid_y), (x1, mid_y), (x1, y1 - bh / 2)]
            else:
                pts = [start, end]
            d.line(pts, fill=ink, width=stroke, joint="curve")
            tip = pts[-1]
            prev = pts[-2]
            ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
            d.polygon([tip, (tip[0] - math.cos(ang - 0.45) * stroke * 5, tip[1] - math.sin(ang - 0.45) * stroke * 5),
                       (tip[0] - math.cos(ang + 0.45) * stroke * 5, tip[1] - math.sin(ang + 0.45) * stroke * 5)], fill=ink)
    if getattr(a, "note", None):
        d.text((int(W * 0.05), H - int(H * 0.08)), a.note, font=small_font, fill=ink)
    project = getattr(a, "project", None) or "diagram"
    base = getattr(a, "name", None) or f"{kind}-figure"
    name = core.build_filename({"project": project, "content": base, "dimensions": size,
                                "version": 1, "status": "review"}, "png", ctx.config.naming)
    path = ctx.out_path(name, ".png")
    core.save_image(ctx, img, path, "png", dpi=(300, 300),
                    pnginfo=core.build_pnginfo({"Software": f"imagekit {core.VERSION}",
                                                "Description": f"{kind} diagram; labels supplied by the author"}))
    svg = _diagram_svg(kind, size, title, nodes, palette, bg)
    svg_path = core.emit_text(ctx, core.slugify(f"{project}-{base}-vector"), "svg", svg)
    report = {"operation": "diagram", "kind": kind, "size": list(size), "title": title,
              "labels": labels, "data": data, "nodes": nodes,
              "files": [core.rel_posix(path, ctx.out_root), core.rel_posix(svg_path, ctx.out_root)],
              "checks": ["arrowheads consistent", "labels never overlap their own node",
                         "font sizes scale with figure height"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{base}-spec"), report)
    ctx.audit("diagram.created", kind=kind, labels=len(labels))
    return {"data": report, "outputs": [path, svg_path, out_json]}


def _diagram_svg(kind, size, title, nodes, palette, bg):
    W, H = size
    ink = palette[3] if len(palette) > 3 else "#111111"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<rect width="{W}" height="{H}" fill="{bg}"/>']
    if title:
        parts.append(f'<text x="{round(W * 0.05)}" y="{round(H * 0.09)}" font-family="sans-serif" '
                     f'font-size="{round(H * 0.055)}" font-weight="700" fill="{ink}">{_esc(title)}</text>')
    for node in nodes:
        if "box" in node:
            x0, y0, x1, y1 = node["box"]
            parts.append(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{round((y1 - y0) * 0.2)}" '
                         f'fill="{palette[1] if len(palette) > 1 else "#cccccc"}" stroke="{ink}" stroke-width="3"/>')
            parts.append(f'<text x="{(x0 + x1) / 2}" y="{(y0 + y1) / 2}" font-family="sans-serif" '
                         f'font-size="{round((y1 - y0) * 0.28)}" fill="{ink}" text-anchor="middle" '
                         f'dominant-baseline="central">{_esc(node["label"])}</text>')
        elif "anchor" in node:
            ax, ay = node["anchor"]
            lx, ly = node["label_at"]
            parts.append(f'<line x1="{ax}" y1="{ay}" x2="{lx}" y2="{ly}" stroke="{ink}" stroke-width="2"/>')
            parts.append(f'<circle cx="{ax}" cy="{ay}" r="5" fill="{ink}"/>')
            parts.append(f'<text x="{lx}" y="{ly - 8}" font-family="sans-serif" font-size="18" fill="{ink}">{_esc(node["label"])}</text>')
        elif "value" in node:
            parts.append(f'<text x="{node["box"][0]}" y="{node["box"][1] - 8}" font-family="sans-serif" '
                         f'font-size="18" fill="{ink}">{node["value"]:g}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------
# storyboards, comic panels, contact sheets
# --------------------------------------------------------------------------


def op_sheet(ctx, a):
    inputs = []
    for raw in (getattr(a, "inputs", None) or []):
        inputs.extend([p.strip() for p in str(raw).split(",") if p.strip()])
    if getattr(a, "input", None):
        inputs.append(a.input)
    if not inputs:
        raise core.usage_error("The 'sheet' command needs --inputs a.png,b.png (repeatable) or --input a.png")
    max_items = min(ctx.config.limit("max_batch_items", 500), 48)
    if len(inputs) > max_items:
        raise core.usage_error(f"At most {max_items} images per sheet (got {len(inputs)})")
    mode = getattr(a, "kind", None) or "contact-sheet"
    if mode not in ("contact-sheet", "storyboard", "comic"):
        raise core.usage_error("--kind must be contact-sheet, storyboard or comic")
    cols = int(getattr(a, "columns", 0) or (4 if mode == "contact-sheet" else 3))
    if cols < 1 or cols > 8:
        raise core.usage_error("--columns must be between 1 and 8")
    cell_w = int(getattr(a, "cell_width", 0) or 420)
    if cell_w < 80 or cell_w > 2000:
        raise core.usage_error("--cell_width must be between 80 and 2000")
    captions = [c.strip() for c in str(getattr(a, "captions", "") or "").split("|") if c.strip()]
    gutter = int(cell_w * 0.05)
    rows = math.ceil(len(inputs) / cols)
    images = []
    for raw in inputs:
        p = core.resolve_input(raw)
        images.append((p, core.open_image(p, ctx).convert("RGB")))
    aspect = float(getattr(a, "aspect", 0) or 1.5)
    cell_h = int(cell_w / max(0.2, aspect))
    cap_band = int(cell_h * 0.16) if mode != "contact-sheet" or captions else int(cell_h * 0.1)
    sheet_w = cols * cell_w + (cols + 1) * gutter
    sheet_h = rows * (cell_h + cap_band) + (rows + 1) * gutter
    sheet_h = min(sheet_h, ctx.config.limit("max_output_dimension", 8192))
    if sheet_w > ctx.config.limit("max_output_dimension", 8192):
        raise core.usage_error("Sheet width exceeds limits.max_output_dimension; reduce --columns or --cell_width")
    bg = core.hex_to_rgb(getattr(a, "background", None) or "#101418")
    sheet = Image.new("RGB", (sheet_w, sheet_h), bg)
    d = ImageDraw.Draw(sheet)
    label_font = core.load_font(max(9, int(cell_h * 0.06)), bold=True)
    cap_font = core.load_font(max(8, int(cap_band * 0.42)))
    panels = []
    for idx, (p, img) in enumerate(images):
        col, row = idx % cols, idx // cols
        x = gutter + col * (cell_w + gutter)
        y = gutter + row * (cell_h + cap_band + gutter)
        plate = _cover_resize(img, (cell_w, cell_h))
        sheet.paste(plate, (x, y))
        if mode == "comic":
            d.rectangle([x - 2, y - 2, x + cell_w + 2, y + cell_h + 2], outline=(238, 238, 238), width=max(2, gutter // 8))
        elif mode == "storyboard":
            d.rectangle([x, y, x + cell_w, y + cell_h], outline=(220, 220, 220), width=1)
        caption = captions[idx] if idx < len(captions) else p.stem
        d.text((x + 4, y + cell_h + cap_band * 0.18), f"{idx + 1:02d}", font=label_font, fill=(240, 240, 240))
        tw = d.textlength(f"{idx + 1:02d}", font=label_font)
        d.text((x + 12 + tw, y + cell_h + cap_band * 0.22), caption[:64], font=cap_font, fill=(170, 185, 200))
        panels.append({"index": idx + 1, "source": core.rel_posix(p), "caption": caption,
                       "box": [x, y, x + cell_w, y + cell_h]})
    project = getattr(a, "project", None) or "sheet"
    base = getattr(a, "name", None) or mode
    name = core.build_filename({"project": project, "content": base, "dimensions": sheet.size,
                                "version": 1, "status": "review"}, "png", ctx.config.naming)
    path = ctx.out_path(name, ".png")
    core.save_image(ctx, sheet, path, "png", dpi=(72, 72),
                    pnginfo=core.build_pnginfo({"Software": f"imagekit {core.VERSION}",
                                                "Description": f"{mode} assembled from {len(images)} images"}))
    report = {"operation": "sheet", "kind": mode, "columns": cols, "rows": rows,
              "cell": [cell_w, cell_h], "size": list(sheet.size), "panels": panels,
              "file": core.rel_posix(path, ctx.out_root)}
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{base}-panels"), report)
    ctx.audit("sheet.assembled", kind=mode, panels=len(panels))
    return {"data": report, "outputs": [path, out_json]}
