#!/usr/bin/env python3
"""Shared runtime for the full-offline-image-capabilities Skill Tool.

Role: the single place for configuration, logging, path safety, hashing,
timeouts, retries, approval gates, JSON I/O, the lightweight JSON-Schema
subset validator, and image decoding (Pillow backend with a pure-standard
library PNG / JPEG / WebP / BMP / GIF codec fallback).

Every other tools-* module imports this one. Nothing here reaches the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import time
import zlib

TOOL_NAME = "full-offline-image-capabilities"
TOOL_VERSION = "1.0.0"
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))


def load_sibling(filename, module_name):
    """Import a hyphenated sibling module by file path.

    Packaged filenames use hyphens, which are not valid Python module names,
    so every tools-* module bootstraps its siblings through this helper.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    if TOOL_DIR not in sys.path:
        sys.path.insert(0, TOOL_DIR)
    path = os.path.join(TOOL_DIR, filename)
    if not os.path.isfile(path):
        raise ImportError("sibling module not found: %s" % path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_APPROVAL = 3
EXIT_TIMEOUT = 4
EXIT_VALIDATION = 5

DEFAULT_CONFIG_ASSET = "asset-default-config.json"
DEFAULT_QC_POLICY_ASSET = "asset-qc-policy.json"
DEFAULT_PRESETS_ASSET = "asset-pipeline-presets.json"
DEFAULT_RECIPES_ASSET = "asset-prompt-recipes.json"
DEFAULT_MODEL_CATALOG_ASSET = "asset-model-catalog.json"
DEFAULT_LICENSE_POLICY_ASSET = "asset-license-policy.json"
DEFAULT_CAPABILITY_CATALOG_ASSET = "asset-capability-catalog.json"
DEFAULT_QC_SCHEMA_ASSET = "asset-qc-schema.json"
DEFAULT_PLAN_SCHEMA_ASSET = "asset-plan-schema.json"
DEFAULT_REPORT_TEMPLATE_ASSET = "asset-report-template.md"


# --------------------------------------------------------------------------
# errors, logging, results
# --------------------------------------------------------------------------
class ToolError(Exception):
    """Operational failure with a stable exit code and machine-readable id."""

    def __init__(self, message, code="error", exit_code=EXIT_ERROR, details=None):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details or {}


class ApprovalRequired(ToolError):
    def __init__(self, action, reason):
        super().__init__(
            "action '%s' requires explicit approval: re-run with --approve (%s)" % (action, reason),
            code="approval-required",
            exit_code=EXIT_APPROVAL,
            details={"action": action, "reason": reason},
        )


class Timeout(ToolError):
    def __init__(self, what, seconds):
        super().__init__(
            "timeout after %ss while %s" % (seconds, what), code="timeout", exit_code=EXIT_TIMEOUT
        )


class Logger:
    LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40, "silent": 100}

    def __init__(self, level="info"):
        self.level = self.LEVELS.get(level, 20)

    def _emit(self, level, msg):
        if self.LEVELS.get(level, 20) >= self.level:
            stream = sys.stderr
            print("[%s] %s" % (level, msg), file=stream)

    def debug(self, msg):
        self._emit("debug", msg)

    def info(self, msg):
        self._emit("info", msg)

    def warn(self, msg):
        self._emit("warn", msg)

    def error(self, msg):
        self._emit("error", msg)


LOG = Logger()


def result_envelope(command, data=None, warnings=None, errors=None):
    return {
        "schema": "foic.result/1",
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "command": command,
        "generated_at": iso_now(),
        "ok": not errors,
        "data": data if data is not None else {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# filesystem helpers and path safety
# --------------------------------------------------------------------------
def is_relative_safe(rel):
    """True when rel is a safe single-component-safe relative path."""
    if not rel or rel.strip() != rel:
        return False
    if rel.startswith(("/", "\\")):
        return False
    if re.match(r"^[A-Za-z]:", rel):
        return False
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return True


def safe_join(root, rel, must_exist=False):
    """Join rel under root, refusing traversal, absolute paths and escapes."""
    if not is_relative_safe(rel):
        raise ToolError(
            "unsafe path rejected: %r" % rel, code="path-traversal", details={"path": rel}
        )
    root_abs = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root_abs, rel.replace("\\", "/")))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise ToolError(
            "resolved path escapes root: %r -> %r" % (rel, target),
            code="path-traversal",
            details={"root": root_abs, "path": target},
        )
    if must_exist and not os.path.exists(target):
        raise ToolError("path does not exist: %s" % target, code="not-found")
    return target


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def human_bytes(n):
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step or unit == "TB":
            return "%.1f%s" % (val, unit) if unit != "B" else "%dB" % int(val)
        val /= step
    return "%.1fTB" % val


