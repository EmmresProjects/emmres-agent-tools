#!/usr/bin/env python3
"""Media I/O layer for the Media Intelligence Workbench Skill Tool.

Responsibilities
----------------
* Format detection from magic bytes (no external tools required).
* Container / stream probing: images, WAV, MP4-family video, common audio.
* Safe decoding of images (native PNG / BMP / PNM decoders) and WAV audio.
* Ingestion safety scanning (decompression bombs, embedded executables,
  macro or script payloads, trailing-data polyglots).
* Content hashing, sidecar discovery and cross-file asset graphing.

This module is import-only. It is loaded by tools-miw.py and by the
metric modules; it never writes to the filesystem.

Optional acceleration: if Pillow is importable, JPEG / GIF / TIFF / WEBP
images can also be decoded. Every other path below is pure standard library.
"""

from __future__ import annotations

import binascii
import hashlib
import os
import struct
import zlib

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class MediaError(Exception):
    """Base error carrying a stable machine-readable code."""

    code = "media-error"

    def __init__(self, message, hint=None, code=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code:
            self.code = code

    def to_dict(self):
        return {"code": self.code, "message": self.message, "hint": self.hint}


class UnsupportedFormat(MediaError):
    code = "unsupported-format"


class DecodeError(MediaError):
    code = "decode-failed"


class LimitExceeded(MediaError):
    code = "limit-exceeded"


# --------------------------------------------------------------------------
# Optional dependency probe
# --------------------------------------------------------------------------


def optional_dependencies():
    deps = {}
    try:  # pragma: no cover - depends on host
        import PIL  # noqa: F401
        from PIL import Image  # noqa: F401

        deps["pillow"] = True
    except Exception:
        deps["pillow"] = False
    try:  # pragma: no cover
        import numpy  # noqa: F401

        deps["numpy"] = True
    except Exception:
        deps["numpy"] = False
    deps["ffmpeg"] = _which("ffmpeg")
    deps["ffprobe"] = _which("ffprobe")
    return deps


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

_HASH_CHUNK = 1 << 20


def sha256_file(path, max_bytes=None):
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if max_bytes is not None and read > max_bytes:
                raise LimitExceeded(
                    "file larger than hashing limit: %s > %s bytes" % (path, max_bytes),
                    hint="raise limits.max_file_bytes in the config file",
                )
            h.update(chunk)
    return h.hexdigest()


def quick_signature(path, size=64):
    with open(path, "rb") as fh:
        head = fh.read(size)
    return binascii.hexlify(head).decode("ascii")


# --------------------------------------------------------------------------
# Magic-byte detection
# --------------------------------------------------------------------------

IMAGE_FORMATS = {"png", "jpeg", "gif", "webp", "bmp", "tiff", "pnm"}
AUDIO_FORMATS = {"wav", "mp3", "flac", "ogg", "m4a", "aiff", "opus"}
VIDEO_FORMATS = {"mp4", "mov", "mkv", "webm", "avi", "m4v", "ts"}
TEXT_FORMATS = {"srt", "vtt", "txt", "json", "csv", "md"}


def detect_format(path):
    """Return a dict describing the detected container of *path*."""
    with open(path, "rb") as fh:
        head = fh.read(4100)
    if not head:
        raise DecodeError("empty file", hint="re-upload or re-export the file")

    def res(fmt, kind, container, confidence="certain", notes=None):
        return {
            "format": fmt,
            "kind": kind,
            "container": container,
            "confidence": confidence,
            "notes": notes or [],
        }

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return res("png", "image", "png")
    if head.startswith(b"\xff\xd8\xff"):
        return res("jpeg", "image", "jpeg")
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return res("gif", "image", "gif")
    if head[:4] == b"RIFF":
        riff = head[8:12]
        if riff == b"WAVE":
            return res("wav", "audio", "wav/riff")
        if riff == b"AVI ":
            return res("avi", "video", "avi/riff")
        if riff == b"WEBP":
            return res("webp", "image", "riff/webp")
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return res("tiff", "image", "tiff")
    if head[:2] == b"BM":
        return res("bmp", "image", "bmp")
    if head[:2] in (b"P5", b"P6", b"P2", b"P3"):
        return res("pnm", "image", "pnm")
    if head[4:8] == b"ftyp":
        brand = head[8:12].decode("latin-1", "replace")
        fmt = "mov" if brand in ("qt  ",) else ("m4v" if brand.startswith("M4V") else "mp4")
        if brand.startswith("M4A"):
            return res("m4a", "audio", "iso-bmff")
        return res(fmt, "video", "iso-bmff", notes=["brand=%s" % brand.strip()])
    if head[:4] == b"\x1aE\xdf\xa3":
        return res("webm" if b"webm" in head else "mkv", "video", "matroska/ebml",
                   confidence="likely")
    if head.startswith(b"fLaC"):
        return res("flac", "audio", "flac")
    if head.startswith(b"OggS"):
        return res("ogg", "audio", "ogg")
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return res("mp3", "audio", "mpeg-audio")
    if head.startswith(b"FORM") and head[8:12] == b"AIFF":
        return res("aiff", "audio", "aiff")
    if head[:4] == b"PK\x03\x04":
        return res("zip", "archive", "zip")
    if head[:5] == b"ustar" or head[257:262] == b"ustar":
        return res("tar", "archive", "tar")
    if head.startswith(b"%PDF"):
        return res("pdf", "document", "pdf")

    # text-ish formats
    sample = head.decode("utf-8", "replace")
    stripped = sample.lstrip("\ufeff \t\r\n")
    if stripped.upper().startswith("WEBVTT"):
        return res("vtt", "text", "webvtt")
    if "-->" in sample and any(ch.isdigit() for ch in sample):
        return res("srt", "text", "subtitle", confidence="likely")
    if stripped.startswith("{") or stripped.startswith("["):
        return res("json", "text", "json", confidence="likely")
    printable = sum(1 for c in sample[:1000] if c.isprintable() or c in "\r\n\t")
    if printable >= max(1, int(len(sample[:1000]) * 0.85)):
        return res("txt", "text", "plain", confidence="likely")
    return res("unknown", "unknown", "unknown", confidence="none",
               notes=["magic bytes not recognised"])


def classify(path):
    info = detect_format(path)
    info["supported_for_analysis"] = _supported(info["format"])
    return info


def _supported(fmt):
    if fmt in ("png", "bmp", "pnm"):
        return {"decode": True, "decoder": "native"}
    if fmt in ("jpeg", "gif", "tiff", "webp"):
        return {"decode": optional_dependencies()["pillow"], "decoder": "pillow"}
    if fmt == "wav":
        return {"decode": True, "decoder": "native"}
    return {"decode": False, "decoder": None}


# --------------------------------------------------------------------------
# Image probing (header only, no pixel decode)
# --------------------------------------------------------------------------


def _png_info(fh):
    fh.seek(0)
    data = fh.read(33)
    if len(data) < 33:
        raise DecodeError("truncated PNG header")
    width, height, depth, ctype = struct.unpack(">IIBB", data[16:26])
    interlace = data[28]
    color_models = {0: "gray", 2: "rgb", 3: "palette", 4: "gray-alpha", 6: "rgba"}
    return {
        "width": width,
        "height": height,
        "bit_depth": depth,
        "color_model": color_models.get(ctype, "unknown"),
        "channels": {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 0),
        "interlaced": bool(interlace),
        "has_alpha": ctype in (4, 6),
    }


def _gif_info(path):
    with open(path, "rb") as fh:
        data = fh.read(13)
    if len(data) < 13:
        raise DecodeError("truncated GIF header")
    width, height = struct.unpack("<HH", data[6:10])
    return {"width": width, "height": height, "bit_depth": (data[10] & 0x7) + 1,
            "color_model": "palette", "channels": 3, "has_alpha": True,
            "animated": _gif_is_animated(path)}


def _gif_is_animated(path):
    with open(path, "rb") as fh:
        blob = fh.read(min(os.path.getsize(path), 1 << 20))
    return blob.count(b"\x21\xf9\x04") > 1


def _bmp_info(path):
    with open(path, "rb") as fh:
        data = fh.read(54)
    if len(data) < 26:
        raise DecodeError("truncated BMP header")
    bpp = struct.unpack("<H", data[28:30])[0] if len(data) >= 30 else 24
    width, height = struct.unpack("<ii", data[18:26])
    return {"width": abs(width), "height": abs(height), "bit_depth": bpp,
            "color_model": "rgb", "channels": 3, "top_down": height < 0}


def _pnm_info(path):
    with open(path, "rb") as fh:
        head = fh.read(64)
    parts = []
    for line in head.split(b"\n"):
        line = line.split(b"#")[0].strip()
        if line:
            parts.extend(line.split())
        if len(parts) >= 4:
            break
    if len(parts) < 4:
        raise DecodeError("truncated or malformed PNM header")
    magic = parts[0].decode("latin-1", "replace")
    width, height, maxval = int(parts[1]), int(parts[2]), int(parts[3])
    return {"width": width, "height": height, "bit_depth": 8 if maxval < 256 else 16,
            "color_model": "rgb" if magic == "P6" else "gray",
            "channels": 3 if magic == "P6" else 1}


def _jpeg_info(path):
    with open(path, "rb") as fh:
        data = fh.read()
    i = 2
    n = len(data)
    while i < n - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            precision = data[i + 4]
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            comps = data[i + 9]
            progressive = marker in (0xC2, 0xC6, 0xCA, 0xCE)
            return {"width": width, "height": height, "bit_depth": precision,
                    "color_model": {1: "gray", 3: "ycbcr", 4: "cmyk"}.get(comps, "unknown"),
                    "channels": comps, "progressive": progressive}
        i += 2 + seglen
    raise DecodeError("no JPEG frame header found", hint="file may be truncated")


def _webp_info(path):
    with open(path, "rb") as fh:
        data = fh.read(40)
    if data[12:16] == b"VP8X":
        flags = data[20]
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return {"width": width, "height": height, "bit_depth": 8,
                "color_model": "rgb", "channels": 4 if flags & 0x10 else 3,
                "has_alpha": bool(flags & 0x10), "animated": bool(flags & 0x02)}
    if data[12:16] == b"VP8L":
        b = data[21:25]
        bits = int.from_bytes(b, "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return {"width": width, "height": height, "bit_depth": 8,
                "color_model": "rgb", "channels": 4, "has_alpha": True}
    if data[12:16] == b"VP8 ":
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return {"width": width, "height": height, "bit_depth": 8,
                "color_model": "yuv", "channels": 3}
    raise DecodeError("unrecognised WEBP bitstream")


def _tiff_info(path):
    deps = optional_dependencies()
    if not deps["pillow"]:
        raise UnsupportedFormat(
            "TIFF probing needs Pillow",
            hint="pip install pillow, or convert to PNG",
        )
    from PIL import Image  # noqa: WPS433

    with Image.open(path) as im:
        return {"width": im.width, "height": im.height, "bit_depth": 8,
                "color_model": im.mode, "channels": len(im.getbands())}


def probe_image(path):
    fmt = detect_format(path)["format"]
    getattr_map = {
        "png": lambda: _png_info(open(path, "rb")),
        "gif": lambda: _gif_info(path),
        "bmp": lambda: _bmp_info(path),
        "pnm": lambda: _pnm_info(path),
        "jpeg": lambda: _jpeg_info(path),
        "webp": lambda: _webp_info(path),
        "tiff": lambda: _tiff_info(path),
    }
    fn = getattr_map.get(fmt)
    if fn is None:
        raise UnsupportedFormat("not an image format: %s" % fmt)
    info = fn()
    info["format"] = fmt
    info["kind"] = "image"
    info["megapixels"] = round(info.get("width", 0) * info.get("height", 0) / 1e6, 3)
    info["aspect_ratio"] = (
        round(info["width"] / info["height"], 4) if info.get("height") else None
    )
    return info


# --------------------------------------------------------------------------
# Audio probing
# --------------------------------------------------------------------------


def _wav_info(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise DecodeError("not a RIFF/WAVE file")
    pos = 12
    fmt = None
    data_size = None
    truncated_chunk = False
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        csize = struct.unpack("<I", blob[pos + 4:pos + 8])[0]
        body = blob[pos + 8:pos + 8 + csize]
        if len(body) < csize:
            truncated_chunk = True
        if cid == b"fmt " and len(body) >= 16:
            audio_format, channels, rate, byte_rate, block_align, bits = struct.unpack(
                "<HHIIHH", body[:16])
            fmt = {
                "codec": {1: "pcm", 3: "pcm-float", 6: "alaw", 7: "mulaw",
                          0xFFFE: "extensible"}.get(audio_format, "0x%x" % audio_format),
                "channels": channels,
                "sample_rate": rate,
                "byte_rate": byte_rate,
                "bit_depth": bits,
            }
        elif cid == b"data":
            data_size = min(csize, len(body))
        pos += 8 + csize + (csize & 1)
    if fmt is None:
        raise DecodeError("WAV file has no fmt chunk")
    frames = (data_size or 0) // max(1, fmt["channels"] * max(1, fmt["bit_depth"] // 8))
    out = dict(fmt)
    out.update({
        "format": "wav",
        "kind": "audio",
        "duration_s": round(frames / fmt["sample_rate"], 4) if fmt["sample_rate"] else 0,
        "frames": frames,
        "data_bytes": data_size,
        "bitrate_kbps": round((fmt["byte_rate"] * 8) / 1000, 1) if fmt.get("byte_rate") else None,
        "truncated": truncated_chunk,
    })
    return out


def _mp3_info(path):
    with open(path, "rb") as fh:
        data = fh.read(1 << 16)
    off = 0
    if data[:3] == b"ID3":
        size = ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14
                | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
        off = 10 + size
    bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    rates = {0: [44100, 48000, 32000], 2: [22050, 24000, 16000], 3: [11025, 12000, 8000]}
    while off + 4 <= len(data):
        if data[off] == 0xFF and (data[off + 1] & 0xE0) == 0xE0:
            h = struct.unpack(">I", data[off:off + 4])[0]
            ver = (h >> 19) & 3
            layer = (h >> 17) & 3
            br_idx = (h >> 12) & 0xF
            sr_idx = (h >> 10) & 3
            if ver != 1 and layer and br_idx and sr_idx != 3:
                return {
                    "format": "mp3", "kind": "audio",
                    "codec": "mpeg-1-layer3" if ver == 3 else "mpeg-2-layer3",
                    "channels": 1 if ((h >> 6) & 3) == 3 else 2,
                    "sample_rate": rates.get(ver, [44100])[sr_idx],
                    "bit_depth": None,
                    "bitrate_kbps": bitrates[br_idx],
                    "duration_s": None,
                    "duration_note": "streamed estimate needs full frame walk; "
                                     "use ffprobe for exact duration",
                }
            off += 1
        else:
            off += 1
    raise DecodeError("no MPEG audio frame header found")


def _flac_info(path):
    with open(path, "rb") as fh:
        data = fh.read(4096)
    idx = data.find(b"STREAMINFO")
    if idx < 0:
        raise DecodeError("FLAC STREAMINFO block missing")
    body = data[idx + 4:idx + 4 + 34]
    if len(body) < 34:
        raise DecodeError("truncated FLAC STREAMINFO")
    bits = int.from_bytes(body[10:18], "big")
    rate = (bits >> 44) & 0xFFFFF
    channels = ((bits >> 41) & 0x7) + 1
    depth = ((bits >> 36) & 0x1F) + 1
    total = bits & 0xFFFFFFFFF
    return {"format": "flac", "kind": "audio", "codec": "flac", "channels": channels,
            "sample_rate": rate, "bit_depth": depth,
            "duration_s": round(total / rate, 4) if rate else None}


def probe_audio(path):
    fmt = detect_format(path)["format"]
    if fmt == "wav":
        return _wav_info(path)
    if fmt == "mp3":
        return _mp3_info(path)
    if fmt == "flac":
        return _flac_info(path)
    if fmt in ("ogg", "opus"):
        return {"format": fmt, "kind": "audio", "codec": fmt,
                "container": "ogg",
                "notes": ["ogg duration requires page walk; not computed by the "
                          "stdlib probe"]}
    if fmt == "m4a":
        info = probe_video(path)
        info["kind"] = "audio"
        return info
    raise UnsupportedFormat("audio probing not implemented for %s" % fmt)


# --------------------------------------------------------------------------
# MP4 / ISO-BMFF video probing
# --------------------------------------------------------------------------

_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}


def _iter_boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        btype = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield btype, pos + header, pos + size
        pos += size


def _parse_mp4(path):
    with open(path, "rb") as fh:
        data = fh.read()
    result = {"format": "mp4", "kind": "video", "container": "iso-bmff",
              "tracks": [], "duration_s": None, "brand": None}
    if data[4:8] == b"ftyp":
        result["brand"] = data[8:12].decode("latin-1", "replace").strip()

    def walk(bstart, bend, depth, track):
        for btype, s, e in _iter_boxes(data, bstart, bend):
            if btype == b"mvhd":
                version = data[s]
                if version == 0:
                    timescale = struct.unpack(">I", data[s + 12:s + 16])[0]
                    dur = struct.unpack(">I", data[s + 16:s + 20])[0]
                else:
                    timescale = struct.unpack(">I", data[s + 20:s + 24])[0]
                    dur = struct.unpack(">Q", data[s + 24:s + 32])[0]
                if timescale:
                    result["duration_s"] = round(dur / timescale, 4)
                    result["timescale"] = timescale
                result["creation_time_raw"] = struct.unpack(">I", data[s + 4:s + 8])[0]
            elif btype == b"trak":
                new_track = {"handler": None, "codecs": [], "width": None,
                             "height": None, "channels": None, "sample_rate": None,
                             "duration_s": None}
                result["tracks"].append(new_track)
                walk(s, e, depth + 1, new_track)
            elif btype == b"mdhd":
                version = data[s]
                if version == 0:
                    timescale = struct.unpack(">I", data[s + 12:s + 16])[0]
                    dur = struct.unpack(">I", data[s + 16:s + 20])[0]
                else:
                    timescale = struct.unpack(">I", data[s + 20:s + 24])[0]
                    dur = struct.unpack(">Q", data[s + 24:s + 32])[0]
                if track is not None and timescale:
                    track["duration_s"] = round(dur / timescale, 4)
            elif btype == b"hdlr" and track is not None:
                track["handler"] = data[s + 8:s + 12].decode("latin-1", "replace")
            elif btype == b"tkhd" and track is not None:
                version = data[s]
                base = s + (84 if version == 0 else 96)
                if base + 8 <= e:
                    w, h = struct.unpack(">II", data[base:base + 8])
                    track["width"] = round(w / 65536.0, 2)
                    track["height"] = round(h / 65536.0, 2)
            elif btype == b"stsd" and track is not None:
                entries = struct.unpack(">I", data[s + 4:s + 8])[0]
                pos = s + 8
                for _ in range(min(entries, 8)):
                    if pos + 8 > e:
                        break
                    esize = struct.unpack(">I", data[pos:pos + 4])[0]
                    codec = data[pos + 4:pos + 8].decode("latin-1", "replace").strip()
                    if codec:
                        track["codecs"].append(codec)
                    if codec in ("mp4a",) and pos + 28 <= e:
                        ch = struct.unpack(">H", data[pos + 16:pos + 18])[0]
                        sr = struct.unpack(">I", data[pos + 24:pos + 28])[0] >> 16
                        track["channels"] = ch
                        track["sample_rate"] = sr
                    if esize < 8:
                        break
                    pos += esize
            elif btype in _CONTAINER_BOXES:
                walk(s, e, depth + 1, track)
            if btype == b"moov" and depth == 0:
                pass

    for btype, s, e in _iter_boxes(data, 0, len(data)):
        if btype == b"moov":
            walk(s, e, 1, None)
        elif btype == b"mdat":
            result["mdat_offset"] = s
            break
    if result["duration_s"] is None:
        result["duration_note"] = ("no moov/mvhd duration found; file may be "
                                   "truncated or fragmented")
    return result


def probe_video(path):
    fmt = detect_format(path)["format"]
    if fmt in ("mp4", "mov", "m4v", "m4a"):
        info = _parse_mp4(path)
        info["format"] = fmt
        vids = [t for t in info["tracks"] if t.get("handler") == "vide"]
        if vids:
            info["width"] = vids[0].get("width")
            info["height"] = vids[0].get("height")
            info["video_codecs"] = vids[0].get("codecs")
        auds = [t for t in info["tracks"] if t.get("handler") == "soun"]
        if auds:
            info["audio_codecs"] = auds[0].get("codecs")
            info["audio_channels"] = auds[0].get("channels")
            info["audio_sample_rate"] = auds[0].get("sample_rate")
        info["frame_rate"] = None
        info["frame_rate_note"] = ("frame rate needs stts sample-table walk or "
                                   "ffprobe; not computed by the stdlib probe")
        return info
    if fmt in ("mkv", "webm"):
        with open(path, "rb") as fh:
            head = fh.read(1 << 16)
        info = {"format": fmt, "kind": "video", "container": "matroska/ebml"}
        idx = head.find(b"\x44\x89")  # Duration element id
        if idx >= 0 and idx + 11 <= len(head):
            try:
                raw = head[idx + 3:idx + 11]
                float_val = struct.unpack(">d", raw)[0]
                scale_idx = head.find(b"\x2a\xd7\xb1")
                scale = 1000000.0
                if scale_idx >= 0 and scale_idx + 7 <= len(head):
                    scale = float(int.from_bytes(head[scale_idx + 3:scale_idx + 7], "big"))
                info["duration_s"] = round(float_val * scale / 1e9, 3)
            except Exception:
                info["duration_note"] = "EBML duration element not parsed"
        info["notes"] = ["Matroska track detail is limited to duration in the "
                         "stdlib probe; use ffprobe for codecs and frame rates"]
        return info
    if fmt == "avi":
        return {"format": "avi", "kind": "video", "container": "avi/riff",
                "notes": ["RIFF/AVI header parsing is not implemented; "
                          "use ffprobe for streams"]}
    if fmt == "ts":
        return {"format": "ts", "kind": "video", "container": "mpeg-ts"}
    raise UnsupportedFormat("video probing not implemented for %s" % fmt)


def probe(path):
    """Probe any supported file. Never raises for unknown media: it reports."""
    size = os.path.getsize(path)
    fmt = detect_format(path)
    out = {"path": path, "size_bytes": size, "detected": fmt}
    kind = fmt["kind"]
    try:
        if kind == "image":
            out["technical"] = probe_image(path)
        elif kind == "audio":
            out["technical"] = probe_audio(path)
        elif kind == "video":
            out["technical"] = probe_video(path)
        elif fmt["format"] in TEXT_FORMATS:
            out["technical"] = _probe_text(path)
        else:
            out["technical"] = {"format": fmt["format"], "kind": kind,
                                "note": "no technical probe for this container"}
    except MediaError as exc:
        out["technical"] = None
        out["technical_error"] = exc.to_dict()
    return out


def _probe_text(path):
    with open(path, "rb") as fh:
        raw = fh.read(4 << 20)
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    non_empty = [l for l in lines if l.strip()]
    return {
        "format": detect_format(path)["format"],
        "kind": "text",
        "lines": len(lines),
        "non_empty_lines": len(non_empty),
        "encoding": "utf-8" if _is_utf8(raw) else "non-utf8/latin-1-fallback",
        "bom": raw.startswith(b"\xef\xbb\xbf"),
        "crlf": b"\r\n" in raw,
        "characters": len(text),
        "preview": non_empty[:5],
    }


def _is_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# --------------------------------------------------------------------------
# Ingestion safety scan
# --------------------------------------------------------------------------

# Payload signatures are only trusted in credible positions: at byte 0 (the file
# itself is executable), appended to the tail, or inside a container that is
# expected to carry them. Scanning random media bytes for short signatures such
# as b"MZ" produces false positives on any noisy JPEG.
_EXEC_HEADERS = [
    (b"MZ", "windows PE executable header"),
    (b"\x7fELF", "ELF executable header"),
    (b"\xca\xfe\xba\xbe", "Java class / Mach-O fat header"),
    (b"#!", "script shebang"),
]
_TRAILING_SIGNATURES = [
    (b"PK\x03\x04", "appended ZIP archive"),
    (b"Rar!\x1a\x07", "appended RAR archive"),
    (b"MZ\x90", "appended PE executable stub"),
    (b"\x7fELF", "appended ELF executable"),
]
_CONTAINER_SIGNATURES = {
    "png": [(b"vbaProject.bin", "Office macro project inside a PNG payload"),
            (b"<script", "script tag inside a PNG payload")],
    "jpeg": [(b"<script", "script tag inside a JPEG payload")],
    "gif": [(b"<script", "script tag inside a GIF payload")],
    "svg": [(b"<script", "script tag in SVG"),
            (b"onload=", "inline event handler in SVG")],
    "pdf": [(b"/JavaScript", "PDF embedded JavaScript"),
            (b"/Launch", "PDF launch action"),
            (b"/OpenAction", "PDF automatic action")],
    "zip": [(b"vbaProject.bin", "Office macro project in archive"),
            (b"AutoOpen", "Office auto-run macro keyword in archive")],
}

def safety_scan(path, max_pixels=80_000_000, max_uncompressed_ratio=200,
               max_archive_entries=2000):
    """Statically screen one file before deeper processing.

    Deliberately conservative about what counts as evidence: short signatures
    are only trusted at credible offsets, so ordinary compressed media does not
    trigger payload findings.
    """
    findings = []
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(1 << 20)
        tail = head if size <= 4096 else b""
        if size > 4096:
            fh.seek(max(0, size - 4096))
            tail = fh.read()

    try:
        fmt = detect_format(path)
    except MediaError as exc:
        return {"path": path, "format": "unknown", "verdict": "block",
                "findings": [{"severity": "high", "code": "header-unreadable",
                              "detail": exc.message}]}

    # 1. the file itself claims to be executable / a script
    for sig, label in _EXEC_HEADERS:
        if head.startswith(sig):
            findings.append({"severity": "high", "code": "executable-file",
                             "detail": "%s at byte 0" % label})
            break

    # 2. payload appended after the declared container
    for sig, label in _TRAILING_SIGNATURES:
        offset = tail.find(sig)
        if offset >= 0 and not (sig == b"PK\x03\x04" and fmt["format"] == "zip"):
            findings.append({
                "severity": "medium", "code": "trailing-payload",
                "detail": "%s found %d bytes before end of file"
                          % (label, len(tail) - offset),
            })
            break

    # 3. signatures that are only meaningful inside a known container
    for sig, label in _CONTAINER_SIGNATURES.get(fmt["format"], []):
        offset = head.find(sig)
        if offset >= 0:
            findings.append({
                "severity": "high" if fmt["format"] in ("pdf", "zip") else "medium",
                "code": "embedded-payload",
                "detail": "%s at byte offset %d" % (label, offset),
            })

    # 4. archive behaviour
    if fmt["format"] == "zip":
        stats = _zip_stats(path)
        if stats.get("entries", 0) > max_archive_entries:
            findings.append({"severity": "medium", "code": "archive-entry-count",
                             "detail": "%d entries exceeds limit %d"
                                       % (stats["entries"], max_archive_entries)})
        ratio = stats.get("ratio")
        if ratio is not None:
            if ratio > max_uncompressed_ratio:
                findings.append({
                    "severity": "high", "code": "decompression-bomb",
                    "detail": "archive expansion ratio %0.1fx exceeds limit %dx"
                              % (ratio, max_uncompressed_ratio),
                })
            else:
                findings.append({"severity": "info", "code": "archive",
                                 "detail": "zip expansion ratio %0.1fx" % ratio})

    # 5. image / container structural checks
    if fmt["kind"] == "image":
        try:
            info = probe_image(path)
            px = info.get("width", 0) * info.get("height", 0)
            if px > max_pixels:
                findings.append({
                    "severity": "high", "code": "pixel-bomb",
                    "detail": "%d pixels exceeds limit %d" % (px, max_pixels),
                })
            if info.get("width", 0) == 0 or info.get("height", 0) == 0:
                findings.append({"severity": "medium", "code": "zero-dimension",
                                 "detail": "image declares a zero width or height"})
        except MediaError as exc:
            findings.append({"severity": "medium", "code": "header-unreadable",
                             "detail": exc.message})

    if fmt["format"] in ("mp4", "mov", "m4a", "m4v"):
        with open(path, "rb") as fh:
            blob = fh.read()
        if b"moov" not in blob:
            findings.append({"severity": "medium", "code": "missing-moov",
                             "detail": "no moov box: file is truncated or "
                                       "streaming-only"})
        elif blob.find(b"mdat") < blob.find(b"moov"):
            findings.append({"severity": "info", "code": "moov-after-mdat",
                             "detail": "index is at the end of the file; random "
                                       "access requires the full download"})

    if size == 0:
        findings.append({"severity": "high", "code": "empty-file",
                         "detail": "zero-byte file"})

    if any(f["severity"] == "high" for f in findings):
        verdict = "block"
    elif any(f["severity"] == "medium" for f in findings):
        verdict = "review"
    else:
        verdict = "pass"
    return {"path": path, "format": fmt["format"], "verdict": verdict,
            "findings": findings}


def _zip_stats(path):
    """Entry count and expansion ratio from the ZIP central directory."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(1 << 22)
    except OSError:
        return {}
    total_c = total_u = 0
    entries = 0
    pos = 0
    while True:
        pos = data.find(b"PK\x01\x02", pos)
        if pos < 0 or pos + 46 > len(data):
            break
        csize, usize = struct.unpack("<II", data[pos + 20:pos + 28])
        total_c += csize
        total_u += usize
        entries += 1
        pos += 46
    ratio = (total_u / float(total_c)) if total_c else None
    return {"entries": entries, "ratio": ratio}


# --------------------------------------------------------------------------
# Image decoding
# --------------------------------------------------------------------------


class Raster(object):
    """Simple 8-bit RGB raster: width, height, channels, bytearray data."""

    __slots__ = ("width", "height", "channels", "data", "source", "decoder")

    def __init__(self, width, height, channels, data, source="", decoder="native"):
        self.width = width
        self.height = height
        self.channels = channels
        self.data = data
        self.source = source
        self.decoder = decoder

    @property
    def pixels(self):
        return self.width * self.height

    def pixel(self, x, y):
        i = (y * self.width + x) * self.channels
        d = self.data
        if self.channels >= 3:
            return d[i], d[i + 1], d[i + 2]
        v = d[i]
        return v, v, v

    def luma(self, x, y):
        r, g, b = self.pixel(x, y)
        return (299 * r + 587 * g + 114 * b) // 1000


def decode_image(path, max_pixels=80_000_000):
    fmt = detect_format(path)["format"]
    if fmt == "png":
        raster = _decode_png(path, max_pixels)
    elif fmt == "bmp":
        raster = _decode_bmp(path, max_pixels)
    elif fmt == "pnm":
        raster = _decode_pnm(path, max_pixels)
    else:
        raster = _decode_with_pillow(path, max_pixels)
    raster.source = path
    return raster


def _check_pixels(w, h, limit):
    if w <= 0 or h <= 0:
        raise DecodeError("image has zero or negative dimensions (%dx%d)" % (w, h))
    if w * h > limit:
        raise LimitExceeded(
            "image has %d pixels which exceeds limit %d" % (w * h, limit),
            hint="raise limits.max_pixels or downscale the file")


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png(path, max_pixels):
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise DecodeError("not a PNG file")
    pos = 8
    width = height = depth = ctype = interlace = None
    palette = b""
    idat = []
    while pos + 8 <= len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        cid = blob[pos + 4:pos + 8]
        body = blob[pos + 8:pos + 8 + length]
        if cid == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body[:13])
        elif cid == b"PLTE":
            palette = body
        elif cid == b"IDAT":
            idat.append(body)
        elif cid == b"IEND":
            break
        pos += 12 + length
    if width is None:
        raise DecodeError("PNG missing IHDR")
    _check_pixels(width, height, max_pixels)
    if interlace:
        return _decode_with_pillow(path, max_pixels)
    if depth not in (8, 16):
        return _decode_with_pillow(path, max_pixels)
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise DecodeError("PNG zlib stream is corrupt: %s" % exc)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bytes_pp = channels * (depth // 8)
    stride = width * bytes_pp
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise DecodeError("PNG pixel stream truncated (%d of %d bytes)" % (len(raw), expected))
    out = bytearray(width * height * (4 if ctype == 6 else 3))
    prev = bytearray(stride)
    offset = 0
    step = depth // 8
    o = 0
    for y in range(height):
        ftype = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if ftype == 1:
            for i in range(bytes_pp, stride):
                line[i] = (line[i] + line[i - bytes_pp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bytes_pp] if i >= bytes_pp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bytes_pp] if i >= bytes_pp else 0
                up_left = prev[i - bytes_pp] if i >= bytes_pp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif ftype != 0:
            raise DecodeError("invalid PNG filter type %d on row %d" % (ftype, y))
        prev = line
        if ctype == 2 and depth == 8:
            out[o:o + width * 3] = line
            o += width * 3
        elif ctype == 6 and depth == 8:
            out[o:o + width * 4] = line
            o += width * 4
        elif ctype == 0:
            for x in range(width):
                v = line[x * step]
                out[o] = out[o + 1] = out[o + 2] = v
                o += 3
        elif ctype == 4:
            for x in range(width):
                v = line[x * step]
                out[o] = out[o + 1] = out[o + 2] = v
                o += 3
        elif ctype == 3:
            for x in range(width):
                idx = line[x]
                base = idx * 3
                if base + 2 < len(palette):
                    out[o], out[o + 1], out[o + 2] = palette[base], palette[base + 1], palette[base + 2]
                o += 3
        elif depth == 16 and ctype in (2, 6):
            n = 3 if ctype == 2 else 4
            for x in range(width):
                base = x * n * 2
                out[o] = line[base]
                out[o + 1] = line[base + 2]
                out[o + 2] = line[base + 4]
                o += 3
        else:
            raise DecodeError("unsupported PNG colour combination (ctype=%d depth=%d)"
                              % (ctype, depth))
    used_channels = 4 if ctype == 6 else 3
    return Raster(width, height, used_channels, out, decoder="native-png")


def _decode_bmp(path, max_pixels):
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:2] != b"BM":
        raise DecodeError("not a BMP file")
    data_off = struct.unpack("<I", blob[10:14])[0]
    header_size = struct.unpack("<I", blob[14:18])[0]
    width, height = struct.unpack("<ii", blob[18:26])
    bpp = struct.unpack("<H", blob[28:30])[0]
    compression = struct.unpack("<I", blob[30:34])[0]
    top_down = height < 0
    height = abs(height)
    _check_pixels(width, height, max_pixels)
    if compression not in (0, 3):
        raise UnsupportedFormat("BMP compression %d is not supported" % compression)
    palette = b""
    if bpp == 8:
        palette = blob[14 + header_size:14 + header_size + 1024]
    row_size = ((width * bpp + 31) // 32) * 4
    out = bytearray(width * height * 3)
    for y in range(height):
        src_y = y if top_down else (height - 1 - y)
        row = data_off + src_y * row_size
        if row + row_size > len(blob):
            raise DecodeError("BMP pixel data truncated at row %d" % y)
        o = y * width * 3
        for x in range(width):
            if bpp == 24:
                b, g, r = blob[row + x * 3:row + x * 3 + 3]
            elif bpp == 32:
                b, g, r = blob[row + x * 4:row + x * 4 + 3]
            elif bpp == 8:
                idx = blob[row + x] * 4
                b, g, r = palette[idx:idx + 3]
            elif bpp == 16:
                val = struct.unpack("<H", blob[row + x * 2:row + x * 2 + 2])[0]
                r = (val >> 10) & 0x1F
                g = (val >> 5) & 0x1F
                b = val & 0x1F
                r, g, b = (r * 255 // 31, g * 255 // 31, b * 255 // 31)
            else:
                raise UnsupportedFormat("BMP bit depth %d is not supported" % bpp)
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            o += 3
    return Raster(width, height, 3, out, decoder="native-bmp")


def _decode_pnm(path, max_pixels):
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:2] not in (b"P5", b"P6"):
        raise UnsupportedFormat("only binary PNM (P5/P6) is supported")
    parts, pos = [], 2
    while len(parts) < 3 and pos < len(blob):
        while pos < len(blob) and blob[pos:pos + 1].isspace():
            pos += 1
        if blob[pos:pos + 1] == b"#":
            while pos < len(blob) and blob[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(blob) and not blob[pos:pos + 1].isspace():
            pos += 1
        parts.append(blob[start:pos])
    pos += 1
    width, height, maxval = int(parts[0]), int(parts[1]), int(parts[2])
    _check_pixels(width, height, max_pixels)
    step = 1 if maxval < 256 else 2
    ch = 3 if blob[:2] == b"P6" else 1
    need = width * height * ch * step
    body = blob[pos:pos + need]
    if len(body) < need:
        raise DecodeError("PNM pixel data truncated")
    out = bytearray(width * height * 3)
    o = 0
    for i in range(0, need, ch * step):
        if ch == 3:
            out[o] = body[i]
            out[o + 1] = body[i + step]
            out[o + 2] = body[i + 2 * step]
        else:
            out[o] = out[o + 1] = out[o + 2] = body[i]
        o += 3
    return Raster(width, height, 3, out, decoder="native-pnm")


def _decode_with_pillow(path, max_pixels):
    if not optional_dependencies()["pillow"]:
        raise UnsupportedFormat(
            "decoding %s needs Pillow (native decoders cover PNG, BMP, PNM, WAV)"
            % detect_format(path)["format"],
            hint="pip install 'Pillow>=10.0' then retry, or convert to PNG",
        )
    from PIL import Image  # noqa: WPS433

    with Image.open(path) as im:
        _check_pixels(im.width, im.height, max_pixels)
        im = im.convert("RGB")
        return Raster(im.width, im.height, 3, bytearray(im.tobytes()),
                      decoder="pillow")


# --------------------------------------------------------------------------
# WAV decoding
# --------------------------------------------------------------------------


def decode_wav(path, max_seconds=7200, max_channels=8):
    """Decode PCM/float WAV into per-channel int16-normalised float lists.

    Returns dict: sample_rate, channels, frames, duration_s, samples (list of
    channel lists, values in -1.0..1.0).
    """
    import wave

    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        width = wf.getsampwidth()
        frames = wf.getnframes()
        if channels > max_channels:
            raise LimitExceeded("WAV has %d channels (limit %d)" % (channels, max_channels))
        duration = frames / float(rate) if rate else 0.0
        if duration > max_seconds:
            raise LimitExceeded(
                "WAV duration %.1fs exceeds analysis limit %ss" % (duration, max_seconds),
                hint="raise limits.max_audio_seconds or trim the excerpt",
            )
        raw = wf.readframes(frames)

    if width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        count = len(raw) // 2
        ints = struct.unpack("<%dh" % count, raw[:count * 2])
        vals = [v / 32768.0 for v in ints]
    elif width == 3:
        vals = []
        for i in range(0, len(raw) - 2, 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            vals.append(v / 8388608.0)
    elif width == 4:
        count = len(raw) // 4
        ints = struct.unpack("<%di" % count, raw[:count * 4])
        vals = [v / 2147483648.0 for v in ints]
    else:
        raise UnsupportedFormat("WAV sample width %d is not supported" % width)

    chans = [vals[c::channels] for c in range(channels)]
    return {
        "sample_rate": rate,
        "channels": channels,
        "sample_width_bytes": width,
        "frames": frames,
        "duration_s": round(duration, 4),
        "samples": chans,
    }


# --------------------------------------------------------------------------
# Asset graph helpers
# --------------------------------------------------------------------------

_SIDECAR_EXTS = {".srt", ".vtt", ".txt", ".json", ".xmp", ".aae", ".xml", ".md"}


def asset_graph(paths, hash_limit=64 << 20):
    """Link thumbnails, proxies, sidecars and duplicates into a graph."""
    nodes = []
    by_stem = {}
    for p in paths:
        base = os.path.basename(p)
        stem, ext = os.path.splitext(base)
        try:
            fmt = detect_format(p)
        except MediaError:
            fmt = {"format": "unknown", "kind": "unknown"}
        size = os.path.getsize(p) if os.path.exists(p) else 0
        digest = None
        if size <= hash_limit:
            try:
                digest = sha256_file(p)
            except (OSError, MediaError):
                digest = None
        node = {"id": base, "path": p, "stem": stem, "ext": ext.lower(),
                "format": fmt["format"], "kind": fmt["kind"], "size_bytes": size,
                "sha256": digest, "role": "original", "links": []}
        nodes.append(node)
        by_stem.setdefault(stem.lower(), []).append(node)

    roles = {"thumb": "thumbnail", "thumbnails": "thumbnail", "proxy": "proxy",
             "preview": "proxy", "small": "proxy", "lowres": "proxy"}
    for stem, group in by_stem.items():
        has_media = any(n["kind"] in ("image", "video", "audio") for n in group)
        if len(group) > 1 and has_media:
            for node in group:
                for token, role in roles.items():
                    if token in node["stem"].lower():
                        node["role"] = role
                for other in group:
                    if other is not node:
                        node["links"].append(other["id"])
        for node in group:
            if node["ext"] in _SIDECAR_EXTS and has_media:
                node["role"] = "sidecar"

    digests = {}
    for node in nodes:
        if node["sha256"]:
            digests.setdefault(node["sha256"], []).append(node["id"])
    duplicates = [{"sha256": d, "files": ids} for d, ids in digests.items() if len(ids) > 1]

    return {"node_count": len(nodes), "nodes": nodes,
            "duplicate_groups": duplicates,
            "unlinked": [n["id"] for n in nodes if not n["links"] and n["role"] == "original"]}


def list_media_files(root, recursive=True, limit=2000):
    """Collect candidate media / subtitle files under *root*."""
    exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
            ".ppm", ".pgm", ".pnm", ".wav", ".mp3", ".flac", ".ogg", ".m4a",
            ".mp4", ".mov", ".mkv", ".webm", ".avi", ".srt", ".vtt", ".json",
            ".txt", ".md", ".zip", ".tar")
    found = []
    if os.path.isfile(root):
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.lower().endswith(exts):
                found.append(os.path.join(dirpath, name))
                if len(found) > limit:
                    raise LimitExceeded("more than %d candidate files under %s" % (limit, root),
                                        hint="use --limit or narrow the input path")
        if not recursive:
            break
    return found
