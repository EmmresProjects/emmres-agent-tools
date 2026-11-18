#!/usr/bin/env python3
"""spriteforge validate: QA, drift, readability, compliance and regression checks.

Read-only by default: checks never modify their inputs. Each operation returns
``ok`` plus a list of findings with severity so a pipeline can gate on them.

Operations
----------
  sheet            dimensions, cell fit, frame count, alpha, padding, metadata
  flicker          frame-to-frame colour/position/alpha drift and pops
  silhouette       readability at target size, greyscale, blur, contrast
  palette          off-palette colours, weak ramps, contrast, team overlap
  seam             tiled seams and loop jumps
  anchors          feet/pivots/ground contact across frames
  regression       diff new exports against approved files
  accessibility    contrast, flashing risk, colour-only communication
  consistency      size/colour/position drift across a set
  engine-smoke     engine import prerequisites (structure only)
  similarity       originality screen against supplied designs   (guarded)
  content-policy   age rating / platform / workplace screen      (guarded)
  cultural         unsupported cultural claims -> trusted review  (guarded)

Guarded checks need ``--approve <token>`` because their output can block a
release, and they encode policy a human must own.
"""

from __future__ import annotations

import argparse
import colorsys
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_validate", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_validate"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageChops = core.ImageChops
ImageFilter = core.ImageFilter
ImageStat = core.ImageStat


# --------------------------------------------------------------------------- #
# findings helper
# --------------------------------------------------------------------------- #

class Findings:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, severity: str, code: str, message: str, **context) -> None:
        assert severity in {"info", "warn", "error"}
        self.items.append({"severity": severity, "code": code, "message": message, "context": context})

    def error(self, code, message, **ctx):
        self.add("error", code, message, **ctx)

    def warn(self, code, message, **ctx):
        self.add("warn", code, message, **ctx)

    def info(self, code, message, **ctx):
        self.add("info", code, message, **ctx)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.items if i["severity"] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.items if i["severity"] == "warn")

    def summary(self) -> dict:
        return {"ok": self.errors == 0, "errors": self.errors, "warnings": self.warnings,
                "findings": len(self.items)}


def luminance(rgb) -> float:
    r, g, b = (c / 255 for c in rgb[:3])
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def load_fs(args, root: Path) -> tuple[core.FrameSet, Path]:
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if getattr(args, "grid", None):
        m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", args.grid)
        if not m:
            raise SFError("BAD_GRID", f"--grid expects COLSxROWS, got '{args.grid}'")
        im = core.load_image(src, max_pixels=args.max_pixels)
        frames = core.grid_frames(im, int(m.group(1)), int(m.group(2)))
        return core.FrameSet(frames=frames, durations_ms=[100] * len(frames), source=str(src)), src
    return core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels), src


def add_grid(p):
    p.add_argument("--grid", help="slice a packed sheet as COLSxROWS")
    p.add_argument("--max-frames", type=int, default=4096)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def write_report(args, root: Path, rep: core.Reporter, payload: dict) -> None:
    if getattr(args, "output", None) and not args.dry_run:
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        core.save_json(payload, out)
        rep.add_output(out, "report")


# --------------------------------------------------------------------------- #
# sheet validation
# --------------------------------------------------------------------------- #

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-. ]{0,80}$")


