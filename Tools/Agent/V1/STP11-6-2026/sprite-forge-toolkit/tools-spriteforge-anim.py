#!/usr/bin/env python3
"""spriteforge anim: timing, in-betweens, loop repair, baking and playback.

Operations
----------
  timing        hold/extend/drop frames, retime, apply easing
  tween         crossfade or morph in-between frames between two poses
  loop-repair   detect the loop seam and repair pose/spacing mismatches
  loop-probe    measure first/last discontinuity without changing pixels
  secondary     derive offset/sway/rotation passes for hair, cloth, tails
  interp        bake smooth motion paths (linear/ease/noise/orbit) into frames
  playback      render labelled previews at several speeds for animation review

All operations read a still, GIF/APNG/WebP or numbered folder and write frames
plus a machine readable animation manifest.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_anim", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_anim"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image
ImageChops = core.ImageChops
ImageDraw = core.ImageDraw
ImageFilter = core.ImageFilter
ImageStat = core.ImageStat


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def load_any(args, root: Path) -> tuple[core.FrameSet, Path]:
    """Load frames from an animation, folder, or a packed sheet via --grid."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    grid = getattr(args, "grid", None)
    if grid:
        m = __import__("re").fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", grid)
        if not m:
            raise SFError("BAD_GRID", f"--grid expects COLSxROWS, got '{grid}'")
        cols, rows = int(m.group(1)), int(m.group(2))
        im = core.load_image(src, max_pixels=args.max_pixels)
        frames = core.grid_frames(im, cols, rows, padding=getattr(args, "padding", 0),
                                  margin=getattr(args, "margin", 0))
        dur = getattr(args, "frame_duration", 100) or 100
        return core.FrameSet(frames=frames, durations_ms=[dur] * len(frames), source=str(src)), src
    fs = core.load_frames(src, max_frames=args.max_frames, max_pixels=args.max_pixels)
    return fs, src


def add_grid_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--grid", help="slice a packed sheet as COLSxROWS instead of reading an animation")
    p.add_argument("--padding", type=int, default=0, help="padding used when the sheet was composed")
    p.add_argument("--margin", type=int, default=0, help="outer margin used when the sheet was composed")
    p.add_argument("--frame-duration", type=int, default=100, help="ms per frame for --grid input")


def write_frames(frames, out_dir: Path, durations, *, name: str, extra: dict | None = None,
                 dry_run: bool = False) -> list[str]:
    names = []
    if dry_run:
        return names
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        p = out_dir / f"{name}_{i:04d}.png"
        core.save_image(f, p)
        names.append(p.name)
    meta = {"name": name, "frames": len(frames), "durations_ms": list(durations),
            "total_ms": sum(durations), "files": names}
    if extra:
        meta.update(extra)
    core.save_json(meta, out_dir / f"{name}-animation.json")
    return names


def frame_signature(im) -> dict:
    """Cheap per-frame fingerprint used by drift and loop checks."""
    rgba = im.convert("RGBA")
    alpha = rgba.split()[-1]
    bbox = alpha.getbbox()
    stat = ImageStat.Stat(rgba.convert("RGB"))
    return {
        "mean_rgb": [round(v, 1) for v in stat.mean],
        "mean_alpha": round(ImageStat.Stat(alpha).mean[0], 2),
        "bbox": list(bbox) if bbox else None,
        "size": list(im.size),
        "opaque_px": core.opaque_pixels(rgba),
    }


def signature_delta(a: dict, b: dict) -> dict:
    rgb = max(abs(x - y) for x, y in zip(a["mean_rgb"], b["mean_rgb"]))
    alpha = abs(a["mean_alpha"] - b["mean_alpha"])
    pos = 0
    if a["bbox"] and b["bbox"]:
        pos = max(abs(x - y) for x, y in zip(a["bbox"], b["bbox"]))
    px = abs(a["opaque_px"] - b["opaque_px"])
    return {"mean_rgb_delta": round(rgb, 2), "mean_alpha_delta": round(alpha, 2),
            "bbox_shift_px": pos, "opaque_delta": px}


