#!/usr/bin/env python3
"""spriteforge analyze: asset intelligence, catalogues, masks, maps and review pages.

Operations
----------
  catalog         scan sprite folders into a searchable index
  duplicates      find exact and near-duplicate sprites/frames
  style           extract practical style rules from approved examples
  gaps            compare a plan against existing files
  layers          suggest part/layer splits from alpha structure
  autotile        derive tile-connection rules from a terrain sheet
  collision       generate box/circle/polygon/capsule masks from alpha
  depth           generate depth / normal / emission helper maps
  motion          key-pose, arc, contact and timing notes from a sequence
  incremental     rebuild only changed outputs using content hashes
  frame-detect    detect grid or irregular frame layout
  review-package  build a static human review page with issues and choices
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
_spec = importlib.util.spec_from_file_location("sfcore_analyze", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_analyze"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageDraw = core.ImageDraw
ImageFilter = core.ImageFilter
ImageOps = core.ImageOps


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #

NAME_PARTS = re.compile(r"^(?P<unit>[a-z0-9]+)[_-](?P<action>[a-z]+)(?:[_-](?P<direction>[a-z]{1,2}))?(?:[_-]?(?P<index>\d{1,4}))?$", re.I)


def op_catalog(args, root: Path, rep: core.Reporter):
    """Scan sprite folders into a searchable index by subject/action/size/status."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"catalog needs a folder, got {src}")
    files = core.list_images(src, recursive=not args.shallow)
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    entries = []
    for p in files:
        rep.deadline.check()
        if len(entries) >= args.max_files:
            rep.warn("TRUNCATED", f"stopped at --max-files {args.max_files}")
            break
        try:
            im = core.load_image(p, max_pixels=args.max_pixels)
        except SFError as exc:
            rep.warn("UNREADABLE", f"{p.name}: {exc.message}")
            continue
        bbox = core.alpha_bbox(im)
        m = NAME_PARTS.match(p.stem)
        entries.append({
            "path": p.relative_to(src).as_posix(),
            "name": p.stem,
            "ext": p.suffix.lower(),
            "bytes": p.stat().st_size,
            "size": list(im.size),
            "content_bbox": list(bbox) if bbox else None,
            "transparent": core.has_transparency(im),
            "colors": len(core.color_counts(im)),
            "sha256": core.image_sha256(im),
            "tags": {"unit": m.group("unit").lower() if m and m.group("unit") else None,
                     "action": m.group("action").lower() if m and m.group("action") else None,
                     "direction": (m.group("direction") or "").lower() if m else None},
        })
    by_action: dict[str, int] = {}
    by_size: dict[str, int] = {}
    for e in entries:
        if e["tags"]["action"]:
            by_action[e["tags"]["action"]] = by_action.get(e["tags"]["action"], 0) + 1
        key = f"{e['size'][0]}x{e['size'][1]}"
        by_size[key] = by_size.get(key, 0) + 1
    index = {
        "root": str(src), "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "by_action": dict(sorted(by_action.items())),
        "by_size": dict(sorted(by_size.items(), key=lambda kv: -kv[1])),
        "entries": entries,
        "search_hint": "filter entries by tags.action / size / name in your own tooling",
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(index, out)
        rep.add_output(out, "catalog")
        if args.markdown:
            mp = core.guard_output(root / args.markdown, root, args.allow_outside_root)
            mp.write_text(_catalog_markdown(index), encoding="utf-8")
            rep.add_output(mp, "catalog-markdown")
    rep.stat("files", len(entries))
    return {"summary": {"files": len(entries), "actions": len(by_action), "output": str(out)}}


def _catalog_markdown(index: dict) -> str:
    lines = [f"# Asset catalog - {Path(index['root']).name}", "",
             f"{index['file_count']} files, {index['total_bytes'] / 1024:.0f} KiB", "",
             "## Actions", "", "| Action | Count |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in index["by_action"].items()] or ["| _none parsed_ | 0 |"]
    lines += ["", "## Sizes", "", "| Size | Count |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in index["by_size"].items()]
    lines += ["", "## Files", "", "| Path | Size | Action | Colours |", "| --- | --- | --- | --- |"]
    for e in index["entries"][:400]:
        lines.append(f"| {e['path']} | {e['size'][0]}x{e['size'][1]} | {e['tags']['action'] or '-'} | {e['colors']} |")
    return "\n".join(lines) + "\n"


def op_duplicates(args, root: Path, rep: core.Reporter):
    """Find exact and visually similar sprites, frames and palettes."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    files = core.list_images(src, recursive=True) if src.is_dir() else [src]
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    items = []
    for p in files:
        im = core.load_image(p, max_pixels=args.max_pixels)
        items.append({"path": p.name, "sha": core.image_sha256(im),
                      "dhash": core.near_duplicate_hash(im), "size": list(im.size)})
    exact: dict[str, list[str]] = {}
    for it in items:
        exact.setdefault(it["sha"], []).append(it["path"])
    exact_groups = [v for v in exact.values() if len(v) > 1]
    near = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["sha"] == b["sha"]:
                continue
            dist = core.hamming(a["dhash"], b["dhash"])
            if dist <= args.threshold:
                near.append({"a": a["path"], "b": b["path"], "distance": dist,
                             "similarity": round(1 - dist / 256, 3)})
    near.sort(key=lambda n: n["distance"])
    payload = {"root": str(src), "files": len(items), "exact_groups": exact_groups,
               "near_duplicates": near[: args.max_pairs], "near_total": len(near),
               "threshold": args.threshold,
               "wasted_bytes": sum(sum(f.stat().st_size for f in files if f.name in g[1:])
                                   for g in exact_groups)}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "duplicate-report")
    if exact_groups:
        rep.warn("EXACT_DUPLICATES", f"{len(exact_groups)} sets of byte-identical images")
    return {"summary": {"files": len(items), "exact_groups": len(exact_groups),
                        "near_pairs": len(near), "wasted_bytes": payload["wasted_bytes"],
                        "output": str(out)}}


def op_style(args, root: Path, rep: core.Reporter):
    """Extract practical style rules from approved examples."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    files = core.list_images(src, recursive=True) if src.is_dir() else [src]
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    colour_counter: dict[tuple[int, int, int], int] = {}
    sizes = []
    alphas = []
    edge_share = []
    outline_px = 0
    total_px = 0
    for p in files[: args.max_files]:
        im = core.load_image(p, max_pixels=args.max_pixels)
        sizes.append(im.size)
        for (r, g, b, a), n in core.color_counts(im).items():
            if a == 128 or (0 < a < 255):
                alphas.append(a)
            if a >= 250 and (r + g + b) < 130:
                outline_px += n
            if a > 0:
                colour_counter[(r, g, b)] = colour_counter.get((r, g, b), 0) + n
                total_px += n
        mask = im.split()[-1].point(lambda v: 255 if v > 0 else 0)
        edge = mask.filter(ImageFilter.FIND_EDGES)
        edge_share.append(core.opaque_pixels(edge) / max(1, core.opaque_pixels(mask)))
    top_colours = sorted(colour_counter.items(), key=lambda kv: -kv[1])[: args.top_colours]
    widths = sorted({s[0] for s in sizes})
    heights = sorted({s[1] for s in sizes})
    semi = len(alphas) / max(1, sum(colour_counter.values()))
    payload = {
        "root": str(src), "files": len(files),
        "canvas": {"widths": widths[:12], "heights": heights[:12],
                   "modal": _mode([f"{w}x{h}" for w, h in sizes])},
        "palette": [{"hex": "#%02x%02x%02x" % c, "share": round(n / total_px, 4)}
                    for c, n in top_colours],
        "unique_colours": len(colour_counter),
        "anti_aliasing": {"semi_transparent_share": round(semi, 4),
                          "verdict": "hard edges" if semi < 0.01 else "soft/anti-aliased edges"},
        "outline": {"dark_outline_share": round(outline_px / max(1, total_px), 4),
                    "verdict": "outlined" if outline_px / max(1, total_px) > 0.02 else "unoutlined"},
        "detail": {"mean_edge_share": round(sum(edge_share) / len(edge_share), 4),
                   "verdict": "high detail" if sum(edge_share) / len(edge_share) > 0.35 else "moderate detail"},
        "rules_extracted": [
            f"canvas: {_mode([f'{w}x{h}' for w, h in sizes])}",
            f"palette: keep to {min(32, len(colour_counter))} named colours",
            f"edges: {'hard, no anti-aliasing' if semi < 0.01 else 'soft edges allowed'}",
            f"outline: {'dark 1px outline' if outline_px / max(1, total_px) > 0.02 else 'no outline'}",
        ],
        "note": "rules are statistical summaries of approved work, not a licence to copy it",
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "style-report")
    return {"summary": {"files": len(files), "modal_canvas": payload["canvas"]["modal"],
                        "unique_colours": len(colour_counter), "output": str(out)}}


def _mode(values: list[str]) -> str:
    if not values:
        return "unknown"
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=counts.get)


def op_gaps(args, root: Path, rep: core.Reporter):
    """Compare the project plan with existing files and list what is missing."""
    plan_path = Path(args.plan) if Path(args.plan).is_absolute() else root / args.plan
    plan = core.load_json(plan_path)
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"--input must be a folder of existing assets: {src}")
    existing = {p.stem.lower() for p in core.list_images(src, recursive=True)}
    axes = plan.get("axes") or {}
    if not axes:
        raise SFError("BAD_PLAN", "plan JSON needs an 'axes' object: {unit: [actions]}")
    missing, present = [], []
    directions = plan.get("directions") or []
    for unit, actions in axes.items():
        for action in actions:
            for direction in (directions or [""]):
                stem = f"{unit}_{action}" + (f"_{direction}" if direction else "")
                if stem.lower() in existing or any(e.startswith(stem.lower()) for e in existing):
                    present.append(stem)
                else:
                    missing.append(stem)
    payload = {"plan": str(plan_path), "root": str(src),
               "expected": len(present) + len(missing), "present": len(present),
               "missing": missing, "missing_count": len(missing),
               "coverage": round(len(present) / max(1, len(present) + len(missing)), 3),
               "existing_files": len(existing)}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "gap-report")
    if missing:
        rep.warn("MISSING_ASSETS", f"{len(missing)} planned assets are missing", example=missing[:5])
    return {"summary": {"expected": payload["expected"], "present": len(present),
                        "missing": len(missing), "coverage": payload["coverage"], "output": str(out)}}


