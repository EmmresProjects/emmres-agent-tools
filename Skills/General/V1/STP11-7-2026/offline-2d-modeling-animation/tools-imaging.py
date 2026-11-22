#!/usr/bin/env python3
"""tools-imaging.py — pure-stdlib raster engine for the Skill Tool.

Implements PNG (8/16-bit, indexed, tRNS, sRGB/gAMA/text chunks, APNG), BMP,
TGA (RLE), PPM/PGM and GIF (LZW decode/encode, animation, transparency) read
and write, plus pixel operations: blending, alpha ops, adjustments, color
science, quantization/dither, resampling, blur, noise, analysis, drawing
primitives, a 5x7 bitmap font, a subset SVG rasterizer, contact sheets, and a
minimal PDF writer. No third-party dependencies; runs fully offline.
"""
import math
import re
import struct
import zlib


def _sib(name):
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    key = "studio." + name
    mod = sys_.modules.get(key)
    if mod is not None:
        return mod
    p = P(__file__).resolve().parent / ("tools-" + name + ".py")
    if not p.is_file():
        raise ImportError("missing sibling module: %s" % p)
    spec = ilu.spec_from_file_location(key, p)
    mod = ilu.module_from_spec(spec)
    sys_.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


core = _sib("core")
err = core.err
require = core.require
RNG = core.RNG

# =========================================================== image model ====

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class Image(object):
    __slots__ = ("w", "h", "px")

    def __init__(self, w, h, px=None):
        w, h = int(w), int(h)
        require(w > 0 and h > 0, "invalid_size", "image size must be positive (got %sx%s)" % (w, h))
        require(w * h <= 50000000, "limit_exceeded", "image too large: %sx%s" % (w, h))
        if px is None:
            px = bytearray(w * h * 4)
        elif not isinstance(px, bytearray):
            px = bytearray(px)
        require(len(px) == w * h * 4, "invalid_input", "pixel buffer size mismatch")
        self.w, self.h, self.px = w, h, px

    def clone(self):
        return Image(self.w, self.h, bytearray(self.px))

    def get(self, x, y):
        i = (y * self.w + x) * 4
        p = self.px
        return (p[i], p[i + 1], p[i + 2], p[i + 3])

    def set(self, x, y, rgba):
        i = (y * self.w + x) * 4
        p = self.px
        p[i], p[i + 1], p[i + 2], p[i + 3] = rgba[0], rgba[1], rgba[2], rgba[3]

    def has_alpha(self):
        return any(a != 255 for a in self.px[3::4])

    def __repr__(self):
        return "Image(%dx%d)" % (self.w, self.h)


def new_image(w, h, color=(0, 0, 0, 0)):
    img = Image(w, h)
    c = tuple(color)
    if len(c) < 4:
        c = c + (255,) * (4 - len(c))
    r, g, b, a = c[:4]
    if (r, g, b, a) != (0, 0, 0, 0):
        px = img.px
        for i in range(0, len(px), 4):
            px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a
    return img


def _clamp8(v):
    return 0 if v < 0 else (255 if v > 255 else int(v))


def _over(dst, src):
    sa = src[3] / 255.0
    da = dst[3] / 255.0
    oa = sa + da * (1 - sa)
    if oa <= 0:
        return (0, 0, 0, 0)
    return tuple(int(round((src[c] * sa + dst[c] * da * (1 - sa)) / oa)) for c in range(3)) + \
        (int(round(oa * 255)),)


# ============================================================== PNG I/O =====

def _png_chunk(ctype, data):
    return struct.pack(">I", len(data)) + ctype + data + \
        struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


_CT_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _parse_png_chunks(data):
    require(data[:8] == PNG_SIG, "invalid_png", "not a PNG file (bad signature)")
    chunks, pos = [], 8
    while pos < len(data):
        require(pos + 8 <= len(data), "corrupt_png", "truncated chunk header")
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        require(pos + 12 + ln <= len(data), "corrupt_png",
                "truncated chunk data (%s)" % ctype.decode("latin1"))
        cdata = data[pos + 8:pos + 8 + ln]
        crc = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])[0]
        require(crc == (zlib.crc32(ctype + cdata) & 0xFFFFFFFF), "corrupt_png",
                "CRC mismatch in %s chunk" % ctype.decode("latin1"))
        chunks.append((ctype, cdata))
        pos += 12 + ln
    require(chunks and chunks[-1][0] == b"IEND", "corrupt_png", "missing IEND chunk")
    return chunks


def _unfilter(raw, stride, h, bpp):
    lines = []
    prev = bytearray(stride)
    for y in range(h):
        ftype = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ftype != 0:
            raise err("invalid_png", "unknown PNG filter type %d" % ftype)
        lines.append(line)
        prev = line
    return lines


def decode_png_bytes(data):
    """Decode PNG -> (Image, meta). Color types 0/2/3/4/6; depths 1/2/4/8/16
    (16-bit is downscaled to 8-bit on load, noted in meta.warnings)."""
    chunks = _parse_png_chunks(data)
    meta = {"format": "png", "text": {}, "warnings": []}
    ihdr = None
    idat = bytearray()
    plte, trns = None, None
    for ctype, cdata in chunks:
        if ctype == b"IHDR":
            ihdr = cdata
        elif ctype == b"IDAT":
            idat += cdata
        elif ctype == b"PLTE":
            plte = [(cdata[i], cdata[i + 1], cdata[i + 2])
                    for i in range(0, len(cdata) - len(cdata) % 3, 3)]
        elif ctype == b"tRNS":
            trns = cdata
        elif ctype == b"sRGB":
            meta["srgb"] = cdata[0] if cdata else 0
        elif ctype == b"gAMA":
            meta["gama"] = struct.unpack(">I", cdata[:4])[0] / 100000.0 if len(cdata) >= 4 else None
        elif ctype == b"iCCP":
            meta["iccp"] = True
        elif ctype == b"tEXt":
            k, _, v = cdata.partition(b"\x00")
            meta["text"][k.decode("latin1")] = v.decode("latin1")
        elif ctype == b"zTXt":
            try:
                k, _, rest = cdata.partition(b"\x00")
                meta["text"][k.decode("latin1")] = zlib.decompress(rest[1:]).decode("latin1")
            except Exception:
                meta["warnings"].append("unreadable zTXt chunk")
        elif ctype == b"iTXt":
            meta["warnings"].append("iTXt chunk not parsed")
        elif ctype == b"acTL":
            meta["apng"] = {"num_frames": struct.unpack(">II", cdata[:8])[0],
                            "num_plays": struct.unpack(">II", cdata[:8])[1]}
    require(ihdr is not None and len(ihdr) >= 13, "corrupt_png", "missing/short IHDR")
    w, h, depth, ctype, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr[:13])
    require(w > 0 and h > 0, "corrupt_png", "zero-size image")
    require(comp == 0 and filt == 0, "invalid_png", "unsupported compression/filter method")
    require(interlace == 0, "invalid_png",
            "interlaced PNG is not supported; re-save without interlacing")
    require(ctype in _CT_CHANNELS, "invalid_png", "unsupported PNG color type %d" % ctype)
    require((ctype == 3 and depth in (1, 2, 4, 8)) or (ctype != 3 and depth in (8, 16)),
            "invalid_png", "unsupported bit depth %d for color type %d" % (depth, ctype))
    ch = _CT_CHANNELS[ctype]
    if ctype == 3:
        require(plte, "corrupt_png", "indexed PNG missing PLTE")
    raw = zlib.decompress(bytes(idat))
    stride = (w * ch * depth + 7) // 8
    require(len(raw) >= (stride + 1) * h, "corrupt_png", "IDAT too short for image size")
    bpp = max(1, ch * depth // 8)
    lines = _unfilter(raw, stride, h, bpp)
    img = Image(w, h)
    px = img.px
    if depth == 8:
        for y, line in enumerate(lines):
            base = y * w * 4
            for x in range(w):
                _store_px(px, base + x * 4, line, x * ch, ctype, plte, trns)
    elif depth == 16:
        meta["warnings"].append("16-bit PNG downscaled to 8-bit on load")
        for y, line in enumerate(lines):
            base = y * w * 4
            for x in range(w):
                _store_px16(px, base + x * 4, line, x * ch * 2, ctype, plte, trns)
    else:  # 1/2/4-bit gray or indexed
        per = 8 // depth
        mask = (1 << depth) - 1
        levels = 255 // mask
        for y, line in enumerate(lines):
            base = y * w * 4
            for x in range(w):
                byte = line[x // per]
                shift = 8 - depth * (x % per + 1)
                v = (byte >> shift) & mask
                i = base + x * 4
                if ctype == 3:
                    r, g, b = plte[v] if v < len(plte) else (0, 0, 0)
                    a = trns[v] if (trns and v < len(trns)) else 255
                else:
                    r = g = b = v * levels
                    a = 255
                px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a
    meta["bit_depth"] = depth
    meta["color_type"] = ctype
    meta["palette_size"] = len(plte) if plte else None
    if trns is not None:
        meta["trns"] = True
    return img, meta


def _store_px(px, i, line, o, ctype, plte, trns):
    if ctype == 6:
        px[i], px[i + 1], px[i + 2], px[i + 3] = line[o], line[o + 1], line[o + 2], line[o + 3]
    elif ctype == 2:
        px[i], px[i + 1], px[i + 2], px[i + 3] = line[o], line[o + 1], line[o + 2], 255
    elif ctype == 0:
        v = line[o]
        px[i], px[i + 1], px[i + 2], px[i + 3] = v, v, v, 255
    elif ctype == 4:
        v = line[o]
        px[i], px[i + 1], px[i + 2], px[i + 3] = v, v, v, line[o + 1]
    else:
        v = line[o]
        r, g, b = plte[v] if v < len(plte) else (0, 0, 0)
        a = trns[v] if (trns and v < len(trns)) else 255
        px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a


def _store_px16(px, i, line, o, ctype, plte, trns):
    if ctype == 6:
        px[i], px[i + 1], px[i + 2], px[i + 3] = line[o], line[o + 2], line[o + 4], line[o + 6]
    elif ctype == 2:
        px[i], px[i + 1], px[i + 2], px[i + 3] = line[o], line[o + 2], line[o + 4], 255
    elif ctype == 0:
        v = line[o]
        px[i], px[i + 1], px[i + 2], px[i + 3] = v, v, v, 255
    elif ctype == 4:
        v = line[o]
        px[i], px[i + 1], px[i + 2], px[i + 3] = v, v, v, line[o + 2]
    else:
        v = line[o]
        r, g, b = plte[v] if v < len(plte) else (0, 0, 0)
        a = trns[v] if (trns and v < len(trns)) else 255
        px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a


def _pack_lines(img, ch, depth16=False):
    w, h, px = img.w, img.h, img.px
    step = 2 if depth16 else 1
    stride = w * ch * step
    out = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        line = bytearray(stride)
        base = y * w * 4
        for x in range(w):
            i = base + x * 4
            o = x * ch * step
            if depth16:
                line[o] = (px[i] * 257) >> 8
                line[o + 1] = (px[i] * 257) & 0xFF
                if ch >= 3:
                    line[o + 2], line[o + 3] = (px[i + 1] * 257) >> 8, (px[i + 1] * 257) & 0xFF
                    line[o + 4], line[o + 5] = (px[i + 2] * 257) >> 8, (px[i + 2] * 257) & 0xFF
                    if ch == 4:
                        line[o + 6], line[o + 7] = (px[i + 3] * 257) >> 8, (px[i + 3] * 257) & 0xFF
            else:
                if ch >= 3:
                    line[o], line[o + 1], line[o + 2] = px[i], px[i + 1], px[i + 2]
                    if ch == 4:
                        line[o + 3] = px[i + 3]
                else:
                    line[o] = px[i]
        sub = bytearray(stride)
        sub[:ch * step] = line[:ch * step]
        for o in range(ch * step, stride):
            sub[o] = (line[o] - line[o - ch * step]) & 0xFF
        up = bytearray(stride)
        for o in range(stride):
            up[o] = (line[o] - prev[o]) & 0xFF
        best, bestline, bestval = 0, line, sum(line)
        if sum(sub) < bestval:
            best, bestline, bestval = 1, sub, sum(sub)
        if sum(up) < bestval:
            best, bestline = 2, up
        out.append(best)
        out += bestline
        prev = line
    return bytes(out)


def encode_png_bytes(img, depth=8, indexed=False, palette=None, trns=None,
                     srgb=True, gama=None, text=None, compression=6):
    px = img.px
    if indexed:
        if palette:
            pal = [tuple(c[:3]) for c in palette]
            nearest = _make_nearest(pal)
        else:
            pal, nearest = quantize(img, 256)
        idx = bytearray(img.w * img.h)
        for p in range(img.w * img.h):
            i = p * 4
            idx[p] = nearest((px[i], px[i + 1], px[i + 2]))
        out = bytearray()
        prev = bytearray(img.w)
        for y in range(img.h):
            line = idx[y * img.w:(y + 1) * img.w]
            sub = bytearray(img.w)
            sub[:1] = line[:1]
            for x in range(1, img.w):
                sub[x] = (line[x] - line[x - 1]) & 0xFF
            best, bestline, bestval = 0, line, sum(line)
            if sum(sub) < bestval:
                best, bestline = 1, sub
            out.append(best)
            out += bestline
            prev = line
        packed = bytes(out)
        plte = b"".join(bytes(c) for c in pal)
        chunks = [_png_chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 3, 0, 0, 0)),
                  _png_chunk(b"PLTE", plte)]
        if trns:
            chunks.append(_png_chunk(b"tRNS", bytes(trns)))
    else:
        alpha = img.has_alpha()
        if depth == 16:
            ctype, ch = (6, 4) if alpha else (2, 3)
            packed = _pack_lines(img, ch, depth16=True)
        else:
            ctype, ch = (6, 4) if alpha else (2, 3)
            packed = _pack_lines(img, ch)
        chunks = [_png_chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, depth, ctype, 0, 0, 0))]
    if srgb:
        chunks.append(_png_chunk(b"sRGB", b"\x00"))
    if gama:
        chunks.append(_png_chunk(b"gAMA", struct.pack(">I", int(gama * 100000))))
    for k, v in (text or {}).items():
        chunks.append(_png_chunk(b"tEXt", k.encode("latin1") + b"\x00" + str(v).encode("latin1")))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(packed, compression)))
    chunks.append(_png_chunk(b"IEND", b""))
    return PNG_SIG + b"".join(chunks)


# ------------------------------------------------------------------ APNG ----

def _delay_fraction(ms):
    ms = max(10, int(ms))
    for den in (1000, 100, 10):
        if (ms * den) % 1000 == 0:
            return (ms * den) // 1000, den
    return ms, 1000


