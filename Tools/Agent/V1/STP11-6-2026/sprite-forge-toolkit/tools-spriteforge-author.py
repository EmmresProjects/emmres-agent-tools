#!/usr/bin/env python3
"""spriteforge author: deterministic sprite bases, palettes, voxel renders, tokens, rigs.

Every operation is procedural and seeded, so the same spec always produces the
same pixels and every generated asset is reproducible. This module owns the
"creation" primitives of the catalog:

  brief        plain text / Markdown / HTML -> validated production brief
  frames       shape-layer sprite spec    -> PNG frames + animated preview
  palette      seed colour + harmony      -> named palette roles
  voxel        box list                   -> lit voxel views / frames
  turntable    box list                   -> fixed-angle turntable frames
  silhouette   sprite(s)                  -> readability scores + test sheet
  variants     base + variant axes        -> variant matrix (+ recolours)
  tokens       spec / images              -> design tokens + drift report
  rigplan      parts + pivots             -> bone/socket hierarchy plan

Run `python3 tools-spriteforge-author.py --help` for the operation list and
`python3 tools-spriteforge-author.py <op> --help` for one operation.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import importlib.util
import json
import math
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_author", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_author"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageDraw = core.ImageDraw
ImageFilter = core.ImageFilter
ImageOps = core.ImageOps

# --------------------------------------------------------------------------- #
# Colour helpers
# --------------------------------------------------------------------------- #

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", v):
        raise SFError("BAD_COLOR", f"expected a #rrggbb colour, got '{value}'")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(c)))) for c in rgb))


def shift(rgb, *, dh=0.0, ds=0.0, dl=0.0) -> tuple[int, int, int]:
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    h = (h + dh) % 1.0
    l = max(0.0, min(1.0, l + dl))
    s = max(0.0, min(1.0, s + ds))
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))  # type: ignore[return-value]


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 3)


def make_palette(seed: str, *, harmony: str = "analogous", steps: int = 5,
                 shadows: int = 2, highlights: int = 2) -> dict:
    """Build a named palette from one seed colour."""
    base = hex_to_rgb(seed) if seed.startswith("#") else hex_to_rgb(PALETTE_SEEDS.get(seed.lower(), "#4a90d9"))
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in base))
    offsets = {
        "mono": [0.0],
        "analogous": [0.0, -1 / 12, 1 / 12],
        "complementary": [0.0, 0.5],
        "split": [0.0, 5 / 12, 7 / 12],
        "triadic": [0.0, 1 / 3, 2 / 3],
        "tetradic": [0.0, 1 / 4, 1 / 2, 3 / 4],
    }
    if harmony not in offsets:
        raise SFError("BAD_HARMONY", f"harmony must be one of {sorted(offsets)}, got '{harmony}'")
    ramp: dict[str, str] = {}
    primary = base
    for i in range(shadows, 0, -1):
        ramp[f"shade_{i}"] = rgb_to_hex(shift(primary, dl=-0.09 * i, ds=-0.05 * i))
    ramp["base"] = rgb_to_hex(primary)
    for i in range(1, highlights + 1):
        ramp[f"light_{i}"] = rgb_to_hex(shift(primary, dl=0.09 * i, ds=-0.03 * i))
    ramp["outline"] = rgb_to_hex(shift(primary, dl=-0.30, ds=0.05))
    accents = {}
    for idx, off in enumerate(offsets[harmony][1:], start=1):
        accents[f"accent_{idx}"] = rgb_to_hex(shift(primary, dh=off))
    palette = {
        "seed": rgb_to_hex(base),
        "harmony": harmony,
        "roles": {**ramp, **accents},
        "team": {
            "team_red": "#d64545", "team_blue": "#3f7fd1",
            "team_green": "#4fa564", "team_yellow": "#dbac3d",
        },
        "status": {
            "ok": "#4fa564", "warn": "#dbac3d", "error": "#d64545",
            "info": "#3f7fd1", "disabled": "#6b6b72",
        },
        "notes": (
            f"{steps} ramp steps requested; value contrast between shade_1 and light_1 is "
            f"{contrast_ratio(shift(primary, dl=-0.09), shift(primary, dl=0.09))}:1"
        ),
    }
    return palette


PALETTE_SEEDS = {
    "hero": "#4a90d9", "enemy": "#c0504d", "earth": "#8a6b46", "forest": "#3f7a48",
    "fire": "#e0662a", "ice": "#7fc8e2", "arcane": "#8a5fc4", "metal": "#8d949c",
    "bone": "#ded4bd", "slime": "#6fae4a",
}

# --------------------------------------------------------------------------- #
# Shape layer renderer
# --------------------------------------------------------------------------- #

SHAPES = {"rect", "ellipse", "capsule", "triangle", "diamond", "polygon", "line", "star", "arc"}

MOTIONS = {"bob", "sway", "pulse", "rotate", "shake", "blink", "flash", "squash", "slide", "dip", "orbit"}


def resolve_colour(value: str, palette: dict[str, str] | None) -> tuple[int, int, int]:
    if not value:
        return (128, 128, 128)
    v = value.strip()
    if v.startswith("@"):
        role = v[1:]
        if not palette or role not in palette:
            raise SFError("UNKNOWN_ROLE", f"colour role '{v}' is not in the palette",
                          details={"known": sorted(palette or {})})
        return hex_to_rgb(palette[role])
    return hex_to_rgb(v)


def _polygon_points(shape: str, box: tuple[int, int, int, int], points, sides: int) -> list[tuple[float, float]]:
    x0, y0, w, h = box
    cx, cy = x0 + w / 2, y0 + h / 2
    if shape == "polygon":
        if not points:
            raise SFError("BAD_LAYER", "polygon shape requires 'points'")
        return [(x0 + float(px), y0 + float(py)) for px, py in points]
    if shape == "triangle":
        return [(cx, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    if shape == "diamond":
        return [(cx, y0), (x0 + w, cy), (cx, y0 + h), (x0, cy)]
    if shape == "star":
        pts = []
        n = max(3, sides)
        for i in range(n * 2):
            r = 0.5 if i % 2 == 0 else 0.22
            a = -math.pi / 2 + i * math.pi / n
            pts.append((cx + math.cos(a) * w * r, cy + math.sin(a) * h * r))
        return pts
    raise SFError("BAD_LAYER", f"shape '{shape}' has no polygon form")


def draw_shape(layer: dict, palette: dict[str, str] | None, size: tuple[int, int]) -> Image.Image:
    """Render one shape layer to its own RGBA tile of the frame size."""
    shape = (layer.get("shape") or "rect").lower()
    if shape not in SHAPES:
        raise SFError("BAD_SHAPE", f"unknown shape '{shape}'", details={"allowed": sorted(SHAPES)})
    box = layer.get("box")
    if not box or len(box) != 4:
        raise SFError("BAD_LAYER", f"layer '{layer.get('name')}' needs a 4-value box [x,y,w,h]")
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        raise SFError("BAD_LAYER", f"layer '{layer.get('name')}' has a non-positive box")
    colour = resolve_colour(layer.get("color", "#888888"), palette)
    opacity = int(layer.get("opacity", 255))
    tile = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    fill = colour + (opacity,)
    if shape == "rect":
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=fill)
    elif shape == "ellipse":
        d.ellipse([x, y, x + w - 1, y + h - 1], fill=fill)
    elif shape == "capsule":
        r = min(w, h) // 2
        d.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=r, fill=fill)
    elif shape == "line":
        width = int(layer.get("width", max(1, min(w, h))))
        d.line([x, y, x + w - 1, y + h - 1], fill=fill, width=width)
    elif shape == "arc":
        start = int(layer.get("start", 180))
        end = int(layer.get("end", 360))
        width = int(layer.get("width", max(1, min(w, h) // 4)))
        d.arc([x, y, x + w - 1, y + h - 1], start=start, end=end, fill=fill, width=width)
    else:
        pts = _polygon_points(shape, (x, y, w, h), layer.get("points"), int(layer.get("sides", 5)))
        d.polygon(pts, fill=fill)
    outline = layer.get("outline")
    if outline:
        width = int(outline.get("width", 1))
        ocol = resolve_colour(outline.get("color", "#101018"), palette) + (255,)
        mode = outline.get("mode", "outer")
        mask = tile.split()[-1].point(lambda v: 255 if v > 0 else 0)
        if mode == "outer":
            grown = mask.filter(ImageFilter.MaxFilter(width * 2 + 1))
            halo = Image.new("RGBA", size, ocol)
            halo.putalpha(grown)
            halo.alpha_composite(tile)
            tile = halo
        else:  # inner
            eroded = mask.filter(ImageFilter.MinFilter(width * 2 + 1))
            edge = Image.new("RGBA", size, ocol)
            edge.putalpha(Image.eval(mask, lambda v: v))
            inner = Image.new("RGBA", size, (0, 0, 0, 0))
            inner.putalpha(eroded)
            tile = tile.copy()
            tile.paste(edge, mask=Image.eval(mask, lambda v: v))
            tile.paste(inner, mask=eroded)
    return tile


def _motion_value(kind: str, t: float, amp: float, phase: float, rng: random.Random) -> float:
    a = 2 * math.pi * (t + phase)
    if kind == "bob":
        return math.sin(a) * amp
    if kind == "sway":
        return math.sin(a) * amp
    if kind == "pulse":
        return math.sin(a) * amp
    if kind == "rotate":
        return math.sin(a) * amp
    if kind == "shake":
        return rng.uniform(-amp, amp)
    if kind == "blink":
        return 0.0
    if kind == "flash":
        return abs(math.sin(a)) * amp
    if kind == "squash":
        return math.sin(a) * amp
    if kind == "slide":
        return (t - 0.5) * 2 * amp
    if kind == "dip":
        return -abs(math.sin(a)) * amp
    if kind == "orbit":
        return math.sin(a) * amp
    return 0.0


def render_frames(spec: dict) -> tuple[list, list[int], dict]:
    """Render a shape-layer spec into frames."""
    size = core.parse_size(spec.get("size", "64x64"))
    layers = spec.get("layers") or []
    if not layers:
        raise SFError("BAD_SPEC", "spec needs at least one layer")
    names = [l.get("name") or f"layer{i}" for i, l in enumerate(layers)]
    if len(set(names)) != len(names):
        raise SFError("BAD_SPEC", f"layer names must be unique, got {names}")
    palette_roles = spec.get("palette") or {}
    base_palette = None
    if isinstance(palette_roles, str):
        base_palette = make_palette(palette_roles, harmony=spec.get("harmony", "analogous"))["roles"]
    elif isinstance(palette_roles, dict):
        base_palette = {}
        for k, v in palette_roles.items():
            base_palette[k] = v if str(v).startswith("#") else rgb_to_hex(hex_to_rgb(str(v)))
    actions = spec.get("actions") or [{"name": "idle", "frames": 1, "duration": 100, "loop": False}]
    seed = int(spec.get("seed", 0)) or (int(hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:8], 16))
    frames: list = []
    durations: list[int] = []
    action_index: list[dict] = []
    frame_offset = 0
    for action in actions:
        count = int(action.get("frames", 1))
        duration = int(action.get("duration", 100))
        motions = action.get("motion") or []
        if count < 1:
            raise SFError("BAD_SPEC", f"action '{action.get('name')}' needs frames >= 1")
        if count > 600:
            raise SFError("BAD_SPEC", f"action '{action.get('name')}' exceeds 600 frames")
        for f in range(count):
            rng = random.Random(seed + f * 7919 + frame_offset)
            t = f / count if count > 1 else 0.0
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            for idx, layer in enumerate(layers):
                name = names[idx]
                tile = draw_shape(layer, base_palette, size)
                dx = dy = 0.0
                scale = 1.0
                rot = 0.0
                visible = True
                for m in motions:
                    if m.get("part") not in (name, "*"):
                        continue
                    kind = (m.get("type") or "").lower()
                    if kind not in MOTIONS:
                        raise SFError("BAD_MOTION", f"unknown motion '{kind}'",
                                      details={"allowed": sorted(MOTIONS)})
                    amp = float(m.get("amplitude", 2))
                    phase = float(m.get("phase", 0))
                    v = _motion_value(kind, t + float(m.get("delay", 0)), amp, phase, rng)
                    if kind in ("bob", "dip"):
                        dy += v
                    elif kind in ("sway", "slide", "orbit"):
                        dx += v
                    elif kind == "pulse":
                        scale *= 1 + v / 100.0
                    elif kind == "rotate":
                        rot += v
                    elif kind == "shake":
                        dx += v
                        dy += rng.uniform(-amp, amp)
                    elif kind == "blink":
                        if m.get("part") == "*":
                            raise SFError("BAD_MOTION",
                                          "blink must target a specific part (e.g. the eyes layer), not '*'",
                                          hint='use {"part": "eyes", "type": "blink"}')
                        blink_at = m.get("blink_frames")
                        if blink_at:
                            visible = f not in set(int(v) for v in blink_at)
                        else:
                            visible = f != count // 2
                    elif kind == "squash":
                        h_s = 1 + v / 100.0
                        tile = tile.resize((max(1, int(size[0] * (2 - h_s))), max(1, int(size[1] * h_s))),
                                           Image.Resampling.NEAREST)
                        tile = core.pad_to(tile, size, (0, int(size[1] * (1 - h_s))))
                    elif kind == "flash":
                        px = tile.load()
                        for yy in range(size[1]):
                            for xx in range(size[0]):
                                r, g, b, a = px[xx, yy]
                                if a:
                                    k = min(1.0, v / 100.0)
                                    px[xx, yy] = (int(r + (255 - r) * k), int(g + (255 - g) * k),
                                                  int(b + (255 - b) * k), a)
                if not visible:
                    continue
                if scale != 1.0:
                    nw, nh = max(1, int(size[0] * scale)), max(1, int(size[1] * scale))
                    tile = tile.resize((nw, nh), Image.Resampling.NEAREST)
                    tile = core.pad_to(tile, size, ((size[0] - nw) // 2, (size[1] - nh) // 2))
                if rot:
                    tile = tile.rotate(rot, resample=Image.Resampling.NEAREST, center=(size[0] / 2, size[1] / 2))
                shifted = Image.new("RGBA", size, (0, 0, 0, 0))
                shifted.paste(tile, (int(dx), int(dy)))
                canvas.alpha_composite(shifted)
            frames.append(canvas)
            durations.append(duration)
        if len(frames) == frame_offset:
            raise SFError("BAD_SPEC", f"action '{action.get('name')}' produced no frames")
        action_index.append({
            "name": action.get("name", f"action{len(action_index)}"),
            "start": frame_offset, "count": len(frames) - frame_offset,
            "duration": duration, "loop": bool(action.get("loop", True)),
        })
        frame_offset = len(frames)
    return frames, durations, {"actions": action_index, "size": list(size), "seed": seed,
                               "layers": names, "total_frames": len(frames)}


# --------------------------------------------------------------------------- #
# Voxel renderer
# --------------------------------------------------------------------------- #

def render_voxel(boxes: list[dict], *, size: tuple[int, int], yaw: float, pitch: float,
                 palette: dict[str, str] | None, scale: float | None = None,
                 auto_fit: bool = True,
                 light: tuple[float, float, float] = (-0.4, -0.8, 0.6)) -> Image.Image:
    """Painter's-algorithm render of axis aligned boxes with simple lambert shading."""
    verts_faces = []
    for bi, b in enumerate(boxes):
        x, y, z = (float(v) for v in b.get("pos", [0, 0, 0]))
        sx, sy, sz = (float(v) for v in b.get("size", [1, 1, 1]))
        colour = resolve_colour(b.get("color", "@base"), palette)
        corners = [(x + dx * sx, y + dy * sy, z + dz * sz)
                   for dx in (-0.5, 0.5) for dy in (-0.5, 0.5) for dz in (-0.5, 0.5)]
        # index of each corner in ``corners`` (same enumeration order)
        idx = {(dx, dy, dz): i for i, (dx, dy, dz) in enumerate(
            [(dx, dy, dz) for dx in (-0.5, 0.5) for dy in (-0.5, 0.5) for dz in (-0.5, 0.5)])}
        faces = [
            ((0, 0, -1), (idx[(-0.5, -0.5, -0.5)], idx[(0.5, -0.5, -0.5)],
                          idx[(0.5, 0.5, -0.5)], idx[(-0.5, 0.5, -0.5)])),
            ((0, 0, 1), (idx[(-0.5, -0.5, 0.5)], idx[(0.5, -0.5, 0.5)],
                         idx[(0.5, 0.5, 0.5)], idx[(-0.5, 0.5, 0.5)])),
            ((0, -1, 0), (idx[(-0.5, -0.5, -0.5)], idx[(0.5, -0.5, -0.5)],
                          idx[(0.5, -0.5, 0.5)], idx[(-0.5, -0.5, 0.5)])),
            ((0, 1, 0), (idx[(-0.5, 0.5, -0.5)], idx[(0.5, 0.5, -0.5)],
                         idx[(0.5, 0.5, 0.5)], idx[(-0.5, 0.5, 0.5)])),
            ((-1, 0, 0), (idx[(-0.5, -0.5, -0.5)], idx[(-0.5, 0.5, -0.5)],
                          idx[(-0.5, 0.5, 0.5)], idx[(-0.5, -0.5, 0.5)])),
            ((1, 0, 0), (idx[(0.5, -0.5, -0.5)], idx[(0.5, 0.5, -0.5)],
                         idx[(0.5, 0.5, 0.5)], idx[(0.5, -0.5, 0.5)])),
        ]
        for normal, quad in faces:
            # ``quad`` already holds indices into ``corners``
            pts3 = [corners[i] for i in quad]
            verts_faces.append((bi, normal, pts3, colour))

    def project(p, yaw_deg, pitch_deg, sc):
        yaw_r = math.radians(yaw_deg)
        pitch_r = math.radians(pitch_deg)
        x, y, z = p
        x1 = x * math.cos(yaw_r) - z * math.sin(yaw_r)
        z1 = x * math.sin(yaw_r) + z * math.cos(yaw_r)
        y1 = y * math.cos(pitch_r) - z1 * math.sin(pitch_r)
        z2 = y * math.sin(pitch_r) + z1 * math.cos(pitch_r)
        return x1, y1, z2

    projected = []
    for bi, normal, pts3, colour in verts_faces:
        rn = project(normal, yaw, pitch, 1)
        if rn[2] >= 0:  # back-face cull (camera looks down -z)
            continue
        # project once in model units; ``scale`` is applied a single time below
        pts = [project(p, yaw, pitch, 1) for p in pts3]
        depth = sum(p[2] for p in pts) / 4
        diff = max(0.0, -(rn[0] * light[0] + rn[1] * light[1] + rn[2] * light[2]))
        shade = 0.45 + 0.55 * diff
        col = tuple(int(c * shade) for c in colour)
        projected.append((depth, [(p[0], p[1]) for p in pts], col))
    projected.sort(key=lambda item: -item[0])
    if not projected:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    xs = [p[0] for _, poly, _ in projected for p in poly]
    ys = [p[1] for _, poly, _ in projected for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    model_w, model_h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    if auto_fit and scale is None:
        # fill most of the canvas regardless of how many voxel units the model spans
        scale = min(size[0] * 0.86 / model_w, size[1] * 0.86 / model_h)
    scale = float(scale or 6.0)
    cw, ch = model_w * scale, model_h * scale
    ox = (size[0] - cw) / 2 - minx * scale
    oy = (size[1] - ch) / 2 - miny * scale
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for _, poly, col in projected:
        d.polygon([(px * scale + ox, py * scale + oy) for px, py in poly],
                  fill=col + (255,), outline=col + (255,))
    return img


def _voxel_spec_boxes(spec: dict) -> list[dict]:
    boxes = spec.get("boxes")
    if not boxes:
        raise SFError("BAD_SPEC", "voxel spec needs a non-empty 'boxes' list")
    for i, b in enumerate(boxes):
        if "pos" not in b or "size" not in b:
            raise SFError("BAD_SPEC", f"box {i} needs 'pos' and 'size'")
        if any(float(v) <= 0 for v in b["size"]):
            raise SFError("BAD_SPEC", f"box {i} size must be positive")
    return boxes


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #

HTML_TAG = re.compile(r"<[^>]+>")
HTML_BLOCK = re.compile(r"</?(p|div|li|br|tr|h[1-6]|section|article)[^>]*>", re.I)


def html_to_text(raw: str) -> str:
    txt = HTML_BLOCK.sub("\n", raw)
    txt = HTML_TAG.sub("", txt)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", txt)


def op_brief(args, root: Path, rep: core.Reporter):
    """Parse free text, Markdown or HTML into a validated production brief."""
    if args.text:
        raw = args.text
    else:
        p = Path(args.input)
        p = p if p.is_absolute() else root / p
        if not p.exists():
            raise SFError("INPUT_NOT_FOUND", f"brief source not found: {p}")
        raw = p.read_text(encoding="utf-8", errors="replace")
        rep.stat("source", str(p))
    clean = html_to_text(raw) if (args.format == "html" or "<" in raw and ">" in raw) else raw
    if not clean.strip():
        raise SFError("EMPTY_BRIEF", "brief source has no usable text")
    lines = [ln.strip(" \t*#->") for ln in clean.splitlines()]
    lines = [ln for ln in lines if ln]
    text = " ".join(lines)
    explicit = {}
    for key, pattern in {
        "size": r"(?:size|canvas)\s*[:\-]?\s*(\d{2,4})\s*[xX]\s*(\d{2,4})",
        "fps": r"(\d{1,3})\s*fps",
        "directions": r"(\d{1,2})\s*(?:directions?|dirs?)\b",
        "palette_size": r"(\d{1,3})\s*(?:colou?rs?|cpl)\b",
    }.items():
        m = re.search(pattern, text, re.I)
        if m:
            explicit[key] = m.group(0) if key != "size" else f"{m.group(1)}x{m.group(2)}"
    name = "sprite"
    m = re.search(r"(?:name|asset|character|title)\s*[:\-]\s*([A-Za-z0-9 _\-]{2,40})", text, re.I)
    if m:
        name = m.group(1).strip()
    elif args.name:
        name = args.name
    roles = [w for w in ("character", "creature", "prop", "vehicle", "effect", "environment", "ui", "icon", "portrait",
                          "boss", "tile", "projectile", "item") if re.search(rf"\b{w}\b", text, re.I)]
    actions = []
    for m in re.finditer(r"\b(idle|walk|run|sprint|jump|fall|attack|cast|block|hit|hurt|death|die|pickup|use|"
                         r"shoot|reload|dodge|roll|climb|swim|fly|emote|talk|open|craft|harvest|build|guard|"
                         r"victory|defeat|spawn|dissolve)\b", text, re.I):
        a = m.group(1).lower()
        if a not in actions:
            actions.append(a)
    if not actions:
        actions = ["idle"]
    directions = int(re.search(r"(\d+)", explicit.get("directions", "0")).group(1)) if "directions" in explicit else 1
    size = explicit.get("size", args.size or "64x64")
    w, h = core.parse_size(size)
    style_words = [w for w in ("pixel", "painted", "vector", "voxel", "isometric", "flat", "cel", "retro",
                               "hand-drawn", "low-poly", "noir", "pastel", "neon") if re.search(rf"\b{w}", text, re.I)]
    brief = {
        "name": core.slugify(name),
        "display_name": name,
        "size": [w, h],
        "roles": roles or ["prop"],
        "style": style_words or ["pixel"],
        "actions": actions,
        "directions": max(1, min(16, directions)),
        "fps": int(re.search(r"(\d+)", explicit.get("fps", "12")).group(1)),
        "palette_size": int(re.search(r"(\d+)", explicit.get("palette_size", "16")).group(1)),
        "frames_per_action": args.frames_per_action,
        "engine": args.engine,
        "sources": [str(Path(args.input))] if args.input else ["inline:text"],
        "notes": [ln for ln in lines if len(ln) > 20][:5],
        "ambiguities": [],
    }
    if not roles:
        brief["ambiguities"].append("no role keyword found; defaulted to 'prop'")
    if "directions" in explicit and directions not in (1, 2, 4, 6, 8, 16):
        brief["ambiguities"].append(f"{directions} directions is unusual; confirm the set size")
    brief["estimated_frames"] = len(actions) * brief["directions"] * brief["frames_per_action"]
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(brief, out)
        rep.add_output(out, "brief", frames=brief["estimated_frames"])
    rep.stat("actions", actions)
    rep.stat("estimated_frames", brief["estimated_frames"])
    return {"summary": {"name": brief["name"], "actions": len(actions), "directions": brief["directions"],
                        "estimated_frames": brief["estimated_frames"], "output": str(out)},
            "brief": brief}


def op_frames(args, root: Path, rep: core.Reporter):
    """Render a shape-layer sprite spec into deterministic frames."""
    sp = core.find_input(Path(args.spec), root)
    spec = core.load_json(sp)
    if not isinstance(spec, dict):
        raise SFError("BAD_SPEC", "spec root must be an object")
    frames, durations, meta = render_frames(spec)
    out_dir = core.guard_output(root / args.output, root, args.allow_outside_root, as_dir=True)
    if not args.dry_run:
        written = []
        for action in meta["actions"]:
            for i in range(action["count"]):
                idx = action["start"] + i
                p = out_dir / f"{meta['seed']}_{action['name']}_{i:03d}.png"
                core.save_image(frames[idx], p)
                written.append(str(p))
        # The preview belongs beside the frame folder, never inside it: the frame
        # folder must contain only frame images so sheet tools cannot pick it up
        # as an extra frame.
        preview = out_dir.parent / f"{core.slugify(spec.get('name', 'sprite'))}-preview.gif"
        core.save_animated(frames, preview, durations, loop=0)
        manifest = {"spec": str(sp), **meta, "files": written, "preview": str(preview)}
        core.save_json(manifest, out_dir / "frames-manifest.json")
        rep.add_output(out_dir, "directory", frames=len(frames))
        rep.add_output(preview, "preview")
    rep.stat("frames", len(frames))
    return {"summary": {"frames": len(frames), "actions": len(meta["actions"]), "out": str(out_dir)},
            "actions": meta["actions"]}


def op_palette(args, root: Path, rep: core.Reporter):
    """Generate a named palette (base, shade, highlight, outline, team, status)."""
    pal = make_palette(args.seed, harmony=args.harmony, shadows=args.shadows, highlights=args.highlights)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(pal, out)
        rep.add_output(out, "palette")
    if args.preview:
        pv = core.guard_output(root / args.preview, root, args.allow_outside_root)
        roles = list(pal["roles"].items())
        sw = 48
        sheet = Image.new("RGBA", (sw * len(roles), sw + 40), (24, 24, 28, 255))
        d = ImageDraw.Draw(sheet)
        font = core.ImageFont.load_default()
        for i, (role, hexv) in enumerate(roles):
            d.rectangle([i * sw, 0, (i + 1) * sw - 1, sw - 1], fill=hex_to_rgb(hexv) + (255,))
            d.text((i * sw + 2, sw + 2), role[:8], fill=(220, 220, 230, 255), font=font)
            d.text((i * sw + 2, sw + 18), hexv, fill=(150, 150, 160, 255), font=font)
        if not args.dry_run:
            core.save_image(sheet, pv)
            rep.add_output(pv, "preview")
    rep.stat("roles", len(pal["roles"]))
    return {"summary": {"seed": pal["seed"], "harmony": pal["harmony"], "roles": len(pal["roles"]),
                        "output": str(out)}}


def op_voxel(args, root: Path, rep: core.Reporter):
    """Render voxel boxes into fixed camera views or a frame sequence."""
    sp = core.find_input(Path(args.spec), root)
    spec = core.load_json(sp)
    boxes = _voxel_spec_boxes(spec)
    size = core.parse_size(args.size or spec.get("size", "64x64"))
    palette = None
    if spec.get("palette"):
        palette = make_palette(spec["palette"], harmony=spec.get("harmony", "analogous"))["roles"] if isinstance(spec["palette"], str) else spec["palette"]
    yaws = core.parse_int_list(args.yaws, name="yaws")
    if len(yaws) > 64:
        raise SFError("TOO_MANY_VIEWS", "at most 64 views per run")
    out_dir = core.guard_output(root / args.output, root, args.allow_outside_root, as_dir=True)
    frames = []
    for i, yaw in enumerate(yaws):
        rep.deadline.check()
        frames.append(render_voxel(boxes, size=size, yaw=float(yaw), pitch=float(args.pitch),
                                   palette=palette,
                                   scale=float(args.scale) if args.scale else None))
    if not args.dry_run:
        for yaw, im in zip(yaws, frames):
            core.save_image(im, out_dir / f"yaw-{yaw:03d}.png")
        core.save_animated(frames, out_dir / "turntable.gif", [args.duration] * len(frames))
        core.save_json({"views": yaws, "pitch": args.pitch, "scale": args.scale, "boxes": len(boxes)},
                       out_dir / "views.json")
        rep.add_output(out_dir, "directory", views=len(frames))
    rep.stat("views", len(frames))
    return {"summary": {"views": len(frames), "boxes": len(boxes), "out": str(out_dir)}}


def _pose_crop(im, anchor: str, size: tuple[int, int]):
    """Crop a pose around an implied anchor so every frame shares a ground line."""
    bbox = core.alpha_bbox(im)
    if not bbox:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    art = im.crop(bbox)
    ax, ay = {"bottom-center": (0.5, 1.0), "center": (0.5, 0.5),
              "top-center": (0.5, 0.0)}.get(anchor, (0.5, 1.0))
    if art.width > size[0] or art.height > size[1]:
        art = ImageOps.contain(art, size, Image.Resampling.NEAREST)
    x = int((size[0] - art.width) * ax)
    y = int((size[1] - art.height) * ay)
    return core.pad_to(art, size, (x, y))


def op_from_frames(args, root: Path, rep: core.Reporter):
    """Build a complete action set from pose images on one shared canvas and ground line."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"--input must be a folder of pose images: {src}")
    files = core.list_images(src, recursive=False)
    if not files:
        raise SFError("NO_FRAMES", f"no pose images under {src}")
    poses = []
    for p in files:
        im = core.load_image(p, max_pixels=args.max_pixels)
        poses.append((p, im))
    size = core.parse_size(args.size) if args.size else (
        max(im.width for _, im in poses), max(im.height for _, im in poses))
    if args.ground_align:
        # align all poses on the tallest opaque pixel row so the feet share a baseline
        max_bottom = max(core.alpha_bbox(im)[3] for _, im in poses if core.alpha_bbox(im))
        aligned = []
        for p, im in poses:
            bbox = core.alpha_bbox(im)
            shift = max_bottom - (bbox[3] if bbox else 0)
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            art = im.crop(bbox) if bbox else im
            if art.width > size[0] or art.height > size[1]:
                art = ImageOps.contain(art, size, Image.Resampling.NEAREST)
            canvas.alpha_composite(art, ((size[0] - art.width) // 2, max(0, size[1] - art.height)))
            aligned.append((p, canvas))
        poses = aligned
    else:
        poses = [(p, _pose_crop(im, args.anchor, size)) for p, im in poses]
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    duration = int(1000 / args.fps)
    written = []
    for i, (p, im) in enumerate(poses):
        if args.dedupe:
            digest = core.image_sha256(im)
            if any(digest == core.image_sha256(prev) for _, prev in poses[:i]):
                continue
        name = f"{args.name}_{i:04d}.png"
        if not args.dry_run:
            core.save_image(im, out_dir / name)
        written.append({"file": name, "source": p.name})
    if not args.dry_run:
        core.save_json({"action": args.name, "frames": len(written), "fps": args.fps,
                        "duration_ms": duration, "size": list(size),
                        "ground_align": bool(args.ground_align), "frames_index": written},
                       out_dir / "action.json")
        if args.preview:
            pv = core.guard_output(root / args.preview, root, args.allow_outside_root)
            core.save_animated([im for _, im in poses][: args.max_preview_frames], pv,
                               [duration] * min(len(poses), args.max_preview_frames))
            rep.add_output(pv, "preview")
        rep.add_output(out_dir, "directory", frames=len(written))
    rep.stat("frames", len(written))
    return {"summary": {"frames": len(written), "fps": args.fps, "size": list(size),
                        "out": str(out_dir)}}


def _p_from_frames(p):
    p.add_argument("--input", "-i", required=True, help="folder of pose images")
    p.add_argument("--output", "-o", required=True, help="output action folder")
    p.add_argument("--name", default="action")
    p.add_argument("--size", help="shared canvas WxH (default: largest pose)")
    p.add_argument("--anchor", default="bottom-center",
                   choices=["bottom-center", "center", "top-center"])
    p.add_argument("--ground-align", action="store_true", default=True,
                   help="align every pose to one ground baseline")
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--dedupe", action="store_true", help="skip byte-identical poses")
    p.add_argument("--preview", help="optional GIF preview path")
    p.add_argument("--max-preview-frames", type=int, default=120)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def op_turntable(args, root: Path, rep: core.Reporter):
    """Render an object turntable at fixed angle steps."""
    if not getattr(args, "yaws", None):
        args.yaws = ",".join(str(int(i * 360 / args.steps)) for i in range(args.steps))
    return op_voxel(args, root, rep)


def op_silhouette(args, root: Path, rep: core.Reporter):
    """Score silhouette clarity, balance and distance readability."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    results = []
    entries = []
    for i, frame in enumerate(fs.frames):
        alpha = frame.convert("RGBA").split()[-1]
        bbox = alpha.getbbox()
        if not bbox:
            results.append({"frame": i, "error": "fully transparent"})
            continue
        crop = frame.crop(bbox)
        mask = crop.split()[-1].point(lambda v: 255 if v >= 128 else 0)
        area = core.opaque_pixels(mask)
        box_area = crop.width * crop.height
        fill = area / box_area if box_area else 0
        # compactness via perimeter estimate on a 4-neighbour boundary count
        px = mask.load()
        perimeter = 0
        for y in range(crop.height):
            for x in range(crop.width):
                if px[x, y]:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if nx < 0 or ny < 0 or nx >= crop.width or ny >= crop.height or not px[nx, ny]:
                            perimeter += 1
        compact = (4 * math.pi * area / (perimeter ** 2)) if perimeter else 0
        mirrored = mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        diff = core.ImageChops.difference(mask, mirrored)
        asym = sum(1 for p in diff.tobytes() if p) / max(1, area)
        small = crop.resize((max(4, crop.width // 4), max(4, crop.height // 4)), Image.Resampling.LANCZOS)
        blurred = crop.convert("RGBA").filter(ImageFilter.GaussianBlur(1.2))
        small_l = small.convert("L")
        lhist = small_l.histogram()
        lmin = next((i for i, v in enumerate(lhist) if v), 0)
        lmax = next((255 - i for i, v in enumerate(reversed(lhist)) if v), 0)
        contrast = (lmax - lmin) / 255 if small.width else 0
        aspect = crop.width / crop.height
        score = 100 * (
            0.30 * min(1.0, fill / 0.55)
            + 0.25 * min(1.0, compact / 0.55)
            + 0.20 * (1 - min(1.0, asym / 0.9))
            + 0.15 * min(1.0, contrast)
            + 0.10 * (1 - min(1.0, abs(aspect - 0.75) / 0.75))
        )
        entry = {
            "frame": i, "bbox": list(bbox), "size": [crop.width, crop.height],
            "area_px": area, "fill_ratio": round(fill, 4), "compactness": round(compact, 4),
            "asymmetry": round(asym, 4), "contrast": round(contrast, 3),
            "aspect": round(aspect, 3), "score": round(score, 1),
            "grade": "good" if score >= 70 else "fair" if score >= 50 else "weak",
        }
        results.append(entry)
        entries.append((crop, f"f{i} {entry['score']:.0f}"))
    values = [r["score"] for r in results if "score" in r]
    if args.output:
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        if not args.dry_run:
            core.save_json({"frames": results, "mean_score": round(sum(values) / len(values), 1) if values else 0}, out)
            rep.add_output(out, "silhouette-report")
            if entries:
                pv = out.with_name(out.stem + "-sheet.png")
                core.save_image(core.make_contact_sheet(entries, cols=min(8, len(entries))), pv)
                rep.add_output(pv, "preview")
    rep.stat("mean_score", round(sum(values) / len(values), 1) if values else 0)
    weakest = min(results, key=lambda r: r.get("score", 999)) if values else None
    return {"summary": {"frames": len(values),
                        "mean_score": rep.data["stats"]["mean_score"],
                        "weakest_frame": weakest["frame"] if weakest else None},
            "frames": results,
            "weighting": {"fill": 0.30, "compactness": 0.25, "symmetry": 0.20, "contrast": 0.15, "aspect": 0.10}}


def op_variants(args, root: Path, rep: core.Reporter):
    """Plan a controlled variant matrix and optionally recolour a base sprite."""
    axes = core.parse_kv_list(args.axis, name="--axis")
    if not axes:
        raise SFError("BAD_AXIS", "provide at least one --axis name=v1,v2,...")
    values = {k: [v.strip() for v in val.split(",") if v.strip()] for k, val in axes.items()}
    total = 1
    for v in values.values():
        total *= len(v)
    if total > args.max_variants:
        raise SFError("TOO_MANY_VARIANTS", f"{total} combinations exceeds --max-variants {args.max_variants}",
                      hint="trim an axis or raise the cap")
    combos = [{}]
    for axis, opts in values.items():
        combos = [dict(c, **{axis: o}) for c in combos for o in opts]
    matrix = []
    for c in combos:
        name = "-".join([args.base] + [core.slugify(c[a]) for a in values])
        matrix.append({"name": name, "variant": c})
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if args.source:
        src = Path(args.source) if Path(args.source).is_absolute() else root / args.source
        base_im = core.load_image(src, max_pixels=args.max_pixels)
        out_dir = core.guard_output(out.parent / (out.stem + "-renders"), root,
                                    args.allow_outside_root, as_dir=True)
        if not args.dry_run:
            for entry in matrix:
                im = base_im.copy()
                shiftv = sum(ord(ch) for ch in "".join(entry["variant"].values())) % 360 / 360.0
                hue = float(args.hue_step) * (list(values.values()).index(list(entry["variant"].values())[0])
                                              if values else 0)
                im = _recolour(im, dh=hue / 360.0 + shiftv * float(args.hue_jitter), ds=args.sat, dl=args.light)
                core.save_image(im, out_dir / f"{entry['name']}.png")
                entry["file"] = str(out_dir / f"{entry['name']}.png")
            rep.add_output(out_dir, "directory", variants=len(matrix))
    if not args.dry_run:
        core.save_json({"base": args.base, "axes": values, "variants": matrix, "count": len(matrix)}, out)
        rep.add_output(out, "variant-matrix")
    rep.stat("variants", len(matrix))
    return {"summary": {"variants": len(matrix), "output": str(out)}, "axes": values}


def _recolour(im, *, dh: float, ds: float, dl: float):
    rgb = im.convert("RGBA")
    r, g, b, a = rgb.split()
    px = rgb.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            pr, pg, pb, pa = px[x, y]
            if pa == 0:
                continue
            h, l, s = colorsys.rgb_to_hls(pr / 255, pg / 255, pb / 255)
            h = (h + dh) % 1
            s = max(0.0, min(1.0, s + ds))
            l = max(0.0, min(1.0, l + dl))
            nr, ng, nb = colorsys.hls_to_rgb(h, l, s)
            px[x, y] = (int(nr * 255), int(ng * 255), int(nb * 255), pa)
    return rgb


def op_tokens(args, root: Path, rep: core.Reporter):
    """Extract design tokens from a spec and report drift against a token file."""
    if args.input:
        path = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        data = core.load_json(path)
        if not isinstance(data, dict):
            raise SFError("BAD_SPEC", "token source must be a JSON object")
        rep.stat("source", str(path))
    elif args.spec:
        path = core.find_input(Path(args.spec), root)
        data = core.load_json(path)
    else:
        raise SFError("MISSING_INPUT", "provide --input (tokens) or --spec (sprite spec)")
    source_kind = "tokens" if args.input else "sprite-spec"
    tokens = {"colours": {}, "sizes": {}, "lines": {}, "palette_roles": [], "source": str(path)}
    if source_kind == "tokens":
        tokens["colours"] = {k: v for k, v in (data.get("roles") or {}).items()}
    else:
        roles = set()
        for layer in data.get("layers", []):
            c = str(layer.get("color", ""))
            if c.startswith("@"):
                roles.add(c[1:])
            elif c:
                tokens["colours"][layer.get("name", "unnamed")] = c
            if layer.get("outline"):
                tokens["lines"][f"{layer.get('name','l')}_width"] = layer["outline"].get("width", 1)
        tokens["palette_roles"] = sorted(roles)
        tokens["sizes"]["canvas"] = data.get("size", "64x64")
    drift = []
    if args.compare:
        cmp_path = Path(args.compare) if Path(args.compare).is_absolute() else root / args.compare
        reference = core.load_json(cmp_path)
        ref_roles = reference.get("roles") or reference.get("colours") or {}
        for role, hexv in ref_roles.items():
            if role in tokens["colours"] and str(tokens["colours"][role]).lower() != str(hexv).lower():
                drift.append({"role": role, "expected": hexv, "actual": tokens["colours"][role]})
        missing = [r for r in ref_roles if r not in tokens["colours"]]
        for role in missing:
            drift.append({"role": role, "expected": ref_roles[role], "actual": None})
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    payload = {"tokens": tokens, "drift": drift, "drift_count": len(drift),
               "status": "clean" if not drift else "drift"}
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "tokens")
    rep.stat("drift", len(drift))
    return {"summary": {"status": payload["status"], "colours": len(tokens["colours"]),
                        "drift": len(drift), "output": str(out)}, "drift": drift}


def op_rigplan(args, root: Path, rep: core.Reporter):
    """Build a bone/socket hierarchy plan with pivots and draw order."""
    sp = core.find_input(Path(args.spec), root)
    spec = core.load_json(sp)
    parts = spec.get("parts")
    if not parts:
        raise SFError("BAD_SPEC", "rig spec needs a 'parts' list with name/parent/pivot")
    names = set()
    for p in parts:
        if not p.get("name"):
            raise SFError("BAD_SPEC", "every part needs a name")
        if p["name"] in names:
            raise SFError("BAD_SPEC", f"duplicate part name '{p['name']}'")
        names.add(p["name"])
    for p in parts:
        parent = p.get("parent")
        if parent and parent not in names:
            raise SFError("BAD_SPEC", f"part '{p['name']}' references unknown parent '{parent}'")
    # cycle detection
    parent_of = {p["name"]: p.get("parent") for p in parts}
    for name in parent_of:
        seen = set()
        cur = name
        while cur:
            if cur in seen:
                raise SFError("RIG_CYCLE", f"cycle detected at '{cur}'")
            seen.add(cur)
            cur = parent_of.get(cur)
    depth = {}
    def resolve(n: str) -> int:
        if n in depth:
            return depth[n]
        p = parent_of.get(n)
        depth[n] = 0 if not p else resolve(p) + 1
        return depth[n]
    for n in parent_of:
        resolve(n)
    draw_order = spec.get("draw_order") or sorted(names, key=lambda n: (depth[n], parent_of[n] or "", n))
    sockets = spec.get("sockets") or []
    for s in sockets:
        if s.get("parent") and s["parent"] not in names:
            raise SFError("BAD_SPEC", f"socket '{s.get('name')}' references unknown parent '{s['parent']}'")
    bones = []
    for p in parts:
        bones.append({
            "name": p["name"], "parent": p.get("parent"), "depth": depth[p["name"]],
            "pivot": p.get("pivot", [0, 0]), "length": p.get("length", 0),
            "rotation": p.get("rotation", 0), "scale": p.get("scale", 1),
            "constraints": p.get("constraints", []),
        })
    plan = {
        "unit": spec.get("unit", "pixels"),
        "canvas": spec.get("size", "64x64"),
        "bones": bones,
        "draw_order": draw_order,
        "sockets": sockets,
        "anchors": spec.get("anchors", {}),
        "engines": {
            "spine": {"note": "map bones to Spine slots; pivot in local bone space"},
            "godot": {"note": "Skeleton2D bones use rest rotation in radians"},
            "unity2d": {"note": "pivot = sprite custom pivot in pixels from bottom-left"},
        },
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(plan, out)
        rep.add_output(out, "rig-plan")
    rep.stat("bones", len(bones))
    return {"summary": {"bones": len(bones), "sockets": len(sockets), "max_depth": max(depth.values()) if depth else 0,
                        "output": str(out)}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_brief(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", help="brief source file (.txt/.md/.html)")
    p.add_argument("--text", help="inline brief text")
    p.add_argument("--format", choices=["auto", "text", "html"], default="auto")
    p.add_argument("--name", default="sprite", help="fallback asset name")
    p.add_argument("--size", help="fallback canvas size, e.g. 64x64")
    p.add_argument("--engine", default="generic", help="target engine for the plan")
    p.add_argument("--frames-per-action", type=int, default=8)
    p.add_argument("--output", "-o", default="brief.json")


def _p_frames(p: argparse.ArgumentParser) -> None:
    p.add_argument("--spec", required=True, help="shape-layer spec JSON")
    p.add_argument("--output", "-o", required=True, help="output folder for frames + preview")


def _p_palette(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seed", required=True, help="seed colour (#rrggbb) or seed name (hero, fire, ice ...)")
    p.add_argument("--harmony", default="analogous",
                   choices=["mono", "analogous", "complementary", "split", "triadic", "tetradic"])
    p.add_argument("--shadows", type=int, default=2)
    p.add_argument("--highlights", type=int, default=2)
    p.add_argument("--preview", help="optional PNG swatch sheet")
    p.add_argument("--output", "-o", default="palette.json")


def _p_voxel(p: argparse.ArgumentParser) -> None:
    p.add_argument("--spec", required=True, help="voxel spec JSON with a boxes list")
    p.add_argument("--yaws", default="0,45,90,135,180,225,270,315", help="comma separated yaw angles")
    p.add_argument("--pitch", type=float, default=25.0)
    p.add_argument("--scale", type=float, default=None,
                   help="pixels per voxel unit (default: auto-fit the canvas)")
    p.add_argument("--size", help="frame size WxH (default from spec)")
    p.add_argument("--duration", type=int, default=100, help="ms per frame in the turntable GIF")
    p.add_argument("--output", "-o", required=True)


def _p_turntable(p: argparse.ArgumentParser) -> None:
    _p_voxel(p)
    p.add_argument("--steps", type=int, default=8, help="number of evenly spaced angles when --yaws is omitted")


def _p_silhouette(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", "-i", required=True, help="image, sheet, GIF or folder")
    p.add_argument("--output", "-o", help="silhouette report JSON")


def _p_variants(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", required=True, help="base asset name")
    p.add_argument("--axis", action="append", default=[], metavar="NAME=v1,v2",
                   help="variant axis (repeatable)")
    p.add_argument("--source", help="base PNG to recolour per variant")
    p.add_argument("--hue-step", type=float, default=24.0)
    p.add_argument("--hue-jitter", type=float, default=0.0)
    p.add_argument("--sat", type=float, default=0.0)
    p.add_argument("--light", type=float, default=0.0)
    p.add_argument("--max-variants", type=int, default=256)
    p.add_argument("--output", "-o", default="variants.json")


def _p_tokens(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", help="palette/token JSON to read")
    p.add_argument("--spec", help="sprite spec JSON to extract tokens from")
    p.add_argument("--compare", help="reference token JSON to detect drift")
    p.add_argument("--output", "-o", default="tokens.json")


def _p_rigplan(p: argparse.ArgumentParser) -> None:
    p.add_argument("--spec", required=True, help="rig spec JSON with parts/sockets")
    p.add_argument("--output", "-o", default="rig-plan.json")


OPS = {
    "brief": (_p_brief, op_brief),
    "frames": (_p_frames, op_frames),
    "palette": (_p_palette, op_palette),
    "voxel": (_p_voxel, op_voxel),
    "turntable": (_p_turntable, op_turntable),
    "silhouette": (_p_silhouette, op_silhouette),
    "variants": (_p_variants, op_variants),
    "tokens": (_p_tokens, op_tokens),
    "from-frames": (_p_from_frames, op_from_frames),
    "rigplan": (_p_rigplan, op_rigplan),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "author",
        "spriteforge author: deterministic sprite bases, palettes, voxel renders, tokens and rig plans",
        OPS,
    ))