def ease(t: float, kind: str) -> float:
    t = max(0.0, min(1.0, t))
    if kind == "linear":
        return t
    if kind == "in":
        return t * t
    if kind == "out":
        return 1 - (1 - t) ** 2
    if kind == "in-out":
        return 3 * t * t - 2 * t * t * t
    if kind == "bounce":
        return 1 - abs(math.cos(t * math.pi * 1.5)) * (1 - t)
    raise SFError("BAD_EASING", f"unknown easing '{kind}'", details={"allowed": ["linear", "in", "out", "in-out", "bounce"]})


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #

def op_timing(args, root: Path, rep: core.Reporter):
    """Hold, extend, drop and retime frames, with optional easing of spacing."""
    fs, src = load_any(args, root)
    durations = list(fs.durations_ms)
    frames = list(fs.frames)
    if args.set_fps:
        if not 1 <= args.set_fps <= 240:
            raise SFError("BAD_ARGUMENT", "--set-fps must be 1..240")
        durations = [int(round(1000 / args.set_fps))] * len(frames)
    if args.uniform is not None:
        durations = [max(10, args.uniform)] * len(frames)
    if args.hold:
        for spec in args.hold:
            try:
                idx, mult = (int(v) for v in spec.split(":"))
            except ValueError:
                raise SFError("BAD_HOLD", f"--hold expects INDEX:MULTIPLIER, got '{spec}'")
            if not 0 <= idx < len(frames):
                raise SFError("BAD_HOLD", f"frame index {idx} out of range 0..{len(frames)-1}")
            if mult < 1:
                raise SFError("BAD_HOLD", "hold multiplier must be >= 1")
            durations[idx] *= mult
    if args.ease:
        n = len(durations)
        total = sum(durations)
        weights = [ease((i + 0.5) / n, args.ease) for i in range(n)]
        wsum = sum(weights) or 1.0
        durations = [max(10, int(round(total * w / wsum))) for w in weights]
    if args.drop:
        drop = set(core.parse_int_list(args.drop, name="drop"))
        if any(i < 0 or i >= len(frames) for i in drop):
            raise SFError("BAD_DROP", f"--drop indices must be 0..{len(frames)-1}")
        if len(drop) >= len(frames):
            raise SFError("BAD_DROP", "cannot drop every frame")
        frames = [f for i, f in enumerate(frames) if i not in drop]
        durations = [d for i, d in enumerate(durations) if i not in drop]
    if args.reverse:
        frames = list(reversed(frames))
        durations = list(reversed(durations))
    if args.pingpong and len(frames) > 2:
        frames = frames + list(reversed(frames[1:-1]))
        durations = durations + list(reversed(durations[1:-1]))
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    names = write_frames(frames, out_dir, durations, name=args.name,
                         extra={"operation": "timing", "source": str(src)}, dry_run=args.dry_run)
    if not args.dry_run:
        rep.add_output(out_dir, "directory", frames=len(frames))
        if args.gif:
            gp = core.guard_output(root / args.gif, root, args.allow_outside_root)
            core.save_animated(frames, gp, durations)
            rep.add_output(gp, "preview")
    rep.stat("frames", len(frames))
    return {"summary": {"frames": len(frames), "total_ms": sum(durations),
                        "avg_ms": round(sum(durations) / len(durations), 1), "out": str(out_dir)}}


