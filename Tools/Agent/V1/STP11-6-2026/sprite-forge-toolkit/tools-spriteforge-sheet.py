#!/usr/bin/env python3
"""spriteforge sheet: compose, extract, pack, label and inspect sprite sheets.

Operations
----------
  compose     frames (folder/GIF/APNG/strip) -> grid or strip sheet + metadata
  extract     sheet / contact sheet / GIF / APNG / video frames -> frames
  atlas       multiple sprites -> packed atlas with MaxRects + metadata
  metadata    rewrite/convert frame maps (json/xml/csv/plist/aseprite/engine)
  preview     contact sheet / review page for any frame set
  repack      reorder/resize/trim/pad frames and re-emit sheet + metadata
  detect      detect a grid or irregular frame layout without extracting
  tile        seamless-tile check or blended transition-tile generation

Metadata written by `compose`/`atlas` is engine-neutral JSON by default and can
be converted with `metadata`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_sheet", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_sheet"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageDraw = core.ImageDraw
ImageOps = core.ImageOps


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def load_input_frames(path: Path, *, max_frames: int, max_pixels: int, pattern: str | None = None,
                      metadata: str | None = None, root: Path | None = None) -> core.FrameSet:
    """Load frames from a file, folder, animation or a sheet plus its metadata."""
    if metadata:
        mp = Path(metadata) if Path(metadata).is_absolute() else (root or Path(".")) / metadata
        meta = core.load_json(core.find_input(mp, root))
        image = meta.get("image")
        if image:
            sheet_path = path if path.suffix.lower() in {".png", ".webp", ".tga", ".bmp", ".tiff"} else \
                (path.parent / image if not Path(image).is_absolute() else Path(image))
            sheet = core.load_image(core.find_input(sheet_path, root), max_pixels=max_pixels)
        else:
            sheet = core.load_image(path, max_pixels=max_pixels)
        frames, durations, labels = [], [], []
        for f in meta.get("frames") or []:
            box = f["frame"]
            frames.append(sheet.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])))
            durations.append(int(f.get("duration", 100)))
            labels.append(f.get("name") or f"frame{len(frames) - 1:04d}")
        if not frames:
            raise SFError("EMPTY_FRAMES", "metadata lists no frames", hint="check the 'frames' array")
        fs = core.FrameSet(frames=frames, durations_ms=durations, source=str(path))
        fs.labels = labels  # type: ignore[attr-defined]
        return fs
    return core.load_frames(path, max_frames=max_frames, max_pixels=max_pixels, pattern=pattern)


def derived_labels(fs: core.FrameSet, prefix: str) -> list[str]:
    """Real frame names from the source files, or a generated fallback."""
    names = getattr(fs, "names", None)
    if names and len(names) == len(fs.frames):
        return [core.slugify(Path(n).stem) for n in names]
    return [f"{core.slugify(prefix)}_{i:04d}" for i in range(len(fs.frames))]


def group_actions_from_names(labels: list[str]) -> list[dict]:
    """Group consecutive frame labels that share an action stem into actions.

    ``knight_walk_000``, ``knight_walk_001`` belong to one action; the shared
    prefix up to the trailing frame index is the action name.
    """
    actions: list[dict] = []
    for i, label in enumerate(labels):
        stem = re.sub(r"[-_]?\d+$", "", label)
        if actions and actions[-1]["stem"] == stem:
            actions[-1]["to"] = i + 1
            continue
        actions.append({"stem": stem, "name": stem or label, "from": i, "to": i + 1})
    return actions


def frame_labels(count: int, prefix: str, action: str | None = None, dirs: list[str] | None = None) -> list[str]:
    labels = []
    for i in range(count):
        action_name = action or prefix
        direction = ""
        if dirs:
            direction = "_" + dirs[i % len(dirs)]
        labels.append(f"{action_name}{direction}_{i:04d}")
    return labels


def sheet_metadata(frames: list[dict], *, image: str, size: tuple[int, int], cell: tuple[int, int],
                   cols: int, rows: int, padding: int, margin: int, name: str,
                   actions: list[dict] | None = None) -> dict:
    return {
        "format": "spriteforge-sheet",
        "version": 1,
        "name": name,
        "image": image,
        "sheet": {"width": size[0], "height": size[1]},
        "cell": {"width": cell[0], "height": cell[1]},
        "grid": {"cols": cols, "rows": rows, "padding": padding, "margin": margin},
        "pivot": {"x": cell[0] // 2, "y": cell[1], "unit": "pixels", "origin": "top-left"},
        "frames": frames,
        "actions": actions or [],
        "generator": f"{core.TOOL_NAME} {core.TOOL_VERSION}",
    }


def frame_record(name: str, x: int, y: int, w: int, h: int, *,
                 source_size: tuple[int, int] | None = None, offset: tuple[int, int] = (0, 0),
                 duration: int = 100, trim: tuple[int, int, int, int] | None = None,
                 rotation: int = 0) -> dict:
    rec = {
        "name": name, "frame": {"x": x, "y": y, "w": w, "h": h},
        "source_size": {"w": (source_size or (w, h))[0], "h": (source_size or (w, h))[1]},
        "offset": {"x": offset[0], "y": offset[1]},
        "duration": duration, "rotation": rotation,
    }
    if trim:
        rec["trim"] = {"x": trim[0], "y": trim[1], "w": trim[2], "h": trim[3]}
    return rec


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #

def op_compose(args, root: Path, rep: core.Reporter):
    """Compose frames into rows, columns, strips or grids with exact spacing."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = load_input_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels, pattern=args.pattern)
    if args.trim:
        trimmed = []
        offsets = []
        for f in fs.frames:
            t, box = core.trim(f, args.alpha_threshold)
            trimmed.append(t)
            offsets.append((box[0], box[1]))
        fs.frames = trimmed
    cell = core.parse_size(args.cell) if args.cell else fs.size
    max_w = max(f.width for f in fs.frames)
    max_h = max(f.height for f in fs.frames)
    if max_w > cell[0] or max_h > cell[1]:
        if args.strict:
            raise SFError("FRAME_TOO_LARGE",
                          f"largest frame {max_w}x{max_h} does not fit cell {cell[0]}x{cell[1]}",
                          hint="raise --cell, pass --resample, or drop --strict")
        rep.warn("FRAME_CLIPPED", f"largest frame {max_w}x{max_h} exceeds cell; resampling",
                 largest=[max_w, max_h], cell=list(cell))
    frames_out = []
    for f in fs.frames:
        tile = Image.new("RGBA", cell, (0, 0, 0, 0))
        img = f
        if img.size != cell:
            img = ImageOps.contain(img, cell, Image.Resampling.NEAREST if args.resample == "nearest" else Image.Resampling.LANCZOS)
        tile.alpha_composite(img, ((cell[0] - img.width) // 2, (cell[1] - img.height) // 2))
        frames_out.append(tile)
    count = len(frames_out)
    cols = args.cols or (count if args.layout == "strip" else math.ceil(math.sqrt(count)))
    cols = max(1, min(cols, count))
    if args.layout == "single-row":
        cols = count
    if args.layout == "single-column":
        cols = 1
    rows = math.ceil(count / cols)
    pad, margin = args.padding, args.margin
    W = cols * cell[0] + (cols - 1) * pad + margin * 2
    H = rows * cell[1] + (rows - 1) * pad + margin * 2
    if W * H > args.max_pixels:
        raise SFError("SHEET_TOO_LARGE", f"sheet {W}x{H} exceeds --max-pixels {args.max_pixels}")
    sheet = Image.new("RGBA", (W, H), tuple(args.background[:3]) + (255,) if args.background else (0, 0, 0, 0))
    records = []
    if args.action or args.directions:
        labels = frame_labels(count, args.name, args.action,
                              [d for d in args.directions.split(",") if d] if args.directions else None)
    else:
        labels = derived_labels(fs, args.name)
    for i, tile in enumerate(frames_out):
        r, c = divmod(i, cols)
        x = margin + c * (cell[0] + pad)
        y = margin + r * (cell[1] + pad)
        sheet.alpha_composite(tile, (x, y))
        src_size = fs.size if not args.trim else cell
        records.append(frame_record(labels[i], x, y, cell[0], cell[1], source_size=src_size,
                                    duration=fs.durations_ms[i]))
    if args.action:
        actions = [{"name": args.action, "from": 0, "to": count, "loop": args.loop,
                    "fps": round(1000 / max(1, fs.durations_ms[0]), 2)}]
    else:
        actions = [{"name": a["name"], "from": a["from"], "to": a["to"], "loop": args.loop,
                    "fps": round(1000 / max(1, fs.durations_ms[0]), 2)}
                   for a in group_actions_from_names(labels)]
    meta = sheet_metadata(records, image=Path(args.output).name, size=(W, H), cell=cell,
                          cols=cols, rows=rows, padding=pad, margin=margin,
                          name=args.name, actions=actions)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    core.ensure_ext(out, [".png", ".webp"], name="--output")
    if not args.dry_run:
        core.save_image(sheet, out)
        rep.add_output(out, "sheet", frames=count)
        if args.metadata:
            mp = core.guard_output(root / args.metadata, root, args.allow_outside_root)
            core.save_json(meta, mp)
            rep.add_output(mp, "metadata")
        if args.preview:
            pp = core.guard_output(root / args.preview, root, args.allow_outside_root)
            entries = [(t, labels[i]) for i, t in enumerate(frames_out)]
            core.save_image(core.make_contact_sheet(entries, cols=min(8, count), cell=cell), pp)
            rep.add_output(pp, "preview")
    rep.stat("sheet_size", [W, H])
    return {"summary": {"frames": count, "sheet": [W, H], "grid": f"{cols}x{rows}", "output": str(out)}}


def _grid_geometry(size: tuple[int, int], cols: int, rows: int, *, padding: int = 0,
                   margin: int = 0) -> tuple[int, int]:
    """Frame cell size implied by a sheet divided into cols x rows with padding/margin."""
    W, H = size
    usable_w = W - 2 * margin - (cols - 1) * padding
    usable_h = H - 2 * margin - (rows - 1) * padding
    if usable_w <= 0 or usable_h <= 0:
        raise SFError("BAD_GRID", f"grid {cols}x{rows} with padding {padding} does not fit {W}x{H}")
    if usable_w % cols or usable_h % rows:
        raise SFError("BAD_GRID",
                      f"{W}x{H} with padding {padding}/margin {margin} is not divisible by {cols}x{rows}",
                      hint="pass the exact --padding/--margin used when composing, or use --irregular")
    return usable_w // cols, usable_h // rows


def _grid_frame_box(index: int, cols: int, cell: tuple[int, int], *, padding: int, margin: int) -> tuple[int, int, int, int]:
    r, c = divmod(index, cols)
    x = margin + c * (cell[0] + padding)
    y = margin + r * (cell[1] + padding)
    return x, y, x + cell[0], y + cell[1]


def _read_grid_arg(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", value)
    if not m:
        raise SFError("BAD_GRID", f"--grid expects COLSxROWS, got '{value}'")
    return int(m.group(1)), int(m.group(2))


def op_detect(args, root: Path, rep: core.Reporter):
    """Detect a grid or irregular frame layout in a sheet (read-only)."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels)
    grid = _read_grid_arg(args.grid)
    result: dict = {"image": str(src), "size": list(im.size)}
    if grid:
        cols, rows = grid
        cell = _grid_geometry(im.size, cols, rows, padding=args.padding, margin=args.margin)
        frames = []
        for r in range(rows):
            for c in range(cols):
                box = _grid_frame_box(r * cols + c, cols, cell, padding=args.padding, margin=args.margin)
                sub = im.crop(box)
                bbox = core.alpha_bbox(sub, args.alpha_threshold)
                frames.append({"index": r * cols + c, "row": r, "col": c, "box": list(box),
                               "content_bbox": list(bbox) if bbox else None,
                               "empty": bbox is None})
        result.update({"mode": "grid", "cell": list(cell), "cols": cols, "rows": rows,
                       "frame_count": len(frames), "frames": frames,
                       "empty_frames": [f["index"] for f in frames if f["empty"]]})
    else:
        boxes = _detect_boxes(im, gap=args.gap, threshold=args.alpha_threshold) if args.irregular else ()
        if args.irregular:
            result.update({"mode": "irregular", "frame_count": len(boxes),
                           "boxes": [list(b) for b in boxes]})
        else:
            width_guess = _guess_columns(im, args.alpha_threshold)
            result.update({"mode": "guess", "detected_columns": width_guess,
                           "cell": [im.width // width_guess, im.height] if width_guess else None,
                           "note": "pass --grid COLSxROWS or --irregular for exact results"})
    if args.output:
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        if not args.dry_run:
            core.save_json(result, out)
            rep.add_output(out, "layout")
    return {"summary": {k: result.get(k) for k in ("mode", "frame_count", "cell", "detected_columns") if k in result}}


def _guess_columns(im, threshold: int) -> int | None:
    alpha = im.convert("RGBA").split()[-1]
    col_has = []
    for x in range(im.width):
        column = alpha.crop((x, 0, x + 1, im.height))
        col_has.append(column.getextrema()[1] > 0)
    runs, run = [], 0
    for has in col_has:
        if not has:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if not col_has[0] and not col_has[-1]:
        gaps = [r for r in runs if r >= 1]
        return len(gaps) + 1 if gaps else None
    return None


def _detect_boxes(im, gap: int = 1, threshold: int = 1) -> list[tuple[int, int, int, int]]:
    """Flood-fill connected components of non-transparent pixels."""
    alpha = im.convert("RGBA").split()[-1]
    if threshold > 1:
        alpha = alpha.point(lambda v: 255 if v >= threshold else 0)
    w, h = alpha.size
    mask = [[alpha.getpixel((x, y)) > 0 for y in range(h)] for x in range(w)]
    seen = [[False] * h for _ in range(w)]
    boxes = []
    for x in range(w):
        for y in range(h):
            if not mask[x][y] or seen[x][y]:
                continue
            stack = [(x, y)]
            seen[x][y] = True
            minx = maxx = x
            miny = maxy = y
            while stack:
                cx, cy = stack.pop()
                minx, maxx = min(minx, cx), max(maxx, cx)
                miny, maxy = min(miny, cy), max(maxy, cy)
                for dx in range(-gap, gap + 1):
                    for dy in range(-gap, gap + 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and mask[nx][ny] and not seen[nx][ny]:
                            seen[nx][ny] = True
                            stack.append((nx, ny))
            if (maxx - minx) >= 1 and (maxy - miny) >= 1:
                boxes.append((minx, miny, maxx - minx + 1, maxy - miny + 1))
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def op_extract(args, root: Path, rep: core.Reporter):
    """Detect and extract frames from sheets, GIFs, APNGs, videos or sequences."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    out_dir = core.guard_output(root / args.output, root, args.allow_outside_root, as_dir=True)
    grid = _read_grid_arg(args.grid)
    extracted: list[tuple[str, Image.Image]] = []
    if args.metadata:
        mp = Path(args.metadata) if Path(args.metadata).is_absolute() else root / args.metadata
        meta = core.load_json(mp)
        base = mp.parent
        image = meta.get("image")
        if image and src == Path("."):
            src = base / image
        im = core.load_image(src, max_pixels=args.max_pixels)
        for f in meta.get("frames", []):
            fr = f["frame"]
            tile = im.crop((fr["x"], fr["y"], fr["x"] + fr["w"], fr["y"] + fr["h"]))
            extracted.append((core.slugify(f["name"], default=f"frame_{len(extracted):04d}"), tile))
    elif args.irregular:
        im = core.load_image(src, max_pixels=args.max_pixels)
        boxes = _detect_boxes(im, gap=args.gap, threshold=args.alpha_threshold)
        if not boxes:
            raise SFError("NO_FRAMES", f"no non-transparent regions detected in {src.name}")
        for i, (x, y, w, h) in enumerate(boxes):
            extracted.append((f"frame_{i:04d}", im.crop((x, y, x + w, y + h))))
    elif grid:
        im = core.load_image(src, max_pixels=args.max_pixels)
        cols, rows = grid
        cell = _grid_geometry(im.size, cols, rows, padding=args.padding, margin=args.margin)
        idx = 0
        for index in range(cols * rows):
            if args.limit and idx >= args.limit:
                break
            box = _grid_frame_box(index, cols, cell, padding=args.padding, margin=args.margin)
            tile = im.crop(box)
            if args.skip_empty and core.alpha_bbox(tile) is None:
                continue
            extracted.append((f"frame_{idx:04d}", tile))
            idx += 1
    else:
        fs = load_input_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
        if args.limit:
            fs.frames = fs.frames[: args.limit]
            fs.durations_ms = fs.durations_ms[: args.limit]
        for i, f in enumerate(fs.frames):
            tile = f
            if args.trim:
                tile = core.trim(tile, args.alpha_threshold)[0]
            extracted.append((f"frame_{i:04d}", tile))
    if not extracted:
        raise SFError("NO_FRAMES", "nothing extracted; check --grid / --irregular / input type")
    if not args.dry_run:
        for name, tile in extracted:
            core.save_image(tile, out_dir / f"{name}.png")
        core.save_json({"source": str(src), "count": len(extracted),
                        "grid": list(grid) if grid else None, "irregular": bool(args.irregular),
                        "metadata": args.metadata,
                        "frames": [n for n, _ in extracted]}, out_dir / "extract.json")
        rep.add_output(out_dir, "directory", frames=len(extracted))
    rep.stat("frames", len(extracted))
    return {"summary": {"frames": len(extracted), "out": str(out_dir)}}


def _maxrects_pack(sizes: list[tuple[int, int]], *, padding: int, max_size: int,
                   power_of_two: bool) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    """Deterministic MaxRects packer (best short side fit) with skyline fallback."""
    if not sizes:
        raise SFError("NO_SPRITES", "nothing to pack")
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))

    def attempt(width: int, height: int) -> list[tuple[int, int]] | None:
        free: list[tuple[int, int, int, int]] = [(0, 0, width, height)]
        placed: list[tuple[int, int] | None] = [None] * len(sizes)
        for i in order:
            w, h = sizes[i]
            best_key = None
            best_pos = None
            for fx, fy, fw, fh in free:
                if w <= fw and h <= fh:
                    short = min(fw - w, fh - h)
                    long_ = max(fw - w, fh - h)
                    key = (short, long_, fy, fx)
                    if best_key is None or key < best_key:
                        best_key = key
                        best_pos = (fx, fy)
            if best_pos is None:
                return None
            px, py = best_pos
            placed[i] = (px, py)
            updated: list[tuple[int, int, int, int]] = []
            for (fx, fy, fw, fh) in free:
                # does this free rect overlap the newly used rect?
                if px >= fx + fw or px + w + padding <= fx or py >= fy + fh or py + h + padding <= fy:
                    updated.append((fx, fy, fw, fh))
                    continue
                if px > fx:
                    updated.append((fx, fy, px - fx, fh))
                if px + w + padding < fx + fw:
                    updated.append((px + w + padding, fy, fx + fw - (px + w + padding), fh))
                if py > fy:
                    updated.append((fx, fy, fw, py - fy))
                if py + h + padding < fy + fh:
                    updated.append((fx, py + h + padding, fw, fy + fh - (py + h + padding)))
            # prune free rects fully contained in another free rect
            pruned = []
            for a in updated:
                ax, ay, aw, ah = a
                if any(b is not a and ax >= b[0] and ay >= b[1] and ax + aw <= b[0] + b[2]
                       and ay + ah <= b[1] + b[3] for b in updated):
                    continue
                pruned.append(a)
            free = pruned
        return [p for p in placed]  # type: ignore[list-item]

    best: tuple[list[tuple[int, int]], tuple[int, int]] | None = None
    total = sum((w + padding) * (h + padding) for w, h in sizes)
    side = 32
    while side <= max_size:
        if side * side >= total:
            got = attempt(side, side)
            if got is not None:
                best = (got, (side, side))
                break
        side *= 2
    if best is None:
        # try wide rectangles at several widths before giving up
        for width in (256, 512, 1024, 2048, max_size):
            if width > max_size:
                continue
            height = 64
            while height <= max_size:
                got = attempt(width, height)
                if got is not None:
                    best = (got, (width, height))
                    break
                height *= 2
            if best:
                break
    if best is None:
        raise SFError("PACK_FAILED", f"could not pack {len(sizes)} sprites within {max_size}px",
                      hint="raise --max-size or reduce --padding")
    placed, box = best
    need_w = max(px + w for (px, py), (w, h) in zip(placed, sizes)) + padding
    need_h = max(py + h for (px, py), (w, h) in zip(placed, sizes)) + padding
    if power_of_two:
        p = 1
        while p < max(need_w, need_h):
            p *= 2
        if p > max_size:
            raise SFError("PACK_FAILED", f"power-of-two atlas {p}px exceeds --max-size {max_size}")
        repacked = attempt(p, p)
        if repacked is None:
            raise SFError("PACK_FAILED", f"could not repack into {p}x{p}")
        return repacked, (p, p)
    return placed, (need_w, need_h)


def op_atlas(args, root: Path, rep: core.Reporter):
    """Pack many sprites into a compact atlas using trim, padding and MaxRects."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if src.is_dir():
        files = core.list_images(src, recursive=args.recursive)
    elif src.exists():
        files = [src]
    else:
        raise SFError("INPUT_NOT_FOUND", f"atlas input not found: {src}")
    if not files:
        raise SFError("NO_FRAMES", f"no images found under {src}")
    if len(files) > args.max_sprites:
        raise SFError("TOO_MANY_SPRITES", f"{len(files)} sprites exceeds --max-sprites {args.max_sprites}")
    sprites, names, trims = [], [], []
    for p in files:
        rep.deadline.check()
        im = core.load_image(p, max_pixels=args.max_pixels)
        names.append(p.stem)
        if args.trim:
            t, box = core.trim(im, args.alpha_threshold)
            trims.append(box)
            sprites.append(t)
        else:
            trims.append((0, 0, im.width, im.height))
            sprites.append(im)
    sizes = [(s.width, s.height) for s in sprites]
    positions, atlas_size = _maxrects_pack(sizes, padding=args.padding, max_size=args.max_size,
                                           power_of_two=args.pow2)
    W, H = atlas_size[0] + args.padding, atlas_size[1] + args.padding
    if W * H > args.max_pixels:
        raise SFError("SHEET_TOO_LARGE", f"atlas {W}x{H} exceeds --max-pixels {args.max_pixels}")
    atlas = Image.new("RGBA", (W, H), tuple(args.background[:3]) + (255,) if args.background else (0, 0, 0, 0))
    records = []
    for i, (name, sprite, pos) in enumerate(zip(names, sprites, positions)):
        assert pos is not None
        atlas.alpha_composite(sprite, pos)
        records.append(frame_record(name, pos[0], pos[1], sprite.width, sprite.height,
                                    source_size=(trims[i][2], trims[i][3]),
                                    trim=trims[i], duration=args.duration))
    used = sum(s.width * s.height for s in sprites)
    meta = sheet_metadata(records, image=Path(args.output).name, size=(W, H), cell=(0, 0),
                          cols=0, rows=0, padding=args.padding, margin=0, name=args.name)
    meta["grid"] = None
    meta["packing"] = {"algorithm": "maxrects-bssf", "power_of_two": bool(args.pow2),
                       "utilisation": round(used / (W * H), 4), "trimmed": bool(args.trim)}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_image(atlas, out)
        rep.add_output(out, "atlas", sprites=len(records))
        mp = core.guard_output(root / args.metadata, root, args.allow_outside_root)
        core.save_json(meta, mp)
        rep.add_output(mp, "metadata")
        if args.preview:
            pp = core.guard_output(root / args.preview, root, args.allow_outside_root)
            core.save_image(core.composite_on(atlas, "checker-dark"), pp)
            rep.add_output(pp, "preview")
    rep.stat("atlas", [W, H])
    rep.stat("utilisation", meta["packing"]["utilisation"])
    return {"summary": {"sprites": len(records), "atlas": [W, H],
                        "utilisation": meta["packing"]["utilisation"], "output": str(out)}}


def normalize_meta_type(kind: str) -> str:
    kind = kind.lower()
    allowed = {"json", "xml", "csv", "plist", "aseprite", "unity", "godot", "phaser", "pixi", "sparrow"}
    if kind not in allowed:
        raise SFError("BAD_FORMAT", f"metadata format '{kind}' is not supported",
                      details={"allowed": sorted(allowed)})
    return kind


def op_metadata(args, root: Path, rep: core.Reporter):
    """Rewrite frame metadata into JSON/XML/CSV/plist/Aseprite/engine formats."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    meta = core.load_json(src)
    if not isinstance(meta, dict) or not isinstance(meta.get("frames"), list):
        raise SFError("BAD_METADATA", "input must be a spriteforge sheet JSON with a 'frames' list",
                      hint="produce one with `compose --metadata` or `atlas --metadata`")
    frames = meta["frames"]
    if not frames:
        raise SFError("BAD_METADATA", "metadata has no frames")
    for key in ("name", "frame"):
        for f in frames:
            if key not in f:
                raise SFError("BAD_METADATA", f"frame record missing '{key}'")
    kind = normalize_meta_type(args.format)
    out_name = Path(args.output).name
    payload: str
    if kind == "json":
        payload = json.dumps(meta if not args.engine else _engine_json(meta, args.engine), indent=2)
    elif kind == "csv":
        lines = ["name,x,y,w,h,offsetX,offsetY,sourceW,sourceH,duration"]
        for f in frames:
            lines.append(",".join(str(v) for v in [
                f["name"], f["frame"]["x"], f["frame"]["y"], f["frame"]["w"], f["frame"]["h"],
                f.get("offset", {}).get("x", 0), f.get("offset", {}).get("y", 0),
                f.get("source_size", {}).get("w", f["frame"]["w"]),
                f.get("source_size", {}).get("h", f["frame"]["h"]), f.get("duration", 100)]))
        payload = "\n".join(lines) + "\n"
    elif kind == "xml":
        parts = ['<?xml version="1.0" encoding="UTF-8"?>', f'<TextureAtlas imagePath="{meta.get("image", out_name)}">']
        for f in frames:
            parts.append(
                f'  <SubTexture name="{f["name"]}" x="{f["frame"]["x"]}" y="{f["frame"]["y"]}" '
                f'width="{f["frame"]["w"]}" height="{f["frame"]["h"]}" frameX="0" frameY="0" '
                f'frameWidth="{f.get("source_size", {}).get("w", f["frame"]["w"])}" '
                f'frameHeight="{f.get("source_size", {}).get("h", f["frame"]["h"])}"/>')
        parts.append("</TextureAtlas>")
        payload = "\n".join(parts) + "\n"
    elif kind == "plist":
        body = []
        for f in frames:
            body.append("\t\t<dict>")
            body.append(f'\t\t\t<key>name</key><string>{f["name"]}</string>')
            for k, v in (("x", "x"), ("y", "y"), ("width", "w"), ("height", "h")):
                body.append(f"\t\t\t<key>{k}</key><integer>{f['frame'][v]}</integer>")
            body.append("\t\t</dict>")
        payload = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                   '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n'
                   '\t<key>frames</key>\n\t<array>\n' + "\n".join(body) + "\n\t</array>\n</dict>\n</plist>\n")
    elif kind == "aseprite":
        payload = json.dumps({"frames": [
            {f["name"]: {"frame": {"x": f["frame"]["x"], "y": f["frame"]["y"], "w": f["frame"]["w"], "h": f["frame"]["h"]},
                         "rotated": False, "trimmed": f["frame"]["w"] != f.get("source_size", {}).get("w", f["frame"]["w"]),
                         "spriteSourceSize": {"x": 0, "y": 0, "w": f["frame"]["w"], "h": f["frame"]["h"]},
                         "sourceSize": {"w": f.get("source_size", {}).get("w", f["frame"]["w"]),
                                        "h": f.get("source_size", {}).get("h", f["frame"]["h"])},
                         "duration": f.get("duration", 100)}}
            for f in frames],
            "meta": {"app": "spriteforge", "version": core.TOOL_VERSION,
                     "image": meta.get("image", out_name), "format": "RGBA8888",
                     "size": {"w": meta.get("sheet", {}).get("width", 0), "h": meta.get("sheet", {}).get("height", 0)},
                     "scale": "1"}}, indent=2)
    else:
        payload = json.dumps(_engine_json(meta, args.engine or kind), indent=2)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        out.write_text(payload, encoding="utf-8")
        rep.add_output(out, "metadata", format=kind)
    rep.stat("format", kind)
    return {"summary": {"format": kind, "frames": len(frames), "output": str(out)}}


