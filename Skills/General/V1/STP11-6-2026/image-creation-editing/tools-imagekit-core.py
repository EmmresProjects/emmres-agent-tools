"""imagekit core: configuration, limits, path safety, naming, IO, image helpers.

Loaded by tools-imagekit.py before every other tool module and registered in
sys.modules as ``imagekit_core`` so sibling modules can ``import imagekit_core``.

No network access, no secrets, no shell execution. Standard library + Pillow only.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

# Exit codes are part of the CLI contract (documented in knowledge-readme.md).
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_APPROVAL = 4
EXIT_PROVIDER = 5
EXIT_POLICY = 6
EXIT_CANCELLED = 130

SKILL_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = "asset-config-default.json"
POLICY_FILE = "asset-quality-policy.json"
PRESETS_FILE = "asset-render-presets.json"
BRIEF_TEMPLATE_FILE = "asset-brief-template.json"
CATALOG_FILE = "asset-skill-catalog.json"

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".ico", ".ppm", ".pgm", ".avif", ".jxl", ".psd",
}
WRITE_FORMATS = {
    "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "gif": "GIF",
    "bmp": "BMP", "tif": "TIFF", "tiff": "TIFF", "ico": "ICO", "pdf": "PDF",
    "avif": "AVIF", "ppm": "PPM",
}


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class ToolError(Exception):
    """Structured, user-facing failure with a stable code and exit status."""

    def __init__(self, code, message, hint="", exit_code=EXIT_INPUT, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.exit_code = exit_code
        self.details = details or {}

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }


def usage_error(message, hint=""):
    return ToolError("usage", message, hint, EXIT_USAGE)


def input_error(message, hint="", details=None):
    return ToolError("input", message, hint, EXIT_INPUT, details)


def approval_error(message, hint="", details=None):
    return ToolError("approval_required", message, hint, EXIT_APPROVAL, details)


def policy_error(message, hint="", details=None):
    return ToolError("policy_blocked", message, hint, EXIT_POLICY, details)


def provider_error(message, hint="", details=None):
    return ToolError("provider_unavailable", message, hint, EXIT_PROVIDER, details)


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def file_size(path):
    """Byte size, or 0 when the file is absent (dry runs never write)."""
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def human_bytes(n):
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < step:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= step
    return f"{n:.1f}PB"


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def slugify(value, max_len=64):
    text = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:max_len].strip("-") or "untitled"


def read_json(path, default=None, required=False):
    p = Path(path)
    if not p.exists():
        if required:
            raise input_error(f"JSON file not found: {p}", "Check the path or omit the option.")
        return default
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise input_error(f"Invalid JSON in {p}: {exc}", "Fix the JSON syntax and retry.")


def deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def atomic_write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def atomic_write_text(path, text, encoding="utf-8"):
    return atomic_write_bytes(path, text.encode(encoding))


def write_json(path, payload):
    return atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def rel_posix(path, start=None):
    """Relative POSIX path used in every report (never absolute, never backslashes)."""
    p = Path(path)
    if start is not None:
        try:
            p = p.resolve().relative_to(Path(start).resolve())
        except ValueError:
            return p.name
    return str(p).replace("\\", "/")


# --------------------------------------------------------------------------
# path safety
# --------------------------------------------------------------------------


def ensure_dir(path):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_join(root, *parts, allow_outside=False):
    """Join under *root*, rejecting traversal and absolute escapes."""
    root = Path(root).resolve()
    target = root
    for part in parts:
        part = str(part)
        if not part:
            continue
        candidate = Path(part)
        if candidate.is_absolute():
            raise input_error(
                f"Absolute paths are not accepted here: {part}",
                "Pass a path relative to the output root.",
            )
        target = target / candidate
    resolved = target.resolve()
    if not allow_outside and not (resolved == root or root in resolved.parents):
        raise input_error(
            f"Path escapes the allowed root: {parts}",
            "Remove '..' segments; all writes must stay inside the output root.",
        )
    return resolved


def resolve_input(path):
    p = Path(path).expanduser()
    if not p.exists():
        raise input_error(f"Input not found: {p}", "Check that the path exists and is spelled correctly.")
    if not p.is_file():
        raise input_error(f"Input is not a file: {p}")
    return p


# --------------------------------------------------------------------------
# naming standard
# --------------------------------------------------------------------------


def build_filename(parts, ext, naming=None):
    """Deterministic delivery name: {project}_{content}_{variant}_WxH_vNN_status.ext"""
    naming = naming or {}
    sep = naming.get("separator") or "-"
    max_len = int(naming.get("max_length") or 96)
    fields = []
    for key in ("project", "content", "variant", "dimensions", "version", "status", "language"):
        value = parts.get(key)
        if value in (None, ""):
            continue
        if key == "dimensions" and isinstance(value, (tuple, list)):
            value = f"{int(value[0])}x{int(value[1])}"
        if key == "version":
            value = f"v{int(value):02d}"
        fields.append(slugify(value, 48))
    ext = ext if ext.startswith(".") else "." + ext
    stem = sep.join(fields) or "asset"
    if len(stem) + len(ext) > max_len:
        stem = stem[: max_len - len(ext)]
    return stem + ext.lower()


def parse_version_token(filename):
    m = re.search(r"(?:^|[_-])[vV](\d{1,3})(?:[_-]|$)", Path(filename).stem)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# configuration + context
# --------------------------------------------------------------------------


def load_config(path=None, overrides=None):
    base = read_json(SKILL_ROOT / DEFAULT_CONFIG_FILE, default={}, required=True)
    if path:
        user = read_json(resolve_input(path), required=True)
        base = deep_merge(base, user)
    if overrides:
        base = deep_merge(base, overrides)
    cfg = Config(base)
    cfg.validate()
    return cfg


class Config:
    def __init__(self, data):
        self.data = data or {}
        self.paths = self.data.get("paths") or {}
        self.limits = self.data.get("limits") or {}
        self.naming = self.data.get("naming") or {}
        self.typography = self.data.get("typography") or {}
        self.render_cfg = self.data.get("render") or {}
        self.providers = self.data.get("providers") or {}
        self.safety = self.data.get("safety") or {}
        self.quality = self.data.get("quality") or {}
        self.pipeline = self.data.get("pipeline") or {}
        self.formats = self.data.get("formats") or {}

    def validate(self):
        lim = self.limits
        for key in ("max_pixels", "max_batch_items", "max_frames", "max_output_dimension",
                    "default_timeout_s", "max_retries", "max_workers"):
            if key in lim:
                val = lim[key]
                if not isinstance(val, (int, float)) or val <= 0:
                    raise usage_error(
                        f"config limits.{key} must be a positive number",
                        "Fix asset-config-default.json or your --config override.",
                    )
        max_dim = int(lim.get("max_output_dimension", 8192))
        if max_dim > 20000:
            raise usage_error("limits.max_output_dimension above 20000 is not supported")

    def resource(self, key, fallback):
        """Resolve a referenced asset file next to the tool, or an absolute override."""
        value = self.data.get(key) or fallback
        p = Path(value)
        return p if p.is_absolute() else (SKILL_ROOT / p)

    def render_presets_path(self):
        return self.resource("render_presets_file", PRESETS_FILE)

    def policy_path(self):
        return self.resource("quality_policy_file", POLICY_FILE)

    def brief_template_path(self):
        return self.resource("brief_template_file", BRIEF_TEMPLATE_FILE)

    def catalog_path(self):
        return self.resource("catalog_file", CATALOG_FILE)

    def limit(self, key, fallback=None):
        try:
            return int(self.limits.get(key, fallback))
        except (TypeError, ValueError):
            return fallback


class Context:
    """Per-invocation state: config, output root, approvals, cancellation, audit."""

    def __init__(self, config, out_dir=None, json_mode=False, quiet=False, seed=None,
                 force=False, approve=False, approval_note="", verbose=False, dry_run=False):
        self.config = config
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        self.seed = seed
        self.force = bool(force)
        self.approve = bool(approve)
        self.approval_note = approval_note or ""
        self.dry_run = bool(dry_run)
        self.cancelled = False
        self.warnings = []
        self.audit_events = []
        self._approvals = []
        base_out = out_dir or config.paths.get("output_dir") or "output"
        self.out_root = ensure_dir(Path(base_out).expanduser())
        self.started = time.time()

    # -- approvals ---------------------------------------------------------
    def require_approval(self, action, reason, details=None):
        if self.approve:
            self._approvals.append({"action": action, "reason": reason, "at": now_iso(),
                                    "note": self.approval_note})
            self.audit("approval", action=action, reason=reason, note=self.approval_note)
            return True
        raise approval_error(
            f"Approval required before: {action}",
            f"{reason} Re-run the same command with --approve --approval-note \"why this is safe\".",
            details or {"action": action, "reason": reason},
        )

    # -- audit -------------------------------------------------------------
    def audit(self, event, **fields):
        record = {"at": now_iso(), "event": event}
        record.update(fields)
        self.audit_events.append(record)
        if not self.dry_run:
            log = self.out_root / "audit-log.jsonl"
            try:
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass

    # -- outputs -----------------------------------------------------------
    def out_path(self, name, ext=None, subdir=None):
        """Resolve an output path under out_root, honoring overwrite policy."""
        ext = ext or Path(name).suffix
        stem = Path(name).stem
        if subdir:
            ensure_dir(safe_join(self.out_root, subdir))
            target = safe_join(self.out_root, subdir, stem + ext)
        else:
            target = safe_join(self.out_root, stem + ext)
        if target.exists() and not self.force and not self.dry_run:
            raise input_error(
                f"Refusing to overwrite existing output: {rel_posix(target, self.out_root)}",
                "Choose another --name, pass --force, or set a different --out directory.",
                {"path": rel_posix(target, self.out_root)},
            )
        return target

    def note(self, message):
        self.warnings.append(message)
        if self.verbose and not self.json_mode:
            print(f"[warn] {message}", file=sys.stderr)


# --------------------------------------------------------------------------
# image IO with validation
# --------------------------------------------------------------------------


def _pillow():
    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ToolError(
            "missing_dependency",
            "Pillow is required but not installed.",
            "Run: python -m pip install -r tools-requirements.txt",
            EXIT_INTERNAL,
        ) from exc
    return sys.modules["PIL.Image"]


def open_image(path, ctx=None, limits=None, modes=("RGB", "RGBA", "L", "LA", "P", "CMYK", "I", "F")):
    """Open and validate an image; enforces byte, pixel and format limits before decoding."""
    Image = _pillow()
    p = Path(path)
    if p.suffix.lower() not in IMAGE_EXTS:
        raise input_error(f"Unsupported image extension: {p.suffix}", "Use a raster format such as .png or .jpg.")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise input_error(f"Cannot read image {p}: {exc}")
    lim = limits or (ctx.config.limits if ctx else {})
    max_bytes = int(lim.get("max_input_bytes", 100 * 1024 * 1024))
    if len(data) > max_bytes:
        raise input_error(f"Input exceeds max_input_bytes ({human_bytes(len(data))})",
                          "Raise limits.max_input_bytes in config or supply a smaller file.")
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise input_error(f"Image could not be decoded ({p.name}): {exc}",
                          "The file may be corrupt or use an unsupported codec.")
    pixels = img.width * img.height
    max_pixels = int(lim.get("max_pixels", 80_000_000))
    if pixels > max_pixels:
        raise input_error(f"Image is {pixels} pixels, above max_pixels ({max_pixels})",
                          "Downscale first or raise limits.max_pixels.")
    if modes and img.mode not in modes:
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    return img


def save_image(ctx, img, path, fmt=None, quality=92, dpi=None, pnginfo=None, icc=None,
               lossless=False, background=None, exif=None):
    """Save with format-aware settings; keeps alpha only where the format supports it."""
    Image = _pillow()
    path = Path(path)
    fmt = str(fmt or WRITE_FORMATS.get(path.suffix.lstrip(".").lower(), "PNG")).lower().lstrip(".")
    fmt = WRITE_FORMATS.get(fmt, fmt.upper())
    ensure_dir(path.parent)
    work = img
    params = {}
    if fmt == "JPEG":
        if work.mode in ("RGBA", "LA", "P"):
            bg = background or (255, 255, 255)
            base = Image.new("RGB", work.size, bg)
            rgba = work.convert("RGBA")
            base.paste(rgba, mask=rgba.split()[-1])
            work = base
        params.update(quality=int(quality), subsampling=0, optimize=True)
    elif fmt == "PNG":
        work = work.convert("RGBA") if "A" in work.getbands() else work.convert("RGB") if work.mode not in ("L", "P") else work
        params.update(optimize=True)
    elif fmt == "WEBP":
        params.update(quality=int(quality), method=4, lossless=bool(lossless))
    elif fmt == "TIFF":
        params.update(compression="tiff_lzw")
    elif fmt == "GIF":
        params.update(optimize=True)
    elif fmt == "AVIF":
        params.update(quality=int(quality))
    if dpi:
        params["dpi"] = dpi
    if pnginfo is not None and fmt == "PNG":
        params["pnginfo"] = pnginfo
    if exif is not None and fmt in ("JPEG", "TIFF", "WEBP"):
        params["exif"] = exif
    if icc and fmt in ("PNG", "JPEG", "TIFF", "WEBP"):
        params["icc_profile"] = icc
    buf = io.BytesIO()
    work.save(buf, format=fmt, **params)   # encode even in dry-run so format errors still surface
    if ctx is None or not getattr(ctx, "dry_run", False):
        atomic_write_bytes(path, buf.getvalue())
    return path


def emit_json(ctx, name, payload, subdir=None):
    path = ctx.out_path(name, ".json", subdir)
    if not ctx.dry_run:
        write_json(path, payload)
    return path


def emit_text(ctx, name, ext, text, subdir=None):
    path = ctx.out_path(name, "." + ext.lstrip("."), subdir)
    if not ctx.dry_run:
        atomic_write_text(path, text)
    return path


def emit_bytes(ctx, name, ext, data, subdir=None):
    path = ctx.out_path(name, "." + ext.lstrip("."), subdir)
    if not ctx.dry_run:
        atomic_write_bytes(path, data)
    return path


def summary_line(ctx, path):
    """Relative path plus size, used in every human-readable report."""
    try:
        return f"{rel_posix(path, ctx.out_root)} ({human_bytes(file_size(path))})"
    except OSError:
        return rel_posix(path, ctx.out_root)


def build_pnginfo(meta):
    """PNG text chunks for title/software/disclosure/provenance."""
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    for key, value in (meta or {}).items():
        if value in (None, ""):
            continue
        info.add_text(str(key), str(value))
    return info


def build_exif(meta):
    """Minimal EXIF for JPEG/TIFF/WEBP: description, software, artist, copyright, comment."""
    Image = _pillow()
    exif = Image.Exif()
    exif[0x010E] = str((meta or {}).get("ImageDescription", ""))[:512]
    exif[0x0131] = str((meta or {}).get("Software", "image-creation-editing skill tool"))[:64]
    exif[0x013B] = str((meta or {}).get("Artist", ""))[:64]
    exif[0x8298] = str((meta or {}).get("Copyright", ""))[:64]
    exif[0x9286] = ("ASCII" + "\x00\x00\x00" + str((meta or {}).get("UserComment", ""))[:900])
    return exif


def read_metadata(path):
    """Read back embedded text/EXIF metadata written by this tool."""
    Image = _pillow()
    out = {"format": None, "text": {}, "exif": {}}
    try:
        with Image.open(path) as im:
            out["format"] = im.format
            text = (im.info or {}).get("text")
            if isinstance(text, dict):
                out["text"] = {k: str(v)[:2000] for k, v in text.items()}
            try:
                exif = im.getexif()
            except Exception:
                exif = None
            if exif:
                for tag, label in ((0x010E, "ImageDescription"), (0x0131, "Software"),
                                   (0x013B, "Artist"), (0x8298, "Copyright"), (0x9286, "UserComment")):
                    try:
                        value = exif.get(tag)
                    except Exception:
                        value = None
                    if value:
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", "ignore").strip("\x00")
                        out["exif"][label] = str(value)[:2000]
            out["icc"] = bool((im.info or {}).get("icc_profile"))
    except Exception as exc:
        out["error"] = str(exc)
    return out


def image_info(path):
    Image = _pillow()
    with Image.open(path) as im:
        dpi = im.info.get("dpi")
        info = {
            "width": im.width,
            "height": im.height,
            "mode": im.mode,
            "format": im.format,
            "bands": list(im.getbands()),
            "has_alpha": "A" in im.getbands() or (im.format == "PNG" and im.mode == "P" and "transparency" in im.info),
            "frames": getattr(im, "n_frames", 1),
            "bytes": file_size(path),
            "dpi": [round(float(dpi[0]), 2), round(float(dpi[1]), 2)] if dpi else None,
            "icc": bool(im.info.get("icc_profile")),
            "text_keys": sorted((im.info or {}).get("text", {}).keys()) if isinstance((im.info or {}).get("text"), dict) else [],
        }
    return info


def dhash(path_or_img, size=8):
    """64-bit difference hash for near-duplicate detection (returns 16 hex chars)."""
    Image = _pillow()
    img = path_or_img if hasattr(path_or_img, "convert") else Image.open(path_or_img)
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(small.getdata())
    bits = 0
    idx = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            if left > right:
                bits |= 1 << idx
            idx += 1
    return f"{bits:016x}"


def hamming_hex(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# --------------------------------------------------------------------------
# colour helpers
# --------------------------------------------------------------------------


def hex_to_rgb(value):
    value = str(value).lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise input_error(f"Invalid hex colour: {value}", "Use #rrggbb.")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(int(clamp(c, 0, 255)) for c in rgb[:3]))


def rgb_to_hsl(rgb):
    r, g, b = (c / 255.0 for c in rgb[:3])
    hi, lo = max(r, g, b), min(r, g, b)
    l = (hi + lo) / 2
    if hi == lo:
        return 0.0, 0.0, l
    d = hi - lo
    s = d / (2 - hi - lo) if l > 0.5 else d / (hi + lo)
    if hi == r:
        h = ((g - b) / d) % 6
    elif hi == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0, s, l


def hsl_to_rgb(h, s, l):
    h = h % 360
    s = clamp(s, 0, 1)
    l = clamp(l, 0, 1)

    def f(n):
        k = (n + h / 30.0) % 12
        a = s * min(l, 1 - l)
        return l - a * max(-1, min(k - 3, 9 - k, 1))

    return tuple(int(round(f(n) * 255)) for n in (0, 8, 4))


def shift_hue(rgb, degrees, sat=1.0, light=1.0):
    h, s, l = rgb_to_hsl(rgb)
    return hsl_to_rgb(h + degrees, clamp(s * sat, 0, 1), clamp(l * light, 0, 1))


def relative_luminance(rgb):
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb_a, rgb_b):
    la, lb = relative_luminance(rgb_a), relative_luminance(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def harmonize(seed_colors, count=5, strategy="analogous"):
    """Derive a working palette from seed colours using HSL relationships."""
    seeds = [hex_to_rgb(c) if isinstance(c, str) else tuple(c) for c in seed_colors] or [(40, 90, 160)]
    h, s, l = rgb_to_hsl(seeds[0])
    if strategy == "complementary":
        offsets = [0, 180, 12, 192, 0]
    elif strategy == "triadic":
        offsets = [0, 120, 240, 30, 200]
    elif strategy == "split":
        offsets = [0, 150, 210, 20, 170]
    elif strategy == "monochrome":
        offsets = [0, 0, 0, 0, 0]
    else:
        offsets = [0, 30, -30, 15, -15]
    out = []
    for i in range(count):
        off = offsets[i % len(offsets)]
        lt = clamp(l + (0.22 if i % 2 else -0.12) * (i / max(1, count - 1) + 0.3), 0.06, 0.94)
        col = hsl_to_rgb(h + off, clamp(s * (0.95 if i else 1.0), 0.05, 1.0), lt)
        if i == 0:
            col = seeds[0]
        out.append(rgb_to_hex(col))
    return out


# --------------------------------------------------------------------------
# typography
# --------------------------------------------------------------------------

_FONT_CACHE = {}


def font_search_dirs():
    dirs = []
    env = os.environ.get("IMAGEKIT_FONT_DIR")
    if env:
        dirs.extend(part for part in env.split(os.pathsep) if part)
    dirs.extend([
        "/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"),
        "/System/Library/Fonts", "/Library/Fonts",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    ])
    return [d for d in dirs if d and Path(d).is_dir()]


def _find_font_file(bold=False, serif=False, mono=False):
    """Locate a usable TTF/OTF by generic role. Returns None when nothing is found."""
    families = []
    if mono:
        families = ["DejaVuSansMono", "LiberationMono", "Consola", "CourierNew", "Courier"]
    elif serif:
        families = ["DejaVuSerif", "LiberationSerif", "TimesNewRoman", "Georgia", "Times"]
    else:
        families = ["DejaVuSans", "LiberationSans", "Arial", "Helvetica", "NotoSans", "Verdana"]
    if bold:
        families = [f + ("-Bold" if "-" not in f else "") for f in families] + families
    candidates = []
    for fam in families:
        for ext in (".ttf", ".otf", ".ttc"):
            candidates.append(fam + ext)
            candidates.append(fam.lower() + ext)
    for root in font_search_dirs():
        for cand in candidates:
            for hit in Path(root).rglob(cand):
                return str(hit)
    return None


def load_font(size, bold=False, serif=False, mono=False, cfg=None):
    """Load a scalable font at *size*, falling back to Pillow's bitmap font."""
    from PIL import ImageFont

    override = os.environ.get("IMAGEKIT_FONT")
    key = (int(size), bold, serif, mono, override)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = override if override and Path(override).is_file() else _find_font_file(bold, serif, mono)
    font = None
    if path:
        try:
            font = ImageFont.truetype(path, int(size))
        except OSError:
            font = None
    if font is None:
        font = ImageFont.load_default(size=max(8, int(size)))
    _FONT_CACHE[key] = font
    return font