def op_tween(args, root: Path, rep: core.Reporter):
    """Crossfade or morph in-between frames between two poses."""
    fs, src = load_any(args, root)
    if len(fs.frames) < 2:
        raise SFError("NEEDS_TWO_FRAMES", "tween needs at least two frames")
    steps = max(1, args.steps)
    first, last = fs.frames[0], fs.frames[-1]
    if first.size != last.size:
        raise SFError("SIZE_MISMATCH", f"endpoints differ: {first.size} vs {last.size}")
    tweens = []
    for i in range(1, steps + 1):
        t = ease(i / (steps + 1), args.easing)
        blended = Image.blend(first.convert("RGBA"), last.convert("RGBA"), t)
        if args.morph:
            # offset blend: slide the last pose toward the first for motion blur style morph
            dx = int((1 - t) * args.morph_px)
            shifted = Image.new("RGBA", last.size, (0, 0, 0, 0))
            shifted.paste(last, (dx, 0))
            blended = Image.blend(first.convert("RGBA"), shifted, ease(t, "in-out"))
        tweens.append(blended)
    frames = [first] + tweens + [last]
    duration = args.duration or (fs.durations_ms[0] if fs.durations_ms else 100)
    durations = [duration] * len(frames)
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    write_frames(frames, out_dir, durations, name=args.name,
                 extra={"operation": "tween", "steps": steps, "easing": args.easing},
                 dry_run=args.dry_run)
    if not args.dry_run:
        rep.add_output(out_dir, "directory", frames=len(frames))
        gp = out_dir / f"{args.name}-preview.gif"
        core.save_animated(frames, gp, durations)
        rep.add_output(gp, "preview")
    rep.stat("frames", len(frames))
    return {"summary": {"in_betweens": steps, "frames": len(frames), "out": str(out_dir)}}


def _seam_metrics(frames) -> dict:
    a, b = frames[0], frames[-1]
    if a.size != b.size:
        return {"identical": False, "error": "size mismatch", "score": 100.0}
    d = core.pixel_diff(a, b)
    worst = max(core.pixel_diff(frames[i], frames[i + 1])["changed_ratio"]
                for i in range(len(frames) - 1)) if len(frames) > 1 else 0
    seam_ratio = d["changed_ratio"]
    score = 0.0 if worst == 0 else round(min(100.0, 100.0 * seam_ratio / worst), 1)
    if d["identical"]:
        verdict = "seamless"
    elif seam_ratio <= 0.02:
        verdict = "subtle"
    elif score <= 100.0:
        verdict = "acceptable"
    else:
        verdict = "visible jump"
    return {
        "identical": d["identical"], "changed_pixels": d["changed_pixels"],
        "changed_ratio": seam_ratio, "worst_step_ratio": worst,
        "seam_score": score, "verdict": verdict, "bbox": d["bbox"],
    }


def op_loop_probe(args, root: Path, rep: core.Reporter):
    """Measure first/last discontinuity without modifying pixels."""
    fs, src = load_any(args, root)
    if len(fs.frames) < 2:
        raise SFError("NEEDS_TWO_FRAMES", "loop-probe needs at least two frames")
    metrics = _seam_metrics(fs.frames)
    sig_first, sig_last = frame_signature(fs.frames[0]), frame_signature(fs.frames[-1])
    metrics["signature_delta"] = signature_delta(sig_first, sig_last)
    per_frame = [signature_delta(frame_signature(fs.frames[i]), frame_signature(fs.frames[i + 1]))
                 for i in range(len(fs.frames) - 1)]
    metrics["steps"] = per_frame
    metrics["pops"] = [i for i, s in enumerate(per_frame)
                       if s["mean_rgb_delta"] > args.pop_threshold or s["opaque_delta"] > args.pop_opaque]
    if metrics["pops"]:
        rep.warn("POPS_DETECTED", f"{len(metrics['pops'])} abrupt frame transitions: {metrics['pops']}")
    if not metrics["identical"]:
        rep.warn("LOOP_SEAM", f"first/last frames differ ({metrics['changed_ratio']*100:.1f}% of pixels)",
                 seam_score=metrics["seam_score"])
    out = core.guard_output(root / args.output, root, args.allow_outside_root) if args.output else None
    if out and not args.dry_run:
        core.save_json(metrics, out)
        rep.add_output(out, "loop-report")
    return {"summary": {k: metrics[k] for k in ("identical", "seam_score", "verdict", "changed_pixels")}
            | {"pops": metrics["pops"]}, "metrics": metrics}


