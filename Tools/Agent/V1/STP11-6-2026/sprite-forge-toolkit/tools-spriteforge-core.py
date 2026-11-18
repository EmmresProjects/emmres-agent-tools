#!/usr/bin/env python3
"""spriteforge core: shared image I/O, geometry, validation and reporting helpers.

This module is imported by every other tools-*.py file in this Skill Tool.
It is intentionally dependency-light (Pillow only) so the whole toolkit runs
on a stock Python 3.10+ installation.

Run it directly to verify the runtime:

    python3 tools-spriteforge-core.py --check

Design notes
------------
* Relative paths on the command line resolve against ``--root`` (default: the
  current working directory). Nothing is written outside the resolved root
  unless ``--allow-outside-root`` is passed.
* Every tool emits one JSON report (``--report`` / ``--json``) so an agent can
  branch on results without scraping stdout.
* Failures raise ``SFError`` and become a ``{"ok": false, "error": {...}}``
  payload with a non-zero exit code (2 usage, 4 approval, 5 timeout).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageSequence, ImageStat
    from PIL import PngImagePlugin, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover - dependency guard
    sys.stderr.write(
        "spriteforge requires Pillow >= 10.0. Install it with:\n"
        "  python3 -m pip install --user 'Pillow>=10,<14'\n"
        f"Original import error: {exc}\n"
    )
    raise SystemExit(3)

TOOL_NAME = "spriteforge"
TOOL_VERSION = "1.0.0"
CORE_VERSION = "1.0.0"

IMAGE_EXTS = {".png", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".gif", ".ppm", ".pgm", ".dds", ".qoi"}
ALPHA_MODES = {"RGBA", "LA", "PA"}


class SFError(Exception):
    """Structured, user facing failure."""

    def __init__(self, code: str, message: str, *, hint: str | None = None,
                 details: Any = None, exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details is not None:
            out["details"] = self.details
        return out


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #

def build_parser(description: str) -> argparse.ArgumentParser:
    """Common parser with consistent global flags for every tool."""
    p = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--root", default=".", help="base directory that all relative paths resolve against (default: .)")
    p.add_argument("--report", default=None, help="write a JSON report to this path ('-' for stdout)")
    p.add_argument("--json", action="store_true", help="shortcut for --report - (JSON on stdout)")
    p.add_argument("--timeout", type=float, default=0, help="wall clock budget in seconds (0 = no limit)")
    p.add_argument("--approve", action="append", default=[], metavar="TOKEN",
                   help="approve a guarded operation by name (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="validate and plan without writing final outputs")
    p.add_argument("--allow-outside-root", action="store_true", help="permit writes outside --root (off by default)")
    p.add_argument("--max-frames", type=int, default=4096, help="hard cap on frames processed per input (default 4096)")
    p.add_argument("--max-pixels", type=int, default=64_000_000,
                   help="refuse single images above this many pixels (default 64000000)")
    p.add_argument("--quiet", action="store_true", help="only print errors")
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION} (core {CORE_VERSION})")
    return p


def add_op_subparsers(parser: argparse.ArgumentParser, ops: dict[str, Any]) -> None:
    """Create one subcommand per operation. ``ops`` maps name -> (configure, handler)."""
    sub = parser.add_subparsers(dest="operation", metavar="OP", required=True)
    for name, pair in ops.items():
        configure, handler = pair
        doc = (handler.__doc__ or name).strip().splitlines()[0]
        sp = sub.add_parser(name, help=doc, description=doc)
        # mirror the most useful global flags so they also work after the operation
        sp.add_argument("--approve", action="append", dest="op_approve", default=[],
                        metavar="TOKEN", help="approve a guarded operation (also accepted before OP)")
        sp.add_argument("--timeout", type=float, dest="op_timeout", default=None,
                        help="wall clock budget in seconds (also accepted before OP)")
        sp.add_argument("--json", action="store_true", dest="op_json",
                        help="emit the JSON report on stdout (also accepted before OP)")
        sp.add_argument("--dry-run", action="store_true", dest="op_dry_run",
                        help="plan without writing outputs (also accepted before OP)")
        sp.add_argument("--allow-outside-root", action="store_true", dest="op_allow_outside",
                        help="permit writes outside --root (also accepted before OP)")
        configure(sp)
        # a module may declare its own --quiet; only add the mirror when it is free
        existing = {opt for a in sp._actions for opt in a.option_strings}
        if "--quiet" not in existing and "-q" not in existing:
            sp.add_argument("--quiet", "-q", action="store_true", dest="op_quiet",
                            help="suppress progress lines (also accepted before OP)")
        sp.set_defaults(_handler=name)


def run_ops(module: str, description: str, ops: dict[str, Any],
            argv: Sequence[str] | None = None) -> int:
    """Entry point for a multi-operation tool."""
    def configure(p: argparse.ArgumentParser) -> None:
        add_op_subparsers(p, ops)

    def dispatch(args: argparse.Namespace, root: Path, rep: Reporter) -> dict[str, Any]:
        return ops[args._handler][1](args, root, rep)

    return run_tool(module, description, configure, dispatch, argv)


def guard_output(out: Path, root: Path, allow_outside: bool, *, as_dir: bool = False) -> Path:
    """Validate an output path before any work happens."""
    target = out.resolve() if out.is_absolute() else (root / out).resolve()
    if not allow_outside:
        try:
            target.relative_to(root.resolve())
        except ValueError:
            raise SFError(
                "OUTSIDE_ROOT",
                f"refusing to write outside the workspace root: {target}",
                hint="pass --allow-outside-root or choose an output inside --root",
            )
    if as_dir:
        if target.exists() and not target.is_dir():
            raise SFError("OUTPUT_IS_FILE", f"expected a directory output, found a file: {target}")
        target.mkdir(parents=True, exist_ok=True)
        return target
    if target.exists() and target.is_dir():
        raise SFError("OUTPUT_IS_DIR", f"output path is a directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def require_approval(args: argparse.Namespace, action: str, reason: str) -> None:
    """Gate destructive / irreversible operations behind an explicit flag."""
    if action not in set(args.approve or []):
        raise SFError(
            "APPROVAL_REQUIRED",
            f"operation '{action}' needs explicit approval: {reason}",
            hint=f"re-run with --approve {action}",
            exit_code=4,
        )


class Deadline:
    """Cooperative timeout checked between frames and operations."""

    def __init__(self, seconds: float | None):
        self.seconds = seconds or 0
        self.start = time.monotonic()

    def check(self) -> None:
        if self.seconds and (time.monotonic() - self.start) > self.seconds:
            raise SFError(
                "TIMEOUT",
                f"operation exceeded its {self.seconds:g}s budget",
                hint="raise --timeout, shrink the frame subset, or split the batch",
                exit_code=5,
            )

    @property
    def elapsed(self) -> float:
        return round(time.monotonic() - self.start, 3)


@dataclass
class FrameSet:
    """Ordered frames plus per-frame timing and provenance."""

    frames: list[Image.Image]
    durations_ms: list[int]
    loop: int = 0
    source: str = ""
    names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.durations_ms) != len(self.frames):
            raise SFError("BAD_FRAMESET", "durations and frames differ in length")

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def size(self) -> tuple[int, int]:
        return self.frames[0].size if self.frames else (0, 0)

    @property
    def total_ms(self) -> int:
        return sum(self.durations_ms)


class Reporter:
    """Collects structured results for one invocation."""

    def __init__(self, module: str, operation: str, args: argparse.Namespace, root: Path):
        self.data: dict[str, Any] = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "module": module,
            "operation": operation,
            "ok": True,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "root": str(root),
            "dry_run": bool(getattr(args, "dry_run", False)),
            "warnings": [],
            "outputs": [],
            "stats": {},
        }
        self.args = args
        self.deadline = Deadline(getattr(args, "timeout", 0))

    def warn(self, code: str, message: str, **context: Any) -> None:
        self.data["warnings"].append({"code": code, "message": message, "context": context})
        if not getattr(self.args, "quiet", False):
            print(f"warning: {code}: {message}", file=sys.stderr)

    def progress(self, text: str, *, err: bool = False) -> None:
        """Human progress line.

        Suppressed under --quiet and under --json / --report - so that stdout
        stays a single parseable JSON document for machine callers.
        """
        if getattr(self.args, "quiet", False):
            return
        if getattr(self.args, "json", False) or getattr(self.args, "report", None) == "-":
            return
        print(text, file=sys.stderr if err else sys.stdout)
        sys.stdout.flush()

    def add_output(self, path: Path | str, kind: str = "file", **extra: Any) -> None:
        entry = {"kind": kind, "path": str(path)}
        entry.update({k: v for k, v in extra.items() if v is not None})
        self.data["outputs"].append(entry)

    def stat(self, key: str, value: Any) -> None:
        self.data["stats"][key] = value

    def finish(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if extra:
            self.data.update(extra)
        self.data["elapsed_s"] = self.deadline.elapsed
        # every report carries its exit code, so automation never has to infer it
        self.data.setdefault("exit_code", 0 if self.data.get("ok") else 1)
        return self.data


def emit(report: dict[str, Any], args: argparse.Namespace, root: Path) -> int:
    """Write the JSON report where the user asked and return an exit code."""
    target = getattr(args, "report", None)
    payload = json.dumps(report, indent=2, default=str, sort_keys=False)
    if target == "-" or (getattr(args, "json", False) and not target):
        print(payload)
    elif target:
        p = Path(target)
        if not p.is_absolute():
            p = root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"report: {p}")
    if not args.quiet and not (target == "-" or getattr(args, "json", False)):
        summary = report.get("summary")
        if summary:
            print(json.dumps(summary, indent=2, default=str))
    return 0 if report.get("ok", False) else int(report.get("exit_code", 1))


def run_tool(module: str, description: str, add_arguments, handler,
             argv: Sequence[str] | None = None) -> int:
    """Standard main() wrapper: parse, execute, report, map errors to exit codes."""
    parser = build_parser(description)
    add_arguments(parser)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    # global options may also be written after the operation (e.g. `op --approve x`)
    if getattr(args, "op_approve", None):
        args.approve = list(args.approve or []) + list(args.op_approve)
    if getattr(args, "op_timeout", None) is not None and not args.timeout:
        args.timeout = args.op_timeout
    if getattr(args, "op_json", False):
        args.json = True
    if getattr(args, "op_dry_run", False):
        args.dry_run = True
    if getattr(args, "op_allow_outside", False):
        args.allow_outside_root = True
    if getattr(args, "op_quiet", False):
        args.quiet = True
    if not root.exists():
        print(f"error: --root does not exist: {root}", file=sys.stderr)
        return 2
    rep = Reporter(module, getattr(args, "operation", ""), args, root)
    try:
        outcome = handler(args, root, rep)
        data = rep.finish(outcome if isinstance(outcome, dict) else {})
        return emit(data, args, root)
    except SFError as exc:
        data = rep.finish({"ok": False, "error": exc.to_dict(), "exit_code": exc.exit_code})
        emit(data, args, root)
        if not args.quiet:
            print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        data = rep.finish({"ok": False, "error": {"code": "CANCELLED", "message": "cancelled by user"}, "exit_code": 130})
        emit(data, args, root)
        return 130
    except Exception as exc:  # noqa: BLE001 - final safety net, always structured
        if os.environ.get("SPRITEFORGE_DEBUG"):
            traceback.print_exc()
        data = rep.finish({
            "ok": False,
            "error": {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"},
            "exit_code": 1,
        })
        emit(data, args, root)
        if not args.quiet:
            print(f"error [INTERNAL]: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("hint: set SPRITEFORGE_DEBUG=1 for a traceback", file=sys.stderr)
        return 1


def add_common_io(parser: argparse.ArgumentParser, *, output_help: str = "output file",
                  input_help: str = "input file") -> None:
    parser.add_argument("--input", "-i", required=True, help=input_help)
    parser.add_argument("--output", "-o", required=True, help=output_help)


# --------------------------------------------------------------------------- #
# Image loading / saving
# --------------------------------------------------------------------------- #

def load_image(path: Path, *, max_pixels: int = 64_000_000, alpha: bool = True) -> Image.Image:
    """Load one image as RGBA (or RGB) with validation."""
    if not path.exists():
        raise SFError("INPUT_NOT_FOUND", f"input image not found: {path}", hint="check the path and --root")
    if path.is_dir():
        raise SFError("INPUT_IS_DIR", f"expected an image file, got a directory: {path}")
    if path.stat().st_size == 0:
        raise SFError("INPUT_EMPTY", f"input image is empty: {path}")
    try:
        im = Image.open(path)
        im.load()
    except UnidentifiedImageError as exc:
        raise SFError("UNSUPPORTED_IMAGE", f"cannot decode image: {path}", details=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise SFError("CORRUPT_IMAGE", f"failed to decode image: {path}", details=str(exc))
    if im.width * im.height > max_pixels:
        raise SFError(
            "IMAGE_TOO_LARGE",
            f"{path.name} is {im.width}x{im.height} ({im.width * im.height:,} px), above --max-pixels {max_pixels:,}",
            hint="raise --max-pixels if you really need it",
        )
    wanted = "RGBA" if alpha else "RGB"
    if im.mode != wanted:
        im = im.convert(wanted)
    return im


def load_frames(path: Path, *, max_frames: int = 4096, max_pixels: int = 64_000_000,
                pattern: str | None = None) -> FrameSet:
    """Load a still, animated GIF/APNG/WebP, or a numbered image sequence folder."""
    if path.is_dir():
        return load_sequence(path, max_frames=max_frames, max_pixels=max_pixels, pattern=pattern)
    if not path.exists():
        raise SFError("INPUT_NOT_FOUND", f"input not found: {path}")
    frames: list[Image.Image] = []
    durations: list[int] = []
    loop = 0
    try:
        im = Image.open(path)
        loop = int(im.info.get("loop", 0) or 0)
        for i, frame in enumerate(ImageSequence.Iterator(im)):
            if i >= max_frames:
                raise SFError("TOO_MANY_FRAMES", f"{path.name} has more than --max-frames {max_frames} frames")
            if frame.width * frame.height > max_pixels:
                raise SFError("IMAGE_TOO_LARGE", f"{path.name} frame {i} exceeds --max-pixels")
            durations.append(int(frame.info.get("duration", im.info.get("duration", 100)) or 100))
            frames.append(frame.convert("RGBA").copy())
    except SFError:
        raise
    except UnidentifiedImageError as exc:
        raise SFError("UNSUPPORTED_IMAGE", f"cannot decode: {path}", details=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise SFError("CORRUPT_IMAGE", f"failed to decode: {path}", details=str(exc))
    if not frames:
        raise SFError("NO_FRAMES", f"no frames decoded from {path}")
    return FrameSet(frames=frames, durations_ms=durations, loop=loop, source=str(path))


_SEQ_RE = re.compile(r"^(?P<stem>.*?)[-_ .]?(?P<num>\d{1,6})(?P<ext>\.(?:png|webp|bmp|tga|tiff?|jpe?g))$", re.IGNORECASE)


def load_sequence(folder: Path, *, pattern: str | None = None, max_frames: int = 4096,
                  max_pixels: int = 64_000_000) -> FrameSet:
    """Load a numbered image sequence from a folder, preserving numeric order."""
    if not folder.is_dir():
        raise SFError("INPUT_NOT_DIR", f"expected a directory of frames: {folder}")
    candidates = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if pattern:
        rx = re.compile(pattern)
        candidates = [p for p in candidates if rx.search(p.name)]
    if not candidates:
        raise SFError("NO_FRAMES", f"no image files found in {folder}", hint="check --input or --pattern")

    def sort_key(p: Path) -> tuple[str, int, str]:
        m = _SEQ_RE.match(p.name)
        if m and m.group("num"):
            return (m.group("stem").lower(), int(m.group("num")), p.suffix.lower())
        return (p.name.lower(), 0, p.suffix.lower())

    candidates.sort(key=sort_key)
    if len(candidates) > max_frames:
        raise SFError("TOO_MANY_FRAMES", f"{len(candidates)} frames in {folder} exceeds --max-frames {max_frames}")
    frames = [load_image(p, max_pixels=max_pixels) for p in candidates]
    sizes = {f.size for f in frames}
    if len(sizes) > 1:
        raise SFError("SIZE_MISMATCH", f"frame sizes differ in {folder}: {sorted(sizes)}",
                      hint="resize or pad the frames first (tools-spriteforge-pack.py op=repack or tools-spriteforge-edit.py)")
    return FrameSet(frames=frames, durations_ms=[100] * len(frames), source=str(folder),
                    loop=0, names=[p.name for p in candidates])


def grid_cell(size: tuple[int, int], cols: int, rows: int, *, padding: int = 0,
              margin: int = 0) -> tuple[int, int]:
    """Cell size implied by slicing an image into cols x rows with padding/margin."""
    W, H = size
    usable_w = W - 2 * margin - (cols - 1) * padding
    usable_h = H - 2 * margin - (rows - 1) * padding
    if usable_w <= 0 or usable_h <= 0:
        raise SFError("BAD_GRID", f"grid {cols}x{rows} with padding {padding} does not fit {W}x{H}")
    if usable_w % cols or usable_h % rows:
        raise SFError("BAD_GRID", f"{W}x{H} is not divisible by {cols}x{rows} (padding {padding}, margin {margin})")
    return usable_w // cols, usable_h // rows


def grid_frames(im: Image.Image, cols: int, rows: int, *, padding: int = 0, margin: int = 0,
                skip_empty: bool = True) -> list[Image.Image]:
    """Slice a packed sheet into frames in row-major order."""
    cw, ch = grid_cell(im.size, cols, rows, padding=padding, margin=margin)
    out = []
    for index in range(cols * rows):
        r, c = divmod(index, cols)
        x = margin + c * (cw + padding)
        y = margin + r * (ch + padding)
        tile = im.crop((x, y, x + cw, y + ch)).convert("RGBA")
        if skip_empty and alpha_bbox(tile) is None:
            continue
        out.append(tile)
    if not out:
        raise SFError("NO_FRAMES", "grid slicing produced no non-empty frames")
    return out


def opaque_pixels(im: Image.Image, min_alpha: int = 1) -> int:
    """Count pixels at or above min_alpha without touching pixel data."""
    alpha = (im if im.mode in ("L", "1") else im.convert("RGBA").split()[-1])
    hist = alpha.histogram()
    return sum(hist[min_alpha:256])


def save_image(im: Image.Image, path: Path, *, format: str | None = None, quality: int = 92,
               optimize: bool = True) -> Path:
    """Save a still image with predictable format handling."""
    fmt = (format or path.suffix.lstrip(".") or "png").upper()
    fmt = {"JPG": "JPEG", "TIF": "TIFF"}.get(fmt, fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    params: dict[str, Any] = {}
    if fmt == "PNG":
        params = {"optimize": optimize}
        if "A" in im.mode:
            params["compress_level"] = 9
    elif fmt == "WEBP":
        params = {"quality": quality, "method": 6}
    elif fmt == "JPEG":
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(rgba.convert("RGB"), mask=rgba.split()[-1])
            im = bg
        params = {"quality": quality, "optimize": True}
    elif fmt == "BMP":
        if "A" in im.mode:
            im = im.convert("RGB")
    try:
        im.save(path, format=fmt, **params)
    except KeyError as exc:
        raise SFError("UNSUPPORTED_FORMAT", f"Pillow cannot write {fmt}: {exc}",
                      hint="use PNG, WEBP, GIF or BMP; SVG is written by tools-spriteforge-convert.py")
    except OSError as exc:
        raise SFError("WRITE_FAILED", f"could not write {path}: {exc}")
    return path


def save_animated(frames: Sequence[Image.Image], path: Path, durations_ms: Sequence[int], *,
                  loop: int = 0, format: str | None = None, quality: int = 90,
                  background: tuple[int, int, int] | None = None) -> Path:
    """Save GIF, APNG (png) or animated WebP."""
    if not frames:
        raise SFError("NO_FRAMES", "cannot write an animation with zero frames")
    fmt = (format or path.suffix.lstrip(".") or "gif").upper()
    fmt = {"APNG": "PNG"}.get(fmt, fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    durs = [max(10, int(d)) for d in durations_ms]
    if fmt == "GIF":
        pal_frames = []
        for f in frames:
            rgba = f.convert("RGBA")
            if background is not None:
                bg = Image.new("RGBA", rgba.size, tuple(background) + (255,))
                bg.alpha_composite(rgba)
                rgba = bg
            alpha = rgba.split()[-1]
            pal = rgba.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT,
                                                dither=Image.Dither.FLOYDSTEINBERG)
            use_transparency = alpha.getextrema()[0] < 128
            if use_transparency:
                idx = list(pal.tobytes())
                amask = list(alpha.tobytes())
                pal.putdata([255 if amask[i] < 128 else idx[i] for i in range(len(idx))])
            pal_frames.append(pal)
        pal_frames[0].save(path, format="GIF", save_all=True, append_images=pal_frames[1:],
                           duration=durs, loop=loop, disposal=2, optimize=False,
                           transparency=255 if use_transparency else None)
        return path
    if fmt == "PNG":
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Software", f"{TOOL_NAME} {TOOL_VERSION}")
        frames[0].save(path, format="PNG", save_all=True, append_images=list(frames[1:]),
                       duration=durs, loop=loop, disposal=0, blend=0, pnginfo=meta)
        return path
    if fmt == "WEBP":
        frames[0].save(path, format="WEBP", save_all=True, append_images=list(frames[1:]),
                       duration=durs, loop=loop, quality=quality, method=6)
        return path
    raise SFError("UNSUPPORTED_FORMAT", f"animated output supports GIF, APNG/PNG, WEBP (got {fmt})")


# --------------------------------------------------------------------------- #
# Alpha / trim / composite helpers
# --------------------------------------------------------------------------- #

def alpha_bbox(im: Image.Image, threshold: int = 1) -> tuple[int, int, int, int] | None:
    """Bounding box of pixels with alpha >= threshold, or None when fully transparent."""
    if im.mode not in ALPHA_MODES:
        return (0, 0, im.width, im.height)
    a = im.convert("RGBA").split()[-1]
    if threshold > 1:
        a = a.point(lambda v: 255 if v >= threshold else 0)
    return a.getbbox()


def trim(im: Image.Image, threshold: int = 1) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Trim transparent borders; returns (image, box_in_source)."""
    box = alpha_bbox(im, threshold)
    if box is None:
        return im.copy(), (0, 0, 0, 0)
    return im.crop(box), box


