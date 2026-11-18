"""Texture engine for the offline-3d-production Skill Tool.

Implements a complete PNG codec (zlib + struct from the standard library) plus
procedural PBR map synthesis, channel packing, normal-map derivation, texture
auditing, and variant generation.

Dependency policy
-----------------
PNG encode/decode is standard library only. Array maths uses **numpy**, which
is installed in the target environment and is also bundled with Blender's
Python, so the same module runs on a workstation and inside a DCC. When numpy
is missing, the texture commands fail with an actionable message instead of
silently degrading.

All generation is deterministic: every random source is a
``numpy.random.default_rng(seed)`` instance and no set iteration order reaches
the output.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import zlib

core = sys.modules["o3d_core"]

try:  # pragma: no cover - environment probe
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def require_numpy():
    if np is None:
        raise core.O3DError(
            "missing_dependency",
            "Texture operations need numpy, which is not installed in this Python.",
            "Run: python -m pip install 'numpy>=1.24'. Blender's bundled Python already includes numpy; "
            "use 'blender --background --python tools-o3d.py -- ...' if pip is unavailable.",
        )


MAX_PIXELS = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# PNG codec (standard library only)
# ---------------------------------------------------------------------------


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def png_decode(data):
    """Decode a PNG byte string into (width, height, channels, pixels, meta).

    ``pixels`` is a flat ``bytearray`` in row-major order with ``channels``
    interleaved 8-bit samples. Palette images are expanded to RGB.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise core.invalid("Not a PNG file (bad signature).", "Convert the texture to PNG, 8-bit.")
    pos = 8
    width = height = bitdepth = colortype = interlace = None
    idat = bytearray()
    palette = b""
    trns = b""
    meta = {}
    while pos + 8 <= len(data):
        length, ctype = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            palette = body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat.extend(body)
        elif ctype in (b"iCCP", b"sRGB", b"gAMA", b"cHRM"):
            meta[ctype.decode("ascii")] = True
        elif ctype == b"IEND":
            break
    if width is None:
        raise core.invalid("PNG is missing its IHDR chunk.")
    if width * height > MAX_PIXELS:
        raise core.limit_exceeded(
            "Texture is {0}x{1} ({2} pixels), above the {3} pixel ceiling.".format(
                width, height, width * height, MAX_PIXELS
            ),
            "Downscale the texture or raise MAX_PIXELS in tools-o3d-texture.py.",
        )
    if bitdepth != 8:
        raise core.invalid(
            "Only 8-bit PNGs are supported (found {0}-bit).".format(bitdepth),
            "Re-export the map at 8 bit/channel; 16-bit input must be downconverted by the DCC.",
        )
    if interlace:
        raise core.invalid(
            "Interlaced (Adam7) PNGs are not supported.",
            "Re-save with interlacing off.",
        )
    raw = zlib.decompress(bytes(idat))
    channels = PNG_CHANNELS[colortype]
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    src = 0
    for y in range(height):
        ftype = raw[src]
        src += 1
        line = bytearray(raw[src : src + stride])
        src += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ftype != 0:
            raise core.invalid("PNG uses unknown filter type {0}.".format(ftype))
        out[y * stride : (y + 1) * stride] = line
        prev = line
    if colortype == 3:
        if not palette:
            raise core.invalid("Palette PNG has no PLTE chunk.")
        expanded = bytearray(width * height * 3)
        for i in range(width * height):
            idx = out[i]
            expanded[i * 3 : i * 3 + 3] = palette[idx * 3 : idx * 3 + 3]
        out = expanded
        channels = 3
    meta.update(
        {
            "width": width,
            "height": height,
            "channels": channels,
            "colortype": colortype,
            "bitdepth": bitdepth,
            "has_alpha": channels in (2, 4),
        }
    )
    return width, height, channels, out, meta