def op_sheet(args, root: Path, rep: core.Reporter):
    """Check dimensions, cell fit, frame count, alpha, padding and metadata."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    f = Findings()
    if args.metadata:
        mp = Path(args.metadata) if Path(args.metadata).is_absolute() else root / args.metadata
        meta = core.load_json(mp)
        if not isinstance(meta, dict) or "frames" not in meta:
            raise SFError("BAD_METADATA", "metadata must be a spriteforge sheet JSON")
        image = meta.get("image")
        sheet_path = src if src.exists() else (mp.parent / (image or ""))
        if not sheet_path.exists():
            raise SFError("MISSING_SHEET", f"metadata points at a missing sheet image: {image}")
        im = core.load_image(sheet_path, max_pixels=args.max_pixels)
        frames = meta["frames"]
        cell = (meta.get("cell", {}).get("width", 0), meta.get("cell", {}).get("height", 0))
        sheet_size = meta.get("sheet", {})
        if sheet_size and (sheet_size.get("width"), sheet_size.get("height")) != im.size:
            f.error("DIMENSION_MISMATCH", f"metadata says {sheet_size.get('width')}x{sheet_size.get('height')} "
                    f"but the sheet is {im.width}x{im.height}")
        if args.frames_expected and len(frames) != args.frames_expected:
            f.error("FRAME_COUNT", f"expected {args.frames_expected} frames, metadata has {len(frames)}")
        names = set()
        for rec in frames:
            fr = rec["frame"]
            if fr["x"] < 0 or fr["y"] < 0 or fr["x"] + fr["w"] > im.width or fr["y"] + fr["h"] > im.height:
                f.error("FRAME_OUT_OF_BOUNDS", f"{rec['name']} rect {fr} is outside {im.width}x{im.height}")
            if cell[0] and cell[1] and (fr["w"] != cell[0] or fr["h"] != cell[1]):
                f.warn("CELL_MISMATCH", f"{rec['name']} is {fr['w']}x{fr['h']}, cell is {cell[0]}x{cell[1]}")
            if not NAME_RE.match(rec["name"]):
                f.error("BAD_FRAME_NAME", f"frame name '{rec['name']}' breaks the naming rule")
            if rec["name"] in names:
                f.error("DUPLICATE_FRAME_NAME", f"duplicate frame name {rec['name']}")
            names.add(rec["name"])
            alpha_bbox = core.alpha_bbox(im.crop((fr["x"], fr["y"], fr["x"] + fr["w"], fr["y"] + fr["h"])))
            if alpha_bbox is None:
                f.warn("EMPTY_FRAME", f"{rec['name']} is fully transparent")
            elif args.padding_check:
                x0, y0, x1, y1 = alpha_bbox
                if min(x0, y0, fr["w"] - x1, fr["h"] - y1) < 1:
                    f.warn("NO_PADDING", f"{rec['name']} art touches the cell edge (engine bleed risk)")
        if not core.has_transparency(im) and args.require_alpha:
            f.error("NO_ALPHA", "sheet has no transparent pixels; engines expect an alpha channel")
        payload = {"check": "sheet", "sheet": str(sheet_path), "size": list(im.size),
                   "frame_count": len(frames), **f.summary(), "findings": f.items}
    else:
        im = core.load_image(src, max_pixels=args.max_pixels)
        if args.grid:
            m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", args.grid)
            if not m:
                raise SFError("BAD_GRID", f"--grid expects COLSxROWS, got '{args.grid}'")
            cols, rows = int(m.group(1)), int(m.group(2))
            try:
                cell = core.grid_cell(im.size, cols, rows, padding=args.padding, margin=args.margin)
            except SFError as exc:
                f.error(exc.code, exc.message)
                cell = (0, 0)
            frames = core.grid_frames(im, cols, rows, padding=args.padding, margin=args.margin,
                                      skip_empty=False) if cell != (0, 0) else []
            empty = sum(1 for fr in frames if core.alpha_bbox(fr) is None)
            if empty:
                f.warn("EMPTY_FRAMES", f"{empty} of {len(frames)} grid cells are fully transparent")
            if cell != (0, 0) and cell[0] != cell[1]:
                f.info("NON_SQUARE_CELL", f"cell is {cell[0]}x{cell[1]}")
            payload = {"check": "sheet", "sheet": str(src), "size": list(im.size),
                       "grid": [cols, rows], "cell": list(cell), "frame_count": len(frames),
                       **f.summary(), "findings": f.items}
        else:
            if args.cols and im.width % args.cols:
                f.error("NOT_DIVISIBLE", f"width {im.width} is not divisible by {args.cols} columns")
            if args.rows and im.height % args.rows:
                f.error("NOT_DIVISIBLE", f"height {im.height} is not divisible by {args.rows} rows")
            if args.frames_expected:
                f.info("FRAME_COUNT_UNVERIFIED", "--frames-expected needs --metadata or --grid to verify")
            if args.require_alpha and not core.has_transparency(im):
                f.error("NO_ALPHA", "sheet has no transparent pixels")
            if args.pow2 and (im.width & (im.width - 1) or im.height & (im.height - 1)):
                f.warn("NOT_POWER_OF_TWO", f"{im.width}x{im.height} is not power-of-two (some GPUs need it)")
            payload = {"check": "sheet", "sheet": str(src), "size": list(im.size),
                       "mode": "image", **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    rep.stat("errors", f.errors)
    return {"summary": {"ok": f.errors == 0, "errors": f.errors, "warnings": f.warnings,
                        "size": payload["size"]}, "findings": f.items}


# --------------------------------------------------------------------------- #
# flicker / drift
# --------------------------------------------------------------------------- #

def frame_profile(im) -> dict:
    rgba = im.convert("RGBA")
    alpha = rgba.split()[-1]
    bbox = alpha.getbbox()
    stat = ImageStat.Stat(rgba.convert("RGB"))
    hsv = rgba.convert("HSV")
    hstat = ImageStat.Stat(hsv)
    return {
        "bbox": list(bbox) if bbox else None,
        "mean_rgb": [round(v, 2) for v in stat.mean],
        "mean_hsv": [round(v, 2) for v in hstat.mean],
        "opaque_px": core.opaque_pixels(rgba),
        "size": list(im.size),
    }


def op_flicker(args, root: Path, rep: core.Reporter):
    """Measure frame-to-frame colour, scale, position and alpha drift."""
    fs, src = load_fs(args, root)
    f = Findings()
    profiles = [frame_profile(fr) for fr in fs.frames]
    steps = []
    for i in range(1, len(profiles)):
        a, b = profiles[i - 1], profiles[i]
        rgb = max(abs(x - y) for x, y in zip(a["mean_rgb"], b["mean_rgb"]))
        hue = max(abs(x - y) for x, y in zip(a["mean_hsv"], b["mean_hsv"]))
        pos = max(abs(x - y) for x, y in zip(a["bbox"], b["bbox"])) if a["bbox"] and b["bbox"] else 0
        scale = (b["opaque_px"] - a["opaque_px"]) / max(1, a["opaque_px"])
        steps.append({"from": i - 1, "to": i, "rgb_delta": round(rgb, 2), "hue_delta": round(hue, 2),
                      "position_delta": pos, "area_delta_ratio": round(scale, 4)})
        if rgb > args.rgb_threshold:
            f.warn("COLOR_FLICKER", f"frame {i-1}->{i} mean colour jumps {rgb:.1f}", frame=i)
        if pos > args.position_threshold:
            f.warn("POSITION_DRIFT", f"frame {i-1}->{i} content shifts {pos}px", frame=i)
        if abs(scale) > args.area_threshold:
            f.warn("SIZE_DRIFT", f"frame {i-1}->{i} area changes {scale*100:.1f}%", frame=i)
    if len({tuple(p["size"]) for p in profiles}) > 1:
        f.error("SIZE_MISMATCH", "frames do not share one canvas size")
    payload = {
        "check": "flicker", "input": str(src), "frames": len(fs.frames),
        "thresholds": {"rgb": args.rgb_threshold, "position_px": args.position_threshold,
                       "area_ratio": args.area_threshold},
        "steps": steps, **f.summary(), "findings": f.items,
    }
    write_report(args, root, rep, payload)
    for item in f.items:
        rep.warn(item["code"], item["message"])
    return {"summary": {"frames": len(fs.frames), "ok": f.errors == 0, "warnings": f.warnings,
                        **({"steps": [s for s in steps if s["rgb_delta"] > args.rgb_threshold
                                      or s["position_delta"] > args.position_threshold][:5]})}}


# --------------------------------------------------------------------------- #
# silhouette readability
# --------------------------------------------------------------------------- #

def op_silhouette(args, root: Path, rep: core.Reporter):
    """Test sprites at target size, greyscale, blur and both backgrounds."""
    fs, src = load_fs(args, root)
    f = Findings()
    results = []
    for i, fr in enumerate(fs.frames):
        bbox = core.alpha_bbox(fr)
        if not bbox:
            f.warn("EMPTY_FRAME", f"frame {i} has no opaque pixels", frame=i)
            continue
        art = fr.crop(bbox)
        target = core.parse_size(args.target_size) if args.target_size else art.size
        small = art.resize(target, Image.Resampling.LANCZOS)
        mask = small.split()[-1].point(lambda v: 255 if v >= 128 else 0)
        area = core.opaque_pixels(mask)
        px = mask.load()
        perimeter = 0
        for y in range(mask.height):
            for x in range(mask.width):
                if px[x, y]:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if nx < 0 or ny < 0 or nx >= mask.width or ny >= mask.height or not px[nx, ny]:
                            perimeter += 1
        compact = (4 * math.pi * area / (perimeter ** 2)) if perimeter else 0.0
        grey = small.convert("L")
        blur = grey.filter(ImageFilter.GaussianBlur(args.blur))
        grey_hist = grey.histogram()
        grey_min = next((i for i, v in enumerate(grey_hist) if v), 0)
        grey_max = next((255 - i for i, v in enumerate(reversed(grey_hist)) if v), 0)
        contrast_dark = contrast((grey_max,) * 3, (18, 18, 20))
        contrast_light = contrast((grey_min,) * 3, (238, 238, 240))
        legibility = round(
            100 * (0.4 * (1 - min(1, abs(compact - 0.5))) + 0.3 * min(1, max(contrast_dark, contrast_light) / 7)
                   + 0.3 * (1 - min(1, (blur.width * blur.height) / max(1, area) / 2))), 1)
        results.append({"frame": i, "target_size": list(target), "fill_px": area,
                        "compactness": round(compact, 3), "contrast_on_dark": contrast_dark,
                        "contrast_on_light": contrast_light, "legibility": legibility,
                        "grade": "good" if legibility >= 65 else "fair" if legibility >= 45 else "weak"})
        if contrast_dark < args.min_contrast and contrast_light < args.min_contrast:
            f.warn("LOW_CONTRAST", f"frame {i} fails contrast on both backgrounds", frame=i)
        if legibility < 45:
            f.warn("WEAK_SILHOUETTE", f"frame {i} legibility {legibility}", frame=i)
    if not results:
        f.error("NO_CONTENT", "no readable frames")
    payload = {"check": "silhouette", "input": str(src), "frames": results,
               "mean_legibility": round(sum(r["legibility"] for r in results) / len(results), 1) if results else 0,
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    if args.preview and not args.dry_run:
        entries = []
        for r in results[:32]:
            fr = fs.frames[r["frame"]]
            bbox = core.alpha_bbox(fr)
            art = fr.crop(bbox) if bbox else fr
            grey = art.convert("L").convert("RGBA")
            entries.append((art, f"f{r['frame']} {r['legibility']:.0f}"))
            entries.append((grey, "grey"))
        pp = core.guard_output(root / args.preview, root, args.allow_outside_root)
        core.save_image(core.make_contact_sheet(entries, cols=8, checker=False), pp)
        rep.add_output(pp, "preview")
    return {"summary": {"frames": len(results), "mean_legibility": payload["mean_legibility"],
                        "ok": f.errors == 0, "warnings": f.warnings}}


# --------------------------------------------------------------------------- #
# palette compliance
# --------------------------------------------------------------------------- #

def op_palette(args, root: Path, rep: core.Reporter):
    """Find off-palette colours, weak ramps and unsafe team-colour overlap."""
    fs, src = load_fs(args, root)
    f = Findings()
    allowed: list[tuple[int, int, int]] = []
    if args.palette:
        data = core.load_json(Path(args.palette) if Path(args.palette).is_absolute() else root / args.palette)
        block = data.get("roles", data.get("colours", data.get("colors", data))) if isinstance(data, dict) else data
        values = list(block.values()) if isinstance(block, dict) else list(block)
        for v in values:
            s = str(v).lstrip("#")
            if len(s) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", s):
                allowed.append(tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)))
        if not allowed:
            raise SFError("EMPTY_PALETTE", "no usable colours in --palette")
    counts: dict[tuple[int, int, int], int] = {}
    for fr in fs.frames:
        for (r, g, b, a), n in core.color_counts(fr).items():
            if a == 0:
                continue
            counts[(r, g, b)] = counts.get((r, g, b), 0) + n
    total = sum(counts.values()) or 1
    unique = len(counts)
    off_palette = {}
    if allowed:
        allowed_set = set(allowed)
        for colour, n in counts.items():
            if colour not in allowed_set:
                nearest = min(allowed, key=lambda c: sum((x - y) ** 2 for x, y in zip(c, colour)))
                dist = math.dist(colour, nearest)
                if dist > args.tolerance:
                    off_palette[colour] = (n, round(dist, 1), nearest)
        ratio = sum(v[0] for v in off_palette.values()) / total
        if ratio > args.max_off_palette_ratio:
            f.error("OFF_PALETTE", f"{ratio*100:.1f}% of art pixels are off-palette "
                    f"(limit {args.max_off_palette_ratio*100:.0f}%)", ratio=round(ratio, 4))
        elif off_palette:
            f.warn("OFF_PALETTE_MINOR", f"{ratio*100:.2f}% off-palette pixels", ratio=round(ratio, 4))
    if args.max_colours and unique > args.max_colours:
        f.error("TOO_MANY_COLOURS", f"{unique} unique colours exceeds --max-colours {args.max_colours}")
    lums = sorted(luminance(c) for c in counts if counts[c] > total * 0.002)
    ramp_ok = False
    if len(lums) >= 3:
        diffs = [lums[i + 1] - lums[i] for i in range(len(lums) - 1)]
        ramp_ok = sum(1 for d in diffs if d > 0.02) >= 2
        if not ramp_ok:
            f.warn("WEAK_RAMP", "colour values are clustered; shading will read as flat")
    team = [c for c in allowed if c in {(214, 69, 69), (63, 127, 209), (79, 165, 100), (219, 172, 61)}]
    team_overlap = None
    if len(team) >= 2:
        pairs = [(contrast(a, b), a, b) for i, a in enumerate(team) for b in team[i + 1:]]
        weakest = min(pairs, key=lambda p: p[0])
        team_overlap = {"pairs": len(pairs), "weakest_contrast": weakest[0]}
        if weakest[0] < 1.6:
            f.warn("TEAM_COLOUR_OVERLAP", f"two team colours have contrast {weakest[0]}:1")
    payload = {"check": "palette", "input": str(src), "unique_colours": unique,
               "total_pixels": total, "allowed": len(allowed),
               "off_palette": [{"rgb": list(k), "count": v[0], "distance": v[1], "nearest": list(v[2])}
                               for k, v in sorted(off_palette.items(), key=lambda kv: -kv[1][0])[:40]],
               "ramp_ok": ramp_ok, "team": team_overlap,
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"unique_colours": unique, "allowed": len(allowed),
                        "off_palette_kinds": len(off_palette), "ok": f.errors == 0,
                        "warnings": f.warnings}}


# --------------------------------------------------------------------------- #
# seam / tile
# --------------------------------------------------------------------------- #

def op_seam(args, root: Path, rep: core.Reporter):
    """Repeat tiles or wrap animations to reveal seams and jumps."""
    fs, src = load_fs(args, root)
    f = Findings()
    report = {"check": "seam", "input": str(src), "frames": len(fs.frames), "tiles": []}
    if len(fs.frames) > 1:
        first, last = fs.frames[0], fs.frames[-1]
        d = core.pixel_diff(first, last)
        report["loop"] = {"changed_ratio": d["changed_ratio"], "identical": d["identical"],
                          "bbox": d["bbox"]}
        if not d["identical"] and d["changed_ratio"] > args.max_loop_ratio:
            f.warn("LOOP_SEAM", f"first/last frames differ on {d['changed_ratio']*100:.1f}% of pixels")
    a, b = fs.frames[0], fs.frames[-1]
    edges = {
        "first_left_vs_last_right": core.pixel_diff(a.crop((0, 0, 1, a.height)), b.crop((b.width - 1, 0, b.width, b.height)))["changed_pixels"],
        "first_top_vs_last_bottom": core.pixel_diff(a.crop((0, 0, a.width, 1)), b.crop((0, b.height - 1, b.width, b.height)))["changed_pixels"],
    }
    report["wrap_edges"] = edges
    if args.tile:
        for i, fr in enumerate(fs.frames[: args.max_tiles]):
            tile_report = {
                "frame": i,
                "left_vs_right": core.pixel_diff(fr.crop((0, 0, 1, fr.height)), fr.crop((fr.width - 1, 0, fr.width, fr.height)))["changed_pixels"],
                "top_vs_bottom": core.pixel_diff(fr.crop((0, 0, fr.width, 1)), fr.crop((0, fr.height - 1, fr.width, fr.height)))["changed_pixels"],
            }
            tile_report["seamless"] = tile_report["left_vs_right"] == 0 and tile_report["top_vs_bottom"] == 0
            report["tiles"].append(tile_report)
            if args.require_tile and not tile_report["seamless"]:
                f.warn("TILE_SEAM", f"frame {i} does not tile exactly", frame=i)
    payload = {**report, **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    if args.preview and not args.dry_run:
        pp = core.guard_output(root / args.preview, root, args.allow_outside_root)
        core.save_image(core.tile_preview(fs.frames[0], 3, 3), pp)
        rep.add_output(pp, "preview")
    return {"summary": {"frames": len(fs.frames), "ok": f.errors == 0,
                        "loop": report.get("loop", {}).get("changed_ratio"), "warnings": f.warnings}}


# --------------------------------------------------------------------------- #
# anchors
# --------------------------------------------------------------------------- #

def op_anchors(args, root: Path, rep: core.Reporter):
    """Audit feet, pivots, sockets and ground contact across frames."""
    fs, src = load_fs(args, root)
    f = Findings()
    rows = []
    boxes = []
    for i, fr in enumerate(fs.frames):
        bbox = core.alpha_bbox(fr)
        if not bbox:
            f.warn("EMPTY_FRAME", f"frame {i} is empty", frame=i)
            continue
        boxes.append(bbox)
        centre = (bbox[0] + bbox[2]) / 2
        rows.append({
            "frame": i, "bbox": list(bbox),
            "bottom_y": bbox[3], "top_y": bbox[1], "centre_x": round(centre, 1),
            "height": bbox[3] - bbox[1], "width": bbox[2] - bbox[0],
            "ground_offset_px": fr.height - bbox[3],
        })
    if not rows:
        raise SFError("NO_CONTENT", "no opaque frames to measure")
    bottoms = [r["bottom_y"] for r in rows]
    centres = [r["centre_x"] for r in rows]
    heights = [r["height"] for r in rows]
    baseline = max(bottoms) if args.baseline == "max" else (sum(bottoms) / len(bottoms))
    drift = [abs(b - baseline) for b in bottoms]
    for r, d in zip(rows, drift):
        if d > args.max_foot_drift:
            f.error("FOOT_DRIFT", f"frame {r['frame']} foot line is {d:.0f}px off the baseline",
                    frame=r["frame"], drift=d)
        if abs(r["centre_x"] - (sum(centres) / len(centres))) > args.max_centre_drift:
            f.warn("CENTRE_DRIFT", f"frame {r['frame']} body centre shifts horizontally", frame=r["frame"])
        if heights and abs(r["height"] - sorted(heights)[len(heights) // 2]) > args.max_height_drift:
            f.warn("HEIGHT_DRIFT", f"frame {r['frame']} height differs from the median", frame=r["frame"])
    pivot = {"x": fs.size[0] // 2, "y": max(bottoms)} if args.pivot == "auto" else \
        {"x": args.pivot_x, "y": args.pivot_y}
    payload = {"check": "anchors", "input": str(src), "pivot": pivot,
               "baseline_y": round(baseline, 1), "frames": rows,
               "max_foot_drift_px": round(max(drift), 1),
               "median_height": sorted(heights)[len(heights) // 2],
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"frames": len(rows), "baseline_y": payload["baseline_y"],
                        "max_foot_drift_px": payload["max_foot_drift_px"],
                        "ok": f.errors == 0, "warnings": f.warnings}}


# --------------------------------------------------------------------------- #
# regression
# --------------------------------------------------------------------------- #

def op_regression(args, root: Path, rep: core.Reporter):
    """Compare new exports with approved files and report only real changes."""
    new = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    old = Path(args.approved) if Path(args.approved).is_absolute() else root / args.approved
    if not new.exists() or not old.exists():
        raise SFError("INPUT_NOT_FOUND", f"missing input: {new if not new.exists() else old}")
    f = Findings()
    changes = []
    if new.is_dir() and old.is_dir():
        new_files = {p.relative_to(new).as_posix(): p for p in new.rglob("*") if p.is_file()}
        old_files = {p.relative_to(old).as_posix(): p for p in old.rglob("*") if p.is_file()}
        added = sorted(set(new_files) - set(old_files))
        removed = sorted(set(old_files) - set(new_files))
        common = sorted(set(new_files) & set(old_files))
        for rel in added:
            f.info("ADDED", f"new file {rel}", path=rel)
        for rel in removed:
            f.warn("REMOVED", f"missing file {rel}", path=rel)
        for rel in common:
            a, b = new_files[rel], old_files[rel]
            if a.suffix.lower() in core.IMAGE_EXTS and b.suffix.lower() in core.IMAGE_EXTS:
                ia = core.load_image(a, max_pixels=args.max_pixels)
                ib = core.load_image(b, max_pixels=args.max_pixels)
                if ia.size != ib.size:
                    changes.append({"path": rel, "kind": "size", "old": list(ib.size), "new": list(ia.size)})
                    f.error("SIZE_CHANGED", f"{rel} changed size {ib.size} -> {ia.size}", path=rel)
                    continue
                d = core.pixel_diff(ib, ia)
                if not d["identical"]:
                    changes.append({"path": rel, "kind": "pixels", **{k: d[k] for k in
                                    ("changed_pixels", "changed_ratio", "bbox", "max_channel_delta")}})
                    severity = "error" if d["changed_ratio"] > args.max_change_ratio else "info"
                    f.add(severity, "PIXEL_CHANGE", f"{rel}: {d['changed_pixels']} px changed "
                          f"({d['changed_ratio']*100:.2f}%)", path=rel)
            else:
                if a.stat().st_size != b.stat().st_size or core.file_sha256(a) != core.file_sha256(b):
                    changes.append({"path": rel, "kind": "bytes",
                                    "old_bytes": b.stat().st_size, "new_bytes": a.stat().st_size})
                    f.info("FILE_CHANGE", f"{rel} bytes changed", path=rel)
        payload = {"check": "regression", "mode": "directory", "new": str(new), "approved": str(old),
                   "added": added, "removed": removed, "changed": changes,
                   "files_compared": len(common), **f.summary(), "findings": f.items}
    else:
        ia = core.load_image(new, max_pixels=args.max_pixels)
        ib = core.load_image(old, max_pixels=args.max_pixels)
        if ia.size != ib.size:
            f.error("SIZE_CHANGED", f"size changed {ib.size} -> {ia.size}")
            payload = {"check": "regression", "mode": "image", "size_changed": True,
                       **f.summary(), "findings": f.items}
        else:
            d = core.pixel_diff(ib, ia)
            if not d["identical"]:
                f.add("error" if d["changed_ratio"] > args.max_change_ratio else "info",
                      "PIXEL_CHANGE", f"{d['changed_pixels']} px changed ({d['changed_ratio']*100:.2f}%)")
            payload = {"check": "regression", "mode": "image", "diff": d, **f.summary(), "findings": f.items}
        changes = payload.get("diff", {})
    write_report(args, root, rep, payload)
    if args.diff_image and new.is_dir():
        rep.warn("DIFF_IMAGE_SKIPPED", "--diff-image only applies to single-image comparisons")
    if args.diff_image and not args.dry_run and not (new.is_dir() or old.is_dir()):
        try:
            ia = core.load_image(new, max_pixels=args.max_pixels)
            ib = core.load_image(old, max_pixels=args.max_pixels)
            if ia.size == ib.size:
                diff = ImageChops.difference(ib.convert("RGBA"), ia.convert("RGBA"))
                faded = Image.blend(ib.convert("RGBA"), Image.new("RGBA", ib.size, (24, 24, 28, 255)), 0.75)
                mask = diff.convert("L").point(lambda v: 255 if v > 0 else 0)
                faded.paste((255, 40, 60, 255), mask=mask)
                dp = core.guard_output(root / args.diff_image, root, args.allow_outside_root)
                core.save_image(faded, dp)
                rep.add_output(dp, "diff-image")
        except Exception as exc:  # noqa: BLE001
            rep.warn("DIFF_IMAGE_FAILED", f"could not render diff image: {exc}")
    return {"summary": {"ok": f.errors == 0, "errors": f.errors, "warnings": f.warnings,
                        "changes": len(changes) if isinstance(changes, list) else int(not changes.get("identical", False))}}


# --------------------------------------------------------------------------- #
# accessibility
# --------------------------------------------------------------------------- #

def op_accessibility(args, root: Path, rep: core.Reporter):
    """Check contrast, flashing risk, colour-only communication and small-size readability."""
    fs, src = load_fs(args, root)
    f = Findings()
    backgrounds = {"dark": (16, 16, 20), "light": (240, 240, 244), "mid": (128, 128, 134)}
    per_bg: dict[str, dict] = {}
    sampling = max(1, args.sample_step)
    for name, bg in backgrounds.items():
        ratios = []
        for fr in fs.frames:
            bbox = core.alpha_bbox(fr)
            art = fr.crop(bbox) if bbox else fr
            for idx, (r, g, b, a) in enumerate(core.iter_pixels(art)):
                if a < 200 or idx % sampling:
                    continue
                ratios.append(contrast((r, g, b), bg))
        if not ratios:
            per_bg[name] = {"worst": 0.0, "best": 0.0, "readable_share": 0.0, "samples": 0}
            continue
        readable = sum(1 for c in ratios if c >= args.min_contrast) / len(ratios)
        per_bg[name] = {"worst": round(min(ratios), 2), "best": round(max(ratios), 2),
                        "readable_share": round(readable, 3), "samples": len(ratios)}
    worst_bg = min(per_bg, key=lambda k: per_bg[k]["readable_share"])
    share = per_bg[worst_bg]["readable_share"]
    if share < args.min_readable_share:
        f.error("LOW_CONTRAST",
                f"only {share*100:.0f}% of art pixels reach {args.min_contrast}:1 on the {worst_bg} "
                f"background (needs {args.min_readable_share*100:.0f}%)", background=worst_bg)
    elif share < 0.5:
        f.warn("CONTRAST_MARGINAL",
               f"{share*100:.0f}% of art pixels reach {args.min_contrast}:1 on {worst_bg}")
    # flashing: count luminance flips over the real clip duration
    flips = []
    prev = None
    for i, fr in enumerate(fs.frames):
        mean = ImageStat.Stat(fr.convert("L")).mean[0]
        if prev is not None and abs(mean - prev) > args.flash_delta:
            flips.append(i)
        prev = mean
    duration_s = max(0.001, fs.total_ms / 1000)
    flashes_per_s = round(len(flips) / duration_s, 2)
    if flashes_per_s > args.max_flashes_per_s:
        f.error("FLASH_RISK", f"{flashes_per_s} luminance flips/second exceeds "
                f"{args.max_flashes_per_s}/s", flips=flips)
    grey_distinct = len({tuple(fr.convert("L").histogram()[8:248]) for fr in fs.frames})
    if args.check_colour_only and len(fs.frames) > 1 and grey_distinct <= 1:
        f.warn("COLOUR_ONLY", "all frames are identical in greyscale; add shape or shadow cues")
    payload = {"check": "accessibility", "input": str(src), "backgrounds": per_bg,
               "worst_background": worst_bg, "readable_share": share,
               "flashes": len(flips), "duration_s": round(duration_s, 3),
               "flashes_per_s": flashes_per_s, "greyscale_distinct_frames": grey_distinct,
               "thresholds": {"min_contrast": args.min_contrast,
                              "min_readable_share": args.min_readable_share,
                              "flash_delta": args.flash_delta,
                              "max_flashes_per_s": args.max_flashes_per_s},
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"ok": f.errors == 0, "readable_share": share, "worst_background": worst_bg,
                        "flashes_per_s": flashes_per_s, "warnings": f.warnings}}


# --------------------------------------------------------------------------- #
# consistency / similarity / policy
# --------------------------------------------------------------------------- #

def op_consistency(args, root: Path, rep: core.Reporter):
    """Detect size, colour and position drift across a set of images."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    files = core.list_images(src, recursive=args.recursive) if src.is_dir() else [src]
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    f = Findings()
    rows = []
    for p in files:
        im = core.load_image(p, max_pixels=args.max_pixels)
        prof = frame_profile(im)
        rows.append({"file": p.name, **prof})
    sizes = {tuple(r["size"]) for r in rows}
    if len(sizes) > 1:
        f.error("SIZE_DRIFT", f"{len(sizes)} different canvas sizes: {sorted(sizes)}")
    from collections import Counter
    size_counter = Counter((r["bbox"][2] - r["bbox"][0], r["bbox"][3] - r["bbox"][1])
                           for r in rows if r["bbox"])
    if len(size_counter) > 1:
        common = size_counter.most_common(1)[0][0]
        outliers = [r["file"] for r in rows if r["bbox"] and
                    (r["bbox"][2] - r["bbox"][0], r["bbox"][3] - r["bbox"][1]) != common]
        if outliers:
            f.warn("CONTENT_SIZE_DRIFT", f"{len(outliers)} files differ from the modal content size "
                    f"{common[0]}x{common[1]}", files=outliers[:8])
    mean_rgb = [sum(r["mean_rgb"][i] for r in rows) / len(rows) for i in range(3)]
    colour_outliers = [r["file"] for r in rows
                       if max(abs(r["mean_rgb"][i] - mean_rgb[i]) for i in range(3)) > args.colour_threshold]
    if colour_outliers:
        f.warn("COLOUR_DRIFT", f"{len(colour_outliers)} files drift in mean colour", files=colour_outliers[:8])
    payload = {"check": "consistency", "input": str(src), "files": len(rows),
               "canvas_sizes": [list(s) for s in sizes], "modal_content_size": list(common),
               "colour_outliers": colour_outliers, "rows": rows,
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"files": len(rows), "ok": f.errors == 0, "warnings": f.warnings,
                        "colour_outliers": len(colour_outliers)}}