def op_layers(args, root: Path, rep: core.Reporter):
    """Suggest part/layer splits from alpha structure and colour regions."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels)
    bbox = core.alpha_bbox(im)
    if not bbox:
        raise SFError("EMPTY_IMAGE", f"{src.name} is fully transparent")
    art = im.crop(bbox)
    h = art.height
    bands = {"head": (0.0, 0.33), "torso": (0.33, 0.68), "legs_feet": (0.68, 1.0)}
    suggestions = []
    mask = art.split()[-1]
    for name, (a, b) in bands.items():
        y0, y1 = int(a * h), int(b * h)
        band = art.crop((0, y0, art.width, y1))
        band_mask = mask.crop((0, y0, art.width, y1))
        if core.opaque_pixels(band_mask) == 0:
            continue
        suggestions.append({"layer": name, "y": [y0, y1], "pixels": core.opaque_pixels(band_mask),
                            "width_ratio": round(core.opaque_pixels(band_mask) / max(1, core.opaque_pixels(mask)), 3)})
    # dominant horizontal split points = rows with fewest opaque pixels
    row_counts = []
    px = mask.load()
    for y in range(h):
        row_counts.append(sum(1 for x in range(art.width) if px[x, y]))
    candidates = sorted(range(int(h * 0.2), max(1, int(h * 0.85))), key=lambda y: (row_counts[y], abs(y - h * 0.45)))
    payload = {
        "source": str(src), "content_bbox": list(bbox), "content_size": [art.width, art.height],
        "bands": suggestions,
        "split_candidates_y": candidates[:3],
        "draw_order_suggestion": [s["layer"] for s in reversed(suggestions)],
        "note": "geometry-based suggestion only; verify against the design before cutting layers",
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "layer-plan")
    return {"summary": {"bands": len(suggestions), "splits": payload["split_candidates_y"],
                        "output": str(out)}}


# --------------------------------------------------------------------------- #
# autotile
# --------------------------------------------------------------------------- #

AUTOTILE_BITS = {
    "n": 1, "e": 2, "s": 4, "w": 8,
}


def op_autotile(args, root: Path, rep: core.Reporter):
    """Derive 4-bit / blob connection rules from a terrain sheet or generate one."""
    if args.mode == "generate":
        tile = core.parse_size(args.tile_size)
        out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                    root, args.allow_outside_root, as_dir=True)
        ground = tuple(int(args.ground.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        edge = tuple(int(args.edge.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        rules = []
        for mask in range(16):
            img = Image.new("RGBA", tile, ground + (255,))
            d = ImageDraw.Draw(img)
            w = max(1, tile[0] // 16)
            if not mask & AUTOTILE_BITS["n"]:
                d.rectangle([0, 0, tile[0] - 1, w - 1], fill=edge + (255,))
            if not mask & AUTOTILE_BITS["s"]:
                d.rectangle([0, tile[1] - w, tile[0] - 1, tile[1] - 1], fill=edge + (255,))
            if not mask & AUTOTILE_BITS["w"]:
                d.rectangle([0, 0, w - 1, tile[1] - 1], fill=edge + (255,))
            if not mask & AUTOTILE_BITS["e"]:
                d.rectangle([tile[0] - w, 0, tile[0] - 1, tile[1] - 1], fill=edge + (255,))
            if not args.dry_run:
                core.save_image(img, out_dir / f"tile_{mask:04b}.png")
            rules.append({"mask": mask, "bits": f"{mask:04b}", "neighbours": [
                k for k, v in AUTOTILE_BITS.items() if mask & v],
                "file": f"tile_{mask:04b}.png"})
        if not args.dry_run:
            rep.add_output(out_dir, "directory", tiles=16)
        return {"summary": {"mode": "generate", "tiles": 16, "out": str(out_dir)},
                "rules": rules}
    # derive from a sheet
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not args.grid:
        raise SFError("MISSING_INPUT", "--grid COLSxROWS is required when deriving from a sheet")
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", args.grid)
    if not m:
        raise SFError("BAD_GRID", f"--grid expects COLSxROWS, got '{args.grid}'")
    im = core.load_image(src, max_pixels=args.max_pixels)
    cols, rows = int(m.group(1)), int(m.group(2))
    frames = core.grid_frames(im, cols, rows, skip_empty=False)
    edges = []
    for i, fr in enumerate(frames):
        mask = fr.split()[-1]
        px = mask.load()
        w, h = fr.size
        edges.append({
            "index": i,
            "north_open": all(px[x, 0] < 128 for x in range(w)),
            "south_open": all(px[x, h - 1] < 128 for x in range(w)),
            "west_open": all(px[0, y] < 128 for y in range(h)),
            "east_open": all(px[w - 1, y] < 128 for y in range(h)),
        })
    consistent = len({tuple(e[k] for k in ("north_open", "south_open", "west_open", "east_open")) for e in edges}) == 1
    payload = {"mode": "derive", "source": str(src), "grid": [cols, rows], "tiles": edges,
               "edges_uniform": consistent,
               "recommendation": "tiles share the same open-edge pattern; add a border rule per mask"
               if consistent else "tiles differ on their edges; map each tile to a 4-bit mask"}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "autotile-report")
    return {"summary": {"mode": "derive", "tiles": len(edges), "uniform": consistent,
                        "output": str(out)}}


# --------------------------------------------------------------------------- #
# collision
# --------------------------------------------------------------------------- #

def op_collision(args, root: Path, rep: core.Reporter):
    """Generate collision masks from sprite alpha."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels)
    bbox = core.alpha_bbox(im, args.alpha_threshold)
    if not bbox:
        raise SFError("EMPTY_IMAGE", f"{src.name} has no opaque pixels")
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    shapes = []
    shapes.append({"type": "box", "x": x0, "y": y0, "w": w, "h": h})
    shapes.append({"type": "circle", "cx": x0 + w / 2, "cy": y0 + h / 2,
                   "r": round(max(w, h) / 2, 2)})
    shapes.append({"type": "capsule", "x": x0, "y": y0, "w": w, "h": h,
                   "note": "vertical capsule inscribed in the content bbox"})
    if args.polygon:
        art = im.crop(bbox).split()[-1]
        px = art.load()
        points = []
        step = max(1, args.polygon_step)
        for y in range(0, art.height, step):
            xs = [x for x in range(art.width) if px[x, y] >= args.alpha_threshold]
            if xs:
                points.append((min(xs) + x0, y + y0))
        for y in range(art.height - 1, -1, -step):
            xs = [x for x in range(art.width) if px[x, y] >= args.alpha_threshold]
            if xs:
                points.append((max(xs) + x0, y + y0))
        shapes.append({"type": "polygon", "points": [[int(a), int(b)] for a, b in points]})
    fill = core.opaque_pixels(im.crop(bbox)) / max(1, w * h)
    payload = {"source": str(src), "size": list(im.size), "content_bbox": list(bbox),
               "alpha_threshold": args.alpha_threshold, "fill_ratio": round(fill, 3),
               "masks": shapes,
               "recommendation": ("box is enough for blocky characters" if fill > 0.6 else
                                  "polygon mask fits this silhouette better than a box")}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "collision-masks")
        if args.overlay:
            ov = im.copy()
            d = ImageDraw.Draw(ov)
            d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(0, 255, 120, 255), width=1)
            d.ellipse([x0 + w / 2 - h / 2, y0, x0 + w / 2 + h / 2, y1], outline=(255, 200, 0, 255), width=1)
            op = core.guard_output(root / args.overlay, root, args.allow_outside_root)
            core.save_image(ov.resize((ov.width * 3, ov.height * 3), Image.Resampling.NEAREST), op)
            rep.add_output(op, "overlay")
    return {"summary": {"masks": len(shapes), "fill_ratio": payload["fill_ratio"],
                        "recommendation": payload["recommendation"], "output": str(out)}}