def has_scalable_font():
    return _find_font_file() is not None


def text_size(draw, text, font):
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=4)
    return box[2] - box[0], box[3] - box[1], box


def wrap_text(draw, text, font, max_width):
    words = str(text).split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        trial = current + " " + word
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_font_size(draw, text, max_width, max_height, start=96, floor=10, bold=False, serif=False):
    """Largest font size at which wrapped text fits the box."""
    size = int(start)
    while size > floor:
        font = load_font(size, bold=bold, serif=serif)
        lines = wrap_text(draw, text, font, max_width)
        w, h, _ = text_size(draw, "\n".join(lines), font)
        if w <= max_width and h * 1.25 <= max_height:
            return font, lines
        size = int(size * 0.92)
    font = load_font(floor, bold=bold, serif=serif)
    return font, wrap_text(draw, text, font, max_width)


# --------------------------------------------------------------------------
# text analysis
# --------------------------------------------------------------------------

STOPWORDS = set("""a an and are as at be been being but by can could did do does for from had has have
how i if in into is it its it's just like make me more most my no not of on or our out over should so some
such than that the their them then there these they this to too up use used using very was we were what when
where which while who will with would you your create created create a image images picture photo please want
need show showing get got also""".split())

MOOD_WORDS = {
    "calm": ["calm", "serene", "quiet", "peaceful", "soft", "gentle", "tranquil"],
    "energetic": ["energetic", "bold", "dynamic", "vibrant", "loud", "punchy", "exciting"],
    "premium": ["premium", "luxury", "luxurious", "elegant", "refined", "upscale", "high-end"],
    "warm": ["warm", "cozy", "friendly", "inviting", "sunny", "nostalgic"],
    "clinical": ["clean", "clinical", "minimal", "technical", "precise", "modern"],
    "dramatic": ["dramatic", "dark", "moody", "cinematic", "tense", "mysterious"],
    "playful": ["playful", "fun", "whimsical", "cute", "quirky", "bright"],
}

