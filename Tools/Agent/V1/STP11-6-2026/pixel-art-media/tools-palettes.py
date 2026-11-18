"""pixel-art-media: palette engineering.

Extraction, rule-based generation, reduction/apply, index-preserving swaps,
format conversion (hex/JSON/GPL/JASC-PAL/ACT/ASE/PNG), analysis with
color-vision simulation, and palette-cycle animation.
"""
from __future__ import annotations

import colorsys
import math
import random
import struct
from pathlib import Path

from PIL import Image

im = None  # loaded lazily via _img()


def _img():
    global im
    if im is None:
        import importlib.util
        import sys
        p = Path(__file__).resolve().parent / "tools-imaging.py"
        spec = importlib.util.spec_from_file_location("tools_imaging", p)
        m = sys.modules.get("tools_imaging")
        if m is None:
            m = importlib.util.module_from_spec(spec)
            sys.modules["tools_imaging"] = m
            spec.loader.exec_module(m)
        im = m
    return im


# ------------------------------------------------------------------ formats -

def read_palette_any(path):
    """Read a palette from hex/json/gpl/pal/act/ase/png. Returns RGB tuples."""
    I = _img()
    p = Path(path)
    if not p.is_file():
        raise I.ToolError("palette-not-found", f"Palette file not found: {path}")
    ext = p.suffix.lower()
    if ext in (".png", ".gif", ".bmp", ".webp"):
        img = Image.open(p)
        img.load()
        if img.mode == "P":
            raw = img.getpalette()
            n = len(set(img.getdata()))
            pal = [tuple(raw[i:i + 3]) for i in range(0, min(256, max(n, 2)) * 3, 3)]
            return pal
        cols = I.image_colors(img.convert("RGBA"))
        if not cols:
            raise I.ToolError("empty-palette", f"No opaque colors in {path}")
        return [c for c, _ in cols[:256]]
    data = p.read_bytes()
    if ext == ".json":
        import json
        try:
            obj = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise I.ToolError("palette-invalid", f"Invalid palette JSON: {e}")
        colors = obj.get("colors") if isinstance(obj, dict) else obj
        if not isinstance(colors, list) or not colors:
            raise I.ToolError("palette-invalid", "JSON palette needs a non-empty 'colors' list.")
        return [I.parse_color(c)[:3] for c in colors]
    if ext == ".act":
        if len(data) not in (768, 772):
            raise I.ToolError("palette-invalid", "ACT must be 768 or 772 bytes.")
        count = 256
        if len(data) == 772:
            count = struct.unpack(">H", data[768:770])[0] or 256
        return [tuple(data[i:i + 3]) for i in range(0, count * 3, 3)]
    if ext == ".ase":
        return _read_ase(data)
    text = data.decode("utf-8-sig", errors="replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if ext == ".gpl" or text.startswith("GIMP Palette"):
        out = []
        for l in lines:
            parts = l.split()
            if len(parts) >= 3 and all(x.isdigit() for x in parts[:3]):
                out.append(tuple(min(255, int(x)) for x in parts[:3]))
        if not out:
            raise I.ToolError("palette-invalid", f"No RGB rows found in GPL {path}")
        return out
    if ext == ".pal" or text.startswith("JASC-PAL"):
        if lines and lines[0].startswith("JASC"):
            # JASC: line0 header, line1 version, line2 count, then RGB rows
            try:
                n = int(lines[2])
            except (ValueError, IndexError):
                n = None
            body = lines[3:] if n is not None else lines
            out = []
            for l in body:
                parts = l.split()
                if len(parts) == 3 and all(x.isdigit() for x in parts):
                    out.append(tuple(int(x) for x in parts))
            if out:
                return out[: n or len(out)]
    # hex list / plain text
    out = []
    for l in lines:
        token = l.split()[0].lstrip("#")
        if len(token) in (3, 6, 8):
            try:
                out.append(I.parse_color("#" + token)[:3])
            except I.ToolError:
                pass
    if not out:
        raise I.ToolError("palette-invalid", f"Cannot parse palette: {path}",
                            "Supported: .json .gpl .pal .hex .txt .act .ase or an image.")
    return out


def _read_ase(data):
    I = _img()
    if data[:4] != b"ASEF":
        raise I.ToolError("palette-invalid", "Not an ASE file (missing ASEF signature).")
    nblocks = struct.unpack(">I", data[8:12])[0]
    pos = 12
    out = []
    for _ in range(nblocks):
        if pos + 6 > len(data):
            break
        btype, blen = struct.unpack(">HI", data[pos:pos + 6])
        body = data[pos + 6:pos + 6 + blen]
        pos += 6 + blen
        if btype != 0x0001:
            continue
        nlen = struct.unpack(">H", body[:2])[0]
        model = body[2 + nlen * 2:2 + nlen * 2 + 4]
        vals = body[2 + nlen * 2 + 4:]
        if model == b"RGB ":
            r, g, b = struct.unpack(">fff", vals[:12])
            out.append(tuple(max(0, min(255, round(v * 255))) for v in (r, g, b)))
    if not out:
        raise I.ToolError("palette-invalid", "ASE contains no RGB colors.")
    return out


def write_palette(pal, path, ctx, name="pixel-art-media palette"):
    I = _img()
    pal = [tuple(c)[:3] for c in pal]
    ext = Path(path).suffix.lower()
    if ext in (".png", ".gif", ".bmp"):
        # Indexed swatch: palette order is preserved exactly on roundtrip.
        cols_n = min(16, max(1, len(pal)))
        rows = math.ceil(len(pal) / cols_n)
        img = Image.new("P", (cols_n, rows))
        flat = []
        for c in pal:
            flat.extend(c)
        flat.extend([0] * (768 - len(flat)))
        img.putpalette(flat)
        img.putdata([i if i < len(pal) else 0 for i in range(cols_n * rows)])
        return I.save_image(img, path, ctx)
    if ext == ".json":
        return I.write_json({"name": name, "colors": [I.hex6(c) for c in pal]}, path, ctx)
    if ext == ".gpl":
        lines = ["GIMP Palette", f"Name: {name}", f"Columns: {min(16, len(pal))}", "#"]
        for i, c in enumerate(pal):
            lines.append(f"{c[0]:3d} {c[1]:3d} {c[2]:3d}\tcolor-{i}")
        return I.write_text("\n".join(lines) + "\n", path, ctx)
    if ext == ".pal":
        lines = ["JASC-PAL", "0100", str(len(pal))]
        lines += [f"{c[0]} {c[1]} {c[2]}" for c in pal]
        return I.write_text("\n".join(lines) + "\n", path, ctx)
    if ext == ".act":
        if len(pal) > 256:
            raise I.ToolError("palette-too-big", "ACT supports at most 256 colors.")
        data = bytearray()
        for c in pal:
            data.extend(c)
        data.extend(b"\x00" * (768 - len(data)))
        data += struct.pack(">HH", len(pal), 0)
        return I.atomic_write(ctx, path, lambda tmp: Path(tmp).write_bytes(bytes(data)))
    if ext == ".ase":
        blocks = bytearray()
        for i, c in enumerate(pal):
            nm = f"color-{i}".encode("utf-16-be") + b"\x00\x00"
            body = struct.pack(">H", len(nm) // 2) + nm + b"RGB "
            body += struct.pack(">fff", c[0] / 255, c[1] / 255, c[2] / 255)
            body += struct.pack(">H", 0)
            blocks += struct.pack(">HI", 1, len(body)) + body
        data = b"ASEF" + struct.pack(">HHI", 1, 0, len(pal)) + bytes(blocks)
        return I.atomic_write(ctx, path, lambda tmp: Path(tmp).write_bytes(data))
    if ext in (".hex", ".txt"):
        return I.write_text("\n".join(I.hex6(c) for c in pal) + "\n", path, ctx)
    raise I.ToolError("bad-palette-format", f"Unsupported palette output extension: {ext}",
                      "Use .json .gpl .pal .hex .txt .act .ase .png")


# --------------------------------------------------------------- generation -

MOODS = {
    "warm": {"hue": 0.07, "shadow_hue": 0.70, "sat": 0.75},
    "cool": {"hue": 0.58, "shadow_hue": 0.66, "sat": 0.60},
    "forest": {"hue": 0.32, "shadow_hue": 0.45, "sat": 0.55},
    "desert": {"hue": 0.10, "shadow_hue": 0.02, "sat": 0.55},
    "neon": {"hue": 0.85, "shadow_hue": 0.60, "sat": 0.95},
    "pastel": {"hue": 0.55, "shadow_hue": 0.70, "sat": 0.35},
    "horror": {"hue": 0.35, "shadow_hue": 0.75, "sat": 0.35},
    "cozy": {"hue": 0.08, "shadow_hue": 0.95, "sat": 0.55},
}

SCHEMES = {"mono": [0.0], "analogous": [0.0, 0.083, -0.083],
           "complementary": [0.0, 0.5], "triadic": [0.0, 0.333, -0.333],
           "split": [0.0, 0.417, -0.417]}


def generate_ramp(base_rgb, steps=5, shadow_hue=None, sat=0.7, hue_drift=0.06):
    """Value ramp with hue shifting: shadows toward shadow_hue, lights warm."""
    h, s, v = colorsys.rgb_to_hsv(*[x / 255 for x in base_rgb])
    if shadow_hue is None:
        shadow_hue = (h + 0.55) % 1.0
    ramp = []
    for i in range(steps):
        t = i / max(1, steps - 1)  # 0 dark -> 1 light
        vv = 0.18 + 0.78 * t
        ss = max(0.05, min(1.0, sat * (1.15 - 0.55 * t)))
        if t < 0.5:
            k = (0.5 - t) * 2 * 0.35
            hh = (h * (1 - k) + shadow_hue * k) % 1.0
        else:
            hh = (h + hue_drift * (t - 0.5)) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hh, ss, vv)
        ramp.append((round(r * 255), round(g * 255), round(b * 255)))
    return ramp


def generate_palette(base="#c06030", mood=None, scheme="mono", ramps=1, steps=5, seed=7):
    I = _img()
    rng = random.Random(seed)
    if mood:
        if mood not in MOODS:
            raise I.ToolError("bad-arg", f"Unknown mood '{mood}'. Options: {', '.join(MOODS)}")
        m = MOODS[mood]
        base_rgb = tuple(round(x * 255) for x in colorsys.hsv_to_rgb(m["hue"], m["sat"], 0.8))
        shadow = m["shadow_hue"]
        sat = m["sat"]
    else:
        base_rgb = I.parse_color(base)[:3]
        shadow = None
        sat = 0.7
    if scheme not in SCHEMES:
        raise I.ToolError("bad-arg", f"Unknown scheme '{scheme}'. Options: {', '.join(SCHEMES)}")
    h0 = colorsys.rgb_to_hsv(*[x / 255 for x in base_rgb])[0]
    offsets = SCHEMES[scheme]
    pal = []
    for r in range(max(1, ramps)):
        off = offsets[r % len(offsets)] + (rng.uniform(-0.01, 0.01) if r >= len(offsets) else 0)
        hh = (h0 + off) % 1.0
        rgb = tuple(round(x * 255) for x in colorsys.hsv_to_rgb(hh, colorsys.rgb_to_hsv(*[x / 255 for x in base_rgb])[1] or sat, 0.8))
        pal.extend(generate_ramp(rgb, steps, shadow_hue=shadow, sat=sat))
    # neutral grays appended for outlines/UI
    pal.extend([(20, 18, 24), (245, 245, 240)])
    seen, out = set(), []
    for c in pal:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:256]


