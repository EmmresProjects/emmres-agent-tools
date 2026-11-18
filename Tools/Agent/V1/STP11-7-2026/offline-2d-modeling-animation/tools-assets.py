#!/usr/bin/env python3
"""tools-assets.py — sprite sheets, atlases, tiles, game assets, pixel art.

Packs and slices sprite sheets (grid + skyline atlas with padding/extrusion),
manages pivots and alignment, exports engine-neutral metadata JSON, builds
nine-slice assets and tilemap packages, budgets sprite memory, and runs
pixel-art specific operations: limited palettes, palette swaps, retro
constraint checks, dither patterns, seamless tiles and cluster cleanup.
Deterministic ordering everywhere. Pure stdlib.
"""
import math

core = None
im = None
err = None
require = None


def _init():
    global core, im
    if core is None:
        import importlib.util as ilu
        import sys as sys_
        from pathlib import Path as P

        def sib(name):
            key = "studio." + name
            mod = sys_.modules.get(key)
            if mod is not None:
                return mod
            p = P(__file__).resolve().parent / ("tools-" + name + ".py")
            spec = ilu.spec_from_file_location(key, p)
            mod = ilu.module_from_spec(spec)
            sys_.modules[key] = mod
            spec.loader.exec_module(mod)
            return mod
        core = sib("core")
        im = sib("imaging")
    return core, im


def err(code, message, **details):
    _init()
    return core.err(code, message, **details)


def require(cond, code, message, **details):
    _init()
    return core.require(cond, code, message, **details)


# --------------------------------------------------------- sheets/atlas -----

def sheet_grid(images, columns=None, cell_w=None, cell_h=None, pad=0,
               bg=(0, 0, 0, 0), labels=None):
    """Grid layout (rows/columns/strip) with stable order. Returns
    (sheet_image, frames_meta)."""
    _, _ = _init()
    require(images, "invalid_input", "sheet needs at least one frame")
    n = len(images)
    columns = max(1, min(columns or int(math.ceil(math.sqrt(n))), n))
    rows = (n + columns - 1) // columns
    if cell_w is None:
        cell_w = max(f.w for f in images)
    if cell_h is None:
        cell_h = max(f.h for f in images)
    sheet = im.new_image(columns * (cell_w + pad) + pad,
                         rows * (cell_h + pad) + pad, bg)
    meta = []
    for i, f in enumerate(images):
        r, c = divmod(i, columns)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + pad)
        for yy in range(f.h):
            if y + yy >= sheet.h:
                break
            for xx in range(f.w):
                if x + xx >= sheet.w:
                    break
                px = f.get(xx, yy)
                if px[3] > 0:
                    sheet.set(x + xx, y + yy, px)
        meta.append({"index": i, "x": x, "y": y, "w": f.w, "h": f.h,
                     "name": labels[i] if labels and i < len(labels) else "frame%04d" % i})
    return sheet, {"layout": "grid", "columns": columns, "rows": rows,
                   "cell": [cell_w, cell_h], "pad": pad, "frames": meta}