def png_encode(width, height, channels, pixels):
    """Encode 8-bit samples into a PNG byte string (filter type 0 per row)."""
    if channels not in (1, 2, 3, 4):
        raise core.invalid("PNG channels must be 1, 2, 3 or 4 (got {0}).".format(channels))
    colortype = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    stride = width * channels
    expected = stride * height
    if len(pixels) != expected:
        raise core.invalid(
            "Pixel buffer is {0} bytes, expected {1}.".format(len(pixels), expected),
            "Check width x height x channels.",
        )
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride : (y + 1) * stride])
    out = bytearray(b"\x89PNG\r\n\x1a\n")

    def chunk(tag, body):
        out.extend(struct.pack(">I", len(body)))
        out.extend(tag)
        out.extend(body)
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        out.extend(struct.pack(">I", crc))

    chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colortype, 0, 0, 0))
    chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    chunk(b"IEND", b"")
    return bytes(out)


# ---------------------------------------------------------------------------
# Image container
# ---------------------------------------------------------------------------


class Image:
    """Linear 8-bit image backed by a numpy array of shape (H, W, C)."""

    def __init__(self, array):
        require_numpy()
        if not isinstance(array, np.ndarray):
            array = np.asarray(array)
        if array.dtype != np.uint8:
            array = np.clip(array, 0, 255).astype(np.uint8)
        if array.ndim == 2:
            array = array[:, :, None]
        self.array = array
        self.meta = {}

    @property
    def width(self):
        return int(self.array.shape[1])

    @property
    def height(self):
        return int(self.array.shape[0])

    @property
    def channels(self):
        return int(self.array.shape[2])

    def channels_of(self, index):
        return self.array[:, :, index]

    def as_float(self):
        return self.array.astype(np.float32) / 255.0

    def save(self, path):
        flat = bytearray(self.array.tobytes())
        data = png_encode(self.width, self.height, self.channels, flat)
        core.write_bytes_atomic(path, data)
        return path

    @classmethod
    def load(cls, path):
        data = core.read_bytes(path)
        width, height, channels, pixels, meta = png_decode(data)
        require_numpy()
        arr = np.frombuffer(bytes(pixels), dtype=np.uint8).reshape((height, width, channels))
        img = cls(arr.copy())
        img.meta = meta
        img.meta["sha256"] = core.sha256_bytes(data)
        img.meta["bytes"] = len(data)
        return img

    def copy(self):
        return Image(self.array.copy())

    def resize(self, width, height):
        """Deterministic bilinear resize (used for mip checks and previews)."""
        src = self.array.astype(np.float32)
        sh, sw = src.shape[0], src.shape[1]
        ys = (np.arange(height) + 0.5) * (sh / float(height)) - 0.5
        xs = (np.arange(width) + 0.5) * (sw / float(width)) - 0.5
        y0 = np.clip(np.floor(ys).astype(int), 0, sh - 1)
        x0 = np.clip(np.floor(xs).astype(int), 0, sw - 1)
        y1 = np.clip(y0 + 1, 0, sh - 1)
        x1 = np.clip(x0 + 1, 0, sw - 1)
        wy = np.clip(ys - y0, 0, 1)[:, None, None]
        wx = np.clip(xs - x0, 0, 1)[None, :, None]
        top = src[y0][:, x0] * (1 - wx) + src[y0][:, x1] * wx
        bot = src[y1][:, x0] * (1 - wx) + src[y1][:, x1] * wx
        out = top * (1 - wy) + bot * wy
        return Image(np.clip(out, 0, 255).astype(np.uint8))

    def to_rgb(self):
        if self.channels == 3:
            return self.copy()
        if self.channels == 1:
            arr = np.repeat(self.array, 3, axis=2)
            return Image(arr)
        if self.channels == 4:
            return Image(self.array[:, :, :3].copy())
        return Image(np.repeat(self.array[:, :, :1], 3, axis=2))

    def to_rgba(self):
        if self.channels == 4:
            return self.copy()
        alpha = np.full((self.height, self.width, 1), 255, dtype=np.uint8)
        return Image(np.concatenate([self.to_rgb().array, alpha], axis=2))