# ---------------------------------------------------------------- analysis --

CVD_MATRICES = {
    # Vienot/Brettel-style linear approximations (sRGB space, informational)
    "protanopia": [(0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758)],
    "deuteranopia": [(0.625, 0.375, 0.0), (0.70, 0.30, 0.0), (0.0, 0.30, 0.70)],
    "tritanopia": [(0.95, 0.05, 0.0), (0.0, 0.433, 0.567), (0.0, 0.475, 0.525)],
}


def simulate_cvd(c, kind):
    m = CVD_MATRICES[kind]
    return tuple(max(0, min(255, round(sum(m[i][j] * c[j] for j in range(3))))) for i in range(3))


def analyze_palette(pal, image=None):
    I = _img()
    pal = [tuple(c)[:3] for c in pal]
    report = {"size": len(pal), "duplicates": [], "near_duplicates": [],
              "value_steps": [], "cvd": {}, "contrast": {}}
    seen = {}
    for i, c in enumerate(pal):
        if c in seen:
            report["duplicates"].append([seen[c], i, I.hex6(c)])
        else:
            seen[c] = i
    for i in range(len(pal)):
        for j in range(i + 1, len(pal)):
            d = math.dist(pal[i], pal[j])
            if 0 < d < 12:
                report["near_duplicates"].append([i, j, round(d, 2)])
    lums = sorted((I.luminance(c), i) for i, c in enumerate(pal))
    steps = [round(lums[k + 1][0] - lums[k][0], 4) for k in range(len(lums) - 1)]
    report["value_steps"] = {"min": min(steps) if steps else 0,
                             "max": max(steps) if steps else 0,
                             "weak_steps(<0.02)": sum(1 for s in steps if s < 0.02)}
    ratios = [I.contrast_ratio(pal[i], pal[j])
              for i in range(len(pal)) for j in range(i + 1, len(pal))]
    report["contrast"] = {"max_pair_ratio": round(max(ratios), 2) if ratios else 1.0,
                          "pairs_meeting_4.5": sum(1 for r in ratios if r >= 4.5)}
    for kind in CVD_MATRICES:
        sim = [simulate_cvd(c, kind) for c in pal]
        mind = min((math.dist(sim[i], sim[j]) for i in range(len(sim))
                    for j in range(i + 1, len(sim))), default=0)
        collisions = sum(1 for i in range(len(sim)) for j in range(i + 1, len(sim))
                         if math.dist(sim[i], sim[j]) < 16)
        report["cvd"][kind] = {"min_distance": round(mind, 1), "close_pairs(<16)": collisions}
    if image is not None:
        used = {c for c, _ in I.image_colors(image)}
        allowed = set(pal)
        off = sorted(used - allowed)
        report["image"] = {"colors_used": len(used), "off_palette": len(off),
                           "off_palette_examples": [I.hex6(c) for c in off[:12]],
                           "unused_entries": len(allowed - used)}
    issues = []
    if report["duplicates"]:
        issues.append("Remove duplicate palette entries.")
    if report["near_duplicates"]:
        issues.append("Merge near-duplicate colors to strengthen ramps.")
    if report["value_steps"]["weak_steps(<0.02)"] > max(1, len(pal) // 4):
        issues.append("Many weak value steps: increase lightness separation for readability.")
    for kind, r in report["cvd"].items():
        if r["close_pairs(<16)"] > 0:
            issues.append(f"{kind}: {r['close_pairs(<16)']} color pairs may be hard to distinguish; add value or shape cues.")
    if report.get("image", {}).get("off_palette"):
        issues.append("Image contains off-palette colors; re-apply the palette or extend it deliberately.")
    report["issues"] = issues
    return report


# ---------------------------------------------------------------- commands --

def cmd_palette_extract(a, ctx):
    I = _img()
    img = I.load_image(a.input, ctx.cfg)
    cols = I.image_colors(img)
    if not cols:
        raise I.ToolError("empty-image", "Image has no opaque pixels.")
    if len(cols) > a.colors:
        q, pal = I.quantize(img, colors=a.colors)
        cols = I.image_colors(q)
    pal = [c for c, _ in cols][: a.colors]
    if a.sort == "luminance":
        pal.sort(key=I.luminance)
    elif a.sort == "hue":
        pal.sort(key=lambda c: colorsys.rgb_to_hsv(*[x / 255 for x in c])[0])
    write_palette(pal, a.output, ctx, name=Path(a.input).stem)
    return {"output": a.output, "colors": len(pal), "sort": a.sort,
            "source_unique_colors": len(I.image_colors(img))}


def cmd_palette_generate(a, ctx):
    pal = generate_palette(a.base, a.mood, a.scheme, a.ramps, a.steps, a.seed)
    write_palette(pal, a.output, ctx, name=f"{a.mood or a.base} {a.scheme}")
    return {"output": a.output, "colors": len(pal), "mood": a.mood,
            "scheme": a.scheme, "ramps": a.ramps, "steps": a.steps, "seed": a.seed}


def cmd_palette_apply(a, ctx):
    I = _img()
    img = I.load_image(a.input, ctx.cfg)
    palette = read_palette_any(a.palette) if a.palette else None
    if palette and len(palette) > ctx.cfg["limits"]["max_colors"]:
        raise I.ToolError("limit-colors", f"Palette exceeds max_colors={ctx.cfg['limits']['max_colors']}.")
    if a.dither.startswith("bayer"):
        if not palette:
            _, palette = I.quantize(img, colors=a.colors)
        out = I.ordered_dither(img, palette, int(a.dither[-1]), strength=a.strength)
        pal = palette
    else:
        out, pal = I.quantize(img, colors=a.colors, palette=palette, dither=a.dither)
    I.save_image(out, a.output, ctx)
    res = {"output": a.output, "colors_used": len(I.image_colors(out)), "dither": a.dither}
    if a.indexed:
        ref = I.palette_image(pal)
        p = out.convert("RGB").quantize(palette=ref, dither=Image.Dither.NONE)
        alpha = out.getchannel("A")
        # reserve index len(pal) for transparency if room
        if len(pal) < 256:
            tindex = len(pal)
            mask = alpha.point(lambda v: 255 if v < 128 else 0)
            p.paste(tindex, (0, 0), mask)
            flat = list(ref.getpalette())
            flat[tindex * 3:tindex * 3 + 3] = [0, 0, 0]
            p.putpalette(flat)
            p.info["transparency"] = tindex
            I.save_image(p, a.indexed, ctx, transparency=tindex)
            res["indexed_output"] = a.indexed
            res["transparent_index"] = tindex
        else:
            ctx.warn("256-color palette leaves no transparent index; indexed export skipped.")
    return res


def cmd_palette_swap(a, ctx):
    I = _img()
    img = I.load_image(a.input, ctx.cfg)
    src = read_palette_any(a.src)
    dst = read_palette_any(a.dst)
    I.check_op_pixels(ctx.cfg, img.width, img.height)
    out = swap_palette_checked(img, src, dst, a.tolerance)
    I.save_image(out, a.output, ctx)
    return {"output": a.output, "mapped_colors": len(src), "tolerance": a.tolerance}


def swap_palette_checked(img, src, dst, tolerance=0):
    I = _img()
    return I.swap_palette(img, src, dst, tolerance)


def cmd_palette_convert(a, ctx):
    pal = read_palette_any(a.input)
    write_palette(pal, a.output, ctx, name=a.name or Path(a.input).stem)
    return {"input": a.input, "output": a.output, "colors": len(pal)}


def cmd_palette_analyze(a, ctx):
    I = _img()
    p = Path(a.input)
    if p.suffix.lower() in (".png", ".gif", ".bmp", ".webp", ".jpg", ".jpeg"):
        img = I.load_image(a.input, ctx.cfg)
        cols = I.image_colors(img)
        pal = [c for c, _ in cols[:256]]
        report = analyze_palette(pal, image=img)
        report["source"] = a.input
        report["note"] = "Palette taken from image colors (max 256)."
    else:
        pal = read_palette_any(a.input)
        img = I.load_image(a.image, ctx.cfg) if a.image else None
        report = analyze_palette(pal, image=img)
        report["source"] = a.input
    if a.swatches:
        cell = 24
        cols_n = min(16, len(pal))
        rows = math.ceil(len(pal) / cols_n)
        kinds = ["original"] + list(CVD_MATRICES)
        from PIL import ImageDraw
        sheet = Image.new("RGBA", (cols_n * cell, rows * cell * len(kinds)), (24, 24, 28, 255))
        d = ImageDraw.Draw(sheet)
        for k, kind in enumerate(kinds):
            for i, c in enumerate(pal):
                cc = c if kind == "original" else simulate_cvd(c, kind)
                x, y = (i % cols_n) * cell, (k * rows + i // cols_n) * cell
                d.rectangle((x + 1, y + 1, x + cell - 2, y + cell - 2), fill=cc + (255,))
        I.save_image(sheet, a.swatches, ctx)
        report["swatches"] = a.swatches
        report["swatch_rows"] = kinds
    if a.report:
        I.write_json(report, a.report, ctx)
    return report


def cmd_palette_cycle(a, ctx):
    I = _img()
    gen = sibling_anim()
    img = I.load_image(a.input, ctx.cfg)
    palette = read_palette_any(a.palette) if a.palette else None
    q, pal = I.quantize(img, colors=a.colors, palette=palette, dither="none")
    ref = I.palette_image(pal)
    indices = q.convert("RGB").quantize(palette=ref, dither=Image.Dither.NONE)
    alpha = q.getchannel("A")
    start, end = a.start, a.end or len(pal)
    if not (0 <= start < end <= len(pal)):
        raise I.ToolError("bad-range", f"Cycle range [{start},{end}) outside palette size {len(pal)}.")
    span = end - start
    nframes = a.frames or span
    if nframes > ctx.cfg["limits"]["max_frames"]:
        raise I.ToolError("limit-frames", "Too many cycle frames.")
    frames = []
    for f in range(nframes):
        shift = (f if not a.reverse else -f) % span
        newpal = list(pal)
        for i in range(start, end):
            newpal[i] = pal[start + ((i - start + shift) % span)]
        flat = []
        for c in newpal:
            flat.extend(c)
        flat.extend([0] * (768 - len(flat)))
        pimg = indices.copy()
        pimg.putpalette(flat)
        fr = pimg.convert("RGB").convert("RGBA")
        fr.putalpha(alpha)
        frames.append(fr)
    durations = [max(ctx.cfg["defaults"]["gif_min_delay_ms"], round(1000 / a.fps))] * len(frames)
    written = gen.encode_animation(frames, durations, a.output, ctx, loop=0)
    res = {"output": a.output, "frames": len(frames), "cycle_range": [start, end],
            "fps": a.fps, "encoded": written}
    if a.frames_dir:
        for i, fr in enumerate(frames):
            I.save_image(fr, str(Path(a.frames_dir) / f"frame-{i:03d}.png"), ctx)
        res["frames_dir"] = a.frames_dir
    return res


def sibling_anim():
    I = _img()
    return I.sibling("sheets")


def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    p = add("palette-extract", "Extract a representative palette from an image", cmd_palette_extract)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--colors", type=int, default=16)
    p.add_argument("--sort", default="frequency", choices=["frequency", "luminance", "hue"])

    p = add("palette-generate", "Generate hue-shifted ramps from mood/base color rules", cmd_palette_generate)
    p.add_argument("output")
    p.add_argument("--base", default="#c06030"); p.add_argument("--mood", choices=sorted(MOODS))
    p.add_argument("--scheme", default="mono", choices=sorted(SCHEMES))
    p.add_argument("--ramps", type=int, default=2); p.add_argument("--steps", type=int, default=5)
    p.add_argument("--seed", type=int, default=7)

    p = add("palette-apply", "Reduce/apply a palette with optional dithering and indexed export", cmd_palette_apply)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--palette"); p.add_argument("--colors", type=int, default=16)
    p.add_argument("--dither", default="none", choices=["none", "floyd", "bayer2", "bayer4", "bayer8"])
    p.add_argument("--strength", type=int, default=48)
    p.add_argument("--indexed", help="Also write an indexed PNG with transparent index here")

    p = add("palette-swap", "Index-aligned palette swap (teams, costumes, variants)", cmd_palette_swap)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--src", required=True, help="Source palette file")
    p.add_argument("--dst", required=True, help="Destination palette file (same length)")
    p.add_argument("--tolerance", type=int, default=0, help="Leave colors farther than this unchanged")

    p = add("palette-convert", "Convert palette formats: json/gpl/pal/hex/act/ase/png", cmd_palette_convert)
    p.add_argument("input"); p.add_argument("output"); p.add_argument("--name")

    p = add("palette-analyze", "Audit ramps, duplicates, contrast, and color-vision safety", cmd_palette_analyze)
    p.add_argument("input", help="Palette file or image")
    p.add_argument("--image", help="Check this image against the palette")
    p.add_argument("--report", help="Write JSON report here")
    p.add_argument("--swatches", help="Write original + CVD-simulated swatch sheet here")

    p = add("palette-cycle", "Animate water/fire/neon by cycling a palette range", cmd_palette_cycle)
    p.add_argument("input"); p.add_argument("output", help=".gif / .png (APNG) / .webp")
    p.add_argument("--palette"); p.add_argument("--colors", type=int, default=16)
    p.add_argument("--start", type=int, default=0); p.add_argument("--end", type=int)
    p.add_argument("--frames", type=int); p.add_argument("--fps", type=int, default=10)
    p.add_argument("--reverse", action="store_true"); p.add_argument("--frames-dir")
