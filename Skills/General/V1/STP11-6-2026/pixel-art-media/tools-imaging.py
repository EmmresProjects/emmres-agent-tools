"""pixel-art-media: core imaging library and pixel-editing commands.

Shared helpers (IO, limits, atomic writes, color, geometry) live here and are
reused by every sibling module. Pure Python + Pillow. No network access.
"""
from __future__ import annotations

import colorsys
import importlib.util
import json
import math
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, features

TOOL_DIR = Path(__file__).resolve().parent


def sibling(name):
    """Load a sibling tools-<name>.py module (hyphenated filenames)."""
    mod = "tools_" + name.replace("-", "_")
    if mod in sys.modules:
        return sys.modules[mod]
    path = TOOL_DIR / ("tools-" + name + ".py")
    if not path.is_file():
        raise ToolError("missing-module", f"Required module not found: {path.name}")
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m


class ToolError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# ---------------------------------------------------------------- config ---

DEFAULT_CONFIG = {
    "limits": {
        "max_side": 4096,
        "max_pixels": 4194304,
        "op_pixels": 1048576,
        "max_frames": 256,
        "max_colors": 256,
        "max_sheet_side": 4096,
        "max_inputs": 512,
        "timeout_seconds": 180,
    },
    "io": {"write_retries": 2, "retry_delay_seconds": 0.2},
    "defaults": {
        "fps": 12,
        "seed": 7,
        "gif_min_delay_ms": 20,
        "alpha_threshold": 128,
        "background": "#00000000",
    },
}


def deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(user_path=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    asset = TOOL_DIR / "asset-config.json"
    if asset.is_file():
        cfg = deep_merge(cfg, json.loads(asset.read_text(encoding="utf-8")))
    if user_path:
        p = Path(user_path)
        if not p.is_file():
            raise ToolError("config-not-found", f"Config file not found: {user_path}")
        try:
            cfg = deep_merge(cfg, json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise ToolError("config-invalid", f"Invalid JSON in config: {e}")
    return cfg


def asset_path(name):
    p = TOOL_DIR / name
    if not p.is_file():
        raise ToolError("missing-asset", f"Required asset not found: {name}",
                        "Keep all asset-* files beside the tools-* files.")
    return p


def load_asset_json(name):
    try:
        return json.loads(asset_path(name).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError("asset-invalid", f"Invalid JSON in {name}: {e}")


# ------------------------------------------------------------------- ctx ---

class Ctx:
    def __init__(self, cfg, force=False, dry_run=False, json_mode=False, quiet=False):
        self.cfg = cfg
        self.force = force
        self.dry_run = dry_run
        self.json_mode = json_mode
        self.quiet = quiet
        self.written = []
        self.warnings = []
        self.started = time.time()

    def warn(self, msg):
        self.warnings.append(msg)

    def check_time(self):
        limit = self.cfg["limits"].get("timeout_seconds", 180)
        if limit and time.time() - self.started > limit:
            raise ToolError("timeout", f"Operation exceeded {limit}s budget.",
                            "Reduce dimensions/frames or raise limits.timeout_seconds.")


def check_dims(cfg, w, h, frames=1):
    lim = cfg["limits"]
    if w < 1 or h < 1:
        raise ToolError("bad-dimensions", f"Invalid dimensions {w}x{h}.")
    if w > lim["max_side"] or h > lim["max_side"]:
        raise ToolError("limit-side", f"{w}x{h} exceeds max_side={lim['max_side']}.",
                        "Raise limits.max_side in config if intentional.")
    if w * h > lim["max_pixels"]:
        raise ToolError("limit-pixels", f"{w}x{h} exceeds max_pixels={lim['max_pixels']}.")
    if frames > lim["max_frames"]:
        raise ToolError("limit-frames", f"{frames} frames exceeds max_frames={lim['max_frames']}.")


def check_op_pixels(cfg, w, h):
    if w * h > cfg["limits"]["op_pixels"]:
        raise ToolError("limit-op-pixels",
                        f"Per-pixel operation on {w}x{h} exceeds op_pixels={cfg['limits']['op_pixels']}.",
                        "Work at native pixel-art resolution, or raise limits.op_pixels.")


# -------------------------------------------------------------------- io ---

def load_image(path, cfg, mode="RGBA"):
    p = Path(path)
    if not p.is_file():
        raise ToolError("input-not-found", f"Input image not found: {path}")
    try:
        img = Image.open(p)
        img.load()
    except Exception as e:
        raise ToolError("input-unreadable", f"Cannot decode image {path}: {e}",
                        "Supported inputs: PNG, GIF, APNG, WebP, BMP, JPEG, TIFF (Pillow).")
    check_dims(cfg, img.width, img.height)
    if mode and img.mode != mode:
        img = img.convert(mode)
    return img


def guard_output(ctx, path):
    p = Path(path)
    if p.exists() and not ctx.force:
        raise ToolError("output-exists", f"Output already exists: {path}",
                        "Re-run with --force to approve overwriting, or choose a new path.")
    return p


def atomic_write(ctx, path, writer):
    """writer(tmp_path) writes the file; then atomically moved into place."""
    p = guard_output(ctx, path)
    if ctx.dry_run:
        ctx.written.append(str(p) + " (dry-run)")
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    retries = ctx.cfg["io"].get("write_retries", 2)
    delay = ctx.cfg["io"].get("retry_delay_seconds", 0.2)
    last = None
    for attempt in range(retries + 1):
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".pam-", suffix=p.suffix)
            os.close(fd)
            writer(tmp)
            os.replace(tmp, p)
            ctx.written.append(str(p))
            return p
        except BaseException as e:  # noqa: BLE001
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            # Validation errors and cancellation must not be retried.
            if isinstance(e, ToolError) or not isinstance(e, Exception):
                raise
            last = e
            if attempt < retries:
                time.sleep(delay)
    raise ToolError("write-failed", f"Failed to write {path}: {last}")


def save_image(img, path, ctx, **kw):
    suffix = Path(path).suffix.lower()
    fmt = {".png": "PNG", ".gif": "GIF", ".webp": "WEBP", ".bmp": "BMP",
           ".tif": "TIFF", ".tiff": "TIFF", ".tga": "TGA", ".jpg": "JPEG",
           ".jpeg": "JPEG"}.get(suffix)
    if fmt is None:
        raise ToolError("bad-output-format", f"Unsupported output extension: {suffix}",
                        "Use .png .gif .webp .bmp .tif .tga .jpg")
    if fmt == "WEBP" and not features.check("webp"):
        raise ToolError("webp-unavailable", "This Pillow build lacks WebP support.")
    if fmt == "JPEG":
        img = img.convert("RGB")

    def writer(tmp):
        params = dict(kw)
        if fmt == "PNG":
            params.setdefault("optimize", True)
        img.save(tmp, format=fmt, **params)

    return atomic_write(ctx, path, writer)


def write_json(obj, path, ctx):
    return atomic_write(ctx, path,
                        lambda tmp: Path(tmp).write_text(
                            json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8"))


def write_text(text, path, ctx):
    return atomic_write(ctx, path,
                        lambda tmp: Path(tmp).write_text(text, encoding="utf-8"))


def read_json(path):
    p = Path(path)
    if not p.is_file():
        raise ToolError("input-not-found", f"Input JSON not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError("input-invalid-json", f"Invalid JSON in {path}: {e}")


def collect_inputs(patterns, exts=(".png", ".gif", ".webp", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff")):
    """Expand files, directories and globs into a sorted image list."""
    out = []
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            out.extend(sorted(q for q in p.iterdir() if q.suffix.lower() in exts))
        elif p.is_file():
            out.append(p)
        else:
            hits = sorted(Path().glob(pat))
            out.extend(q for q in hits if q.is_file() and q.suffix.lower() in exts)
    seen, uniq = set(), []
    for q in out:
        r = str(q.resolve())
        if r not in seen:
            seen.add(r)
            uniq.append(q)
    if not uniq:
        raise ToolError("no-inputs", f"No input images matched: {patterns}",
                        "Pass image files, a directory, or a glob like 'frames/*.png'.")
    return uniq


def load_frames(paths, cfg):
    """Load still or animated inputs into a flat RGBA frame list + durations."""
    files = collect_inputs(paths)
    if len(files) > cfg["limits"]["max_inputs"]:
        raise ToolError("limit-inputs", f"{len(files)} inputs exceeds max_inputs.")
    frames, durations = [], []
    for f in files:
        try:
            img = Image.open(f)
        except Exception as e:
            raise ToolError("input-unreadable", f"Cannot open {f}: {e}")
        n = getattr(img, "n_frames", 1)
        if n > cfg["limits"]["max_frames"]:
            raise ToolError("limit-frames", f"{f} has {n} frames (> max_frames).")
        for i in range(n):
            img.seek(i)
            fr = img.convert("RGBA")
            check_dims(cfg, fr.width, fr.height, frames=len(frames) + 1)
            frames.append(fr)
            durations.append(img.info.get("duration", 1000 // cfg["defaults"]["fps"]))
    return frames, durations, files


# ----------------------------------------------------------------- color ---

def parse_color(s):
    """'#rgb', '#rrggbb', '#rrggbbaa', 'r,g,b[,a]', or a few names -> RGBA."""
    if isinstance(s, (list, tuple)):
        t = tuple(int(x) for x in s)
        if len(t) == 3:
            return t + (255,)
        if len(t) == 4:
            return t
        raise ToolError("bad-color", f"Bad color tuple: {s}")
    s = str(s).strip().lower()
    names = {"transparent": (0, 0, 0, 0), "black": (0, 0, 0, 255),
             "white": (255, 255, 255, 255), "red": (255, 0, 0, 255),
             "green": (0, 255, 0, 255), "blue": (0, 0, 255, 255),
             "yellow": (255, 255, 0, 255), "magenta": (255, 0, 255, 255),
             "cyan": (0, 255, 255, 255), "gray": (128, 128, 128, 255),
             "grey": (128, 128, 128, 255)}
    if s in names:
        return names[s]
    if "," in s:
        parts = [int(x) for x in s.split(",")]
        return parse_color(parts)
    h = s.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{3}", h):
        h = "".join(c * 2 for c in h)
    if re.fullmatch(r"[0-9a-f]{6}", h):
        h += "ff"
    if not re.fullmatch(r"[0-9a-f]{8}", h):
        raise ToolError("bad-color", f"Cannot parse color: {s}",
                        "Use #rgb, #rrggbb, #rrggbbaa, or r,g,b[,a].")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))


def hex6(c):
    return "#%02x%02x%02x" % c[:3]


def hex8(c):
    return "#%02x%02x%02x%02x" % (c + (255,))[:4] if len(c) == 3 else "#%02x%02x%02x%02x" % c[:4]


def luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(c1, c2):
    l1, l2 = luminance(c1), luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def image_colors(img, with_alpha=False):
    """Unique opaque colors sorted by frequency (desc)."""
    rgba = img.convert("RGBA")
    counts = {}
    for px in rgba.getdata():
        if px[3] == 0:
            continue
        key = px if with_alpha else px[:3]
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
BAYER2 = [[0, 2], [3, 1]]


def bayer_matrix(n):
    if n == 2:
        return BAYER2
    if n == 4:
        return BAYER4
    if n == 8:
        m = [[0] * 8 for _ in range(8)]
        for y in range(8):
            for x in range(8):
                base = BAYER4[y % 4][x % 4] * 4
                sub = BAYER2[(y // 4) % 2][(x // 4) % 2]
                m[y][x] = base + sub
        return m
    raise ToolError("bad-arg", f"Unsupported Bayer size {n} (use 2, 4, 8).")


# ------------------------------------------------------------ quantizing ---

def palette_image(palette):
    """Build a Pillow P-mode reference image from RGB(A) tuples."""
    pal = [tuple(c)[:3] for c in palette][:256]
    if not pal:
        raise ToolError("empty-palette", "Palette is empty.")
    ref = Image.new("P", (1, 1))
    flat = []
    for c in pal:
        flat.extend(c)
    flat.extend([0] * (768 - len(flat)))
    ref.putpalette(flat)
    return ref


def quantize(img, colors=None, palette=None, dither="none", alpha_threshold=128):
    """Quantize RGBA -> (RGBA image, palette list). Alpha kept binary."""
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    rgb = Image.merge("RGB", (r, g, b))
    dmap = {"none": Image.Dither.NONE, "floyd": Image.Dither.FLOYDSTEINBERG,
            "fs": Image.Dither.FLOYDSTEINBERG}
    if dither not in dmap:
        raise ToolError("bad-arg", f"Unknown dither '{dither}' (none|floyd).")
    if palette:
        ref = palette_image(palette)
        q = rgb.quantize(palette=ref, dither=dmap[dither])
        pal = [tuple(c)[:3] for c in palette][:256]
    else:
        n = max(2, min(256, int(colors or 16)))
        q = rgb.quantize(colors=n, method=Image.Quantize.MEDIANCUT, dither=dmap[dither])
        raw = q.getpalette()[: n * 3]
        pal = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    out = q.convert("RGB").convert("RGBA")
    mask = a.point(lambda v: 255 if v >= alpha_threshold else 0)
    out.putalpha(mask)
    return out, pal


def ordered_dither(img, palette, matrix_size=4, strength=48, alpha_threshold=128):
    """Palette-aware ordered (Bayer) dithering, pure pixel loop."""
    rgba = img.convert("RGBA")
    m = bayer_matrix(matrix_size)
    n = len(m)
    pal = [tuple(c)[:3] for c in palette]
    if not pal:
        raise ToolError("empty-palette", "Palette is empty.")
    cache = {}

    def nearest(c):
        if c not in cache:
            cache[c] = min(pal, key=lambda p: (p[0]-c[0])**2 + (p[1]-c[1])**2 + (p[2]-c[2])**2)
        return cache[c]

    src = list(rgba.getdata())
    out = []
    w = rgba.width
    for i, px in enumerate(src):
        if px[3] < alpha_threshold:
            out.append((0, 0, 0, 0))
            continue
        x, y = i % w, i // w
        t = (m[y % n][x % n] + 0.5) / (n * n) - 0.5
        adj = tuple(max(0, min(255, int(v + t * strength))) for v in px[:3])
        out.append(nearest(adj) + (255,))
    res = Image.new("RGBA", rgba.size)
    res.putdata(out)
    return res


# -------------------------------------------------------------- geometry ---

def nearest_scale(img, factor):
    if factor < 1 or int(factor) != factor:
        raise ToolError("bad-arg", f"Scale factor must be a positive integer, got {factor}.")
    return img.resize((img.width * factor, img.height * factor), Image.Resampling.NEAREST)


def scale_to(img, w, h, method="nearest"):
    methods = {"nearest": Image.Resampling.NEAREST, "box": Image.Resampling.BOX,
               "bilinear": Image.Resampling.BILINEAR, "lanczos": Image.Resampling.LANCZOS}
    if method not in methods:
        raise ToolError("bad-arg", f"Unknown scale method '{method}'.")
    return img.resize((max(1, w), max(1, h)), methods[method])


def trim(img, padding=0):
    """Trim transparent bounds; returns (image, offset_x, offset_y, orig_size)."""
    rgba = img.convert("RGBA")
    bbox = rgba.getbbox()
    if bbox is None:
        return Image.new("RGBA", (1, 1)), 0, 0, rgba.size
    l, t, r, b = bbox
    l = max(0, l - padding); t = max(0, t - padding)
    r = min(rgba.width, r + padding); b = min(rgba.height, b + padding)
    return rgba.crop((l, t, r, b)), l, t, rgba.size


def _recon_score(rgba, b):
    w, h = rgba.size
    sw, sh = max(1, w // b), max(1, h // b)
    small = rgba.resize((sw, sh), Image.Resampling.BOX)
    back = small.resize((sw * b, sh * b), Image.Resampling.NEAREST)
    ref = rgba.crop((0, 0, sw * b, sh * b))
    a = list(ref.getdata()); c = list(back.getdata())
    step = max(1, len(a) // 20000)
    same = total = 0
    for i in range(0, len(a), step):
        total += 1
        if max(abs(a[i][j] - c[i][j]) for j in range(4)) <= 10:
            same += 1
    return same / max(1, total)


def detect_grid(img, max_block=16, threshold=0.985):
    """Detect the native pixel block of upscaled pixel art.

    Primary: GCD of interior run lengths (exact for clean nearest upscales).
    Fallback: box-downscale reconstruction score (handles resampled damage).
    """
    rgba = img.convert("RGBA")
    w, h = rgba.size
    if len(set(rgba.getdata())) < 2:
        return {"block": 1, "score": 1.0, "method": "no-detail", "candidates": {}}
    g = 0
    for seq in (list(rgba.getdata())[y * w:(y + 1) * w] for y in range(h)):
        runs, cur, ln = [], seq[0], 1
        for px in seq[1:]:
            if px == cur:
                ln += 1
            else:
                runs.append(ln); cur, ln = px, 1
        runs.append(ln)
        for ln in runs[1:-1]:  # interior runs only (border runs may be partial)
            g = math.gcd(g, ln)
    for x in range(w):
        col = [rgba.getpixel((x, y)) for y in range(h)]
        runs, cur, ln = [], col[0], 1
        for px in col[1:]:
            if px == cur:
                ln += 1
            else:
                runs.append(ln); cur, ln = px, 1
        runs.append(ln)
        for ln in runs[1:-1]:
            g = math.gcd(g, ln)
        if g == 1:
            break
    if g >= 2:
        score = _recon_score(rgba, g)
        if score >= 0.95:
            return {"block": g, "score": round(score, 4), "method": "run-gcd", "candidates": {}}
    # Fallback for resampled (bilinear/bicubic) damage: the interpolation knots
    # form a periodic spike train in the second derivative; autocorrelate it.
    data = list(rgba.getdata())
    sx = [0.0] * (w - 2)
    for y in range(h):
        base = y * w
        for x in range(1, w - 1):
            a, b, c = data[base + x - 1], data[base + x], data[base + x + 1]
            sx[x - 1] += max(abs(a[j] - 2 * b[j] + c[j]) for j in range(3))
    sy = [0.0] * (h - 2)
    for x in range(w):
        for y in range(1, h - 1):
            a, b, c = data[(y - 1) * w + x], data[y * w + x], data[(y + 1) * w + x]
            sy[y - 1] += max(abs(a[j] - 2 * b[j] + c[j]) for j in range(3))

    def acf_best(s):
        if len(s) < 6:
            return 1, 0.0
        mean = sum(s) / len(s)
        s = [v - mean for v in s]
        norm = sum(v * v for v in s) or 1.0
        scores = {lag: sum(s[i] * s[i + lag] for i in range(len(s) - lag)) / norm
                  for lag in range(2, min(max_block, len(s) // 2) + 1)}
        best = max(scores, key=scores.get)
        return best, scores[best]

    bx, cx = acf_best(sx)
    by, cy = acf_best(sy)
    if bx == by and cx >= 0.45 and cy >= 0.45:
        return {"block": bx, "score": round(min(cx, cy), 4),
                "method": "gradient-period", "candidates": {}}
    return {"block": 1, "score": round(_recon_score(rgba, 1), 4),
            "method": "none", "candidates": {}}


def components(mask_img, connectivity=8, max_components=4096):
    """Connected components on an L-mode mask (>0 = foreground)."""
    w, h = mask_img.size
    data = mask_img.tobytes()
    seen = bytearray(w * h)
    nbrs = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if connectivity == 8:
        nbrs += [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    boxes = []
    for start in range(w * h):
        if not data[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        minx = maxx = start % w
        miny = maxy = start // w
        pixels = []
        while stack:
            idx = stack.pop()
            x, y = idx % w, idx // w
            pixels.append(idx)
            minx = min(minx, x); maxx = max(maxx, x)
            miny = min(miny, y); maxy = max(maxy, y)
            for dx, dy in nbrs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    if data[ni] and not seen[ni]:
                        seen[ni] = 1
                        stack.append(ni)
        boxes.append({"bbox": (minx, miny, maxx + 1, maxy + 1), "pixels": pixels})
        if len(boxes) >= max_components:
            break
    return boxes


# ------------------------------------------------------------- pixel ops ---

def outline(img, color=(0, 0, 0, 255), mode="outer", alpha_threshold=128):
    """Add a 1px outline. mode: outer | inner | inline (both)."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    src = list(rgba.getdata())
    out = list(src)

    def opaque(x, y):
        return 0 <= x < w and 0 <= y < h and src[y * w + x][3] >= alpha_threshold

    for y in range(h):
        for x in range(w):
            i = y * w + x
            is_op = src[i][3] >= alpha_threshold
            nbr = any(opaque(x + dx, y + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
            if mode in ("outer", "inline") and not is_op and nbr:
                out[i] = tuple(color)
            elif mode in ("inner", "inline") and is_op and not all(
                    opaque(x + dx, y + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))):
                out[i] = tuple(color)
    res = Image.new("RGBA", rgba.size)
    res.putdata(out)
    return res


def cleanup_orphans(img, passes=1, alpha_threshold=128):
    """Remove isolated single pixels (no same-color 4-neighbors)."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    src = list(rgba.getdata())
    removed = 0
    for _ in range(max(1, passes)):
        out = list(src)
        for y in range(h):
            for x in range(w):
                i = y * w + x
                px = src[i]
                same = 0
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and src[ny * w + nx] == px:
                        same += 1
                if same == 0:
                    if px[3] >= alpha_threshold:
                        # replace with most common opaque neighbor, else clear
                        nbrs = [src[(y + dy) * w + (x + dx)]
                                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
                                if 0 <= x + dx < w and 0 <= y + dy < h
                                and src[(y + dy) * w + (x + dx)][3] >= alpha_threshold]
                        if nbrs:
                            out[i] = max(set(nbrs), key=nbrs.count)
                        else:
                            out[i] = (0, 0, 0, 0)
                        removed += 1
                    elif px[3] > 0:
                        out[i] = (0, 0, 0, 0)
        src = out
    res = Image.new("RGBA", rgba.size)
    res.putdata(src)
    return res, removed


def silhouette(img, color=(0, 0, 0, 255), alpha_threshold=128):
    rgba = img.convert("RGBA")
    out = [(tuple(color) if px[3] >= alpha_threshold else (0, 0, 0, 0))
           for px in rgba.getdata()]
    res = Image.new("RGBA", rgba.size)
    res.putdata(out)
    return res


def drop_shadow(img, offset=(2, 2), color=(0, 0, 0, 110), blob=False, alpha_threshold=128):
    rgba = img.convert("RGBA")
    sh = silhouette(rgba, color, alpha_threshold)
    if blob:
        bbox = rgba.getbbox() or (0, 0, rgba.width, rgba.height)
        sh = Image.new("RGBA", rgba.size)
        d = ImageDraw.Draw(sh)
        bw = max(2, int((bbox[2] - bbox[0]) * 0.8))
        bh = max(1, int(bw * 0.3))
        cx = (bbox[0] + bbox[2]) // 2
        by = min(rgba.height - 1, bbox[3])
        d.ellipse((cx - bw // 2, by - bh // 2, cx + bw // 2, by + bh // 2), fill=color)
    canvas = Image.new("RGBA", (rgba.width + abs(offset[0]), rgba.height + abs(offset[1])))
    sx, sy = max(0, offset[0]), max(0, offset[1])
    ix, iy = max(0, -offset[0]), max(0, -offset[1])
    canvas.alpha_composite(sh, (sx, sy))
    canvas.alpha_composite(rgba, (ix, iy))
    return canvas


def remove_background(img, tolerance=12, key=None, alpha_threshold=128):
    """Flood-fill remove a flat background from the borders (or a key color)."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    src = list(rgba.getdata())
    if key is None:
        corners = [src[0], src[w - 1], src[(h - 1) * w], src[h * w - 1]]
        key = max(set(corners), key=corners.count)[:3]
    else:
        key = tuple(key)[:3]

    def match(px):
        return px[3] < alpha_threshold or all(abs(px[j] - key[j]) <= tolerance for j in range(3))

    seen = bytearray(w * h)
    stack = [x for x in range(w)] + [(h - 1) * w + x for x in range(w)]
    stack += [y * w for y in range(h)] + [y * w + w - 1 for y in range(h)]
    out = list(src)
    while stack:
        i = stack.pop()
        if seen[i] or not match(src[i]):
            continue
        seen[i] = 1
        out[i] = (0, 0, 0, 0)
        x, y = i % w, i // w
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                ni = ny * w + nx
                if not seen[ni]:
                    stack.append(ni)
    res = Image.new("RGBA", rgba.size)
    res.putdata(out)
    return res, hex6(key)


RECOLOR_PRESETS = {
    "night": {"hue_shift": 0.55, "target_hue": 0.62, "mix": 0.35, "sat": 0.75, "val": 0.62},
    "sunset": {"hue_shift": 0.0, "target_hue": 0.06, "mix": 0.30, "sat": 1.15, "val": 0.95},
    "dawn": {"hue_shift": 0.0, "target_hue": 0.10, "mix": 0.22, "sat": 0.95, "val": 1.05},
    "storm": {"hue_shift": 0.0, "target_hue": 0.58, "mix": 0.25, "sat": 0.55, "val": 0.78},
    "underwater": {"hue_shift": 0.0, "target_hue": 0.52, "mix": 0.40, "sat": 0.85, "val": 0.85},
    "winter": {"hue_shift": 0.0, "target_hue": 0.55, "mix": 0.20, "sat": 0.60, "val": 1.10},
    "neon": {"hue_shift": 0.0, "target_hue": None, "mix": 0.0, "sat": 1.6, "val": 1.1},
    "sepia": {"hue_shift": 0.0, "target_hue": 0.09, "mix": 0.65, "sat": 0.55, "val": 0.95},
}


def recolor_hsv(img, hue_shift=0.0, sat=1.0, val=1.0, target_hue=None, mix=0.0,
                requantize=None, alpha_threshold=128):
    """HSV-space recolor using Pillow's fast HSV conversion."""
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    hsv = Image.merge("RGB", (r, g, b)).convert("HSV")
    hh, ss, vv = hsv.split()
    if target_hue is not None and mix > 0:
        th = int((target_hue % 1.0) * 255)
        hh = hh.point(lambda v: int((v * (1 - mix) + th * mix)) % 256)
    if hue_shift:
        sh = int((hue_shift % 1.0) * 255)
        hh = hh.point(lambda v: (v + sh) % 256)
    if sat != 1.0:
        ss = ss.point(lambda v: max(0, min(255, int(v * sat))))
    if val != 1.0:
        vv = vv.point(lambda v: max(0, min(255, int(v * val))))
    rgb = Image.merge("HSV", (hh, ss, vv)).convert("RGB")
    res = rgb.convert("RGBA")
    res.putalpha(a.point(lambda v: 255 if v >= alpha_threshold else 0))
    if requantize:
        res, _ = quantize(res, colors=requantize)
    return res


def swap_palette(img, src_pal, dst_pal, tolerance=0, alpha_threshold=128):
    """Map colors of src_pal to dst_pal by index (nearest match for others)."""
    if len(src_pal) != len(dst_pal):
        raise ToolError("palette-size", "Source and destination palettes must have equal length.",
                        "Pad the shorter palette or use palette-apply for arbitrary requantization.")
    rgba = img.convert("RGBA")
    w, h = rgba.size
    src = [tuple(c)[:3] for c in src_pal]
    dst = [tuple(c) for c in dst_pal]
    cache = {}

    def target(c):
        if c in cache:
            return cache[c]
        best, bd = None, None
        for i, s in enumerate(src):
            d = sum((s[j] - c[j]) ** 2 for j in range(3))
            if bd is None or d < bd:
                best, bd = i, d
        if tolerance and bd > tolerance * tolerance * 3:
            cache[c] = c
        else:
            t = dst[best]
            cache[c] = (t[0], t[1], t[2])
        return cache[c]

    out = []
    for px in rgba.getdata():
        if px[3] < alpha_threshold:
            out.append((0, 0, 0, 0))
        else:
            out.append(target(px[:3]) + (255,))
    res = Image.new("RGBA", rgba.size)
    res.putdata(out)
    return res


def glitch(img, seed=7, intensity=0.5, bands=6, channel_shift=2):
    rng = random.Random(seed)
    rgba = img.convert("RGBA")
    w, h = rgba.size
    out = rgba.copy()
    intensity = max(0.0, min(1.0, intensity))
    for _ in range(max(1, int(bands * intensity))):
        y = rng.randrange(h)
        bh = rng.randint(1, max(1, int(h * 0.12)))
        dx = rng.randint(-int(w * 0.2 * intensity) - 1, int(w * 0.2 * intensity) + 1)
        band = rgba.crop((0, y, w, min(h, y + bh)))
        out.paste((0, 0, 0, 0), (0, y, w, min(h, y + bh)))
        out.alpha_composite(band, ((dx) % w, y))
        if dx > 0:
            out.alpha_composite(band.crop((w - dx, 0, w, band.height)), (0, y))
        elif dx < 0:
            out.alpha_composite(band.crop((0, 0, -dx, band.height)), (w + dx, y))
    if channel_shift > 0 and intensity > 0.3:
        r, g, b, a = out.split()
        sh = max(1, int(channel_shift * intensity))
        rr = Image.new("L", (w, h)); rr.paste(r, (sh, 0))
        bb = Image.new("L", (w, h)); bb.paste(b, (-sh, 0))
        out = Image.merge("RGBA", (rr, g, bb, a))
    return out


def normal_map(img, strength=2.0, alpha_threshold=128):
    """Tangent-space normal map from luminance height."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    lum = rgba.convert("L")
    px = lum.load()
    alpha = rgba.getchannel("A").load()
    out = Image.new("RGBA", (w, h))
    op = out.load()
    for y in range(h):
        for x in range(w):
            if alpha[x, y] < alpha_threshold:
                op[x, y] = (128, 128, 255, 0)
                continue
            xl = px[max(0, x - 1), y] / 255.0
            xr = px[min(w - 1, x + 1), y] / 255.0
            yt = px[x, max(0, y - 1)] / 255.0
            yb = px[x, min(h - 1, y + 1)] / 255.0
            nx = (xl - xr) * strength
            ny = (yt - yb) * strength
            nz = 1.0
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            op[x, y] = (round((nx / ln * 0.5 + 0.5) * 255),
                        round((ny / ln * 0.5 + 0.5) * 255),
                        round((nz / ln * 0.5 + 0.5) * 255), 255)
    return out


def display_sim(img, mode="crt", scale=3, intensity=0.5):
    """Presentation-layer display simulation (never alters the master)."""
    base = nearest_scale(img.convert("RGBA"), scale)
    w, h = base.size
    out = base.copy()
    px = out.load()
    intensity = max(0.0, min(1.0, intensity))
    if mode == "crt":
        for y in range(h):
            if y % scale == scale - 1:
                for x in range(w):
                    r, g, b, a = px[x, y]
                    f = 1 - 0.55 * intensity
                    px[x, y] = (int(r * f), int(g * f), int(b * f), a)
        for x in range(w):
            tint = (x // max(1, scale)) % 3
            for y in range(h):
                r, g, b, a = px[x, y]
                boost = int(18 * intensity)
                if tint == 0:
                    r = min(255, r + boost)
                elif tint == 1:
                    g = min(255, g + boost)
                else:
                    b = min(255, b + boost)
                px[x, y] = (r, g, b, a)
    elif mode == "lcd":
        grid = (30, 30, 34, 255)
        for y in range(h):
            for x in range(w):
                if x % scale == scale - 1 or y % scale == scale - 1:
                    r, g, b, a = px[x, y]
                    f = intensity
                    px[x, y] = (int(r * (1 - f) + grid[0] * f),
                                int(g * (1 - f) + grid[1] * f),
                                int(b * (1 - f) + grid[2] * f), a)
    elif mode == "led":
        cell = max(2, scale)
        small = img.convert("RGBA").resize((max(1, w // cell), max(1, h // cell)), Image.Resampling.BOX)
        out = Image.new("RGBA", (small.width * cell, small.height * cell), (8, 8, 10, 255))
        d = ImageDraw.Draw(out)
        sp = small.load()
        for y in range(small.height):
            for x in range(small.width):
                c = sp[x, y]
                if c[3] < 64:
                    continue
                cx, cy = x * cell + cell // 2, y * cell + cell // 2
                rad = max(1, cell // 2 - 1)
                d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=c)
    elif mode in ("gameboy", "mono", "1bit"):
        gray = img.convert("L")
        if mode == "gameboy":
            pal = [(15, 56, 15), (48, 98, 48), (139, 172, 15), (155, 188, 15)]
            data = [pal[min(3, v * 4 // 256)] + (255,) for v in gray.getdata()]
            mono = Image.new("RGBA", gray.size)
            mono.putdata(data)
            out = nearest_scale(mono, scale)
        elif mode == "mono":
            data = [(v, v, v, 255) for v in gray.getdata()]
            mono = Image.new("RGBA", gray.size)
            mono.putdata(data)
            out = nearest_scale(mono, scale)
        else:
            m = bayer_matrix(4)
            data = []
            gw = gray.width
            for i, v in enumerate(gray.getdata()):
                t = m[(i // gw) % 4][i % 4] * 16
                data.append(((255, 255, 255, 255) if v > t else (0, 0, 0, 255)))
            mono = Image.new("RGBA", gray.size)
            mono.putdata(data)
            out = nearest_scale(mono, scale)
    else:
        raise ToolError("bad-arg", f"Unknown display mode '{mode}' (crt|lcd|led|gameboy|mono|1bit).")
    return out


def to_svg(img, path, ctx, pixel=1, background=None):
    """Grid-aligned SVG: one rect per horizontal run of identical pixels."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    data = list(rgba.getdata())
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w * pixel}" height="{h * pixel}" '
             f'viewBox="0 0 {w} {h}" shape-rendering="crispEdges">']
    if background:
        parts.append(f'<rect width="{w}" height="{h}" fill="{background}"/>')
    for y in range(h):
        x = 0
        row = data[y * w:(y + 1) * w]
        while x < w:
            c = row[x]
            x2 = x + 1
            while x2 < w and row[x2] == c:
                x2 += 1
            if c[3] > 0:
                fill = hex6(c)
                op = f' fill-opacity="{c[3] / 255:.3f}"' if c[3] < 255 else ""
                parts.append(f'<rect x="{x}" y="{y}" width="{x2 - x}" height="1" fill="{fill}"{op}/>')
            x = x2
    parts.append("</svg>")
    return write_text("\n".join(parts), path, ctx)


def composite_layers(layers):
    """layers: list of (img, x, y, opacity)."""
    if not layers:
        raise ToolError("no-inputs", "No layers to composite.")
    w = max(im.width + x for im, x, y, o in layers)
    h = max(im.height + y for im, x, y, o in layers)
    canvas = Image.new("RGBA", (max(1, w), max(1, h)))
    for im, x, y, o in layers:
        layer = im.convert("RGBA")
        if o < 1.0:
            a = layer.getchannel("A").point(lambda v: int(v * o))
            layer.putalpha(a)
        canvas.alpha_composite(layer, (max(0, x), max(0, y)))
    return canvas


# =============================================================== commands ==

def cmd_convert(a, ctx):
    pal_mod = sibling("palettes")
    img = load_image(a.input, ctx.cfg)
    tw = a.width
    th = a.height or max(1, round(img.height * tw / img.width))
    check_dims(ctx.cfg, tw, th)
    small = scale_to(img, tw, th, a.resample)
    palette = None
    if a.palette:
        palette = pal_mod.read_palette_any(a.palette)
    if a.dither in ("bayer2", "bayer4", "bayer8"):
        if not palette:
            _, palette = quantize(small, colors=a.colors)
        small = ordered_dither(small, palette, int(a.dither[-1]), strength=a.dither_strength)
        pal = palette
    else:
        small, pal = quantize(small, colors=a.colors, palette=palette, dither=a.dither,
                              alpha_threshold=ctx.cfg["defaults"]["alpha_threshold"])
    if a.outline:
        small = outline(small, parse_color(a.outline))
    if a.cleanup:
        small, removed = cleanup_orphans(small)
        if removed:
            ctx.warn(f"cleanup replaced {removed} orphan pixels")
    save_image(small, a.output, ctx)
    result = {"input": a.input, "output": a.output, "native_size": [tw, th],
              "colors_used": len(image_colors(small)), "palette_size": len(pal)}
    if a.preview_scale and a.preview_scale > 1:
        prev = nearest_scale(small, a.preview_scale)
        ppath = str(Path(a.output).with_name(Path(a.output).stem + f"-x{a.preview_scale}.png"))
        save_image(prev, ppath, ctx)
        result["preview"] = ppath
    if a.palette_out:
        pal_mod.write_palette(pal, a.palette_out, ctx)
        result["palette_file"] = a.palette_out
    return result


def cmd_scale(a, ctx):
    img = load_image(a.input, ctx.cfg)
    if a.factor:
        if a.factor != int(a.factor):
            raise ToolError("non-integer-scale", "Factor must be an integer for pixel-perfect scaling.",
                            "Use --width/--height with --method for controlled non-integer scaling.")
        f = int(a.factor)
        check_dims(ctx.cfg, img.width * f, img.height * f)
        out = nearest_scale(img, f)
        info = {"factor": f, "method": "nearest"}
    else:
        if not (a.width or a.height):
            raise ToolError("bad-arg", "Provide --factor or --width/--height.")
        w = a.width or max(1, round(img.width * a.height / img.height))
        h = a.height or max(1, round(img.height * a.width / img.width))
        check_dims(ctx.cfg, w, h)
        if a.method == "nearest" and (w % img.width or h % img.height):
            ctx.warn("Non-integer nearest scaling produces uneven pixel sizes; "
                     "consider integer --factor or --method box for downscaling.")
        out = scale_to(img, w, h, a.method)
        info = {"method": a.method}
    save_image(out, a.output, ctx)
    return {"input": a.input, "output": a.output,
            "size": [img.width, img.height], "new_size": list(out.size), **info}


def cmd_transform(a, ctx):
    img = load_image(a.input, ctx.cfg)
    ops = {"flip-h": Image.Transpose.FLIP_LEFT_RIGHT, "flip-v": Image.Transpose.FLIP_TOP_BOTTOM,
           "rot90": Image.Transpose.ROTATE_90, "rot180": Image.Transpose.ROTATE_180,
           "rot270": Image.Transpose.ROTATE_270,
           "transpose": Image.Transpose.TRANSPOSE, "transverse": Image.Transpose.TRANSVERSE}
    if a.op not in ops:
        raise ToolError("bad-arg", f"Unknown transform '{a.op}'. Options: {', '.join(ops)}")
    out = img.transpose(ops[a.op])
    save_image(out, a.output, ctx)
    return {"op": a.op, "output": a.output, "new_size": list(out.size)}


def cmd_crop(a, ctx):
    img = load_image(a.input, ctx.cfg)
    box = (a.x, a.y, a.x + a.width, a.y + a.height)
    if a.x < 0 or a.y < 0 or box[2] > img.width or box[3] > img.height or a.width < 1 or a.height < 1:
        raise ToolError("bad-crop", f"Crop box {box} outside image {img.size}.")
    out = img.crop(box)
    save_image(out, a.output, ctx)
    return {"box": list(box), "output": a.output}


def cmd_trim(a, ctx):
    img = load_image(a.input, ctx.cfg)
    out, ox, oy, orig = trim(img, a.padding)
    save_image(out, a.output, ctx)
    meta = {"file": Path(a.output).name, "trimmed_size": list(out.size),
            "offset": [ox, oy], "original_size": list(orig)}
    if a.meta:
        write_json(meta, a.meta, ctx)
    return meta


def cmd_pad(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_dims(ctx.cfg, a.width, a.height)
    if a.width < img.width or a.height < img.height:
        raise ToolError("bad-pad", "Target canvas smaller than image; use crop instead.")
    bg = parse_color(a.background)
    out = Image.new("RGBA", (a.width, a.height), bg)
    pos = {"center": ((a.width - img.width) // 2, (a.height - img.height) // 2),
           "top-left": (0, 0), "bottom": ((a.width - img.width) // 2, a.height - img.height)}
    if a.anchor not in pos:
        raise ToolError("bad-arg", f"Unknown anchor '{a.anchor}'.")
    out.alpha_composite(img, pos[a.anchor])
    save_image(out, a.output, ctx)
    return {"output": a.output, "canvas": [a.width, a.height], "anchor": a.anchor}


def cmd_outline(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    out = outline(img, parse_color(a.color), a.mode)
    save_image(out, a.output, ctx)
    return {"output": a.output, "mode": a.mode, "color": a.color}


def cmd_cleanup(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    out, removed = cleanup_orphans(img, a.passes)
    save_image(out, a.output, ctx)
    return {"output": a.output, "orphans_fixed": removed, "passes": a.passes}


def cmd_dither(a, ctx):
    pal_mod = sibling("palettes")
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    palette = pal_mod.read_palette_any(a.palette) if a.palette else None
    if not palette:
        _, palette = quantize(img, colors=a.colors)
    out = ordered_dither(img, palette, a.matrix, strength=a.strength)
    save_image(out, a.output, ctx)
    return {"output": a.output, "matrix": a.matrix, "palette_size": len(palette)}


def cmd_shadow(a, ctx):
    img = load_image(a.input, ctx.cfg)
    out = drop_shadow(img, (a.dx, a.dy), parse_color(a.color), blob=a.blob)
    save_image(out, a.output, ctx)
    return {"output": a.output, "blob": a.blob, "offset": [a.dx, a.dy], "new_size": list(out.size)}


def cmd_silhouette(a, ctx):
    img = load_image(a.input, ctx.cfg)
    out = silhouette(img, parse_color(a.color))
    save_image(out, a.output, ctx)
    return {"output": a.output, "color": a.color}


def cmd_recolor(a, ctx):
    img = load_image(a.input, ctx.cfg)
    if a.preset:
        if a.preset not in RECOLOR_PRESETS:
            raise ToolError("bad-arg", f"Unknown preset '{a.preset}'. Options: {', '.join(RECOLOR_PRESETS)}")
        params = dict(RECOLOR_PRESETS[a.preset])
    else:
        params = {"hue_shift": a.hue_shift, "sat": a.sat, "val": a.val,
                  "target_hue": None, "mix": 0.0}
    out = recolor_hsv(img, requantize=a.colors or None, **params)
    save_image(out, a.output, ctx)
    return {"output": a.output, "preset": a.preset, "params": params,
            "colors_used": len(image_colors(out))}


def cmd_remove_bg(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    key = parse_color(a.key)[:3] if a.key else None
    out, used = remove_background(img, a.tolerance, key)
    save_image(out, a.output, ctx)
    return {"output": a.output, "key_color": used, "tolerance": a.tolerance}


def cmd_split_layers(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    w, h = img.size
    outdir = Path(a.outdir)
    manifest = {"source": a.input, "size": [w, h], "mode": a.mode, "layers": []}
    if a.mode == "component":
        comps = components(img.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
        comps.sort(key=lambda c: -len(c["pixels"]))
        for i, comp in enumerate(comps[: a.max_layers]):
            layer = Image.new("RGBA", (w, h))
            lp = layer.load(); sp = img.load()
            for idx in comp["pixels"]:
                x, y = idx % w, idx // w
                lp[x, y] = sp[x, y]
            name = f"layer-{i:03d}.png"
            save_image(layer, str(outdir / name), ctx)
            manifest["layers"].append({"file": name, "bbox": list(comp["bbox"]),
                                       "pixels": len(comp["pixels"])})
    elif a.mode == "color":
        cols = image_colors(img)
        for i, (c, count) in enumerate(cols[: a.max_layers]):
            layer = Image.new("RGBA", (w, h))
            data = [(px if px[:3] == c and px[3] >= 128 else (0, 0, 0, 0))
                    for px in img.getdata()]
            layer.putdata(data)
            name = f"layer-{i:03d}-{hex6(c)[1:]}.png"
            save_image(layer, str(outdir / name), ctx)
            manifest["layers"].append({"file": name, "color": hex6(c), "pixels": count})
        if len(cols) > a.max_layers:
            ctx.warn(f"{len(cols)} colors found; exported first {a.max_layers} (use --max-layers).")
    else:
        raise ToolError("bad-arg", "mode must be component or color.")
    write_json(manifest, str(outdir / "layers.json"), ctx)
    return manifest


def cmd_composite(a, ctx):
    if a.layers_json:
        spec = read_json(a.layers_json)
        base = Path(a.layers_json).parent
        layers = []
        for l in spec.get("layers", []):
            im = load_image(str(base / l["file"]), ctx.cfg)
            layers.append((im, l.get("x", 0), l.get("y", 0), l.get("opacity", 1.0)))
    else:
        files = collect_inputs(a.inputs)
        layers = [(load_image(str(f), ctx.cfg), 0, 0, 1.0) for f in files]
    out = composite_layers(layers)
    check_dims(ctx.cfg, out.width, out.height)
    save_image(out, a.output, ctx)
    return {"output": a.output, "layers": len(layers), "size": list(out.size)}


def cmd_glitch(a, ctx):
    img = load_image(a.input, ctx.cfg)
    out = glitch(img, a.seed, a.intensity, a.bands, a.channel_shift)
    save_image(out, a.output, ctx)
    return {"output": a.output, "seed": a.seed, "intensity": a.intensity}


def cmd_display_sim(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_dims(ctx.cfg, img.width * a.scale, img.height * a.scale)
    out = display_sim(img, a.mode, a.scale, a.intensity)
    save_image(out, a.output, ctx)
    return {"output": a.output, "mode": a.mode, "scale": a.scale,
            "note": "Presentation preview only; keep the unsimulated master."}


def cmd_normal_map(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    out = normal_map(img, a.strength)
    save_image(out, a.output, ctx)
    return {"output": a.output, "strength": a.strength, "space": "tangent (+Y up convention may need flip per engine)"}


def cmd_svg_export(a, ctx):
    img = load_image(a.input, ctx.cfg)
    check_op_pixels(ctx.cfg, img.width, img.height)
    to_svg(img, a.output, ctx, background=a.background)
    return {"output": a.output, "size": list(img.size), "crisp_edges": True}


def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    p = add("convert", "Convert any image into deliberate pixel art (downscale + palette + cleanup)", cmd_convert)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--width", type=int, default=64, help="Target native width (default 64)")
    p.add_argument("--height", type=int, help="Target native height (default: keep aspect)")
    p.add_argument("--colors", type=int, default=16)
    p.add_argument("--palette", help="Palette file to constrain colors")
    p.add_argument("--dither", default="none", choices=["none", "floyd", "bayer2", "bayer4", "bayer8"])
    p.add_argument("--dither-strength", type=int, default=48)
    p.add_argument("--resample", default="box", choices=["box", "bilinear", "lanczos", "nearest"])
    p.add_argument("--outline", help="Add outline of this color after conversion")
    p.add_argument("--cleanup", action="store_true", help="Remove orphan pixels")
    p.add_argument("--preview-scale", type=int, default=0, help="Also write an integer nearest upscaled preview")
    p.add_argument("--palette-out", help="Write the resulting palette to this file")

    p = add("scale", "Pixel-perfect integer scaling (or controlled resampling)", cmd_scale)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--factor", type=float)
    p.add_argument("--width", type=int); p.add_argument("--height", type=int)
    p.add_argument("--method", default="nearest", choices=["nearest", "box", "bilinear", "lanczos"])

    p = add("transform", "Lossless flips and 90-degree rotations", cmd_transform)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("op", choices=["flip-h", "flip-v", "rot90", "rot180", "rot270", "transpose", "transverse"])

    p = add("crop", "Crop an exact pixel rectangle", cmd_crop)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--x", type=int, default=0); p.add_argument("--y", type=int, default=0)
    p.add_argument("--width", type=int, required=True); p.add_argument("--height", type=int, required=True)

    p = add("trim", "Trim transparent bounds, record offsets for engine alignment", cmd_trim)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--meta", help="Write offset/size metadata JSON here")

    p = add("pad", "Place image on a larger canvas with anchor + background", cmd_pad)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--width", type=int, required=True); p.add_argument("--height", type=int, required=True)
    p.add_argument("--anchor", default="center", choices=["center", "top-left", "bottom"])
    p.add_argument("--background", default="#00000000")

    p = add("outline", "Add a 1px pixel outline (outer/inner/inline)", cmd_outline)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--color", default="#000000"); p.add_argument("--mode", default="outer", choices=["outer", "inner", "inline"])

    p = add("cleanup", "Remove orphan pixels and stray alpha noise", cmd_cleanup)
    p.add_argument("input"); p.add_argument("output"); p.add_argument("--passes", type=int, default=1)

    p = add("dither", "Apply palette-aware ordered (Bayer) dithering", cmd_dither)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--palette"); p.add_argument("--colors", type=int, default=8)
    p.add_argument("--matrix", type=int, default=4, choices=[2, 4, 8])
    p.add_argument("--strength", type=int, default=48)

    p = add("shadow", "Generate drop or contact-blob shadow", cmd_shadow)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--dx", type=int, default=2); p.add_argument("--dy", type=int, default=2)
    p.add_argument("--color", default="#00000070"); p.add_argument("--blob", action="store_true")

    p = add("silhouette", "Extract a solid silhouette for readability checks", cmd_silhouette)
    p.add_argument("input"); p.add_argument("output"); p.add_argument("--color", default="#000000")

    p = add("recolor", "HSV recolor / lighting-variant presets (night, sunset, ...)", cmd_recolor)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--preset", choices=sorted(RECOLOR_PRESETS))
    p.add_argument("--hue-shift", type=float, default=0.0)
    p.add_argument("--sat", type=float, default=1.0); p.add_argument("--val", type=float, default=1.0)
    p.add_argument("--colors", type=int, default=0, help="Requantize to N colors (0=off)")

    p = add("remove-bg", "Flood-remove a flat background from the borders", cmd_remove_bg)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--tolerance", type=int, default=12); p.add_argument("--key", help="Key color (default: dominant border color)")

    p = add("split-layers", "Split flat art into editable layers by component or color", cmd_split_layers)
    p.add_argument("input"); p.add_argument("outdir")
    p.add_argument("--mode", default="component", choices=["component", "color"])
    p.add_argument("--max-layers", type=int, default=32)

    p = add("composite", "Composite layers (manifest JSON or ordered images)", cmd_composite)
    p.add_argument("output"); p.add_argument("inputs", nargs="*")
    p.add_argument("--layers-json", help="layers.json manifest from split-layers")

    p = add("glitch", "Controlled seeded glitch/datamosh-style effect", cmd_glitch)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--seed", type=int, default=7); p.add_argument("--intensity", type=float, default=0.5)
    p.add_argument("--bands", type=int, default=6); p.add_argument("--channel-shift", type=int, default=2)

    p = add("display-sim", "Simulate CRT/LCD/LED/Game Boy/mono presentation", cmd_display_sim)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--mode", default="crt", choices=["crt", "lcd", "led", "gameboy", "mono", "1bit"])
    p.add_argument("--scale", type=int, default=3); p.add_argument("--intensity", type=float, default=0.5)

    p = add("normal-map", "Generate a tangent-space normal map from luminance height", cmd_normal_map)
    p.add_argument("input"); p.add_argument("output"); p.add_argument("--strength", type=float, default=2.0)

    p = add("svg-export", "Export pixel grid as crisp-edges SVG rectangles", cmd_svg_export)
    p.add_argument("input"); p.add_argument("output"); p.add_argument("--background")