def op_similarity(args, root: Path, rep: core.Reporter):
    """Originality screen against supplied designs (guarded: blocks release)."""
    core.require_approval(args, "validate-similarity",
                          "originality findings can block a release and need a human owner")
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    ref = Path(args.protect) if Path(args.protect).is_absolute() else root / args.protect
    cand = core.list_images(src) if src.is_dir() else [src]
    refs = core.list_images(ref) if ref.is_dir() else [ref]
    if not cand or not refs:
        raise SFError("NO_FRAMES", "need at least one candidate and one protected reference")
    f = Findings()
    pairs = []
    for c in cand:
        ic = core.load_image(c, max_pixels=args.max_pixels)
        hc = core.near_duplicate_hash(ic)
        for r in refs:
            ir = core.load_image(r, max_pixels=args.max_pixels)
            hr = core.near_duplicate_hash(ir)
            dist = core.hamming(hc, hr)
            similarity = round(1 - dist / 256, 3)
            pairs.append({"candidate": c.name, "reference": r.name, "hash_distance": dist,
                          "similarity": similarity})
            if similarity >= args.threshold:
                f.error("TOO_SIMILAR", f"{c.name} is {similarity*100:.0f}% similar to {r.name}",
                        candidate=c.name, reference=r.name)
    payload = {"check": "similarity", "candidates": len(cand), "references": len(refs),
               "threshold": args.threshold, "pairs": sorted(pairs, key=lambda p: -p["similarity"])[:40],
               "method": "dhash-256 perceptual hash; structural screening, not legal advice",
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"ok": f.errors == 0, "pairs_flagged": f.errors, "similarity_threshold": args.threshold,
                        "note": "screening only; refer flagged pairs to a human reviewer"}}


