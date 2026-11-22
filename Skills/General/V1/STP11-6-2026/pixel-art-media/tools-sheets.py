"""pixel-art-media: sprite sheets, atlases, animation encoding/decoding.

Frame extraction, grid/auto slicing, deterministic packing with padding and
edge extrusion, multi-format atlas metadata, GIF/APNG/WebP animation export,
image sequences, and Aseprite-compatible JSON exports.
"""
from __future__ import annotations

import csv
import io
import json
import math
import plistlib
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, features


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


# ------------------------------------------------------------- animation io -

def decode_animation(path, cfg):
    """Any still/animated image -> (frames RGBA, durations ms, info)."""
    L = I()
    p = Path(path)
    if not p.is_file():
        raise L.ToolError("input-not-found", f"Input not found: {path}")
    try:
        img = Image.open(p)
    except Exception as e:
        raise L.ToolError("input-unreadable", f"Cannot open {path}: {e}")
    frames, durations = [], []
    n = getattr(img, "n_frames", 1)
    if n > cfg["limits"]["max_frames"]:
        raise L.ToolError("limit-frames", f"{n} frames exceeds max_frames.")
    for i in range(n):
        img.seek(i)
        fr = img.convert("RGBA")
        L.check_dims(cfg, fr.width, fr.height)
        frames.append(fr)
        durations.append(int(img.info.get("duration", 1000 // cfg["defaults"]["fps"]) or 0))
    info = {"format": img.format, "loop": img.info.get("loop"), "n_frames": n}
    return frames, durations, info


def encode_animation(frames, durations, output, ctx, loop=0, disposal=2):
    """Encode RGBA frames to GIF / APNG (.png) / WebP by extension."""
    L = I()
    if not frames:
        raise L.ToolError("no-frames", "No frames to encode.")
    if len(frames) > ctx.cfg["limits"]["max_frames"]:
        raise L.ToolError("limit-frames", "Too many frames to encode.")
    w, h = frames[0].size
    if any(f.size != (w, h) for f in frames):
        raise L.ToolError("frame-size-mismatch", "All frames must share dimensions.",
                          "Use pad or sheet tools to normalize frames first.")
    ext = Path(output).suffix.lower()
    min_ms = ctx.cfg["defaults"]["gif_min_delay_ms"]
    if ext == ".gif":
        # Global palette across sampled frames to avoid inter-frame flicker.
        sample = frames[:: max(1, len(frames) // 16)][:16]
        strip = Image.new("RGB", (w, h * len(sample)))
        for i, f in enumerate(sample):
            strip.paste(f.convert("RGB"), (0, i * h))
        qp = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        pal = qp.getpalette()[: 255 * 3] + [0, 0, 0]
        ref = Image.new("P", (1, 1)); ref.putpalette(pal)
        gframes = []
        for f in frames:
            q = f.convert("RGB").quantize(palette=ref, dither=Image.Dither.NONE)
            mask = f.getchannel("A").point(lambda v: 255 if v < 128 else 0)
            q.paste(255, (0, 0), mask)
            q.putpalette(pal)
            gframes.append(q)
        durs = [max(min_ms, int(round(d / 10.0)) * 10) for d in durations]
        if any(abs(d - md) > 5 for d, md in zip(durations, durs)):
            ctx.warn("GIF delays are 10ms steps (min 20ms); durations were rounded.")

        def writer(tmp):
            gframes[0].save(tmp, format="GIF", save_all=True, append_images=gframes[1:],
                            duration=durs, loop=loop, disposal=disposal, transparency=255,
                            optimize=False)
        L.atomic_write(ctx, output, writer)
        return {"format": "GIF", "frames": len(frames), "durations_ms": durs, "loop": loop}
    if ext == ".png":
        def writer(tmp):
            frames[0].save(tmp, format="PNG", save_all=True, append_images=frames[1:],
                           duration=durations, loop=loop, disposal=0, blend=0, optimize=True)
        L.atomic_write(ctx, output, writer)
        return {"format": "APNG", "frames": len(frames), "durations_ms": durations, "loop": loop}
    if ext == ".webp":
        if not features.check("webp"):
            raise L.ToolError("webp-unavailable", "This Pillow build lacks WebP support.")
        def writer(tmp):
            frames[0].save(tmp, format="WEBP", save_all=True, append_images=frames[1:],
                           duration=durations, loop=loop, lossless=True, quality=100, method=4)
        L.atomic_write(ctx, output, writer)
        return {"format": "WEBP", "frames": len(frames), "durations_ms": durations, "loop": loop}
    raise L.ToolError("bad-output-format", f"Animation extension must be .gif, .png (APNG) or .webp, got {ext}")


def frames_from_input(a, ctx):
    """Resolve --grid slicing, animated files, or frame lists into frames."""
    L = I()
    inputs = a.inputs
    grid = getattr(a, "grid", None)
    if grid:
        if len(inputs) != 1:
            raise L.ToolError("bad-arg", "--grid requires exactly one sheet image.")
        img = L.load_image(inputs[0], ctx.cfg)
        frames, meta = slice_grid(img, grid, getattr(a, "margin", 0), getattr(a, "spacing", 0),
                                  getattr(a, "count", None))
        fps = getattr(a, "fps", None) or ctx.cfg["defaults"]["fps"]
        durs = [1000 // fps] * len(frames)
        return frames, durs, [m["name"] for m in meta]
    files = L.collect_inputs(inputs)
    if len(files) == 1:
        frames, durs, info = decode_animation(str(files[0]), ctx.cfg)
        if len(frames) > 1:
            return frames, durs, [f"frame-{i:03d}" for i in range(len(frames))]
    frames, names = [], []
    for f in files:
        frames.append(L.load_image(str(f), ctx.cfg))
        names.append(f.stem)
    fps = getattr(a, "fps", None) or ctx.cfg["defaults"]["fps"]
    return frames, [1000 // fps] * len(frames), names


# ----------------------------------------------------------------- slicing --

def slice_grid(img, grid, margin=0, spacing=0, count=None):
    L = I()
    try:
        gw, gh = (int(v) for v in grid.lower().split("x"))
    except Exception:
        raise L.ToolError("bad-arg", f"--grid must be WxH, got '{grid}'.")
    if gw < 1 or gh < 1 or margin < 0 or spacing < 0:
        raise L.ToolError("bad-arg", "Grid, margin and spacing must be positive.")
    cols = (img.width - 2 * margin + spacing) // (gw + spacing)
    rows = (img.height - 2 * margin + spacing) // (gh + spacing)
    if cols < 1 or rows < 1:
        raise L.ToolError("grid-mismatch", f"Grid {gw}x{gh} does not fit {img.size} with margin/spacing.")
    frames, meta = [], []
    for r in range(rows):
        for c in range(cols):
            x = margin + c * (gw + spacing)
            y = margin + r * (gh + spacing)
            fr = img.crop((x, y, x + gw, y + gh))
            i = len(frames)
            frames.append(fr)
            meta.append({"name": f"frame-{i:03d}", "x": x, "y": y, "w": gw, "h": gh})
            if count and len(frames) >= count:
                return frames, meta
    return frames, meta


def slice_auto(img, alpha_threshold=8, min_area=4, max_frames=256):
    """Detect sprites separated by transparent gutters or components."""
    L = I()
    mask = img.getchannel("A").point(lambda v: 255 if v > alpha_threshold else 0)
    comps = L.components(mask, connectivity=8, max_components=max_frames + 1)
    comps = [c for c in comps if len(c["pixels"]) >= min_area]
    if not comps:
        raise L.ToolError("nothing-found", "No opaque sprites detected.")
    if len(comps) > max_frames:
        raise L.ToolError("limit-frames", f"{len(comps)} components exceeds {max_frames}.")
    # merge overlapping bboxes (one sprite may have detached pixels)
    boxes = [list(c["bbox"]) for c in comps]
    merged = True
    while merged:
        merged = False
        out = []
        while boxes:
            b = boxes.pop()
            hit = None
            for o in out:
                if not (b[2] <= o[0] or o[2] <= b[0] or b[3] <= o[1] or o[3] <= b[1]):
                    hit = o
                    break
            if hit:
                hit[0] = min(hit[0], b[0]); hit[1] = min(hit[1], b[1])
                hit[2] = max(hit[2], b[2]); hit[3] = max(hit[3], b[3])
                merged = True
            else:
                out.append(b)
        boxes = out
    boxes.sort(key=lambda b: (b[1] // max(1, img.height // 8), b[0]))
    frames, meta = [], []
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        frames.append(img.crop((x0, y0, x1, y1)))
        meta.append({"name": f"sprite-{i:03d}", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
    return frames, meta


# ----------------------------------------------------------------- packing --

def pack_frames(frames, names, durations=None, layout="packed", cols=0, padding=1,
                extrude=0, pot=False, max_width=1024, do_trim=False, pivot=(0.5, 1.0),
                cfg=None):
    """Deterministic atlas packing. Returns (sheet RGBA, metadata dict)."""
    L = I()
    if not frames:
        raise L.ToolError("no-frames", "No frames to pack.")
    if len(frames) > cfg["limits"]["max_frames"]:
        raise L.ToolError("limit-frames", "Too many frames to pack.")
    durations = durations or [100] * len(frames)
    items = []
    for i, f in enumerate(frames):
        src_w, src_h = f.size
        if do_trim:
            tf, ox, oy, _ = L.trim(f, padding=0)
        else:
            tf, ox, oy = f, 0, 0
        items.append({"i": i, "img": tf, "name": names[i], "ox": ox, "oy": oy,
                      "sw": src_w, "sh": src_h, "w": tf.width, "h": tf.height,
                      "duration": durations[i]})
    cell_pad = max(padding, extrude)
    if layout in ("horizontal", "vertical"):
        cols_n = len(items) if layout == "horizontal" else 1
    elif layout == "grid":
        cols_n = max(1, cols or math.ceil(math.sqrt(len(items))))
    else:
        cols_n = 0  # shelf packed
    if layout == "packed":
        order = sorted(items, key=lambda it: (-it["h"], -it["w"], it["name"]))
        x = y = shelf_h = 0
        width_used = 0
        for it in order:
            iw, ih = it["w"] + 2 * cell_pad, it["h"] + 2 * cell_pad
            if x + iw > max_width and x > 0:
                x = 0
                y += shelf_h + 0
                shelf_h = 0
            it["x"], it["y"] = x + cell_pad, y + cell_pad
            x += iw
            shelf_h = max(shelf_h, ih)
            width_used = max(width_used, x)
        sheet_w, sheet_h = width_used, y + shelf_h
    else:
        cw = max(it["w"] for it in items) + 2 * cell_pad
        ch = max(it["h"] for it in items) + 2 * cell_pad
        rows_n = math.ceil(len(items) / cols_n)
        for k, it in enumerate(items):
            r, c = divmod(k, cols_n)
            it["x"] = c * cw + cell_pad + (cw - 2 * cell_pad - it["w"]) // 2
            it["y"] = r * ch + cell_pad + (ch - 2 * cell_pad - it["h"]) // 2
        sheet_w, sheet_h = cols_n * cw, rows_n * ch
    if pot:
        sheet_w = 1 << (max(1, sheet_w) - 1).bit_length()
        sheet_h = 1 << (max(1, sheet_h) - 1).bit_length()
    if sheet_w < 1 or sheet_h < 1:
        raise L.ToolError("pack-failed", "Computed empty atlas.")
    L.check_dims(cfg, sheet_w, sheet_h)
    if sheet_w > cfg["limits"]["max_sheet_side"] or sheet_h > cfg["limits"]["max_sheet_side"]:
        raise L.ToolError("limit-sheet", f"Atlas {sheet_w}x{sheet_h} exceeds max_sheet_side.",
                          "Lower --max-width, split inputs, or disable power-of-two.")
    sheet = Image.new("RGBA", (sheet_w, sheet_h))
    for it in items:
        sheet.alpha_composite(it["img"], (it["x"], it["y"]))
        if extrude > 0:
            x, y, w, h = it["x"], it["y"], it["w"], it["h"]
            for e in range(1, extrude + 1):
                sheet.paste(sheet.crop((x, y, x + w, y + 1)), (x, y - e))
                sheet.paste(sheet.crop((x, y + h - 1, x + w, y + h)), (x, y + h - 1 + e))
                sheet.paste(sheet.crop((x, y, x + 1, y + h)), (x - e, y))
                sheet.paste(sheet.crop((x + w - 1, y, x + w, y + h)), (x + w - 1 + e, y))
    items.sort(key=lambda it: it["i"])
    meta_frames = []
    for it in items:
        meta_frames.append({
            "name": it["name"], "x": it["x"], "y": it["y"], "w": it["w"], "h": it["h"],
            "duration": it["duration"], "trimmed": bool(do_trim),
            "offset": [it["ox"], it["oy"]], "source_size": [it["sw"], it["sh"]],
            "pivot": list(pivot)})
    meta = {"image_size": [sheet_w, sheet_h], "layout": layout, "padding": padding,
            "extrude": extrude, "power_of_two": pot, "frames": meta_frames}
    return sheet, meta


def meta_to_format(meta, image_name, fmt, app="pixel-art-media", tags=None, scale=1):
    L = I()
    w, h = meta["image_size"]
    if fmt in ("json-hash", "phaser", "pixi", "texturepacker"):
        frames = {}
        for f in meta["frames"]:
            frames[f["name"]] = {
                "frame": {"x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"]},
                "rotated": False, "trimmed": f["trimmed"],
                "spriteSourceSize": {"x": f["offset"][0], "y": f["offset"][1],
                                     "w": f["w"], "h": f["h"]},
                "sourceSize": {"w": f["source_size"][0], "h": f["source_size"][1]},
                "pivot": {"x": f["pivot"][0], "y": f["pivot"][1]},
                "duration": f["duration"]}
        return {"frames": frames,
                "meta": {"app": app, "version": "1.0", "image": image_name,
                         "format": "RGBA8888", "size": {"w": w, "h": h}, "scale": str(scale)}}
    if fmt in ("json-array", "aseprite"):
        frames = []
        for i, f in enumerate(meta["frames"]):
            frames.append({
                "filename": f["name"],
                "frame": {"x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"]},
                "rotated": False, "trimmed": f["trimmed"],
                "spriteSourceSize": {"x": f["offset"][0], "y": f["offset"][1],
                                     "w": f["w"], "h": f["h"]},
                "sourceSize": {"w": f["source_size"][0], "h": f["source_size"][1]},
                "duration": f["duration"]})
        m = {"app": app, "version": "1.0", "image": image_name, "format": "RGBA8888",
             "size": {"w": w, "h": h}, "scale": str(scale),
             "layers": [{"name": "Layer 1", "opacity": 255, "blendMode": "normal"}]}
        if tags:
            m["frameTags"] = tags
        return {"frames": frames, "meta": m}
    if fmt == "json-simple":
        return meta
    if fmt == "csv":
        buf = io.StringIO()
        wr = csv.writer(buf)
        wr.writerow(["name", "x", "y", "w", "h", "offset_x", "offset_y",
                     "source_w", "source_h", "pivot_x", "pivot_y", "duration_ms"])
        for f in meta["frames"]:
            wr.writerow([f["name"], f["x"], f["y"], f["w"], f["h"],
                         f["offset"][0], f["offset"][1],
                         f["source_size"][0], f["source_size"][1],
                         f["pivot"][0], f["pivot"][1], f["duration"]])
        return buf.getvalue()
    if fmt == "xml":
        root = ET.Element("TextureAtlas", {"imagePath": image_name,
                                           "width": str(w), "height": str(h)})
        for f in meta["frames"]:
            ET.SubElement(root, "SubTexture", {
                "name": f["name"], "x": str(f["x"]), "y": str(f["y"]),
                "width": str(f["w"]), "height": str(f["h"]),
                "frameX": str(-f["offset"][0]), "frameY": str(-f["offset"][1]),
                "frameWidth": str(f["source_size"][0]),
                "frameHeight": str(f["source_size"][1])})
        ET.indent(root)
        return ET.tostring(root, encoding="unicode") + "\n"
    if fmt == "plist":
        frames = {}
        for f in meta["frames"]:
            frames[f["name"]] = {
                "frame": f"{{{{{f['x']},{f['y']}}},{{{f['w']},{f['h']}}}}}",
                "offset": f"{{{f['offset'][0]},{f['offset'][1]}}}",
                "rotated": False,
                "sourceSize": f"{{{f['source_size'][0]},{f['source_size'][1]}}}",
                "sourceColorRect": f"{{{{{f['offset'][0]},{f['offset'][1]}}},{{{f['w']},{f['h']}}}}}"}
        obj = {"frames": frames,
               "metadata": {"format": 2, "realTextureFileName": image_name,
                            "size": f"{{{w},{h}}}", "textureFileName": image_name}}
        return plistlib.dumps(obj).decode("utf-8")
    raise L.ToolError("bad-arg", f"Unknown metadata format '{fmt}'.",
                      "Use json-hash, json-array, aseprite, json-simple, csv, xml, plist.")


def write_meta(meta_text_or_obj, path, ctx):
    L = I()
    if isinstance(meta_text_or_obj, str):
        return L.write_text(meta_text_or_obj, path, ctx)
    return L.write_json(meta_text_or_obj, path, ctx)


# ---------------------------------------------------------------- commands --

def cmd_sheet_pack(a, ctx):
    L = I()
    frames, durations, names = frames_from_input(a, ctx)
    pivot = tuple(float(v) for v in a.pivot.split(","))
    if len(pivot) != 2 or not all(0 <= v <= 1 for v in pivot):
        raise L.ToolError("bad-arg", "--pivot must be two floats in [0,1], e.g. 0.5,1")
    sheet, meta = pack_frames(frames, names, durations, a.layout, a.cols, a.padding,
                              a.extrude, a.power_of_two, a.max_width, a.trim, pivot, ctx.cfg)
    L.save_image(sheet, a.output, ctx)
    image_name = Path(a.output).name
    result = {"output": a.output, "frames": len(frames), "size": meta["image_size"],
              "layout": a.layout}
    tags = None
    if a.tags_json:
        tags = L.read_json(a.tags_json).get("tags", [])
    if a.meta:
        fmt = a.meta_format
        obj = meta_to_format(meta, image_name, fmt, tags=tags)
        write_meta(obj, a.meta, ctx)
        result["metadata"] = a.meta
        result["metadata_format"] = fmt
    return result


def cmd_sheet_slice(a, ctx):
    L = I()
    img = L.load_image(a.input, ctx.cfg)
    if a.grid:
        frames, meta = slice_grid(img, a.grid, a.margin, a.spacing, a.count)
        mode = "grid"
    else:
        frames, meta = slice_auto(img, max_frames=ctx.cfg["limits"]["max_frames"])
        mode = "auto"
    outdir = Path(a.outdir)
    manifest = {"source": a.input, "mode": mode, "frames": []}
    for fr, m in zip(frames, meta):
        if a.trim:
            fr, ox, oy, orig = L.trim(fr)
            m["trim_offset"] = [ox, oy]
            m["original_size"] = list(orig)
        name = m["name"] + ".png"
        L.save_image(fr, str(outdir / name), ctx)
        m["file"] = name
        manifest["frames"].append(m)
    L.write_json(manifest, str(outdir / "frames.json"), ctx)
    return {"outdir": a.outdir, "frames": len(frames), "mode": mode,
            "manifest": str(outdir / "frames.json")}


def cmd_atlas_meta(a, ctx):
    L = I()
    meta = L.read_json(a.input)
    if "frames" not in meta or "image_size" not in meta:
        raise L.ToolError("bad-meta", "Input must be a json-simple atlas manifest (from sheet-pack).")
    obj = meta_to_format(meta, a.image_name or "sheet.png", a.format)
    write_meta(obj, a.output, ctx)
    return {"output": a.output, "format": a.format, "frames": len(meta["frames"])}


def cmd_anim_encode(a, ctx):
    L = I()
    frames, durations, names = frames_from_input(a, ctx)
    if a.scale > 1:
        frames = [L.nearest_scale(f, a.scale) for f in frames]
    if a.reverse:
        frames, durations = frames[::-1], durations[::-1]
    if a.pingpong and len(frames) > 2:
        frames = frames + frames[-2:0:-1]
        durations = durations + durations[-2:0:-1]
    if a.fps:
        durations = [1000 // a.fps] * len(frames)
    if a.hold and a.hold > 1:
        durations = [d * a.hold for d in durations]
    L.check_dims(ctx.cfg, frames[0].width, frames[0].height, frames=len(frames))
    info = encode_animation(frames, durations, a.output, ctx, loop=a.loop)
    return {"output": a.output, "input_frames": len(names), **info,
            "size": list(frames[0].size)}


def cmd_anim_decode(a, ctx):
    L = I()
    frames, durations, info = decode_animation(a.input, ctx.cfg)
    outdir = Path(a.outdir)
    manifest = {"source": a.input, **info, "frames": []}
    for i, (fr, d) in enumerate(zip(frames, durations)):
        name = f"frame-{i:03d}.png"
        L.save_image(fr, str(outdir / name), ctx)
        manifest["frames"].append({"file": name, "duration_ms": d})
    L.write_json(manifest, str(outdir / "timing.json"), ctx)
    return {"outdir": a.outdir, "frames": len(frames), **info}


def cmd_sequence_export(a, ctx):
    L = I()
    frames, durations, names = frames_from_input(a, ctx)
    if a.scale > 1:
        frames = [L.nearest_scale(f, a.scale) for f in frames]
    ext = "." + a.format
    outdir = Path(a.outdir)
    manifest = {"frames": [], "fps": a.fps, "format": a.format}
    for i, (fr, d) in enumerate(zip(frames, durations)):
        name = f"{a.prefix}{i:04d}{ext}"
        L.save_image(fr, str(outdir / name), ctx)
        manifest["frames"].append({"file": name, "duration_ms": d})
    L.write_json(manifest, str(outdir / "sequence.json"), ctx)
    return {"outdir": a.outdir, "frames": len(frames), "format": a.format,
            "pattern": f"{a.prefix}%04d{ext}"}


def cmd_aseprite_export(a, ctx):
    L = I()
    frames, durations, names = frames_from_input(a, ctx)
    sheet, meta = pack_frames(frames, names, durations, a.layout, a.cols, a.padding,
                              0, False, a.max_width, False, (0.5, 0.5), ctx.cfg)
    L.save_image(sheet, a.output, ctx)
    tags = [{"name": a.tag, "from": 0, "to": len(frames) - 1, "direction": "forward"}]
    if a.tags_json:
        tags = L.read_json(a.tags_json).get("tags", tags)
    obj = meta_to_format(meta, Path(a.output).name, "aseprite", tags=tags)
    json_path = str(Path(a.output).with_suffix(".json"))
    L.write_json(obj, json_path, ctx)
    return {"sheet": a.output, "json": json_path, "frames": len(frames), "tags": tags,
            "note": "Aseprite-compatible JSON (import via Aseprite's JSON data or engine loaders)."}


def cmd_tags(a, ctx):
    """Create/edit/validate animation tag, pivot, and event sidecars."""
    L = I()
    p = Path(a.file)
    if a.init:
        if not a.frames or not a.size:
            raise L.ToolError("bad-arg", "--init needs --frames N and --size WxH.")
        try:
            w, h = (int(v) for v in a.size.lower().split("x"))
        except Exception:
            raise L.ToolError("bad-arg", "--size must be WxH.")
        side = {"frames": a.frames, "size": [w, h], "fps": a.fps,
                "pivots": {}, "tags": [], "events": []}
        L.write_json(side, a.file, ctx)
        return {"file": a.file, "created": True, **side}
    side = L.read_json(a.file)
    changed = False
    if a.add_tag:
        try:
            parts = a.add_tag.split(":")
            name, f0, f1 = parts[0], int(parts[1]), int(parts[2])
            direction = parts[3] if len(parts) > 3 else "forward"
        except Exception:
            raise L.ToolError("bad-arg", "--add-tag name:from:to[:forward|reverse|pingpong]")
        if direction not in ("forward", "reverse", "pingpong"):
            raise L.ToolError("bad-arg", "tag direction must be forward|reverse|pingpong.")
        side.setdefault("tags", []).append({"name": name, "from": f0, "to": f1,
                                            "direction": direction})
        changed = True
    if a.add_event:
        try:
            parts = a.add_event.split(":", 2)
            frame, name = int(parts[0]), parts[1]
            data = parts[2] if len(parts) > 2 else ""
        except Exception:
            raise L.ToolError("bad-arg", "--add-event frame:name[:data]")
        side.setdefault("events", []).append({"frame": frame, "name": name, "data": data})
        changed = True
    if a.set_pivot:
        try:
            name, xy = a.set_pivot.split(":", 1)
            x, y = (float(v) for v in xy.split(","))
        except Exception:
            raise L.ToolError("bad-arg", "--set-pivot name:x,y (normalized 0..1)")
        side.setdefault("pivots", {})[name] = [x, y]
        changed = True
    if changed:
        if not ctx.force and not ctx.dry_run:
            raise L.ToolError("output-exists", f"{a.file} exists; editing a sidecar in place needs --force.",
                              "Re-run with --force to approve the in-place edit.")
        L.write_json(side, a.file, ctx)
    # validate always
    problems = []
    n = side.get("frames", 0)
    w, h = side.get("size", [0, 0])
    for t in side.get("tags", []):
        if not (0 <= t["from"] <= t["to"] < n):
            problems.append(f"tag '{t['name']}' range {t['from']}..{t['to']} outside 0..{n-1}")
        if t.get("direction") not in ("forward", "reverse", "pingpong"):
            problems.append(f"tag '{t['name']}' bad direction")
    for e in side.get("events", []):
        if not (0 <= e["frame"] < n):
            problems.append(f"event '{e['name']}' frame {e['frame']} outside 0..{n-1}")
    for name, pv in side.get("pivots", {}).items():
        if not (0 <= pv[0] <= 1 and 0 <= pv[1] <= 1):
            problems.append(f"pivot '{name}' outside 0..1")
    return {"file": a.file, "tags": len(side.get("tags", [])),
            "events": len(side.get("events", [])), "pivots": len(side.get("pivots", {})),
            "valid": not problems, "problems": problems, "pass": not problems}


def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    def frame_args(p):
        p.add_argument("inputs", nargs="+", help="Images, directory, glob, or one animated file")
        p.add_argument("--grid", help="Slice a sheet by WxH cells instead of reading files")
        p.add_argument("--margin", type=int, default=0)
        p.add_argument("--spacing", type=int, default=0)
        p.add_argument("--count", type=int)
        p.add_argument("--fps", type=int, default=None,
                       help="Override timing (default: keep source durations, or config fps for stills)")

    p = add("sheet-pack", "Pack frames into a sheet/atlas with padding, extrusion, metadata", cmd_sheet_pack)
    frame_args(p)
    p.add_argument("--output", required=True)
    p.add_argument("--layout", default="packed", choices=["packed", "grid", "horizontal", "vertical"])
    p.add_argument("--cols", type=int, default=0)
    p.add_argument("--padding", type=int, default=1)
    p.add_argument("--extrude", type=int, default=0)
    p.add_argument("--power-of-two", action="store_true")
    p.add_argument("--max-width", type=int, default=1024)
    p.add_argument("--trim", action="store_true")
    p.add_argument("--pivot", default="0.5,1.0")
    p.add_argument("--meta", help="Write atlas metadata here")
    p.add_argument("--meta-format", default="json-hash",
                   choices=["json-hash", "json-array", "aseprite", "json-simple", "csv", "xml", "plist"])
    p.add_argument("--tags-json", help="Animation tags sidecar (see tags command)")

    p = add("sheet-slice", "Slice a sheet by grid or auto-detect sprites", cmd_sheet_slice)
    p.add_argument("input"); p.add_argument("outdir")
    p.add_argument("--grid", help="WxH cell size; omit for auto-detection")
    p.add_argument("--margin", type=int, default=0)
    p.add_argument("--spacing", type=int, default=0)
    p.add_argument("--count", type=int)
    p.add_argument("--trim", action="store_true")

    p = add("atlas-meta", "Convert json-simple atlas manifest to engine metadata formats", cmd_atlas_meta)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--format", default="json-hash",
                   choices=["json-hash", "json-array", "aseprite", "csv", "xml", "plist"])
    p.add_argument("--image-name")

    p = add("anim-encode", "Encode frames/sheet/animation to GIF, APNG or WebP", cmd_anim_encode)
    frame_args(p)
    p.add_argument("--output", required=True)
    p.add_argument("--scale", type=int, default=1)
    p.add_argument("--loop", type=int, default=0, help="0 = infinite")
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--pingpong", action="store_true")
    p.add_argument("--hold", type=int, default=1, help="Multiply every frame duration")

    p = add("anim-decode", "Extract frames + timing from GIF/APNG/WebP", cmd_anim_decode)
    p.add_argument("input"); p.add_argument("outdir")

    p = add("sequence-export", "Export numbered PNG/BMP/TGA/TIFF frame sequence", cmd_sequence_export)
    frame_args(p)
    p.add_argument("--outdir", required=True)
    p.add_argument("--format", default="png", choices=["png", "bmp", "tga", "tiff"])
    p.add_argument("--prefix", default="frame-")
    p.add_argument("--scale", type=int, default=1)

    p = add("tags", "Create/edit/validate animation tags, pivots, and events sidecar", cmd_tags)
    p.add_argument("file")
    p.add_argument("--init", action="store_true")
    p.add_argument("--frames", type=int); p.add_argument("--size")
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--add-tag", help="name:from:to[:forward|reverse|pingpong]")
    p.add_argument("--add-event", help="frame:name[:data]")
    p.add_argument("--set-pivot", help="name:x,y (normalized)")

    p = add("aseprite-export", "Export sheet + Aseprite-compatible JSON with tags", cmd_aseprite_export)
    frame_args(p)
    p.add_argument("--output", required=True)
    p.add_argument("--layout", default="horizontal", choices=["packed", "grid", "horizontal", "vertical"])
    p.add_argument("--cols", type=int, default=0)
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--max-width", type=int, default=2048)
    p.add_argument("--tag", default="loop")
    p.add_argument("--tags-json")
