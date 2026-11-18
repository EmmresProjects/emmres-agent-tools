"""imagekit render: deterministic scene compositor driven by asset-render-presets.json.

This engine builds real pixel output from a scene grammar (backdrop -> layers ->
treatment) with seeded reproducibility. It is a reference compositor for
composition, palette, hierarchy and delivery validation. It is NOT a diffusion
model and does not produce photoreal output; configure a provider
(tools-imagekit-providers.py) when photoreal synthesis is required.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageEnhance, ImageOps

import imagekit_core as core
import imagekit_plan as plan

LAYER_KINDS = (
    "gradient", "sweep", "flat", "sun", "moon", "ridge", "atmosphere", "particles", "bokeh",
    "petals", "fibers", "terrain", "roads", "parcels", "blocks", "ground", "table",
    "figure", "figure_scale", "creature", "floating_shapes", "abstract", "washes", "hatch",
    "halftone", "overprint", "light_shaft", "light_streaks", "diagonal_band", "diagram_nodes",
    "foliage", "focal_landform", "shadow", "product", "backlight_pool", "grid",
    "map_landmass", "map_mountains", "map_routes", "map_legend", "line_accents",
)


# --------------------------------------------------------------------------
# preset + palette resolution
# --------------------------------------------------------------------------


def presets_data(cfg):
    data = core.read_json(cfg.render_presets_path(), required=True)
    return data


def resolve_preset(cfg, preset=None, style=None, brief=None):
    data = presets_data(cfg)
    if preset:
        if preset not in data["presets"]:
            raise core.usage_error(
                f"Unknown --preset '{preset}'",
                "Available: " + ", ".join(sorted(data["presets"])) + ". Aliases: " + ", ".join(sorted(data["style_aliases"])))
        return preset, data["presets"][preset]
    key = style
    if key and key in data["style_aliases"]:
        key = data["style_aliases"][key]
    elif key and key in data["presets"]:
        pass
    else:
        key = None
    if not key and brief:
        candidate = brief.get("style")
        if candidate in data["style_aliases"]:
            key = data["style_aliases"][candidate]
        elif candidate in data["presets"]:
            key = candidate
        else:
            form = brief.get("subject_type")
            key = {"product": "photo-studio", "person": "lifestyle", "environment": "photo-environment",
                   "architecture": "architecture", "creature": "creature", "abstract": "editorial-flat",
                   "data": "technical-diagram"}.get(form, "photo-environment")
    if not key:
        key = "photo-environment"
    return key, data["presets"][key]


def working_palette(brief, preset, pp, explicit=None):
    if explicit:
        return [c if c.startswith("#") else "#" + c for c in explicit][:5]
    if brief and brief.get("palette"):
        pal = [c if c.startswith("#") else "#" + c for c in brief["palette"]][:5]
        while len(pal) < 5:
            pal.append(core.harmonize(pal, 5)[len(pal)])
        return pal
    hint = preset.get("palette_hint", "warm-neutral")
    return pp["palettes"].get(hint, pp["palettes"]["warm-neutral"])["colors"][:5]


class Scene:
    """Drawing context: palette tokens, rng, size helpers and the working layer."""

    def __init__(self, size, palette, rng, preset):
        self.w, self.h = int(size[0]), int(size[1])
        self.palette = palette
        self.rng = rng
        self.preset = preset
        self.img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 255))
        self.labels = []

    def color(self, token, fallback=(128, 128, 128)):
        if token is None:
            return fallback
        token = str(token)
        if token.lower().startswith("#"):
            return core.hex_to_rgb(token)
        if len(token) == 2 and token[0] in "pP" and token[1].isdigit():
            idx = int(token[1]) % max(1, len(self.palette))
            return core.hex_to_rgb(self.palette[idx])
        return fallback

    def px(self, fx):
        return fx * self.w

    def py(self, fy):
        return fy * self.h

    def scale(self, f):
        return f * min(self.w, self.h)

    def blend(self, layer_rgba, mask=None, opacity=1.0):
        if opacity < 1.0:
            alpha = layer_rgba.getchannel("A").point(lambda v: int(v * opacity))
            layer_rgba = layer_rgba.copy()
            layer_rgba.putalpha(alpha)
        if mask is not None:
            alpha = ImageChops.multiply(layer_rgba.getchannel("A"), mask)
            layer_rgba = layer_rgba.copy()
            layer_rgba.putalpha(alpha)
        self.img = Image.alpha_composite(self.img, layer_rgba)

    def tint(self, color, factor):
        return tuple(core.clamp(int(c * factor), 0, 255) for c in color[:3])

    def mix(self, a, b, t):
        return tuple(int(round(core.lerp(a[i], b[i], t))) for i in range(3))


# --------------------------------------------------------------------------
# layer primitives
# --------------------------------------------------------------------------


def _chaikin(pts, iterations=2, closed=True):
    """Corner-cutting subdivision: turns jittered polygons into organic outlines."""
    for _ in range(iterations):
        out = []
        n = len(pts)
        limit = n if closed else n - 1
        for i in range(limit):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        if not closed:
            out.append(pts[-1])
        pts = out
    return pts


def _poly_blob(rng, cx, cy, rx, ry, points=18, rough=0.22):
    pts = []
    for i in range(points):
        ang = 2 * math.pi * i / points
        jitter = 1 + (rng.random() - 0.5) * 2 * rough
        pts.append((cx + math.cos(ang) * rx * jitter, cy + math.sin(ang) * ry * jitter))
    return _chaikin(pts, 2, True)


def _quad_curve(p0, p1, p2, steps=28):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def _ridge_profile(rng, width, base_y, amp, roughness, spires=False):
    """Polygon points for a mountain ridge line across the full width."""
    steps = max(8, int(width / 12))
    values = []
    for i in range(steps + 1):
        t = i / steps
        v = (math.sin(t * math.pi * (1.3 + roughness)) * 0.5 +
             math.sin(t * math.pi * (3.1 + roughness * 2) + 1.7) * 0.28 +
             rng.uniform(-1, 1) * roughness * 0.5)
        if spires and rng.random() < 0.06:
            v += rng.uniform(0.4, 1.2)
        values.append(v)
    # smooth
    for _ in range(2):
        values = [values[0]] + [(values[i - 1] + values[i] * 2 + values[i + 1]) / 4
                                for i in range(1, len(values) - 1)] + [values[-1]]
    top = min(values)
    span = (max(values) - top) or 1
    pts = [(i / steps * width, base_y - (v - top) / span * amp) for i, v in enumerate(values)]
    return pts + [(width, base_y + amp * 3), (0, base_y + amp * 3)]


def layer_gradient(scene, layer):
    stops = layer.get("stops") or [[0.0, "p0"], [1.0, "p1"]]
    stops = [(float(pos), scene.color(tok)) for pos, tok in stops]
    img = core.gradient_image((scene.w, scene.h), stops, layer.get("direction", "vertical"),
                              layer.get("center", (0.5, 0.42)))
    scene.img = img.convert("RGBA")


def layer_flat(scene, layer):
    scene.img = Image.new("RGBA", (scene.w, scene.h), scene.color(layer.get("color", "p0")) + (255,))


def layer_sweep(scene, layer):
    top = scene.color(layer.get("top", "p0"))
    bottom = scene.color(layer.get("bottom", "p1"))
    img = core.gradient_image((scene.w, scene.h), [(0.0, top), (1.0, bottom)], "vertical")
    scene.img = img.convert("RGBA")
    draw = ImageDraw.Draw(scene.img)
    curve = float(layer.get("curve", 0.62))
    for i in range(30):
        t = i / 29
        y = scene.py(curve) + scene.py(0.12) * (t ** 1.8)
        shade = scene.tint(bottom, 0.94 - t * 0.10)
        draw.line([(0, y), (scene.w, y)], fill=shade + (255,), width=max(2, int(scene.py(0.02))))
    glow = Image.new("L", (scene.w, scene.h), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([scene.px(0.1), scene.py(0.02), scene.px(0.9), scene.py(0.62)], fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(scene.scale(0.1)))
    hi = Image.new("RGBA", (scene.w, scene.h), scene.tint(top, 1.12) + (0,))
    hi.putalpha(glow)
    scene.img = Image.alpha_composite(scene.img, hi)


def layer_sun(scene, layer):
    cx, cy = scene.px(layer.get("x", 0.7)), scene.py(layer.get("y", 0.32))
    r = scene.scale(layer.get("radius", 0.08))
    glow = float(layer.get("glow", 0.5))
    color = scene.color(layer.get("color", "p0"))
    halo = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for i in range(9, 0, -1):
        rr = r * (1 + i * 0.55)
        alpha = int(255 * glow * (0.10 / i * 3))
        hd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=scene.tint(color, 1.05) + (min(255, alpha),))
    halo = halo.filter(ImageFilter.GaussianBlur(max(2, r * 0.16)))
    scene.img = Image.alpha_composite(scene.img, halo)
    disc = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (245,))
    scene.img = Image.alpha_composite(scene.img, disc)


def layer_moon(scene, layer):
    cx, cy = scene.px(layer.get("x", 0.66)), scene.py(layer.get("y", 0.24))
    r = scene.scale(layer.get("radius", 0.07))
    color = scene.color(layer.get("color", "p4"))
    halo = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse([cx - r * 2.2, cy - r * 2.2, cx + r * 2.2, cy + r * 2.2], fill=scene.tint(color, 1.0) + (60,))
    scene.img = Image.alpha_composite(scene.img, halo.filter(ImageFilter.GaussianBlur(r * 0.5)))
    disc = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    dd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (250,))
    for _ in range(14):
        a = scene.rng.uniform(0, 2 * math.pi)
        d = scene.rng.uniform(0, r * 0.8)
        cr = scene.rng.uniform(r * 0.04, r * 0.14)
        px, py = cx + math.cos(a) * d, cy + math.sin(a) * d
        dd.ellipse([px - cr, py - cr, px + cr, py + cr], fill=scene.tint(color, 0.88) + (90,))
    scene.img = Image.alpha_composite(scene.img, disc)


def layer_ridge(scene, layer):
    rows = int(layer.get("rows", 3))
    colors = layer.get("colors") or ["p2", "p3", "p4"]
    heights = layer.get("height") or [0.6 + 0.1 * i for i in range(rows)]
    hazes = layer.get("haze") or [0.0 + 0.15 * i for i in range(rows)]
    roughness = float(layer.get("roughness", 0.6))
    for row in range(rows):
        color = scene.color(colors[row % len(colors)])
        base = scene.py(heights[row % len(heights)])
        haze = hazes[row % len(hazes)]
        atm = scene.color(layer.get("haze_color", "p1"))
        fill = scene.mix(color, atm, haze)
        pts = _ridge_profile(scene.rng, scene.w, base, scene.py(0.05 + 0.16 * (1 - row / max(1, rows))),
                             roughness, layer.get("spires", False))
        poly = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
        ImageDraw.Draw(poly).polygon(pts, fill=fill + (255,))
        if layer.get("wash") or haze > 0.1:
            poly = poly.filter(ImageFilter.GaussianBlur(0.6 + haze * 3))
        scene.img = Image.alpha_composite(scene.img, poly)


def layer_atmosphere(scene, layer):
    color = scene.color(layer.get("color", "p1"))
    y0, y1 = scene.py(layer.get("from", 0.4)), scene.py(layer.get("to", 0.8))
    h = max(2, int(y1 - y0))
    band = Image.new("L", (1, h), 0)
    for i in range(h):
        t = i / max(1, h - 1)
        band.putpixel((0, i), int(255 * (1 - abs(t - 0.5) * 2) ** 0.8))
    band = band.resize((scene.w, h), Image.Resampling.BILINEAR)
    layer_img = Image.new("RGBA", (scene.w, h), color + (255,))
    alpha = band.point(lambda v: int(v * float(layer.get("opacity", 0.2))))
    layer_img.putalpha(alpha)
    full = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    full.paste(layer_img, (0, int(y0)))
    scene.img = Image.alpha_composite(scene.img, full)


def layer_particles(scene, layer):
    count = int(layer.get("count", 40))
    color = scene.color(layer.get("color", "p0"))
    region = layer.get("region", "all")
    rmin, rmax = (layer.get("radius") or [1, 3])[:2]
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        y = scene.rng.uniform(0, 0.5) * scene.h if region == "upper" else scene.rng.uniform(0, scene.h)
        x = scene.rng.uniform(0, scene.w)
        r = scene.rng.uniform(rmin, rmax) * max(1, scene.w / 1024)
        a = int(255 * scene.rng.uniform(0.3, 1.0) * float(layer.get("opacity", 0.4)))
        d.ellipse([x - r, y - r, x + r, y + r], fill=scene.tint(color, 1.1) + (a,))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_bokeh(scene, layer):
    count = int(layer.get("count", 16))
    color = scene.color(layer.get("color", "p0"))
    rmin, rmax = (layer.get("radius") or [8, 40])[:2]
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        r = scene.rng.uniform(rmin, rmax) * max(0.5, scene.w / 1024)
        x, y = scene.rng.uniform(0, scene.w), scene.rng.uniform(0, scene.h)
        a = int(255 * scene.rng.uniform(0.2, 1.0) * float(layer.get("opacity", 0.18)))
        d.ellipse([x - r, y - r, x + r, y + r], fill=scene.tint(color, 1.05) + (a,))
    layer_img = layer_img.filter(ImageFilter.GaussianBlur(scene.scale(0.012)))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_petals(scene, layer):
    center = layer.get("center") or [0.46, 0.5]
    cx, cy = scene.px(center[0]), scene.py(center[1])
    radius = scene.scale(layer.get("radius", 0.4))
    color = scene.color(layer.get("color", "p0"))
    accent = scene.color(layer.get("accent", "p1"))
    count = int(layer.get("count", 7))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for i in range(count):
        ang = 2 * math.pi * i / count + scene.rng.uniform(-0.15, 0.15)
        pts = []
        for k in range(22):
            t = k / 21
            rr = radius * (0.25 + 0.75 * math.sin(math.pi * t) ** 0.7)
            spread = 0.42 * (1 - t * 0.6)
            a = ang + (t - 0.5) * spread * 2
            pts.append((cx + math.cos(a) * rr * 1.15, cy + math.sin(a) * rr))
        shade = scene.mix(color, accent, i / max(1, count))
        d.polygon(pts, fill=shade + (230,))
    core_dot = scene.scale(0.07)
    d.ellipse([cx - core_dot, cy - core_dot, cx + core_dot, cy + core_dot], fill=accent + (255,))
    layer_img = layer_img.filter(ImageFilter.GaussianBlur(scene.scale(0.004)))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_fibers(scene, layer):
    count = int(layer.get("count", 60))
    color = scene.color(layer.get("color", "p1"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        x, y = scene.rng.uniform(0, scene.w), scene.rng.uniform(0, scene.h)
        ln = scene.rng.uniform(0.02, 0.14) * scene.w
        ang = scene.rng.uniform(0, math.pi)
        a = int(255 * scene.rng.uniform(0.1, 1.0) * float(layer.get("opacity", 0.2)))
        d.line([(x, y), (x + math.cos(ang) * ln, y + math.sin(ang) * ln)],
               fill=scene.tint(color, 0.9) + (a,), width=max(1, int(scene.w / 900)))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_terrain(scene, layer):
    patches = int(layer.get("patches", 20))
    colors = [scene.color(c) for c in (layer.get("colors") or ["p1", "p2", "p3"])]
    roughness = float(layer.get("roughness", 0.7))
    base = Image.new("RGBA", (scene.w, scene.h), colors[0] + (255,))
    d = ImageDraw.Draw(base)
    for i in range(patches):
        cx, cy = scene.rng.uniform(-0.1, 1.1) * scene.w, scene.rng.uniform(-0.1, 1.1) * scene.h
        rx, ry = scene.rng.uniform(0.06, 0.28) * scene.w, scene.rng.uniform(0.06, 0.3) * scene.h
        d.polygon(_poly_blob(scene.rng, cx, cy, rx, ry, 22, roughness * 0.55),
                  fill=colors[1 + i % (len(colors) - 1)] + (255,))
    base = base.filter(ImageFilter.GaussianBlur(scene.scale(0.010)))
    scene.img = Image.alpha_composite(scene.img, base)


def layer_roads(scene, layer):
    count = int(layer.get("count", 4))
    color = scene.color(layer.get("color", "p0"))
    wmin, wmax = (layer.get("width") or [2, 5])[:2]
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        p0 = (scene.rng.uniform(-0.1, 0.4) * scene.w, scene.rng.uniform(0, 1) * scene.h)
        p2 = (scene.rng.uniform(0.6, 1.1) * scene.w, scene.rng.uniform(0, 1) * scene.h)
        p1 = ((p0[0] + p2[0]) / 2 + scene.rng.uniform(-0.2, 0.2) * scene.w,
              (p0[1] + p2[1]) / 2 + scene.rng.uniform(-0.2, 0.2) * scene.h)
        d.line(_quad_curve(p0, p1, p2), fill=scene.tint(color, 1.0) + (200,),
               width=max(1, int(scene.rng.uniform(wmin, wmax) * scene.w / 1024)), joint="curve")
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_parcels(scene, layer):
    count = int(layer.get("count", 14))
    color = scene.color(layer.get("color", "p1"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        x, y = scene.rng.uniform(0, 0.9) * scene.w, scene.rng.uniform(0, 0.9) * scene.h
        w, h = scene.rng.uniform(0.05, 0.14) * scene.w, scene.rng.uniform(0.04, 0.12) * scene.h
        skew = scene.rng.uniform(-0.03, 0.03) * scene.w
        shade = scene.tint(color, scene.rng.uniform(0.85, 1.12))
        pts = [(x, y), (x + w, y + skew * 0.4), (x + w + skew, y + h), (x + skew * 0.6, y + h)]
        d.polygon(pts, fill=shade + (int(255 * float(layer.get("opacity", 0.35))),))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_blocks(scene, layer):
    count = int(layer.get("count", 5))
    colors = [scene.color(c) for c in (layer.get("colors") or ["p3", "p4"])]
    baseline = scene.py(layer.get("baseline", 0.8))
    perspective = float(layer.get("perspective", 0.1))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    slot_w = scene.w / (count + 1)
    for i in range(count):
        cx = slot_w * (i + 1) + scene.rng.uniform(-0.2, 0.2) * slot_w
        w = slot_w * scene.rng.uniform(0.55, 1.15)
        h = scene.py(scene.rng.uniform(0.14, 0.5))
        top = baseline - h - scene.py(perspective * (cx / max(1, scene.w) - 0.5))
        shade = colors[i % len(colors)]
        d.rectangle([cx - w / 2, top, cx + w / 2, baseline], fill=shade + (255,))
        if layer.get("damaged"):
            for _ in range(4):
                dx = scene.rng.uniform(-0.4, 0.4) * w
                dw = scene.rng.uniform(0.05, 0.2) * w
                dy = scene.rng.uniform(0, 0.1) * h
                d.polygon([(cx + dx - dw, top + dy), (cx + dx + dw, top + dy),
                           (cx + dx, top + dy + scene.rng.uniform(0.03, 0.1) * h)],
                          fill=scene.tint(shade, 0.7) + (255,))
        if layer.get("windows"):
            glow = scene.color(layer.get("glow_color", "p0"))
            cols = max(2, int(w / (scene.w * 0.02)))
            rows = max(2, int(h / (scene.h * 0.035)))
            for r in range(rows):
                for c in range(cols):
                    if scene.rng.random() < 0.55:
                        continue
                    wx = cx - w / 2 + (c + 0.3) * (w / cols)
                    wy = top + (r + 0.3) * (h / rows)
                    ww = (w / cols) * 0.4
                    wh = (h / rows) * 0.4
                    d.rectangle([wx, wy, wx + ww, wy + wh], fill=scene.tint(glow, 1.1) + (190,))
        if layer.get("glow_color"):
            edge = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
            ImageDraw.Draw(edge).line([(cx - w / 2, top), (cx + w / 2, top)],
                                      fill=scene.color(layer["glow_color"]) + (200,), width=max(1, int(scene.w / 700)))
            layer_img = Image.alpha_composite(layer_img, edge.filter(ImageFilter.GaussianBlur(scene.scale(0.004))))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_ground(scene, layer):
    y = scene.py(layer.get("y", 0.78))
    color = scene.color(layer.get("color", "p2"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    d.rectangle([0, y, scene.w, scene.h], fill=color + (int(255 * float(layer.get("opacity", 0.85))),))
    for i in range(14):
        t = i / 13
        yy = y + (scene.h - y) * t
        d.line([(0, yy), (scene.w, yy)], fill=scene.tint(color, 0.95 - t * 0.12) + (90,),
               width=max(1, int(scene.h * 0.004)))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_table(scene, layer):
    y = scene.py(layer.get("y", 0.74))
    color = scene.color(layer.get("color", "p3"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    d.rectangle([0, y, scene.w, scene.h], fill=color + (255,))
    d.line([(0, y), (scene.w, y)], fill=scene.tint(color, 1.25) + (200,), width=max(2, int(scene.h * 0.004)))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def _draw_figure(layer_img, cx, base_y, height, color, pose="standing", stroke=0,
                 outline_only=False, rim=None, rim_color=None):
    """Stylised human silhouette built from primitives so proportions stay readable."""
    d = ImageDraw.Draw(layer_img)
    unit = height
    head_w = unit * 0.13
    head_h = unit * 0.16
    head_cy = base_y - unit + head_h * 0.55
    neck_top = head_cy + head_h * 0.42
    shoulder_y = base_y - unit * 0.76
    hip_y = base_y - unit * (0.44 if pose == "standing" else 0.42)
    shoulder_w = unit * 0.115
    waist_w = unit * 0.085
    fill = color + (255,)
    shapes = []
    torso = [(cx - shoulder_w, neck_top), (cx + shoulder_w, neck_top),
             (cx + waist_w * 1.15, hip_y), (cx - waist_w * 1.15, hip_y)]
    shapes.append(("polygon", torso))
    shapes.append(("ellipse", [cx - head_w, head_cy - head_h, cx + head_w, head_cy + head_h]))
    # arms
    for sgn in (-1, 1):
        ax0 = cx + sgn * shoulder_w * 0.85
        ax1 = cx + sgn * (shoulder_w * 0.72 + unit * 0.03)
        ay1 = hip_y + unit * (0.02 if pose == "standing" else 0.06)
        a = max(1, int(unit * 0.035))
        shapes.append(("line_arm", (ax0, shoulder_y + unit * 0.02, ax1, ay1, a)))
    # legs
    if pose == "seated":
        knee_y = hip_y + unit * 0.04
        for sgn in (-1, 1):
            lx = cx + sgn * waist_w * 0.62
            shapes.append(("rect_leg", [lx - unit * 0.07, knee_y, lx + unit * 0.07, knee_y + unit * 0.06]))
            shapes.append(("rect_leg", [lx + sgn * unit * 0.055, knee_y, lx + sgn * unit * 0.115, base_y - unit * 0.02]))
    else:
        for sgn in (-1, 1):
            lx = cx + sgn * waist_w * 0.62
            shapes.append(("rect_leg", [lx - unit * 0.042, hip_y, lx + unit * 0.042, base_y]))
    for kind, arg in shapes:
        if outline_only and stroke:
            if kind in ("polygon",):
                d.line(arg + [arg[0]], fill=fill, width=stroke, joint="curve")
            elif kind == "ellipse":
                d.ellipse(arg, outline=fill, width=stroke)
            elif kind in ("rect", "rect_leg"):
                box = [min(arg[0], arg[2]), min(arg[1], arg[3]), max(arg[0], arg[2]), max(arg[1], arg[3])]
                if box[2] - box[0] >= 2 and box[3] - box[1] >= 2:
                    d.rounded_rectangle(box, radius=int(unit * 0.02), outline=fill, width=stroke)
            elif kind == "line_arm":
                d.line([(arg[0], arg[1]), (arg[2], arg[3])], fill=fill, width=max(1, stroke // 2))
        else:
            if kind == "polygon":
                d.polygon(arg, fill=fill)
            elif kind == "ellipse":
                d.ellipse(arg, fill=fill)
            elif kind in ("rect", "rect_leg"):
                box = [min(arg[0], arg[2]), min(arg[1], arg[3]), max(arg[0], arg[2]), max(arg[1], arg[3])]
                if box[2] - box[0] >= 2 and box[3] - box[1] >= 2:
                    d.rounded_rectangle(box, radius=int(unit * 0.02), fill=fill)
            elif kind == "line_arm":
                d.line([(arg[0], arg[1]), (arg[2], arg[3])], fill=fill, width=arg[4])
    if rim and rim_color is not None:
        rim_layer = Image.new("RGBA", layer_img.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(rim_layer)
        rd.line([(cx - shoulder_w, neck_top), (cx - waist_w * 1.15, hip_y)],
                fill=rim_color + (210,), width=max(2, int(unit * 0.014)))
        rd.arc([cx - head_w, head_cy - head_h, cx + head_w, head_cy + head_h], 120, 260,
               fill=rim_color + (210,), width=max(2, int(unit * 0.012)))
        rd.line([(cx - shoulder_w * 0.85, shoulder_y + unit * 0.02), (cx - shoulder_w * 0.72 - unit * 0.03, hip_y)],
                fill=rim_color + (190,), width=max(2, int(unit * 0.011)))
        layer_img.alpha_composite(rim_layer.filter(ImageFilter.GaussianBlur(max(1, unit * 0.004))))
    return layer_img


def layer_figure(scene, layer):
    count = int(layer.get("count", 1))
    color = scene.color(layer.get("color", "p4"))
    h = scene.py(layer.get("h", 0.5))
    base_y = scene.py(layer.get("y", 0.5)) + h / 2
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    stroke = max(2, int(scene.scale(0.006))) if layer.get("outline_only") else 0
    rim = bool(layer.get("rim"))
    rim_color = scene.color(layer.get("rim_color", "p3")) if rim else None
    for i in range(count):
        cx = scene.px(layer.get("x", 0.5)) + (i - (count - 1) / 2) * h * 0.55
        _draw_figure(layer_img, cx, base_y, h, color, layer.get("pose", "standing"),
                     stroke=stroke, outline_only=bool(layer.get("outline_only")),
                     rim=rim, rim_color=rim_color)
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_figure_scale(scene, layer):
    count = int(layer.get("count", 2))
    color = scene.color(layer.get("color", "p4"))
    h = scene.py(layer.get("height", 0.1))
    base_y = scene.py(layer.get("y", 0.84))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    for _ in range(count):
        cx = scene.px(scene.rng.uniform(0.15, 0.85))
        _draw_figure(layer_img, cx, base_y, h, color)
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_creature(scene, layer):
    cx, cy = scene.px(layer.get("x", 0.5)), scene.py(layer.get("y", 0.58))
    scale = scene.scale(layer.get("scale", 0.4))
    color = scene.color(layer.get("color", "p0"))
    accent = scene.color(layer.get("accent", "p3"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    body_w, body_h = scale * 1.05, scale * 0.72
    d.ellipse([cx - body_w / 2, cy - body_h / 2, cx + body_w / 2, cy + body_h / 2], fill=color + (255,))
    head_r = scale * 0.26
    head_cx, head_cy = cx + body_w * 0.36, cy - body_h * 0.34
    d.ellipse([head_cx - head_r, head_cy - head_r, head_cx + head_r, head_cy + head_r], fill=color + (255,))
    if layer.get("horns"):
        for sgn in (-1, 1):
            d.polygon([(head_cx + sgn * head_r * 0.5, head_cy - head_r * 0.7),
                       (head_cx + sgn * head_r * 1.5, head_cy - head_r * 2.1),
                       (head_cx + sgn * head_r * 0.9, head_cy - head_r * 0.5)], fill=accent + (255,))
    if layer.get("wings"):
        for sgn in (-1, 1):
            wing = [(cx + sgn * body_w * 0.1, cy - body_h * 0.1),
                    (cx + sgn * body_w * 1.05, cy - body_h * 1.15),
                    (cx + sgn * body_w * 0.95, cy + body_h * 0.15),
                    (cx + sgn * body_w * 0.3, cy + body_h * 0.35)]
            d.polygon(wing, fill=scene.tint(accent, 0.9) + (170,))
    for i in range(4):
        lx = cx - body_w * 0.34 + i * body_w * 0.22
        d.line([(lx, cy + body_h * 0.3), (lx, cy + body_h * 0.62)], fill=color + (255,),
               width=max(3, int(scale * 0.05)))
    eye_r = max(2, head_r * 0.16)
    d.ellipse([head_cx + head_r * 0.1 - eye_r, head_cy - eye_r, head_cx + head_r * 0.1 + eye_r, head_cy + eye_r],
              fill=accent + (255,))
    if layer.get("cute"):
        d.ellipse([head_cx - head_r * 0.25, head_cy - head_r * 0.1, head_cx + head_r * 0.05, head_cy + head_r * 0.2],
                  fill=(255, 255, 255, 200))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_floating_shapes(scene, layer):
    count = int(layer.get("count", 5))
    colors = [scene.color(c) for c in (layer.get("colors") or ["p3", "p4", "p0"])]
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    for i in range(count):
        cx = scene.px(scene.rng.uniform(0.2, 0.85))
        cy = scene.py(scene.rng.uniform(0.2, 0.7))
        r = scene.scale(scene.rng.uniform(0.05, 0.16))
        color = colors[i % len(colors)]
        sphere = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sphere)
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (255,))
        shade = core.gradient_image((int(r * 2) + 2, int(r * 2) + 2),
                                    [(0.0, scene.tint(color, 1.3)), (1.0, scene.tint(color, 0.55))],
                                    "radial", (0.35, 0.3)).convert("RGBA")
        mask = Image.new("L", sphere.size, 0)
        ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        sphere.paste(Image.alpha_composite(Image.new("RGBA", sphere.size, (0, 0, 0, 0)),
                                           _place(shade, sphere.size, (int(cx - r), int(cy - r)))),
                     (0, 0), mask)
        shadow = core.soft_shadow((scene.w, scene.h), offset=(int(r * 0.2), int(r * 0.6)),
                                  blur=max(4, int(r * 0.5)), opacity=0.3, inset=0.0)
        sh_mask = Image.new("L", shadow.size, 0)
        ImageDraw.Draw(sh_mask).ellipse([cx - r * 1.1, cy + r * 0.8, cx + r * 1.1, cy + r * 1.5], fill=255)
        shadow.putalpha(ImageChops.multiply(shadow.getchannel("A"), sh_mask))
        layer_img = Image.alpha_composite(layer_img, shadow)
        layer_img = Image.alpha_composite(layer_img, sphere)
    scene.img = Image.alpha_composite(scene.img, layer_img)


def _place(layer, size, xy):
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(layer, xy)
    return canvas


def layer_abstract(scene, layer):
    count = int(layer.get("count", 4))
    colors = [scene.color(c) for c in (layer.get("colors") or ["p1", "p2"])]
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    stroke = max(2, int(scene.scale(float(layer.get("stroke", 0.01)))))
    for i in range(count):
        kind = scene.rng.choice(["circle", "arc", "rect", "blob"])
        cx = scene.px(scene.rng.uniform(0.1, 0.9))
        cy = scene.py(scene.rng.uniform(0.1, 0.9))
        r = scene.scale(scene.rng.uniform(0.08, 0.3))
        color = colors[i % len(colors)]
        alpha = int(255 * float(layer.get("opacity", 1.0)))
        if kind == "circle":
            box = [cx - r, cy - r, cx + r, cy + r]
            if layer.get("outline_only"):
                d.ellipse(box, outline=color + (alpha,), width=stroke)
            else:
                d.ellipse(box, fill=color + (alpha,))
        elif kind == "arc":
            box = [cx - r, cy - r, cx + r, cy + r]
            d.arc(box, start=scene.rng.uniform(0, 180), end=scene.rng.uniform(200, 355),
                  fill=color + (alpha,), width=stroke * 2)
        elif kind == "rect":
            d.rectangle([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7],
                        fill=None if layer.get("outline_only") else color + (alpha,),
                        outline=color + (alpha,) if layer.get("outline_only") else None, width=stroke)
        else:
            pts = _poly_blob(scene.rng, cx, cy, r, r * 0.8, 10, 0.28)
            if layer.get("outline_only"):
                d.line(pts + [pts[0]], fill=color + (alpha,), width=stroke, joint="curve")
            else:
                d.polygon(pts, fill=color + (alpha,))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_washes(scene, layer):
    count = int(layer.get("count", 5))
    colors = [scene.color(c) for c in (layer.get("colors") or ["p1", "p2"])]
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for i in range(count):
        cx = scene.px(scene.rng.uniform(0, 1))
        cy = scene.py(scene.rng.uniform(0, 1))
        r = scene.scale(scene.rng.uniform(0.12, 0.34))
        color = colors[i % len(colors)]
        d.polygon(_poly_blob(scene.rng, cx, cy, r, r * 0.75, 12, 0.3),
                  fill=color + (int(255 * float(layer.get("opacity", 0.4))),))
    layer_img = layer_img.filter(ImageFilter.GaussianBlur(scene.scale(0.02)))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_hatch(scene, layer):
    count = int(layer.get("count", 24))
    color = scene.color(layer.get("color", "p1"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        x0 = scene.rng.uniform(-0.2, 0.9) * scene.w
        y0 = scene.rng.uniform(0, 1) * scene.h
        ln = scene.rng.uniform(0.06, 0.3) * scene.w
        d.line([(x0, y0), (x0 + ln, y0 + ln * 0.55)],
               fill=color + (int(255 * float(layer.get("opacity", 0.5))),),
               width=max(1, int(scene.scale(0.003))))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_halftone(scene, layer):
    cell = max(3, int(layer.get("cell", 7) * scene.w / 1024))
    color = scene.color(layer.get("color", "p1"))
    small_w = max(8, scene.w // cell)
    small_h = max(8, scene.h // cell)
    lum = scene.img.convert("L").resize((small_w, small_h), Image.Resampling.BOX)
    dots = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    alpha_max = int(255 * float(layer.get("opacity", 0.5)))
    region = layer.get("region", "all")
    for y in range(small_h):
        for x in range(small_w):
            v = lum.getpixel((x, y))
            if region == "lower" and y < small_h * 0.45:
                continue
            if region == "upper" and y > small_h * 0.55:
                continue
            r = (1 - v / 255) * cell * 0.52
            if r < 0.4:
                continue
            cx, cy = x * cell + cell / 2, y * cell + cell / 2
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha_max,))
    scene.img = Image.alpha_composite(scene.img, dots)


def layer_overprint(scene, layer):
    color = scene.color(layer.get("color", "p2"))
    offset = layer.get("offset") or [2, 1]
    edges = scene.img.convert("L").filter(ImageFilter.FIND_EDGES)
    alpha = edges.point(lambda v: min(255, int(v * 1.4 * float(layer.get("opacity", 0.34)))))
    layer_img = Image.new("RGBA", (scene.w, scene.h), color + (255,))
    layer_img.putalpha(alpha)
    shifted = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    shifted.paste(layer_img, (int(offset[0] * scene.w / 1024), int(offset[1] * scene.h / 1024)))
    scene.img = Image.alpha_composite(scene.img, shifted)


def layer_light_shaft(scene, layer):
    color = scene.color(layer.get("color", "p3"))
    angle = math.radians(float(layer.get("angle", 28)))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    cx, cy = scene.w * 0.3, -scene.h * 0.1
    spread = scene.w * 0.18
    dx, dy = math.cos(angle), math.sin(angle)
    pts = [(cx + dx * -spread, cy + dy * -spread),
           (cx + dx * scene.h * 1.6 + spread * 1.4, cy + dy * scene.h * 1.6 + spread * 1.4),
           (cx + dx * scene.h * 1.6 + spread * 2.6, cy + dy * scene.h * 1.6 + spread * 2.6),
           (cx + dx * spread * 1.4, cy + dy * spread * 1.4)]
    d.polygon(pts, fill=color + (int(255 * float(layer.get("opacity", 0.28))),))
    scene.img = Image.alpha_composite(scene.img, layer_img.filter(ImageFilter.GaussianBlur(scene.scale(0.05))))


def layer_light_streaks(scene, layer):
    count = int(layer.get("count", 5))
    color = scene.color(layer.get("color", "p3"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        y = scene.rng.uniform(0.1, 0.9) * scene.h
        x0 = scene.rng.uniform(0, 0.5) * scene.w
        d.line([(x0, y), (x0 + scene.rng.uniform(0.2, 0.6) * scene.w, y)],
               fill=color + (int(255 * scene.rng.uniform(0.2, 0.7) * float(layer.get("opacity", 0.4))),),
               width=max(1, int(scene.scale(0.004))))
    scene.img = Image.alpha_composite(scene.img, layer_img.filter(ImageFilter.GaussianBlur(scene.scale(0.006))))


def layer_diagonal_band(scene, layer):
    color = scene.color(layer.get("color", "p1"))
    angle = math.radians(float(layer.get("angle", 24)))
    band = Image.new("RGBA", (int(scene.w * 1.6), int(scene.h * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    d.rectangle([0, band.height * 0.42, band.width, band.height * 0.58],
                fill=color + (int(255 * float(layer.get("opacity", 0.9))),))
    band = band.rotate(-math.degrees(angle), resample=Image.Resampling.BICUBIC, expand=False)
    canvas = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    canvas.paste(band, (int((scene.w - band.width) / 2), int((scene.h - band.height) / 2)))
    scene.img = Image.alpha_composite(scene.img, canvas)


def layer_grid(scene, layer):
    color = scene.color(layer.get("color", "p2"))
    horizon = scene.py(layer.get("horizon", 0.58))
    lines = int(layer.get("lines", 14))
    opacity = float(layer.get("opacity", 0.3))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    alpha = int(255 * opacity)
    if layer.get("flat_grid"):
        step = scene.w / max(2, lines)
        for i in range(lines + 1):
            x = i * step
            d.line([(x, 0), (x, scene.h)], fill=color + (alpha,), width=1)
            y = i * step
            d.line([(0, y), (scene.w, y)], fill=color + (alpha,), width=1)
    else:
        for i in range(lines + 1):
            t = i / lines
            d.line([(t * scene.w, scene.py(1.0)), (scene.w * 0.5 + (t - 0.5) * scene.w * 0.07, horizon)],
                   fill=color + (alpha,), width=1)
        for i in range(lines):
            yy = horizon + (scene.h - horizon) * (i / max(1, lines)) ** 2.1
            d.line([(0, yy), (scene.w, yy)], fill=color + (max(20, alpha - 30),), width=1)
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_shadow(scene, layer):
    shape = layer.get("shape", "ellipse")
    offset = layer.get("offset") or [0.02, 0.05]
    shadow = core.soft_shadow((scene.w, scene.h),
                              offset=(int(offset[0] * scene.w), int(offset[1] * scene.h)),
                              blur=max(2, int(float(layer.get("blur", 18)) * scene.w / 1024)),
                              opacity=float(layer.get("opacity", 0.35)), shape=shape,
                              inset=float(layer.get("inset", 0.1)))
    scene.img = Image.alpha_composite(scene.img, shadow)


def layer_backlight_pool(scene, layer):
    color = scene.color(layer.get("color", "p0"))
    cx, cy = scene.px(layer.get("x", 0.5)), scene.py(layer.get("y", 0.3))
    r = scene.scale(layer.get("radius", 0.45))
    pool = Image.new("L", (scene.w, scene.h), 0)
    ImageDraw.Draw(pool).ellipse([cx - r, cy - r * 0.8, cx + r, cy + r * 0.8], fill=255)
    pool = pool.filter(ImageFilter.GaussianBlur(r * 0.32))
    layer_img = Image.new("RGBA", (scene.w, scene.h), scene.tint(color, 1.08) + (255,))
    layer_img.putalpha(pool.point(lambda v: int(v * float(layer.get("opacity", 0.55)))))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_product(scene, layer):
    cx, cy = scene.px(layer.get("x", 0.5)), scene.py(layer.get("y", 0.55))
    w = scene.px(layer.get("w", 0.26))
    h = scene.py(layer.get("h", 0.46))
    color = scene.color(layer.get("color", "p3"))
    accent = scene.color(layer.get("accent", "p2"))
    form = layer.get("form", "bottle")
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    left, right = cx - w / 2, cx + w / 2
    top, bottom = cy - h / 2, cy + h / 2
    if form == "bottle":
        neck_w = w * 0.24
        shoulder = top + h * 0.30
        d.rounded_rectangle([left, shoulder, right, bottom], radius=int(w * 0.16), fill=color + (255,))
        d.rectangle([cx - neck_w / 2, top + h * 0.08, cx + neck_w / 2, shoulder],
                    fill=scene.tint(color, 0.97) + (255,))
        d.rounded_rectangle([cx - neck_w / 2 - w * 0.03, top, cx + neck_w / 2 + w * 0.03,
                             top + h * 0.10], radius=int(w * 0.025), fill=scene.tint(color, 0.72) + (255,))
        d.line([(cx - neck_w / 2, top + h * 0.12), (cx - neck_w / 2, shoulder)],
               fill=scene.tint(color, 1.12) + (200,), width=max(1, int(w * 0.012)))
    elif form == "box":
        front_top = top + h * 0.16
        d.polygon([(left, front_top), (left + w * 0.14, top), (right - w * 0.04, top),
                   (right - w * 0.18, front_top)], fill=scene.tint(color, 1.16) + (255,))
        d.polygon([(right - w * 0.18, front_top), (right - w * 0.04, top), (right, top + h * 0.08),
                   (right, bottom - h * 0.04), (right - w * 0.18, bottom)],
                  fill=scene.tint(color, 0.8) + (255,))
        d.rectangle([left, front_top, right - w * 0.18, bottom], fill=color + (255,))
        d.line([(left, front_top), (right - w * 0.18, front_top)], fill=scene.tint(color, 1.08) + (255,),
               width=max(1, int(h * 0.006)))
    elif form == "pouch":
        d.rounded_rectangle([left, top + h * 0.16, right, bottom], radius=int(w * 0.2), fill=color + (255,))
        d.rectangle([left + w * 0.06, top, right - w * 0.06, top + h * 0.2],
                    fill=scene.tint(color, 0.9) + (255,))
        for i in range(7):
            lx = left + w * (0.1 + 0.12 * i)
            d.line([(lx, top + h * 0.03), (lx, top + h * 0.17)], fill=scene.tint(color, 0.75) + (200,),
                   width=max(1, int(w * 0.008)))
    elif form == "tote":
        handle_w = max(4, int(w * 0.07))
        for hx0, hx1 in ((0.24, 0.46), (0.54, 0.76)):
            d.arc([left + w * hx0, top + h * 0.02, left + w * hx1, top + h * 0.40], 180, 360,
                  fill=scene.tint(color, 0.82) + (255,), width=handle_w)
            for lx in (hx0, hx1):
                d.line([(left + w * lx, top + h * 0.2), (left + w * lx, top + h * 0.36)],
                       fill=scene.tint(color, 0.82) + (255,), width=handle_w)
        d.polygon([(left, top + h * 0.32), (right, top + h * 0.32), (right, bottom),
                   (left, bottom)], fill=color + (255,))
        d.line([(left, top + h * 0.3), (right, top + h * 0.3)], fill=scene.tint(color, 1.1) + (255,),
               width=max(1, int(h * 0.008)))
    elif form == "phone":
        d.rounded_rectangle([left, top, right, bottom], radius=int(w * 0.14),
                            fill=scene.tint(color, 0.35) + (255,))
        d.rounded_rectangle([left + w * 0.05, top + h * 0.05, right - w * 0.05, bottom - h * 0.05],
                            radius=int(w * 0.1), fill=scene.tint(color, 1.05) + (255,))
    elif form == "poster":
        d.rectangle([left, top, right, bottom], fill=scene.tint(color, 1.02) + (255,))
        d.rectangle([left, top, right, bottom], outline=scene.tint(color, 0.8) + (255,),
                    width=max(1, int(w * 0.006)))
    else:  # cup / jar
        d.rounded_rectangle([left, top + h * 0.16, right, bottom], radius=int(w * 0.18), fill=color + (255,))
        d.ellipse([left, top + h * 0.1, right, top + h * 0.26], fill=scene.tint(color, 1.15) + (255,))
        d.ellipse([left + w * 0.14, top + h * 0.16, right - w * 0.14, top + h * 0.24],
                  fill=scene.tint(color, 0.7) + (255,))
    # cylindrical shading + specular
    x0, y0 = int(left), int(top)
    x1, y1 = max(x0 + 2, int(right)), max(y0 + 2, int(bottom))
    region = layer_img.crop((x0, y0, x1, y1))
    mask = region.getchannel("A")
    shade = core.gradient_image(region.size,
                                [(0.0, scene.tint(color, 0.72)), (0.32, scene.tint(color, 1.2)),
                                 (0.55, color), (1.0, scene.tint(color, 0.6))], "horizontal").convert("RGBA")
    region.paste(shade, (0, 0), mask)
    layer_img.paste(region, (x0, y0))
    if layer.get("label"):
        label_h = h * 0.26
        label_y = top + h * (0.45 if form != "cup" else 0.52)
        d.rectangle([left + w * 0.1, label_y, right - w * 0.1, label_y + label_h], fill=accent + (235,))
        d.line([(left + w * 0.16, label_y + label_h * 0.35), (right - w * 0.16, label_y + label_h * 0.35)],
               fill=scene.tint(accent, 1.35) + (220,), width=max(1, int(w * 0.02)))
        d.line([(left + w * 0.16, label_y + label_h * 0.62), (right - w * 0.28, label_y + label_h * 0.62)],
               fill=scene.tint(accent, 1.2) + (180,), width=max(1, int(w * 0.015)))
    spec = Image.new("RGBA", (scene.w, scene.h), (255, 255, 255, 0))
    sd = ImageDraw.Draw(spec)
    sd.rounded_rectangle([left + w * 0.16, top + h * 0.12, left + w * 0.26, bottom - h * 0.14],
                         radius=int(w * 0.04), fill=(255, 255, 255, 70))
    layer_img = Image.alpha_composite(layer_img, spec.filter(ImageFilter.GaussianBlur(w * 0.03)))
    if layer.get("contact_shadow"):
        shadow = core.soft_shadow((scene.w, scene.h), offset=(0, int(h * 0.02)),
                                  blur=max(4, int(w * 0.12)), opacity=0.3, inset=0.0)
        smask = Image.new("L", (scene.w, scene.h), 0)
        ImageDraw.Draw(smask).ellipse([left - w * 0.2, bottom - h * 0.03, right + w * 0.2, bottom + h * 0.07], fill=255)
        shadow.putalpha(ImageChops.multiply(shadow.getchannel("A"), smask))
        layer_img = Image.alpha_composite(shadow, layer_img)
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_foliage(scene, layer):
    count = int(layer.get("count", 12))
    color = scene.color(layer.get("color", "p2"))
    accent = scene.color(layer.get("accent", "p3"))
    baseline = scene.py(layer.get("baseline", 0.86))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for i in range(count):
        x = scene.px(scene.rng.uniform(0, 1))
        h = scene.py(scene.rng.uniform(0.06, 0.2))
        d.line([(x, baseline), (x, baseline - h)], fill=scene.tint(accent, 0.7) + (255,),
               width=max(2, int(scene.scale(0.005))))
        for k in range(3):
            r = scene.scale(scene.rng.uniform(0.02, 0.05))
            cy = baseline - h * (0.6 + k * 0.2)
            d.ellipse([x - r + (k - 1) * r * 0.6, cy - r, x + r + (k - 1) * r * 0.6, cy + r],
                      fill=color + (255,))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_focal_landform(scene, layer):
    color = scene.color(layer.get("color", "p2"))
    cx, cy = scene.px(layer.get("x", 0.34)), scene.py(layer.get("y", 0.72))
    scale = scene.scale(layer.get("scale", 0.3))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    pts = _poly_blob(scene.rng, cx, cy, scale, scale * 0.75, 11, 0.42)
    d.polygon(pts, fill=color + (255,))
    d.polygon([(cx - scale * 0.9, cy + scale * 0.8), (cx + scale * 0.9, cy + scale * 0.8),
               (cx + scale * 1.1, scene.h), (cx - scale * 1.1, scene.h)], fill=scene.tint(color, 0.9) + (255,))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_map_landmass(scene, layer):
    patches = int(layer.get("patches", 4))
    fill = scene.color(layer.get("color", "p1"))
    coast = scene.color(layer.get("coast", "p3"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for i in range(patches):
        cx = scene.px(scene.rng.uniform(0.2, 0.8))
        cy = scene.py(scene.rng.uniform(0.2, 0.8))
        r = scene.scale(scene.rng.uniform(0.14, 0.32))
        pts = _poly_blob(scene.rng, cx, cy, r * 1.3, r, 26, float(layer.get("roughness", 0.5)) * 0.5)
        land = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(land)
        ld.polygon(pts, fill=fill + (255,))
        land = land.filter(ImageFilter.GaussianBlur(scene.scale(0.003)))
        layer_img = Image.alpha_composite(layer_img, land)
        d = ImageDraw.Draw(layer_img)
        d.line(pts + [pts[0]], fill=coast + (235,), width=max(2, int(scene.scale(0.0035))), joint="curve")
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_map_mountains(scene, layer):
    count = int(layer.get("count", 12))
    color = scene.color(layer.get("color", "p3"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        cx = scene.px(scene.rng.uniform(0.15, 0.85))
        cy = scene.py(scene.rng.uniform(0.2, 0.8))
        s = scene.scale(scene.rng.uniform(0.015, 0.035))
        d.polygon([(cx - s, cy + s * 0.6), (cx, cy - s), (cx + s, cy + s * 0.6)],
                  outline=color + (220,), fill=scene.tint(color, 1.4) + (120,))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_map_routes(scene, layer):
    count = int(layer.get("count", 3))
    color = scene.color(layer.get("color", "p3"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        p0 = (scene.px(scene.rng.uniform(0.15, 0.35)), scene.py(scene.rng.uniform(0.25, 0.75)))
        p2 = (scene.px(scene.rng.uniform(0.65, 0.85)), scene.py(scene.rng.uniform(0.25, 0.75)))
        p1 = ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2 + scene.py(scene.rng.uniform(-0.15, 0.15)))
        pts = _quad_curve(p0, p1, p2, 40)
        for i in range(0, len(pts) - 1, 2):
            d.line([pts[i], pts[i + 1]], fill=color + (230,), width=max(2, int(scene.scale(0.004))))
        for pt in (p0, p2):
            r = scene.scale(0.012)
            d.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r], fill=color + (255,))
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_map_legend(scene, layer):
    color = scene.color(layer.get("color", "p3"))
    w, h = scene.px(0.22), scene.py(0.16)
    x0, y0 = scene.px(0.72), scene.py(0.76)
    layer_img = Image.new("RGBA", (scene.w, scene.h), (255, 255, 255, 0))
    d = ImageDraw.Draw(layer_img)
    d.rectangle([x0, y0, x0 + w, y0 + h], fill=(255, 255, 255, 215), outline=color + (255,),
                width=max(1, int(scene.scale(0.002))))
    font = core.load_font(max(8, int(h * 0.14)), bold=True)
    d.text((x0 + w * 0.08, y0 + h * 0.1), "LEGEND", font=font, fill=color + (255,))
    font2 = core.load_font(max(7, int(h * 0.1)))
    for i, label in enumerate(("settlement", "route", "highland")):
        yy = y0 + h * (0.36 + i * 0.2)
        d.line([(x0 + w * 0.08, yy), (x0 + w * 0.2, yy)], fill=color + (255,), width=max(1, int(scene.scale(0.003))))
        d.text((x0 + w * 0.26, yy - h * 0.06), label, font=font2, fill=color + (230,))
    scene.img = Image.alpha_composite(scene.img, layer_img)
    scene.labels.append("legend")


def layer_diagram_nodes(scene, layer):
    count = int(layer.get("count", 6))
    color = scene.color(layer.get("color", "p3"))
    accent = scene.color(layer.get("accent", "p1"))
    labels = layer.get("labels") or []
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    nodes = []
    cols = min(3, count)
    rows = math.ceil(count / cols)
    box_w, box_h = scene.px(0.2), scene.py(0.11)
    for i in range(count):
        col, row = i % cols, i // cols
        cx = scene.px(0.5) + (col - (cols - 1) / 2) * scene.px(0.28)
        cy = scene.py(0.3) + row * scene.py(0.24)
        nodes.append((cx, cy))
    stroke = max(2, int(scene.scale(0.004)))
    arrow = [(scene.scale(0.012), 0), (-scene.scale(0.008), -scene.scale(0.008)),
             (-scene.scale(0.008), scene.scale(0.008))]

    def arrowhead(tip):
        d.polygon([(tip[0] + dx, tip[1] + dy) for dx, dy in arrow], fill=color + (230,))

    for i in range(len(nodes) - 1):
        ax, ay = nodes[i]
        bx, by = nodes[i + 1]
        if abs(ay - by) < 1:                       # same row: straight right
            start, end = (ax + box_w / 2, ay), (bx - box_w / 2, by)
            d.line([start, end], fill=color + (200,), width=stroke)
            arrowhead(end)
        else:                                       # wrap to the next row: elbow route
            mid_y = (ay + by) / 2
            path = [(ax, ay + box_h / 2), (ax, mid_y), (bx, mid_y), (bx, by - box_h / 2)]
            d.line(path, fill=color + (190,), width=stroke)
            arrowhead(path[-1])
    font = core.load_font(max(9, int(box_h * 0.3)), bold=True)
    for i, (cx, cy) in enumerate(nodes):
        d.rounded_rectangle([cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2],
                            radius=int(box_h * 0.18), fill=(255, 255, 255, 255),
                            outline=color + (255,), width=stroke)
        label = labels[i] if i < len(labels) else f"Step {i + 1}"
        tw = d.textlength(label, font=font)
        d.text((cx - tw / 2, cy - box_h * 0.16), label, font=font, fill=color + (255,))
        scene.labels.append(label)
    scene.img = Image.alpha_composite(scene.img, layer_img)


def layer_line_accents(scene, layer):
    count = int(layer.get("count", 8))
    color = scene.color(layer.get("color", "p4"))
    layer_img = Image.new("RGBA", (scene.w, scene.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer_img)
    for _ in range(count):
        x0 = scene.px(scene.rng.uniform(0, 1))
        y0 = scene.py(scene.rng.uniform(0, 1))
        ln = scene.scale(scene.rng.uniform(0.04, 0.2))
        ang = scene.rng.choice([0, math.pi / 2, math.pi / 4])
        d.line([(x0, y0), (x0 + math.cos(ang) * ln, y0 + math.sin(ang) * ln)],
               fill=color + (int(255 * float(layer.get("opacity", 0.6))),),
               width=max(2, int(scene.scale(0.004))))
    scene.img = Image.alpha_composite(scene.img, layer_img)


LAYER_FUNCS = {
    "gradient": layer_gradient, "flat": layer_flat, "sweep": layer_sweep, "sun": layer_sun,
    "moon": layer_moon, "ridge": layer_ridge, "atmosphere": layer_atmosphere,
    "particles": layer_particles, "bokeh": layer_bokeh, "petals": layer_petals,
    "fibers": layer_fibers, "terrain": layer_terrain, "roads": layer_roads,
    "parcels": layer_parcels, "blocks": layer_blocks, "ground": layer_ground,
    "table": layer_table, "figure": layer_figure, "figure_scale": layer_figure_scale,
    "creature": layer_creature, "floating_shapes": layer_floating_shapes,
    "abstract": layer_abstract, "washes": layer_washes, "hatch": layer_hatch,
    "halftone": layer_halftone, "overprint": layer_overprint, "light_shaft": layer_light_shaft,
    "light_streaks": layer_light_streaks, "diagonal_band": layer_diagonal_band,
    "diagram_nodes": layer_diagram_nodes, "foliage": layer_foliage,
    "focal_landform": layer_focal_landform, "shadow": layer_shadow,
    "product": layer_product, "backlight_pool": layer_backlight_pool, "grid": layer_grid,
    "map_landmass": layer_map_landmass, "map_mountains": layer_map_mountains,
    "map_routes": layer_map_routes, "map_legend": layer_map_legend,
    "line_accents": layer_line_accents,
}


# --------------------------------------------------------------------------
# treatments
# --------------------------------------------------------------------------


def apply_treatment(scene, treatment, seed_salt):
    img = scene.img
    t = treatment or {}
    if t.get("painterly"):
        blur = img.filter(ImageFilter.GaussianBlur(scene.scale(0.004)))
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        edge_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        edge_layer.putalpha(edges.point(lambda v: int(v * 0.35)))
        img = Image.alpha_composite(blur, edge_layer)
    if t.get("soft"):
        img = Image.alpha_composite(img, img.filter(ImageFilter.GaussianBlur(scene.scale(0.003))))
    if t.get("wet_edges"):
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(1))
        dark = Image.new("RGBA", img.size, (40, 36, 30, 0))
        dark.putalpha(edges.point(lambda v: int(v * 0.4)))
        img = Image.alpha_composite(img, dark)
    if t.get("bloom"):
        lum = img.convert("L")
        hi = lum.point(lambda v: max(0, int((v - 200) * 3.2)))
        glow = Image.new("RGBA", img.size, (255, 250, 235, 0))
        glow.putalpha(hi.point(lambda v: int(v * float(t["bloom"]) * 1.6)))
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(scene.scale(0.02))))
    if t.get("blur_edges"):
        blurred = img.filter(ImageFilter.GaussianBlur(scene.scale(float(t["blur_edges"]) / 1000)))
        mask = core.vignette_mask(img.size, strength=1.0, softness=0.8)
        img = Image.composite(blurred, img, mask)
    if t.get("stroke"):
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 30 else 0)
        canvas = Image.new("RGBA", img.size, scene.color("p0") + (255,))
        ink = Image.new("RGBA", img.size, scene.color("p1") + (255,))
        canvas.paste(ink, (0, 0), edges)
        img = canvas
    if t.get("posterize"):
        levels = int(t["posterize"])
        rgb = ImageOps.posterize(img.convert("RGB"), max(1, min(8, levels)))
        img = rgb.convert("RGBA")
    if t.get("contrast", 1.0) != 1.0:
        img = ImageEnhance.Contrast(img.convert("RGB")).enhance(float(t["contrast"])).convert("RGBA")
    if t.get("saturation", 1.0) != 1.0:
        img = ImageEnhance.Color(img.convert("RGB")).enhance(float(t["saturation"])).convert("RGBA")
    if t.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(float(t["blur"])))
    if t.get("vignette"):
        mask = core.vignette_mask(img.size, strength=float(t["vignette"]), softness=1.0)
        dark = Image.new("RGBA", img.size, (0, 0, 0, 255))
        dark.putalpha(mask)
        img = Image.alpha_composite(img, dark)
    if t.get("grain"):
        img = core.apply_grain(img, float(t["grain"]), core.seeded_rng(seed_salt, "grain"))
    return img


# --------------------------------------------------------------------------
# scene assembly + op
# --------------------------------------------------------------------------


def render_scene(size, preset, palette, seed, label_overrides=None, override_layers=None):
    rng = core.seeded_rng(seed, preset.get("label", "scene"))
    scene = Scene(size, palette, rng, preset)
    backdrop = preset.get("backdrop") or {"type": "gradient", "stops": [[0.0, "p0"], [1.0, "p1"]]}
    kind = backdrop.get("type", "gradient")
    if kind == "sweep":
        layer_sweep(scene, backdrop)
    elif kind == "flat":
        layer_flat(scene, backdrop)
    elif kind == "paper":
        layer_flat(scene, {"color": backdrop.get("color", "p0")})
        fibers = {"kind": "fibers", "count": 120, "color": backdrop.get("tint", "p1"), "opacity": 0.16}
        layer_fibers(scene, fibers)
        texture = core.noise_image((scene.w, scene.h), core.seeded_rng(seed, "paper"), scale=40, octaves=3, contrast=0.6)
        paper = Image.new("RGBA", (scene.w, scene.h), scene.color(backdrop.get("tint", "p1")) + (255,))
        paper.putalpha(texture.point(lambda v: int(abs(v - 128) * 0.5)))
        scene.img = Image.alpha_composite(scene.img, paper)
    elif kind == "radial":
        layer_gradient(scene, {"stops": backdrop.get("stops"), "direction": "radial",
                               "center": tuple(backdrop.get("center", (0.5, 0.42)))})
    else:
        layer_gradient(scene, {"stops": backdrop.get("stops"), "direction": backdrop.get("direction", "vertical")})

    layers = override_layers if override_layers is not None else preset.get("layers", [])
    for layer in layers:
        kind = layer.get("kind")
        func = LAYER_FUNCS.get(kind)
        if func is None:
            raise core.usage_error(f"Preset layer kind '{kind}' is not implemented",
                                   "Supported kinds: " + ", ".join(LAYER_KINDS))
        if kind == "diagram_nodes" and label_overrides:
            layer = dict(layer, labels=label_overrides)
        func(scene, layer)
    out = apply_treatment(scene, preset.get("treatment"), f"{seed}:{preset.get('label')}")
    return out, scene


def provenance_payload(op, brief, preset_key, preset, seed, size, palette, prompt_text, disclosure):
    return {
        "tool": "image-creation-editing (imagekit)",
        "tool_version": core.VERSION,
        "created_at": core.now_iso(),
        "operation": op,
        "engine": "deterministic-reference-compositor",
        "notice": "Rendered by a deterministic scene compositor from a declared recipe. Not a diffusion or photographic model.",
        "prompt": prompt_text,
        "seed": seed,
        "preset": preset_key,
        "preset_label": preset.get("label"),
        "size": list(size),
        "aspect": core.aspect_label(*size),
        "palette": palette,
        "recipe": {"backdrop": preset.get("backdrop"), "layers": preset.get("layers"),
                   "treatment": preset.get("treatment")},
        "brief_project": (brief or {}).get("project"),
        "brief_title": (brief or {}).get("title"),
        "disclosure": disclosure,
    }


def op_render(ctx, a):
    cfg = ctx.config
    pp = plan.prompt_presets(cfg)
    brief = None
    if getattr(a, "brief", None) or getattr(a, "input", None):
        brief = plan.load_brief(a, ctx)
        core.enforce_policy(ctx, json.dumps(brief, ensure_ascii=False), "render-request")
    preset_key, preset = resolve_preset(cfg, getattr(a, "preset", None), getattr(a, "style", None), brief)

    size_arg = getattr(a, "size", None) or (brief or {}).get("dimensions") or cfg.render_cfg.get("default_size", [1024, 1024])
    size = core.check_size(cfg, core.parse_size(size_arg))
    palette_arg = [c.strip() for c in str(a.palette).split(",")] if getattr(a, "palette", None) else None
    palette = working_palette(brief, preset, pp, palette_arg)
    seed = ctx.seed if ctx.seed is not None else (getattr(a, "seed", None) if getattr(a, "seed", None) is not None else 20260101)
    count = int(getattr(a, "count", 1) or 1)
    if count < 1 or count > min(24, cfg.limit("max_batch_items", 500)):
        raise core.usage_error("--count must be between 1 and 24 for rendering")
    fmt = (getattr(a, "format", None) or (brief or {}).get("format") or "png").lower().lstrip(".")
    if fmt not in core.WRITE_FORMATS:
        raise core.usage_error(f"Unsupported --format '{fmt}'", "Supported: " + ", ".join(sorted(core.WRITE_FORMATS)))
    quality = int(getattr(a, "quality", 0) or cfg.render_cfg.get("quality", 92))
    project = (brief or {}).get("project") or getattr(a, "project", None) or "render"
    base_name = getattr(a, "name", None) or preset_key
    labels = [s.strip() for s in str(getattr(a, "labels", "") or "").split(",") if s.strip()] or None

    policy = None
    if getattr(a, "no_disclosure", False):
        policy = {"disclosure": ""}
    disclosure = pp["accessibility"].get("disclosure", "") if policy else ""
    disclosure_text = "" if getattr(a, "no_disclosure", False) else (
        "Synthetic image generated with AI-assisted tooling; not a photograph of a real event or person.")

    prompt_text = ""
    if brief:
        built = plan.build_prompt(brief, cfg)
        prompt_text = built["positive"]
    elif getattr(a, "prompt_text", None):
        prompt_text = a.prompt_text

    outputs, variants, metrics = [], [], []
    for i in range(count):
        variant_seed = seed if count == 1 else core.derive_seed(seed, f"variant-{i}")
        img, scene = render_scene(size, preset, palette, variant_seed, labels)
        name = core.build_filename(
            {"project": project, "content": base_name, "variant": (f"r{i + 1:02d}" if count > 1 else None),
             "dimensions": size, "version": int(getattr(a, "version", 1) or 1),
             "status": getattr(a, "status", None) or "draft"}, fmt, cfg.naming)
        path = ctx.out_path(name, "." + fmt)
        meta = provenance_payload("render", brief, preset_key, preset, variant_seed, size, palette,
                                  prompt_text, disclosure_text)
        text_meta = {
            "Title": (brief or {}).get("title") or base_name,
            "Software": f"imagekit {core.VERSION}",
            "Description": disclosure_text,
            "imagekit:preset": preset_key,
            "imagekit:seed": str(variant_seed),
        }
        if fmt == "png":
            core.save_image(ctx, img, path, fmt, quality=quality, dpi=(core.dpi_default(cfg),) * 2,
                            pnginfo=core.build_pnginfo(text_meta))
        else:
            core.save_image(ctx, img, path, fmt, quality=quality, dpi=(core.dpi_default(cfg),) * 2,
                            exif=core.build_exif({"ImageDescription": text_meta["Description"],
                                                  "Software": text_meta["Software"],
                                                  "UserComment": json.dumps({"preset": preset_key, "seed": variant_seed})}))
        if not ctx.dry_run:
            core.write_json(path.with_suffix(path.suffix + ".provenance.json"), meta)
        quick = {
            "file": core.rel_posix(path, ctx.out_root),
            "size": f"{img.width}x{img.height}",
            "bytes": core.file_size(path),
            "sharpness": core.laplacian_variance(img),
            "contrast_rms": core.rms_contrast(img),
            "palette": core.dominant_colors(img, 4),
            "seed": variant_seed,
            "provenance_sidecar": core.rel_posix(path.with_suffix(path.suffix + ".provenance.json"), ctx.out_root),
        }
        quick["qa"] = quick_quality(quick["sharpness"], quick["contrast_rms"], img, cfg)
        variants.append(quick)
        outputs.append(path)
        outputs.append(path.with_suffix(path.suffix + ".provenance.json"))
        if ctx.dry_run:
            break

    manifest = {
        "operation": "render",
        "preset": preset_key,
        "preset_label": preset.get("label"),
        "engine": "deterministic-reference-compositor",
        "engine_notice": "Reference compositor output. Photoreal synthesis requires a configured provider.",
        "size": list(size),
        "aspect": core.aspect_label(*size),
        "palette": palette,
        "seed": seed,
        "count": count,
        "format": fmt,
        "brief_project": (brief or {}).get("project"),
        "variants": variants,
        "negative_space": preset.get("negative_space"),
        "safe_text_zone": preset.get("safe_text"),
        "next_steps": ["review composition and palette", "run 'qa' for gates",
                       "add typography with 'layout' when text is needed",
                       "attach disclosure before publication"],
        "created_at": core.now_iso(),
    }
    name = core.slugify(f"{project}-{base_name}-manifest")
    outs = outputs + [core.emit_json(ctx, name, manifest)]
    ctx.audit("render.completed", preset=preset_key, count=count, seed=seed)
    return {"data": {k: manifest[k] for k in ("operation", "preset", "preset_label", "size", "aspect",
                                              "palette", "seed", "count", "format", "variants")},
            "outputs": outs,
            "warnings": ([manifest["engine_notice"]] if count else [])}


def quick_quality(sharpness, contrast_rms, img, cfg):
    qa = core.load_policy(cfg).get("qa", {})
    checks = []
    min_sharp = qa.get("sharpness", {}).get("min_laplacian_variance", 18.0)
    checks.append({"check": "sharpness", "value": sharpness, "min": min_sharp,
                   "pass": sharpness >= min_sharp})
    min_rms = qa.get("contrast", {}).get("min_rms", 18.0)
    checks.append({"check": "contrast", "value": contrast_rms, "min": min_rms,
                   "pass": contrast_rms >= min_rms})
    stats = core.tone_stats(img)
    max_white = qa.get("clipping", {}).get("max_white_clip_pct", 6.0)
    checks.append({"check": "white_clipping", "value": stats["white_clip_pct"], "max": max_white,
                   "pass": stats["white_clip_pct"] <= max_white})
    max_black = qa.get("clipping", {}).get("max_black_clip_pct", 8.0)
    checks.append({"check": "black_clipping", "value": stats["black_clip_pct"], "max": max_black,
                   "pass": stats["black_clip_pct"] <= max_black})
    verdict = "pass" if all(c["pass"] for c in checks) else "review"
    return {"verdict": verdict, "checks": checks}


# --------------------------------------------------------------------------
# packaging / product mockups
# --------------------------------------------------------------------------


def product_face(form, left, top, w, h):
    """Rect the printable face occupies on a product form (matches layer_product geometry)."""
    if form == "bottle":
        return (left + w * 0.07, top + h * 0.29, left + w * 0.93, top + h * 0.94)
    if form == "box":
        return (left + w * 0.03, top + h * 0.17, left + w * 0.95, top + h * 0.97)
    if form == "cup":
        return (left + w * 0.13, top + h * 0.34, left + w * 0.87, top + h * 0.9)
    if form == "pouch":
        return (left + w * 0.09, top + h * 0.34, left + w * 0.91, top + h * 0.92)
    if form == "tote":
        return (left + w * 0.07, top + h * 0.36, left + w * 0.93, top + h * 0.93)
    if form == "phone":
        return (left + w * 0.08, top + h * 0.07, left + w * 0.92, top + h * 0.93)
    return (left + w * 0.06, top + h * 0.06, left + w * 0.94, top + h * 0.94)  # poster


FORM_GEOMETRY = {
    "bottle": {"x": 0.5, "y": 0.56, "w": 0.30, "h": 0.50},
    "box": {"x": 0.5, "y": 0.56, "w": 0.34, "h": 0.44},
    "pouch": {"x": 0.5, "y": 0.56, "w": 0.32, "h": 0.46},
    "cup": {"x": 0.5, "y": 0.55, "w": 0.30, "h": 0.46},
    "tote": {"x": 0.5, "y": 0.58, "w": 0.36, "h": 0.44},
    "phone": {"x": 0.5, "y": 0.54, "w": 0.26, "h": 0.52},
    "poster": {"x": 0.5, "y": 0.54, "w": 0.44, "h": 0.56},
}


def op_mockup(ctx, a):
    """Place approved artwork onto a rendered product form, inside its printable face."""
    if not (getattr(a, "artwork", None) or getattr(a, "input", None)):
        raise core.usage_error("The 'mockup' command needs --artwork PATH (label or packaging art).")
    art_path = core.resolve_input(a.artwork or a.input)
    art = core.open_image(art_path, ctx).convert("RGBA")
    form = (getattr(a, "form", None) or getattr(a, "preset", None) or "bottle").lower()
    form = {"tote-bag": "tote", "bottle-mockup": "bottle"}.get(form, form)
    if form not in FORM_GEOMETRY:
        raise core.usage_error("--form must be bottle, box, pouch, cup, poster, phone or tote")
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or "1600x1600"))
    geom = dict(FORM_GEOMETRY[form])
    body_color = getattr(a, "bg_color", None) or ("#e8e4dc" if form in ("poster", "phone") else "#cfc7b9")
    scene_preset = {
        "label": f"mockup-{form}",
        "backdrop": {"type": "sweep", "top": "#f6f4ef", "bottom": "#d9d4ca", "curve": 0.62},
        "layers": [
            {"kind": "product", "form": form, "color": body_color, "accent": "#b9b1a3",
             "x": geom["x"], "y": geom["y"], "w": geom["w"], "h": geom["h"],
             "contact_shadow": True},
            {"kind": "atmosphere", "opacity": 0.12, "color": "p0"},
        ],
        "treatment": {"grain": 0.015, "vignette": 0.16, "bloom": 0.08, "contrast": 1.02,
                      "saturation": 0.98},
    }
    palette = ["#f6f4ef", "#dcd6cc", "#cfc7b9", "#8c8478", "#241f1a"]
    seed = ctx.seed if ctx.seed is not None else 99
    mock, scene = render_scene(size, scene_preset, palette, seed)
    mock = mock.convert("RGBA")
    canvas_w, canvas_h = size
    left = scene.px(geom["x"]) - scene.px(geom["w"]) / 2
    top = scene.py(geom["y"]) - scene.py(geom["h"]) / 2
    face = product_face(form, left, top, scene.px(geom["w"]), scene.py(geom["h"]))
    face_img = _quad_warp(art, face, (canvas_w, canvas_h))
    mock = Image.alpha_composite(mock, face_img)
    fw, fh = max(2, int(face[2] - face[0])), max(2, int(face[3] - face[1]))
    shade = core.gradient_image((fw, fh), [(0.0, (0, 0, 0)), (0.30, (255, 255, 255)),
                                           (0.62, (240, 240, 240)), (1.0, (40, 40, 40))], "horizontal")
    shade_rgba = shade.convert("RGBA")
    shade_rgba.putalpha(shade.convert("L").point(lambda v: int(96 * abs(v - 128) / 128)))
    shade_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    shade_layer.paste(shade_rgba, (int(face[0]), int(face[1])))
    face_mask = Image.new("L", (canvas_w, canvas_h), 0)
    ImageDraw.Draw(face_mask).rectangle([face[0], face[1], face[2], face[3]], fill=255)
    shade_layer.putalpha(ImageChops.multiply(shade_layer.getchannel("A"), face_mask))
    mock = Image.alpha_composite(mock, shade_layer)
    bounds = [round(v, 1) for v in face]
    fmt = (getattr(a, "format", None) or "png").lower().lstrip(".")
    project = core.slugify(getattr(a, "project", None) or "mockup")
    content = getattr(a, "name", None) or f"{form}-mockup"
    if core.slugify(content).startswith(project):
        content = core.slugify(content)[len(project):].lstrip("-_") or f"{form}-mockup"
    name = core.build_filename({"project": project, "content": content, "dimensions": size,
                               "version": int(getattr(a, "version", 1) or 1),
                               "status": getattr(a, "status", None) or "review"}, fmt, ctx.config.naming)
    path = ctx.out_path(name, "." + fmt)
    core.save_image(ctx, mock, path, fmt, quality=94,
                    pnginfo=core.build_pnginfo({"Software": f"imagekit {core.VERSION}",
                                                "Description": "Packaging mockup; artwork warped to the printable face."}))
    manifest = {
        "operation": "mockup", "form": form, "artwork": core.rel_posix(art_path),
        "artwork_aspect": core.aspect_label(art.width, art.height),
        "face_bounds_px": bounds,
        "face_aspect": core.aspect_label(int(fw), int(fh)),
        "artwork_scale": round(min(fw / art.width, fh / art.height), 4),
        "size": list(size),
        "warnings": ["Perspective is approximated by a slice warp; confirm final print tolerances "
                     "against a production dieline.",
                     "Artwork is fit to the face and cropped, not distorted."],
        "checks": ["confirm the face bounds clear all folds, curves and seams",
                   "confirm label copy stays above the minimum type size in print"],
        "output": core.rel_posix(path, ctx.out_root),
    }
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{form}-manifest"), manifest)
    ctx.audit("mockup.created", form=form)
    return {"data": manifest, "outputs": [path, out_json]}


def _quad_warp(src, bounds, canvas_size=None):
    """Approximate a flat-surface perspective placement by horizontal slice scaling."""
    x0, y0, x1, y1 = bounds
    w, h = int(x1 - x0), int(y1 - y0)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    resized = src.resize((w, h), Image.Resampling.LANCZOS)
    slices = max(24, h // 8)
    for i in range(slices):
        ty = i / slices
        # curvature: full width at the middle, slightly narrower at top and bottom
        inset = (math.sin(ty * math.pi) ** 0.35) * 0.04 + 0.96
        band_h = max(1, h // slices)
        sy0 = int(ty * h)
        sy1 = min(h, sy0 + band_h)
        if sy1 <= sy0:
            continue
        band = resized.crop((0, sy0, w, sy1))
        new_w = max(2, int(w * inset))
        band = band.resize((new_w, band.height), Image.Resampling.BILINEAR)
        out.paste(band, (int((w - new_w) / 2), sy0), band)
    size = canvas_size or (int(x0) + w, int(y0) + h)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(out, (int(x0), int(y0)), out)
    return canvas