POLICY_RULES = {
    "everyone": [
        (r"\b(blood|gore|dismember|decapitat)", "error", "violence-gore"),
        (r"\b(gambl|casino|betting)", "error", "gambling"),
        (r"\b(drug|narcotic|smoking|alcohol)", "warn", "substances"),
        (r"\b(weapon|gun|rifle|knife)", "warn", "weapons"),
    ],
    "teen": [
        (r"\b(gore|dismember|decapitat)", "error", "violence-gore"),
        (r"\b(gambl|casino|betting)", "warn", "gambling"),
        (r"\b(blood)", "warn", "blood"),
    ],
    "mature": [
        (r"\b(explicit sexual|pornograph)", "error", "sexual-content"),
    ],
}


def op_content_policy(args, root: Path, rep: core.Reporter):
    """Screen sprite content against an age rating and platform rules (guarded)."""
    core.require_approval(args, "validate-content-policy",
                          "content policy findings block releases and encode team rules")
    if args.rating not in POLICY_RULES:
        raise SFError("BAD_RATING", f"rating must be one of {sorted(POLICY_RULES)}")
    text = " ".join([args.notes or "", args.name or "", args.tags or ""])
    f = Findings()
    hits = []
    for pattern, severity, tag in POLICY_RULES[args.rating]:
        for m in re.finditer(pattern, text, re.I):
            hits.append({"tag": tag, "match": m.group(0), "severity": severity})
            f.add(severity, "POLICY_HIT", f"{tag}: matched '{m.group(0)}' for rating {args.rating}",
                  tag=tag)
    if not args.notes and not args.name and not args.tags:
        raise SFError("MISSING_INPUT", "provide --notes, --name or --tags describing the content")
    f.info("SCOPE", "screen covers text metadata only; visual content needs human review")
    payload = {"check": "content-policy", "rating": args.rating, "hits": hits,
               "scope_note": "text-metadata screen only; pair with human visual review",
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"ok": f.errors == 0, "rating": args.rating, "hits": len(hits),
                        "warnings": f.warnings}}