def _engine_json(meta: dict, engine: str) -> dict:
    frames = meta.get("frames", [])
    if engine in ("phaser", "pixi"):
        return {"frames": {f["name"]: {"frame": {"x": f["frame"]["x"], "y": f["frame"]["y"],
                                                 "w": f["frame"]["w"], "h": f["frame"]["h"]},
                                       "sourceSize": {"w": f.get("source_size", {}).get("w", f["frame"]["w"]),
                                                      "h": f.get("source_size", {}).get("h", f["frame"]["h"])}}
                           for f in frames},
                "meta": {"image": meta.get("image"), "size": meta.get("sheet"),
                         "scale": "1", "app": "spriteforge"}}
    if engine == "godot":
        return {"resource": "SpriteFrames-plan",
                "frames": [{"name": f["name"], "region": [f["frame"]["x"], f["frame"]["y"],
                                                          f["frame"]["w"], f["frame"]["h"]],
                            "duration": f.get("duration", 100)} for f in frames],
                "animations": meta.get("actions", [])}
    if engine == "unity":
        return {"texture": meta.get("image"), "pixelsPerUnit": 100,
                "sprites": [{"name": f["name"], "rect": {"x": f["frame"]["x"], "y": f["frame"]["y"],
                                                         "width": f["frame"]["w"], "height": f["frame"]["h"]},
                             "pivot": {"x": 0.5, "y": 0.0}} for f in frames]}
    return meta