def blur(array, radius):
    """Separable box blur repeated three times as a cheap gaussian."""
    require_numpy()
    arr = array.astype(np.float32)
    if radius < 1:
        return arr
    for _ in range(3):
        arr = _box1d(arr, radius, axis=1)
        arr = _box1d(arr, radius, axis=0)
    return arr


def _box1d(arr, radius, axis=0):
    """Moving-average filter along one axis using a padded cumulative sum."""
    pad = [(0, 0)] * arr.ndim
    pad[axis] = (radius, radius)
    padded = np.pad(arr, pad, mode="edge")
    csum = np.cumsum(padded, axis=axis)
    zero_shape = list(csum.shape)
    zero_shape[axis] = 1
    csum = np.concatenate([np.zeros(zero_shape, dtype=csum.dtype), csum], axis=axis)
    n = arr.shape[axis]
    k = 2 * radius + 1
    if axis == 0:
        return (csum[k : k + n] - csum[0:n]) / k
    return (csum[:, k : k + n] - csum[:, 0:n]) / k


# ---------------------------------------------------------------------------
# Procedural generation
# ---------------------------------------------------------------------------


def value_noise(height, width, cells, rng):
    grid = rng.random((cells + 1, cells + 1)).astype(np.float32)
    ys = np.linspace(0, cells, height, endpoint=False)
    xs = np.linspace(0, cells, width, endpoint=False)
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    fy = (ys - y0)[:, None]
    fx = (xs - x0)[None, :]
    fy = fy * fy * (3 - 2 * fy)
    fx = fx * fx * (3 - 2 * fx)
    a = grid[np.ix_(y0, x0)]
    b = grid[np.ix_(y0, x0 + 1)]
    c = grid[np.ix_(y0 + 1, x0)]
    d = grid[np.ix_(y0 + 1, x0 + 1)]
    top = a * (1 - fx) + b * fx
    bot = c * (1 - fx) + d * fx
    return top * (1 - fy) + bot * fy


def fbm(height, width, octaves=5, base_cells=4, seed=0, persistence=0.5):
    require_numpy()
    rng = np.random.default_rng(seed)
    total = np.zeros((height, width), dtype=np.float32)
    amp, norm, cells = 1.0, 0.0, base_cells
    for _ in range(max(1, octaves)):
        total += value_noise(height, width, max(1, int(cells)), rng) * amp
        norm += amp
        amp *= persistence
        cells *= 2
    return total / max(norm, 1e-6)


def gradient_map(height, width, direction="radial"):
    ys = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    xs = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    if direction == "horizontal":
        return np.repeat(xs, height, axis=0)
    if direction == "vertical":
        return np.repeat(ys, width, axis=1)
    cy, cx = 0.5, 0.5
    dy = ys - cy
    dx = xs - cx
    r = np.sqrt(dy * dy + dx * dx) / 0.7071
    return np.clip(1.0 - r, 0, 1)


