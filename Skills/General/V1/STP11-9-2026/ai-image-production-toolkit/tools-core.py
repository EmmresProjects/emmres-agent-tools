"""Shared primitives for the AI Image Production Toolkit.

Standard library only. Pillow and NumPy are detected at runtime and used when
present (see tools-requirements.txt); every capability that depends on them
degrades gracefully and reports the limitation instead of failing silently.

Module loading convention
--------------------------
Files in a Skill Tool folder are named with the ``tools-`` prefix, which is not
a valid Python identifier. Modules are therefore loaded by file path through
``importlib`` (see :func:`load_module`), keeping every internal reference
relative to the Skill Tool folder.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import time
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TOOLKIT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Base error carrying a stable machine code."""

    code = "error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class InvalidInput(ToolError):
    code = "invalid_input"


class BlockedIntegration(ToolError):
    """A required external service, credential or runtime is unavailable."""

    code = "blocked_integration"


class ApprovalRequired(ToolError):
    code = "approval_required"


# ---------------------------------------------------------------------------
# Assets and files
# ---------------------------------------------------------------------------

_ASSET_CACHE: Dict[str, Any] = {}


def asset_path(name: str) -> str:
    """Return the absolute path of a packaged asset inside the Skill Tool folder."""
    if not re.match(r"^[A-Za-z0-9._-]+$", name):
        raise InvalidInput(f"invalid asset name: {name!r}")
    path = os.path.join(TOOLKIT_DIR, name)
    if not os.path.isfile(path):
        raise InvalidInput(f"asset not found: {name}")
    return path


def load_asset(name: str) -> Any:
    """Load and cache a packaged JSON asset."""
    if name not in _ASSET_CACHE:
        with open(asset_path(name), "r", encoding="utf-8") as fh:
            _ASSET_CACHE[name] = json.load(fh)
    return _ASSET_CACHE[name]


def rel_path(path: str) -> str:
    """Render *path* relative to the current working directory when possible."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.getcwd())
    except ValueError:
        return os.path.abspath(path)
    if rel.startswith(".."):
        return os.path.abspath(path)
    return rel.replace(os.sep, "/")


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_text(path: str, text: str) -> str:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def write_json(path: str, payload: Any) -> str:
    return write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: str, max_bytes: int = 64 * 1024 * 1024) -> Any:
    if not os.path.isfile(path):
        raise InvalidInput(f"file not found: {path}")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise InvalidInput(f"file too large ({size} bytes): {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"invalid JSON in {path}: {exc}") from exc


def read_text(path: str, max_bytes: int = 8 * 1024 * 1024) -> str:
    if not os.path.isfile(path):
        raise InvalidInput(f"file not found: {path}")
    if os.path.getsize(path) > max_bytes:
        raise InvalidInput(f"file too large: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(obj: Any, length: int = 12) -> str:
    """Deterministic short hash of any JSON-serialisable object."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:length]