# --------------------------------------------------------------------------- #
# depth / normal / emission
# --------------------------------------------------------------------------- #

def op_depth(args, root: Path, rep: core.Reporter):
    """Generate depth, normal and emission helper maps from a sprite."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    im = core.load_image(src, max_pixels=args.max_pixels)
    alpha = im.split()[-1]
    grey = ImageOps.grayscale(im)
    grey = ImageOps.autocontrast(grey)
    if args.height_from == "alpha":
        depth = alpha
    else:
        depth = grey
    depth = ImageOps.invert(depth).point(lambda v: int(v * args.depth_gain / 255))
    depth = Image.merge("RGBA", (depth, depth, depth, alpha))
    dx = depth.convert("L").filter(ImageFilter.Kernel((3, 3), [-1, 0, 1, -2, 0, 2, -1, 0, 1], scale=args.normal_strength, offset=128))
    dy = depth.convert("L").filter(ImageFilter.Kernel((3, 3), [-1, -2, -1, 0, 0, 0, 1, 2, 1], scale=args.normal_strength, offset=128))
    r, g = dx, dy
    b = Image.new("L", im.size, 255)
    normal = Image.merge("RGBA", (r, g, b, alpha))
    luma = grey.point(lambda v: 255 if v > args.emission_threshold else 0)
    emission = Image.merge("RGBA", (luma, luma, luma, alpha))
    base = Path(args.output) if Path(args.output).is_absolute() else root / args.output
    base = core.guard_output(base, root, args.allow_outside_root)
    stem = base.with_suffix("")
    if not args.dry_run:
        outs = []
        for kind, img in (("depth", depth), ("normal", normal), ("emission", emission)):
            p = Path(f"{stem}-{kind}.png")
            core.save_image(img, p)
            outs.append(str(p))
            rep.add_output(p, kind)
        core.save_json({"source": str(src), "maps": outs, "normal_strength": args.normal_strength,
                        "height_from": args.height_from}, Path(f"{stem}-maps.json"))
        rep.add_output(Path(f"{stem}-maps.json"), "depth-manifest")
    rep.stat("maps", 3)
    return {"summary": {"maps": 3, "base": str(stem)}}


# --------------------------------------------------------------------------- #
# motion
# --------------------------------------------------------------------------- #

def op_motion(args, root: Path, rep: core.Reporter):
    """Key-pose, arc, contact and timing notes from a sequence."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    profs = []
    for i, fr in enumerate(fs.frames):
        bbox = core.alpha_bbox(fr)
        if not bbox:
            continue
        profs.append({"frame": i, "bbox": list(bbox),
                      "cx": (bbox[0] + bbox[2]) / 2, "cy": (bbox[1] + bbox[3]) / 2,
                      "bottom": bbox[3], "area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])})
    if len(profs) < 2:
        raise SFError("NEEDS_TWO_FRAMES", "motion analysis needs at least two non-empty frames")
    speeds = []
    for a, b in zip(profs, profs[1:]):
        dist = math.dist((a["cx"], a["cy"]), (b["cx"], b["cy"]))
        speeds.append({"frames": [a["frame"], b["frame"]], "distance_px": round(dist, 2)})
    still = [s for s in speeds if s["distance_px"] < args.still_threshold]
    contacts = [p["frame"] for p in profs if p["bottom"] >= max(q["bottom"] for q in profs) - 1]
    areas = [p["area"] for p in profs]
    monotone = all(b >= a for a, b in zip(areas, areas[1:])) or all(b <= a for a, b in zip(areas, areas[1:]))
    arc = []
    for a, b, c in zip(profs, profs[1:], profs[2:]):
        cross = (b["cx"] - a["cx"]) * (c["cy"] - b["cy"]) - (b["cy"] - a["cy"]) * (c["cx"] - b["cx"])
        arc.append({"frame": b["frame"], "turn": round(cross, 2)})
    payload = {
        "source": str(src), "frames": len(fs.frames), "durations_ms": fs.durations_ms,
        "total_ms": fs.total_ms, "fps": round(1000 / (fs.total_ms / len(fs.frames)), 2) if fs.frames else 0,
        "per_frame": profs, "step_distances": speeds, "holds": still,
        "contact_frames": contacts, "area_monotone": monotone,
        "arc_samples": arc,
        "notes": [
            f"{len(still)} stationary transitions (anticipation or hold candidates)",
            f"ground contact on frames {contacts[:12]}",
            "area grows steadily (extension/approach)" if monotone else "area oscillates (impact/squash present)",
        ],
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "motion-report")
    return {"summary": {"frames": len(fs.frames), "holds": len(still),
                        "contact_frames": contacts[:8], "output": str(out)}}


# --------------------------------------------------------------------------- #
# incremental
# --------------------------------------------------------------------------- #

def op_incremental(args, root: Path, rep: core.Reporter):
    """Rebuild only changed outputs using a content-hash state file."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"--input must be a source folder: {src}")
    state_path = Path(args.state) if Path(args.state).is_absolute() else root / args.state
    state = core.load_json(state_path) if state_path.exists() else {"files": {}}
    old: dict[str, str] = state.get("files", {})
    current: dict[str, str] = {}
    changed, added, unchanged = [], [], []
    for p in core.list_images(src, recursive=True):
        rel = p.relative_to(src).as_posix()
        digest = core.file_sha256(p)
        current[rel] = digest
        if rel not in old:
            added.append(rel)
        elif old[rel] != digest:
            changed.append(rel)
        else:
            unchanged.append(rel)
    removed = [k for k in old if k not in current]
    to_build = sorted(added + changed)
    payload = {"source": str(src), "state": str(state_path), "files": len(current),
               "added": added, "changed": changed, "removed": removed, "unchanged": len(unchanged),
               "rebuild": to_build, "full_rebuild": not state_path.exists()}
    if not args.dry_run:
        target_state = state_path if state_path.is_absolute() else core.guard_output(state_path, root, args.allow_outside_root)
        core.save_json({"files": current,
                        "updated_utc": core.time.strftime("%Y-%m-%dT%H:%M:%SZ", core.time.gmtime())},
                       target_state)
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        core.save_json(payload, out)
        rep.add_output(out, "incremental-plan")
    rep.stat("rebuild", len(to_build))
    return {"summary": {"files": len(current), "rebuild": len(to_build),
                        "unchanged": len(unchanged), "removed": len(removed)}}


# --------------------------------------------------------------------------- #
# review package
# --------------------------------------------------------------------------- #

def op_review_package(args, root: Path, rep: core.Reporter):
    """Build a static human review page with frames, issues and approval choices."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    findings = []
    if args.findings:
        fp = Path(args.findings) if Path(args.findings).is_absolute() else root / args.findings
        data = core.load_json(fp)
        findings = data.get("findings", []) if isinstance(data, dict) else data
    entries = [(f, f"f{i} {fs.durations_ms[i] if i < len(fs.durations_ms) else 100}ms")
               for i, f in enumerate(fs.frames)]
    sheet = core.make_contact_sheet(entries, cols=args.cols)
    sheet_name = "review-contact-sheet.png"
    rows = "".join(
        f'<tr><td>{i}</td><td>{label}</td><td><input type="radio" name="f{i}" value="approve"></td>'
        f'<td><input type="radio" name="f{i}" value="revise"></td></tr>'
        for i, (_, label) in enumerate(entries))
    issue_rows = "".join(
        f'<tr><td>{it.get("severity", "")}</td><td>{it.get("code", "")}</td>'
        f'<td>{str(it.get("message", ""))[:160]}</td></tr>' for it in findings[:200])
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Review - {args.name}</title>
<style>body{{font:14px system-ui,-apple-system,sans-serif;margin:24px;max-width:1000px;color:#1a1a20}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}td,th{{border-bottom:1px solid #d8d8e0;padding:5px 8px;text-align:left}}
h1{{font-size:22px}}code{{background:#f2f2f6;padding:1px 4px;border-radius:3px}}
textarea{{width:100%;height:110px;font:13px monospace}}
.badge{{display:inline-block;background:#eef;border-radius:4px;padding:2px 6px;margin-right:6px}}</style></head>
<body>
<h1>Review package: {args.name}</h1>
<p><span class="badge">{len(entries)} frames</span><span class="badge">{fs.total_ms} ms total</span>
<span class="badge">{len(findings)} findings</span></p>
<p>Contact sheet: <code>{sheet_name}</code> (open it beside this page)</p>
<h2>Findings</h2>
<table><thead><tr><th>Severity</th><th>Code</th><th>Message</th></tr></thead><tbody>{issue_rows or '<tr><td colspan=3>none supplied</td></tr>'}</tbody></table>
<h2>Per-frame verdict</h2>
<table><thead><tr><th>#</th><th>Frame</th><th>Approve</th><th>Revise</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Reviewer notes</h2>
<textarea placeholder="e.g. 'frame 4 outline broken', 'cape lags 40ms'"></textarea>
<p><small>Static page generated by {core.TOOL_NAME} {core.TOOL_VERSION}; no data leaves this folder.</small></p>
</body></html>
"""
    payload = {"name": args.name, "frames": len(entries), "findings": len(findings),
               "contact_sheet": sheet_name, "issues": findings[:200]}
    if not args.dry_run:
        core.save_image(sheet, out_dir / sheet_name)
        (out_dir / "review.html").write_text(html, encoding="utf-8")
        core.save_json(payload, out_dir / "review.json")
        rep.add_output(out_dir, "directory", frames=len(entries))
    return {"summary": {"frames": len(entries), "findings": len(findings), "out": str(out_dir)}}


# --------------------------------------------------------------------------- #
# frame-detect (alias of sheet detect, kept for the catalog capability)
# --------------------------------------------------------------------------- #

def op_frame_detect(args, root: Path, rep: core.Reporter):
    """Detect grid or irregular frame layout (delegates to the sheet detector)."""
    spec = importlib.util.spec_from_file_location("sf_sheet_for_detect", HERE / "tools-spriteforge-sheet.py")
    sheet_mod = importlib.util.module_from_spec(spec)
    sys.modules["sf_sheet_for_detect"] = sheet_mod
    spec.loader.exec_module(sheet_mod)  # type: ignore[union-attr]
    return sheet_mod.op_detect(args, root, rep)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_catalog(p):
    p.add_argument("--input", "-i", required=True, help="folder to scan")
    p.add_argument("--output", "-o", default="catalog.json")
    p.add_argument("--markdown", help="also write a Markdown summary here")
    p.add_argument("--shallow", action="store_true", help="do not recurse")
    p.add_argument("--max-files", type=int, default=4000)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_dupes(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="duplicates.json")
    p.add_argument("--threshold", type=int, default=6, help="max dHash distance to call a near-duplicate")
    p.add_argument("--max-pairs", type=int, default=100)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_style(p):
    p.add_argument("--input", "-i", required=True, help="folder of approved examples")
    p.add_argument("--output", "-o", default="style-report.json")
    p.add_argument("--top-colours", type=int, default=12)
    p.add_argument("--max-files", type=int, default=64)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_gaps(p):
    p.add_argument("--input", "-i", required=True, help="folder of existing assets")
    p.add_argument("--plan", required=True, help="plan JSON with an axes object")
    p.add_argument("--output", "-o", default="gaps.json")


def _p_layers(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="layers.json")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_autotile(p):
    p.add_argument("--mode", choices=["derive", "generate"], default="derive")
    p.add_argument("--input", "-i")
    p.add_argument("--grid", help="COLSxROWS of the terrain sheet")
    p.add_argument("--output", "-o", default="autotile.json")
    p.add_argument("--tile-size", default="32x32", help="generated tile size")
    p.add_argument("--ground", default="#6fae4a")
    p.add_argument("--edge", default="#3f7a48")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_collision(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="collision.json")
    p.add_argument("--alpha-threshold", type=int, default=128)
    p.add_argument("--polygon", action="store_true", default=True)
    p.add_argument("--polygon-step", type=int, default=4)
    p.add_argument("--overlay", help="write a 3x overlay PNG with masks drawn")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_depth(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True, help="base path; -depth/-normal/-emission are appended")
    p.add_argument("--height-from", choices=["luma", "alpha"], default="luma")
    p.add_argument("--depth-gain", type=float, default=1.0)
    p.add_argument("--normal-strength", type=float, default=1.0)
    p.add_argument("--emission-threshold", type=int, default=220)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_motion(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="motion.json")
    p.add_argument("--still-threshold", type=float, default=0.5)
    p.add_argument("--max-frames", type=int, default=4096)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_incremental(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--state", required=True, help="hash state JSON (created on first run)")
    p.add_argument("--output", "-o", default="incremental.json")


def _p_detect(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--grid", help="COLSxROWS if known")
    p.add_argument("--irregular", action="store_true")
    p.add_argument("--gap", type=int, default=1)
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--margin", type=int, default=0)
    p.add_argument("--alpha-threshold", type=int, default=1)
    p.add_argument("--output", "-o")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_review(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True, help="output review folder")
    p.add_argument("--name", default="asset")
    p.add_argument("--findings", help="findings JSON from a validate run")
    p.add_argument("--cols", type=int, default=6)
    p.add_argument("--max-frames", type=int, default=512)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


OPS = {
    "catalog": (_p_catalog, op_catalog),
    "duplicates": (_p_dupes, op_duplicates),
    "style": (_p_style, op_style),
    "gaps": (_p_gaps, op_gaps),
    "layers": (_p_layers, op_layers),
    "autotile": (_p_autotile, op_autotile),
    "collision": (_p_collision, op_collision),
    "depth": (_p_depth, op_depth),
    "motion": (_p_motion, op_motion),
    "incremental": (_p_incremental, op_incremental),
    "frame-detect": (_p_detect, op_frame_detect),
    "review-package": (_p_review, op_review_package),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "analyze",
        "spriteforge analyze: catalogues, duplicates, masks, maps and review packages",
        OPS,
    ))