def op_loop_repair(args, root: Path, rep: core.Reporter):
    """Find the loop seam and repair or re-time it."""
    fs, src = load_any(args, root)
    if len(fs.frames) < 2:
        raise SFError("NEEDS_TWO_FRAMES", "loop-repair needs at least two frames")
    before = _seam_metrics(fs.frames)
    frames = list(fs.frames)
    durations = list(fs.durations_ms)
    action = args.strategy
    if action == "drop-last":
        frames = frames[:-1]
        durations = durations[:-1]
    elif action == "crossfade":
        blend = Image.blend(frames[-1].convert("RGBA"), frames[0].convert("RGBA"), 0.5)
        frames = frames[:-1] + [blend]
    elif action == "append-first":
        frames = frames + [frames[0]]
        durations = durations + [durations[-1]]
    elif action == "auto":
        if before["identical"]:
            # already a clean loop: do not touch a working animation
            action = "none"
            rep.stat("auto_chosen", "none")
        else:
            candidates: list[tuple[str, list, list]] = []
            if core.pixel_diff(frames[-1], frames[0])["changed_ratio"] < args.duplicate_threshold and len(frames) > 2:
                candidates.append(("drop-last", frames[:-1], durations[:-1]))
            blend = Image.blend(frames[-1].convert("RGBA"), frames[0].convert("RGBA"), 0.5)
            candidates.append(("crossfade", frames[:-1] + [blend], durations))
            candidates.append(("append-first", frames + [frames[0]], durations + [durations[-1]]))
            candidates.append(("none", frames, durations))
            best = min(candidates, key=lambda c: _seam_metrics(c[1])["changed_ratio"])
            action = best[0]
            frames, durations = best[1], best[2]
            rep.stat("auto_chosen", action)
    after = _seam_metrics(frames)
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    write_frames(frames, out_dir, durations, name=args.name,
                 extra={"operation": "loop-repair", "strategy": action,
                        "before": before, "after": after}, dry_run=args.dry_run)
    if not args.dry_run:
        rep.add_output(out_dir, "directory", frames=len(frames))
        gp = out_dir / f"{args.name}-preview.gif"
        core.save_animated(frames, gp, durations)
        rep.add_output(gp, "preview")
    rep.stat("seam_before", before["seam_score"])
    rep.stat("seam_after", after["seam_score"])
    if not args.dry_run:
        core.save_json({"before": before, "after": after, "strategy": action},
                       out_dir / "loop-repair.json")
    return {"summary": {"strategy": action, "frames": len(frames),
                        "seam_before": before["seam_score"], "seam_after": after["seam_score"],
                        "improved": after["changed_ratio"] < before["changed_ratio"] or after["identical"],
                        "out": str(out_dir)}, "before": before, "after": after}


def op_secondary(args, root, rep: core.Reporter):
    """Derive offset/rotation passes for hair, cloth, tails and loose parts."""
    fs, src = load_any(args, root)
    layers = core.parse_kv_list(args.layer, name="--layer")
    if not layers:
        raise SFError("MISSING_INPUT", "provide at least one --layer name=path/to/part.png")
    base = fs.frames[0]
    parts = []
    for name, path in layers.items():
        p = Path(path) if Path(path).is_absolute() else root / path
        im = core.load_image(p, max_pixels=args.max_pixels)
        if im.size != base.size:
            im = core.pad_to(im, base.size, (0, 0))
        parts.append((name, im))
    steps = max(1, args.steps)
    out_frames = []
    for i in range(steps):
        t = i / steps
        canvas = base.copy()
        for name, im in parts:
            amp = float(args.amplitude)
            phase = float(hash(name) % 100) / 100.0 * args.phase_spread
            dx = int(round(math.sin(2 * math.pi * (t + phase)) * amp))
            dy = int(round(math.cos(2 * math.pi * (t + phase)) * amp * 0.35))
            part = im
            if args.rotate:
                part = part.rotate(math.sin(2 * math.pi * (t + phase)) * args.rotate,
                                   resample=Image.Resampling.NEAREST)
            shifted = Image.new("RGBA", part.size, (0, 0, 0, 0))
            shifted.paste(part, (dx, dy))
            canvas.alpha_composite(shifted)
        out_frames.append(canvas)
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    durations = [args.duration] * len(out_frames)
    write_frames(out_frames, out_dir, durations, name=args.name,
                 extra={"operation": "secondary", "parts": list(layers)}, dry_run=args.dry_run)
    if not args.dry_run:
        rep.add_output(out_dir, "directory", frames=len(out_frames))
    rep.stat("frames", len(out_frames))
    return {"summary": {"parts": len(parts), "frames": len(out_frames), "out": str(out_dir)}}