def iter_files(root, skip_dirs=None):
    skip = set(skip_dirs or [])
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            yield os.path.join(dirpath, name)


def atomic_write_text(path, text, overwrite=False):
    return atomic_write_bytes(path, text.encode("utf-8"), overwrite=overwrite)


def atomic_write_bytes(path, data, overwrite=False):
    if os.path.exists(path) and not overwrite:
        raise ToolError(
            "refusing to overwrite existing file without --overwrite: %s" % path,
            code="would-overwrite",
        )
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    tmp = path + ".foic-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def sha256_file(path, chunk=1 << 20, deadline=None):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            if deadline:
                deadline.check("hashing %s" % path)
            block = fh.read(chunk)
            if not block:
                break
            size += len(block)
            h.update(block)
    return h.hexdigest(), size


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj):
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))


# --------------------------------------------------------------------------
# JSON I/O plus a documented JSON-Schema subset validator
# --------------------------------------------------------------------------
def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ToolError("file not found: %s" % path, code="not-found")
    except json.JSONDecodeError as exc:
        raise ToolError("invalid JSON in %s: %s" % (path, exc), code="invalid-json")


def write_json(path, obj, overwrite=False, indent=2):
    return atomic_write_text(
        path, json.dumps(obj, indent=indent, sort_keys=False, ensure_ascii=False) + "\n", overwrite
    )