def deterministic_seed(text: str) -> int:
    """Map text to a reproducible 32-bit seed (same input always gives same seed)."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Module loading (tools- prefixed filenames are not importable by name)
# ---------------------------------------------------------------------------

_MODULE_CACHE: Dict[str, Any] = {}


def load_module(stem: str) -> Any:
    """Load a sibling ``tools-<stem>.py`` module by file path."""
    if stem in _MODULE_CACHE:
        return _MODULE_CACHE[stem]
    path = os.path.join(TOOLKIT_DIR, f"tools-{stem}.py")
    if not os.path.isfile(path):
        raise InvalidInput(f"module not found: tools-{stem}.py")
    import importlib.util

    mod_name = f"image_toolkit_{stem.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise InvalidInput(f"cannot load module: tools-{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    # Also register the conventional ``tools_<stem>`` alias so that sibling
    # modules can use ``from tools_<stem> import ...`` style imports.
    alias = f"tools_{stem.replace('-', '_')}"
    sys.modules.setdefault(alias, module)
    spec.loader.exec_module(module)
    _MODULE_CACHE[stem] = module
    return module


# ---------------------------------------------------------------------------
# Optional third-party acceleration
# ---------------------------------------------------------------------------


def have_pillow() -> bool:
    try:
        import PIL.Image  # noqa: F401

        return True
    except Exception:
        return False


def have_numpy() -> bool:
    try:
        import numpy  # noqa: F401

        return True
    except Exception:
        return False


def pillow_version() -> Optional[str]:
    try:
        import PIL

        return getattr(PIL, "__version__", "unknown")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pure-python PNG codec (fallback when Pillow is unavailable)
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_write(path: str, width: int, height: int, pixels: bytes, alpha: bool = False) -> str:
    """Write 8-bit RGB(A) pixel data (row-major, 3 or 4 bytes per pixel) to a PNG."""
    channels = 4 if alpha else 3
    stride = width * channels
    if len(pixels) != stride * height:
        raise InvalidInput("pixel buffer size does not match image dimensions")
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    body = _PNG_SIG + png_chunk(b"IHDR", ihdr)
    body += png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    body += png_chunk(b"IEND", b"")
    ensure_parent(path)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


def png_write_text_chunks(path: str, texts: Dict[str, str]) -> str:
    """Append tEXt chunks to an existing PNG file (provenance sidecar embed)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(_PNG_SIG):
        raise InvalidInput("not a PNG file")
    end = data.rfind(b"\x00\x00\x00\x00IEND")
    if end < 0:
        raise InvalidInput("PNG is missing its IEND chunk")
    insert_at = end - 4
    extra = b""
    for key, value in texts.items():
        extra += png_chunk(b"tEXt", key.encode("latin-1", "replace") + b"\x00" + value.encode("latin-1", "replace"))
    out = data[:insert_at] + extra + data[insert_at:]
    with open(path, "wb") as fh:
        fh.write(out)
    return path