def op_interp(args, root, rep: core.Reporter):
    """Bake smooth motion paths into frames."""
    size = core.parse_size(args.size) if args.size else (64, 64)
    steps = max(1, args.steps)
    seed = args.seed or 7
    rng = random.Random(seed)
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    sprite = core.load_image(src, max_pixels=args.max_pixels)
    if sprite.size != size:
        sprite = sprite.resize(size, Image.Resampling.NEAREST)
    path = args.path
    if path not in {"linear", "ease", "noise", "orbit", "arc"}:
        raise SFError("BAD_PATH", f"unknown path '{path}'",
                      details={"allowed": ["linear", "ease", "noise", "orbit", "arc"]})
    out_frames = []
    for i in range(steps):
        t = i / max(1, steps - 1)
        if path == "linear":
            dx, dy = int((t - 0.5) * args.distance), 0
        elif path == "ease":
            dx = int((ease(t, "in-out") - 0.5) * args.distance)
            dy = 0
        elif path == "noise":
            dx = int(rng.uniform(-args.distance, args.distance) * args.noise)
            dy = int(rng.uniform(-args.distance, args.distance) * args.noise)
        elif path == "orbit":
            dx = int(math.cos(2 * math.pi * t) * args.distance)
            dy = int(math.sin(2 * math.pi * t) * args.distance)
        else:  # arc
            dx = int((t - 0.5) * args.distance)
            dy = -int(math.sin(math.pi * t) * args.distance * args.arc_height)
        frame = Image.new("RGBA", size, (0, 0, 0, 0))
        frame.alpha_composite(sprite, (dx, dy))
        out_frames.append(frame)
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    durations = [int(1000 / args.fps)] * len(out_frames)
    write_frames(out_frames, out_dir, durations, name=args.name,
                 extra={"operation": "interp", "path": path, "seed": seed, "distance": args.distance},
                 dry_run=args.dry_run)
    if not args.dry_run:
        rep.add_output(out_dir, "directory", frames=len(out_frames))
        gp = out_dir / f"{args.name}-preview.gif"
        core.save_animated(out_frames, gp, durations)
        rep.add_output(gp, "preview")
    rep.stat("frames", len(out_frames))
    return {"summary": {"path": path, "frames": len(out_frames), "out": str(out_dir)}}