def encode_apng_bytes(frames, loop=0, default_image=False):
    """frames: list of {image, delay_ms, dispose(0..3), blend(0=source,1=over)}."""
    require(frames, "invalid_input", "APNG needs at least one frame")
    w, h = frames[0]["image"].w, frames[0]["image"].h
    seq = [0]
    alpha = any(f["image"].has_alpha() for f in frames)
    ch = 4 if alpha else 3

    def fctl(f):
        num, den = _delay_fraction(f.get("delay_ms", 100))
        return struct.pack(">IIIIIHHBB", seq[0], f["image"].w, f["image"].h, 0, 0,
                           num, den, f.get("dispose", 0), f.get("blend", 0))

    def fdat(data):
        out = b""
        for i in range(0, len(data), 65536):
            out += _png_chunk(b"fdAT", struct.pack(">I", seq[0]) + data[i:i + 65536])
            seq[0] += 1
        return out

    base = frames[0]["image"]
    chunks = [_png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0)),
              _png_chunk(b"acTL", struct.pack(">II", len(frames), max(0, loop)))]
    idat = zlib.compress(_pack_lines(base, ch), 6)
    if default_image:
        chunks.append(_png_chunk(b"IDAT", idat))
        seq[0] += 1
        for f in frames[1:]:
            chunks.append(_png_chunk(b"fcTL", fctl(f)))
            seq[0] += 1
            chunks.append(fdat(zlib.compress(_pack_lines(f["image"], ch), 6)))
    else:
        for n, f in enumerate(frames):
            chunks.append(_png_chunk(b"fcTL", fctl(f)))
            seq[0] += 1
            if n == 0:
                chunks.append(_png_chunk(b"IDAT", idat))
            else:
                chunks.append(fdat(zlib.compress(_pack_lines(f["image"], ch), 6)))
    chunks.append(_png_chunk(b"IEND", b""))
    return PNG_SIG + b"".join(chunks)


def decode_apng_bytes(data):
    """Decode APNG -> list of {image, delay_ms, dispose, blend}."""
    chunks = _parse_png_chunks(data)
    frames = []
    pending = None
    w = h = None
    for ctype, cdata in chunks:
        if ctype == b"IHDR":
            w, h = struct.unpack(">II", cdata[:8])
        elif ctype == b"fcTL":
            if pending:
                frames.append(pending)
            seq, fw, fh, x, y, num, den, disp, blend = struct.unpack(">IIIIIHHBB", cdata[:26])
            pending = {"w": fw, "h": fh, "x": x, "y": y,
                       "delay_ms": int(round(num * 1000.0 / den)) if den else 100,
                       "dispose": disp, "blend": blend, "data": bytearray()}
        elif ctype == b"IDAT":
            if pending is not None and not pending["data"]:
                pending["data"] += cdata
        elif ctype == b"fdAT":
            if pending is not None:
                pending["data"] += cdata[4:]
    if pending:
        frames.append(pending)
    if not frames:
        img, meta = decode_png_bytes(data)
        return [{"image": img, "delay_ms": 100, "dispose": 0, "blend": 0}]
    out = []
    canvas = None
    for f in frames:
        raw = zlib.decompress(bytes(f["data"]))
        fw, fh = f["w"], f["h"]
        stride = fw * 4
        require(len(raw) >= (stride + 1) * fh, "corrupt_png", "APNG frame data too short")
        lines = _unfilter(raw, stride, fh, 4)
        img = Image(fw, fh)
        for y, line in enumerate(lines):
            img.px[y * fw * 4:(y + 1) * fw * 4] = line
        if canvas is None:
            canvas = new_image(w, h)
        snapshot = canvas.clone() if f["dispose"] == 3 else None
        _apng_composite(canvas, img, f)
        out.append({"image": canvas.clone(), "delay_ms": f["delay_ms"],
                    "dispose": f["dispose"], "blend": f["blend"]})
        if f["dispose"] == 2:
            canvas = new_image(w, h)
        elif f["dispose"] == 3 and snapshot is not None:
            canvas = snapshot
    return out


def _apng_composite(canvas, frame, f):
    x, y = f["x"], f["y"]
    for yy in range(frame.h):
        if y + yy >= canvas.h:
            break
        for xx in range(frame.w):
            if x + xx >= canvas.w:
                break
            si = (yy * frame.w + xx) * 4
            r, g, b, a = frame.px[si:si + 4]
            if f.get("blend", 0) == 1 and a < 255:
                canvas.set(x + xx, y + yy, _over(canvas.get(x + xx, y + yy), (r, g, b, a)))
            else:
                canvas.set(x + xx, y + yy, (r, g, b, a))


# ================================================================ BMP =======