def op_preview(args, root: Path, rep: core.Reporter):
    """Build an indexed contact sheet / review page for any frame set."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = load_input_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels,
                           metadata=getattr(args, "metadata", None), root=root)
    frames = fs.frames
    if args.limit:
        frames = frames[: args.limit]
    entries = []
    labels = getattr(fs, "labels", None)
    for i, f in enumerate(frames):
        label = labels[i] if labels and i < len(labels) else f"{i:04d}"
        if args.label_timing:
            label += f" {fs.durations_ms[i] if i < len(fs.durations_ms) else args.duration}ms"
        if args.label_action and args.action_names:
            names = [a.strip() for a in args.action_names.split(",") if a.strip()]
            label = f"{names[i % len(names)]} {label}"
        entries.append((f, label))
    cell = core.parse_size(args.cell) if args.cell else None
    sheet = core.make_contact_sheet(entries, cols=args.cols, cell=cell)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_image(sheet, out)
        rep.add_output(out, "contact-sheet", frames=len(entries))
        if args.html:
            hp = core.guard_output(root / args.html, root, args.allow_outside_root)
            html = _review_html(entries, args, out, frames)
            hp.write_text(html, encoding="utf-8")
            rep.add_output(hp, "review-page")
    rep.stat("frames", len(entries))
    return {"summary": {"frames": len(entries), "output": str(out)}}


def _review_html(entries, args, sheet_path: Path, frames) -> str:
    rows = "".join(
        f'<tr><td>{i}</td><td>{label}</td><td>{frames[i].width}x{frames[i].height}</td>'
        f'<td><input type="radio" name="frame{i}" value="ok">ok '
        f'<input type="radio" name="frame{i}" value="fix">fix</td></tr>'
        for i, (_, label) in enumerate(entries) if i < len(frames))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>spriteforge review - {Path(args.output).name}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;max-width:900px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}}
textarea{{width:100%;height:120px;margin-top:16px}}</style></head>
<body><h1>Review: {Path(args.output).name}</h1>
<p>{len(entries)} frames. Contact sheet: <code>{sheet_path.name}</code></p>
<table><thead><tr><th>#</th><th>Frame</th><th>Size</th><th>Verdict</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Notes</h2><textarea placeholder="Per-frame fixes, e.g. 'frame 4 outline broken'"></textarea>
<p><small>Generated by {core.TOOL_NAME} {core.TOOL_VERSION}. This page is static: save it next to the contact sheet.</small></p>
</body></html>"""