SUBJECT_WORDS = {
    "person": ["person", "people", "woman", "man", "child", "portrait", "model", "athlete", "worker",
               "chef", "family", "crowd", "audience", "founder", "employee", "customers"],
    "product": ["product", "bottle", "package", "packaging", "device", "phone", "laptop", "shoe",
                "sneaker", "watch", "cosmetic", "serum", "can", "jar", "jar", "food", "coffee", "drink"],
    "environment": ["landscape", "mountain", "forest", "beach", "city", "street", "interior", "room",
                    "office", "kitchen", "desert", "island", "river", "valley", "sky", "field"],
    "architecture": ["building", "architecture", "house", "facade", "tower", "bridge", "museum",
                     "interior", "exterior", "floorplan", "apartment"],
    "creature": ["creature", "monster", "dragon", "animal", "bird", "insect", "beast", "mascot", "robot"],
    "abstract": ["abstract", "pattern", "texture", "geometric", "gradient", "shape", "material", "fluid"],
    "data": ["chart", "graph", "diagram", "infographic", "map", "schematic", "figure", "labelled", "annotated"],
}

FRAMING_WORDS = {
    "close-up": ["close-up", "closeup", "macro", "tight crop", "portrait crop"],
    "wide": ["wide", "establishing", "panorama", "wide-angle", "landscape orientation"],
    "medium": ["medium shot", "three-quarter", "mid shot"],
    "top-down": ["top-down", "flat lay", "overhead", "bird's eye", "aerial", "drone"],
    "full": ["full body", "full-length", "whole product", "full view"],
}