def checker_map(height, width, cells=8, colors=((210, 210, 210), (60, 60, 60))):
    require_numpy()
    ys = (np.arange(height) * cells // height)
    xs = (np.arange(width) * cells // width)
    grid = ((ys[:, None] + xs[None, :]) % 2).astype(np.uint8)
    c0 = np.array(colors[0], dtype=np.uint8)
    c1 = np.array(colors[1], dtype=np.uint8)
    arr = np.where(grid[:, :, None] == 0, c0, c1)
    return Image(arr.astype(np.uint8))


def uv_layout_map(height, width, tiles=1, margin_px=8, label_tiles=True):
    """Tech-art UV layout reference: UDIM tiles, U/D/V/R markers, 0-1 border."""
    require_numpy()
    img = np.full((height, width, 3), 40, dtype=np.uint8)
    tile_w = width // tiles
    for t in range(tiles):
        x0 = t * tile_w
        img[:, x0 : x0 + max(1, tile_w - 1)] = 0
        # 0-1 border
        img[margin_px : height - margin_px, x0 + margin_px : x0 + tile_w - margin_px] = 22
        # grid of 8
        for k in range(1, 8):
            gx = x0 + (tile_w * k) // 8
            if gx < x0 + tile_w:
                img[:, gx] = 70
            gy = (height * k) // 8
            img[gy, x0 : x0 + tile_w] = 70
        # tile markers: filled squares in tile corners encode orientation
        s = max(4, margin_px * 2)
        img[margin_px : margin_px + s, x0 + margin_px : x0 + margin_px + s] = (230, 60, 60)  # top-left red
        img[margin_px : margin_px + s, x0 + tile_w - margin_px - s : x0 + tile_w - margin_px] = (60, 230, 60)
        img[height - margin_px - s : height - margin_px, x0 + margin_px : x0 + margin_px + s] = (60, 60, 230)
    img[margin_px : height - margin_px, margin_px : max(margin_px + 1, width - margin_px)] = np.maximum(
        img[margin_px : height - margin_px, margin_px : max(margin_px + 1, width - margin_px)], 10
    )
    return Image(img)


def height_to_normal(height_arr, strength=1.0, wrap=True):
    """Convert a height field in [0,1] to a tangent-space normal map (OpenGL +Y)."""
    require_numpy()
    h = height_arr.astype(np.float32) * strength
    if wrap:
        dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5
        dy = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * 0.5
    else:
        gx, gy = np.gradient(h)
        dx, dy = gx, gy
    nx = -dx
    ny = dy
    nz = np.ones_like(h)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    arr = np.stack([nx * inv, ny * inv, nz * inv], axis=2)
    # Tangent space: OpenGL thumb rule -> +Y is up in the image (row 0 at top)
    arr[:, :, 1] = -arr[:, :, 1]
    return np.clip((arr * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)


def height_to_ao(height_arr, radius=4):
    require_numpy()
    h = height_arr.astype(np.float32)
    blurred = blur(h, radius)
    diff = np.clip(h - blurred, 0, 1)
    ao = 1.0 - np.clip(diff * 2.5, 0, 1)
    return np.clip(ao * 255.0, 0, 255).astype(np.uint8)


def generate_texture(kind, width, height, seed=0, channels=3, options=None):
    """Generate one procedural map. Used by ``texture-generate``."""
    require_numpy()
    options = options or {}
    rng = np.random.default_rng(seed)
    if kind == "checker":
        return checker_map(height, width, int(options.get("cells", 8)))
    if kind == "uv-layout":
        return uv_layout_map(height, width, int(options.get("tiles", 1)), int(options.get("margin", 8)))
    if kind == "gradient":
        g = gradient_map(height, width, options.get("direction", "radial"))
        return Image(np.stack([g, g, g], axis=2) * 255 if channels == 3 else g * 255)
    if kind == "noise":
        n = fbm(height, width, int(options.get("octaves", 5)), int(options.get("cells", 4)), seed,
                float(options.get("persistence", 0.5)))
        n = np.clip(n, 0, 1)
        if channels == 1:
            return Image(n * 255)
        tint = np.array(options.get("tint", [1.0, 1.0, 1.0]), dtype=np.float32)
        return Image(np.stack([n * tint[0], n * tint[1], n * tint[2]], axis=2) * 255)
    if kind == "height":
        n = fbm(height, width, int(options.get("octaves", 5)), int(options.get("cells", 4)), seed)
        return Image(np.clip(n, 0, 1) * 255)
    if kind == "normal":
        base = fbm(height, width, int(options.get("octaves", 4)), int(options.get("cells", 4)), seed)
        return Image(height_to_normal(base, float(options.get("strength", 2.0))))
    if kind == "ao":
        base = fbm(height, width, int(options.get("octaves", 4)), int(options.get("cells", 4)), seed)
        return Image(height_to_ao(base, int(options.get("radius", 4))))
    if kind == "roughness":
        base = fbm(height, width, int(options.get("octaves", 6)), int(options.get("cells", 8)), seed)
        lo, hi = options.get("range", [0.25, 0.85])
        val = lo + (hi - lo) * base
        return Image(val * 255)
    if kind == "metallic":
        mask = np.zeros((height, width), dtype=np.float32)
        m = options.get("mask", "stripes")
        if m == "stripes":
            xs = np.arange(width)
            mask[:, :] = ((xs // max(1, width // 16)) % 2)[None, :]
        elif m == "radial":
            mask = gradient_map(height, width, "radial")
        else:
            mask = (fbm(height, width, 3, 2, seed) > 0.5).astype(np.float32)
        return Image(np.clip(mask, 0, 1) * 255)
    if kind == "basecolor":
        base = fbm(height, width, int(options.get("octaves", 4)), int(options.get("cells", 3)), seed)
        c0 = np.array(options.get("color_a", [92, 120, 74]), dtype=np.float32)
        c1 = np.array(options.get("color_b", [166, 148, 104]), dtype=np.float32)
        mix = base[:, :, None]
        arr = c0[None, None, :] * (1 - mix) + c1[None, None, :] * mix
        if channels == 4:
            alpha = np.full((height, width, 1), 255, dtype=np.float32)
            arr = np.concatenate([arr, alpha], axis=2)
        return Image(arr)
    if kind == "mask":
        n = fbm(height, width, 3, int(options.get("cells", 6)), seed)
        mask = (n > float(options.get("threshold", 0.5))).astype(np.float32)
        return Image(mask * 255)
    raise core.invalid(
        "Unknown procedural texture kind '{0}'.".format(kind),
        "Kinds: checker, uv-layout, gradient, noise, height, normal, ao, roughness, metallic, basecolor, mask.",
    )


def pbr_set(width, height, seed=0, family="stone", name_prefix="pbr"):
    """Synthesise a coherent PBR set: basecolor, roughness, metallic, normal, AO, height."""
    require_numpy()
    presets = {
        "stone": {"cells": 3, "octaves": 6, "color_a": [104, 106, 108], "color_b": [150, 148, 142],
                  "rough": [0.55, 0.92], "metallic": 0.0, "strength": 2.4},
        "metal": {"cells": 6, "octaves": 5, "color_a": [122, 126, 132], "color_b": [168, 172, 178],
                  "rough": [0.18, 0.55], "metallic": 1.0, "strength": 1.2},
        "fabric": {"cells": 10, "octaves": 7, "color_a": [58, 70, 96], "color_b": [92, 108, 140],
                   "rough": [0.72, 0.98], "metallic": 0.0, "strength": 1.6},
        "skin": {"cells": 2, "octaves": 6, "color_a": [168, 122, 104], "color_b": [214, 168, 148],
                 "rough": [0.38, 0.62], "metallic": 0.0, "strength": 0.9},
        "wood": {"cells": 4, "octaves": 5, "color_a": [96, 62, 32], "color_b": [158, 112, 66],
                 "rough": [0.45, 0.8], "metallic": 0.0, "strength": 2.0},
    }
    if family not in presets:
        raise core.invalid(
            "Unknown PBR family '{0}'.".format(family),
            "Families: " + ", ".join(sorted(presets)),
        )
    p = presets[family]
    rng = np.random.default_rng(seed)
    height_arr = fbm(height, width, p["octaves"], p["cells"], seed)
    # anisotropic stretch for wood/fabric grain
    if family == "wood":
        stretch = np.repeat(height_arr[:, : max(1, width // 8)], 8, axis=1)
        height_arr = (height_arr * 0.4 + stretch[:, :width] * 0.6)
    if family == "fabric":
        xs = np.arange(width)[None, :]
        ys = np.arange(height)[:, None]
        weave = 0.5 + 0.5 * np.sin(xs * math.pi / 2.0) * np.sin(ys * math.pi / 2.0)
        height_arr = height_arr * 0.6 + weave * 0.4
    height_arr = np.clip((height_arr - height_arr.min()) / max(1e-6, height_arr.max() - height_arr.min()), 0, 1)
    c0 = np.array(p["color_a"], dtype=np.float32)
    c1 = np.array(p["color_b"], dtype=np.float32)
    mix = height_arr[:, :, None]
    base = c0[None, None, :] * (1 - mix) + c1[None, None, :] * mix
    grain = rng.normal(0, 5.0, size=(height, width, 1)).astype(np.float32)
    base = np.clip(base + grain, 0, 255).astype(np.uint8)
    rough_lo, rough_hi = p["rough"]
    rough = (rough_lo + (rough_hi - rough_lo) * np.clip(height_arr * 0.85 + grain[:, :, 0] / 255.0 * 0.15, 0, 1))
    rough_arr = np.clip(rough * 255, 0, 255).astype(np.uint8)
    metal_arr = np.full((height, width), int(round(p["metallic"] * 255)), dtype=np.uint8)
    normal_arr = height_to_normal(height_arr, p["strength"])
    ao_arr = height_to_ao(height_arr, max(2, min(width, height) // 128))
    set_images = {
        "basecolor": Image(base),
        "roughness": Image(rough_arr),
        "metallic": Image(metal_arr),
        "normal": Image(normal_arr),
        "ao": Image(ao_arr),
        "height": Image(np.clip(height_arr * 255, 0, 255).astype(np.uint8)),
    }
    return {"family": family, "seed": seed, "images": set_images, "name_prefix": name_prefix}


# ---------------------------------------------------------------------------
# Texture audit
# ---------------------------------------------------------------------------


def is_power_of_two(n):
    return n > 0 and (n & (n - 1)) == 0


def audit_texture(path, role=None, expected_size=None, padding_px=8):
    """Inspect one texture and return structured findings + severity list."""
    img = Image.load(path)
    arr = img.array
    findings = []

    def add(severity, code, message, hint=None):
        findings.append({"severity": severity, "code": code, "message": message, "hint": hint})

    if not (is_power_of_two(img.width) and is_power_of_two(img.height)):
        add("warning", "non_power_of_two",
            "{0}x{1} is not power-of-two.".format(img.width, img.height),
            "Use POT sizes (1024, 2048, 4096) for mipmapped real-time targets.")
    if expected_size and (img.width, img.height) != tuple(expected_size):
        add("error", "size_mismatch",
            "Expected {0}x{1}, found {2}x{3}.".format(expected_size[0], expected_size[1], img.width, img.height),
            "Re-export at the project texture budget.")
    stats = {}
    for c in range(img.channels):
        chan = arr[:, :, c].astype(np.float32)
        mean = float(chan.mean())
        std = float(chan.std())
        uniq = int(np.unique(chan.astype(np.uint8)).size)
        stats["channel_{0}".format(c)] = {
            "min": float(chan.min()), "max": float(chan.max()),
            "mean": round(mean, 3), "std": round(std, 3), "unique_values": uniq,
        }
        if std < 0.5:
            add("info", "flat_channel",
                "Channel {0} is flat (std={1:.2f}).".format(c, std),
                "Confirm the channel is intentionally unused; drop it to save memory if so.")
        if uniq <= 4 and std >= 0.5:
            add("warning", "low_bit_depth_content",
                "Channel {0} has only {1} distinct values; the map is quantised.".format(c, uniq),
                "Raise the export bit depth or remove a posterising modifier.")
    banding = detect_banding(arr[:, :, 0])
    if banding["banding_rows"] > arr.shape[0] * 0.25:
        add("warning", "banding",
            "Detected vertical gradient banding in {0} rows.".format(banding["banding_rows"]),
            "Add subtle dither/noise before quantising to 8-bit.")
    if role == "normal":
        n = arr[:, :, :3].astype(np.float32) / 255.0 * 2.0 - 1.0
        if img.channels < 3:
            add("error", "normal_not_rgb", "Normal map must have 3 channels.", "Re-export as RGB.")
        else:
            z_mean = float(n[:, :, 2].mean())
            y_mean = float(n[:, :, 1].mean())
            if z_mean < 0.2:
                add("error", "normal_invalid_z",
                    "Normal map mean Z is {0:.2f}; expected near 1.0.".format(z_mean),
                    "Re-bake with the correct tangent basis, or flip the green channel.")
            if abs(y_mean) > 0.25:
                add("warning", "normal_green_convention",
                    "Green channel is off-centre ({0:.2f}); likely a DirectX/OpenGL mix-up.".format(y_mean),
                    "OpenGL (+Y up) expects mean green ~0.5 after remap; invert green for DirectX targets.")
    if role in ("basecolor", "albedo"):
        if img.channels >= 3:
            lum = arr[:, :, :3].astype(np.float32).mean(axis=2)
            spec = float((lum > 250).mean())
            if spec > 0.02:
                add("warning", "baked_specular",
                    "{0:.1%} of the base colour is near-white; specular may be baked in.".format(spec),
                    "Base colour should not contain lighting highlights above the reference value.")
    if role in ("opacity", "alpha") and img.channels not in (2, 4):
        add("error", "missing_alpha", "Opacity role requested but the image has no alpha channel.",
            "Export RGBA or use a dedicated mask.")
    edge = edge_padding_report(arr, padding_px)
    if edge["uv_seam_risk"]:
        add("warning", "seam_padding",
            "Outer {0}px border does not match interior statistics; dilation may be missing.".format(padding_px),
            "Bake with >= {0}px dilation and verify mip bleeding.".format(padding_px))
    if img.meta.get("iCCP") or img.meta.get("sRGB"):
        add("info", "embedded_color_profile", "PNG carries an embedded colour profile chunk.",
            "Confirm it matches the project colour pipeline (ACEScg/linear vs sRGB).")
    return {
        "path": os.path.basename(path),
        "role": role,
        "width": img.width,
        "height": img.height,
        "channels": img.channels,
        "has_alpha": img.channels in (2, 4),
        "bytes": img.meta.get("bytes"),
        "sha256": img.meta.get("sha256"),
        "power_of_two": is_power_of_two(img.width) and is_power_of_two(img.height),
        "channel_stats": stats,
        "banding": banding,
        "edge": edge,
        "findings": findings,
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
    }


def detect_banding(channel, threshold=0.02):
    """Count rows whose adjacent-pixel step is a repeated constant (posterised ramp)."""
    require_numpy()
    arr = channel.astype(np.float32)
    diffs = np.abs(np.diff(arr, axis=1))
    rows = (diffs.mean(axis=1) < threshold).sum()
    return {"banding_rows": int(rows)}


def edge_padding_report(arr, padding_px):
    require_numpy()
    if arr.shape[0] <= padding_px * 2 or arr.shape[1] <= padding_px * 2:
        return {"uv_seam_risk": False, "note": "image too small for padding check"}
    border = np.concatenate(
        [
            arr[:padding_px, :, :].reshape(-1, arr.shape[2]),
            arr[-padding_px:, :, :].reshape(-1, arr.shape[2]),
            arr[:, :padding_px, :].reshape(-1, arr.shape[2]),
            arr[:, -padding_px:, :].reshape(-1, arr.shape[2]),
        ]
    ).astype(np.float32)
    interior = arr[padding_px:-padding_px, padding_px:-padding_px, :].reshape(-1, arr.shape[2]).astype(np.float32)
    b_mean = border.mean(axis=0)
    i_mean = interior.mean(axis=0)
    delta = float(np.abs(b_mean - i_mean).max() / 255.0)
    return {
        "padding_px": padding_px,
        "border_mean": [round(float(x), 2) for x in b_mean],
        "interior_mean": [round(float(x), 2) for x in i_mean],
        "max_channel_delta": round(delta, 4),
        "uv_seam_risk": delta > 0.25,
    }


# ---------------------------------------------------------------------------
# Channel packing / variants
# ---------------------------------------------------------------------------


def pack_channels(sources, layout, out_channels=3):
    """Pack grayscale PNGs into an RGB(A) texture.

    ``layout`` maps an output channel letter to a source spec:
    ``"ao:r"`` means the red channel of the AO file, ``"roughness"`` means the
    luminance/first channel. Letters: R,G,B,A.
    """
    require_numpy()
    size = None
    arrays = {}
    for letter, spec in layout.items():
        if ":" in spec:
            filename, chan = spec.split(":", 1)
            cidx = {"r": 0, "g": 1, "b": 2, "a": 3}[chan.lower()]
        else:
            filename, cidx = spec, 0
        if filename not in sources:
            raise core.invalid(
                "Packing layout references unknown source '{0}'.".format(filename),
                "Provide every referenced file with --source name=path.",
            )
        img = Image.load(sources[filename])
        if size is None:
            size = (img.height, img.width)
        elif (img.height, img.width) != size:
            raise core.invalid(
                "All packed maps must share one resolution ({0} references {1}x{2}).".format(
                    filename, img.width, img.height),
                "Resample the sources to a common size first.",
            )
        arr = img.array
        arrays[letter.upper()] = arr[:, :, cidx] if cidx < img.channels else arr[:, :, 0]
    order = "RGBA"[:out_channels]
    missing = [c for c in order if c not in arrays]
    for c in missing:
        arrays[c] = arrays.get("R", np.zeros(size, dtype=np.uint8))
        if c == "A":
            arrays[c] = np.full(size, 255, dtype=np.uint8)
    stacked = np.stack([arrays[c] for c in order], axis=2).astype(np.uint8)
    return Image(stacked)


def make_variant(img, mode="worn", amount=0.5, seed=0):
    """Derive a controlled texture variant from a source image."""
    require_numpy()
    rng = np.random.default_rng(seed)
    arr = img.array.astype(np.float32)
    if mode == "worn":
        noise = fbm(img.height, img.width, 5, 4, seed)[:, :, None]
        mask = np.clip((noise - (0.5 - amount * 0.2)) * 3.0, 0, 1)
        arr = arr * (1 - 0.45 * amount * mask) + 40 * amount * mask
    elif mode == "wet":
        arr = arr * (1.0 - 0.22 * amount)
        sheen = fbm(img.height, img.width, 3, 2, seed + 1)[:, :, None]
        arr = arr + 18 * amount * sheen
    elif mode == "dirty":
        noise = fbm(img.height, img.width, 4, 3, seed + 2)[:, :, None]
        arr = arr * (1.0 - 0.35 * amount) + np.array([72, 62, 48], dtype=np.float32) * 0.35 * amount * noise
    elif mode == "colorway":
        tint = np.array([1.0 + 0.15 * amount, 1.0 - 0.05 * amount, 1.0 - 0.2 * amount], dtype=np.float32)
        arr = arr[:, :, :3] * tint[None, None, :]
        if img.channels == 4:
            arr = np.concatenate([arr, img.array[:, :, 3:]], axis=2)
    elif mode == "damaged":
        noise = fbm(img.height, img.width, 6, 6, seed + 3)
        mask = (noise > 0.62).astype(np.float32)[:, :, None]
        arr = arr * (1 - mask * 0.6)
    elif mode == "clean":
        arr = np.clip(arr * 1.05 + 4.0, 0, 255)
    else:
        raise core.invalid(
            "Unknown variant mode '{0}'.".format(mode),
            "Modes: worn, wet, dirty, colorway, damaged, clean.",
        )
    arr = arr + rng.normal(0, 1.5 * amount, size=arr.shape)
    return Image(np.clip(arr, 0, 255).astype(np.uint8))


def downsample_chain(img, levels=3):
    """Generate a mip chain and report per-level statistics (bleed detection)."""
    out = []
    current = img
    for level in range(levels):
        if current.width <= 4 or current.height <= 4:
            break
        current = current.resize(max(1, current.width // 2), max(1, current.height // 2))
        arr = current.array.astype(np.float32)
        out.append(
            {
                "level": level + 1,
                "width": current.width,
                "height": current.height,
                "mean": [round(float(arr[:, :, c].mean()), 2) for c in range(current.channels)],
                "std": [round(float(arr[:, :, c].std()), 2) for c in range(current.channels)],
            }
        )
    return out, current