def op_playback(args, root, rep: core.Reporter):
    """Render labelled previews at several speeds for animation review."""
    fs, src = load_any(args, root)
    speeds = [float(s) for s in str(args.speeds).replace(" ", "").split(",") if s]
    if not speeds:
        raise SFError("MISSING_INPUT", "--speeds needs at least one multiplier")
    out_dir = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root, as_dir=True)
    outputs = []
    for speed in speeds:
        if speed <= 0:
            raise SFError("BAD_ARGUMENT", "speed multipliers must be > 0")
        durations = [max(10, int(round(d / speed))) for d in fs.durations_ms]
        label = f"x{speed:g}"
        if args.label:
            frames = []
            for i, f in enumerate(fs.frames):
                canvas = core.pad_to(f, (f.width, f.height + 14), (0, 14))
                d = ImageDraw.Draw(canvas)
                d.rectangle([0, 0, canvas.width, 13], fill=(20, 20, 24, 255))
                d.text((2, 2), f"{label} f{i} {durations[i]}ms", fill=(220, 220, 230, 255),
                       font=core.ImageFont.load_default())
                frames.append(canvas)
        else:
            frames = fs.frames
        if not args.dry_run:
            gp = out_dir / f"playback-{label}.gif"
            core.save_animated(frames, gp, durations)
            outputs.append(gp.name)
            rep.add_output(gp, "preview", speed=speed)
    slowest = max(speeds) if speeds else 1
    rep.stat("previews", len(outputs))
    return {"summary": {"previews": len(outputs), "speeds": speeds,
                        "base_total_ms": fs.total_ms,
                        "slowest_total_ms": round(fs.total_ms * slowest), "out": str(out_dir)}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_timing(p):
    p.add_argument("--input", "-i", required=True)
    add_grid_args(p)
    p.add_argument("--output", "-o", required=True, help="output frame folder")
    p.add_argument("--name", default="timed")
    p.add_argument("--set-fps", type=int, help="uniform fps")
    p.add_argument("--uniform", type=int, help="uniform ms per frame")
    p.add_argument("--hold", action="append", default=[], metavar="INDEX:MULT",
                   help="multiply one frame duration (repeatable)")
    p.add_argument("--ease", choices=["linear", "in", "out", "in-out", "bounce"],
                   help="redistribute total duration with easing weights")
    p.add_argument("--drop", help="comma list of frame indices to remove")
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--pingpong", action="store_true")
    p.add_argument("--gif", help="optional GIF preview path")


def _p_tween(p):
    p.add_argument("--input", "-i", required=True)
    add_grid_args(p)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--name", default="tweened")
    p.add_argument("--steps", type=int, default=3, help="in-between frames to generate")
    p.add_argument("--easing", default="in-out", choices=["linear", "in", "out", "in-out", "bounce"])
    p.add_argument("--morph", action="store_true", help="slide the end pose instead of pure crossfade")
    p.add_argument("--morph-px", type=int, default=4)
    p.add_argument("--duration", type=int, help="ms per generated frame")


def _p_probe(p):
    p.add_argument("--input", "-i", required=True)
    add_grid_args(p)
    p.add_argument("--output", "-o")
    p.add_argument("--pop-threshold", type=float, default=12.0, help="mean RGB delta treated as a pop")
    p.add_argument("--pop-opaque", type=int, default=400, help="opaque pixel delta treated as a pop")


def _p_repair(p):
    p.add_argument("--input", "-i", required=True)
    add_grid_args(p)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--name", default="looped")
    p.add_argument("--strategy", default="auto", choices=["auto", "drop-last", "crossfade", "append-first"])
    p.add_argument("--duplicate-threshold", type=float, default=0.01)


def _p_secondary(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--name", default="secondary")
    p.add_argument("--layer", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--steps", type=int, default=8)
    p.add_argument("--amplitude", type=float, default=3.0)
    p.add_argument("--rotate", type=float, default=0.0)
    p.add_argument("--phase-spread", type=float, default=0.35)
    p.add_argument("--duration", type=int, default=100)


def _p_interp(p):
    p.add_argument("--input", "-i", required=True, help="single sprite image")
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--name", default="motion")
    p.add_argument("--path", default="ease", choices=["linear", "ease", "noise", "orbit", "arc"])
    p.add_argument("--steps", type=int, default=8)
    p.add_argument("--distance", type=float, default=24)
    p.add_argument("--noise", type=float, default=0.25)
    p.add_argument("--arc-height", type=float, default=0.4)
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--seed", type=int)
    p.add_argument("--size", help="canvas WxH (default 64x64)")


def _p_playback(p):
    p.add_argument("--input", "-i", required=True)
    add_grid_args(p)
    p.add_argument("--output", "-o", required=True)
    p.add_argument("--speeds", default="0.5,1,2")
    p.add_argument("--label", action="store_true", default=True)


OPS = {
    "timing": (_p_timing, op_timing),
    "tween": (_p_tween, op_tween),
    "loop-repair": (_p_repair, op_loop_repair),
    "loop-probe": (_p_probe, op_loop_probe),
    "secondary": (_p_secondary, op_secondary),
    "interp": (_p_interp, op_interp),
    "playback": (_p_playback, op_playback),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "anim",
        "spriteforge anim: timing, in-betweens, loop repair, baking and review playback",
        OPS,
    ))