LIGHTING_WORDS = {
    "natural-daylight": ["natural light", "daylight", "sunlight", "golden hour", "overcast"],
    "studio-soft": ["softbox", "studio light", "soft light", "diffused", "beauty dish", "studio",
                    "seamless backdrop", "product photo"],
    "dramatic": ["dramatic", "hard light", "chiaroscuro", "rim light", "moody light", "noir"],
    "neon": ["neon", "night", "artificial color light", "cyberpunk glow"],
    "backlit": ["backlit", "silhouette", "against the light", "sun flare"],
    "flat": ["flat", "even light", "no shadows", "diagram light", "technical illustration"],
}


def tokenize(text):
    return re.findall(r"[a-z0-9][a-z0-9+#./-]*", str(text).lower())


def keywords(text, limit=8):
    counts = {}
    for tok in tokenize(text):
        tok = tok.strip("-./")
        if len(tok) < 3 or tok in STOPWORDS or tok.isdigit():
            continue
        counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [word for word, _ in ranked[:limit]]


def detect(text, lexicon):
    """First lexicon label whose trigger words appear in *text*."""
    low = f" {str(text).lower()} "
    for label, words in lexicon.items():
        for word in words:
            if f" {word}" in low or f"{word} " in low or f"{word}," in low:
                return label
    return None


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", str(text).strip())
    return [p.strip() for p in parts if p.strip()]