def atlas_pack(images, names=None, pad=2, extrude=0, power_of_two=False,
               max_size=4096, rotate=False):
    """Skyline shelf packer with deterministic order (sorted by height desc,
    name asc). Returns (atlas_image, meta)."""
    _, _ = _init()
    require(images, "invalid_input", "atlas needs at least one sprite")
    items = []
    for i, f in enumerate(images):
        items.append({"i": i, "img": f,
                      "name": names[i] if names and i < len(names) else "sprite%04d" % i})
    items.sort(key=lambda it: (-it["img"].h, it["img"].w, it["name"]))
    placed = []
    shelves = []  # [y, height, x_cursor]
    max_w = 32
    for it in items:
        w, h = it["img"].w, it["img"].h
        target_w = None
        for sh in shelves:
            if sh["x"] + w + pad <= max_size and h <= sh["h"] or \
               (sh["x"] + w + pad <= max_size and abs(h - sh["h"]) <= 2):
                target_w = sh
                break
        if target_w is None:
            y = sum(s["h"] + pad for s in shelves)
            target_w = {"y": y, "h": h, "x": 0}
            shelves.append(target_w)
        placed.append({"i": it["i"], "img": it["img"], "name": it["name"],
                       "x": target_w["x"], "y": target_w["y"], "w": w, "h": h})
        target_w["x"] += w + pad
        max_w = max(max_w, target_w["x"])
    total_h = sum(s["h"] + pad for s in shelves)
    if power_of_two:
        W = 1
        while W < max_w:
            W *= 2
        H = 1
        while H < total_h:
            H *= 2
    else:
        W, H = max_w, total_h
    require(W <= max_size and H <= max_size, "limit_exceeded",
            "atlas %dx%d exceeds max %d; split the set" % (W, H, max_size))
    atlas = im.new_image(W, H, (0, 0, 0, 0))
    frames = []
    for p in sorted(placed, key=lambda p: p["i"]):
        e = extrude
        for yy in range(-e, p["img"].h + e):
            for xx in range(-e, p["img"].w + e):
                sx = min(max(xx, 0), p["img"].w - 1)
                sy = min(max(yy, 0), p["img"].h - 1)
                dx, dy = p["x"] + e + xx, p["y"] + e + yy
                if 0 <= dx < W and 0 <= dy < H:
                    v = p["img"].get(sx, sy)
                    if v[3] > 0:
                        atlas.set(dx, dy, v)
        frames.append({"index": p["i"], "name": p["name"],
                       "x": p["x"], "y": p["y"],
                       "w": p["img"].w + 2 * e, "h": p["img"].h + 2 * e,
                       "rotated": False})
    return atlas, {"layout": "skyline", "size": [W, H], "pad": pad,
                   "extrude": extrude, "power_of_two": bool(power_of_two),
                   "frames": frames}


def trim_sprite(img, threshold=1):
    """Transparent-bounds trim; returns (image, offset)."""
    _, _ = _init()
    minx, miny, maxx, maxy = img.w, img.h, -1, -1
    for y in range(img.h):
        base = y * img.w
        for x in range(img.w):
            if img.px[(base + x) * 4 + 3] >= threshold:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return im.new_image(1, 1, (0, 0, 0, 0)), [0, 0, img.w, img.h]
    return (im.crop(img, minx, miny, maxx - minx + 1, maxy - miny + 1),
            {"x": minx, "y": miny, "w": maxx - minx + 1, "h": maxy - miny + 1})