def _png_unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    out = bytearray(stride * height)
    pos = 0
    prev = bytearray(stride)
    for y in range(height):
        if pos >= len(raw):
            raise InvalidInput("truncated PNG data")
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) != stride:
            raise InvalidInput("truncated PNG scanline")
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        else:
            raise InvalidInput(f"unsupported PNG filter type {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def png_read(path: str) -> Dict[str, Any]:
    """Minimal PNG reader: returns width, height, channels and raw pixel bytes."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(_PNG_SIG):
        raise InvalidInput("not a PNG file")
    pos = len(_PNG_SIG)
    width = height = depth = ctype = None
    idat = bytearray()
    palette = b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if width is None:
        raise InvalidInput("PNG missing IHDR")
    if depth != 8:
        raise InvalidInput(f"pure-python PNG reader supports 8-bit only (got {depth}-bit)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise InvalidInput(f"unsupported PNG colour type {ctype}")
    raw = zlib.decompress(bytes(idat))
    bpp = channels
    flat = _png_unfilter(raw, width, height, bpp)
    if ctype == 3:
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            idx = flat[i] * 3
            rgb[i * 3:i * 3 + 3] = palette[idx:idx + 3]
        return {"width": width, "height": height, "channels": 3, "pixels": bytes(rgb)}
    if ctype == 0:
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            v = flat[i]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = v
        return {"width": width, "height": height, "channels": 3, "pixels": bytes(rgb)}
    if ctype == 4:
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            v = flat[i * 2]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = v
        return {"width": width, "height": height, "channels": 3, "pixels": bytes(rgb)}
    return {"width": width, "height": height, "channels": channels, "pixels": bytes(flat)}


# ---------------------------------------------------------------------------
# Image metadata probing (header level, no decoding required)
# ---------------------------------------------------------------------------


def _probe_png(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"format": "PNG"}
    pos = len(_PNG_SIG)
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body[:13])
            info.update({"width": w, "height": h, "bit_depth": depth, "color_type": ctype, "interlaced": bool(interlace)})
        elif kind == b"iCCP":
            info["icc_profile"] = True
        elif kind == b"pHYs":
            px, py, unit = struct.unpack(">IIB", body[:9])
            if unit == 1:
                info["dpi"] = [round(px * 0.0254, 2), round(py * 0.0254, 2)]
        elif kind == b"tEXt":
            key = body.split(b"\x00", 1)[0].decode("latin-1", "replace")
            info.setdefault("text_chunks", []).append(key)
        elif kind == b"eXIf":
            info["exif"] = True
        elif kind == b"IEND":
            break
    return info


def _probe_jpeg(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"format": "JPEG"}
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            prec, h, w, ncomp = struct.unpack(">BHHB", data[pos + 4:pos + 10])
            info.update({"width": w, "height": h, "bit_depth": prec, "components": ncomp,
                         "chroma_subsampling": "4:4:4" if ncomp == 3 and prec == 8 else "unknown"})
        elif marker == 0xE1:
            if data[pos + 4:pos + 9] == b"Exif\x00\x00":
                info["exif"] = True
        elif marker == 0xE2 and data[pos + 4:pos + 11] == b"ICC_PROFILE":
            info["icc_profile"] = True
        elif marker == 0xED:
            info["exif"] = True
        elif marker == 0xDA:
            break
        pos += 2 + seg_len
    return info


def _probe_webp(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"format": "WEBP"}
    if data[12:16] == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        info.update({"width": w, "height": h})
    elif data[12:16] == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        info.update({"width": (bits & 0x3FFF) + 1, "height": ((bits >> 14) & 0x3FFF) + 1})
    elif data[12:16] == b"VP8 ":
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        info.update({"width": w, "height": h})
    if b"ICCP" in data[:4096]:
        info["icc_profile"] = True
    if b"EXIF" in data[:8192]:
        info["exif"] = True
    return info


def _probe_gif(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"format": "GIF"}
    if len(data) >= 10:
        info.update({"width": struct.unpack("<H", data[6:8])[0], "height": struct.unpack("<H", data[8:10])[0]})
    return info


def _probe_tiff(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"format": "TIFF", "exif": True}
    if len(data) >= 8:
        endian = "<" if data[:2] == b"II" else ">"
        offset = struct.unpack(endian + "I", data[4:8])[0]
        if offset + 2 <= len(data):
            count = struct.unpack(endian + "H", data[offset:offset + 2])[0]
            for i in range(count):
                entry = offset + 2 + i * 12
                if entry + 12 > len(data):
                    break
                tag, ftype = struct.unpack(endian + "HH", data[entry:entry + 4])
                if tag in (256, 257):
                    vtype, vcount = struct.unpack(endian + "II", data[entry + 4:entry + 12])
                    if ftype == 3:
                        val = struct.unpack(endian + "H", data[entry + 8:entry + 10])[0]
                    else:
                        val = struct.unpack(endian + "I", data[entry + 8:entry + 12])[0]
                    info["width" if tag == 256 else "height"] = val
    return info


def _probe_bmp(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"format": "BMP"}
    if len(data) >= 26:
        info["width"] = struct.unpack("<i", data[18:22])[0]
        info["height"] = abs(struct.unpack("<i", data[22:26])[0])
    return info


def probe_image(path: str) -> Dict[str, Any]:
    """Probe an image file for format, dimensions and metadata (no full decode)."""
    if not os.path.isfile(path):
        raise InvalidInput(f"image not found: {path}")
    size = os.path.getsize(path)
    if size == 0:
        raise InvalidInput(f"image file is empty: {path}")
    with open(path, "rb") as fh:
        head = fh.read(65536)
    info: Dict[str, Any] = {}
    if head.startswith(_PNG_SIG):
        info = _probe_png(head)
    elif head[:2] == b"\xff\xd8":
        info = _probe_jpeg(head)
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        info = _probe_webp(head)
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        info = _probe_gif(head)
    elif head[:2] in (b"II", b"MM") and len(head) > 4:
        info = _probe_tiff(head)
    elif head[:2] == b"BM":
        info = _probe_bmp(head)
    else:
        raise InvalidInput(f"unrecognised image format: {path}")
    info.setdefault("width", None)
    info.setdefault("height", None)
    info["bytes"] = size
    info["path"] = path
    info["name"] = os.path.basename(path)
    info["extension"] = os.path.splitext(path)[1].lower().lstrip(".")
    info["mime"] = {
        "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp",
        "gif": "image/gif", "tiff": "image/tiff", "bmp": "image/bmp",
    }.get(info["extension"], "application/octet-stream")
    if info.get("width") and info.get("height"):
        info["aspect_ratio"] = round(info["width"] / info["height"], 5)
        info["megapixels"] = round(info["width"] * info["height"] / 1_000_000, 3)
    info["sha256"] = sha256_file(path)
    return info


def load_rgb(path: str) -> Dict[str, Any]:
    """Load an image as raw RGB bytes. Uses Pillow when available, PNG codec otherwise."""
    if have_pillow():
        from PIL import Image

        with Image.open(path) as im:
            im.load()
            rgba = im.convert("RGBA")
            return {"width": rgba.width, "height": rgba.height, "mode": "RGBA",
                    "pixels": rgba.tobytes(), "source": "pillow"}
    info = png_read(path)
    return {"width": info["width"], "height": info["height"], "mode": "RGB",
            "pixels": info["pixels"], "source": "stdlib-png"}


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if not re.match(r"^[0-9a-fA-F]{6}$", v):
        raise InvalidInput(f"invalid hex colour: {value!r}")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def rgb_to_hex(rgb: Sequence[int]) -> str:
    r, g, b = (max(0, min(255, int(c))) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(v: float) -> int:
    v = max(0.0, min(1.0, v))
    s = 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
    return int(round(s * 255))


def relative_luminance(rgb: Sequence[int]) -> float:
    r, g, b = (srgb_to_linear(float(c)) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Sequence[int], b: Sequence[int]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def rgb_to_lab(rgb: Sequence[int]) -> Tuple[float, float, float]:
    r, g, b = (srgb_to_linear(float(c)) for c in rgb[:3])
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab_delta_e(lab1: Sequence[float], lab2: Sequence[float]) -> float:
    return round(sum((a - b) ** 2 for a, b in zip(lab1, lab2)) ** 0.5, 2)


def nearest_palette_color(rgb: Sequence[int], palette: Sequence[Sequence[int]]) -> Tuple[int, float]:
    lab = rgb_to_lab(rgb)
    best_i, best_d = 0, 1e9
    for i, cand in enumerate(palette):
        d = lab_delta_e(lab, rgb_to_lab(cand))
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


def grayscale(rgb: Sequence[int]) -> int:
    r, g, b = rgb[:3]
    return int(round(0.2126 * r + 0.7152 * g + 0.0722 * b))


def hex_palette_to_rgb(palette: Sequence[str]) -> List[Tuple[int, int, int]]:
    return [hex_to_rgb(c) for c in palette]


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * (pct / 100.0)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return float(ordered[low])
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def slugify(text: str, max_len: int = 60) -> str:
    s = text.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len].strip("-")


def kebab(text: str) -> str:
    return slugify(text)


def truncate(text: str, limit: int = 120) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def md_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(out)


def to_title(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def wrap(text: str, width: int = 96) -> str:
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SVG output (pure standard library)
# ---------------------------------------------------------------------------


def svg_document(width: int, height: int, body: str, background: str = "#ffffff") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">\n'
        f'<rect width="{width}" height="{height}" fill="{background}"/>\n'
        f"{body}\n</svg>\n"
    )


def svg_text(x: float, y: float, text: str, size: int = 14, fill: str = "#111111",
             anchor: str = "start", weight: str = "normal", family: str = "DejaVu Sans, Verdana, sans-serif") -> str:
    safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{safe}</text>')


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sanitize_filename(name: str, max_len: int = 80) -> str:
    base = os.path.basename(name)
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")
    return base[:max_len] or "file"


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def looks_like_base64(text: str) -> bool:
    s = text.strip()
    if len(s) < 16 or len(s) % 4 != 0:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", s):
        return False
    try:
        base64.b64decode(s + "=" * (-len(s) % 4), validate=False)
        return True
    except (binascii.Error, ValueError):
        return False


def decode_maybe_base64(text: str) -> Optional[str]:
    s = text.strip()
    if not looks_like_base64(s):
        return None
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4))
        return raw.decode("utf-8", "replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Deterministic placeholder images
# ---------------------------------------------------------------------------

PLACEHOLDER_KINDS = ("image", "before", "after", "mask", "depth", "edges",
                     "segmentation", "matte", "pose", "reference", "background",
                     "exposure")


def placeholder_pixels(kind: str, width: int = 192, height: int = 144) -> Tuple[bytes, bool]:
    """Build deterministic placeholder pixel data for the given *kind*.

    Returns ``(pixels, has_alpha)`` where pixels are row-major RGB(A) bytes.
    """
    kind = (kind or "image").lower()
    rows = bytearray()
    cx, cy = width * 0.42, height * 0.55
    radius = min(width, height) * 0.22
    for y in range(height):
        for x in range(width):
            if kind in ("mask",):
                inside = abs(x - cx) < radius and abs(y - cy) < radius
                value = 255 if inside else 0
                rows += bytes((value, value, value))
            elif kind == "depth":
                value = int(255 * (1.0 - (y / max(1, height - 1))))
                rows += bytes((value, value, value))
            elif kind == "edges":
                on = (abs(x - cx) < radius) or (abs(y - cy) < radius)
                value = 255 if on else 0
                rows += bytes((value, value, value))
            elif kind == "segmentation":
                band = (x * 4 // max(1, width)) % 3
                rows += bytes(((60, 140, 200), (200, 160, 70), (90, 180, 120))[band])
            elif kind == "pose":
                on = (abs(x - cx) < 3 and abs(y - cy) < radius) or \
                     (abs(y - (cy - radius * 0.6)) < 3 and abs(x - cx) < radius * 0.5)
                value = 255 if on else 0
                rows += bytes((value, value, value))
            elif kind == "background":
                rows += bytes((238, 238, 242))
            else:
                base = (40, 60, 90) if kind in ("before", "mask", "pose", "edges", "depth") else (90, 60, 40)
                if kind == "after":
                    base = (150, 90, 60)
                elif kind == "exposure":
                    base = (200, 200, 205)
                elif kind in ("mask", "pose", "edges", "depth"):
                    base = (0, 0, 0)
                t = (x / max(1, width - 1)) * 0.5 + (y / max(1, height - 1)) * 0.5
                r = min(255, int(base[0] + 140 * t))
                g = min(255, int(base[1] + 110 * t))
                b = min(255, int(base[2] + 90 * (1 - t)))
                if kind not in ("mask", "pose", "edges", "depth"):
                    if abs(x - cx) < radius and abs(y - cy) < radius:
                        r, g, b = 235, 205, 120
                rows += bytes((r, g, b))
    return bytes(rows), kind == "matte"


def write_placeholder_image(path: str, kind: str = "image", width: int = 192,
                            height: int = 144) -> str:
    """Write a deterministic placeholder PNG for *kind* and return its path."""
    pixels, has_alpha = placeholder_pixels(kind, width, height)
    if kind == "matte":
        out = bytearray()
        for i in range(0, len(pixels), 3):
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            alpha = 255 if (r > 200 and g > 150) else 0
            out += bytes((r, g, b, alpha))
        pixels = bytes(out)
        has_alpha = True
    return png_write(path, width, height, pixels, alpha=has_alpha)