def estimate_reading(text, words_per_minute=200):
    n = len(str(text).split())
    return {"words": n, "seconds": round(n / words_per_minute * 60, 1)}


# --------------------------------------------------------------------------
# policy engine (safety, rights, approval triggers)
# --------------------------------------------------------------------------

POLICY_CACHE = {}


def load_policy(cfg=None):
    if cfg is None:
        cfg = load_config()
    path = cfg.policy_path()
    key = str(path)
    if key not in POLICY_CACHE:
        POLICY_CACHE[key] = read_json(path, required=True)
    return POLICY_CACHE[key]


def scan_text(text, policy=None):
    """Deterministic lexical risk scan. Returns findings with severity and gates."""
    policy = policy or load_policy()
    haystack = " " + re.sub(r"\s+", " ", str(text).lower()) + " "
    findings = []
    for rule in policy.get("text_rules", []):
        hits = []
        for term in rule.get("terms", []):
            term_l = term.lower()
            if " " in term_l:
                if term_l in haystack:
                    hits.append(term)
            elif re.search(rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])", haystack):
                hits.append(term)
        if hits:
            findings.append({
                "id": rule.get("id"),
                "category": rule.get("category"),
                "severity": rule.get("severity", "review"),
                "action": rule.get("action", "review"),
                "matched_terms": sorted(set(hits)),
                "message": rule.get("message", ""),
                "gates": rule.get("gates", []),
            })
    order = {"block": 0, "approval": 1, "disclose": 2, "review": 3, "info": 4}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings


def risk_summary(findings):
    severities = [f["severity"] for f in findings]
    gates = sorted({g for f in findings for g in f.get("gates", [])})
    if "block" in severities:
        level = "blocked"
    elif "approval" in severities:
        level = "approval-required"
    elif "disclose" in severities:
        level = "disclosure-required"
    elif "review" in severities:
        level = "review"
    else:
        level = "clear"
    return {"level": level, "gates": gates,
            "counts": {sev: severities.count(sev) for sev in ("block", "approval", "disclose", "review", "info")}}


def enforce_policy(ctx, text, stage, policy=None):
    """Raise when the policy blocks; collect disclosure + approval needs on the context."""
    policy = policy or load_policy(ctx.config)
    findings = scan_text(text, policy)
    summary = risk_summary(findings)
    if summary["level"] == "blocked":
        blocked = [f for f in findings if f["severity"] == "block"]
        raise policy_error(
            "Request blocked by image safety policy at %s" % stage,
            "; ".join(f["message"] for f in blocked) or "Rewrite the request without the prohibited element.",
            {"findings": blocked, "stage": stage},
        )
    if summary["level"] == "approval-required":
        ctx.require_approval(
            f"{stage} policy review",
            "This request matches approval-gated policy categories (consent, likeness, trademark, deception).",
            {"findings": findings, "stage": stage},
        )
    disclosures = []
    for finding in findings:
        if finding["severity"] == "disclose":
            template = policy.get("disclosures", {}).get(finding["category"])
            if template:
                disclosures.append({"category": finding["category"], "text": template})
    return {"findings": findings, "summary": summary, "disclosures": disclosures}


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------


def dpi_default(cfg, use="web"):
    presets = (cfg.formats or {}).get("dpi_presets", {})
    return presets.get(use, presets.get("web", 72))


