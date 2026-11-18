"""pixel-art-media: procedural generators.

Seeded sprites/icons, seamless patterns, autotile tilesets (16/47 blob),
effect animations, motion from stills, bitmap-font text, UI widgets, and
voxel models with isometric rendering / turntables / sprite stacks / .vox IO.
All generators are deterministic for a given seed and parameters.
"""
from __future__ import annotations

import colorsys
import json
import math
import random
import re
import struct
from pathlib import Path

from PIL import Image, ImageDraw

import importlib.util
import sys


def I():
    m = sys.modules.get("tools_imaging")
    if m is None:
        p = Path(__file__).resolve().parent / "tools-imaging.py"
        spec = importlib.util.spec_from_file_location("tools_imaging", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["tools_imaging"] = m
        spec.loader.exec_module(m)
    return m


def S():
    return I().sibling("sheets")


def P():
    return I().sibling("palettes")


# ------------------------------------------------------------------- text ---

_FONT_CACHE = None


def get_font(name="5x7"):
    global _FONT_CACHE
    L = I()
    if _FONT_CACHE is None:
        _FONT_CACHE = L.load_asset_json("asset-font.json")
    fonts = _FONT_CACHE["fonts"]
    if name not in fonts:
        raise L.ToolError("bad-arg", f"Unknown font '{name}'. Options: {', '.join(fonts)}")
    return fonts[name]


def tokenize(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == "{":
            j = text.find("}", i)
            if j > i:
                out.append(text[i:j + 1]); i = j + 1; continue
        out.append(text[i]); i += 1
    return out


def measure_text(text, font="5x7", spacing=1, scale=1):
    f = get_font(font)
    lines = text.split("\n")
    widths = []
    for line in lines:
        toks = tokenize(line)
        w = sum(f["width"] + spacing for _ in toks) - (spacing if toks else 0)
        widths.append(max(0, w))
    return (max(widths) * scale, (len(lines) * (f["height"] + spacing) - spacing) * scale)


def render_text(text, font="5x7", color=(255, 255, 255, 255), spacing=1, scale=1,
                outline_color=None, shadow_color=None, align="left", bg=(0, 0, 0, 0)):
    L = I()
    f = get_font(font)
    gw, gh = f["width"], f["height"]
    glyphs = f["glyphs"]
    tw, th = measure_text(text, font, spacing, 1)
    pad = 1 if (outline_color or shadow_color) else 0
    img = Image.new("RGBA", (tw + pad * 2 + (1 if shadow_color else 0),
                             th + pad * 2 + (1 if shadow_color else 0)), bg)
    px = img.load()
    y = pad
    for line in text.split("\n"):
        toks = tokenize(line)
        lw = sum(gw + spacing for _ in toks) - (spacing if toks else 0)
        x = pad + {"left": 0, "center": (tw - lw) // 2, "right": tw - lw}[align]
        for tok in toks:
            key = tok if tok.startswith("{") else tok.upper()
            rows = glyphs.get(key, glyphs.get("?", [0] * gh))
            for ry, bits in enumerate(rows):
                for rx in range(gw):
                    if bits & (1 << (gw - 1 - rx)):
                        if shadow_color:
                            px[x + rx + 1, y + ry + 1] = tuple(shadow_color)
                        px[x + rx, y + ry] = tuple(color)
            x += gw + spacing
        y += gh + spacing
    if outline_color:
        img = L.outline(img, tuple(outline_color), mode="outer")
    if scale > 1:
        img = L.nearest_scale(img, scale)
    return img


def cmd_text_render(a, ctx):
    L = I()
    text = a.text.replace("\\n", "\n")
    color = L.parse_color(a.color)
    oc = L.parse_color(a.outline) if a.outline else None
    sc = L.parse_color(a.shadow) if a.shadow else None
    bg = L.parse_color(a.background)
    if a.effect:
        return _text_effect(a, ctx, text, color, oc, sc, bg)
    img = render_text(text, a.font, color, a.spacing, a.scale, oc, sc, a.align, bg)
    L.check_dims(ctx.cfg, img.width, img.height)
    L.save_image(img, a.output, ctx)
    return {"output": a.output, "size": list(img.size), "font": a.font, "text": a.text}


def _text_effect(a, ctx, text, color, oc, sc, bg):
    L = I()
    n = a.frames
    if n > ctx.cfg["limits"]["max_frames"]:
        raise L.ToolError("limit-frames", "Too many text-effect frames.")
    base = render_text(text, a.font, color, a.spacing, 1, oc, sc, a.align, bg)
    frames = []
    rng = random.Random(a.seed)
    f = get_font(a.font)
    cell = f["width"] + a.spacing
    for i in range(n):
        t = i / n
        if a.effect == "type":
            shown = max(1, math.ceil(len(text) * t))
            partial = render_text(text[:shown], a.font, color, a.spacing, 1, oc, sc, a.align, bg)
            fr = Image.new("RGBA", base.size, bg)
            fr.alpha_composite(partial)
        elif a.effect == "wave":
            canvas = Image.new("RGBA", (base.width, base.height + 4), bg)
            for cx in range(base.width):
                dy = round(2 * math.sin(2 * math.pi * (t + cx / max(1, base.width) * 1.5)))
                colimg = base.crop((cx, 0, cx + 1, base.height))
                canvas.alpha_composite(colimg, (cx, 2 + dy))
            fr = canvas
        elif a.effect == "bounce":
            dy = -abs(round(3 * math.sin(2 * math.pi * t)))
            canvas = Image.new("RGBA", (base.width, base.height + 3), bg)
            canvas.alpha_composite(base, (0, 3 + dy))
            fr = canvas
        elif a.effect == "flicker":
            v = 0.75 + 0.25 * math.sin(2 * math.pi * t * 2)
            fr = base.copy()
            r, g, b, al = fr.split()
            fr = Image.merge("RGBA", (r.point(lambda x: int(x * v)),
                                      g.point(lambda x: int(x * v)),
                                      b.point(lambda x: int(x * v)), al))
        elif a.effect == "rainbow":
            fr = base.copy()
            data = []
            w = fr.width
            for idx, px in enumerate(fr.getdata()):
                if px[3] == 0:
                    data.append(px); continue
                h = (t + (idx % w) / max(1, w)) % 1.0
                r, g, b = colorsys.hsv_to_rgb(h, 0.8, 1.0)
                data.append((int(r * 255), int(g * 255), int(b * 255), px[3]))
            fr.putdata(data)
        else:
            raise L.ToolError("bad-arg", f"Unknown text effect '{a.effect}'.")
        if a.scale > 1:
            fr = L.nearest_scale(fr, a.scale)
        frames.append(fr)
    durs = [1000 // a.fps] * n
    info = S().encode_animation(frames, durs, a.output, ctx)
    res = {"output": a.output, "effect": a.effect, **info}
    if a.frames_dir:
        for i, fr in enumerate(frames):
            L.save_image(fr, str(Path(a.frames_dir) / f"frame-{i:03d}.png"), ctx)
        res["frames_dir"] = a.frames_dir
    return res


def cmd_font_sheet(a, ctx):
    L = I()
    f = get_font(a.font)
    glyphs = f["glyphs"]
    keys = sorted(glyphs.keys())
    gw, gh = f["width"], f["height"]
    cols = a.cols
    rows = math.ceil(len(keys) / cols)
    pad = 1
    sheet = Image.new("RGBA", (cols * (gw + pad) + pad, rows * (gh + pad) + pad), (0, 0, 0, 0))
    mapping = {"font": a.font, "glyph_width": gw, "glyph_height": gh, "image": Path(a.output).name,
               "glyphs": {}}
    color = L.parse_color(a.color)
    for i, k in enumerate(keys):
        r, c = divmod(i, cols)
        x, y = pad + c * (gw + pad), pad + r * (gh + pad)
        for ry, bits in enumerate(glyphs[k]):
            for rx in range(gw):
                if bits & (1 << (gw - 1 - rx)):
                    sheet.putpixel((x + rx, y + ry), color)
        mapping["glyphs"][k] = {"x": x, "y": y, "w": gw, "h": gh,
                                "advance": gw + a.spacing}
    L.save_image(sheet, a.output, ctx)
    jpath = str(Path(a.output).with_suffix(".json"))
    L.write_json(mapping, jpath, ctx)
    return {"sheet": a.output, "mapping": jpath, "glyphs": len(keys), "font": a.font}


# ---------------------------------------------------------------- sprites ---

def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c[:3]) + (255,)


def ramp3(palette):
    """Pick dark/mid/light proportionally from any palette (>=1 colors)."""
    L = I()
    ramp = sorted(palette, key=L.luminance)
    n = len(ramp)
    return (ramp[round((n - 1) * 0.15)], ramp[round((n - 1) * 0.5)], ramp[round((n - 1) * 0.85)])


def gen_sprite(kind="creature", size=16, seed=7, palette=None, symmetry=True):
    L = I()
    rng = random.Random(seed)
    pal_mod = P()
    if palette is None:
        palette = pal_mod.generate_palette(mood=rng.choice(list(pal_mod.MOODS)), scheme="mono",
                                           ramps=1, steps=5, seed=seed)
    ramp = sorted(palette[:5], key=L.luminance)
    w = h = size
    half = (w + 1) // 2
    grid = [[0] * w for _ in range(h)]
    density = {"creature": 0.55, "robot": 0.62, "bug": 0.5, "ghost": 0.58, "slime": 0.66}.get(kind, 0.55)
    # cellular body growth in left half
    for y in range(2, h - 2):
        for x in range(1, half):
            center_bias = 1 - abs(x - half * 0.55) / half
            vert = 1 - abs(y - h * 0.5) / (h * 0.62)
            if rng.random() < density * (0.5 + center_bias) * (0.4 + vert):
                grid[y][x] = 1
    # smooth pass (keep pixels with >=2 neighbors)
    for _ in range(2):
        ng = [[0] * w for _ in range(h)]
        for y in range(h):
            for x in range(half):
                n = sum(grid[yy][xx] for yy in range(max(0, y - 1), min(h, y + 2))
                        for xx in range(max(0, x - 1), min(half, x + 2))) - grid[y][x]
                ng[y][x] = 1 if (grid[y][x] and n >= 2) or (not grid[y][x] and n >= 5) else 0
        grid = ng
    if symmetry:
        for y in range(h):
            for x in range(half):
                grid[y][w - 1 - x] = grid[y][x]
    # ensure non-empty
    if not any(any(row) for row in grid):
        for y in range(h // 3, 2 * h // 3):
            for x in range(w // 3, 2 * w // 3):
                grid[y][x] = 1
    img = Image.new("RGBA", (w, h))
    px = img.load()
    base = ramp[len(ramp) // 2]
    for y in range(h):
        for x in range(w):
            if not grid[y][x]:
                continue
            edge = any(not (0 <= x + dx < w and 0 <= y + dy < h and grid[y + dy][x + dx])
                       for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
            if edge and (x <= w // 2 and y <= h // 2):
                c = _shade(base, 1.35)  # top-left lit rim
            elif edge:
                c = _shade(base, 0.55)
            else:
                t = 1.15 - (y / h) * 0.5 + rng.uniform(-0.06, 0.06)
                c = _shade(base, t)
            px[x, y] = c
    # eyes / features
    if kind in ("creature", "robot", "bug"):
        ey = h // 3
        ex1, ex2 = w // 2 - 2, w // 2 + 1
        for ex in (ex1, ex2):
            if grid[ey][ex]:
                px[ex, ey] = (250, 250, 250, 255)
                if ey + 1 < h and grid[ey + 1][ex]:
                    px[ex, ey + 1] = (20, 20, 30, 255)
    if kind == "robot":
        ay = 0
        ax = w // 2
        for y in range(h):
            if grid[y][ax]:
                ay = y
                break
        if ay > 0:
            px[ax, ay - 1] = _shade(base, 1.5)
    img = L.outline(img, (18, 16, 24, 255), mode="outer")
    return img, {"kind": kind, "size": size, "seed": seed}


def cmd_sprite_generate(a, ctx):
    L = I()
    palette = P().read_palette_any(a.palette) if a.palette else None
    outdir = Path(a.outdir)
    manifest = {"generator": "sprite-generate", "kind": a.kind, "size": a.size,
                "base_seed": a.seed, "sprites": []}
    frames, names = [], []
    for i in range(a.count):
        seed = a.seed + i
        img, info = gen_sprite(a.kind, a.size, seed, palette)
        name = f"{a.kind}-{seed:04d}.png"
        L.save_image(img, str(outdir / name), ctx)
        manifest["sprites"].append({"file": name, **info})
        frames.append(img); names.append(name[:-4])
    L.write_json(manifest, str(outdir / "sprites.json"), ctx)
    res = {"outdir": a.outdir, "count": a.count, "manifest": str(outdir / "sprites.json")}
    if a.sheet:
        sheet, meta = S().pack_frames(frames, names, layout="grid",
                                      cols=min(8, a.count), padding=1, cfg=ctx.cfg)
        L.save_image(sheet, a.sheet, ctx)
        res["sheet"] = a.sheet
    return res


ICON_KINDS = ["potion", "coin", "gem", "key", "sword", "heart", "star", "shield",
              "ring", "scroll", "chest", "mushroom"]


def gen_icon(kind="potion", size=16, seed=7, palette=None):
    L = I()
    rng = random.Random(seed)
    if palette is None:
        palette = P().generate_palette(base=rng.choice(["#c04040", "#4080c0", "#40a060", "#c0a040"]),
                                       scheme="mono", ramps=1, steps=5, seed=seed)
    ramp = sorted(palette[:5], key=L.luminance)
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    s = size / 16.0

    def R(*coords):
        return tuple(int(round(v * s)) for v in coords)

    dark, mid, light = ramp3(palette)
    if kind == "potion":
        d.rectangle(R(6, 2, 9, 4), fill=(160, 120, 80, 255))
        d.rectangle(R(5, 5, 10, 6), fill=_shade(light, 1.1))
        d.ellipse(R(4, 6, 11, 13), fill=mid)
        d.ellipse(R(5, 8, 10, 12), fill=light)
        d.rectangle(R(6, 7, 7, 8), fill=(255, 255, 255, 200))
    elif kind == "coin":
        d.ellipse(R(3, 3, 12, 12), fill=mid)
        d.ellipse(R(4, 4, 11, 11), fill=light)
        d.ellipse(R(6, 6, 9, 9), fill=mid)
        d.line(R(5, 5, 7, 5), fill=(255, 255, 240, 255))
    elif kind == "gem":
        d.polygon([R(8, 2), R(13, 7), R(8, 14), R(3, 7)], fill=mid)
        d.polygon([R(8, 2), R(11, 7), R(8, 12), R(5, 7)], fill=light)
        d.line([R(6, 5), R(8, 3)], fill=(255, 255, 255, 220))
    elif kind == "key":
        d.ellipse(R(3, 3, 8, 8), fill=light)
        d.ellipse(R(5, 5, 6, 6), fill=(0, 0, 0, 0))
        d.rectangle(R(7, 7, 13, 8), fill=mid)
        d.rectangle(R(11, 9, 12, 10), fill=mid)
        d.rectangle(R(13, 9, 13, 11), fill=mid)
    elif kind == "sword":
        d.polygon([R(12, 2), R(13, 3), R(6, 10), R(5, 9)], fill=light)
        d.line([R(12, 3), R(6, 9)], fill=(255, 255, 255, 200))
        d.rectangle(R(3, 9, 7, 10), fill=mid)
        d.rectangle(R(4, 10, 5, 13), fill=(120, 80, 50, 255))
    elif kind == "heart":
        d.ellipse(R(3, 4, 8, 9), fill=mid)
        d.ellipse(R(7, 4, 12, 9), fill=mid)
        d.polygon([R(3, 7), R(12, 7), R(8, 13)], fill=mid)
        d.ellipse(R(4, 5, 6, 7), fill=light)
    elif kind == "star":
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = 6.2 if i % 2 == 0 else 2.6
            pts.append(R(8 + r * math.cos(ang), 8 + r * math.sin(ang)))
        d.polygon(pts, fill=light)
        d.polygon(pts[0:3] + [R(8, 8)], fill=_shade(light, 1.2))
    elif kind == "shield":
        d.polygon([R(3, 3), R(12, 3), R(12, 8), R(8, 13), R(3, 8)], fill=mid)
        d.polygon([R(5, 5), R(10, 5), R(10, 8), R(8, 11), R(5, 8)], fill=light)
        d.line([R(8, 5), R(8, 10)], fill=dark)
    elif kind == "ring":
        d.ellipse(R(4, 6, 11, 13), outline=light, width=max(1, int(s)))
        d.polygon([R(8, 2), R(10, 4), R(8, 6), R(6, 4)], fill=(120, 220, 255, 255))
    elif kind == "scroll":
        d.rectangle(R(4, 3, 11, 12), fill=(235, 220, 180, 255))
        d.rectangle(R(3, 2, 12, 4), fill=(200, 180, 140, 255))
        d.rectangle(R(3, 11, 12, 13), fill=(200, 180, 140, 255))
        for ly in (6, 8, 10):
            d.line(R(5, ly, 10, ly), fill=(120, 100, 70, 255))
    elif kind == "chest":
        d.rectangle(R(3, 6, 12, 12), fill=(150, 100, 55, 255))
        d.rectangle(R(3, 4, 12, 7), fill=(180, 125, 70, 255))
        d.rectangle(R(7, 6, 8, 9), fill=(230, 200, 90, 255))
        d.line(R(3, 7, 12, 7), fill=(90, 60, 35, 255))
    elif kind == "mushroom":
        d.ellipse(R(3, 3, 12, 9), fill=mid)
        d.rectangle(R(6, 9, 9, 13), fill=(235, 225, 200, 255))
        d.ellipse(R(5, 4, 7, 6), fill=light)
        d.ellipse(R(9, 5, 11, 7), fill=light)
    else:
        raise L.ToolError("bad-arg", f"Unknown icon kind '{kind}'. Options: {', '.join(ICON_KINDS)}")
    img = img.resize((size, size), Image.Resampling.NEAREST) if img.size != (size, size) else img
    img = L.outline(img, (18, 16, 24, 255), mode="outer")
    return img


def cmd_icon_generate(a, ctx):
    L = I()
    palette = P().read_palette_any(a.palette) if a.palette else None
    outdir = Path(a.outdir)
    kinds = ICON_KINDS if a.kind == "all" else [a.kind]
    manifest = {"generator": "icon-generate", "size": a.size, "icons": []}
    frames, names = [], []
    for kind in kinds:
        for v in range(a.variants):
            seed = a.seed + v
            img = gen_icon(kind, a.size, seed, palette)
            name = f"{kind}-{seed:04d}.png"
            L.save_image(img, str(outdir / name), ctx)
            manifest["icons"].append({"file": name, "kind": kind, "seed": seed})
            frames.append(img); names.append(name[:-4])
    L.write_json(manifest, str(outdir / "icons.json"), ctx)
    res = {"outdir": a.outdir, "count": len(frames)}
    if a.sheet:
        sheet, _ = S().pack_frames(frames, names, layout="grid", cols=min(8, len(frames)),
                                   padding=1, cfg=ctx.cfg)
        L.save_image(sheet, a.sheet, ctx)
        res["sheet"] = a.sheet
    return res


# --------------------------------------------------------------- patterns ---

PATTERN_KINDS = ["checker", "stripes", "diagonal", "dots", "noise", "brick", "weave",
                 "scales", "grass", "water", "stone", "wood"]


def gen_pattern(kind="checker", size=32, seed=7, palette=None, scale=4):
    L = I()
    rng = random.Random(seed)
    if palette is None:
        palette = P().generate_palette(mood="forest", ramps=1, steps=5, seed=seed)
    ramp = sorted(palette[:5], key=L.luminance)
    img = Image.new("RGBA", (size, size))
    px = img.load()
    c0, c1, c2 = ramp3(palette)
    for y in range(size):
        for x in range(size):
            u, v = x // max(1, scale), y // max(1, scale)
            if kind == "checker":
                c = c0 if (u + v) % 2 == 0 else c1
            elif kind == "stripes":
                c = c0 if v % 2 == 0 else c1
            elif kind == "diagonal":
                c = (c0, c1, c2)[(u + v) % 3]
            elif kind == "dots":
                c = c2 if (u % 3 == 1 and v % 3 == 1) else c0
            elif kind == "noise":
                c = rng.choice([c0, c1, c1, c2])
            elif kind == "brick":
                row = y // max(2, scale)
                off = (scale * 2) if row % 2 else 0
                mortar = (y % max(2, scale) == 0) or ((x + off) % (scale * 4) == 0)
                c = c0 if mortar else (c1 if (row + (x + off) // (scale * 4)) % 2 else c2)
            elif kind == "weave":
                c = c1 if ((u % 4 < 2) != (v % 4 < 2)) else c0
                if u % 4 == 0 or v % 4 == 0:
                    c = _shade(c, 0.8)
            elif kind == "scales":
                row = y // max(2, scale)
                off = scale if row % 2 else 0
                cx = ((x + off) % (scale * 2)) - scale
                cy = y % max(2, scale)
                c = c2 if (cx * cx + cy * cy * 2) < scale * scale else c0
            elif kind == "grass":
                c = rng.choice([c0, c1, c1, c2])
                if rng.random() < 0.06:
                    c = _shade(c2, 1.3)
            elif kind == "water":
                wv = math.sin(2 * math.pi * x / size * 2) + math.sin(2 * math.pi * y / size * 3)
                c = c2 if wv > 0.8 else (c1 if wv > -0.4 else c0)
            elif kind == "stone":
                n = math.sin(x * 0.7 + seed) + math.sin(y * 0.9) + math.sin((x + y) * 0.4)
                c = c1 if n > 0.5 else (c0 if n < -0.8 else c2)
                if rng.random() < 0.05:
                    c = _shade(c, 0.75)
            elif kind == "wood":
                g = math.sin(2 * math.pi * (x / size) * 3 + math.sin(y * 0.3) * 0.8)
                c = c2 if g > 0.3 else (c1 if g > -0.5 else c0)
            else:
                raise L.ToolError("bad-arg", f"Unknown pattern '{kind}'. Options: {', '.join(PATTERN_KINDS)}")
            px[x, y] = tuple(c[:3]) + (255,)
    return img


def cmd_pattern_generate(a, ctx):
    L = I()
    palette = P().read_palette_any(a.palette) if a.palette else None
    img = gen_pattern(a.kind, a.size, a.seed, palette, a.scale)
    L.save_image(img, a.output, ctx)
    res = {"output": a.output, "kind": a.kind, "size": a.size, "seed": a.seed,
           "seamless": a.kind not in ("noise", "grass", "stone")}
    if a.preview:
        prev = Image.new("RGBA", (a.size * 2, a.size * 2))
        for yy in (0, a.size):
            for xx in (0, a.size):
                prev.paste(img, (xx, yy))
        L.save_image(prev, a.preview, ctx)
        res["preview"] = a.preview
    return res


# --------------------------------------------------------------- tilesets ---

TERRAINS = {
    "grass": {"base": "#4a8f3c", "edge": "#2f5f28", "light": "#6fb85a"},
    "dirt": {"base": "#8a6242", "edge": "#5d3f2a", "light": "#a97e57"},
    "sand": {"base": "#d8bd7a", "edge": "#a68d52", "light": "#efd89b"},
    "stone": {"base": "#8b8f96", "edge": "#5c6066", "light": "#adb1b8"},
    "water": {"base": "#3a6fb0", "edge": "#274b7a", "light": "#5d95d6"},
    "snow": {"base": "#e8eef4", "edge": "#b7c4d0", "light": "#ffffff"},
    "lava": {"base": "#d1541e", "edge": "#8a2f0e", "light": "#f2a13c"},
}


def blob47_masks():
    """Canonical 47 blob masks. Bits: N=1 NE=2 E=4 SE=8 S=16 SW=32 W=64 NW=128."""
    seen = {}
    for m in range(256):
        n = m
        if not (m & 1 and m & 4):
            n &= ~2
        if not (m & 16 and m & 4):
            n &= ~8
        if not (m & 16 and m & 64):
            n &= ~32
        if not (m & 1 and m & 64):
            n &= ~128
        seen.setdefault(n, m)
    return sorted(seen.keys())


def render_autotile(base_img, mask, mode16=True):
    """Draw edges/corners on a terrain tile for a neighbor bitmask."""
    L = I()
    t = base_img.copy()
    d = ImageDraw.Draw(t)
    w, h = t.size
    e = max(1, w // 8)
    edge = tuple(int(v * 0.55) for v in base_img.getpixel((w // 2, h // 2))[:3]) + (255,)
    light = tuple(min(255, int(v * 1.3)) for v in base_img.getpixel((w // 2, h // 2))[:3]) + (255,)
    N, E, Sv, W = bool(mask & 1), bool(mask & 4), bool(mask & 16), bool(mask & 64)
    if mode16:
        if not N:
            d.rectangle((0, 0, w - 1, e - 1), fill=light)
            d.rectangle((0, e, w - 1, e), fill=edge)
        if not Sv:
            d.rectangle((0, h - e, w - 1, h - 1), fill=edge)
        if not W:
            d.rectangle((0, 0, e - 1, h - 1), fill=light if not N else edge)
        if not E:
            d.rectangle((w - e, 0, w - 1, h - 1), fill=edge)
        return t
    # blob: orthogonals + corners
    if not N:
        d.rectangle((0, 0, w - 1, e - 1), fill=light)
    if not Sv:
        d.rectangle((0, h - e, w - 1, h - 1), fill=edge)
    if not W:
        d.rectangle((0, 0, e - 1, h - 1), fill=light)
    if not E:
        d.rectangle((w - e, 0, w - 1, h - 1), fill=edge)
    # outer corners (round cut where two orthogonals missing)
    if not N and not W:
        d.rectangle((0, 0, e - 1, e - 1), fill=(0, 0, 0, 0))
        d.point((e - 1, e - 1), fill=edge)
    if not N and not E:
        d.rectangle((w - e, 0, w - 1, e - 1), fill=(0, 0, 0, 0))
        d.point((w - e, e - 1), fill=edge)
    if not Sv and not W:
        d.rectangle((0, h - e, e - 1, h - 1), fill=(0, 0, 0, 0))
        d.point((e - 1, h - e), fill=edge)
    if not Sv and not E:
        d.rectangle((w - e, h - e, w - 1, h - 1), fill=(0, 0, 0, 0))
        d.point((w - e, h - e), fill=edge)
    # inner corners: both orthogonals present, diagonal missing
    if N and W and not (mask & 128):
        d.rectangle((0, 0, e - 1, e - 1), fill=edge)
    if N and E and not (mask & 2):
        d.rectangle((w - e, 0, w - 1, e - 1), fill=edge)
    if Sv and W and not (mask & 32):
        d.rectangle((0, h - e, e - 1, h - 1), fill=edge)
    if Sv and E and not (mask & 8):
        d.rectangle((w - e, h - e, w - 1, h - 1), fill=edge)
    return t


def cmd_tileset_generate(a, ctx):
    L = I()
    if a.terrain not in TERRAINS and not a.base_color:
        raise L.ToolError("bad-arg", f"Unknown terrain '{a.terrain}'. Options: {', '.join(TERRAINS)} or --base-color")
    ts = a.tile_size
    if ts < 8 or ts > 128:
        raise L.ToolError("bad-arg", "tile-size must be 8..128.")
    seed = a.seed
    if a.base_color:
        base_rgb = L.parse_color(a.base_color)[:3]
        pal = P().generate_ramp(base_rgb, steps=5)
    else:
        t = TERRAINS[a.terrain]
        pal = [L.parse_color(t["edge"])[:3], L.parse_color(t["base"])[:3], L.parse_color(t["light"])[:3]]
    base = gen_pattern("noise" if a.terrain in ("grass", "dirt") else
                       ("water" if a.terrain == "water" else "stone"),
                       ts, seed, pal, scale=max(1, ts // 16))
    masks = list(range(16)) if a.mode == "16" else blob47_masks()
    variants = max(1, a.variants)
    total = len(masks) * variants
    cols = a.cols or (4 if a.mode == "16" else 8)
    rows = math.ceil(total / cols)
    pad = a.padding
    sheet = Image.new("RGBA", (cols * (ts + pad) + pad, rows * (ts + pad) + pad), (0, 0, 0, 0))
    tiles = []
    for i, m in enumerate(masks):
        for v in range(variants):
            b = base if v == 0 else gen_pattern("noise", ts, seed + v + 1, pal, scale=max(1, ts // 16))
            tile = render_autotile(b, m if a.mode != "16" else m, mode16=(a.mode == "16"))
            k = i * variants + v
            r, c = divmod(k, cols)
            x, y = pad + c * (ts + pad), pad + r * (ts + pad)
            sheet.alpha_composite(tile, (x, y))
            tiles.append({"id": k, "mask": m, "variant": v, "x": x, "y": y, "w": ts, "h": ts})
    L.save_image(sheet, a.output, ctx)
    meta = {"tileset": Path(a.output).name, "terrain": a.terrain, "mode": a.mode,
            "tile_size": ts, "padding": pad, "masks": "N=1,E=4,S=16,W=64 (blob adds NE=2,SE=8,SW=32,NW=128)",
            "tiles": tiles, "seed": seed}
    jpath = str(Path(a.output).with_suffix(".json"))
    L.write_json(meta, jpath, ctx)
    res = {"output": a.output, "metadata": jpath, "tiles": len(tiles), "mode": a.mode,
           "sheet_size": list(sheet.size)}
    if a.preview_map:
        res["preview_map"] = render_tilemap_preview(a, ctx, sheet, tiles, ts, pad, masks, variants)
    return res


def render_tilemap_preview(a, ctx, sheet, tiles, ts, pad, masks, variants):
    L = I()
    if not Path(a.preview_map).is_file():
        raise L.ToolError("input-not-found", f"Preview map file not found: {a.preview_map}",
                          "--preview-map takes an ASCII map file ('#' cells filled), not an output path.")
    lines = Path(a.preview_map).read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if l.strip()]
    if not lines:
        raise L.ToolError("bad-map", "Preview map file is empty.")
    h = len(lines); w = max(len(l) for l in lines)
    filled = [[ch == a.fill_char for ch in l.ljust(w)] for l in lines]
    out = Image.new("RGBA", (w * ts, h * ts), (0, 0, 0, 0))
    by_mask = {t["mask"]: t for t in tiles if t["variant"] == 0}
    for y in range(h):
        for x in range(w):
            if not filled[y][x]:
                continue
            if a.mode == "16":
                m = (1 if y > 0 and filled[y - 1][x] else 0) | \
                    (4 if x < w - 1 and filled[y][x + 1] else 0) | \
                    (16 if y < h - 1 and filled[y + 1][x] else 0) | \
                    (64 if x > 0 and filled[y][x - 1] else 0)
            else:
                def at(dx, dy):
                    nx, ny = x + dx, y + dy
                    return 0 <= nx < w and 0 <= ny < h and filled[ny][nx]
                m = (1 if at(0, -1) else 0) | (2 if at(1, -1) else 0) | (4 if at(1, 0) else 0) | \
                    (8 if at(1, 1) else 0) | (16 if at(0, 1) else 0) | (32 if at(-1, 1) else 0) | \
                    (64 if at(-1, 0) else 0) | (128 if at(-1, -1) else 0)
                if not (m & 1 and m & 4): m &= ~2
                if not (m & 16 and m & 4): m &= ~8
                if not (m & 16 and m & 64): m &= ~32
                if not (m & 1 and m & 64): m &= ~128
            t = by_mask.get(m)
            if t is None:
                continue
            tile = sheet.crop((t["x"], t["y"], t["x"] + ts, t["y"] + ts))
            out.alpha_composite(tile, (x * ts, y * ts))
    ppath = str(Path(a.output).with_name(Path(a.output).stem + "-map.png"))
    L.save_image(out, ppath, ctx)
    return ppath


# ---------------------------------------------------------------- effects ---

EFFECT_KINDS = ["fire", "rain", "snow", "sparkle", "explosion", "smoke", "water", "energy"]
EXACT_LOOP = {"sparkle", "water", "energy"}


def fire_palette():
    stops = [(0, 0, 0), (40, 8, 8), (120, 20, 8), (200, 60, 10),
             (240, 130, 20), (250, 200, 60), (255, 245, 180)]
    pal = []
    for i in range(32):
        t = i / 31 * (len(stops) - 1)
        k = min(len(stops) - 2, int(t))
        f = t - k
        pal.append(tuple(int(stops[k][j] * (1 - f) + stops[k + 1][j] * f) for j in range(3)))
    return pal


def gen_effect(kind, size=48, frames_n=16, seed=7, palette=None, fps=12):
    L = I()
    rng = random.Random(seed)
    w = h = size
    frames = []
    if kind == "fire":
        pal = palette or fire_palette()
        heat = [[0] * w for _ in range(h)]
        allf = []
        for f in range(frames_n + 24):  # warmup
            for x in range(w):
                heat[h - 1][x] = 31 if rng.random() > 0.08 else rng.randint(18, 31)
            for y in range(h - 1):
                for x in range(w):
                    src_x = (x + rng.choice([-1, 0, 0, 1])) % w
                    decay = rng.randint(0, 2)
                    heat[y][x] = max(0, heat[y + 1][src_x] - decay)
            if f >= 24:
                img = Image.new("RGBA", (w, h))
                img.putdata([tuple(pal[heat[y][x]][:3]) + ((255,) if heat[y][x] > 1 else (0,))
                             for y in range(h) for x in range(w)])
                allf.append(img)
        frames = allf
    elif kind in ("rain", "snow"):
        n = max(8, size // 2)
        parts = []
        for _ in range(n):
            speed = rng.choice([2, 4, 6]) if kind == "rain" else rng.choice([1, 2])
            parts.append({"x": rng.randrange(w), "y0": rng.randrange(h), "sp": speed,
                          "len": rng.randint(3, 6), "ph": rng.uniform(0, 6.28),
                          "amp": rng.uniform(0.5, 2.0)})
        col = (150, 190, 255, 220) if kind == "rain" else (245, 248, 255, 255)
        for f in range(frames_n):
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            for pt in parts:
                y = (pt["y0"] + pt["sp"] * f) % h
                if kind == "rain":
                    for k in range(pt["len"]):
                        img.putpixel((pt["x"] % w, (y + k) % h), col)
                else:
                    x = int(pt["x"] + pt["amp"] * math.sin(2 * math.pi * f / frames_n + pt["ph"])) % w
                    img.putpixel((x, y), col)
                    if size >= 32:
                        img.putpixel(((x + 1) % w, y), col)
            frames.append(img)
        # note: loops exactly when speed*frames_n % h == 0; speeds chosen from divisors when possible
    elif kind == "sparkle":
        n = max(4, size // 6)
        stars = [{"x": rng.randrange(w), "y": rng.randrange(h),
                  "ph": rng.randrange(frames_n), "life": rng.randint(max(3, frames_n // 3), frames_n)}
                 for _ in range(n)]
        for f in range(frames_n):
            img = Image.new("RGBA", (w, h))
            for st in stars:
                t = ((f - st["ph"]) % frames_n) / st["life"]
                if t > 1:
                    continue
                b = math.sin(math.pi * t)
                c = (255, 255, int(180 + 60 * b), int(255 * b))
                x, y = st["x"], st["y"]
                img.putpixel((x, y), c)
                r = 1 + int(b * 2)
                for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r)):
                    if 0 <= x + dx < w and 0 <= y + dy < h:
                        img.putpixel((x + dx, y + dy), (c[0], c[1], c[2], c[3] // 2))
            frames.append(img)
    elif kind == "explosion":
        cx, cy = w // 2, h // 2
        debris = [(rng.uniform(0, 6.28), rng.uniform(0.5, 1.0)) for _ in range(14)]
        for f in range(frames_n):
            t = f / max(1, frames_n - 1)
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            r = t * size * 0.45
            if t < 0.25:
                d.ellipse((cx - r * 1.4, cy - r * 1.4, cx + r * 1.4, cy + r * 1.4),
                          fill=(255, 250, 220, 255))
            if r > 1:
                d.ellipse((cx - r, cy - r, cx + r, cy + r),
                          outline=(250, 150 + int(80 * (1 - t)), 40, int(255 * (1 - t))),
                          width=max(1, size // 16))
                r2 = r * 0.65
                d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2),
                          fill=(255, 220, 120, int(200 * max(0, 1 - t * 1.6))))
            for ang, sp in debris:
                dd = t * size * 0.5 * sp
                x, y = int(cx + dd * math.cos(ang)), int(cy + dd * math.sin(ang) + t * t * 6)
                if 0 <= x < w and 0 <= y < h:
                    img.putpixel((x, y), (255, 180, 60, int(255 * (1 - t))))
            frames.append(img)
    elif kind == "smoke":
        puffs = [{"x": w // 2 + rng.randint(-3, 3), "ph": rng.uniform(0, 1),
                  "drift": rng.uniform(-0.4, 0.4), "r": rng.uniform(2, size / 8)}
                 for _ in range(6)]
        for f in range(frames_n):
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            for pf in puffs:
                t = (f / frames_n + pf["ph"]) % 1.0
                y = h - 2 - t * (h - 6)
                x = pf["x"] + pf["drift"] * t * h * 0.3
                r = pf["r"] * (0.5 + t)
                alpha = int(160 * (1 - t) * min(1, t * 6))
                g = 150 + int(60 * t)
                d.ellipse((x - r, y - r, x + r, y + r), fill=(g, g, g, alpha))
            frames.append(img)
    elif kind == "water":
        pal = palette or P().generate_palette(base="#3a6fb0", ramps=1, steps=5, seed=seed)
        dark, mid, light = ramp3(pal)
        deep = _shade(dark, 0.8)
        for f in range(frames_n):
            img = Image.new("RGBA", (w, h))
            px = img.load()
            ph = 2 * math.pi * f / frames_n
            for y in range(h):
                for x in range(w):
                    v = math.sin(2 * math.pi * x / w * 2 + ph) + math.sin(2 * math.pi * y / h * 2 - ph)
                    c = light if v > 1.2 else (mid if v > 0.3 else (dark if v > -0.8 else deep))
                    px[x, y] = c[:3] + (255,)
            frames.append(img)
    elif kind == "energy":
        cx, cy = w / 2, h / 2
        base_r = size * 0.22
        sparks = [(rng.uniform(0, 6.28), rng.uniform(0.7, 1.3)) for _ in range(8)]
        for f in range(frames_n):
            t = f / frames_n
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            r = base_r * (1 + 0.15 * math.sin(2 * math.pi * t))
            d.ellipse((cx - r * 1.5, cy - r * 1.5, cx + r * 1.5, cy + r * 1.5),
                      fill=(80, 160, 255, 60))
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(120, 200, 255, 200))
            d.ellipse((cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5),
                      fill=(240, 250, 255, 255))
            for ang, sp in sparks:
                a2 = ang + 2 * math.pi * t * sp
                x = int(cx + math.cos(a2) * r * 1.7)
                y = int(cy + math.sin(a2) * r * 1.7)
                if 0 <= x < w and 0 <= y < h:
                    img.putpixel((x, y), (200, 240, 255, 255))
            frames.append(img)
    else:
        raise L.ToolError("bad-arg", f"Unknown effect '{kind}'. Options: {', '.join(EFFECT_KINDS)}")
    return frames[:frames_n]


def cmd_effect_generate(a, ctx):
    L = I()
    palette = P().read_palette_any(a.palette) if a.palette else None
    frames = gen_effect(a.kind, a.size, a.frames, a.seed, palette, a.fps)
    outdir = Path(a.outdir)
    for i, fr in enumerate(frames):
        L.save_image(fr, str(outdir / f"frame-{i:03d}.png"), ctx)
    durs = [1000 // a.fps] * len(frames)
    res = {"outdir": a.outdir, "kind": a.kind, "frames": len(frames),
           "exact_loop": a.kind in EXACT_LOOP, "seed": a.seed}
    if a.animation:
        info = S().encode_animation(frames, durs, a.animation, ctx)
        res["animation"] = a.animation
        res.update(info)
    if a.sheet:
        sheet, meta = S().pack_frames(frames, [f"frame-{i:03d}" for i in range(len(frames))],
                                      durs, layout="grid", cols=min(8, len(frames)),
                                      padding=1, cfg=ctx.cfg)
        L.save_image(sheet, a.sheet, ctx)
        L.write_json(meta, str(Path(a.sheet).with_suffix(".json")), ctx)
        res["sheet"] = a.sheet
    return res


# ----------------------------------------------------------------- motion ---

MOTION_KINDS = ["idle-bob", "hover", "shake", "jump", "walk-bob", "coin-spin",
                "hit-flash", "dissolve", "pulse"]


def gen_motion(kind, sprite, frames_n=12, amplitude=2, seed=7):
    L = I()
    rng = random.Random(seed)
    w, h = sprite.size
    frames = []
    pad = amplitude + 3
    canvas_size = (w + pad * 2, h + pad * 2)
    for f in range(frames_n):
        t = f / frames_n
        canvas = Image.new("RGBA", canvas_size)
        if kind == "idle-bob":
            dy = round(amplitude * 0.5 * (1 - math.cos(2 * math.pi * t)))
            canvas.alpha_composite(sprite, (pad, pad + dy))
        elif kind == "hover":
            dy = round(amplitude * math.sin(2 * math.pi * t))
            canvas.alpha_composite(sprite, (pad, pad + dy))
        elif kind == "shake":
            decay = 1 - t
            dx = round(amplitude * decay * math.sin(2 * math.pi * t * 4))
            dy = round(amplitude * 0.5 * decay * math.cos(2 * math.pi * t * 3))
            canvas.alpha_composite(sprite, (pad + dx, pad + dy))
        elif kind == "jump":
            yt = 4 * t * (1 - t)  # parabola 0..1..0
            dy = -round(amplitude * 2 * yt)
            sq = 1 if 0.1 < t < 0.9 else 0
            spr = sprite
            if sq == 0:
                spr = sprite.resize((w + 1, max(1, h - 1)), Image.Resampling.NEAREST)
            canvas.alpha_composite(spr, (pad, pad + dy + (h - spr.height)))
        elif kind == "walk-bob":
            dy = -abs(round(amplitude * math.sin(2 * math.pi * t * 2)))
            lean = round(math.sin(2 * math.pi * t * 2))
            spr = sprite.copy()
            shifted = Image.new("RGBA", (w, h))
            for y in range(h):
                row = spr.crop((0, y, w, y + 1))
                dx = lean if y < h // 2 else 0
                shifted.alpha_composite(row, (dx, y))
            canvas.alpha_composite(shifted, (pad, pad + dy))
        elif kind == "coin-spin":
            cw = max(1, round(w * abs(math.cos(math.pi * t))))
            spr = sprite.resize((cw, h), Image.Resampling.NEAREST)
            if math.cos(math.pi * t) < 0:
                spr = spr.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            canvas.alpha_composite(spr, (pad + (w - cw) // 2, pad))
        elif kind == "hit-flash":
            spr = sprite.copy()
            if f % 4 < 2 and f < frames_n * 0.6:
                white = Image.new("RGBA", (w, h), (255, 255, 255, 255))
                mask = spr.getchannel("A")
                spr = Image.composite(white, spr, mask)
            canvas.alpha_composite(spr, (pad, pad))
        elif kind == "dissolve":
            m = L.bayer_matrix(4)
            thr = t * 16
            data = []
            for i, px in enumerate(sprite.getdata()):
                keep = m[(i // w) % 4][i % 4] >= thr
                data.append(px if keep else (0, 0, 0, 0))
            spr = Image.new("RGBA", (w, h)); spr.putdata(data)
            canvas.alpha_composite(spr, (pad, pad))
        elif kind == "pulse":
            v = 1 + 0.25 * math.sin(2 * math.pi * t)
            spr = sprite.copy()
            r, g, b, al = spr.split()
            spr = Image.merge("RGBA", (r.point(lambda x: min(255, int(x * v))),
                                       g.point(lambda x: min(255, int(x * v))),
                                       b.point(lambda x: min(255, int(x * v))), al))
            canvas.alpha_composite(spr, (pad, pad))
        else:
            raise L.ToolError("bad-arg", f"Unknown motion '{kind}'. Options: {', '.join(MOTION_KINDS)}")
        frames.append(canvas)
    return frames


def cmd_motion_generate(a, ctx):
    L = I()
    sprite = L.load_image(a.input, ctx.cfg)
    frames = gen_motion(a.kind, sprite, a.frames, a.amplitude, a.seed)
    durs = [1000 // a.fps] * len(frames)
    res = {"kind": a.kind, "frames": len(frames)}
    if a.outdir:
        for i, fr in enumerate(frames):
            L.save_image(fr, str(Path(a.outdir) / f"frame-{i:03d}.png"), ctx)
        res["outdir"] = a.outdir
    if a.animation:
        info = S().encode_animation(frames, durs, a.animation, ctx)
        res["animation"] = a.animation
        res.update(info)
    if a.sheet:
        sheet, meta = S().pack_frames(frames, [f"frame-{i:03d}" for i in range(len(frames))],
                                      durs, layout="horizontal", padding=1, cfg=ctx.cfg)
        L.save_image(sheet, a.sheet, ctx)
        L.write_json(meta, str(Path(a.sheet).with_suffix(".json")), ctx)
        res["sheet"] = a.sheet
    if not (a.outdir or a.animation or a.sheet):
        raise L.ToolError("bad-arg", "Provide --outdir, --animation or --sheet for output.")
    return res


# --------------------------------------------------------------------- ui ---

def nine_slice_panel(w, h, fill=(40, 44, 68, 255), border=(220, 225, 240, 255),
                     style="bevel", corner=3):
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    dark = tuple(max(0, int(v * 0.55)) for v in fill[:3]) + (255,)
    light = tuple(min(255, int(v * 1.5)) for v in fill[:3]) + (255,)
    if style == "rounded":
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=corner, fill=fill, outline=border)
    else:
        d.rectangle((0, 0, w - 1, h - 1), fill=fill, outline=border)
        if style == "bevel":
            d.line((1, 1, w - 2, 1), fill=light)
            d.line((1, 1, 1, h - 2), fill=light)
            d.line((1, h - 2, w - 2, h - 2), fill=dark)
            d.line((w - 2, 1, w - 2, h - 2), fill=dark)
    return img


def cmd_ui_generate(a, ctx):
    L = I()
    fill = L.parse_color(a.fill)
    border = L.parse_color(a.border)
    accent = L.parse_color(a.accent)
    res = {"kind": a.kind, "size": [a.width, a.height]}
    if a.kind == "panel":
        img = nine_slice_panel(a.width, a.height, fill, border, a.style)
        L.save_image(img, a.output, ctx)
        meta = {"nine_slice": {"left": 4, "top": 4, "right": 4, "bottom": 4},
                "style": a.style, "image": Path(a.output).name}
        L.write_json(meta, str(Path(a.output).with_suffix(".json")), ctx)
        res["nine_slice"] = meta["nine_slice"]
    elif a.kind == "button":
        states = ["normal", "hover", "pressed", "disabled", "focus"]
        imgs = []
        for st in states:
            f = fill
            b = border
            if st == "hover":
                f = tuple(min(255, int(v * 1.25)) for v in fill[:3]) + (255,)
            elif st == "pressed":
                f = tuple(int(v * 0.7) for v in fill[:3]) + (255,)
            elif st == "disabled":
                g = sum(fill[:3]) // 3
                f = (g, g, g, 160)
                b = (120, 120, 120, 200)
            elif st == "focus":
                b = accent
            p = nine_slice_panel(a.width, a.height, f, b, a.style)
            if a.label:
                t = render_text(a.label, "3x5", (245, 245, 250, 255) if st != "disabled" else (180, 180, 180, 255),
                                align="center")
                p.alpha_composite(t, ((a.width - t.width) // 2, (a.height - t.height) // 2 + (1 if st == "pressed" else 0)))
            if st == "focus":
                d = ImageDraw.Draw(p)
                d.rectangle((2, 2, a.width - 3, a.height - 3), outline=accent)
            imgs.append(p)
        sheet = Image.new("RGBA", (a.width * len(states), a.height))
        for i, p in enumerate(imgs):
            sheet.alpha_composite(p, (i * a.width, 0))
        L.save_image(sheet, a.output, ctx)
        L.write_json({"states": {st: {"x": i * a.width, "y": 0, "w": a.width, "h": a.height}
                                 for i, st in enumerate(states)},
                      "image": Path(a.output).name},
                     str(Path(a.output).with_suffix(".json")), ctx)
        res["states"] = states
    elif a.kind == "bar":
        img = Image.new("RGBA", (a.width, a.height))
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, a.width - 1, a.height - 1), fill=(20, 22, 30, 255), outline=border)
        inner = max(0, a.width - 4)
        fillw = round(inner * max(0.0, min(1.0, a.value)))
        if a.segmented:
            segs = max(1, a.width // 8)
            for sgi in range(segs):
                sx = 2 + sgi * (inner / segs)
                if sx + inner / segs - 1 <= 2 + fillw:
                    d.rectangle((sx, 2, sx + inner / segs - 2, a.height - 3), fill=accent)
        else:
            d.rectangle((2, 2, 2 + fillw - 1, a.height - 3), fill=accent)
            d.line((2, 2, 2 + fillw - 1, 2), fill=tuple(min(255, int(v * 1.4)) for v in accent[:3]) + (255,))
        L.save_image(img, a.output, ctx)
        res["value"] = a.value
    elif a.kind == "dialog":
        img = nine_slice_panel(a.width, a.height, fill, border, a.style)
        if a.label:
            plate = nine_slice_panel(min(a.width - 8, 8 + len(a.label) * 4), 11,
                                     tuple(int(v * 0.8) for v in fill[:3]) + (255,), border, "flat")
            t = render_text(a.label, "3x5", accent)
            plate.alpha_composite(t, (4, 3))
            img.alpha_composite(plate, (4, -5))
        d = ImageDraw.Draw(img)
        d.polygon([(a.width - 10, a.height - 7), (a.width - 6, a.height - 7),
                   (a.width - 8, a.height - 4)], fill=accent)
        L.save_image(img, a.output, ctx)
    elif a.kind == "cursor":
        img = Image.new("RGBA", (16, 16))
        d = ImageDraw.Draw(img)
        d.polygon([(2, 1), (2, 12), (5, 9), (7, 13), (9, 12), (7, 8), (11, 8)],
                  fill=(250, 250, 250, 255), outline=(20, 20, 20, 255))
        img = L.nearest_scale(img, max(1, a.width // 16))
        L.save_image(img, a.output, ctx)
        res["hotspot"] = [2, 1]
    elif a.kind == "slot":
        cols, rows = a.cols or 4, a.rows or 3
        cell = a.width
        img = Image.new("RGBA", (cols * (cell + 2) + 2, rows * (cell + 2) + 2), (0, 0, 0, 0))
        for r in range(rows):
            for c in range(cols):
                p = nine_slice_panel(cell, cell, fill, border, "bevel")
                img.alpha_composite(p, (2 + c * (cell + 2), 2 + r * (cell + 2)))
        L.save_image(img, a.output, ctx)
        res["grid"] = [cols, rows]
        res["cell"] = cell
    elif a.kind == "heart":
        full = gen_icon("heart", 16, seed=1, palette=[(120, 20, 30), (200, 40, 50), (240, 90, 90), (255, 160, 150), (255, 220, 210)])
        empty = L.recolor_hsv(full, sat=0.0, val=0.45)
        half = full.copy()
        hp = half.load()
        ep = empty.load()
        for y in range(16):
            for x in range(8, 16):
                hp[x, y] = ep[x, y]
        sheet = Image.new("RGBA", (16 * 3 + 4, 16))
        for i, himg in enumerate((full, half, empty)):
            sheet.alpha_composite(himg, (i * 17, 0))
        if a.width != 16:
            sheet = L.nearest_scale(sheet, max(1, a.width // 16))
        L.save_image(sheet, a.output, ctx)
        res["states"] = ["full", "half", "empty"]
    else:
        raise L.ToolError("bad-arg", "kind must be panel|button|bar|dialog|cursor|slot|heart.")
    res["output"] = a.output
    return res


# ------------------------------------------------------------------ voxel ---

def vox_read(path):
    L = I()
    data = Path(path).read_bytes()
    if data[:4] != b"VOX ":
        raise L.ToolError("bad-vox", "Not a MagicaVoxel .vox file.")
    pos = 8
    size = None
    voxels = []
    palette = None
    while pos + 12 <= len(data):
        cid = data[pos:pos + 4]
        n, m = struct.unpack("<II", data[pos + 4:pos + 12])
        if cid == b"MAIN":
            pos += 12  # descend into MAIN children
            continue
        content = data[pos + 12:pos + 12 + n]
        if cid == b"SIZE":
            size = struct.unpack("<III", content[:12])
        elif cid == b"XYZI":
            count = struct.unpack("<I", content[:4])[0]
            for i in range(count):
                x, y, z, ci = content[4 + i * 4:8 + i * 4]
                voxels.append((x, y, z, ci))
        elif cid == b"RGBA":
            palette = [tuple(content[i:i + 3]) for i in range(0, 1024, 4)]
        pos += 12 + n + m
    if size is None:
        raise L.ToolError("bad-vox", "VOX file missing SIZE chunk.")
    if palette is None:
        palette = [(i, i, i) for i in range(256)]
    return {"size": list(size), "voxels": voxels, "palette": palette, "format": "vox"}


def vox_write(model, path, ctx):
    L = I()
    sx, sy, sz = model["size"]
    pal = model["palette"][:256]
    body = bytearray()
    size_c = struct.pack("<III", sx, sy, sz)
    xyzi = bytearray(struct.pack("<I", len(model["voxels"])))
    for x, y, z, ci in model["voxels"]:
        xyzi += bytes((x, y, z, ci))
    rgba = bytearray()
    for i in range(256):
        c = pal[i] if i < len(pal) else (0, 0, 0)
        rgba += bytes((c[0], c[1], c[2], 255))
    children = bytearray()
    for cid, content in ((b"SIZE", size_c), (b"XYZI", bytes(xyzi)), (b"RGBA", bytes(rgba))):
        children += cid + struct.pack("<II", len(content), 0) + content
    data = b"VOX " + struct.pack("<I", 150) + b"MAIN" + struct.pack("<II", 0, len(children)) + bytes(children)
    return L.atomic_write(ctx, path, lambda tmp: Path(tmp).write_bytes(data))


def model_read(path):
    L = I()
    p = Path(path)
    if not p.is_file():
        raise L.ToolError("input-not-found", f"Model not found: {path}")
    if p.suffix.lower() == ".vox":
        return vox_read(path)
    obj = L.read_json(path)
    if "size" not in obj or "voxels" not in obj:
        raise L.ToolError("bad-model", "Model JSON needs size, palette, voxels[[x,y,z,colorIndex]].")
    pal = [L.parse_color(c)[:3] for c in obj.get("palette", ["#888888"])]
    return {"size": obj["size"], "palette": pal,
            "voxels": [tuple(v) for v in obj["voxels"]], "format": "json"}


def model_write(model, path, ctx):
    L = I()
    if Path(path).suffix.lower() == ".vox":
        return vox_write(model, path, ctx)
    obj = {"size": model["size"], "palette": [L.hex6(c) for c in model["palette"]],
           "voxels": [list(v) for v in model["voxels"]]}
    return L.write_json(obj, path, ctx)


def render_voxel(model, scale=4, view="iso", light=(1.0, 0.72, 0.5), outline_on=True):
    """Isometric (2:1) or orthographic voxel render, painter-sorted."""
    L = I()
    sx, sy, sz = model["size"]
    pal = model["palette"]
    vox = {}
    for x, y, z, ci in model["voxels"]:
        c = pal[(ci - 1) % len(pal)] if ci > 0 else pal[0]
        vox[(x, y, z)] = c
    u = max(2, scale + (scale % 2))  # even unit
    if view in ("front", "side", "top"):
        if view == "front":
            w, h = sx * u, sz * u
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            for (x, y, z), c in sorted(vox.items(), key=lambda kv: -kv[0][1]):
                d.rectangle((x * u, h - (z + 1) * u, (x + 1) * u - 1, h - z * u - 1), fill=c + (255,))
        elif view == "side":
            w, h = sy * u, sz * u
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            for (x, y, z), c in sorted(vox.items(), key=lambda kv: kv[0][0]):
                d.rectangle((y * u, h - (z + 1) * u, (y + 1) * u - 1, h - z * u - 1), fill=c + (255,))
        else:
            w, h = sx * u, sy * u
            img = Image.new("RGBA", (w, h))
            d = ImageDraw.Draw(img)
            for (x, y, z), c in sorted(vox.items(), key=lambda kv: kv[0][2]):
                d.rectangle((x * u, y * u, (x + 1) * u - 1, (y + 1) * u - 1), fill=c + (255,))
        return img
    # iso: sxpix=(x-y)*u, sypix=(x+y)*u/2 - z*u
    half = u // 2
    min_px = -(sy) * u
    max_px = sx * u
    width = max_px - min_px
    height = (sx + sy) * half + sz * u + u
    img = Image.new("RGBA", (width, height))
    d = ImageDraw.Draw(img)
    off_x = sy * u
    off_y = sz * u + half
    lt, ll, lr = light
    for (x, y, z), c in sorted(vox.items(), key=lambda kv: (kv[0][0] + kv[0][1] + kv[0][2], kv[0][2])):
        cx = off_x + (x - y) * u
        cy = off_y + (x + y) * half - z * u
        top = [(cx, cy - half), (cx + u, cy), (cx, cy + half), (cx - u, cy)]
        left = [(cx - u, cy), (cx, cy + half), (cx, cy + half + u), (cx - u, cy + u)]
        right = [(cx + u, cy), (cx, cy + half), (cx, cy + half + u), (cx + u, cy + u)]
        ct = tuple(min(255, int(v * lt)) for v in c) + (255,)
        cl = tuple(int(v * ll) for v in c) + (255,)
        cr = tuple(int(v * lr) for v in c) + (255,)
        d.polygon(left, fill=cl)
        d.polygon(right, fill=cr)
        d.polygon(top, fill=ct)
        if outline_on:
            d.line(top + [top[0]], fill=tuple(int(v * 0.4) for v in c) + (255,))
    return img


def rotate_model_z(model, quarter_turns):
    turns = quarter_turns % 4
    out = []
    for x, y, z, ci in model["voxels"]:
        cx, cy = x, y
        sx, sy = model["size"][0], model["size"][1]
        for _ in range(turns):
            cx, cy = sy - 1 - cy, cx
            sx, sy = sy, sx
        out.append((cx, cy, z, ci))
    size = list(model["size"])
    if turns % 2 == 1:
        size = [size[1], size[0], size[2]]
    return {"size": size, "palette": model["palette"], "voxels": out}


def cmd_voxel_build(a, ctx):
    L = I()
    pal_mod = P()
    if a.heightmap:
        img = L.load_image(a.heightmap, ctx.cfg)
        if img.width > 64 or img.height > 64:
            raise L.ToolError("limit-voxel", "Heightmap must be <= 64x64 for voxel builds.")
        q, pal = I().quantize(img, colors=a.colors)
        lum = q.convert("L")
        maxh = a.max_height
        voxels = []
        colors = sorted({c for c, _ in I().image_colors(q)}, key=L.luminance)
        pal_index = {c: i + 1 for i, c in enumerate(colors)}
        for y in range(img.height):
            for x in range(img.width):
                px = q.getpixel((x, y))
                if px[3] < 128:
                    continue
                hgt = max(1, round(lum.getpixel((x, y)) / 255 * maxh))
                for z in range(hgt):
                    voxels.append((x, y, z, pal_index[px[:3]]))
        model = {"size": [img.width, img.height, maxh], "palette": colors, "voxels": voxels}
    elif a.box:
        try:
            bx, by, bz = (int(v) for v in a.box.lower().split("x"))
        except Exception:
            raise L.ToolError("bad-arg", "--box must be WxDxH, e.g. 8x8x8.")
        if max(bx, by, bz) > 64 or min(bx, by, bz) < 1:
            raise L.ToolError("limit-voxel", "Box dimensions must be 1..64.")
        pal = [L.parse_color(a.color)[:3]]
        voxels = [(x, y, z, 1) for x in range(bx) for y in range(by) for z in range(bz)]
        model = {"size": [bx, by, bz], "palette": pal, "voxels": voxels}
    elif a.from_model:
        model = model_read(a.from_model)
    else:
        raise L.ToolError("bad-arg", "Provide --heightmap, --box or --from-model.")
    model_write(model, a.output, ctx)
    return {"output": a.output, "size": model["size"], "voxels": len(model["voxels"]),
            "palette_colors": len(model["palette"])}


def cmd_voxel_render(a, ctx):
    L = I()
    model = model_read(a.input)
    img = render_voxel(model, a.scale, a.view)
    L.check_dims(ctx.cfg, img.width, img.height)
    L.save_image(img, a.output, ctx)
    return {"output": a.output, "view": a.view, "size": list(img.size),
            "voxels": len(model["voxels"])}


def cmd_voxel_turntable(a, ctx):
    L = I()
    S_ = S()
    model = model_read(a.input)
    frames, names = [], []
    for q in range(4):
        rot = rotate_model_z(model, q)
        img = render_voxel(rot, a.scale, "iso")
        frames.append(img)
        names.append(f"angle-{q * 90:03d}")
    # normalize canvas sizes
    w = max(f.width for f in frames); h = max(f.height for f in frames)
    norm = []
    for f in frames:
        c = Image.new("RGBA", (w, h))
        c.alpha_composite(f, ((w - f.width) // 2, (h - f.height) // 2))
        norm.append(c)
    outdir = Path(a.outdir)
    for n, f in zip(names, norm):
        L.save_image(f, str(outdir / f"{n}.png"), ctx)
    sheet, meta = S_.pack_frames(norm, names, layout="horizontal", padding=2, cfg=ctx.cfg)
    spath = str(outdir / "turntable.png")
    L.save_image(sheet, spath, ctx)
    res = {"outdir": a.outdir, "angles": [0, 90, 180, 270], "sheet": spath}
    if a.animation:
        info = S_.encode_animation(norm, [1000 // a.fps] * 4, a.animation, ctx)
        res["animation"] = a.animation
        res.update(info)
    return res


def cmd_voxel_stack(a, ctx):
    L = I()
    model = model_read(a.input)
    sx, sy, sz = model["size"]
    pal = model["palette"]
    used_z = sorted({z for _, _, z, _ in model["voxels"]})
    if not used_z:
        raise L.ToolError("empty-model", "Model has no voxels.")
    layers = []
    for z in range(max(used_z) + 1):
        img = Image.new("RGBA", (sx, sy))
        for x, y, zz, ci in model["voxels"]:
            if zz == z:
                c = pal[(ci - 1) % len(pal)] if ci > 0 else pal[0]
                img.putpixel((x, y), c + (255,))
        layers.append(img)
    names = [f"z-{z:03d}" for z in range(len(layers))]
    sheet, meta = S().pack_frames(layers, names, layout="grid", cols=min(8, len(layers)),
                                  padding=1, cfg=ctx.cfg)
    L.save_image(sheet, a.output, ctx)
    meta["axis"] = "z (bottom to top)"
    meta["model_size"] = model["size"]
    L.write_json(meta, str(Path(a.output).with_suffix(".json")), ctx)
    return {"output": a.output, "layers": len(layers),
            "note": "Sprite stack: render layers bottom-to-top with vertical offset for pseudo-3D."}


def cmd_iso_primitive(a, ctx):
    L = I()
    color = L.parse_color(a.color)[:3]
    if a.kind == "tile":
        u = max(2, a.size + (a.size % 2))
        img = Image.new("RGBA", (2 * u, u))
        d = ImageDraw.Draw(img)
        d.polygon([(u, 0), (2 * u - 1, u // 2), (u, u - 1), (0, u // 2)], fill=color + (255,),
                  outline=tuple(int(v * 0.5) for v in color) + (255,))
    elif a.kind in ("cube", "slab"):
        model = {"size": [1, 1, 1], "palette": [color], "voxels": [(0, 0, 0, 1)]}
        img = render_voxel(model, a.size, "iso")
        if a.kind == "slab":
            img = img.crop((0, img.height // 3, img.width, img.height))
    else:
        raise L.ToolError("bad-arg", "kind must be tile|cube|slab.")
    L.save_image(img, a.output, ctx)
    return {"output": a.output, "kind": a.kind, "size": list(img.size),
            "projection": "2:1 isometric"}


# --------------------------------------------------------------- register ---

def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    p = add("text-render", "Render bitmap-font text with outline/shadow/effects", cmd_text_render)
    p.add_argument("text"); p.add_argument("output")
    p.add_argument("--font", default="5x7", choices=["5x7", "3x5"])
    p.add_argument("--color", default="#ffffff"); p.add_argument("--outline")
    p.add_argument("--shadow"); p.add_argument("--background", default="#00000000")
    p.add_argument("--spacing", type=int, default=1); p.add_argument("--scale", type=int, default=2)
    p.add_argument("--align", default="left", choices=["left", "center", "right"])
    p.add_argument("--effect", choices=["type", "wave", "bounce", "flicker", "rainbow"])
    p.add_argument("--frames", type=int, default=16); p.add_argument("--fps", type=int, default=12)
    p.add_argument("--seed", type=int, default=7); p.add_argument("--frames-dir")

    p = add("font-sheet", "Export glyph atlas + mapping JSON for engines", cmd_font_sheet)
    p.add_argument("output"); p.add_argument("--font", default="5x7", choices=["5x7", "3x5"])
    p.add_argument("--cols", type=int, default=16); p.add_argument("--color", default="#ffffff")
    p.add_argument("--spacing", type=int, default=1)

    p = add("sprite-generate", "Seeded symmetric creature/robot sprites with palette shading", cmd_sprite_generate)
    p.add_argument("outdir")
    p.add_argument("--kind", default="creature", choices=["creature", "robot", "bug", "ghost", "slime"])
    p.add_argument("--size", type=int, default=16); p.add_argument("--count", type=int, default=8)
    p.add_argument("--seed", type=int, default=7); p.add_argument("--palette"); p.add_argument("--sheet")

    p = add("icon-generate", "Procedural item icons (potion, coin, gem, sword, ...)", cmd_icon_generate)
    p.add_argument("outdir")
    p.add_argument("--kind", default="all", choices=["all"] + ICON_KINDS)
    p.add_argument("--size", type=int, default=16); p.add_argument("--variants", type=int, default=1)
    p.add_argument("--seed", type=int, default=7); p.add_argument("--palette"); p.add_argument("--sheet")

    p = add("pattern-generate", "Seamless procedural textures (brick, weave, water, ...)", cmd_pattern_generate)
    p.add_argument("output")
    p.add_argument("--kind", default="checker", choices=PATTERN_KINDS)
    p.add_argument("--size", type=int, default=32); p.add_argument("--scale", type=int, default=4)
    p.add_argument("--seed", type=int, default=7); p.add_argument("--palette"); p.add_argument("--preview")

    p = add("tileset-generate", "Autotile terrain tileset (16-mask or 47-blob) + metadata", cmd_tileset_generate)
    p.add_argument("output")
    p.add_argument("--terrain", default="grass", choices=sorted(TERRAINS))
    p.add_argument("--base-color", help="Custom terrain color instead of named terrain")
    p.add_argument("--mode", default="16", choices=["16", "47"])
    p.add_argument("--tile-size", type=int, default=16); p.add_argument("--variants", type=int, default=1)
    p.add_argument("--cols", type=int, default=0); p.add_argument("--padding", type=int, default=0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--preview-map", help="ASCII map file; '#' cells filled")
    p.add_argument("--fill-char", default="#")

    p = add("effect-generate", "Procedural effect animations (fire, rain, explosion, ...)", cmd_effect_generate)
    p.add_argument("outdir")
    p.add_argument("--kind", default="fire", choices=EFFECT_KINDS)
    p.add_argument("--size", type=int, default=48); p.add_argument("--frames", type=int, default=16)
    p.add_argument("--fps", type=int, default=12); p.add_argument("--seed", type=int, default=7)
    p.add_argument("--palette"); p.add_argument("--animation", help=".gif/.png/.webp output")
    p.add_argument("--sheet")

    p = add("motion-generate", "Animate a still sprite: idle, hover, jump, coin-spin, ...", cmd_motion_generate)
    p.add_argument("input")
    p.add_argument("--kind", default="idle-bob", choices=MOTION_KINDS)
    p.add_argument("--frames", type=int, default=12); p.add_argument("--amplitude", type=int, default=2)
    p.add_argument("--fps", type=int, default=12); p.add_argument("--seed", type=int, default=7)
    p.add_argument("--outdir"); p.add_argument("--animation"); p.add_argument("--sheet")

    p = add("ui-generate", "Pixel UI widgets: panel/button/bar/dialog/cursor/slot/heart", cmd_ui_generate)
    p.add_argument("kind", choices=["panel", "button", "bar", "dialog", "cursor", "slot", "heart"])
    p.add_argument("output")
    p.add_argument("--width", type=int, default=96); p.add_argument("--height", type=int, default=32)
    p.add_argument("--fill", default="#28304a"); p.add_argument("--border", default="#dce1f0")
    p.add_argument("--accent", default="#ffd24a"); p.add_argument("--style", default="bevel",
                   choices=["flat", "bevel", "rounded"])
    p.add_argument("--label"); p.add_argument("--value", type=float, default=0.7)
    p.add_argument("--segmented", action="store_true")
    p.add_argument("--cols", type=int); p.add_argument("--rows", type=int)

    p = add("voxel-build", "Build voxel model from heightmap/box/existing model (.json/.vox)", cmd_voxel_build)
    p.add_argument("output")
    p.add_argument("--heightmap"); p.add_argument("--max-height", type=int, default=8)
    p.add_argument("--colors", type=int, default=8)
    p.add_argument("--box", help="WxDxH solid box, e.g. 8x8x8"); p.add_argument("--color", default="#7a9b4e")
    p.add_argument("--from-model")

    p = add("voxel-render", "Render voxel model isometric/orthographic PNG", cmd_voxel_render)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--view", default="iso", choices=["iso", "front", "side", "top"])
    p.add_argument("--scale", type=int, default=6)

    p = add("voxel-turntable", "4-angle (0/90/180/270) iso turntable sheet + optional GIF", cmd_voxel_turntable)
    p.add_argument("input"); p.add_argument("outdir")
    p.add_argument("--scale", type=int, default=6); p.add_argument("--fps", type=int, default=4)
    p.add_argument("--animation")

    p = add("voxel-stack", "Export z-layers as sprite-stack sheet + metadata", cmd_voxel_stack)
    p.add_argument("input"); p.add_argument("output")

    p = add("iso-primitive", "2:1 isometric tile/cube/slab primitives", cmd_iso_primitive)
    p.add_argument("kind", choices=["tile", "cube", "slab"]); p.add_argument("output")
    p.add_argument("--size", type=int, default=16); p.add_argument("--color", default="#7a9b4e")