def _type_ok(value, expected):
    if isinstance(expected, list):
        return any(_type_ok(value, e) for e in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_schema(obj, schema, path="$"):
    """Validate obj against the documented JSON-Schema subset.

    Supported keywords: type, required, properties, items, enum, minimum,
    maximum, minLength, maxLength, additionalProperties (bool).
    Returns a list of {"path":..., "message":...} problems (empty == valid).
    """
    problems = []
    stype = schema.get("type")
    if stype and not _type_ok(obj, stype):
        problems.append({"path": path, "message": "expected type %r" % (stype,)})
        return problems
    if "enum" in schema and obj not in schema["enum"]:
        problems.append({"path": path, "message": "value not in enum %r" % (schema["enum"],)})
    if isinstance(obj, str):
        if "minLength" in schema and len(obj) < schema["minLength"]:
            problems.append({"path": path, "message": "shorter than minLength"})
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            problems.append({"path": path, "message": "longer than maxLength"})
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            problems.append({"path": path, "message": "below minimum %s" % schema["minimum"]})
        if "maximum" in schema and obj > schema["maximum"]:
            problems.append({"path": path, "message": "above maximum %s" % schema["maximum"]})
    if isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                problems.append({"path": "%s.%s" % (path, key), "message": "required key missing"})
        props = schema.get("properties", {})
        for key, value in obj.items():
            if key in props:
                problems.extend(validate_schema(value, props[key], "%s.%s" % (path, key)))
            elif schema.get("additionalProperties") is False:
                problems.append({"path": "%s.%s" % (path, key), "message": "additional property"})
    if isinstance(obj, list) and isinstance(schema.get("items"), dict):
        for idx, item in enumerate(obj):
            problems.extend(validate_schema(item, schema["items"], "%s[%d]" % (path, idx)))
    return problems


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def deep_merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Configuration container with dotted lookup and deep merge."""

    def __init__(self, raw, source=None):
        self.raw = raw
        self.source = source

    def get(self, dotted, default=None):
        node = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_dotted(self, dotted, value):
        parts = dotted.split(".")
        node = self.raw
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ToolError("cannot set %s: parent is not an object" % dotted, code="bad-config")
        node[parts[-1]] = value
        return self

    def merged_with(self, override):
        return Config(deep_merge(self.raw, override), self.source)

    def fingerprint(self):
        return sha256_json(self.raw)[:16]


def load_config(
    config_path=None, overrides=None, base_dir=None, logger=LOG, default_asset=DEFAULT_CONFIG_ASSET
):
    default_path = os.path.join(TOOL_DIR, default_asset)
    raw = read_json(default_path)
    source = os.path.relpath(default_path, TOOL_DIR)
    if config_path:
        user = read_json(config_path)
        if not isinstance(user, dict):
            raise ToolError("config file must contain a JSON object: %s" % config_path, code="bad-config")
        raw = deep_merge(raw, user)
        source = "%s + %s" % (source, config_path)
    cfg = Config(raw, source)
    for item in overrides or []:
        if "=" not in item:
            raise ToolError("--set expects key=value, got %r" % item, code="usage", exit_code=EXIT_USAGE)
        key, _, value = item.partition("=")
        cfg.set_dotted(key.strip(), coerce_value(value.strip()))
    level = cfg.get("logging.level", "info")
    if cfg.get("logging.quiet"):
        level = "silent"
    logger.level = Logger.LEVELS.get(level, 20)
    return cfg


def coerce_value(text):
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def resolve_under(base, path):
    if path is None:
        return None
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(base or os.getcwd(), path))


# --------------------------------------------------------------------------
# limits, timeouts, retries, approvals
# --------------------------------------------------------------------------
class Deadline:
    """Bounded wall-clock budget; every long loop calls check()."""

    def __init__(self, seconds=None, what="work"):
        self.seconds = seconds
        self.what = what
        self.started = time.monotonic()
        self.end = self.started + seconds if seconds and seconds > 0 else None

    def remaining(self):
        if not self.end:
            return None
        return max(0.0, self.end - time.monotonic())

    def expired(self):
        return self.end is not None and time.monotonic() >= self.end

    def check(self, what=None):
        if self.expired():
            raise Timeout(what or self.what, self.seconds)
        return self.remaining()

    def elapsed(self):
        return time.monotonic() - self.started


def retry_call(fn, attempts=1, backoff=0.5, deadline=None, what="operation", logger=LOG):
    last = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return fn()
        except ToolError as exc:
            if exc.code in ("approval-required", "usage", "path-traversal"):
                raise
            last = exc
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            last = ToolError("%s failed: %s" % (what, exc), code="runtime-error")
        if attempt < max(1, attempts):
            logger.warn("%s attempt %d failed (%s); retrying" % (what, attempt, getattr(last, "code", "error")))
            time.sleep(backoff * attempt)
            if deadline:
                deadline.check(what)
    raise last


def require_approval(action, cfg, approved, reason="config policy"):
    gated = cfg.get("security.require_approval_for", [])
    if action in gated and not approved:
        raise ApprovalRequired(action, reason)
    return True


def check_file_limit(path, cfg):
    max_bytes = (cfg.get("runtime.max_file_mb") or 0) * 1024 * 1024
    size = os.path.getsize(path)
    if max_bytes and size > max_bytes:
        raise ToolError(
            "file exceeds runtime.max_file_mb (%s > %s): %s"
            % (human_bytes(size), human_bytes(max_bytes), path),
            code="file-too-large",
        )
    return size


# --------------------------------------------------------------------------
# image decoding: Pillow backend + pure-stdlib codec fallback
# --------------------------------------------------------------------------
class ImageData:
    """Decoded raster. pixels may be None when only metadata was parsed."""

    __slots__ = ("width", "height", "mode", "pixels", "format", "backend", "meta", "truncated")

    def __init__(self, width, height, mode, pixels=None, fmt=None, backend=None, meta=None, truncated=False):
        self.width = width
        self.height = height
        self.mode = mode
        self.pixels = pixels
        self.format = fmt
        self.backend = backend
        self.meta = meta or {}
        self.truncated = truncated

    @property
    def has_pixels(self):
        return self.pixels is not None

    @property
    def channels(self):
        return {"L": 1, "RGB": 3, "RGBA": 4}[self.mode]

    @property
    def has_alpha(self):
        return self.mode == "RGBA"


class UnsupportedImage(ToolError):
    def __init__(self, message, code="unsupported-image"):
        super().__init__(message, code=code)


def pillow_available():
    try:
        import PIL  # noqa: F401
        import PIL.Image  # noqa: F401

        return True
    except Exception:
        return False


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_chunks(data):
    """Yield (type, payload) for each PNG chunk. Tolerant of truncation."""
    if not data.startswith(_PNG_MAGIC):
        raise UnsupportedImage("not a PNG file")
    pos = 8
    n = len(data)
    while pos + 8 <= n:
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        start = pos + 8
        end = min(start + length, n)
        yield ctype, data[start:end], (end - start) == length
        if ctype == b"IEND":
            return
        pos = end + 4
        if length == 0 and ctype == b"":
            return


def _png_unfilter(raw, width, height, channels, bitdepth):
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) < expected:
        height = len(raw) // (stride + 1)
        if height <= 0:
            raise UnsupportedImage("PNG contains no complete scanlines")
        raw = raw[: height * (stride + 1)]
        truncated = True
    else:
        raw = raw[:expected]
        truncated = False
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise UnsupportedImage("unsupported PNG filter type %d" % ftype)
        out[_ * stride : (_ + 1) * stride] = line
        prev = line
    return bytes(out), height, truncated


def _png_palette_expand(indices, palette, trns, width, height):
    alpha = bool(trns)
    mode = "RGBA" if alpha else "RGB"
    out = bytearray(width * height * (4 if alpha else 3))
    pal = {i: tuple(palette[i * 3 : i * 3 + 3]) for i in range(len(palette) // 3)}
    for i, idx in enumerate(indices):
        r, g, b = pal.get(idx, (0, 0, 0))
        a = trns[idx] if alpha and idx < len(trns) else 255
        if alpha:
            out[i * 4 : i * 4 + 4] = bytes((r, g, b, a))
        else:
            out[i * 3 : i * 3 + 3] = bytes((r, g, b))
    return bytes(out), mode


def decode_png_bytes(data, want_pixels=True, allow_partial=False):
    """Pure-stdlib PNG decoder (8/16-bit, colour types 0/2/3/4/6, non-interlaced)."""
    ihdr = None
    idat = bytearray()
    palette = b""
    trns = b""
    meta = {}
    complete = True
    saw_iend = False
    for ctype, payload, whole in _png_chunks(data):
        if ctype == b"IHDR":
            if len(payload) < 13:
                raise UnsupportedImage("truncated PNG IHDR")
            ihdr = struct.unpack(">IIBBBBB", payload[:13])
        elif ctype == b"IDAT":
            if not whole:
                complete = False
            idat += payload
        elif ctype == b"PLTE":
            palette = payload
        elif ctype == b"tRNS":
            trns = payload
        elif ctype == b"iCCP":
            meta["icc"] = True
        elif ctype == b"eXIf":
            meta["exif"] = True
        elif ctype == b"tEXt" or ctype == b"iTXt" or ctype == b"zTXt":
            meta["text"] = True
        elif ctype == b"pHYs" and len(payload) >= 9:
            ppux, ppuy, unit = struct.unpack(">IIB", payload[:9])
            if unit == 1 and ppux:
                meta["dpi"] = int(round(ppux * 0.0254))
        elif ctype == b"IEND":
            saw_iend = True
    if ihdr is None:
        raise UnsupportedImage("PNG missing IHDR")
    width, height, bitdepth, colortype, _comp, _filt, interlace = ihdr
    if interlace:
        raise UnsupportedImage("interlaced PNG not supported by the stdlib backend")
    if bitdepth not in (8, 16):
        raise UnsupportedImage("PNG bit depth %d unsupported by the stdlib backend" % bitdepth)
    meta["bit_depth"] = bitdepth
    meta["png_color_type"] = colortype
    mode_map = {0: "L", 2: "RGB", 4: "L", 6: "RGBA"}
    if colortype not in mode_map and colortype != 3:
        raise UnsupportedImage("PNG colour type %d unsupported" % colortype)
    if want_pixels:
        try:
            raw = zlib.decompress(bytes(idat))
        except zlib.error:
            if not allow_partial:
                raise UnsupportedImage("PNG image data is corrupt or truncated")
            dec = zlib.decompressobj()
            try:
                raw = dec.decompress(bytes(idat))
            except zlib.error:
                raw = b""
            complete = False
        if not complete and not allow_partial:
            raise UnsupportedImage("PNG image data incomplete")
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colortype]
        flat, rows, truncated = _png_unfilter(raw, width, height, channels, bitdepth)
        if bitdepth == 16:
            stride16 = width * channels * 2
            kept = bytearray()
            for y in range(rows):
                line = flat[y * stride16 : (y + 1) * stride16]
                kept += bytes(line[i] for i in range(0, len(line), 2))
            flat = bytes(kept)
        if colortype == 3:
            pixels, mode = _png_palette_expand(flat, palette, trns, width, rows)
        elif colortype == 4:
            mode = "RGBA"
            pixels = bytearray()
            for i in range(0, len(flat), 2):
                pixels += bytes((flat[i], flat[i], flat[i], flat[i + 1]))
            pixels = bytes(pixels)
        else:
            mode = mode_map[colortype]
            pixels = flat
        meta["rows_decoded"] = rows
        return ImageData(width, rows, mode, pixels, "PNG", "stdlib", meta, truncated or not (complete and saw_iend))
    mode = {0: "L", 2: "RGB", 3: "RGB", 4: "RGBA", 6: "RGBA"}[colortype]
    return ImageData(width, height, mode, None, "PNG", "stdlib", meta)


def encode_png(width, height, pixels, mode="RGB"):
    """Write a PNG with the standard library only (filter 0, 8-bit)."""
    channels = {"L": 1, "RGB": 3, "RGBA": 4}[mode]
    if len(pixels) != width * height * channels:
        raise ToolError("pixel buffer size mismatch for %dx%d %s" % (width, height, mode), code="bad-pixels")
    colortype = {"L": 0, "RGB": 2, "RGBA": 6}[mode]

    def chunk(ctype, payload):
        return (
            struct.pack(">I", len(payload))
            + ctype
            + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
        )

    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride : (y + 1) * stride]
    out = bytearray(_PNG_MAGIC)
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colortype, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    out += chunk(b"IEND", b"")
    return bytes(out)


def _jpeg_info(data):
    pos = 2
    n = len(data)
    meta = {}
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xD9 or marker == 0xDA:
            break
        if pos + 4 > n:
            break
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        segment = data[pos + 4 : pos + 4 + length]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if len(segment) >= 5:
                h, w = struct.unpack(">HH", segment[1:5])
                meta["width"], meta["height"] = w, h
                meta["bit_depth"] = segment[0]
                meta["components"] = segment[5] if len(segment) > 5 else 3
        elif marker == 0xE1 and segment.startswith(b"Exif"):
            meta["exif"] = True
        elif marker == 0xE2 and segment.startswith(b"ICC_PROFILE"):
            meta["icc"] = True
        if length <= 0:
            break
        pos += 2 + length
    if "width" not in meta:
        raise UnsupportedImage("JPEG start-of-frame marker not found")
    return meta


def _webp_info(data):
    if data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise UnsupportedImage("not a WebP file")
    pos = 12
    n = len(data)
    meta = {}
    while pos + 8 <= n:
        fourcc = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        body = data[pos + 8 : pos + 8 + size]
        if fourcc == b"VP8X" and len(body) >= 10:
            meta["width"] = (int.from_bytes(body[4:7], "little")) + 1
            meta["height"] = (int.from_bytes(body[7:10], "little")) + 1
            meta["alpha"] = bool(body[0] & 0x10)
        elif fourcc == b"VP8 " and len(body) >= 10:
            meta["width"] = struct.unpack("<H", body[6:8])[0] & 0x3FFF
            meta["height"] = struct.unpack("<H", body[8:10])[0] & 0x3FFF
        elif fourcc == b"VP8L" and len(body) >= 5:
            bits = int.from_bytes(body[1:5], "little")
            meta["width"] = (bits & 0x3FFF) + 1
            meta["height"] = ((bits >> 14) & 0x3FFF) + 1
        pos += 8 + size + (size & 1)
    if "width" not in meta:
        raise UnsupportedImage("WebP chunk not recognised")
    return meta


def _bmp_info(data):
    if data[0:2] != b"BM":
        raise UnsupportedImage("not a BMP file")
    (dib,) = struct.unpack("<I", data[14:18])
    if dib >= 40:
        w, h = struct.unpack("<ii", data[18:26])
        bits = struct.unpack("<H", data[28:30])[0]
    else:
        w, h = struct.unpack("<hh", data[18:22])
        bits = struct.unpack("<H", data[24:26])[0]
    return {"width": abs(w), "height": abs(h), "bit_depth": bits}


def _gif_info(data):
    if data[0:3] not in (b"GIF",):
        raise UnsupportedImage("not a GIF file")
    w, h = struct.unpack("<HH", data[6:10])
    return {"width": w, "height": h}


def sniff_format(data):
    if data.startswith(_PNG_MAGIC):
        return "PNG"
    if data[:2] == b"\xff\xd8":
        return "JPEG"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[0:2] == b"BM":
        return "BMP"
    if data[0:3] == b"GIF":
        return "GIF"
    if data[0:4] == b"II*\x00" or data[0:4] == b"MM\x00*":
        return "TIFF"
    return "UNKNOWN"


def _stdlib_probe(data, fmt):
    if fmt == "PNG":
        img = decode_png_bytes(data, want_pixels=False)
        meta = dict(img.meta)
        mode = img.mode
        return {"width": img.width, "height": img.height, "mode": mode, "meta": meta}
    if fmt == "JPEG":
        meta = _jpeg_info(data)
        return {
            "width": meta.pop("width"),
            "height": meta.pop("height"),
            "mode": "L" if meta.get("components") == 1 else "RGB",
            "meta": meta,
        }
    if fmt == "WEBP":
        meta = _webp_info(data)
        return {
            "width": meta.pop("width"),
            "height": meta.pop("height"),
            "mode": "RGBA" if meta.pop("alpha", False) else "RGB",
            "meta": meta,
        }
    if fmt == "BMP":
        meta = _bmp_info(data)
        return {
            "width": meta.pop("width"),
            "height": meta.pop("height"),
            "mode": "RGB" if meta.get("bit_depth", 24) >= 24 else "L",
            "meta": meta,
        }
    if fmt == "GIF":
        meta = _gif_info(data)
        return {"width": meta.pop("width"), "height": meta.pop("height"), "mode": "RGB", "meta": meta}
    raise UnsupportedImage("no stdlib decoder for %s (install Pillow for pixel access)" % fmt)


def load_image(path, cfg=None, want_pixels=True, backend=None, allow_partial=False, logger=LOG):
    """Decode an image. Returns ImageData; pixels is None when only probing."""
    cfg = cfg or Config({})
    backend = backend or cfg.get("runtime.image_backend", "auto")
    max_pixels = cfg.get("runtime.max_pixels") or 67108864
    with open(path, "rb") as fh:
        data = fh.read()
    fmt = sniff_format(data)
    if fmt == "UNKNOWN":
        raise UnsupportedImage("unrecognised image container: %s" % path)
    pixels_wanted = want_pixels
    if backend == "stdlib":
        use_pillow = False
    elif backend == "pillow":
        use_pillow = pillow_available()
        if not use_pillow and pixels_wanted:
            raise ToolError(
                "Pillow backend requested but Pillow is not importable; use runtime.image_backend=auto",
                code="backend-unavailable",
            )
    else:
        use_pillow = pillow_available()
    if use_pillow:
        try:
            return _pillow_load(path, data, fmt, pixels_wanted)
        except Exception as exc:  # noqa: BLE001 - fall back to stdlib codecs
            logger.debug("Pillow decode failed for %s (%s); using stdlib backend" % (path, exc))
    if pixels_wanted and fmt not in ("PNG",):
        logger.debug("stdlib backend provides metadata only for %s; pixels unavailable" % fmt)
        pixels_wanted = False
    probe = _stdlib_probe(data, fmt)
    if not pixels_wanted:
        return ImageData(
            probe["width"], probe["height"], probe["mode"], None, fmt, "stdlib", probe["meta"]
        )
    img = decode_png_bytes(data, want_pixels=True, allow_partial=allow_partial)
    img.meta.update(probe["meta"])
    if img.width * img.height > max_pixels:
        raise ToolError(
            "image exceeds runtime.max_pixels (%d px): %s" % (max_pixels, path), code="image-too-large"
        )
    return img


def _pillow_load(path, data, fmt, want_pixels):
    from PIL import Image  # imported lazily so the stdlib path stays dependency free
    from PIL import ImageCms  # noqa: F401 - presence probe only

    with Image.open(io.BytesIO(data)) as im:
        meta = {
            "exif": bool(im.getexif()) if hasattr(im, "getexif") else False,
            "icc": "icc_profile" in im.info,
            "dpi": int(im.info.get("dpi", (0, 0))[0]) or None,
            "bit_depth": 8,
        }
        if im.mode in ("P", "LA", "La", "I;16", "I", "F", "CMYK", "YCbCr", "1"):
            meta["source_mode"] = im.mode
        width, height = im.size
        mode = im.mode
        if not want_pixels:
            return ImageData(width, height, _pillow_mode(mode), None, im.format or fmt, "pillow", meta)
        converted = im
        if mode == "P":
            converted = im.convert("RGBA") if "transparency" in im.info else im.convert("RGB")
        elif mode in ("LA", "La"):
            converted = im.convert("RGBA")
        elif mode in ("1", "I;16", "I", "F"):
            converted = im.convert("L")
        elif mode not in ("L", "RGB", "RGBA"):
            converted = im.convert("RGB")
        pixels = converted.tobytes()
        out_mode = _pillow_mode(converted.mode)
        meta["format"] = im.format or fmt
        return ImageData(width, height, out_mode, pixels, im.format or fmt, "pillow", meta)


def _pillow_mode(mode):
    if mode == "RGBA":
        return "RGBA"
    if mode == "RGB":
        return "RGB"
    return "L"


def save_image(path, img, cfg=None, fmt=None, quality=95, overwrite=False, strip_metadata=False):
    """Encode ImageData to disk. Uses Pillow when present, else the stdlib PNG writer."""
    fmt = (fmt or "png").upper()
    if fmt == "JPEG":
        fmt = "JPEG"
    if pillow_available() and not (fmt == "PNG" and img.backend == "stdlib" and cfg and cfg.get("runtime.image_backend") == "stdlib"):
        from PIL import Image as PILImage

        pil_mode = {"L": "L", "RGB": "RGB", "RGBA": "RGBA"}[img.mode]
        out = PILImage.frombytes(pil_mode, (img.width, img.height), img.pixels)
        kwargs = {}
        if fmt in ("JPEG", "WEBP"):
            kwargs["quality"] = int(quality)
            if fmt == "JPEG" and pil_mode == "RGBA":
                out = out.convert("RGB")
        if fmt == "PNG":
            kwargs["optimize"] = True
        buf = io.BytesIO()
        if strip_metadata:
            out.save(buf, format=fmt, **kwargs)
        else:
            out.save(buf, format=fmt, **kwargs)
        return atomic_write_bytes(path, buf.getvalue(), overwrite=overwrite)
    if fmt != "PNG":
        raise ToolError(
            "writing %s requires Pillow; install Pillow or export PNG with the stdlib writer" % fmt,
            code="backend-unavailable",
        )
    return atomic_write_bytes(path, encode_png(img.width, img.height, img.pixels, img.mode), overwrite=overwrite)


# --------------------------------------------------------------------------
# pixel analytics helpers (pure Python, operate on the sampled buffer)
# --------------------------------------------------------------------------
def to_gray(img):
    if img.mode == "L":
        return img.pixels
    px = img.pixels
    if img.mode == "RGB":
        gray = bytearray(len(px) // 3)
        for i in range(len(gray)):
            o = i * 3
            gray[i] = (px[o] * 299 + px[o + 1] * 587 + px[o + 2] * 114) // 1000
        return bytes(gray)
    gray = bytearray(len(px) // 4)
    for i in range(len(gray)):
        o = i * 4
        gray[i] = (px[o] * 299 + px[o + 1] * 587 + px[o + 2] * 114) // 1000
    return bytes(gray)


def resize_gray_nearest(gray, width, height, new_w, new_h):
    if (new_w, new_h) == (width, height):
        return gray
    out = bytearray(new_w * new_h)
    x_ratio = width / float(new_w)
    y_ratio = height / float(new_h)
    for y in range(new_h):
        sy = min(height - 1, int(y * y_ratio))
        row = sy * width
        for x in range(new_w):
            sx = min(width - 1, int(x * x_ratio))
            out[y * new_w + x] = gray[row + sx]
    return bytes(out)


def resize_rgba_nearest(img, new_w, new_h):
    """Return (w, h, pixels) downsampled with nearest neighbour, preserving mode."""
    if (new_w, new_h) == (img.width, img.height):
        return img.width, img.height, img.pixels
    ch = img.channels
    src = img.pixels
    out = bytearray(new_w * new_h * ch)
    x_ratio = img.width / float(new_w)
    y_ratio = img.height / float(new_h)
    for y in range(new_h):
        sy = min(img.height - 1, int(y * y_ratio))
        for x in range(new_w):
            sx = min(img.width - 1, int(x * x_ratio))
            so = (sy * img.width + sx) * ch
            do = (y * new_w + x) * ch
            out[do : do + ch] = src[so : so + ch]
    return new_w, new_h, bytes(out)


def sample_image(img, side=512):
    """Downsample so the long edge is <= side, for bounded analysis cost."""
    if not img.has_pixels:
        return img
    longest = max(img.width, img.height)
    if longest <= side:
        return img
    scale = side / float(longest)
    new_w = max(1, int(round(img.width * scale)))
    new_h = max(1, int(round(img.height * scale)))
    w, h, px = resize_rgba_nearest(img, new_w, new_h)
    return ImageData(w, h, img.mode, px, img.format, img.backend, img.meta, img.truncated)


def dhash(img, hash_size=8):
    """64-bit difference hash; robust to resizing and mild re-encoding."""
    small = sample_image(img, 64)
    gray = to_gray(small)
    small_w, small_h = hash_size + 1, hash_size
    g = resize_gray_nearest(gray, small.width, small.height, small_w, small_h)
    bits = 0
    for y in range(hash_size):
        row = y * small_w
        for x in range(hash_size):
            bits = (bits << 1) | (1 if g[row + x] < g[row + x + 1] else 0)
    return "%016x" % bits


def ahash(img, hash_size=8):
    small = sample_image(img, 64)
    gray = to_gray(small)
    small = resize_gray_nearest(gray, small.width, small.height, hash_size, hash_size)
    avg = sum(small) // len(small)
    bits = 0
    for value in small:
        bits = (bits << 1) | (1 if value > avg else 0)
    return "%016x" % bits


def hamming_hex(a, b):
    if not a or not b or len(a) != len(b):
        return None
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def analyze(img, thresholds=None):
    """Single-pass image statistics used by every QC check."""
    th = thresholds or {}
    block_size = int(th.get("block_size", 16) or 16)
    work = sample_image(img, int(th.get("sample_side", 512) or 512))
    gray = to_gray(work)
    w, h = work.width, work.height
    n = w * h
    total = 0
    total_sq = 0
    clipped = 0
    semi_alpha = 0
    opaque_alpha = 0
    alpha_sum = 0
    sat_sum = 0
    colors = set()
    sample_cap = 200000
    step = max(1, n // sample_cap)
    counter = 0
    mode = work.mode
    px = work.pixels
    for i in range(0, n, step):
        if mode == "L":
            r = g = b = px[i]
            a = 255
        elif mode == "RGB":
            o = i * 3
            r, g, b = px[o], px[o + 1], px[o + 2]
            a = 255
        else:
            o = i * 4
            r, g, b, a = px[o], px[o + 1], px[o + 2], px[o + 3]
            alpha_sum += a
            if a < 250:
                opaque_alpha += 1
                if 10 < a < 245:
                    semi_alpha += 1
        lum = (r * 299 + g * 587 + b * 114) // 1000
        total += lum
        total_sq += lum * lum
        if lum >= 253 or lum <= 2:
            clipped += 1
        sat_sum += max(r, g, b) - min(r, g, b)
        if len(colors) < 65536:
            colors.add((r >> 3, g >> 3, b >> 3))
        counter += 1
    mean = total / float(counter) if counter else 0.0
    var = (total_sq / float(counter) - mean * mean) if counter else 0.0
    std = var ** 0.5 if var > 0 else 0.0

    # block flatness: share of blocks whose internal range is near zero
    flat_blocks = 0
    blocks = 0
    for by in range(0, h - block_size + 1, block_size):
        for bx in range(0, w - block_size + 1, block_size):
            blocks += 1
            lo = 255
            hi = 0
            for y in range(by, by + block_size, max(1, block_size // 4)):
                base = y * w
                for x in range(bx, bx + block_size, max(1, block_size // 4)):
                    v = gray[base + x]
                    if v < lo:
                        lo = v
                    if v > hi:
                        hi = v
            if hi - lo <= 2:
                flat_blocks += 1

    # edge density and quantisation banding ratio
    edges = 0
    nonzero_steps = 0
    tiny_steps = 0
    for y in range(h):
        base = y * w
        if y + 1 < h:
            nxt = base + w
            for x in range(w - 1):
                v = gray[base + x]
                dx = abs(v - gray[base + x + 1])
                dy = abs(v - gray[nxt + x])
                if dx > 24 or dy > 24:
                    edges += 1
                if dx:
                    nonzero_steps += 1
                    if dx <= 2:
                        tiny_steps += 1
    total_px = max(1, h * max(1, w - 1) if h > 1 else w)

    stats = {
        "sampled_width": w,
        "sampled_height": h,
        "mean_luma": round(mean, 2),
        "std_luma": round(std, 2),
        "unique_colors_est": len(colors),
        "clipped_ratio": round(clipped / float(counter), 5) if counter else 0.0,
        "flat_block_ratio": round(flat_blocks / float(blocks), 4) if blocks else 0.0,
        "edge_density": round(edges / float(total_px), 4) if total_px else 0.0,
        "banding_step_ratio": round(tiny_steps / float(nonzero_steps), 4) if nonzero_steps else 0.0,
        "mean_saturation": round(sat_sum / float(counter), 2) if counter else 0.0,
    }
    if mode == "RGBA":
        stats["alpha_coverage"] = round(opaque_alpha / float(counter), 4) if counter else 0.0
        stats["semi_transparent_ratio"] = round(semi_alpha / float(counter), 4) if counter else 0.0
        stats["mean_alpha"] = round(alpha_sum / float(counter), 2) if counter else 0.0
    else:
        stats["alpha_coverage"] = 0.0
        stats["semi_transparent_ratio"] = 0.0
        stats["mean_alpha"] = 255.0
    return stats