def aspect_label(w, h):
    known = {(1, 1): "1:1", (4, 3): "4:3", (3, 4): "3:4", (16, 9): "16:9", (9, 16): "9:16",
             (3, 2): "3:2", (2, 3): "2:3", (4, 5): "4:5", (5, 4): "5:4", (21, 9): "21:9",
             (2, 1): "2:1", (1, 2): "1:2", (5, 3): "5:3"}
    g = math.gcd(int(w), int(h)) or 1
    return known.get((int(w) // g, int(h) // g), f"{int(w)}:{int(h)}")


def parse_size(text, default=(1024, 1024)):
    if text in (None, ""):
        return default
    if isinstance(text, (list, tuple)) and len(text) == 2:
        return (int(text[0]), int(text[1]))
    m = re.match(r"^\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*$", str(text))
    if not m:
        raise usage_error(f"Invalid size: {text}", "Use WIDTHxHEIGHT, for example 1024x1024.")
    return (int(m.group(1)), int(m.group(2)))


def check_size(cfg, size):
    w, h = size
    max_dim = cfg.limit("max_output_dimension", 8192)
    if w < 8 or h < 8:
        raise usage_error("Output dimensions must be at least 8x8 pixels")
    if w > max_dim or h > max_dim:
        raise usage_error(f"Output dimension {w}x{h} exceeds limits.max_output_dimension ({max_dim})",
                          "Reduce the size or raise the limit in your config.")
    if w * h > cfg.limit("max_pixels", 80_000_000):
        raise usage_error("Requested pixel count exceeds limits.max_pixels")
    return (w, h)


def seeded_rng(seed, salt=""):
    import random
    h = hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def derive_seed(seed, salt=""):
    h = hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()
    return int(h[:8], 16)


# --------------------------------------------------------------------------
# raster primitives shared by render / edit / qa
# --------------------------------------------------------------------------


def gradient_image(size, stops, direction="vertical", radial_center=(0.5, 0.42)):
    """Linear or radial multi-stop gradient. stops = [(pos 0..1, (r,g,b)), ...]."""
    Image = _pillow()
    w, h = int(size[0]), int(size[1])
    stops = sorted(((float(p), tuple(int(c) for c in rgb)) for p, rgb in stops), key=lambda s: s[0])
    if not stops:
        stops = [(0.0, (0, 0, 0)), (1.0, (255, 255, 255))]

    def sample(t):
        t = clamp(t, 0.0, 1.0)
        prev = stops[0]
        for pos, rgb in stops:
            if t <= pos:
                span = max(1e-6, pos - prev[0])
                f = (t - prev[0]) / span
                return tuple(int(round(lerp(prev[1][i], rgb[i], f))) for i in range(3))
            prev = (pos, rgb)
        return stops[-1][1]

    if direction == "radial":
        small = Image.new("RGB", (96, 96))
        px = small.load()
        cx, cy = radial_center[0] * 95, radial_center[1] * 95
        maxd = math.hypot(max(cx, 95 - cx), max(cy, 95 - cy))
        for y in range(96):
            for x in range(96):
                px[x, y] = sample(min(1.0, math.hypot(x - cx, y - cy) / maxd))
        return small.resize((w, h), Image.Resampling.BICUBIC)

    line = Image.new("RGB", (1, 256))
    lpx = line.load()
    for y in range(256):
        lpx[0, y] = sample(y / 255.0)
    if direction == "vertical":
        return line.resize((w, h), Image.Resampling.BILINEAR)
    horizontal = line.rotate(-90, expand=True).resize((w, h), Image.Resampling.BILINEAR)
    if direction == "diagonal":
        base = line.resize((max(w, h), max(w, h)), Image.Resampling.BILINEAR)
        rot = base.rotate(35, resample=Image.Resampling.BICUBIC, expand=False)
        return rot.crop((0, 0, w, h))
    return horizontal


def noise_image(size, rng, scale=6, octaves=3, blur=0.0, contrast=1.0):
    """Smooth value-noise field as an L image. scale = base grid cells across the width."""
    Image = _pillow()
    w, h = int(size[0]), int(size[1])
    acc = None
    for octave in range(max(1, octaves)):
        grid_w = max(2, int(scale * (2 ** octave)))
        grid_h = max(2, int(grid_w * h / max(1, w)))
        raw = bytes(rng.randrange(256) for _ in range(grid_w * grid_h))
        layer = Image.frombytes("L", (grid_w, grid_h), raw).resize((w, h), Image.Resampling.BICUBIC)
        acc = layer if acc is None else Image.blend(acc, layer, 0.5)
    if blur:
        from PIL import ImageFilter
        acc = acc.filter(ImageFilter.GaussianBlur(blur))
    if contrast != 1.0:
        acc = acc.point(lambda v: clamp(int((v - 128) * contrast + 128), 0, 255))
    return acc


def apply_mask_color(size, mask, color, opacity=1.0):
    """Solid colour layer clipped to an L mask."""
    Image = _pillow()
    layer = Image.new("RGB", size, tuple(int(c) for c in color[:3]))
    if opacity < 1.0:
        mask = mask.point(lambda v: int(v * opacity))
    return layer, mask


def vignette_mask(size, strength=0.3, softness=1.0):
    """L mask: 255 at edges (to be darkened), 0 at centre."""
    Image = _pillow()
    w, h = int(size[0]), int(size[1])
    small = 128
    mask = Image.new("L", (small, small))
    px = mask.load()
    cx, cy = small / 2, small / 2
    maxd = math.hypot(cx, cy)
    for y in range(small):
        for x in range(small):
            d = math.hypot(x - cx, y - cy) / maxd
            v = clamp((d - 0.45) / max(0.05, 0.55 * softness), 0, 1) ** 1.4
            px[x, y] = int(255 * v * strength)
    return mask.resize((w, h), Image.Resampling.BICUBIC)


def soft_shadow(size, offset=(12, 18), blur=18, opacity=0.45, shape="ellipse", inset=0.08):
    """Pre-blurred RGBA shadow footprint for compositing under a subject."""
    from PIL import ImageFilter
    Image = _pillow()
    w, h = int(size[0]), int(size[1])
    layer = Image.new("L", (w, h), 0)
    from PIL import ImageDraw
    d = ImageDraw.Draw(layer)
    x0 = int(w * inset) + offset[0]
    y0 = int(h * inset) + offset[1]
    x1 = int(w * (1 - inset)) + offset[0]
    y1 = int(h * (1 - inset)) + offset[1]
    if shape == "rect":
        d.rectangle([x0, y0, x1, y1], fill=int(255 * opacity))
    elif shape == "soft-rect":
        d.rounded_rectangle([x0, y0, x1, y1], radius=max(4, int(min(w, h) * 0.06)), fill=int(255 * opacity))
    else:
        d.ellipse([x0, y0, x1, y1], fill=int(255 * opacity))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.putalpha(layer)
    return shadow


def apply_grain(img, amount=0.03, rng=None, mono=True):
    """Soft luminance grain that keeps the original alpha."""
    Image = _pillow()
    rng = rng or seeded_rng(0, "grain")
    base = img.convert("RGBA")
    w, h = base.size
    grid = noise_image((w, h), rng, scale=max(40, w // 8), octaves=2, contrast=1.6)
    grain = Image.merge("RGBA", (grid, grid, grid, Image.new("L", (w, h), int(255 * amount))))
    out = Image.alpha_composite(base, grain)
    return out.convert(img.mode) if img.mode != "RGBA" else out


def saliency_map(img, blur=8):
    """Cheap centre-weighted edge/presence saliency map (L, 0-255)."""
    from PIL import ImageFilter, ImageChops
    Image = _pillow()
    rgb = img.convert("RGB")
    small = rgb.resize((128, max(8, int(128 * rgb.height / max(1, rgb.width)))), Image.Resampling.BILINEAR)
    edges = small.convert("L").filter(ImageFilter.FIND_EDGES)
    center = vignette_mask(small.size, strength=1.0, softness=1.6)
    center = ImageChops.invert(center)
    weighted = ImageChops.multiply(edges, center)
    combined = Image.blend(edges, weighted, 0.55)
    return combined.filter(ImageFilter.GaussianBlur(blur)).resize(rgb.size, Image.Resampling.BICUBIC)


def laplacian_variance(img):
    from PIL import ImageFilter, ImageStat
    g = img.convert("L")
    lap = g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return round(ImageStat.Stat(lap).var[0], 3)


def rms_contrast(img):
    from PIL import ImageStat
    g = img.convert("L")
    return round(ImageStat.Stat(g).stddev[0] * 0.9 + 8, 3)


def channel_means(img):
    from PIL import ImageStat
    rgb = img.convert("RGB")
    return [round(v, 2) for v in ImageStat.Stat(rgb).mean]


def edge_density(img):
    from PIL import ImageFilter
    g = img.convert("L").resize((min(512, img.width), max(8, int(min(512, img.width) * img.height / max(1, img.width)))))
    edges = g.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    strong = sum(hist[48:])
    total = sum(hist) or 1
    return round(strong / total, 4)


def dominant_colors(img, count=6, quantize=96):
    """Quantised dominant palette with population share."""
    Image = _pillow()
    small = img.convert("RGB")
    small.thumbnail((quantize, quantize), Image.Resampling.LANCZOS)
    q = small.quantize(colors=max(2, min(32, count * 3)), method=Image.Quantize.MEDIANCUT)
    palette = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    out = []
    for cnt, idx in counts[: max(count * 2, count)]:
        rgb = tuple(palette[idx * 3: idx * 3 + 3])
        if len(rgb) < 3:
            continue
        out.append({"hex": rgb_to_hex(rgb), "rgb": list(rgb), "share": round(cnt / total, 4)})
    merged = {}
    for entry in out:
        key = tuple(v // 12 for v in entry["rgb"])
        if key in merged:
            merged[key]["share"] = round(merged[key]["share"] + entry["share"], 4)
        else:
            merged[key] = dict(entry)
    return sorted(merged.values(), key=lambda e: -e["share"])[:count]


def tone_stats(img):
    from PIL import ImageStat
    g = img.convert("L")
    stat = ImageStat.Stat(g)
    hist = g.histogram()
    total = sum(hist) or 1
    black = sum(hist[:5]) / total * 100
    white = sum(hist[-5:]) / total * 100
    mid = sum(hist[64:192]) / total * 100
    return {
        "mean": round(stat.mean[0], 2),
        "stddev": round(stat.stddev[0], 2),
        "black_clip_pct": round(black, 2),
        "white_clip_pct": round(white, 2),
        "midtone_pct": round(mid, 2),
        "dynamic_range": int(max(i for i, v in enumerate(hist) if v) - min(i for i, v in enumerate(hist) if v)),
    }


def skin_ratio(img):
    """Ratio of skin-tone-like pixels. Heuristic presence signal, not face detection."""
    Image = _pillow()
    small = img.convert("RGB")
    small.thumbnail((160, 160), Image.Resampling.BILINEAR)
    px = list(small.getdata())
    if not px:
        return 0.0
    hits = 0
    for r, g, b in px:
        if r > 95 and g > 40 and b > 20 and r > g + 12 and r > b + 18 and abs(r - g) > 8 and max(px[0]) > 0:
            hits += 1
    return round(hits / len(px), 4)


def ssim(a, b):
    """Structural similarity on grayscale using Pillow convolution (no SciPy)."""
    from PIL import ImageFilter, ImageStat, ImageChops
    Image = _pillow()
    ga = a.convert("L")
    gb = b.convert("L").resize(ga.size, Image.Resampling.LANCZOS)
    blur = lambda im: im.filter(ImageFilter.GaussianBlur(1.6))
    mu_a, mu_b = blur(ga), blur(gb)
    sa = ImageStat.Stat(mu_a).mean[0]
    sb = ImageStat.Stat(mu_b).mean[0]
    # local means via small blocks for variance terms
    size = 16
    pw, ph = ga.width // size, ga.height // size
    if pw < 2 or ph < 2:
        return 1.0 if ga.tobytes() == gb.tobytes() else 0.0
    pa = ga.resize((pw, ph), Image.Resampling.BOX)
    pb = gb.resize((pw, ph), Image.Resampling.BOX)
    va = ImageStat.Stat(pa).stddev[0] ** 2
    vb = ImageStat.Stat(pb).stddev[0] ** 2
    cov = ImageStat.Stat(ImageChops.multiply(pa, pb)).mean[0] - (ImageStat.Stat(pa).mean[0] * ImageStat.Stat(pb).mean[0])
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    num = (2 * sa * sb + c1) * (2 * cov + c2)
    den = (sa ** 2 + sb ** 2 + c1) * (va + vb + c2)
    return round(max(0.0, min(1.0, num / den if den else 0.0)), 4)


def psnr(a, b):
    from PIL import ImageChops, ImageStat
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB").resize(a.size))
    stat = ImageStat.Stat(diff)
    mse = sum(s ** 2 for s in stat.rms) / len(stat.rms)
    if mse <= 1e-9:
        return 99.0
    return round(10 * math.log10((255 ** 2) / mse), 2)


def alpha_bbox(img):
    if "A" not in img.getbands():
        return (0, 0, img.width, img.height)
    return img.getchannel("A").getbbox() or (0, 0, 0, 0)