def op_repack(args, root: Path, rep: core.Reporter):
    """Reorder, resize, trim, pad and repack frames without losing anchors."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = load_input_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    order = core.parse_int_list(args.order, name="order") if args.order else list(range(len(fs.frames)))
    if any(i < 0 or i >= len(fs.frames) for i in order):
        raise SFError("BAD_ORDER", f"--order values must be 0..{len(fs.frames)-1}")
    frames = [fs.frames[i] for i in order]
    durations = [fs.durations_ms[i] for i in order]
    target = core.parse_size(args.cell) if args.cell else fs.size
    out_frames = []
    offsets = []
    for f in frames:
        if args.trim:
            t, box = core.trim(f, args.alpha_threshold)
            offsets.append((box[0], box[1]))
        else:
            t, box = f, (0, 0, f.width, f.height)
            offsets.append((0, 0))
        scale = args.scale or 1.0
        if scale != 1.0 or t.size != target:
            if args.resample == "nearest":
                resample = Image.Resampling.NEAREST
            elif args.resample == "bicubic":
                resample = Image.Resampling.BICUBIC
            else:
                resample = Image.Resampling.LANCZOS
            t = ImageOps.contain(t, target, resample)
        out_frames.append(core.pad_to(t, target, ((target[0] - t.width) // 2, (target[1] - t.height) // 2)))
    cols = args.cols or min(8, len(out_frames))
    pad = args.padding
    W = cols * target[0] + (cols - 1) * pad
    H = math.ceil(len(out_frames) / cols) * target[1] + (math.ceil(len(out_frames) / cols) - 1) * pad
    sheet = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    records = []
    for i, tile in enumerate(out_frames):
        r, c = divmod(i, cols)
        x, y = c * (target[0] + pad), r * (target[1] + pad)
        sheet.alpha_composite(tile, (x, y))
        records.append(frame_record(f"frame_{i:04d}", x, y, target[0], target[1],
                                    source_size=target, offset=offsets[i], duration=durations[i]))
    meta = sheet_metadata(records, image=Path(args.output).name, size=(W, H), cell=target,
                          cols=cols, rows=math.ceil(len(out_frames) / cols), padding=pad, margin=0,
                          name=args.name)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_image(sheet, out)
        rep.add_output(out, "sheet", frames=len(records))
        if args.metadata:
            mp = core.guard_output(root / args.metadata, root, args.allow_outside_root)
            core.save_json(meta, mp)
            rep.add_output(mp, "metadata")
    rep.stat("frames", len(records))
    return {"summary": {"frames": len(records), "sheet": [W, H], "order": order[:12], "output": str(out)}}


def op_tile(args, root: Path, rep: core.Reporter):
    """Check seamless tiling or build blended transition tiles."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if args.mode == "check":
        im = core.load_image(src, max_pixels=args.max_pixels)
        left_right = core.pixel_diff(im.crop((0, 0, 1, im.height)),
                                     im.crop((im.width - 1, 0, im.width, im.height)))["changed_pixels"]
        top_bottom = core.pixel_diff(im.crop((0, 0, im.width, 1)),
                                     im.crop((0, im.height - 1, im.width, im.height)))["changed_pixels"]
        result = {
            "size": list(im.size),
            "edge_difference": {"left_vs_right_pixels": left_right, "top_vs_bottom_pixels": top_bottom},
            "seamless_estimate": left_right == 0 and top_bottom == 0,
            "note": "exact edge equality is strict; small differences can still tile acceptably",
        }
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        if not args.dry_run:
            tp = out.with_name(out.stem + "-tiled.png")
            core.save_image(core.tile_preview(im, 3, 3), tp)
            rep.add_output(tp, "tile-preview")
            core.save_json(result, out)
            rep.add_output(out, "tile-report")
        return {"summary": {"seamless_estimate": result["seamless_estimate"],
                            "lr": left_right, "tb": top_bottom}}
    missing = [f for f in ("--input", "--input-b") if not getattr(args, f.replace("--", "").replace("-", "_"))]
    if missing:
        raise SFError("MISSING_INPUT", "transition mode needs both --input and --input-b")
    a = core.load_image(src, max_pixels=args.max_pixels)
    b_path = Path(args.input_b) if Path(args.input_b).is_absolute() else root / args.input_b
    b = core.load_image(b_path, max_pixels=args.max_pixels)
    if a.size != b.size:
        raise SFError("SIZE_MISMATCH", f"tiles differ: {a.size} vs {b.size}")
    steps = max(1, args.steps)
    out_dir = core.guard_output(root / args.output, root, args.allow_outside_root, as_dir=True)
    for i in range(steps):
        alpha = i / steps
        blended = Image.blend(a.convert("RGBA"), b.convert("RGBA"), alpha)
        edge = args.edge_blend
        if edge > 0:
            mask = Image.new("L", a.size, 0)
            d = ImageDraw.Draw(mask)
            band = max(1, int(min(a.size) * edge))
            d.rectangle([0, a.height - band, a.width, a.height], fill=255)
            blended = Image.composite(b.convert("RGBA"), blended, mask)
        if not args.dry_run:
            core.save_image(blended, out_dir / f"transition_{i:02d}.png")
    if not args.dry_run:
        rep.add_output(out_dir, "directory", steps=steps)
    return {"summary": {"steps": steps, "out": str(out_dir)}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_compose(p):
    p.add_argument("--input", "-i", required=True, help="frame folder, GIF/APNG, or sheet strip")
    p.add_argument("--output", "-o", required=True, help="sheet PNG/WebP output")
    p.add_argument("--metadata", help="metadata JSON output")
    p.add_argument("--preview", help="contact sheet preview output")
    p.add_argument("--cell", help="cell size WxH (default: source frame size)")
    p.add_argument("--cols", type=int, default=0, help="columns (0 = auto)")
    p.add_argument("--layout", default="grid", choices=["grid", "strip", "single-row", "single-column"])
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--margin", type=int, default=0)
    p.add_argument("--trim", action="store_true", help="trim transparent borders per frame")
    p.add_argument("--alpha-threshold", type=int, default=1)
    p.add_argument("--resample", choices=["nearest", "lanczos", "bicubic"], default="nearest")
    p.add_argument("--background", help="hex background colour (default transparent)")
    p.add_argument("--name", default="sheet")
    p.add_argument("--action", default=None,
                   help="force one action name for every frame (default: derive from file names)")
    p.add_argument("--directions", help="comma list e.g. s,se,e")
    p.add_argument("--loop", action="store_true", default=True)
    p.add_argument("--strict", action="store_true", help="fail instead of rescaling oversized frames")
    p.add_argument("--pattern", help="only load sequence files matching this regex")


def _p_detect(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--grid", help="COLSxROWS if known")
    p.add_argument("--irregular", action="store_true", help="find connected components")
    p.add_argument("--gap", type=int, default=1, help="diagonal connectivity radius for --irregular")
    p.add_argument("--padding", type=int, default=0, help="padding used when the sheet was composed")
    p.add_argument("--margin", type=int, default=0, help="outer margin used when the sheet was composed")
    p.add_argument("--alpha-threshold", type=int, default=1)
    p.add_argument("--output", "-o")


def _p_extract(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True, help="output folder")
    p.add_argument("--grid", help="COLSxROWS for a packed sheet")
    p.add_argument("--padding", type=int, default=0, help="padding used when composing the sheet")
    p.add_argument("--margin", type=int, default=0, help="outer margin used when composing the sheet")
    p.add_argument("--metadata", help="extract using a spriteforge sheet JSON (exact rects)")
    p.add_argument("--irregular", action="store_true")
    p.add_argument("--gap", type=int, default=1)
    p.add_argument("--alpha-threshold", type=int, default=1)
    p.add_argument("--trim", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--skip-empty", action="store_true", default=True)


def op_slices(args, root: Path, rep: core.Reporter):
    """Slice one packed sheet into engine-ready per-frame sprites plus a contact index."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels)
    if args.metadata:
        mp = Path(args.metadata) if Path(args.metadata).is_absolute() else root / args.metadata
        meta = core.load_json(mp)
        frames = meta.get("frames") or []
        if not frames:
            raise SFError("BAD_METADATA", "metadata has no frames")
        rects = [(f["name"], f["frame"]["x"], f["frame"]["y"], f["frame"]["w"], f["frame"]["h"]) for f in frames]
    elif args.grid:
        m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", args.grid)
        if not m:
            raise SFError("BAD_GRID", f"--grid expects COLSxROWS, got '{args.grid}'")
        cols, rows = int(m.group(1)), int(m.group(2))
        cell = core.grid_cell(im.size, cols, rows, padding=args.padding, margin=args.margin)
        rects = []
        for index in range(cols * rows):
            r, c = divmod(index, cols)
            x = args.margin + c * (cell[0] + args.padding)
            y = args.margin + r * (cell[1] + args.padding)
            rects.append((f"{args.name}_{index:04d}", x, y, cell[0], cell[1]))
    else:
        raise SFError("MISSING_INPUT", "pass --grid COLSxROWS or --metadata sheet JSON")
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    entries = []
    manifest = []
    for name, x, y, w, h in rects:
        rep.deadline.check()
        tile = im.crop((x, y, x + w, y + h))
        if args.skip_empty and core.alpha_bbox(tile) is None:
            continue
        safe = core.slugify(name)
        if not args.dry_run:
            core.save_image(tile, out_dir / f"{safe}.png")
        entries.append((tile, safe))
        manifest.append({"name": name, "file": f"{safe}.png", "x": x, "y": y, "w": w, "h": h})
    if not manifest:
        raise SFError("NO_FRAMES", "no non-empty frames found in the sheet")
    if not args.dry_run:
        core.save_json({"source": str(src), "frames": len(manifest), "cell": [rects[0][3], rects[0][4]],
                        "frames_index": manifest}, out_dir / "slices.json")
        if args.index:
            ip = core.guard_output(root / args.index, root, args.allow_outside_root)
            core.save_image(core.make_contact_sheet(entries, cols=min(8, len(entries))), ip)
            rep.add_output(ip, "index-sheet")
        rep.add_output(out_dir, "directory", frames=len(manifest))
    rep.stat("frames", len(manifest))
    return {"summary": {"frames": len(manifest), "out": str(out_dir)}}


def _p_slices(p):
    p.add_argument("--input", "-i", required=True, help="packed sheet image")
    p.add_argument("--output", "-o", required=True, help="output folder for individual frames")
    p.add_argument("--grid", help="COLSxROWS layout")
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--margin", type=int, default=0)
    p.add_argument("--metadata", help="spriteforge sheet JSON for exact rects")
    p.add_argument("--name", default="frame")
    p.add_argument("--index", help="optional contact-sheet PNG index")
    p.add_argument("--skip-empty", action="store_true", default=True)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_atlas(p):
    p.add_argument("--input", "-i", required=True, help="folder of sprites (or one image)")
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--metadata", required=True)
    p.add_argument("--preview")
    p.add_argument("--padding", type=int, default=2)
    p.add_argument("--max-size", type=int, default=4096)
    p.add_argument("--pow2", action="store_true")
    p.add_argument("--trim", action="store_true", default=True)
    p.add_argument("--alpha-threshold", type=int, default=1)
    p.add_argument("--recursive", action="store_true", default=True)
    p.add_argument("--max-sprites", type=int, default=2048)
    p.add_argument("--background")
    p.add_argument("--name", default="atlas")
    p.add_argument("--duration", type=int, default=100)


def _p_metadata(p):
    p.add_argument("--input", "-i", required=True, help="spriteforge sheet JSON")
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--format", default="json",
                   choices=["json", "xml", "csv", "plist", "aseprite", "unity", "godot", "phaser", "pixi", "sparrow"])
    p.add_argument("--engine", choices=["unity", "godot", "phaser", "pixi"])


def _p_preview(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--cols", type=int, default=6)
    p.add_argument("--cell", help="force cell size WxH")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--label-timing", action="store_true")
    p.add_argument("--label-action", action="store_true")
    p.add_argument("--action-names", help="comma list cycled across frames")
    p.add_argument("--metadata", help="spriteforge sheet JSON for exact cells and action names")
    p.add_argument("--duration", type=int, default=100)
    p.add_argument("--html", help="also write a static review page")


def _p_repack(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--metadata")
    p.add_argument("--order", help="comma list of source frame indices")
    p.add_argument("--cell")
    p.add_argument("--cols", type=int, default=0)
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--trim", action="store_true")
    p.add_argument("--alpha-threshold", type=int, default=1)
    p.add_argument("--resample", choices=["nearest", "lanczos", "bicubic"], default="nearest")
    p.add_argument("--name", default="repacked")


def _p_tile(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--input-b", help="second tile for transition mode")
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--mode", choices=["check", "transition"], default="check")
    p.add_argument("--steps", type=int, default=8)
    p.add_argument("--edge-blend", type=float, default=0.25)


OPS = {
    "compose": (_p_compose, op_compose),
    "extract": (_p_extract, op_extract),
    "atlas": (_p_atlas, op_atlas),
    "slices": (_p_slices, op_slices),
    "metadata": (_p_metadata, op_metadata),
    "preview": (_p_preview, op_preview),
    "repack": (_p_repack, op_repack),
    "detect": (_p_detect, op_detect),
    "tile": (_p_tile, op_tile),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "sheet",
        "spriteforge sheet: compose, extract, pack, label and inspect sprite sheets",
        OPS,
    ))