def pad_to(im: Image.Image, size: tuple[int, int], offset: tuple[int, int] = (0, 0),
           color: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Image.Image:
    """Place an image on a larger transparent canvas at offset."""
    canvas = Image.new("RGBA", size, color)
    canvas.paste(im, offset, im if "A" in im.mode else None)
    return canvas


def checkerboard(size: tuple[int, int], cell: int = 8,
                 light: tuple[int, int, int, int] = (200, 200, 200, 255),
                 dark: tuple[int, int, int, int] = (150, 150, 150, 255)) -> Image.Image:
    im = Image.new("RGBA", size, light)
    d = ImageDraw.Draw(im)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if ((x // cell) + (y // cell)) % 2:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=dark)
    return im


def composite_on(im: Image.Image, background: tuple[int, int, int] | str = "checker") -> Image.Image:
    """Flatten RGBA onto a solid colour or a checker background for previews."""
    rgba = im.convert("RGBA")
    if background == "checker":
        base = checkerboard(rgba.size)
    elif background == "checker-dark":
        base = checkerboard(rgba.size, light=(48, 48, 52, 255), dark=(32, 32, 36, 255))
    else:
        colour = tuple(background) if isinstance(background, (list, tuple)) else (255, 255, 255)
        base = Image.new("RGBA", rgba.size, tuple(colour[:3]) + (255,))
    base.alpha_composite(rgba)
    return base


def has_transparency(im: Image.Image) -> bool:
    if im.mode not in ALPHA_MODES:
        return False
    return im.convert("RGBA").split()[-1].getextrema()[0] < 255


def near_duplicate_hash(im: Image.Image, size: int = 16) -> str:
    """Difference hash (dHash) used by the near-duplicate finder: hex string of 256 bits."""
    small = im.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(small.tobytes())
    bits = []
    for y in range(size):
        row = px[y * (size + 1):(y + 1) * (size + 1)]
        bits.extend("1" if row[x] > row[x + 1] else "0" for x in range(size))
    return f"{int(''.join(bits), 2):0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def image_sha256(im: Image.Image) -> str:
    return hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest()


def iter_pixels(im: Image.Image):
    """Iterate (r, g, b, a) without the deprecated getdata() API."""
    rgba = im.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    for y in range(h):
        for x in range(w):
            yield px[x, y]


def color_counts(im: Image.Image) -> dict[tuple[int, int, int, int], int]:
    """Histogram of exact RGBA colours."""
    rgba = im.convert("RGBA")
    q = rgba.quantize(colors=256) if False else None
    counts: dict[tuple[int, int, int, int], int] = {}
    px = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            p = px[x, y]
            counts[p] = counts.get(p, 0) + 1
    return counts


def pixel_diff(a: Image.Image, b: Image.Image) -> dict[str, Any]:
    """Compare two images of identical size and report changed pixels/bounds."""
    if a.size != b.size:
        raise SFError("SIZE_MISMATCH", f"cannot diff {a.size} against {b.size}")
    ra, rb = a.convert("RGBA"), b.convert("RGBA")
    diff = ImageChops.difference(ra, rb)
    bbox = diff.getbbox()
    changed = 0
    max_delta = 0
    if bbox:
        crop = diff.crop(bbox)
        raw = crop.tobytes()
        step = len(crop.mode)
        total_px = len(raw) // step
        changed = sum(1 for i in range(0, len(raw), step) if any(raw[i:i + step]))
        max_delta = max(raw)
    total = a.size[0] * a.size[1]
    return {
        "identical": bbox is None,
        "changed_pixels": changed,
        "changed_ratio": round(changed / total, 6) if total else 0.0,
        "bbox": list(bbox) if bbox else None,
        "max_channel_delta": max_delta,
        "total_pixels": total,
    }


# --------------------------------------------------------------------------- #
# Misc utilities
# --------------------------------------------------------------------------- #

def parse_size(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX,]\s*(\d+)\s*", value or "")
    if not m:
        raise SFError("BAD_SIZE", f"expected WIDTHxHEIGHT, got '{value}'")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise SFError("BAD_SIZE", f"size must be positive, got {value}")
    return w, h


def parse_int_list(value: str | Sequence[int] | int, *, name: str = "values") -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    parts = [p for p in re.split(r"[,\s]+", str(value).strip()) if p]
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            raise SFError("BAD_NUMBER", f"{name} must be comma separated integers, got '{p}'")
    return out


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def slugify(value: str, *, default: str = "asset") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or default


def list_images(root: Path, recursive: bool = True, exts: Iterable[str] = IMAGE_EXTS) -> list[Path]:
    exts = {e.lower() for e in exts}
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in exts)


def bundled(name: str) -> Path | None:
    """A file shipped next to the tools, used as a read-only fallback.

    Example specs live beside the tool files. When --root points at a project
    folder, ``asset-sf-*.json`` still resolves from the toolkit itself so the
    shipped examples work from any workspace.
    """
    here = Path(__file__).resolve().parent
    candidate = here / name
    return candidate if candidate.exists() else None


def find_input(path: Path, root: Path | None = None) -> Path:
    """Resolve a user-supplied input path.

    Order: the path as given -> relative to --root -> an example shipped next to
    the tools. Raises SPEC_NOT_FOUND only when all three miss.
    """
    if path.is_absolute():
        return path
    if path.exists():
        return path
    if root is not None and (root / path).exists():
        return root / path
    fallback = bundled(path.name)
    return fallback if fallback is not None else path


def load_json(path: Path) -> Any:
    if not path.exists():
        fallback = bundled(path.name) if not path.is_absolute() else None
        if fallback is not None:
            path = fallback
        else:
            raise SFError("SPEC_NOT_FOUND", f"spec file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SFError("SPEC_NOT_JSON", f"{path.name} is not a UTF-8 text file",
                      hint="expected a JSON document, got binary data")
    except OSError as exc:
        raise SFError("SPEC_READ_FAILED", f"could not read {path}: {exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SFError("SPEC_INVALID_JSON", f"invalid JSON in {path}: {exc}")


def save_json(data: Any, path: Path, *, indent: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent, default=str) + "\n", encoding="utf-8")
    return path


def make_contact_sheet(entries: list[tuple[Image.Image, str]], *, cols: int = 6,
                       cell: tuple[int, int] | None = None, pad: int = 6, label_h: int = 14,
                       bg: tuple[int, int, int, int] = (28, 28, 32, 255),
                       checker: bool = True) -> Image.Image:
    """Generic labelled grid used by previews and review pages."""
    if not entries:
        raise SFError("NO_ENTRIES", "contact sheet needs at least one entry")
    if cell is None:
        cw = max(im.width for im, _ in entries)
        ch = max(im.height for im, _ in entries)
    else:
        cw, ch = cell
    cols = max(1, min(cols, len(entries)))
    rows = math.ceil(len(entries) / cols)
    W = cols * (cw + pad) + pad
    H = rows * (ch + label_h + pad) + pad
    sheet = Image.new("RGBA", (W, H), bg)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, (im, label) in enumerate(entries):
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = pad + r * (ch + label_h + pad)
        frame = checkerboard((cw, ch)) if checker else Image.new("RGBA", (cw, ch), (34, 34, 38, 255))
        thumb = im.convert("RGBA")
        if thumb.size != (cw, ch):
            thumb = ImageOps.contain(thumb, (cw, ch), Image.Resampling.NEAREST)
        frame.alpha_composite(thumb, ((cw - thumb.width) // 2, (ch - thumb.height) // 2))
        sheet.paste(frame, (x, y))
        draw.rectangle([x, y, x + cw - 1, y + ch - 1], outline=(70, 70, 80, 255))
        draw.text((x + 3, y + ch + 2), label[: max(4, cw // 6)], fill=(220, 220, 230, 255), font=font)
    return sheet


def tile_preview(im: Image.Image, tiles_x: int = 3, tiles_y: int = 3) -> Image.Image:
    """Repeat an image to reveal seams (used by tile and loop checks)."""
    out = Image.new("RGBA", (im.width * tiles_x, im.height * tiles_y))
    for y in range(tiles_y):
        for x in range(tiles_x):
            out.paste(im, (x * im.width, y * im.height))
    return out


def parse_kv_list(values: Sequence[str], *, name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise SFError("BAD_PAIR", f"{name} expects KEY=VALUE, got '{item}'")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def ensure_ext(path: Path, allowed: Sequence[str], *, name: str = "output") -> Path:
    if path.suffix.lower() not in {e.lower() for e in allowed}:
        raise SFError("BAD_EXTENSION", f"{name} must end with one of {', '.join(allowed)} (got '{path.name}')")
    return path


@contextlib.contextmanager
def temp_dir(prefix: str = "sf-") -> Iterator[Path]:
    """Temporary working directory, always cleaned up."""
    base = Path(os.environ.get("TMPDIR", "/tmp")) / f"{prefix}{os.getpid()}-{int(time.time() * 1000) % 100000}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Self check (executed when run directly)
# --------------------------------------------------------------------------- #

def _self_check() -> int:
    checks: list[tuple[str, bool, str]] = []
    with temp_dir("sf-core-") as tmp:
        im = Image.new("RGBA", (32, 24), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle([8, 4, 23, 19], fill=(200, 60, 40, 255))
        p = tmp / "t.png"
        save_image(im, p)
        checks.append(("png round-trip", load_image(p).size == (32, 24), "size survives"))
        checks.append(("trim", trim(im)[1] == (8, 4, 24, 20), str(trim(im)[1])))
        frames = FrameSet([im, im.rotate(5)], [80, 120])
        g = tmp / "t.gif"
        save_animated(frames.frames, g, frames.durations_ms)
        checks.append(("gif write", g.stat().st_size > 0, f"{g.stat().st_size}B"))
        a = tmp / "anim.png"
        save_animated(frames.frames, a, frames.durations_ms)
        checks.append(("apng frames", len(load_frames(a)) == 2, "2 frames read back"))
        w = tmp / "anim.webp"
        save_animated(frames.frames, w, frames.durations_ms)
        checks.append(("webp write", w.stat().st_size > 0, f"{w.stat().st_size}B"))
        d = pixel_diff(im, im.copy())
        checks.append(("pixel_diff identical", d["identical"], "no changes"))
        checks.append(("hash stable", image_sha256(im) == image_sha256(im.copy()), "sha256 equal"))
        seq = tmp / "seq"
        seq.mkdir(exist_ok=True)
        for i in (1, 2, 3, 10):
            save_image(im, seq / f"walk_{i:03d}.png")
        fs = load_sequence(seq)
        checks.append(("sequence order", len(fs.frames) == 4, "4 frames sorted"))
        checks.append(("parse_size", parse_size("64 x 32") == (64, 32), "whitespace tolerated"))
        checks.append(("slugify", slugify("Hero  Idle!!") == "hero-idle", slugify("Hero  Idle!!")))
        checks.append(("hash distance", hamming(near_duplicate_hash(im), near_duplicate_hash(im)) == 0, "self distance 0"))
        checks.append(("checkerboard", checkerboard((16, 16), 8).size == (16, 16), "builds"))
        err = SFError("X", "y", hint="z")
        checks.append(("error dict", err.to_dict()["hint"] == "z", "serialises"))
    ok = all(c[1] for c in checks)
    for name, good, detail in checks:
        print(f"[{'PASS' if good else 'FAIL'}] {name}: {detail}")
    print(f"\ncore self-check: {'PASS' if ok else 'FAIL'} ({len(checks)} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="spriteforge shared core self-check")
    parser.add_argument("--check", action="store_true", help="run the internal self-check")
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    ns = parser.parse_args()
    if ns.check:
        raise SystemExit(_self_check())
    parser.print_help()
    print("\nThis file is a shared library. Run with --check to verify the runtime.")