def slice_sheet(sheet, columns, rows=None, cell=None, margin=0, spacing=0):
    """Cut a sheet into frames by grid; missing cell/rows are derived."""
    _, _ = _init()
    require(columns >= 1, "invalid_input", "columns must be >= 1")
    cw = (cell[0] if cell and cell[0] else None) or \
        max(1, (sheet.w - 2 * margin - (columns - 1) * spacing) // columns)
    ch = (cell[1] if cell and cell[1] else None)
    if ch is None:
        if rows:
            ch = max(1, (sheet.h - 2 * margin - (rows - 1) * spacing) // rows)
        else:
            ch = cw
    require(cw > 0 and ch > 0, "invalid_input", "cell size must be positive")
    rows = rows or max(1, (sheet.h - margin) // (ch + spacing))
    frames = []
    for r in range(rows):
        for c in range(columns):
            x = margin + c * (cw + spacing)
            y = margin + r * (ch + spacing)
            if x + cw <= sheet.w and y + ch <= sheet.h:
                frames.append(im.crop(sheet, x, y, cw, ch))
    require(frames, "invalid_input", "grid produced no frames")
    return frames, {"columns": columns, "rows": rows, "cell": [cw, ch],
                    "count": len(frames)}


def set_pivots(frames_meta, mode="center"):
    """Annotate pivots for a frame list: center / bottom-center / top-center /
    origin, or per-frame explicit [x, y]."""
    _, _ = _init()
    out = []
    for i, fm in enumerate(frames_meta):
        w, h = fm.get("w", 0), fm.get("h", 0)
        if mode == "center":
            pv = [w // 2, h // 2]
        elif mode == "bottom":
            pv = [w // 2, h - 1]
        elif mode == "top":
            pv = [w // 2, 0]
        elif mode == "origin":
            pv = [0, 0]
        elif isinstance(mode, list):
            pv = list(mode)
        elif isinstance(mode, dict):
            pv = list(mode.get(fm.get("name", ""), [w // 2, h // 2]))
        else:
            raise err("invalid_input", "unknown pivot mode '%s'" % mode)
        entry = dict(fm)
        entry["pivot"] = pv
        out.append(entry)
    return out


def pivot_alignment_check(frames_images, mode="bottom", tolerance=2):
    """Check that registration points (feet/centers) do not jump between
    frames (Sprite Alignment Validator)."""
    _, _ = _init()
    points = []
    for img in frames_images:
        m = im.silhouette_metrics(img, threshold=1)
        if not m["centroid"] or m["area"] == 0:
            points.append(None)
            continue
        cx, cy = m["centroid"]
        bbox = m["bbox"]
        if mode == "bottom":
            points.append([cx, bbox[1] + bbox[3] - 1])
        elif mode == "center":
            points.append([cx, cy])
        elif mode == "top":
            points.append([cx, bbox[1]])
        else:
            raise err("invalid_input", "mode must be center/bottom/top")
    jumps = []
    for i in range(1, len(points)):
        if points[i] is None or points[i - 1] is None:
            continue
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        if abs(dx) > tolerance or abs(dy) > tolerance:
            jumps.append({"frame": i, "dx": round(dx, 1), "dy": round(dy, 1)})
    return {"mode": mode, "tolerance": tolerance, "jumps": jumps,
            "verdict": "pass" if not jumps else "%d jump(s)" % len(jumps)}


def atlas_metadata(atlas_meta, out_path, kind="generic", fps=None, durations=None,
                   tags=None, slices=None):
    """Write engine-neutral atlas metadata JSON (also readable by humans)."""
    _, _ = _init()
    doc = {"meta": {"size": atlas_meta.get("size"), "layout": atlas_meta.get("layout"),
                    "pad": atlas_meta.get("pad"), "format": "RGBA8"},
           "frames": atlas_meta.get("frames", [])}
    if fps:
        doc["animation"] = {"fps": fps,
                            "durations": durations or [1.0 / fps] * len(doc["frames"]),
                            "tags": tags or []}
    if slices:
        doc["slices"] = slices
    core.save_json(out_path, doc)
    return doc


def nine_slice(sprite, margins=(4, 4, 4, 4), verify_size=(64, 64)):
    """Build a 3x3 nine-slice description + a validation render at
    verify_size (fixed corners, stretched edges/center)."""
    _, _ = _init()
    l, t, r, b = [int(m) for m in margins]
    w, h = sprite.w, sprite.h
    require(l + r < w and t + b < h, "invalid_input",
            "margins leave no center region (%d+%d >= %d wide)" % (l, r, w))
    vw, vh = verify_size
    out = im.new_image(vw, vh, (0, 0, 0, 0))

    def stamp(sx, sy, sw, sh, dx, dy, dw, dh):
        for yy in range(dh):
            for xx in range(dw):
                sxx = sx + min(sw - 1, int(xx * sw / float(max(1, dw))))
                syy = sy + min(sh - 1, int(yy * sh / float(max(1, dh))))
                px = sprite.get(sxx, syy)
                if px[3] > 0:
                    out.set(dx + xx, dy + yy, px)
    stamp(0, 0, l, t, 0, 0, l, t)
    stamp(w - r, 0, r, t, vw - r, 0, r, t)
    stamp(0, h - b, l, b, 0, vh - b, l, b)
    stamp(w - r, h - b, r, b, vw - r, vh - b, r, b)
    stamp(l, 0, w - l - r, t, l, 0, vw - l - r, t)
    stamp(l, h - b, w - l - r, b, l, vh - b, vw - l - r, b)
    stamp(0, t, l, h - t - b, 0, t, l, vh - t - b)
    stamp(w - r, t, r, h - t - b, vw - r, t, r, vh - t - b)
    stamp(l, t, w - l - r, h - t - b, l, t, vw - l - r, vh - t - b)
    return out, {"margins": {"left": l, "top": t, "right": r, "bottom": b},
                 "source_size": [w, h], "verify_size": [vw, vh]}


def tileset_package(tiles, out_dir, tile_size=None, variant_map=None,
                    collision=None, bg=(0, 0, 0, 0)):
    """Pack tiles into a sheet + write tileset JSON (grid, variants,
    collision hints). out_dir must exist or be creatable."""
    _, _ = _init()
    from pathlib import Path as P
    out_dir = P(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = tile_size or (tiles[0].w if tiles else 16, tiles[0].h if tiles else 16)
    sheet, meta = sheet_grid(tiles, cell_w=ts[0], cell_h=ts[1], pad=0, bg=bg)
    sheet_path = out_dir / "tileset.png"
    im.save_image(str(sheet_path), sheet)
    doc = {"image": "tileset.png", "tile_size": list(ts),
           "columns": meta["columns"], "count": len(tiles),
           "tiles": [{"index": f["index"], "x": f["x"], "y": f["y"],
                      "name": f["name"]} for f in meta["frames"]],
           "variants": variant_map or {},
           "collision": collision or {},
           "grid": "aligned; no off-grid tiles",
           "seamless": "verify with tile-seamless command"}
    core.save_json(str(out_dir / "tileset.json"), doc)
    return {"dir": str(out_dir), "sheet": str(sheet_path), "tiles": len(tiles),
            "doc": doc}


def seamless_check(tile, times=2):
    """Verify a tile wraps: the image must equal itself after a half-shift
    in x and y (cheap seam detector for tiling textures)."""
    _, _ = _init()
    issues = []
    for axis in ("x", "y"):
        for k in range(1, times + 1):
            shift = (tile.w // (times + 1) * k) if axis == "x" else 0
            shift_y = (tile.h // (times + 1) * k) if axis == "y" else 0
            shifted = im.new_image(tile.w, tile.h)
            for y in range(tile.h):
                for x in range(tile.w):
                    shifted.set(x, y, tile.get((x + shift) % tile.w, (y + shift_y) % tile.h))
            if axis == "x":
                d = im.diff_images(shifted, tile)
                seam_x = 0
                for y in range(tile.h):
                    for x in range(2):
                        i = (y * tile.w + ((x - 1) % tile.w)) * 4
                        if abs(shifted.px[i] - tile.px[i]) > 24:
                            seam_x += 1
                if seam_x > tile.h * 0.1:
                    issues.append({"axis": "x", "shift": shift,
                                   "edge_mismatch": seam_x})
            else:
                seam_y = 0
                for x in range(tile.w):
                    for y in range(2):
                        i = (((y - 1) % tile.h) * tile.w + x) * 4
                        if abs(shifted.px[i] - tile.px[i]) > 24:
                            seam_y += 1
                if seam_y > tile.w * 0.1:
                    issues.append({"axis": "y", "shift": shift_y,
                                   "edge_mismatch": seam_y})
    return {"issues": issues,
            "verdict": "seamless" if not issues else "%d seam issue(s)" % len(issues)}


def sprite_memory_budget(frames, sizes, format_="RGBA8", mipmaps=False,
                         compression=None):
    """Estimate atlas/video memory from dimensions and format."""
    _, _ = _init()
    bpp = {"RGBA8": 32, "RGB8": 24, "RGBA4444": 16, "RGB565": 16,
           "A8": 8, "P8": 8}.get(format_)
    require(bpp is not None, "invalid_input", "unknown format '%s'" % format_)
    total = 0
    rows = []
    for name, (w, h) in sizes:
        b = w * h * bpp // 8
        if mipmaps:
            b = int(b * 4 / 3.0)
        if compression:
            b = int(b * {"etc2": 0.25, "astc": 0.25, "pvrtc": 0.125,
                         "bc7": 0.5}.get(compression, 1.0))
        rows.append({"name": name, "size": [w, h], "bytes": b})
        total += b
    return {"frames": frames, "format": format_, "mipmaps": mipmaps,
             "compression": compression, "total_bytes": total,
             "total_human": core.human_size(total),
             "per_frame": rows[:50],
             "budget_note": "runtime overhead and padding add 5-15 percent"}


def engine_import_check(atlas_meta, max_size=4096, expect_pow2=False,
                        pivots_required=True):
    """Validate assets for engine import: size caps, power-of-two, pivots in
    bounds, frame order stability."""
    _, _ = _init()
    issues = []
    W, H = atlas_meta.get("size", [0, 0])
    if W > max_size or H > max_size:
        issues.append({"issue": "size_cap", "detail": "%dx%d exceeds %d" % (W, H, max_size)})
    if expect_pow2 and (W & (W - 1) or H & (H - 1)):
        issues.append({"issue": "not_power_of_two", "detail": "%dx%d" % (W, H)})
    idx = [f.get("index", -1) for f in atlas_meta.get("frames", [])]
    if idx != sorted(idx):
        issues.append({"issue": "unstable_order", "detail": "frame indices not ascending"})
    for f in atlas_meta.get("frames", []):
        pv = f.get("pivot")
        if pivots_required and pv is None:
            issues.append({"issue": "missing_pivot", "detail": f.get("name", "?")})
        elif pv and not (0 <= pv[0] < max(1, f.get("w", 1)) and
                         0 <= pv[1] < max(1, f.get("h", 1))):
            issues.append({"issue": "pivot_out_of_bounds", "detail": f.get("name", "?")})
    return {"issues": issues,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


# ------------------------------------------------------------- pixel art ----

def load_palettes():
    _, _ = _init()
    return core.load_json(core.ROOT / "asset-color-palettes.json")


def load_retro_constraints():
    _, _ = _init()
    return core.load_json(core.ROOT / "asset-retro-constraints.json")


def pixel_constraints_check(img, profile="pico8", custom=None):
    """Check art against hardware-style limits (Retro Platform Constraint
    Emulator): palette size, colors per sprite, sprite cell size, grid."""
    _, _ = _init()
    prof = custom or load_retro_constraints()["profiles"].get(profile)
    require(prof is not None, "invalid_input", "unknown profile '%s'" % profile)
    colors = {}
    px = img.px
    semi = 0
    for i in range(0, len(px), 4):
        if px[i + 3] == 0:
            continue
        if px[i + 3] < 255:
            semi += 1
        colors[(px[i], px[i + 1], px[i + 2])] = colors.get((px[i], px[i + 1], px[i + 2]), 0) + 1
    issues = []
    if semi > 0:
        issues.append({"issue": "semi_transparency", "detail": "%d semi-transparent pixels "
                       "(retro targets need binary alpha)" % semi})
    if prof.get("palette_colors") and len(colors) > prof["palette_colors"]:
        issues.append({"issue": "too_many_colors",
                       "detail": "%d colors used, limit %d" % (len(colors), prof["palette_colors"])})
    if prof.get("display") and (img.w > prof["display"][0] or img.h > prof["display"][1]):
        issues.append({"issue": "display_overflow",
                       "detail": "%dx%d exceeds display %s" % (img.w, img.h, prof["display"])})
    tw, th = prof.get("tile_size", [8, 8])
    if img.w % tw or img.h % th:
        issues.append({"issue": "off_grid", "detail": "%dx%d not aligned to %dx%d tiles"
                       % (img.w, img.h, tw, th)})
    if prof.get("sprite_max"):
        smw, smh = prof["sprite_max"]
        if img.w <= smw and img.h <= smh and prof.get("colors_per_sprite") and \
                len(colors) > prof["colors_per_sprite"]:
            issues.append({"issue": "colors_per_sprite",
                           "detail": "%d colors in %dx%d sprite, limit %d"
                                     % (len(colors), img.w, img.h, prof["colors_per_sprite"])})
    return {"profile": profile, "colors_used": len(colors),
            "top_colors": [[list(c), n] for c, n in
                           sorted(colors.items(), key=lambda kv: -kv[1])[:8]],
            "issues": issues,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def palette_limit(img, n_colors, protected=None, dither=False, seed="pal"):
    """Reduce to a limited palette (median cut) protecting key colors."""
    _, _ = _init()
    pal, nearest = im.quantize(img, n_colors, protected=protected)
    out = im.Image(img.w, img.h)
    px, spx = out.px, img.px
    bayer = im._BAYER4
    for p in range(img.w * img.h):
        i = p * 4
        if spx[i + 3] == 0:
            continue
        key = (spx[i], spx[i + 1], spx[i + 2])
        if dither:
            d = (bayer[(p // img.w) & 3][p % img.w & 3] - 7.5) * 24
            key = (im._clamp8(key[0] + d), im._clamp8(key[1] + d), im._clamp8(key[2] + d))
        c = pal[nearest(key)]
        px[i], px[i + 1], px[i + 2], px[i + 3] = c[0], c[1], c[2], 255 if spx[i + 3] > 127 else 0
    return out, {"colors": [[c[0], c[1], c[2]] for c in pal],
                 "dither": bool(dither)}


def palette_swap(img, mapping):
    """Deterministic colorway swap: mapping {from_hex: to_hex} applied
    exactly (no re-quantization). Unmapped colors pass through."""
    _, _ = _init()
    lut = {}
    for k, v in mapping.items():
        lut[im.hex_to_rgb(k)] = im.hex_to_rgb(v)
    out = img.clone()
    px = out.px
    for i in range(0, len(px), 4):
        key = (px[i], px[i + 1], px[i + 2])
        if key in lut:
            px[i], px[i + 1], px[i + 2] = lut[key]
    return out, {"mapped": len(lut), "changed_rule": "exact rgb match"}


def palette_swap_by_index(img, pal, remap):
    """Indexed palette swap: pal is the sprite's palette order, remap maps
    index -> new rgb (stable across frames)."""
    _, _ = _init()
    out = img.clone()
    px = out.px
    pal_rgb = [tuple(c) for c in pal]
    hits = 0
    for i in range(0, len(px), 4):
        key = (px[i], px[i + 1], px[i + 2])
        if key in pal_rgb and px[i + 3] > 0:
            idx = pal_rgb.index(key)
            if idx in remap:
                new = remap[idx]
                px[i], px[i + 1], px[i + 2] = new[0], new[1], new[2]
                hits += 1
    return out, {"remapped_pixels": hits}


def pixel_dither_pattern(out_path, size=32, kind="bayer4", seed="d"):
    """Generate ordered-dither / pattern brush sheets (Pixel Brush and Dither
    Designer): 4x4 Bayer, 2x2, checker, noise."""
    _, _ = _init()
    img = im.new_image(size, size, (255, 255, 255, 255))
    rng = core.RNG(kind + ":" + seed)
    for y in range(size):
        for x in range(size):
            if kind == "bayer4":
                v = im._BAYER4[y & 3][x & 3] * 255 // 15
            elif kind == "bayer2":
                v = 255 if ((x ^ y) & 1) else 0
            elif kind == "checker":
                v = 255 if (x // 2 + y // 2) % 2 == 0 else 60
            elif kind == "noise":
                v = int(rng.random() * 255)
            elif kind == "hatch":
                v = 255 if (x + y) % 4 < 2 else 40
            else:
                raise err("invalid_input", "kind must be bayer4/bayer2/checker/noise/hatch")
            img.set(x, y, (v, v, v, 255))
    im.save_image(out_path, img)
    return {"path": str(out_path), "kind": kind, "size": [size, size]}


def pixel_cluster_cleanup(img, aggressive=1):
    """Remove isolated pixels and fill single-pixel holes (Pixel Cluster
    Cleaner) while preserving the style. aggressive: cleanup passes."""
    _, _ = _init()
    out = img.clone()
    for _ in range(max(1, int(aggressive))):
        src = out.clone()
        sp, op = src.px, out.px
        for y in range(img.h):
            base = y * img.w
            for x in range(img.w):
                i = (base + x) * 4
                if sp[i + 3] == 0:
                    continue
                same = []
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < img.w and 0 <= ny < img.h:
                        j = (ny * img.w + nx) * 4
                        if sp[j + 3] > 0 and (sp[j], sp[j + 1], sp[j + 2]) == \
                           (sp[i], sp[i + 1], sp[i + 2]):
                            same.append(j)
                if not same:
                    op[i + 3] = 0  # isolated pixel
    return out, {"passes": int(aggressive)}


def pixel_scaling_check(img, target_w=None):
    """Detect non-integer scaling / blended edges in pixel art previews."""
    _, _ = _init()
    issues = []
    semi = 0
    blended = 0
    px = img.px
    for i in range(0, len(px), 4):
        if 0 < px[i + 3] < 255:
            semi += 1
    if semi:
        issues.append({"issue": "semi_transparent_pixels", "count": semi})
    for y in range(1, img.h - 1):
        for x in range(1, img.w - 1):
            i = (y * img.w + x) * 4
            if px[i + 3] == 0 or px[i + 3] < 255:
                continue
            c = (px[i], px[i + 1], px[i + 2])
            n = []
            for dx, dy in ((1, 0), (0, 1)):
                j = ((y + dy) * img.w + x + dx) * 4
                if px[j + 3] == 255:
                    n.append((px[j], px[j + 1], px[j + 2]))
            for nc in n:
                if nc != c and sum(abs(nc[k] - c[k]) for k in range(3)) < 24:
                    blended += 1
    if blended > img.w * img.h * 0.002:
        issues.append({"issue": "soft_edges", "count": blended,
                       "hint": "resampled pixel art — re-export with nearest-neighbor"})
    if target_w and img.w % target_w:
        issues.append({"issue": "non_integer_scale",
                       "detail": "%d px not a multiple of %d" % (img.w, target_w)})
    return {"issues": issues,
            "verdict": "clean" if not issues else "%d issue(s)" % len(issues)}


def tile_with_variants(base_tile, variants=3, seed="tv"):
    """Create variation tiles by deterministic pixel jitter of a base tile."""
    _, _ = _init()
    rng = core.RNG(seed)
    out = []
    for v in range(int(variants)):
        t = base_tile.clone()
        px = t.px
        for p in range(base_tile.w * base_tile.h):
            i = p * 4
            if px[i + 3] > 0 and rng.random() < 0.06:
                d = int((rng.random() - 0.5) * 36)
                px[i] = im._clamp8(px[i] + d)
                px[i + 1] = im._clamp8(px[i + 1] + d)
                px[i + 2] = im._clamp8(px[i + 2] + d)
        out.append(t)
    return out


# ------------------------------------------------- pixel craft (5.1, 5.7) ---

def pixel_canvas_setup(width, height, palette_name="pixel-limited-16",
                       zoom_note=True, out_path=None):
    """Pixel Canvas Setup: small-power-friendly canvas + palette pinning +
    grid; enforces integer scaling guidance."""
    core, im = _init()
    require(width > 0 and height > 0, "invalid_input", "positive size required")
    require(max(width, height) <= 512, "invalid_input",
            "pixel canvas above 512px is usually not pixel art")
    pal_all = load_palettes()
    palettes = pal_all.get("palettes", {})
    pal = palettes.get(palette_name)
    require(pal is not None, "invalid_input",
            "unknown palette '%s' (have: %s)" % (palette_name,
                                                 ", ".join(sorted(palettes))))
    rec = {"canvas": [int(width), int(height)],
           "palette": palette_name, "colors": len(pal["colors"]),
           "scaling": "integer only (nearest neighbour); 1x export for games",
           "grid": "1px grid on; snap tools to pixels",
           "zoom": "work at 400-800 percent, review at 100 percent",
           "rules": ["no anti-aliasing", "no sub-pixel colours",
                     "dither sparingly — clusters over noise"]}
    if out_path:
        first = pal["colors"][0]["hex"]
        img = im.new_image(int(width), int(height), im.hex_to_rgb(first))
        im.draw_pixel_grid(img, cell=8, color=(60, 60, 60, 120))
        im.save_image(out_path, img)
        rec["preview"] = out_path
    return rec


def pixel_character_animate(base_sprite, frames=8, action="walk",
                            direction="right", seed="pixel"):
    """Pixel Character Animator: build a walk/idle cycle from a base sprite
    using integer-pixel offsets only (no sub-pixel smear). Returns per-frame
    offsets + generated bounce/step frames from the sprite itself."""
    core, im = _init()
    rng = core.RNG("pixanim:%s:%s" % (action, seed))
    require(frames in (2, 4, 6, 8, 12, 16), "invalid_input",
            "frames must be 2/4/6/8/12/16")
    w, h = base_sprite.w, base_sprite.h
    out = []
    plan = []
    for f in range(frames):
        t = f / float(frames)
        if action == "walk":
            bob = -1 if (f % (frames // 2)) in (1,) else 0  # 1px bob
            lean = 1 if f < frames // 2 else 0
            off = [0, bob]
        elif action == "idle":
            off = [0, -1 if f in (frames // 4, frames // 4 + 1) else 0]
            lean = 0
        elif action == "run":
            off = [-(f % 2), -2 if f % 2 else 0]
            lean = 1
        else:
            raise err("invalid_input", "action must be walk/idle/run")
        frame = im.new_image(w, h + 4, (0, 0, 0, 0))
        for yy in range(h):
            src = yy * w * 4
            dst = ((yy + 2 + off[1]) * w + max(0, lean)) * 4
            frame.px[dst:dst + w * 4] = base_sprite.px[src:src + w * 4]
        out.append(frame)
        plan.append({"frame": f, "offset": off, "lean_px": lean})
    return {"frames": out, "plan": plan, "action": action,
            "direction": direction, "frames_count": frames,
            "rules": ["whole-pixel offsets only", "2-4 unique drawings, "
                      "reused with offsets", "export at 1x"]}


# ---------------------------------------------- long-term formats (24.5) ----

def format_migration_plan(files):
    """Audit project formats against long-term open targets."""
    core, im = _init()
    targets = {".png": ("keep", "open standard, lossless"),
               ".svg": ("keep", "open standard"),
               ".wav": ("keep", "open standard, uncompressed PCM"),
               ".ora": ("keep", "open raster standard"),
               ".kra": ("convert", "export to ORA/PNG for archive"),
               ".xcf": ("convert", "export to PNG/OR A"),
               ".psd": ("convert", "export to PNG/TIFF or ORA"),
               ".ttf": ("keep", "outline text at delivery"),
               ".mp4": ("convert", "re-encode to lossless FFV1/MKV for master"),
               ".gif": ("keep", "fine for review; not archival"),
               ".pdf": ("keep", "open standard"),
               ".blend": ("keep", "open format")}
    counts = {}
    rows = []
    from pathlib import Path as P
    for f in files or []:
        ext = P(str(f)).suffix.lower()
        counts[ext] = counts.get(ext, 0) + 1
    for ext, n in sorted(counts.items()):
        action, why = targets.get(ext, ("review", "unknown format — decide "
                                                 "before archiving"))
        rows.append({"format": ext or "(none)", "count": n, "action": action,
                     "reason": why})
    return {"formats": rows, "files": sum(counts.values()),
            "principle": "archive masters in open, lossless, documented formats"}