CULTURAL_PATTERNS = [
    (r"\b(native|tribal|aboriginal|indigenous)\b", "unverified cultural reference"),
    (r"\b(religious|sacred|ritual|deity|god of)\b", "religious symbolism reference"),
    (r"\b(war bonnet|feather headdress|geisha|voodoo|shaman)\b", "high-risk cultural motif"),
    (r"\b(swastika|hammer and sickle|confederate)\b", "political/hate symbol risk"),
]


def op_cultural(args, root: Path, rep: core.Reporter):
    """Flag cultural motifs and claims that need trusted human review (guarded)."""
    core.require_approval(args, "validate-cultural",
                          "cultural review must be owned by a trusted reviewer")
    text = " ".join([args.notes or "", args.name or "", args.tags or ""])
    if not text.strip():
        raise SFError("MISSING_INPUT", "provide --notes, --name or --tags describing the design")
    f = Findings()
    flags = []
    for pattern, reason in CULTURAL_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            flags.append({"match": m.group(0), "reason": reason})
            f.warn("NEEDS_REVIEW", f"'{m.group(0)}': {reason}")
    if args.claimed_source:
        flags.append({"match": args.claimed_source, "reason": "claimed source needs citation check"})
        f.warn("NEEDS_REVIEW", f"claimed source '{args.claimed_source}' must be cited and verified")
    payload = {"check": "cultural", "flags": flags,
               "recommendation": "route to a trusted reviewer from the represented culture; do not self-approve",
               "note": "this tool flags for review, it does not judge correctness",
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        rep.warn(item["code"], item["message"])
    return {"summary": {"ok": f.errors == 0, "flags": len(flags), "warnings": f.warnings,
                        "recommendation": payload["recommendation"]}}


# --------------------------------------------------------------------------- #
# engine smoke (structural)
# --------------------------------------------------------------------------- #

def op_engine_smoke(args, root: Path, rep: core.Reporter):
    """Validate engine-import prerequisites structurally (no engine runtime)."""
    core.require_approval(args, "validate-engine-smoke",
                          "import readiness gates a release; confirm the target first")
    engine = args.engine
    f = Findings()
    checks: dict[str, bool] = {}
    meta = None
    if args.metadata:
        mp = Path(args.metadata) if Path(args.metadata).is_absolute() else root / args.metadata
        meta = core.load_json(mp)
    if args.input:
        sheet_path = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        if sheet_path.exists():
            im = core.load_image(sheet_path, max_pixels=args.max_pixels)
            checks["power_of_two"] = not (im.width & (im.width - 1)) and not (im.height & (im.height - 1))
            checks["has_alpha"] = core.has_transparency(im)
            checks["max_size_4096"] = im.width <= 4096 and im.height <= 4096
            if not checks["power_of_two"]:
                f.warn("NOT_POWER_OF_TWO", f"{im.width}x{im.height} is not power-of-two")
            if not checks["has_alpha"]:
                f.error("NO_ALPHA", "texture has no transparency")
            if not checks["max_size_4096"]:
                f.warn("OVERSIZED", f"{im.width}x{im.height} exceeds 4096 on a side")
    if meta:
        frames = meta.get("frames", [])
        checks["has_metadata"] = bool(frames)
        checks["unique_names"] = len({r["name"] for r in frames}) == len(frames)
        checks["has_cell"] = bool(meta.get("cell", {}).get("width"))
        checks["has_pivot"] = "pivot" in meta
        if not checks["unique_names"]:
            f.error("DUPLICATE_NAMES", "frame names must be unique for engine slicing")
        if engine in {"unity", "godot", "unreal"} and not checks["has_pivot"]:
            f.warn("NO_PIVOT", f"{engine} import expects explicit pivots")
        if engine == "unity" and not args.ppu:
            f.warn("NO_PPU", "unity import should declare --ppu (pixels per unit)")
    engine_notes = {
        "unity": "sprite mode Multiple, PPU from --ppu, pivot from metadata; rigging needs single sprites",
        "godot": "SpriteFrames resource; region_enabled with AtlasTexture; filter off for pixel art",
        "unreal": "Paper2D flipbook keyframes in frame order; texture group UI or 2D pixels",
        "gamemaker": "sequence with origins at pivot; image_speed from fps; collision mask from validate",
        "rpgmaker": "grid must match the version (e.g. 48x48 for MV/MZ character cells)",
        "web": "atlas + JSON; watch premultiplied alpha and texture size limits per device",
        "generic": "verify slicing, order, pivots, playback and texture settings in the target runtime",
    }
    f.info("ENGINE_NOTES", engine_notes.get(engine, engine_notes["generic"]))
    payload = {"check": "engine-smoke", "engine": engine, "checks": checks,
               "engine_notes": engine_notes.get(engine), "ppu": args.ppu,
               "limitation": "structural validation only; no engine runtime is invoked",
               **f.summary(), "findings": f.items}
    write_report(args, root, rep, payload)
    for item in f.items:
        if item["severity"] != "info":
            rep.warn(item["code"], item["message"])
    return {"summary": {"ok": f.errors == 0, "engine": engine, "checks": checks,
                        "warnings": f.warnings,
                        "limitation": "structural only - run the engine import yourself"}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_sheet(p):
    p.add_argument("--input", "-i", required=True, help="sheet image (or sheet path when --metadata is used)")
    p.add_argument("--metadata", help="spriteforge sheet JSON to validate against")
    p.add_argument("--grid", help="COLSxROWS when validating a plain sheet")
    p.add_argument("--cols", type=int, default=0)
    p.add_argument("--rows", type=int, default=0)
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--margin", type=int, default=0)
    p.add_argument("--frames-expected", type=int, default=0)
    p.add_argument("--pow2", action="store_true", help="require power-of-two dimensions")
    p.add_argument("--require-alpha", action="store_true", default=True)
    p.add_argument("--padding-check", action="store_true", default=True)
    p.add_argument("--output", "-o")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_flicker(p):
    p.add_argument("--input", "-i", required=True)
    add_grid(p)
    p.add_argument("--rgb-threshold", type=float, default=8.0)
    p.add_argument("--position-threshold", type=int, default=3)
    p.add_argument("--area-threshold", type=float, default=0.25)
    p.add_argument("--output", "-o")


def _p_silhouette(p):
    p.add_argument("--input", "-i", required=True)
    add_grid(p)
    p.add_argument("--target-size", help="test at this WxH instead of native size")
    p.add_argument("--blur", type=float, default=1.0)
    p.add_argument("--min-contrast", type=float, default=3.0)
    p.add_argument("--preview")
    p.add_argument("--output", "-o")


def _p_palette(p):
    p.add_argument("--input", "-i", required=True)
    add_grid(p)
    p.add_argument("--palette", help="palette JSON")
    p.add_argument("--tolerance", type=float, default=18.0)
    p.add_argument("--max-off-palette-ratio", type=float, default=0.02)
    p.add_argument("--max-colours", type=int, default=0)
    p.add_argument("--output", "-o")


def _p_seam(p):
    p.add_argument("--input", "-i", required=True)
    add_grid(p)
    p.add_argument("--tile", action="store_true", help="check every frame tiles seamlessly")
    p.add_argument("--max-tiles", type=int, default=16)
    p.add_argument("--require-tile", action="store_true")
    p.add_argument("--max-loop-ratio", type=float, default=0.02)
    p.add_argument("--preview")
    p.add_argument("--output", "-o")


def _p_anchors(p):
    p.add_argument("--input", "-i", required=True)
    add_grid(p)
    p.add_argument("--baseline", choices=["max", "mean"], default="max")
    p.add_argument("--max-foot-drift", type=int, default=1)
    p.add_argument("--max-centre-drift", type=int, default=4)
    p.add_argument("--max-height-drift", type=int, default=4)
    p.add_argument("--pivot", choices=["auto", "explicit"], default="auto")
    p.add_argument("--pivot-x", type=int, default=0)
    p.add_argument("--pivot-y", type=int, default=0)
    p.add_argument("--output", "-o")


def _p_regression(p):
    p.add_argument("--input", "-i", required=True, help="new files (file or folder)")
    p.add_argument("--approved", required=True, help="approved baseline (file or folder)")
    p.add_argument("--max-change-ratio", type=float, default=0.01)
    p.add_argument("--diff-image", help="write a red-marked diff image here")
    p.add_argument("--output", "-o")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_access(p):
    p.add_argument("--input", "-i", required=True)
    add_grid(p)
    p.add_argument("--min-contrast", type=float, default=3.0)
    p.add_argument("--min-readable-share", type=float, default=0.25,
                   help="share of art pixels that must reach --min-contrast")
    p.add_argument("--sample-step", type=int, default=3, help="sample every Nth pixel")
    p.add_argument("--flash-delta", type=float, default=42.0)
    p.add_argument("--max-flashes-per-s", type=float, default=3.0)
    p.add_argument("--check-colour-only", action="store_true", default=True)
    p.add_argument("--output", "-o")


def _p_consistency(p):
    p.add_argument("--input", "-i", required=True, help="folder or single image")
    p.add_argument("--recursive", action="store_true", default=True)
    p.add_argument("--colour-threshold", type=float, default=18.0)
    p.add_argument("--output", "-o")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_similarity(p):
    p.add_argument("--input", "-i", required=True, help="candidate sprite(s)")
    p.add_argument("--protect", required=True, help="protected reference design(s)")
    p.add_argument("--threshold", type=float, default=0.88)
    p.add_argument("--output", "-o")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_policy(p):
    p.add_argument("--rating", default="everyone", choices=sorted(POLICY_RULES))
    p.add_argument("--name")
    p.add_argument("--notes")
    p.add_argument("--tags")
    p.add_argument("--output", "-o")


def _p_cultural(p):
    p.add_argument("--name")
    p.add_argument("--notes")
    p.add_argument("--tags")
    p.add_argument("--claimed-source")
    p.add_argument("--output", "-o")


def _p_smoke(p):
    p.add_argument("--engine", default="generic",
                   choices=["unity", "godot", "unreal", "gamemaker", "rpgmaker", "web", "generic"])
    p.add_argument("--input", "-i", help="sheet/texture image")
    p.add_argument("--metadata", help="frame metadata JSON")
    p.add_argument("--ppu", type=int, help="pixels per unit (unity)")
    p.add_argument("--output", "-o")
    p.add_argument("--max-pixels", type=int, default=64_000_000)


OPS = {
    "sheet": (_p_sheet, op_sheet),
    "flicker": (_p_flicker, op_flicker),
    "silhouette": (_p_silhouette, op_silhouette),
    "palette": (_p_palette, op_palette),
    "seam": (_p_seam, op_seam),
    "anchors": (_p_anchors, op_anchors),
    "regression": (_p_regression, op_regression),
    "accessibility": (_p_access, op_accessibility),
    "consistency": (_p_consistency, op_consistency),
    "similarity": (_p_similarity, op_similarity),
    "content-policy": (_p_policy, op_content_policy),
    "cultural": (_p_cultural, op_cultural),
    "engine-smoke": (_p_smoke, op_engine_smoke),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "validate",
        "spriteforge validate: QA, drift, readability, compliance and regression checks",
        OPS,
    ))