def decode_bmp_bytes(data):
    require(data[:2] == b"BM", "invalid_bmp", "not a BMP file")
    require(len(data) >= 54, "invalid_bmp", "BMP header truncated")
    px_off = struct.unpack("<I", data[10:14])[0]
    hsize = struct.unpack("<I", data[14:18])[0]
    require(hsize >= 40, "invalid_bmp", "unsupported BMP header size %d" % hsize)
    w = struct.unpack("<i", data[18:22])[0]
    h = struct.unpack("<i", data[22:26])[0]
    bpp = struct.unpack("<H", data[28:30])[0]
    comp = struct.unpack("<I", data[30:34])[0]
    require(comp in (0, 3), "invalid_bmp",
            "unsupported BMP compression %d (RLE not supported)" % comp)
    require(bpp in (1, 4, 8, 16, 24, 32), "invalid_bmp", "unsupported BMP bpp %d" % bpp)
    top_down = h < 0
    h = abs(h)
    pal = []
    if bpp <= 8:
        ncol = struct.unpack("<I", data[46:50])[0] or (1 << bpp)
        for i in range(min(ncol, 256)):
            o = 14 + hsize + i * 4
            if o + 3 < len(data):
                pal.append((data[o + 2], data[o + 1], data[o]))
    masks = (0xF800, 0x07E0, 0x001F)
    if comp == 3 and bpp in (16, 32) and hsize >= 56 and len(data) >= 70:
        masks = struct.unpack("<III", data[54:66])
    img = Image(w, h)
    row_size = ((w * bpp + 31) // 32) * 4
    for y in range(h):
        yy = y if top_down else h - 1 - y
        row = data[px_off + y * row_size:px_off + (y + 1) * row_size]
        for x in range(w):
            if bpp == 24:
                o = x * 3
                if o + 2 < len(row):
                    img.set(x, yy, (row[o + 2], row[o + 1], row[o], 255))
            elif bpp == 32:
                o = x * 4
                if o + 3 < len(row):
                    img.set(x, yy, (row[o + 2], row[o + 1], row[o], row[o + 3]))
            elif bpp == 8:
                c = pal[row[x]] if row[x] < len(pal) else (0, 0, 0)
                img.set(x, yy, (c[0], c[1], c[2], 255))
            elif bpp in (1, 4):
                per = 8 // bpp
                byte = row[x // per] if x // per < len(row) else 0
                v = (byte >> (8 - bpp * (x % per + 1))) & ((1 << bpp) - 1)
                c = pal[v] if v < len(pal) else (0, 0, 0)
                img.set(x, yy, (c[0], c[1], c[2], 255))
            else:
                o = x * 2
                if o + 1 < len(row):
                    v = struct.unpack("<H", row[o:o + 2])[0]
                    if masks == (0xF800, 0x07E0, 0x001F):
                        img.set(x, yy, (((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63,
                                        (v & 31) * 255 // 31, 255))
                    else:
                        img.set(x, yy, (((v >> 10) & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31,
                                        (v & 31) * 255 // 31, 255))
    return img, {"format": "bmp", "bpp": bpp}


def encode_bmp_bytes(img, alpha32=False):
    """BMP writer. Defaults to 24-bit BGR (maximum reader compatibility);
    alpha32=True writes 32-bit BGRA (many readers ignore the alpha byte)."""
    alpha = alpha32 and img.has_alpha()
    bpp = 32 if alpha else 24
    if img.has_alpha() and not alpha32:
        img = flatten(img, (0, 0, 0))
    row_size = ((img.w * bpp + 31) // 32) * 4
    data_size = row_size * img.h
    off = 14 + 40
    out = bytearray()
    out += b"BM" + struct.pack("<IHHI", off + data_size, 0, 0, off)
    out += struct.pack("<IiiHHIIiiII", 40, img.w, -img.h, 1, bpp, 0, data_size, 2835, 2835, 0, 0)
    pad = b"\x00" * (row_size - img.w * bpp // 8)
    px = img.px
    for y in range(img.h):
        row = bytearray()
        for x in range(img.w):
            i = (y * img.w + x) * 4
            if alpha:
                row += bytes((px[i + 2], px[i + 1], px[i], px[i + 3]))
            else:
                row += bytes((px[i + 2], px[i + 1], px[i]))
        out += row + pad
    return bytes(out)


# ================================================================ TGA =======

def decode_tga_bytes(data):
    require(len(data) >= 18, "invalid_tga", "TGA header truncated")
    id_len, cmap_type, img_type = data[0], data[1], data[2]
    cmap_first, cmap_len, cmap_bpp = struct.unpack("<HHB", data[3:8])
    x, y, w, h, bpp, desc = struct.unpack("<HHHHBB", data[8:18])
    require(img_type in (1, 2, 3, 9, 10, 11), "invalid_tga", "unsupported TGA type %d" % img_type)
    require(bpp in (8, 16, 24, 32), "invalid_tga", "unsupported TGA bpp %d" % bpp)
    pos = 18 + id_len
    pal = []
    if cmap_type:
        for i in range(cmap_len):
            o = pos + i * (cmap_bpp // 8)
            if o + 2 >= len(data):
                break
            if cmap_bpp == 32:
                pal.append((data[o + 2], data[o + 1], data[o], data[o + 3]))
            else:
                pal.append((data[o + 2], data[o + 1], data[o], 255))
        pos += cmap_len * (cmap_bpp // 8)
    rle = img_type in (9, 10, 11)
    raw = data[pos:]
    pixels = bytearray()
    nbytes = bpp // 8

    def read_px(o):
        if bpp == 8:
            if cmap_type:
                k = data[o] if o < len(data) else 0
                k = k - cmap_first
                return pal[k] if 0 <= k < len(pal) else (0, 0, 0, 0)
            v = raw[o]
            return (v, v, v, 255)
        if bpp == 16:
            v = struct.unpack("<H", raw[o:o + 2])[0]
            return (((v >> 10) & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31,
                    (v & 31) * 255 // 31, 255 if v & 0x8000 else 255)
        if bpp == 24:
            return (raw[o + 2], raw[o + 1], raw[o], 255)
        return (raw[o + 2], raw[o + 1], raw[o], raw[o + 3])

    o = 0
    while len(pixels) < w * h * 4 and o < len(raw):
        if rle:
            hdr = raw[o]
            o += 1
            count = (hdr & 0x7F) + 1
            if hdr & 0x80:
                if o + nbytes > len(raw):
                    break
                c = read_px(o)
                o += nbytes
                pixels += bytes(c) * count
            else:
                for _ in range(count):
                    if o + nbytes > len(raw):
                        break
                    pixels += bytes(read_px(o))
                    o += nbytes
        else:
            if o + nbytes > len(raw):
                break
            pixels += bytes(read_px(o))
            o += nbytes
    require(len(pixels) == w * h * 4, "corrupt_tga", "TGA pixel data incomplete")
    img = Image(w, h)
    top_down = bool(desc & 0x20)
    right_left = bool(desc & 0x10)
    for yy in range(h):
        sy = yy if top_down else h - 1 - yy
        for xx in range(w):
            sx = xx if not right_left else w - 1 - xx
            i = (yy * w + xx) * 4
            j = (sy * w + sx) * 4
            img.px[j:j + 4] = pixels[i:i + 4]
    return img, {"format": "tga", "bpp": bpp, "rle": rle}


def encode_tga_bytes(img):
    alpha = img.has_alpha()
    bpp = 32 if alpha else 24
    out = bytearray()
    out += bytes((0, 0, 2, 0, 0, 0, 0, 0))
    out += struct.pack("<HHHHBB", 0, 0, img.w, img.h, bpp, 0x20)
    px = img.px
    for i in range(0, len(px), 4):
        if alpha:
            out += bytes((px[i + 2], px[i + 1], px[i], px[i + 3]))
        else:
            out += bytes((px[i + 2], px[i + 1], px[i]))
    return bytes(out)


# ============================================================ PPM/PGM =======

def decode_ppm_bytes(data):
    require(data[:2] in (b"P5", b"P6"), "invalid_ppm", "not a binary PPM/PGM file")

    def tokenize(start, n):
        vals, i, seen = [], start, 0
        while seen < n and i < len(data):
            while i < len(data) and data[i:i + 1].isspace():
                i += 1
            if i < len(data) and data[i:i + 1] == b"#":
                while i < len(data) and data[i:i + 1] not in (b"\n", b"\r"):
                    i += 1
                continue
            j = i
            while j < len(data) and not data[j:j + 1].isspace():
                j += 1
            vals.append(int(data[i:j]))
            i = j
            seen += 1
        return vals, i

    (w, h, maxv), pos = tokenize(2, 3)
    require(w > 0 and h > 0 and 0 < maxv < 65536, "invalid_ppm", "bad PPM header")
    pos += 1
    gray = data[:2] == b"P5"
    ch = 1 if gray else 3
    if maxv < 256:
        raw = data[pos:pos + w * h * ch]
        require(len(raw) == w * h * ch, "corrupt_ppm", "PPM pixel data incomplete")
    else:
        raw16 = data[pos:pos + w * h * ch * 2]
        require(len(raw16) == w * h * ch * 2, "corrupt_ppm", "PPM pixel data incomplete")
        raw = bytearray()
        for k in range(0, len(raw16), 2):
            raw.append((raw16[k] << 8 | raw16[k + 1]) * 255 // maxv)
        maxv = 255
    img = Image(w, h)
    for i in range(w * h):
        if gray:
            v = raw[i] * 255 // maxv
            img.px[i * 4:i * 4 + 4] = bytes((v, v, v, 255))
        else:
            o = i * 3
            img.px[i * 4:i * 4 + 4] = bytes((raw[o] * 255 // maxv, raw[o + 1] * 255 // maxv,
                                             raw[o + 2] * 255 // maxv, 255))
    return img, {"format": "ppm", "gray": gray, "maxval": maxv}


def encode_ppm_bytes(img, gray=False):
    out = bytearray(b"P5 " if gray else b"P6 ")
    out += b"%d %d 255\n" % (img.w, img.h)
    px = img.px
    for i in range(0, len(px), 4):
        if gray:
            out.append((px[i] * 54 + px[i + 1] * 183 + px[i + 2] * 19) >> 8)
        else:
            out += bytes((px[i], px[i + 1], px[i + 2]))
    return bytes(out)


# ================================================================ GIF =======

def _gif_lzw_decode(min_code_size, data, pixel_count):
    clear = 1 << min_code_size
    eoi = clear + 1
    size = min_code_size + 1
    dict_ = {i: [i] for i in range(clear)}
    next_slot = eoi + 1
    out = []
    prev = None
    bitpos = 0
    total_bits = len(data) * 8

    def read_code():
        nonlocal bitpos
        if bitpos + size > total_bits:
            return None
        bi = bitpos >> 3
        v = data[bi]
        if bi + 1 < len(data):
            v |= data[bi + 1] << 8
        if bi + 2 < len(data):
            v |= data[bi + 2] << 16
        code = (v >> (bitpos & 7)) & ((1 << size) - 1)
        bitpos += size
        return code

    while len(out) < pixel_count:
        code = read_code()
        if code is None:
            break
        if code == eoi:
            break
        if code == clear:
            dict_ = {i: [i] for i in range(clear)}
            next_slot = eoi + 1
            size = min_code_size + 1
            prev = None
            continue
        if prev is None:
            require(code in dict_, "corrupt_gif", "bad first code in LZW stream")
            out.extend(dict_[code])
            prev = code
            continue
        if code in dict_:
            entry = dict_[code]
        elif code == next_slot and prev in dict_:
            entry = dict_[prev] + [dict_[prev][0]]
        else:
            raise err("corrupt_gif", "invalid LZW code %d (next slot %d)" % (code, next_slot))
        out.extend(entry)
        if next_slot < 4096:
            dict_[next_slot] = dict_[prev] + [entry[0]]
            next_slot += 1
            if next_slot == (1 << size) and size < 12:
                size += 1
        prev = code
    if len(out) < pixel_count:
        out.extend([0] * (pixel_count - len(out)))
    return out[:pixel_count]


def decode_gif_bytes(data):
    require(data[:3] == b"GIF", "invalid_gif", "not a GIF file")
    require(data[3:6] in (b"87a", b"89a"), "invalid_gif", "unknown GIF version %r" % data[3:6])
    w, h, flags, bg = struct.unpack("<HHBB", data[6:12])
    pos = 13
    gct = None
    if flags & 0x80:
        n = 2 << (flags & 7)
        gct = [(data[pos + i * 3], data[pos + i * 3 + 1], data[pos + i * 3 + 2])
               for i in range(n)]
        pos += n * 3
    frames = []
    delay = 10
    transparent = None
    disposal = 0
    loop = None
    canvas = new_image(w, h)
    snapshot = None
    while pos < len(data):
        b = data[pos]
        pos += 1
        if b == 0x3B:
            break
        if b == 0x21:
            label = data[pos]
            pos += 1
            if label == 0xF9:
                sz = data[pos]
                blk = data[pos + 1:pos + 1 + sz]
                if len(blk) >= 4:
                    disposal = (blk[0] >> 2) & 7
                    delay = struct.unpack("<H", blk[1:3])[0] * 10
                    transparent = blk[3] if blk[0] & 1 else None
                pos += 1 + sz
                pos = _gif_skip_subblocks(data, pos)
            elif label == 0xFF:
                sz = data[pos]
                blk = data[pos + 1:pos + 1 + sz]
                pos += 1 + sz
                if blk[:11] == b"NETSCAPE2.0" and pos < len(data) and data[pos] >= 3:
                    sub = data[pos + 1:pos + 1 + data[pos]]
                    if len(sub) >= 3 and sub[0] == 1:
                        loop = struct.unpack("<H", sub[1:3])[0]
                    pos += 1 + data[pos]
                pos = _gif_skip_subblocks(data, pos)
            else:
                pos = _gif_skip_subblocks(data, pos)
        elif b == 0x2C:
            ix, iy, iw, ih, iflags = struct.unpack("<HHHHB", data[pos:pos + 9])
            pos += 9
            lct = gct
            if iflags & 0x80:
                n = 2 << (iflags & 7)
                lct = [(data[pos + i * 3], data[pos + i * 3 + 1], data[pos + i * 3 + 2])
                       for i in range(n)]
                pos += n * 3
            interlaced = bool(iflags & 0x40)
            min_code_size = data[pos]
            pos += 1
            lzw = bytearray()
            while True:
                sz = data[pos]
                pos += 1
                if sz == 0:
                    break
                lzw += data[pos:pos + sz]
                pos += sz
            require(lct is not None, "corrupt_gif", "GIF frame missing palette")
            idx = _gif_lzw_decode(min_code_size, bytes(lzw), iw * ih)
            frame_img = new_image(iw, ih)
            order = _gif_interlace_order(ih) if interlaced else list(range(ih))
            for yy, sy in enumerate(order):
                row = idx[yy * iw:(yy + 1) * iw]
                for xx in range(iw):
                    v = row[xx]
                    if transparent is not None and v == transparent:
                        continue
                    c = lct[v] if v < len(lct) else (0, 0, 0)
                    frame_img.set(xx, sy, (c[0], c[1], c[2], 255))
            snapshot = canvas.clone() if disposal == 3 else snapshot
            composed = canvas.clone()
            for yy in range(ih):
                if iy + yy >= h:
                    break
                for xx in range(iw):
                    if ix + xx >= w:
                        break
                    r, g, bl, a = frame_img.get(xx, yy)
                    if a > 0:
                        composed.set(ix + xx, iy + yy, (r, g, bl, 255))
            frames.append({"image": composed, "delay_ms": max(delay, 10)})
            if disposal == 3 and snapshot is not None:
                canvas = snapshot
                snapshot = None
            elif disposal == 2:
                canvas = new_image(w, h)
            else:
                canvas = composed
        else:
            raise err("corrupt_gif", "unknown GIF block 0x%02X" % b)
    require(frames, "corrupt_gif", "GIF contains no frames")
    return frames, {"format": "gif", "loop": loop, "frames": len(frames), "size": [w, h]}


def _gif_interlace_order(h):
    order = []
    for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
        for row in range(start, h, step):
            order.append(row)
    return order


def _gif_skip_subblocks(data, pos):
    while pos < len(data):
        sz = data[pos]
        pos += 1
        if sz == 0:
            return pos
        pos += sz
    return pos


def _gif_lzw_encode(min_code_size, indices):
    clear = 1 << min_code_size
    eoi = clear + 1
    out_bits = bytearray()
    state = {"buf": 0, "cnt": 0}
    size = [min_code_size + 1]
    table = {}
    next_code = [eoi + 1]

    def emit(code):
        state["buf"] |= code << state["cnt"]
        state["cnt"] += size[0]
        while state["cnt"] >= 8:
            out_bits.append(state["buf"] & 0xFF)
            state["buf"] >>= 8
            state["cnt"] -= 8

    def bump():
        # gifenc-compatible: raise size when next code exceeds current range
        if next_code[0] > (1 << size[0]) - 1 and size[0] < 12:
            size[0] += 1

    emit(clear)
    if not indices:
        emit(eoi)
        if state["cnt"]:
            out_bits.append(state["buf"] & 0xFF)
        return bytes(out_bits)
    prefix = (indices[0],)
    for v in indices[1:]:
        cand = prefix + (v,)
        if cand in table:
            prefix = cand
            continue
        emit(prefix[0] if len(prefix) == 1 else table[prefix])
        bump()
        if next_code[0] < 4096:
            table[cand] = next_code[0]
            next_code[0] += 1
        else:
            emit(clear)
            size[0] = min_code_size + 1
            table = {}
            next_code[0] = eoi + 1
        prefix = (v,)
    emit(prefix[0] if len(prefix) == 1 else table[prefix])
    bump()
    emit(eoi)
    if state["cnt"]:
        out_bits.append(state["buf"] & 0xFF)
    return bytes(out_bits)


_BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def _make_nearest(pal):
    cache = {}

    def nearest(c):
        i = cache.get(c)
        if i is None:
            best, bd = 0, 1 << 30
            for j, (r, g, b) in enumerate(pal):
                d = (r - c[0]) ** 2 + (g - c[1]) ** 2 + (b - c[2]) ** 2
                if d < bd:
                    best, bd = j, d
            i = cache[c] = best
        return i
    return nearest


def _median_cut(samples, n_colors):
    boxes = [bytes(samples)]
    while len(boxes) < n_colors:
        widest, wr, wci = None, -1, 0
        for bi, box in enumerate(boxes):
            if len(box) < 6:
                continue
            ranges = []
            for k in range(3):
                chn = box[k::3]
                ranges.append(max(chn) - min(chn))
            r = max(ranges)
            if r > wr:
                widest, wr, wci = bi, r, ranges.index(r)
        if widest is None or wr <= 0:
            break
        box = boxes.pop(widest)
        ci = wci
        order = sorted(range(len(box) // 3), key=lambda p: box[p * 3 + ci])
        mid = len(order) // 2
        left = bytearray()
        right = bytearray()
        for p in order[:mid]:
            left += box[p * 3:p * 3 + 3]
        for p in order[mid:]:
            right += box[p * 3:p * 3 + 3]
        if left:
            boxes.append(bytes(left))
        if right:
            boxes.append(bytes(right))
    pal = []
    for box in boxes:
        n = len(box) // 3
        if n:
            pal.append((sum(box[0::3]) // n, sum(box[1::3]) // n, sum(box[2::3]) // n))
    return pal or [(0, 0, 0)]


def quantize(img, n_colors, protected=None):
    """Median-cut quantization -> (palette, nearest-color function).
    Protected colors may be hex strings or rgb tuples and map exactly."""
    px = img.px
    colors = {}
    for i in range(0, len(px), 4):
        if px[i + 3] > 0:
            key = (px[i], px[i + 1], px[i + 2])
            colors[key] = colors.get(key, 0) + 1
    keys = sorted(colors, key=lambda c: -colors[c])
    pal = []
    for c in (protected or []):
        rgb = hex_to_rgb(c) if isinstance(c, str) else tuple(int(v) for v in c[:3])
        if rgb not in pal:
            pal.append(rgb)
    if len(keys) + len(pal) <= n_colors:
        for c in keys:
            if c not in pal:
                pal.append(c)
    else:
        samples = bytearray()
        for c in keys[:n_colors * 64]:
            samples += bytes(c)
        for c in _median_cut(samples, n_colors - len(pal)):
            if c not in pal:
                pal.append(c)
    pal = pal[:n_colors]
    return pal, _make_nearest(pal)


def encode_gif_bytes(frames, loop=0, dither=False, max_colors=256, transparent_threshold=128):
    require(frames, "invalid_input", "GIF needs at least one frame")
    w, h = frames[0]["image"].w, frames[0]["image"].h
    for f in frames:
        require(f["image"].w == w and f["image"].h == h, "invalid_input",
                "all GIF frames must share one canvas size")
    samples = bytearray()
    for f in frames:
        img = f["image"]
        step = max(1, (img.w * img.h) // 20000)
        px = img.px
        for i in range(0, len(px), 4 * step):
            if px[i + 3] >= transparent_threshold:
                samples += bytes((px[i], px[i + 1], px[i + 2]))
    uniq = set(bytes(samples[i:i + 3]) for i in range(0, len(samples), 3))
    has_transparency = any(f["image"].has_alpha() for f in frames)
    budget = max_colors - (1 if has_transparency else 0)
    if len(uniq) <= budget:
        pal = [tuple(c) for c in sorted(uniq)]
    else:
        pal = _median_cut(samples, budget)
    pal = [(int(r), int(g), int(b)) for r, g, b in pal]
    tidx = len(pal) if has_transparency else None
    pal_full = pal + [(0, 0, 0)] if has_transparency else pal
    # pad palette to a power of two (GIF global color table requirement)
    pow2 = 2
    while pow2 < len(pal_full):
        pow2 *= 2
    while len(pal_full) < pow2:
        pal_full.append((0, 0, 0))
    nearest = _make_nearest(pal)
    min_code_size = 2
    while (1 << min_code_size) < len(pal_full):
        min_code_size += 1
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", w, h, 0x80 | (pow2.bit_length() - 2), 0, 0)
    for c in pal_full:
        out += bytes(c)
    if loop is not None:
        out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01" + struct.pack("<H", max(0, loop)) + b"\x00"
    for f in frames:
        img = f["image"]
        px = img.px
        idx = bytearray(w * h)
        for p in range(w * h):
            i = p * 4
            if has_transparency and px[i + 3] < transparent_threshold:
                idx[p] = tidx
            else:
                key = (px[i], px[i + 1], px[i + 2])
                if dither:
                    d = (_BAYER4[(p // w) & 3][p % w & 3] - 7.5) * 24
                    key = (_clamp8(key[0] + d), _clamp8(key[1] + d), _clamp8(key[2] + d))
                idx[p] = nearest(key)
        delay_cs = max(2, int(round(f.get("delay_ms", 100) / 10.0)))
        dispose = 2 if has_transparency else 1
        gce_flags = (1 if has_transparency else 0) | (dispose << 2)
        out += b"\x21\xF9\x04" + bytes((gce_flags,)) + struct.pack("<H", delay_cs) + \
            bytes((tidx if tidx is not None else 0,)) + b"\x00"
        out += bytes((0x2C,)) + struct.pack("<HHHHB", 0, 0, w, h, 0)
        out += bytes((min_code_size,))
        lzw = _gif_lzw_encode(min_code_size, bytes(idx))
        for i in range(0, len(lzw), 255):
            out += bytes((min(255, len(lzw) - i),)) + lzw[i:i + 255]
        out += b"\x00"
    out += b"\x3B"
    return bytes(out)


# ==================================================== generic image I/O =====

def sniff_format(data, hint=""):
    if data[:8] == PNG_SIG:
        return "png"
    if data[:3] == b"GIF":
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:2] in (b"P5", b"P6"):
        return "ppm"
    if hint.lower().endswith(".tga"):
        return "tga"
    return None


def decode_image_bytes(data, path_hint=""):
    fmt = sniff_format(data, path_hint)
    if fmt == "png":
        img, meta = decode_png_bytes(data)
        return img, meta
    if fmt == "gif":
        frames, meta = decode_gif_bytes(data)
        meta["gif_frames"] = len(frames)
        return frames[0]["image"], meta
    if fmt == "bmp":
        return decode_bmp_bytes(data)
    if fmt == "ppm":
        return decode_ppm_bytes(data)
    if fmt == "tga":
        return decode_tga_bytes(data)
    raise err("unsupported_format",
              "cannot decode %s: unsupported or unrecognized image format "
              "(PNG/GIF/BMP/TGA/PPM supported; JPEG/TIFF/WebP need an external converter)"
              % path_hint)


def load_image(path):
    data = core.read_bytes(path)
    img, meta = decode_image_bytes(data, str(path))
    meta["path"] = str(path)
    meta["bytes"] = len(data)
    return img, meta


def load_animation(path):
    """Load an animation file -> (frames, meta)."""
    data = core.read_bytes(path)
    if data[:3] == b"GIF":
        return decode_gif_bytes(data)
    if data[:8] == PNG_SIG and b"acTL" in data:
        frames = decode_apng_bytes(data)
        return frames, {"format": "apng", "frames": len(frames),
                        "size": [frames[0]["image"].w, frames[0]["image"].h]}
    img, meta = decode_image_bytes(data, str(path))
    return ([{"image": img, "delay_ms": 100}], meta)


def save_image(path, img, fmt=None, **opts):
    path = str(path)
    fmt = (fmt or path.rsplit(".", 1)[-1].lower())
    if fmt == "png":
        data = encode_png_bytes(img, depth=opts.get("depth", 8), indexed=opts.get("indexed", False),
                                palette=opts.get("palette"), srgb=opts.get("srgb", True),
                                gama=opts.get("gama"), text=opts.get("text"),
                                compression=opts.get("compression", 6))
    elif fmt == "bmp":
        data = encode_bmp_bytes(img)
    elif fmt == "tga":
        data = encode_tga_bytes(img)
    elif fmt in ("ppm", "pnm"):
        data = encode_ppm_bytes(img)
    elif fmt == "pgm":
        data = encode_ppm_bytes(img, gray=True)
    else:
        raise err("unsupported_format", "cannot write .%s (use png/bmp/tga/ppm/pgm)" % fmt)
    core.write_bytes(path, data)
    return path


def save_animation(path, frames, loop=0, **opts):
    path = str(path)
    fmt = path.rsplit(".", 1)[-1].lower()
    if fmt == "gif":
        data = encode_gif_bytes(frames, loop=loop, dither=opts.get("dither", False),
                                max_colors=opts.get("max_colors", 256))
    elif fmt == "png" or fmt == "apng":
        data = encode_apng_bytes(frames, loop=loop, default_image=opts.get("default_image", False))
    else:
        raise err("unsupported_format", "cannot write animation .%s (use .gif or .png/.apng)" % fmt)
    core.write_bytes(path, data)
    return path


# ============================================================ pixel ops =====

BLEND_MODES = ["normal", "multiply", "screen", "add", "subtract", "darken", "lighten",
               "difference", "exclusion", "overlay", "hard_light", "soft_light",
               "color_dodge", "color_burn"]


def _blend_pair(mode, b, t):
    if mode == "normal":
        return t
    if mode == "multiply":
        return b * t // 255
    if mode == "screen":
        return 255 - (255 - b) * (255 - t) // 255
    if mode == "add":
        return min(255, b + t)
    if mode == "subtract":
        return max(0, b - t)
    if mode == "darken":
        return b if b < t else t
    if mode == "lighten":
        return b if b > t else t
    if mode == "difference":
        return b - t if b > t else t - b
    if mode == "exclusion":
        return b + t - 2 * b * t // 255
    if mode == "overlay":
        return (2 * b * t // 255) if t <= 127 else (255 - 2 * (255 - b) * (255 - t) // 255)
    if mode == "hard_light":
        return (2 * b * t // 255) if b <= 127 else (255 - 2 * (255 - b) * (255 - t) // 255)
    if mode == "soft_light":
        return (b * (255 + (2 * t - 255) * ((2 * b * (255 - b)) // 65025))) // 255
    if mode == "color_dodge":
        return 255 if t >= 255 else min(255, b * 255 // (255 - t))
    if mode == "color_burn":
        return 0 if t <= 0 else max(0, 255 - (255 - b) * 255 // t)
    raise err("invalid_input", "unknown blend mode '%s' (available: %s)" % (mode, ", ".join(BLEND_MODES)))


def composite(base, top, mode="normal", opacity=1.0):
    """Composite top over base with a blend mode. Returns a new Image."""
    require(base.w == top.w and base.h == top.h, "invalid_input",
            "layer size mismatch: %dx%d vs %dx%d" % (base.w, base.h, top.w, top.h))
    require(0.0 <= opacity <= 1.0, "invalid_input", "opacity must be within 0..1")
    require(mode in BLEND_MODES, "invalid_input", "unknown blend mode '%s'" % mode)
    out = base.clone()
    bp, tp, op = base.px, top.px, out.px
    cache = {}
    for i in range(0, len(bp), 4):
        ta = tp[i + 3]
        if ta == 0:
            continue
        if opacity < 1.0:
            ta = int(ta * opacity)
        if ta == 0:
            continue
        key = (bp[i], bp[i + 1], bp[i + 2], tp[i], tp[i + 1], tp[i + 2], ta)
        c = cache.get(key)
        if c is None:
            bc = (bp[i], bp[i + 1], bp[i + 2])
            tc = (tp[i], tp[i + 1], tp[i + 2])
            blended = (_blend_pair(mode, bc[0], tc[0]), _blend_pair(mode, bc[1], tc[1]),
                       _blend_pair(mode, bc[2], tc[2]))
            sa = ta / 255.0
            da = bp[i + 3] / 255.0
            oa = sa + da * (1 - sa)
            if oa <= 0:
                c = (0, 0, 0, 0)
            else:
                c = tuple(int(round((blended[ch] * sa + bc[ch] * da * (1 - sa)) / oa))
                          for ch in range(3)) + (int(round(oa * 255)),)
            cache[key] = c
        op[i:i + 4] = bytes(c)
    return out


def flatten(img, bg=(0, 0, 0)):
    """Composite over an opaque background color (drops alpha)."""
    bg = tuple(int(c) for c in bg[:3])
    out = Image(img.w, img.h)
    px, op = img.px, out.px
    for i in range(0, len(px), 4):
        r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a == 255:
            op[i:i + 4] = bytes((r, g, b, 255))
        elif a == 0:
            op[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))
        else:
            k = a / 255.0
            op[i:i + 4] = bytes((_clamp8(r * k + bg[0] * (1 - k)),
                                  _clamp8(g * k + bg[1] * (1 - k)),
                                  _clamp8(b * k + bg[2] * (1 - k)), 255))
    return out


def apply_opacity(img, opacity):
    out = img.clone()
    if opacity >= 1.0:
        return out
    px = out.px
    for i in range(3, len(px), 4):
        px[i] = int(px[i] * opacity)
    return out


def premultiply(img):
    out = img.clone()
    px = out.px
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if a < 255:
            px[i] = px[i] * a // 255
            px[i + 1] = px[i + 1] * a // 255
            px[i + 2] = px[i + 2] * a // 255
    return out


def unpremultiply(img):
    out = img.clone()
    px = out.px
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if 0 < a < 255:
            px[i] = min(255, px[i] * 255 // a)
            px[i + 1] = min(255, px[i + 1] * 255 // a)
            px[i + 2] = min(255, px[i + 2] * 255 // a)
    return out


def defringe(img, radius=1):
    """Remove matte fringes: bleed nearby opaque color into low-alpha pixels."""
    out = img.clone()
    for _ in range(max(1, int(radius))):
        src = out.clone()
        for y in range(img.h):
            for x in range(img.w):
                i = (y * img.w + x) * 4
                if src.px[i + 3] > 32:
                    continue
                best = None
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < img.w and 0 <= ny < img.h:
                        j = (ny * img.w + nx) * 4
                        if src.px[j + 3] > 128:
                            wgt = src.px[j + 3]
                            if best is None or wgt > best[3]:
                                best = (src.px[j], src.px[j + 1], src.px[j + 2], wgt)
                if best:
                    out.px[i], out.px[i + 1], out.px[i + 2] = best[0], best[1], best[2]
    return out


# ------------------------------------------------------------- adjust -------

def apply_lut(img, lut):
    out = img.clone()
    px = out.px
    for i in range(0, len(px), 4):
        px[i] = lut[px[i]]
        px[i + 1] = lut[px[i + 1]]
        px[i + 2] = lut[px[i + 2]]
    return out


def op_invert(img):
    return apply_lut(img, [255 - v for v in range(256)])


def op_levels(img, black=0, white=255, gamma=1.0):
    require(black < white, "invalid_input", "levels black must be below white")
    lut = []
    for v in range(256):
        t = (v - black) / float(white - black)
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        lut.append(_clamp8(math.pow(t, 1.0 / max(gamma, 0.01)) * 255))
    return apply_lut(img, lut)


def op_curves(img, points):
    require(len(points) >= 2, "invalid_input", "curves need at least 2 points")
    pts = sorted([tuple(p) for p in points])
    lut = []
    j = 0
    for v in range(256):
        while j < len(pts) - 2 and pts[j + 1][0] < v:
            j += 1
        x0, y0 = pts[j]
        x1, y1 = pts[min(j + 1, len(pts) - 1)]
        t = 0.0 if x1 == x0 or v < x0 else (1.0 if v >= x1 else (v - x0) / float(x1 - x0))
        t = t * t * (3 - 2 * t)
        lut.append(_clamp8(y0 + (y1 - y0) * t))
    return apply_lut(img, lut)


def op_hsv(img, hue_shift=0.0, sat_scale=1.0, val_scale=1.0):
    out = img.clone()
    px = out.px
    cache = {}
    for i in range(0, len(px), 4):
        key = (px[i], px[i + 1], px[i + 2])
        c = cache.get(key)
        if c is None:
            h, s, v = rgb_to_hsv(key[0], key[1], key[2])
            h = (h + hue_shift / 360.0) % 1.0
            s = max(0.0, min(1.0, s * sat_scale))
            v = max(0.0, min(1.0, v * val_scale))
            c = hsv_to_rgb(h, s, v)
            cache[key] = c
        px[i], px[i + 1], px[i + 2] = c
    return out


def op_brightness_contrast(img, brightness=0.0, contrast=0.0):
    b = brightness * 255.0
    lut = [_clamp8((v - 128) * (1 + contrast) + 128 + b) for v in range(256)]
    return apply_lut(img, lut)


def op_exposure(img, stops=0.0):
    k = 2.0 ** stops
    lut = [_clamp8(v * k) for v in range(256)]
    return apply_lut(img, lut)


def op_posterize(img, levels=4):
    require(2 <= levels <= 64, "invalid_input", "posterize levels must be 2..64")
    step = 255 // (levels - 1)
    lut = [_clamp8(int(round(v / float(step))) * step) for v in range(256)]
    return apply_lut(img, lut)


ADJUST_OPS = {
    "invert": lambda img, p: op_invert(img),
    "levels": lambda img, p: op_levels(img, p.get("black", 0), p.get("white", 255), p.get("gamma", 1.0)),
    "curves": lambda img, p: op_curves(img, p.get("points", [[0, 0], [255, 255]])),
    "hue_sat": lambda img, p: op_hsv(img, p.get("hue", 0), p.get("sat", 1.0), p.get("value", 1.0)),
    "brightness_contrast": lambda img, p: op_brightness_contrast(img, p.get("brightness", 0), p.get("contrast", 0)),
    "exposure": lambda img, p: op_exposure(img, p.get("stops", 0)),
    "posterize": lambda img, p: op_posterize(img, p.get("levels", 4)),
}


def apply_recipe(img, recipe):
    """recipe: list of {op, ...params} applied in order. Recipes are saved as
    JSON and re-applied (recorded, non-destructive workflow)."""
    require(isinstance(recipe, list), "invalid_input", "recipe must be a list of operations")
    for step in recipe:
        require(isinstance(step, dict) and step.get("op") in ADJUST_OPS, "invalid_input",
                "unknown recipe op: %r" % (step,))
        img = ADJUST_OPS[step["op"]](img, step)
    return img


# ------------------------------------------------------------ color science -

def rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    v = mx
    s = 0.0 if mx == 0 else d / mx
    if d == 0:
        h = 0.0
    elif mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h / 6.0, s, v)


def hsv_to_rgb(h, s, v):
    h = h % 1.0
    i = int(h * 6)
    f = h * 6 - i
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return (_clamp8(r * 255), _clamp8(g * 255), _clamp8(b * 255))


def rgb_to_hsl(r, g, b):
    h, s, v = rgb_to_hsv(r, g, b)
    l = v * (1 - s / 2.0)
    s2 = 0.0 if l in (0, 1) else (v - l) / min(l, 1 - l)
    return (h, s2, l)


def hex_to_rgb(value):
    v = str(value).strip().lstrip("#")
    if len(v) == 3:
        return tuple(int(ch * 2, 16) for ch in v)
    if len(v) == 6:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    raise err("invalid_input", "bad hex color '%s'" % value)


def rgb_to_hex(c):
    return "#%02x%02x%02x" % (int(c[0]), int(c[1]), int(c[2]))


def luma8(r, g, b):
    return (r * 54 + g * 183 + b * 19) >> 8


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def relative_luminance(rgb):
    return 0.2126 * srgb_to_linear(rgb[0]) + 0.7152 * srgb_to_linear(rgb[1]) + \
        0.0722 * srgb_to_linear(rgb[2])


def contrast_ratio(c1, c2):
    l1 = relative_luminance(c1) + 0.05
    l2 = relative_luminance(c2) + 0.05
    hi, lo = max(l1, l2), min(l1, l2)
    return round(hi / lo, 3)


CVD_MATRICES = {
    "protanopia": ((0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758)),
    "protanomaly": ((0.817, 0.183, 0.0), (0.333, 0.667, 0.0), (0.0, 0.125, 0.875)),
    "deuteranopia": ((0.625, 0.375, 0.0), (0.7, 0.3, 0.0), (0.0, 0.3, 0.7)),
    "deuteranomaly": ((0.8, 0.2, 0.0), (0.258, 0.742, 0.0), (0.0, 0.142, 0.858)),
    "tritanopia": ((0.95, 0.05, 0.0), (0.0, 0.433, 0.567), (0.0, 0.475, 0.525)),
    "tritanomaly": ((0.967, 0.033, 0.0), (0.0, 0.733, 0.267), (0.0, 0.183, 0.817)),
    "achromatopsia": ((0.299, 0.587, 0.114),) * 3,
}


def apply_cvd(img, kind="deuteranopia"):
    require(kind in CVD_MATRICES, "invalid_input", "unknown CVD type '%s'" % kind)
    m = CVD_MATRICES[kind]
    out = img.clone()
    px = out.px
    cache = {}
    for i in range(0, len(px), 4):
        key = (px[i], px[i + 1], px[i + 2])
        c = cache.get(key)
        if c is None:
            c = tuple(_clamp8(m[k][0] * key[0] + m[k][1] * key[1] + m[k][2] * key[2])
                      for k in range(3))
            cache[key] = c
        px[i], px[i + 1], px[i + 2] = c
    return out


# ------------------------------------------------------------ resampling ----

def resize(img, w2, h2, method="bilinear"):
    w2, h2 = int(w2), int(h2)
    require(w2 > 0 and h2 > 0, "invalid_input", "target size must be positive")
    if w2 == img.w and h2 == img.h:
        return img.clone()
    if method == "nearest":
        out = Image(w2, h2)
        for y in range(h2):
            sy = min(img.h - 1, y * img.h // h2)
            for x in range(w2):
                sx = min(img.w - 1, x * img.w // w2)
                i = (sy * img.w + sx) * 4
                j = (y * w2 + x) * 4
                out.px[j:j + 4] = img.px[i:i + 4]
        return out
    if method in ("box", "bilinear"):
        tmp = Image(w2, img.h)
        sx_ratio = img.w / float(w2)
        for x in range(w2):
            if method == "box" and sx_ratio > 1:
                x0 = int(x * sx_ratio)
                x1 = max(x0 + 1, min(img.w, int((x + 1) * sx_ratio)))
                n = x1 - x0
                for y in range(img.h):
                    r = g = b = a = 0
                    for xs in range(x0, x1):
                        i = (y * img.w + xs) * 4
                        r += img.px[i]
                        g += img.px[i + 1]
                        b += img.px[i + 2]
                        a += img.px[i + 3]
                    j = (y * w2 + x) * 4
                    tmp.px[j] = r // n
                    tmp.px[j + 1] = g // n
                    tmp.px[j + 2] = b // n
                    tmp.px[j + 3] = a // n
            else:
                fx = (x + 0.5) * sx_ratio - 0.5
                x0 = int(math.floor(fx))
                fx -= x0
                x0c = max(0, min(img.w - 1, x0))
                x1c = max(0, min(img.w - 1, x0 + 1))
                for y in range(img.h):
                    i0 = (y * img.w + x0c) * 4
                    i1 = (y * img.w + x1c) * 4
                    j = (y * w2 + x) * 4
                    for c in range(4):
                        tmp.px[j + c] = int(img.px[i0 + c] * (1 - fx) + img.px[i1 + c] * fx + 0.5)
        out = Image(w2, h2)
        sy_ratio = img.h / float(h2)
        for y in range(h2):
            if method == "box" and sy_ratio > 1:
                y0 = int(y * sy_ratio)
                y1 = max(y0 + 1, min(tmp.h, int((y + 1) * sy_ratio)))
                n = y1 - y0
                for x in range(w2):
                    r = g = b = a = 0
                    for ys in range(y0, y1):
                        i = (ys * w2 + x) * 4
                        r += tmp.px[i]
                        g += tmp.px[i + 1]
                        b += tmp.px[i + 2]
                        a += tmp.px[i + 3]
                    j = (y * w2 + x) * 4
                    out.px[j] = r // n
                    out.px[j + 1] = g // n
                    out.px[j + 2] = b // n
                    out.px[j + 3] = a // n
            else:
                fy = (y + 0.5) * sy_ratio - 0.5
                y0 = int(math.floor(fy))
                fy -= y0
                y0c = max(0, min(tmp.h - 1, y0))
                y1c = max(0, min(tmp.h - 1, y0 + 1))
                for x in range(w2):
                    i0 = (y0c * w2 + x) * 4
                    i1 = (y1c * w2 + x) * 4
                    j = (y * w2 + x) * 4
                    for c in range(4):
                        out.px[j + c] = int(tmp.px[i0 + c] * (1 - fy) + tmp.px[i1 + c] * fy + 0.5)
        return out
    raise err("invalid_input", "unknown resample method '%s' (nearest/box/bilinear)" % method)


def flip(img, horizontal=True):
    out = Image(img.w, img.h)
    for y in range(img.h):
        for x in range(img.w):
            sx = img.w - 1 - x if horizontal else x
            sy = img.h - 1 - y if not horizontal else y
            i = (sy * img.w + sx) * 4
            j = (y * img.w + x) * 4
            out.px[j:j + 4] = img.px[i:i + 4]
    return out


def rotate90(img, quarter=1):
    quarter %= 4
    if quarter == 0:
        return img.clone()
    if quarter == 2:
        return flip(flip(img, True), False)
    w2, h2 = img.h, img.w
    out = Image(w2, h2)
    for y in range(img.h):
        for x in range(img.w):
            i = (y * img.w + x) * 4
            j = (x * w2 + (h2 - 1 - y)) * 4 if quarter == 1 else ((w2 - 1 - x) * w2 + y) * 4
            out.px[j:j + 4] = img.px[i:i + 4]
    return out


def rotate_free(img, degrees, bg=(0, 0, 0, 0)):
    rad = math.radians(degrees)
    cosr, sinr = math.cos(rad), math.sin(rad)
    out = new_image(img.w, img.h, bg)
    cx, cy = img.w / 2.0, img.h / 2.0
    for y in range(img.h):
        for x in range(img.w):
            dx, dy = x - cx, y - cy
            sx = cosr * dx + sinr * dy + cx
            sy = -sinr * dx + cosr * dy + cy
            x0, y0 = int(sx), int(sy)
            if 0 <= x0 < img.w - 1 and 0 <= y0 < img.h - 1:
                fx, fy = sx - x0, sy - y0
                i00 = (y0 * img.w + x0) * 4
                i10 = i00 + 4
                i01 = i00 + img.w * 4
                i11 = i01 + 4
                j = (y * img.w + x) * 4
                for c in range(4):
                    out.px[j + c] = _clamp8(img.px[i00 + c] * (1 - fx) * (1 - fy) +
                                            img.px[i10 + c] * fx * (1 - fy) +
                                            img.px[i01 + c] * (1 - fx) * fy +
                                            img.px[i11 + c] * fx * fy)
    return out


def crop(img, x, y, w, h, fill=(0, 0, 0, 0)):
    out = new_image(w, h, fill)
    for yy in range(h):
        for xx in range(w):
            sx, sy = x + xx, y + yy
            if 0 <= sx < img.w and 0 <= sy < img.h:
                i = (sy * img.w + sx) * 4
                j = (yy * w + xx) * 4
                out.px[j:j + 4] = img.px[i:i + 4]
    return out


# ---------------------------------------------------------------- blur ------

def blur_image(img, radius):
    """Separable box blur (clamped edges), premultiplied for clean alpha."""
    if radius < 1:
        return img.clone()
    radius = int(radius)
    w, h = img.w, img.h

    def blur_axis(src, n, other, stride, rowoff, radius):
        dst = bytearray(len(src))
        for o in range(other):
            base = o * rowoff
            s = [0, 0, 0, 0]
            count = 0
            for k in range(0, min(radius + 1, n)):
                i = base + k * stride
                s[0] += src[i]
                s[1] += src[i + 1]
                s[2] += src[i + 2]
                s[3] += src[i + 3]
                count += 1
            for k in range(n):
                i = base + k * stride
                dst[i] = s[0] // count
                dst[i + 1] = s[1] // count
                dst[i + 2] = s[2] // count
                dst[i + 3] = s[3] // count
                add = k + radius + 1
                if add < n:
                    j = base + add * stride
                    s[0] += src[j]
                    s[1] += src[j + 1]
                    s[2] += src[j + 2]
                    s[3] += src[j + 3]
                    count += 1
                rem = k - radius
                if rem >= 0:
                    j = base + rem * stride
                    s[0] -= src[j]
                    s[1] -= src[j + 1]
                    s[2] -= src[j + 2]
                    s[3] -= src[j + 3]
                    count -= 1
        return dst

    pre = premultiply(img).px
    tmp = blur_axis(pre, w, h, 4, w * 4, radius)
    tmp = blur_axis(tmp, h, w, w * 4, 4, radius)
    return unpremultiply(Image(w, h, tmp))


# ------------------------------------------------------------- analysis -----

def stats(img):
    px = img.px
    n = img.w * img.h
    sums = [0, 0, 0, 0]
    mins = [255] * 4
    maxs = [0] * 4
    for i in range(0, len(px), 4):
        for c in range(4):
            v = px[i + c]
            sums[c] += v
            if v < mins[c]:
                mins[c] = v
            if v > maxs[c]:
                maxs[c] = v
    return {"size": [img.w, img.h], "pixels": n,
            "mean": [round(sums[c] / float(n), 2) for c in range(4)],
            "min": mins, "max": maxs, "has_alpha": img.has_alpha()}


def histogram(img):
    h = {"r": [0] * 256, "g": [0] * 256, "b": [0] * 256, "a": [0] * 256, "luma": [0] * 256}
    px = img.px
    for i in range(0, len(px), 4):
        h["r"][px[i]] += 1
        h["g"][px[i + 1]] += 1
        h["b"][px[i + 2]] += 1
        h["a"][px[i + 3]] += 1
        h["luma"][luma8(px[i], px[i + 1], px[i + 2])] += 1
    return h


def mean_color(img):
    px = img.px
    n = img.w * img.h
    return (sum(px[0::4]) // n, sum(px[1::4]) // n, sum(px[2::4]) // n)


def dominant_colors(img, n=8):
    counts = {}
    px = img.px
    for i in range(0, len(px), 4):
        if px[i + 3] < 8:
            continue
        key = (px[i] & 0xF8, px[i + 1] & 0xF8, px[i + 2] & 0xF8)
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:n]
    total = sum(c for _, c in top) or 1
    return [{"rgb": list(k), "share": round(c / float(total), 4)} for k, c in top]


def ahash(img, hash_size=8):
    small = resize(img, hash_size, hash_size, "bilinear")
    lums = [luma8(small.px[i], small.px[i + 1], small.px[i + 2])
            for i in range(0, len(small.px), 4)]
    srt = sorted(lums)
    med = srt[len(srt) // 2]
    bits = 0
    for i, v in enumerate(lums):
        if v > med:
            bits |= 1 << i
    return "%016x" % bits


def dhash(img, hash_size=8):
    small = resize(img, hash_size + 1, hash_size, "bilinear")
    bits = 0
    i = 0
    for y in range(hash_size):
        for x in range(hash_size):
            a = (y * (hash_size + 1) + x) * 4
            b = a + 4
            if luma8(small.px[a], small.px[a + 1], small.px[a + 2]) > \
               luma8(small.px[b], small.px[b + 1], small.px[b + 2]):
                bits |= 1 << i
            i += 1
    return "%016x" % bits


def hamming_hex(h1, h2):
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def diff_images(a, b):
    require(a.w == b.w and a.h == b.h, "invalid_input", "images differ in size")
    out = new_image(a.w, a.h, (0, 0, 0, 255))
    changed = 0
    maxd = 0
    sdiff = 0
    for i in range(0, len(a.px), 4):
        d = max(abs(a.px[i] - b.px[i]), abs(a.px[i + 1] - b.px[i + 1]),
                abs(a.px[i + 2] - b.px[i + 2]), abs(a.px[i + 3] - b.px[i + 3]))
        if d > 0:
            changed += 1
        sdiff += d
        if d > maxd:
            maxd = d
        v = _clamp8(d)
        out.px[i], out.px[i + 1], out.px[i + 2] = v, v, v
    n = a.w * a.h
    return {"changed_pixels": changed, "changed_fraction": round(changed / float(n), 6),
            "max_delta": maxd, "mean_delta": round(sdiff / float(n) / 4.0, 4), "diff_image": out}


def silhouette_metrics(img, threshold=8):
    """Alpha silhouette: area, bbox, centroid, edge density (line-boil signal)."""
    w, h = img.w, img.h
    px = img.px
    area = 0
    minx, miny, maxx, maxy = w, h, -1, -1
    cx = cy = 0.0
    for y in range(h):
        rowbase = y * w
        for x in range(w):
            if px[(rowbase + x) * 4 + 3] > threshold:
                area += 1
                cx += x
                cy += y
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if area == 0:
        return {"area": 0, "bbox": None, "centroid": None, "edge_density": 0.0}
    edges = 0
    for y in range(max(0, miny), min(h, maxy + 1)):
        rowbase = y * w
        for x in range(max(0, minx), min(w, maxx + 1)):
            if px[(rowbase + x) * 4 + 3] > threshold:
                if x + 1 >= w or px[(rowbase + x + 1) * 4 + 3] <= threshold:
                    edges += 1
                if y + 1 >= h or px[((y + 1) * w + x) * 4 + 3] <= threshold:
                    edges += 1
    return {"area": area, "bbox": [minx, miny, maxx - minx + 1, maxy - miny + 1],
            "centroid": [round(cx / area, 2), round(cy / area, 2)],
            "edge_density": round(edges / float(area), 5)}


def otsu_threshold(img):
    hist = [0] * 256
    px = img.px
    for i in range(0, len(px), 4):
        hist[luma8(px[i], px[i + 1], px[i + 2])] += 1
    total = img.w * img.h
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = 0
    w_b = 0
    best_t, best_v = 0, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        mb = sum_b / float(w_b)
        mf = (sum_all - sum_b) / float(w_f)
        v = w_b * w_f * (mb - mf) * (mb - mf)
        if v > best_v:
            best_v, best_t = v, t
    return best_t


# ------------------------------------------------------------- noise --------

def value_noise(w, h, seed, cells=6, octaves=1):
    """Deterministic grayscale noise field, values 0..255, size w*h."""
    rng = RNG(seed)
    fields = []
    for o in range(max(1, octaves)):
        g = max(2, min(256, int(cells * (2 ** o))))
        grid = [[rng.random() for _ in range(g + 1)] for _ in range(g + 1)]
        fields.append((grid, g, 1.0 / (2 ** o)))
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            v = 0.0
            norm = 0.0
            for grid, g, amp in fields:
                fx = x / float(w) * g
                fy = y / float(h) * g
                x0, y0 = int(fx), int(fy)
                tx, ty = fx - x0, fy - y0
                x1, y1 = min(x0 + 1, g), min(y0 + 1, g)
                a = grid[y0][x0] * (1 - tx) + grid[y0][x1] * tx
                b = grid[y1][x0] * (1 - tx) + grid[y1][x1] * tx
                v += (a * (1 - ty) + b * ty) * amp
                norm += amp
            out[y * w + x] = _clamp8(v / norm * 255)
    return out


def add_grain(img, amount=12, seed="grain"):
    noise = value_noise(img.w, img.h, seed, cells=64)
    out = img.clone()
    px = out.px
    for p in range(img.w * img.h):
        i = p * 4
        if px[i + 3] == 0:
            continue
        d = (noise[p] - 128) * amount / 128.0
        px[i] = _clamp8(px[i] + d)
        px[i + 1] = _clamp8(px[i + 1] + d)
        px[i + 2] = _clamp8(px[i + 2] + d)
    return out


# ------------------------------------------------------------ drawing -------

def draw_pixel(img, x, y, color):
    if 0 <= x < img.w and 0 <= y < img.h:
        img.set(x, y, color)


def _fill_circle_ish(img, cx, cy, r, color):
    ri = int(r + 0.5)
    for dy in range(-ri, ri + 1):
        for dx in range(-ri, ri + 1):
            if dx * dx + dy * dy <= r * r + 0.5:
                draw_pixel(img, int(cx) + dx, int(cy) + dy, color)


def draw_line(img, x0, y0, x1, y1, color, thickness=1):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    guard = (dx + dy + 2) * 4 + 8
    for _ in range(guard):
        if thickness <= 1:
            draw_pixel(img, x0, y0, color)
        else:
            _fill_circle_ish(img, x0, y0, thickness / 2.0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return img


def draw_rect(img, x, y, w, h, color, filled=True):
    if filled:
        for yy in range(int(y), int(y + h)):
            for xx in range(int(x), int(x + w)):
                draw_pixel(img, xx, yy, color)
    else:
        draw_line(img, x, y, x + w - 1, y, color)
        draw_line(img, x, y + h - 1, x + w - 1, y + h - 1, color)
        draw_line(img, x, y, x, y + h - 1, color)
        draw_line(img, x + w - 1, y, x + w - 1, y + h - 1, color)
    return img


def draw_ellipse(img, cx, cy, rx, ry, color, filled=True):
    if filled:
        for y in range(int(cy - ry), int(cy + ry) + 1):
            for x in range(int(cx - rx), int(cx + rx) + 1):
                nx = (x - cx) / float(rx) if rx else 0
                ny = (y - cy) / float(ry) if ry else 0
                if nx * nx + ny * ny <= 1.0:
                    draw_pixel(img, x, y, color)
    else:
        steps = max(16, int(max(rx, ry) * 4))
        pts = []
        for s in range(steps + 1):
            a = 2 * math.pi * s / steps
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
        draw_polyline(img, pts, color, 1)
    return img


def draw_polyline(img, pts, color, thickness=1):
    if len(pts) < 2:
        if pts:
            _fill_circle_ish(img, pts[0][0], pts[0][1], max(thickness / 2.0, 0.5), color)
        return img
    for i in range(len(pts) - 1):
        draw_line(img, int(pts[i][0]), int(pts[i][1]),
                  int(pts[i + 1][0]), int(pts[i + 1][1]), color, thickness)
    return img


def fill_polygon(img, pts, color):
    """Even-odd scanline polygon fill using pixel centers."""
    if len(pts) < 3:
        return img
    ys = [p[1] for p in pts]
    for y in range(int(math.floor(min(ys))), int(math.ceil(max(ys))) + 1):
        yc = y + 0.5
        xs = []
        n = len(pts)
        for i in range(n):
            x0, y0 = pts[i][0], pts[i][1]
            x1, y1 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
            if y0 == y1:
                continue
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                t = (yc - y0) / float(y1 - y0)
                xs.append(x0 + t * (x1 - x0))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            x_lo = int(math.ceil(xs[k] - 0.5))
            x_hi = int(math.floor(xs[k + 1] - 0.5))
            for x in range(max(0, x_lo), min(img.w - 1, x_hi) + 1):
                draw_pixel(img, x, y, color)
    return img


# --------------------------------------------------------- bitmap font ------
# 5x7 utility font (ASCII 32..126). Rows are 5-bit values, MSB = leftmost
# pixel, encoded as 7 base-32 characters per glyph. Used for burn-ins,
# slates, contact-sheet labels and credits; not intended for final typography.

_F5X7_SRC = {
    " ": "0000000", "!": "4444404", '"': "AA00000", "#": "0AVAVA0", "$": "4FKE5V4",
    "%": "PQ248BJ", "&": "69A4L9M", "'": "4400000", "(": "2488842", ")": "8422248",
    "*": "04LEV40", "+": "044V440", ",": "0000048", "-": "000V000", ".": "0000044",
    "/": "0G84210", "0": "EHJLPHE", "1": "4C4444E", "2": "EH1248V", "3": "V2421HE",
    "4": "26AIV22", "5": "VGU11HE", "6": "68GUHHE", "7": "V124888", "8": "EHHEHHE",
    "9": "EHHF12C", ":": "0040040", ";": "0040048", "<": "248G842", "=": "00V0V00",
    ">": "8421248", "?": "EH12404", "@": "EHNPPGE", "A": "4AHHVHH", "B": "UHHUHHU",
    "C": "EHGGGHE", "D": "SIHHHIS", "E": "VGGUGGV", "F": "VGGUGGG", "G": "EHGNHHF",
    "H": "HHHVHHH", "I": "E44444E", "J": "11111HE", "K": "HIKOKIH", "L": "GGGGGGV",
    "M": "HRLLHHH", "N": "HPLJHHH", "O": "EHHHHHE", "P": "UHHUGGG", "Q": "EHHHLID",
    "R": "UHHUKIH", "S": "FGGE11U", "T": "V444444", "U": "HHHHHHE", "V": "HHHHHA4",
    "W": "HHHLLRH", "X": "HHA4AHH", "Y": "HHA4444", "Z": "V1248GV", "[": "E88888E",
    "\\": "GG84211", "]": "E22222E", "^": "4AH0000", "_": "000000V", "`": "8400000",
    "a": "00E1FHF", "b": "GGUHHHU", "c": "00FGGGF", "d": "11FHHHF", "e": "00EHVGE",
    "f": "688S888", "g": "0FHHF1U", "h": "GGUHHHH", "i": "40C444E", "j": "206222S",
    "k": "GGIKOKI", "l": "C44444E", "m": "00QLLLL", "n": "00UHHHH", "o": "00EHHHE",
    "p": "0UHHUGG", "q": "0FHHF11", "r": "00MPGGG", "s": "00FGE1U", "t": "88S8886",
    "u": "00HHHHF", "v": "00HHHA4", "w": "00HLLLA", "x": "00HA4AH", "y": "0HHHF1U",
    "z": "00V248V", "{": "6884886", "|": "4444444", "}": "6224226", "~": "08L2000",
}
_B32 = "0123456789ABCDEFGHIJKLMNOPQRSTUV"
F5X7 = {k: [_B32.index(ch) for ch in v] for k, v in _F5X7_SRC.items()}


def measure_text(text, scale=1, tracking=0):
    if not text:
        return (0, 7 * scale)
    return ((len(str(text)) * (5 + tracking) - tracking) * scale, 7 * scale)


def draw_text(img, x, y, text, color=(255, 255, 255, 255), scale=1, tracking=0,
              outline=0, outline_color=(0, 0, 0, 255)):
    scale = max(1, int(scale))

    def run(ox, oy, col):
        cx = int(x) + ox
        for ch in str(text):
            glyph = F5X7.get(ch)
            if glyph is None:
                glyph = F5X7.get("?")
            if glyph is not None:
                for gy, row in enumerate(glyph):
                    for gx in range(5):
                        if row & (1 << (4 - gx)):
                            for sy in range(scale):
                                for sx in range(scale):
                                    draw_pixel(img, cx + gx * scale + sx,
                                               int(y) + oy + gy * scale + sy, col)
            cx += (5 + tracking) * scale
    if outline > 0:
        for d in range(1, outline * scale + 1):
            for dx, dy in ((-d, 0), (d, 0), (0, -d), (0, d),
                           (-d, -d), (d, -d), (-d, d), (d, d)):
                run(dx, dy, outline_color)
    run(0, 0, color)
    return img


def text_width(text, scale=1, tracking=0):
    return measure_text(text, scale, tracking)[0]


# --------------------------------------------------------- contact sheet ----

def contact_sheet(images, labels=None, columns=None, cell_w=160, cell_h=120,
                  bg=(24, 24, 24, 255), label_color=(255, 255, 255, 255), margin=6):
    n = len(images)
    require(n > 0, "invalid_input", "contact sheet needs at least one image")
    columns = max(1, min(columns or 6, n))
    rows = (n + columns - 1) // columns
    label_h = 11
    sheet = new_image(columns * (cell_w + margin) + margin,
                      rows * (cell_h + label_h + margin) + margin, bg)
    for i, img in enumerate(images):
        r, c = divmod(i, columns)
        x0 = margin + c * (cell_w + margin)
        y0 = margin + r * (cell_h + label_h + margin)
        k = min(cell_w / float(img.w), cell_h / float(img.h))
        tw, th = max(1, int(img.w * k)), max(1, int(img.h * k))
        thumb = resize(img, tw, th, "bilinear")
        ox = x0 + (cell_w - tw) // 2
        oy = y0 + (cell_h - th) // 2
        for yy in range(th):
            for xx in range(tw):
                j = (yy * tw + xx) * 4
                dst = sheet.get(ox + xx, oy + yy)
                sheet.set(ox + xx, oy + yy, _over(dst, (thumb.px[j], thumb.px[j + 1],
                                                        thumb.px[j + 2], thumb.px[j + 3])))
        label = str(labels[i]) if (labels is not None and i < len(labels)) else str(i + 1)
        draw_text(sheet, x0, y0 + cell_h, label[:max(4, (cell_w - 4) // 6)], label_color, 1, 1)
    return sheet


# ------------------------------------------------------------- PDF out ------

def pdf_from_images(images, page_w=512, page_h=None, bg=(255, 255, 255)):
    """Minimal PDF writer: one image per page, FlateDecode DeviceRGB."""
    require(images, "invalid_input", "PDF needs at least one page image")
    pages = []
    objs = {}
    for idx, img in enumerate(images):
        rgb = bytearray()
        px = img.px
        for i in range(0, len(px), 4):
            if px[i + 3] < 128:
                rgb += b"\xff\xff\xff"
            else:
                rgb += bytes((px[i], px[i + 1], px[i + 2]))
        zdata = zlib.compress(bytes(rgb), 6)
        img_id = 10 + idx * 3
        cont_id = img_id + 1
        page_id = img_id + 2
        pw = page_w
        ph = page_h or int(page_w * img.h / float(img.w))
        objs[img_id] = (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                        b"/Length %d >>\nstream\n" % (img.w, img.h, len(zdata))
                        + zdata + b"\nendstream")
        content = b"q %d 0 0 %d 0 0 cm /Im%d Do Q" % (pw, ph, idx)
        objs[cont_id] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
        pages.append((page_id, img_id, cont_id, pw, ph))
    kids = b" ".join(b"%d 0 R" % p[0] for p in pages)
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages))
    for page_id, img_id, cont_id, pw, ph in pages:
        objs[page_id] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                         b"/Resources << /XObject << /Im%d %d 0 R >> >> /Contents %d 0 R >>"
                         % (pw, ph, img_id, img_id, cont_id))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    xref_pos = len(out)
    maxn = max(objs)
    out += b"xref\n0 %d\n" % (maxn + 1)
    out += b"0000000000 65535 f \n"
    for n in range(1, maxn + 1):
        out += b"%010d 00000 n \n" % offsets.get(n, 0)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (maxn + 1, xref_pos)
    return bytes(out)


# ------------------------------------------------- SVG mini rasterizer ------

_PATH_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([+-]?(?:\d*\.\d+|\d+\.?)(?:[eE][+-]?\d+)?)")
_NUM = re.compile(r"[+-]?(?:\d*\.\d+|\d+\.?)(?:[eE][+-]?\d+)?")
_SVG_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff",
    "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080",
    "brown": "#a52a2a", "pink": "#ffc0cb", "navy": "#000080", "teal": "#008080",
    "olive": "#808000", "lime": "#00ff00", "aqua": "#00ffff", "fuchsia": "#ff00ff",
    "silver": "#c0c0c0", "maroon": "#800000", "darkgray": "#a9a9a9", "lightgray": "#d3d3d3",
    "none": None, "transparent": None,
}


def parse_color(value):
    if value is None:
        return None
    v = str(value).strip()
    if v.startswith("url("):
        return ("ref", v[4:].rstrip(")").lstrip("#"))
    if v in _SVG_COLORS:
        c = _SVG_COLORS[v]
        return parse_color(c) if c else None
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            return tuple(int(ch * 2, 16) for ch in h)
        if len(h) in (6, 8):
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = re.match(r"rgba?\(([^)]+)\)", v)
    if m:
        vals = []
        for p in [p.strip() for p in m.group(1).split(",")][:3]:
            vals.append(int(float(p[:-1]) * 255) if p.endswith("%") else int(float(p)))
        return tuple(vals[:3])
    return None


def parse_transform(text):
    m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    if not text:
        return m
    for name, args in re.findall(r"([\w]+)\s*\(([^)]*)\)", text):
        a = [float(v) for v in _NUM.findall(args)]
        if name == "translate" and len(a) >= 1:
            t = [1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0]
        elif name == "scale" and len(a) >= 1:
            sx = a[0]
            t = [sx, 0, 0, a[1] if len(a) > 1 else sx, 0, 0]
        elif name == "rotate" and a:
            ang = math.radians(a[0])
            t = [math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang), 0, 0]
            if len(a) == 3:
                m = _mat_mul(m, [1, 0, 0, 1, a[1], a[2]])
                m = _mat_mul(m, t)
                m = _mat_mul(m, [1, 0, 0, 1, -a[1], -a[2]])
                continue
        elif name == "matrix" and len(a) == 6:
            t = a
        else:
            t = [1, 0, 0, 1, 0, 0]
        m = _mat_mul(m, t)
    return m


def _mat_mul(a, b):
    return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]]


def _xf_point(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def _flatten_bezier(p0, p1, p2, p3, steps=16):
    pts = []
    for s in range(steps + 1):
        t = s / float(steps)
        mt = 1 - t
        pts.append([mt * mt * mt * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t * t * t * p3[0],
                    mt * mt * mt * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t * t * t * p3[1]])
    return pts


def _arc_to_beziers(x0, y0, rx, ry, phi, large, sweep, x1, y1):
    if rx == 0 or ry == 0:
        return [[(x0, y0), (x0, y0), (x0, y0), (x1, y1)]]
    phi = math.radians(phi % 360)
    cosp, sinp = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = cosp * dx2 + sinp * dy2
    y1p = -sinp * dx2 + cosp * dy2
    rx, ry = abs(rx), abs(ry)
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx
    cx = cosp * cxp - sinp * cyp + (x0 + x1) / 2.0
    cy = sinp * cxp + cosp * cyp + (y0 + y1) / 2.0

    def ang(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.sqrt((ux * ux + uy * uy) * (vx * vx + vy * vy))
        a = math.acos(max(-1.0, min(1.0, dot / n))) if n else 0.0
        return a if ux * vy - uy * vx >= 0 else -a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    nseg = max(1, int(math.ceil(abs(dth) / (math.pi / 2))))
    delta = dth / nseg
    t4 = 4 / 3.0 * math.tan(delta / 4)
    beziers = []
    th = th1
    px, py = x0, y0
    for _ in range(nseg):
        th2 = th + delta
        s1, c1 = math.sin(th), math.cos(th)
        s2, c2 = math.sin(th2), math.cos(th2)
        ex = cx + rx * c2
        ey = cy + ry * s2
        d1 = (-rx * s1, ry * c1)
        d2 = (-rx * s2, ry * c2)
        p1 = (px + t4 * (cosp * d1[0] - sinp * d1[1]), py + t4 * (sinp * d1[0] + cosp * d1[1]))
        p2 = (ex - t4 * (cosp * d2[0] - sinp * d2[1]), ey - t4 * (sinp * d2[0] + cosp * d2[1]))
        beziers.append([(px, py), p1, p2, (ex, ey)])
        px, py = ex, ey
        th = th2
    return beziers


def flatten_path(d):
    """Parse an SVG path 'd' into a list of subpath point lists."""
    tokens = []
    for m in _PATH_TOKEN.finditer(d or ""):
        cmd, num = m.group(1), m.group(2)
        tokens.append(cmd if cmd else float(num))
    polys = []
    cur = [0.0, 0.0]
    start = [0.0, 0.0]
    ctrl = None
    i = 0
    pending = []

    def nxt():
        nonlocal i
        v = tokens[i]
        i += 1
        return v

    def at_cmd():
        return i < len(tokens) and isinstance(tokens[i], str)

    cmd = None
    while i < len(tokens):
        if at_cmd():
            cmd = nxt()
        if cmd in ("Z", "z"):
            if pending:
                polys.append(pending)
                pending = []
            cur = list(start)
            continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = nxt(), nxt()
            if rel:
                x += cur[0]
                y += cur[1]
            if pending:
                polys.append(pending)
            pending = [[x, y]]
            cur = [x, y]
            start = [x, y]
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = nxt(), nxt()
            if rel:
                x += cur[0]
                y += cur[1]
            pending.append([x, y])
            cur = [x, y]
        elif c == "H":
            x = nxt()
            if rel:
                x += cur[0]
            pending.append([x, cur[1]])
            cur = [x, cur[1]]
        elif c == "V":
            y = nxt()
            if rel:
                y += cur[1]
            pending.append([cur[0], y])
            cur = [cur[0], y]
        elif c in "CTSQ":
            if c == "C":
                p = [nxt(), nxt(), nxt(), nxt(), nxt(), nxt()]
            elif c == "S":
                x2, y2, x, y = nxt(), nxt(), nxt(), nxt()
                rc = ctrl or cur
                p = [2 * cur[0] - rc[0], 2 * cur[1] - rc[1], x2, y2, x, y]
            elif c == "Q":
                x1, y1, x, y = nxt(), nxt(), nxt(), nxt()
                p = [cur[0] + 2 / 3.0 * (x1 - cur[0]), cur[1] + 2 / 3.0 * (y1 - cur[1]),
                     x + 2 / 3.0 * (x1 - x), y + 2 / 3.0 * (y1 - y), x, y]
            else:
                x, y = nxt(), nxt()
                rc = ctrl or cur
                x1 = 2 * cur[0] - rc[0]
                y1 = 2 * cur[1] - rc[1]
                p = [cur[0] + 2 / 3.0 * (x1 - cur[0]), cur[1] + 2 / 3.0 * (y1 - cur[1]),
                     x + 2 / 3.0 * (x1 - x), y + 2 / 3.0 * (y1 - y), x, y]
            if rel:
                for k in range(0, 6, 2):
                    p[k] += cur[0]
                    p[k + 1] += cur[1]
            ctrl = [p[0], p[1]] if c in "QS" else [p[2], p[3]]
            bez = _flatten_bezier(cur, [p[0], p[1]], [p[2], p[3]], [p[4], p[5]])
            pending.extend(bez[1:])
            cur = [p[4], p[5]]
        elif c == "A":
            rx, ry, rot, large, sweep, x, y = (nxt(), nxt(), nxt(), nxt(), nxt(), nxt(), nxt())
            if rel:
                x += cur[0]
                y += cur[1]
            for b in _arc_to_beziers(cur[0], cur[1], rx, ry, rot, large, sweep, x, y):
                pending.extend(_flatten_bezier(b[0], b[1], b[2], b[3])[1:])
            cur = [x, y]
        else:
            raise err("invalid_svg", "unsupported path command '%s'" % cmd)
    if pending:
        polys.append(pending)
    return polys


class _GradStop(object):
    def __init__(self, offset, color, opacity):
        self.offset = offset
        self.color = color
        self.opacity = opacity


def _num_attr(el, name, default):
    v = el.get(name, default)
    if str(v).endswith("%"):
        return float(_NUM.findall(v)[0]) / 100.0
    return float((_NUM.findall(v) or [default])[0])


def _svg_element_polys(el, t, warnings):
    def num(v, d=0.0):
        return float((_NUM.findall(v or "") or [d])[0])

    if t == "rect":
        x, y = num(el.get("x")), num(el.get("y"))
        w, h = num(el.get("width")), num(el.get("height"))
        rx = num(el.get("rx"), num(el.get("ry")))
        if w <= 0 or h <= 0:
            return []
        if rx > 0:
            rx = min(rx, w / 2.0, h / 2.0)
            pts = []
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([x + rx - rx * math.cos(a), y + rx - rx * math.sin(a)])
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([x + w - rx + rx * math.sin(a), y + rx - rx * math.cos(a)])
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([x + w - rx + rx * math.cos(a), y + h - rx + rx * math.sin(a)])
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([x + rx - rx * math.sin(a), y + h - rx + rx * math.cos(a)])
            return [pts]
        return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]]]
    if t == "circle":
        cx, cy, r = num(el.get("cx")), num(el.get("cy")), num(el.get("r"))
        if r <= 0:
            return []
        return [[[cx + r * math.cos(2 * math.pi * k / 24.0), cy + r * math.sin(2 * math.pi * k / 24.0)]
                 for k in range(24)]]
    if t == "ellipse":
        cx, cy = num(el.get("cx")), num(el.get("cy"))
        rx, ry = num(el.get("rx")), num(el.get("ry"))
        if rx <= 0 or ry <= 0:
            return []
        return [[[cx + rx * math.cos(2 * math.pi * k / 24.0), cy + ry * math.sin(2 * math.pi * k / 24.0)]
                 for k in range(24)]]
    if t == "line":
        return [[[num(el.get("x1")), num(el.get("y1"))], [num(el.get("x2")), num(el.get("y2"))]]]
    if t in ("polyline", "polygon"):
        nums = _NUM.findall(el.get("points", ""))
        pts = [[float(nums[i]), float(nums[i + 1])] for i in range(0, len(nums) - 1, 2)]
        return [pts]
    if t == "path":
        try:
            return flatten_path(el.get("d", ""))
        except (IndexError, ValueError) as e:
            warnings.append("unparseable path data: %s" % e)
            return []
    return []


def _stop_color(stops, t):
    if not stops:
        return (0, 0, 0, 255)

    def rgba(s):
        return (s.color[0], s.color[1], s.color[2], int(255 * s.opacity))
    if t <= stops[0].offset:
        return rgba(stops[0])
    if t >= stops[-1].offset:
        return rgba(stops[-1])
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        if a.offset <= t <= b.offset:
            f = (t - a.offset) / ((b.offset - a.offset) or 1.0)
            ca, cb = rgba(a), rgba(b)
            return tuple(int(ca[c] + (cb[c] - ca[c]) * f) for c in range(4))
    return rgba(stops[-1])


def _gradient_fill(img, polys, stops, axis, bbox, opacity):
    gx1, gy1, gx2, gy2 = axis
    x0, y0, x1, y1 = bbox
    bw, bh = (x1 - x0) or 1, (y1 - y0) or 1
    ax0, ay0 = x0 + gx1 * bw, y0 + gy1 * bh
    ax1, ay1 = x0 + gx2 * bw, y0 + gy2 * bh
    dx, dy = ax1 - ax0, ay1 - ay0
    L2 = dx * dx + dy * dy or 1.0
    for poly in polys:
        if len(poly) < 3:
            continue
        ys = [p[1] for p in poly]
        xs = [p[0] for p in poly]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            yc = y + 0.5
            xints = []
            n = len(poly)
            for i in range(n):
                x0p, y0p = poly[i]
                x1p, y1p = poly[(i + 1) % n]
                if y0p == y1p:
                    continue
                if (y0p <= yc < y1p) or (y1p <= yc < y0p):
                    t = (yc - y0p) / float(y1p - y0p)
                    xints.append(x0p + t * (x1p - x0p))
            xints.sort()
            for k in range(0, len(xints) - 1, 2):
                for x in range(int(xints[k]), int(xints[k + 1]) + 1):
                    t = ((x + 0.5 - ax0) * dx + (yc - ay0) * dy) / L2
                    col = _stop_color(stops, max(0.0, min(1.0, t)))
                    img.set(x, y, (col[0], col[1], col[2], int(col[3] * opacity)))


def rasterize_svg(text, width=None, height=None, bg=None, ss=2):
    """Rasterize a subset of SVG: shapes, paths, flat + gradient fills,
    strokes, transforms, opacity. Text is not rendered (warning issued).
    ss = supersampling factor for anti-aliased edges."""
    import xml.etree.ElementTree as ET
    warnings = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise err("invalid_svg", "SVG is not well-formed XML: %s" % e)
    vb = root.get("viewBox")

    def _px(v, default):
        m = re.match(r"([-\d.]+)", str(v).strip()) if v else None
        return float(m.group(1)) if m else default

    if vb:
        parts = [float(p) for p in _NUM.findall(vb)[:4]]
        w = int(width or (parts[2] or 640))
        h = int(height or (parts[3] or 480))
    else:
        w = int(width or _px(root.get("width"), 640))
        h = int(height or _px(root.get("height"), 480))
    require(w > 0 and h > 0, "invalid_svg", "SVG has no usable size")
    W, H = w * ss, h * ss
    img = new_image(W, H, bg if bg is not None else (0, 0, 0, 0))
    if vb and len(parts) == 4:
        kx = W / parts[2] if parts[2] else 1
        ky = H / parts[3] if parts[3] else 1
        base = [kx, 0, 0, ky, -parts[0] * kx, -parts[1] * ky]
    else:
        base = [W / float(w), 0, 0, H / float(h), 0, 0]
    grads = {}
    for g in root.iter():
        tag = g.tag.rsplit("}", 1)[-1]
        if tag in ("linearGradient", "radialGradient"):
            stops = []
            for st in g:
                if st.tag.rsplit("}", 1)[-1] != "stop":
                    continue
                off = float((_NUM.findall(st.get("offset", "0")) or ["0"])[0])
                stops.append(_GradStop(off, parse_color(st.get("stop-color", "#000000")) or (0, 0, 0),
                                       float((_NUM.findall(st.get("stop-opacity", "1")) or ["1"])[0])))
            stops.sort(key=lambda s: s.offset)
            grads[g.get("id")] = (tag, g, stops)

    def walk(el, mat, inherit_opacity):
        for child in el:
            t = child.tag.rsplit("}", 1)[-1]
            m = _mat_mul(mat, parse_transform(child.get("transform")))
            op = inherit_opacity * float((_NUM.findall(child.get("opacity", "1")) or ["1"])[0])
            if t in ("defs", "metadata", "title", "desc", "style"):
                continue
            if t == "g":
                walk(child, m, op)
                continue
            if t in ("text", "textPath", "tspan"):
                warnings.append("text element not rasterized (no font engine); "
                                "convert text to paths for pixel-exact output")
                continue
            if t in ("filter", "clipPath", "mask", "pattern", "symbol", "marker",
                     "linearGradient", "radialGradient", "stop", "animate"):
                continue
            polys = _svg_element_polys(child, t, warnings)
            if not polys:
                if t not in ("use", "image", "svg"):
                    warnings.append("unsupported or empty element <%s> skipped" % t)
                continue
            tf = [[_xf_point(m, p) for p in poly] for poly in polys]
            fill = parse_color(child.get("fill") if child.get("fill") is not None else "#000000")
            if fill is not None:
                fop = op * float((_NUM.findall(child.get("fill-opacity", "1")) or ["1"])[0])
                if isinstance(fill, tuple) and fill and fill[0] == "ref":
                    gdef = grads.get(fill[1])
                    if gdef is None:
                        warnings.append("missing gradient #%s" % fill[1])
                    else:
                        gtag, gel, stops = gdef
                        if gtag == "radialGradient":
                            warnings.append("radial gradients approximated as linear")
                        pts = [p for poly in tf for p in poly]
                        xs = [p[0] for p in pts] or [0]
                        ys = [p[1] for p in pts] or [0]
                        axis = (_num_attr(gel, "x1", "0%"), _num_attr(gel, "y1", "0%"),
                                _num_attr(gel, "x2", "100%"), _num_attr(gel, "y2", "0%"))
                        _gradient_fill(img, tf, stops, axis,
                                       (min(xs), min(ys), max(xs), max(ys)), fop)
                else:
                    color = (fill[0], fill[1], fill[2], int(255 * fop))
                    for poly in tf:
                        if len(poly) >= 3:
                            fill_polygon(img, poly, color)
            stroke = parse_color(child.get("stroke"))
            if stroke is not None:
                sw = float((_NUM.findall(child.get("stroke-width", "1")) or ["1"])[0])
                sop = op * float((_NUM.findall(child.get("stroke-opacity", "1")) or ["1"])[0])
                color = (stroke[0], stroke[1], stroke[2], int(255 * sop))
                for poly in tf:
                    draw_polyline(img, poly, color, max(1, int(round(sw * ss))))
    walk(root, base, 1.0)
    if ss > 1:
        img = resize(img, w, h, "box")
    return img, {"warnings": sorted(set(warnings)), "size": [w, h]}


# ------------------------------------------------ marching squares ----------
# (used by vector boolean: rasterize polygons -> binary mask -> contours)

def mask_from_polygons(w, h, polys):
    mask = bytearray(w * h)
    for poly in polys:
        if len(poly) < 3:
            continue
        ys = [p[1] for p in poly]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            yc = y + 0.5
            xs = []
            n = len(poly)
            for i in range(n):
                x0, y0 = poly[i][0], poly[i][1]
                x1, y1 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
                if y0 == y1:
                    continue
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    t = (yc - y0) / float(y1 - y0)
                    xs.append(x0 + t * (x1 - x0))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                for x in range(max(0, int(xs[k])), min(w - 1, int(xs[k + 1] - 0.001)) + 1):
                    mask[y * w + x] = 1
    return mask


def marching_squares(mask, w, h):
    """Trace contours of a binary mask -> list of loops of corner coordinates.

    Emits each filled pixel's four directed boundary edges, cancels interior
    edges, then chains the remaining edges into loops.
    """
    edges = set()
    for y in range(h):
        base = y * w
        for x in range(w):
            if not mask[base + x]:
                continue
            edges.add(((x, y), (x + 1, y)))
            edges.add(((x + 1, y), (x + 1, y + 1)))
            edges.add(((x + 1, y + 1), (x, y + 1)))
            edges.add(((x, y + 1), (x, y)))
    interior = set()
    for a, b in edges:
        if (b, a) in edges:
            interior.add((a, b))
    edges -= interior
    by_start = {}
    for a, b in edges:
        by_start.setdefault(a, []).append(b)
    loops = []
    visited = set()
    for s, e in edges:
        if (s, e) in visited:
            continue
        loop = [s]
        cur = e
        visited.add((s, e))
        guard = 0
        while cur != s and guard < 4 * (w + h + 8):
            loop.append(cur)
            nxt = None
            for cand in by_start.get(cur, ()):
                if (cur, cand) not in visited:
                    nxt = cand
                    break
            if nxt is None:
                break
            visited.add((cur, nxt))
            cur = nxt
            guard += 1
        if len(loop) >= 3:
            loops.append(loop)
    return loops


# ------------------------------------------------ raster painting helpers ---

def flood_fill(img, x, y, color, tolerance=24, contiguous=True):
    """Fill a region (Flat Color Separator core): contiguous flood from a
    seed over similar colors, or global replace when contiguous=False.
    Returns a new Image."""
    x, y = int(x), int(y)
    require(0 <= x < img.w and 0 <= y < img.h, "invalid_input",
            "fill seed (%d,%d) outside %dx%d" % (x, y, img.w, img.h))
    color = tuple(int(c) for c in color[:4]) if len(color) >= 4 else \
        tuple(int(c) for c in color) + (255,)
    out = img.clone()
    px = out.px
    target = (img.px[(y * img.w + x) * 4], img.px[(y * img.w + x) * 4 + 1],
              img.px[(y * img.w + x) * 4 + 2], img.px[(y * img.w + x) * 4 + 3])
    tol3 = tolerance * 3
    if not contiguous:
        for i in range(0, len(px), 4):
            c = (px[i], px[i + 1], px[i + 2], px[i + 3])
            if sum(abs(c[k] - target[k]) for k in range(4)) <= tol3:
                px[i:i + 4] = bytes(color)
        return out
    visited = bytearray(img.w * img.h)
    stack = [(x, y)]
    while stack:
        cx, cy = stack.pop()
        p = cy * img.w + cx
        if visited[p]:
            continue
        visited[p] = 1
        i = p * 4
        c = (px[i], px[i + 1], px[i + 2], px[i + 3])
        if sum(abs(c[k] - target[k]) for k in range(4)) > tol3:
            continue
        px[i:i + 4] = bytes(color)
        if cx > 0:
            stack.append((cx - 1, cy))
        if cx < img.w - 1:
            stack.append((cx + 1, cy))
        if cy > 0:
            stack.append((cx, cy - 1))
        if cy < img.h - 1:
            stack.append((cx, cy + 1))
    return out


def ink_stroke(img, pts, width=4, taper=0.65, smooth=2, color=(10, 10, 10, 255)):
    """Digital inking: smoothed (chaikin) polyline with tapered ends drawn as
    overlapping dab circles (stabilization comes from smoothing passes)."""
    require(len(pts) >= 2, "invalid_input", "ink stroke needs >= 2 points")
    smooth_pts = pts
    for _ in range(max(0, int(smooth))):
        smooth_pts = _chaikin_once(smooth_pts)
    n = len(smooth_pts)
    out = img.clone()

    def dab(x, y, r):
        ri = int(r + 0.5)
        for dy in range(-ri, ri + 1):
            for dx in range(-ri, ri + 1):
                if dx * dx + dy * dy <= r * r + 0.5:
                    draw_pixel(out, int(x) + dx, int(y) + dy, color)
    for i, (x, y) in enumerate(smooth_pts):
        t = min(i / max(1, n - 1), (n - 1 - i) / max(1.0, (n - 1) * taper)) \
            if taper > 0 else 1.0
        t = max(0.15, min(1.0, t))
        dab(x, y, max(0.5, width * t / 2.0))
    return out


def _chaikin_once(pts):
    if len(pts) < 3:
        return [list(p) for p in pts]
    out = [list(pts[0])]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        out.append([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25])
        out.append([a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75])
    out.append(list(pts[-1]))
    return out


def auto_shadow(img, dx=6, dy=8, color=(30, 40, 80), blur=2, strength=0.45):
    """Cel shading pass: offset the alpha silhouette, blur it, multiply onto
    the base (Raster Shading Builder core)."""
    sil = Image(img.w, img.h)
    for i in range(0, len(img.px), 4):
        if img.px[i + 3] > 128:
            a = int(strength * 255)
            sil.px[i], sil.px[i + 1], sil.px[i + 2], sil.px[i + 3] = \
                color[0], color[1], color[2], a
    moved = crop(sil, -int(dx), -int(dy), img.w, img.h)
    if blur:
        moved = blur_image(moved, blur)
    shadow = premultiply(moved)
    out = img.clone()
    for i in range(0, len(out.px), 4):
        sa = shadow.px[i + 3] / 255.0
        if sa > 0:
            out.px[i] = _clamp8(out.px[i] * (1 - sa) + shadow.px[i] * sa)
            out.px[i + 1] = _clamp8(out.px[i + 1] * (1 - sa) + shadow.px[i + 1] * sa)
            out.px[i + 2] = _clamp8(out.px[i + 2] * (1 - sa) + shadow.px[i + 2] * sa)
    return out


def tile_texture(img, texture, scale=1.0, opacity=0.5, mode="multiply",
                 mask=None):
    """Texture painting: tile a texture over the image (optionally limited to
    a mask's opaque region) with a blend mode."""
    tw = max(1, int(texture.w * scale))
    th = max(1, int(texture.h * scale))
    tex = resize(texture, tw, th, "nearest") if (tw != texture.w or th != texture.h) \
        else texture.clone()
    tiled = Image(img.w, img.h)
    for y in range(img.h):
        for x in range(img.w):
            t = tex.get(x % tw, y % th)
            tiled.set(x, y, (t[0], t[1], t[2], 255))
    if mask is not None:
        require(mask.w == img.w and mask.h == img.h, "invalid_input",
                "mask size mismatch")
        for y in range(img.h):
            for x in range(img.w):
                if mask.get(x, y)[3] <= 128:
                    tiled.set(x, y, (0, 0, 0, 0))
    return composite(img, tiled, mode=mode, opacity=opacity)


def draw_pixel_grid(img, cell=8, color=(60, 60, 80, 255)):
    """Pixel-grid overlay for pixel-art canvas setup guides."""
    for x in range(0, img.w, int(cell)):
        for y in range(img.h):
            draw_pixel(img, x, y, color)
    for y in range(0, img.h, int(cell)):
        for x in range(img.w):
            draw_pixel(img, x, y, color)
    return img


# ------------------------------------------- raster craft (cat 3) additions --

def brush_config(name, size=8, hardness=0.8, opacity=1.0, spacing=0.15,
                 flow_dynamics=False, size_dynamics=False, stamp="round",
                 smudge=0.0):
    """Brush Engine Configurator: validate and normalize a brush preset.
    stamp: round | square | textured."""
    require(1 <= int(size) <= 500, "invalid_input", "size 1-500 px")
    require(0.0 <= hardness <= 1.0 and 0.0 <= opacity <= 1.0, "invalid_input",
            "hardness/opacity in [0,1]")
    require(0.01 <= spacing <= 5.0, "invalid_input", "spacing 0.01-5.0")
    require(stamp in ("round", "square", "textured"), "invalid_input",
            "stamp must be round/square/textured")
    preset = {"name": str(name), "size": int(size), "hardness": round(hardness, 3),
              "opacity": round(opacity, 3), "spacing": round(spacing, 3),
              "size_dynamics": bool(size_dynamics),
              "flow_dynamics": bool(flow_dynamics), "stamp": stamp,
              "smudge": round(float(smudge), 3),
              "stamps_per_px": round(1.0 / spacing, 2)}
    return preset


def brush_library(presets, categories=None):
    """Brush Library Curator: dedupe, categorize, and audit a brush set."""
    seen = {}
    rows = []
    for p in presets or []:
        name = str(p.get("name", "")).strip()
        key = (p.get("size"), p.get("stamp"), p.get("hardness"))
        dup_of = seen.get(key)
        seen[key] = name
        cat = None
        for c, keys in (categories or {}).items():
            if any(k in name.lower() for k in keys):
                cat = c
                break
        rows.append({"name": name or "(unnamed)", "size": p.get("size"),
                     "stamp": p.get("stamp"), "category": cat or "uncategorized",
                     "duplicate_of": dup_of if dup_of not in (None, name) else None})
    return {"brushes": rows, "count": len(rows),
            "duplicates": [r for r in rows if r["duplicate_of"]],
            "uncategorized": sum(1 for r in rows if r["category"] == "uncategorized"),
            "rule": "rename or merge duplicates; one category per brush"}


def flat_color_separate(img, min_area=4):
    """Flat Color Separator: split a flat-colored image into one mask per
    connected color region (flood from unvisited pixels)."""
    w, h = img.w, img.h
    px = img.px
    visited = bytearray(w * h)
    regions = []
    for start in range(w * h):
        if visited[start]:
            continue
        r0, g0, b0, a0 = px[start * 4:start * 4 + 4]
        stack = [start]
        visited[start] = 1
        cells = []
        while stack:
            idx = stack.pop()
            cells.append(idx)
            x, y = idx % w, idx // w
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if not visited[j]:
                        r, g, b, a = px[j * 4:j * 4 + 4]
                        if (r, g, b, a) == (r0, g0, b0, a0):
                            visited[j] = 1
                            stack.append(j)
        if len(cells) >= min_area:
            mask = new_image(w, h, (0, 0, 0, 0))
            for idx in cells:
                mask.px[idx * 4:idx * 4 + 4] = (255, 255, 255, 255)
            regions.append({"color": (r0, g0, b0, a0), "pixels": len(cells),
                            "mask": mask})
    regions.sort(key=lambda r: -r["pixels"])
    return {"regions": regions, "count": len(regions),
            "coverage": round(sum(r["pixels"] for r in regions) /
                              float(w * h), 4)}


def raster_cleanup(img, speckle_area=2, line_darken=0, gray_floor=200):
    """Raster Cleanup Technician: remove isolated specks, drop pencil-gray
    haze below the floor, optionally darken surviving lines."""
    out = img.clone()
    w, h = img.w, img.h
    px = out.px
    # speckle removal: dark pixels whose 8-neighbourhood is all light
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            i = (y * w + x) * 4
            if luma8(px[i], px[i + 1], px[i + 2]) < 128 and px[i + 3] > 0:
                dark_n = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx or dy:
                            j = ((y + dy) * w + (x + dx)) * 4
                            if luma8(px[j], px[j + 1], px[j + 2]) < 128 and \
                                    px[j + 3] > 0:
                                dark_n += 1
                if dark_n == 0:
                    px[i:i + 4] = (255, 255, 255, 255)
    # pencil-gray haze removal
    for i in range(0, len(px), 4):
        if 0 < px[i + 3] < 255 and luma8(px[i], px[i + 1], px[i + 2]) >= gray_floor:
            px[i + 3] = 0
    if line_darken:
        f_ = max(0.0, min(1.0, (255 - int(line_darken)) / 255.0))
        for i in range(0, len(px), 4):
            if px[i + 3] > 0 and luma8(px[i], px[i + 1], px[i + 2]) < 128:
                px[i] = int(px[i] * f_)
                px[i + 1] = int(px[i + 1] * f_)
                px[i + 2] = int(px[i + 2] * f_)
    return out


def downsample_mip(img, target_w=None, levels=4, sharpen=True):
    """High-Resolution Downsample Specialist: step-halving mip chain with a
    gentle unsharp after each halving to keep edges crisp."""
    chain = [img]
    cur = img
    n = levels if target_w is None else 999
    while len(chain) <= n and (target_w is None or cur.w // 2 >= target_w):
        if cur.w <= 2 or cur.h <= 2:
            break
        cur = resize(cur, cur.w // 2, cur.h // 2, method="box")
        if sharpen:
            blurred = blur_image(cur, 1)
            buf = bytearray(len(cur.px))
            for i in range(0, len(cur.px), 4):
                for c in range(4):
                    buf[i + c] = min(255, max(0, cur.px[i + c] * 2 -
                                              blurred.px[i + c]))
            cur = Image(cur.w, cur.h, bytes(buf))
        chain.append(cur)
        if target_w is not None and cur.w <= target_w:
            break
    if target_w is not None:
        last = chain[-1]
        if last.w != target_w:
            chain.append(resize(last, target_w,
                                int(last.h * target_w / last.w), "bilinear"))
    return chain


def raster_batch(files, operation, out_dir, args=None):
    """Raster Batch Processor: apply one operation to many files, structured
    per-file results, continue past failures."""
    from pathlib import Path as P
    ops = {"resize": lambda im, a: resize(im, a.get("w", im.w // 2),
                                          a.get("h", int(im.h * (a.get("w", im.w // 2) / im.w))),
                                          a.get("method", "bilinear")),
           "cleanup": lambda im, a: raster_cleanup(im, **(a or {})),
           "flatten": lambda im, a: flatten(im, (a or {}).get("bg", (255, 255, 255))),
           "invert": lambda im, a: op_invert(im),
           "crop_margin": lambda im, a: _crop_alpha_margin(im, a.get("margin", 0))}
    require(operation in ops, "invalid_input",
            "operation must be one of %s" % ", ".join(sorted(ops)))
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in files or []:
        try:
            img, _ = load_image(str(f))
            res = ops[operation](img, args or {})
            dest = out / (P(str(f)).stem + "-" + operation + ".png")
            save_image(str(dest), res)
            rows.append({"file": P(str(f)).name, "ok": True, "out": str(dest),
                         "size": [res.w, res.h]})
        except Exception as e:
            rows.append({"file": P(str(f)).name, "ok": False,
                         "error": getattr(e, "code", "failed"),
                         "message": str(e)[:120]})
    return {"operation": operation, "processed": sum(1 for r in rows if r["ok"]),
            "failed": sum(1 for r in rows if not r["ok"]), "rows": rows}


def _crop_alpha_margin(img, margin=0):
    box = _alpha_bbox_local(img)
    if box == [0, 0, img.w, img.h]:
        return img
    x, y, w, h = box
    x = max(0, x - margin)
    y = max(0, y - margin)
    w = min(img.w - x, w + 2 * margin)
    h = min(img.h - y, h + 2 * margin)
    return crop(img, x, y, w, h)


def _alpha_bbox_local(img, threshold=1):
    w, h = img.w, img.h
    x0, y0, x1, y1 = w, h, 0, 0
    found = False
    px = img.px
    for y in range(h):
        base = y * w
        for x in range(w):
            if px[(base + x) * 4 + 3] >= threshold:
                found = True
                if x < x0:
                    x0 = x
                if x + 1 > x1:
                    x1 = x + 1
                if y < y0:
                    y0 = y
                if y + 1 > y1:
                    y1 = y + 1
    if not found:
        return [0, 0, w, h]
    return [x0, y0, x1 - x0, y1 - y0]


# ------------------------------------------ compositing (cat 6) additions ---

def layer_stack(layers, canvas=None):
    """Layer Stack Architect: validate/order a layer list (name, mode,
    opacity, visibility) and report the composite plan."""
    modes = ("normal", "multiply", "screen", "overlay", "add", "darken",
             "lighten")
    rows = []
    issues = []
    for i, lyr in enumerate(layers or []):
        mode = str(lyr.get("mode", "normal")).lower()
        opacity = float(lyr.get("opacity", 1.0))
        name = str(lyr.get("name", "layer-%d" % i))
        if mode not in modes:
            issues.append({"layer": name, "issue": "unknown_mode", "mode": mode})
            mode = "normal"
        if not 0.0 <= opacity <= 1.0:
            issues.append({"layer": name, "issue": "opacity_range"})
            opacity = min(1.0, max(0.0, opacity))
        rows.append({"name": name, "mode": mode, "opacity": round(opacity, 3),
                     "visible": bool(lyr.get("visible", True)),
                     "order": i})
    rows.reverse()  # paint bottom-up like a GUI stack
    return {"layers": rows, "canvas": canvas or
            (rows and [layers[0].get("w", 0), layers[0].get("h", 0)] or [0, 0]),
            "issues": issues, "modes_supported": list(modes),
            "composite_plan": "bottom-up: normal first, then blend modes in order"}


def mask_workflow(img, kind="luminance", threshold=128, invert=False,
                  feather=0):
    """Mask Workflow Designer: derive a mask (luminance/alpha/polygon) and
    describe a non-destructive apply plan."""
    w, h = img.w, img.h
    if kind == "luminance":
        mask = new_image(w, h, (0, 0, 0, 255))
        for i in range(0, len(img.px), 4):
            v = 255 if luma8(img.px[i], img.px[i + 1], img.px[i + 2]) >= threshold else 0
            if invert:
                v = 255 - v
            mask.px[i:i + 3] = (v, v, v)
    elif kind == "alpha":
        mask = new_image(w, h, (0, 0, 0, 255))
        for i in range(0, len(img.px), 4):
            v = img.px[i + 3]
            if invert:
                v = 255 - v
            mask.px[i:i + 3] = (v, v, v)
    elif kind == "polygon":
        raise err("invalid_input",
                  "use mask_from_polygons(w, h, polys) for polygon masks")
    else:
        raise err("invalid_input", "kind must be luminance or alpha")
    if feather:
        mask = blur_image(mask, feather)
    return {"mask": mask, "kind": kind, "threshold": threshold,
            "invert": bool(invert), "feather": int(feather),
            "apply_plan": ["duplicate the layer", "apply mask as layer alpha",
                           "keep the original mask editable"]}


def blend_mode_audit(stack, consumer="web"):
    """Blend Mode Auditor: flag modes the target consumer cannot show."""
    safe = {"web": ("normal", "multiply", "screen"),
            "game-engine": ("normal", "multiply", "add"),
            "print": ("normal", "multiply")}
    allowed = safe.get(consumer, ("normal",))
    rows = []
    for lyr in stack or []:
        mode = str(lyr.get("mode", "normal")).lower()
        rows.append({"layer": lyr.get("name", "?"), "mode": mode,
                     "safe": mode in allowed,
                     "action": None if mode in allowed else
                     "flatten to normal before export to " + consumer})
    return {"consumer": consumer, "allowed": list(allowed), "rows": rows,
            "unsafe": sum(1 for r in rows if not r["safe"])}


def color_script(shots):
    """Color Script Builder: palette progression across the beat board."""
    script = []
    for i, s in enumerate(shots or []):
        mood = str(s.get("mood", "neutral")).lower()
        base = {"happy": (255, 214, 120), "neutral": (190, 200, 210),
                "tense": (110, 90, 130), "sad": (95, 120, 160),
                "angry": (200, 90, 80), "night": (60, 70, 110),
                "warm": (255, 180, 130)}.get(mood, (190, 200, 210))
        accent = tuple(min(255, c + 60) for c in base)
        script.append({"shot": s.get("shot", i + 1), "mood": mood,
                       "base": list(base), "accent": list(accent),
                       "key_light": str(s.get("key_light", "frontal")),
                       "note": "keep 60/30/10 base/mid/accent split"})
    return {"script": script, "shots": len(script),
            "progression": "review adjacent shots for unintentional jumps"}


def color_profile(img, profile="sRGB"):
    """Color Profile Manager: report the assumed profile and gamma handling;
    all IO in this tool is 8-bit sRGB by convention."""
    st = stats(img)
    gamma_mismatch = False
    # a mid-gray ramp check: if mid pixels cluster near 188 instead of ~128,
    # the data probably arrived in linear light
    mid = [img.px[i] for i in range(0, len(img.px), 28)
           if 150 <= img.px[i] <= 230]
    if len(mid) >= 10 and sum(mid) / len(mid) > 180:
        gamma_mismatch = True
    return {"assumed_profile": profile, "bit_depth": 8,
            "gamma": 2.2, "gamma_mismatch_suspected": gamma_mismatch,
            "mean_rgb": [round(v, 1) for v in st["mean"]],
            "advice": "all saved PNGs are sRGB; convert before wide-gamut work"}


def compositing_preflight(layers):
    """Compositing Preflight Operator: size/alpha/colorspace checks before
    a composite is attempted."""
    issues = []
    if not layers:
        raise err("invalid_input", "no layers given")
    w, h = layers[0]["image"].w, layers[0]["image"].h
    for i, lyr in enumerate(layers):
        img = lyr["image"]
        if (img.w, img.h) != (w, h):
            issues.append({"layer": i, "issue": "size_mismatch",
                           "size": [img.w, img.h], "expected": [w, h]})
        a_min = min(img.px[3::4]) if img.px else 255
        if lyr.get("premultiplied") and a_min < 255:
            issues.append({"layer": i, "issue": "premultiplied_over_opaque"})
    return {"canvas": [w, h], "layers": len(layers), "issues": issues,
            "colorspace": "sRGB 8-bit",
            "verdict": "ready" if not issues else "%d issue(s)" % len(issues)}
