#!/usr/bin/env python3
"""tools-audio-io.py - WAV (RIFF) container reader, structural inspector, writer,
resampler, channel mapper, and dither stage.

Standard library only. Supports PCM 8/16/24/32-bit and IEEE float 32/64-bit,
plain and WAVE_FORMAT_EXTENSIBLE headers. Floating point buffers are float64 so
24-bit and 32-bit material round-trips without measurable loss.

Run directly for its own help:  python3 tools-audio-io.py --help
"""

from __future__ import annotations

import array
import math
import os
import struct
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"


def _common():
    if "common" in globals():
        return globals()["common"]
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import importlib.util

    if "aps_audio_common" in sys.modules:
        globals()["common"] = sys.modules["aps_audio_common"]
        return globals()["common"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), COMMON_FILENAME)
    spec = importlib.util.spec_from_file_location("aps_audio_common", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["aps_audio_common"] = module
    spec.loader.exec_module(module)
    globals()["common"] = module
    return module


FMT_PCM = 0x0001
FMT_FLOAT = 0x0003
FMT_EXTENSIBLE = 0xFFFE
SUPPORTED = {
    (FMT_PCM, 8): "pcm_u8",
    (FMT_PCM, 16): "pcm_s16",
    (FMT_PCM, 24): "pcm_s24",
    (FMT_PCM, 32): "pcm_s32",
    (FMT_FLOAT, 32): "float32",
    (FMT_FLOAT, 64): "float64",
}


class AudioBuffer(object):
    """Deinterleaved float64 audio. channels[i] is an array('d') of samples."""

    __slots__ = ("sample_rate", "channels", "source", "warnings")

    def __init__(self, sample_rate, channels, source=None):
        if sample_rate <= 0:
            raise _common().ToolError("invalid_audio", "Sample rate must be positive")
        if not channels:
            raise _common().ToolError("invalid_audio", "At least one channel is required")
        self.sample_rate = int(sample_rate)
        self.channels = channels
        self.source = source or {}
        self.warnings = []

    # -- basic properties ----------------------------------------------------
    @property
    def channel_count(self):
        return len(self.channels)

    @property
    def frames(self):
        return len(self.channels[0])

    @property
    def duration(self):
        return self.frames / float(self.sample_rate) if self.sample_rate else 0.0

    def copy(self):
        buf = AudioBuffer(self.sample_rate, [array.array("d", ch) for ch in self.channels],
                          dict(self.source))
        buf.warnings = list(self.warnings)
        return buf

    def peak(self):
        peak = 0.0
        for ch in self.channels:
            for value in ch:
                magnitude = value if value >= 0 else -value
                if magnitude > peak:
                    peak = magnitude
        return peak

    def rms(self, channel=None):
        chans = [self.channels[channel]] if channel is not None else self.channels
        total = 0.0
        count = 0
        for ch in chans:
            for value in ch:
                total += value * value
            count += len(ch)
        return math.sqrt(total / count) if count else 0.0

    def slice(self, start_frame, end_frame):
        start = max(0, int(start_frame))
        end = min(self.frames, int(end_frame))
        return AudioBuffer(self.sample_rate, [array.array("d", ch[start:end]) for ch in self.channels],
                           dict(self.source))

    def info(self):
        c = _common()
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channel_count,
            "frames": self.frames,
            "duration_seconds": c.r2(self.duration, 3),
            "peak_dbfs": c.r2(c.lin_to_db(self.peak())),
            "rms_dbfs": c.r2(c.lin_to_db(self.rms())),
        }


# --- reading ----------------------------------------------------------------
def _decode_samples(subformat, bits, data, channels, warnings):
    c = _common()
    try:
        if subformat == "pcm_u8":
            raw = array.array("B")
            raw.frombytes(data)
            values = [(v - 128) / 128.0 for v in raw]
        elif subformat == "pcm_s16":
            raw = array.array("h")
            raw.frombytes(data)
            if sys.byteorder == "big":
                raw.byteswap()
            values = [v / 32768.0 for v in raw]
        elif subformat == "pcm_s24":
            values = []
            for i in range(0, len(data) - 2, 3):
                low = data[i]
                mid = data[i + 1]
                high = data[i + 2]
                if high < 128:
                    val = (high << 16) | (mid << 8) | low
                else:
                    val = ((high << 16) | (mid << 8) | low) - 0x1000000
                values.append(val / 8388608.0)
        elif subformat == "pcm_s32":
            raw = array.array("i")
            raw.frombytes(data)
            if sys.byteorder == "big":
                raw.byteswap()
            values = [v / 2147483648.0 for v in raw]
        elif subformat == "float32":
            raw = array.array("f")
            raw.frombytes(data)
            if sys.byteorder == "big":
                raw.byteswap()
            values = [float(v) for v in raw]
        elif subformat == "float64":
            raw = array.array("d")
            raw.frombytes(data)
            if sys.byteorder == "big":
                raw.byteswap()
            values = [float(v) for v in raw]
        else:  # pragma: no cover - guarded by caller
            raise c.ToolError("unsupported_format", "Unsupported sample format %r" % (subformat,))
    except (ValueError, struct.error) as exc:
        raise c.ToolError("invalid_audio", "Failed to decode audio samples: %s" % exc)
    if len(values) % channels:
        trimmed = len(values) - (len(values) % channels)
        warnings.append("Partial final frame discarded (%d samples)" % (len(values) - trimmed))
        values = values[:trimmed]
    channels_out = [array.array("d", values[i::channels]) for i in range(channels)]
    return channels_out


def inspect(path, max_seconds=None):
    """Parse the RIFF structure and report every fact needed for validation."""
    c = _common()
    path = c.resolve_path(path, must_exist=True)
    if max_seconds is not None:
        c.seconds_within_limits(None, max_seconds)
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12:
            raise c.ToolError("invalid_audio", "File is too small to be a RIFF/WAVE file",
                              {"path": path, "bytes": size})
        riff, riff_size, wave = struct.unpack("<4sI4s", header)
        if riff != b"RIFF":
            raise c.ToolError("invalid_audio", "Not a RIFF file (missing RIFF magic)",
                              {"path": path, "magic": riff.decode("latin-1", "replace")},
                              hint="Only WAV/RIFF audio is supported. Convert other containers first.")
        if wave != b"WAVE":
            raise c.ToolError("invalid_audio", "RIFF file is not WAVE form",
                              {"form": wave.decode("latin-1", "replace")})
        chunks = []
        fmt = None
        data_offset = None
        data_size = None
        position = 12
        handle.seek(0, os.SEEK_END)
        file_size = handle.tell()
        while position + 8 <= file_size:
            handle.seek(position)
            cid, csize = struct.unpack("<4sI", handle.read(8))
            body_offset = position + 8
            padded = csize + (csize & 1)
            chunks.append({"id": cid.decode("latin-1", "replace"), "offset": position,
                           "size": csize, "padded": padded})
            if cid == b"fmt ":
                handle.seek(body_offset)
                fmt = handle.read(min(csize, 64))
            elif cid == b"data":
                data_offset = body_offset
                data_size = csize
            position = body_offset + padded
        truncated = position > file_size

    if fmt is None:
        raise c.ToolError("invalid_audio", "Missing fmt chunk", {"path": path})
    if data_offset is None:
        raise c.ToolError("invalid_audio", "Missing data chunk", {"path": path})
    if len(fmt) < 16:
        raise c.ToolError("invalid_audio", "fmt chunk is shorter than 16 bytes", {"path": path})

    audio_format, nchannels, sample_rate, byte_rate, block_align, bits = struct.unpack("<HHIIHH", fmt[:16])
    valid_bits = bits
    mask = None
    if audio_format == FMT_EXTENSIBLE:
        if len(fmt) < 40:
            raise c.ToolError("invalid_audio", "Truncated WAVE_FORMAT_EXTENSIBLE fmt chunk")
        cb_size, valid_bits, mask = struct.unpack("<HHI", fmt[16:24])
        sub = struct.unpack("<H", fmt[24:26])[0]
        audio_format = sub
    fmt_name = SUPPORTED.get((audio_format, bits))
    declared_data = data_size
    available = max(0, file_size - data_offset)
    effective_data = min(declared_data, available)
    truncated_data = declared_data > available
    frames = effective_data // block_align if block_align else 0
    problems = []
    if fmt_name is None:
        problems.append("Unsupported sample format (audio_format=%s, bits=%s)" % (audio_format, bits))
    if nchannels < 1:
        problems.append("Channel count is zero")
    if sample_rate < 8000 or sample_rate > 768000:
        problems.append("Unusual sample rate: %s Hz" % sample_rate)
    if block_align != nchannels * (bits // 8) and fmt_name:
        problems.append("block_align %s does not match channels*bits/8 (%s)"
                        % (block_align, nchannels * (bits // 8)))
    if truncated_data:
        problems.append("data chunk declares %s bytes but only %s are present (truncated)"
                        % (declared_data, available))
    if truncated:
        problems.append("RIFF chunk table overruns the end of the file")
    if riff_size + 8 != file_size:
        problems.append("RIFF size field %s does not match file size %s" % (riff_size + 8, file_size))
    duration = frames / float(sample_rate) if sample_rate else 0.0
    if max_seconds and duration > float(max_seconds):
        raise c.ToolError("limit_exceeded",
                          "Audio is %.2fs but --max-seconds is %s" % (duration, max_seconds),
                          {"duration_seconds": c.r2(duration), "limit_seconds": max_seconds})

    return {
        "path": path,
        "file_size_bytes": file_size,
        "riff_size_field": riff_size,
        "chunks": chunks,
        "audio_format": "float" if fmt_name in ("float32", "float64") else "pcm",
        "container": "RIFF/WAVE",
        "sample_format": fmt_name,
        "sample_rate": sample_rate,
        "channels": nchannels,
        "bit_depth": bits,
        "valid_bits": valid_bits,
        "channel_mask": mask,
        "byte_rate": byte_rate,
        "block_align": block_align,
        "data_bytes": effective_data,
        "frames": frames,
        "duration_seconds": c.r2(duration, 4),
        "truncated": truncated_data or truncated,
        "warnings": problems,
        "structurally_valid": not problems,
    }


def read_wav(path, max_seconds=None, channel_hint=None):
    """Read a WAV file into an AudioBuffer, validating structure first."""
    c = _common()
    meta = inspect(path, max_seconds=max_seconds)
    c.seconds_within_limits(meta["duration_seconds"], max_seconds, meta["channels"], meta["frames"])
    if meta["sample_format"] is None:
        raise c.ToolError("unsupported_format",
                          "Unsupported WAV sample format (bit depth %s)" % meta["bit_depth"],
                          {"audio_format": meta["audio_format"], "bit_depth": meta["bit_depth"]})
    if meta["truncated"]:
        raise c.ToolError("corrupt_audio", "Refusing to decode a truncated or inconsistent WAV file",
                          {"problems": meta["warnings"]},
                          hint="Run 'inspect' to confirm, or repair the file with an external tool.")
    with open(meta["path"], "rb") as handle:
        # locate the data chunk body again (inspect already parsed the layout)
        offset = None
        for chunk in meta["chunks"]:
            if chunk["id"] == "data":
                offset = chunk["offset"] + 8
                break
        handle.seek(offset)
        data = handle.read(meta["data_bytes"])
    warnings = []
    channels = _decode_samples(meta["sample_format"], meta["bit_depth"], data,
                               meta["channels"], warnings)
    buffer = AudioBuffer(meta["sample_rate"], channels,
                         {"path": meta["path"], "sample_format": meta["sample_format"],
                          "bit_depth": meta["bit_depth"]})
    buffer.warnings = warnings
    return buffer, meta


# --- writing ----------------------------------------------------------------
def _tpdf_dither(shape_order=0):
    """TPDF dither generator with optional first/second order noise shaping."""
    state = {"e1": 0.0, "e2": 0.0, "prev": 0.0}

    def shaped(value, lsb):
        noise = (__import__("random").random() - __import__("random").random()) * lsb
        if shape_order <= 0:
            return value + noise
        if shape_order == 1:
            out = value + noise + 0.5 * state["e1"]
        else:
            out = value + noise + 1.0 * state["e1"] - 0.5 * state["e2"]
        quantised = math.floor(out / lsb + 0.5) * lsb
        state["e2"] = state["e1"]
        state["e1"] = out - quantised
        return out

    return shaped


def write_wav(path, buffer, bit_depth=24, sample_format="pcm", dither=False,
              noise_shaping=0, seed=1):
    """Write PCM (8/16/24/32) or float (32/64) WAV with optional dither."""
    c = _common()
    import random

    random.seed(seed)
    if sample_format == "float":
        if bit_depth not in (32, 64):
            raise c.ToolError("invalid_argument", "Float output supports 32 or 64 bits")
    else:
        c.validate_choice(bit_depth, (8, 16, 24, 32), "bit depth")
    channels = buffer.channel_count
    frames = buffer.frames
    if channels > c.LIMITS["max_channels"]:
        raise c.ToolError("limit_exceeded", "Channel count exceeds %s" % c.LIMITS["max_channels"])

    lsb = 1.0 / float(2 ** (bit_depth - 1)) if sample_format == "pcm" else 0.0
    shaped = _tpdf_dither(noise_shaping) if (dither and sample_format == "pcm") else None
    clipped = 0
    body = bytearray()

    if sample_format == "float" and bit_depth == 32:
        out = array.array("f")
        for i in range(frames):
            for ch in buffer.channels:
                out.append(ch[i])
        if sys.byteorder == "big":
            out.byteswap()
        body = out.tobytes()
    elif sample_format == "float":
        out = array.array("d")
        for i in range(frames):
            for ch in buffer.channels:
                out.append(ch[i])
        if sys.byteorder == "big":
            out.byteswap()
        body = out.tobytes()
    elif bit_depth == 8:
        for i in range(frames):
            for ch in buffer.channels:
                value = ch[i]
                if shaped:
                    value = shaped(value, lsb)
                q = int(round(value * 128.0)) + 128
                if q < 0:
                    q = 0
                    clipped += 1
                elif q > 255:
                    q = 255
                    clipped += 1
                body.append(q)
    elif bit_depth == 16:
        out = array.array("h")
        for i in range(frames):
            for ch in buffer.channels:
                value = ch[i]
                if shaped:
                    value = shaped(value, lsb)
                q = int(round(value * 32768.0))
                if q > 32767:
                    q = 32767
                    clipped += 1
                elif q < -32768:
                    q = -32768
                    clipped += 1
                out.append(q)
        if sys.byteorder == "big":
            out.byteswap()
        body = out.tobytes()
    elif bit_depth == 32:
        out = array.array("i")
        for i in range(frames):
            for ch in buffer.channels:
                value = ch[i]
                if shaped:
                    value = shaped(value, lsb)
                q = int(round(value * 2147483648.0))
                if q > 2147483647:
                    q = 2147483647
                    clipped += 1
                elif q < -2147483648:
                    q = -2147483648
                    clipped += 1
                out.append(q)
        if sys.byteorder == "big":
            out.byteswap()
        body = out.tobytes()
    else:  # 24-bit
        for i in range(frames):
            for ch in buffer.channels:
                value = ch[i]
                if shaped:
                    value = shaped(value, lsb)
                q = int(round(value * 8388608.0))
                if q > 8388607:
                    q = 8388607
                    clipped += 1
                elif q < -8388608:
                    q = -8388608
                    clipped += 1
                body += struct.pack("<i", q)[:3]

    block_align = channels * (bit_depth // 8)
    byte_rate = buffer.sample_rate * block_align
    fmt_tag = FMT_FLOAT if sample_format == "float" else FMT_PCM
    fmt_chunk = struct.pack("<HHIIHH", fmt_tag, channels, buffer.sample_rate,
                            byte_rate, block_align, bit_depth)
    fact = struct.pack("<I", frames) if sample_format == "float" else b""
    chunks = [(b"fmt ", fmt_chunk)]
    if fact:
        chunks.append((b"fact", fact))
    chunks.append((b"data", bytes(body)))
    payload = bytearray(b"WAVE")
    for cid, data in chunks:
        payload += cid + struct.pack("<I", len(data)) + data
        if len(data) & 1:
            payload += b"\x00"
    riff = b"RIFF" + struct.pack("<I", len(payload)) + bytes(payload)

    path = c.resolve_path(path)
    c.ensure_dir(os.path.dirname(path) or ".")
    tmp = path + ".aps-tmp"
    try:
        with open(tmp, "wb") as handle:
            handle.write(riff)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise c.ToolError("io_error", "Could not write %s: %s" % (path, exc))
    return {"path": path, "bit_depth": bit_depth, "sample_format": sample_format,
            "channels": channels, "frames": frames, "sample_rate": buffer.sample_rate,
            "bytes": len(riff), "clipped_samples": clipped, "dither": bool(dither),
            "noise_shaping": noise_shaping}


# --- conversion primitives --------------------------------------------------
def _sinc(x):
    if x == 0.0:
        return 1.0
    return math.sin(math.pi * x) / (math.pi * x)


def _kernel_table(half_taps, cutoff, phases=256):
    """Polyphase windowed-sinc table: phases x (2*half_taps) coefficients."""
    rows = []
    taps = 2 * half_taps
    for p in range(phases):
        frac = p / float(phases)
        row = []
        total = 0.0
        for k in range(taps):
            x = (k - half_taps + 1) - frac
            if abs(x) >= half_taps:
                weight = 0.0
            else:
                window = 0.5 + 0.5 * math.cos(math.pi * x / half_taps)
                weight = cutoff * _sinc(cutoff * x) * window
            row.append(weight)
            total += weight
        if total:
            row = [w / total for w in row]
        rows.append(row)
    return rows


def resample(buffer, target_rate, quality="medium"):
    """Band-limited resampling with a windowed-sinc polyphase kernel."""
    c = _common()
    target_rate = int(target_rate)
    if target_rate < 8000 or target_rate > 768000:
        raise c.ToolError("invalid_argument", "Target sample rate out of supported range: %s" % target_rate)
    if target_rate == buffer.sample_rate:
        return buffer.copy(), {"resampled": False, "sample_rate": target_rate}
    half_taps = c.LIMITS["resample_half_taps"].get(
        c.validate_choice(quality, ("low", "medium", "high"), "quality"))
    ratio = target_rate / float(buffer.sample_rate)
    cutoff = min(1.0, ratio) * 0.96
    table = _kernel_table(half_taps, cutoff)
    phases = len(table)
    out_frames = int(math.floor(buffer.frames * ratio))
    if out_frames <= 0:
        raise c.ToolError("invalid_argument", "Resampling would produce zero frames")
    taps = 2 * half_taps
    source = buffer.channels
    out_channels = []
    for ch in source:
        out = array.array("d", bytes(8 * out_frames))
        length = len(ch)
        for n in range(out_frames):
            pos = n / ratio
            i0 = int(pos)
            frac = pos - i0
            phase = int(frac * phases)
            if phase >= phases:
                phase = phases - 1
            row = table[phase]
            base = i0 - half_taps + 1
            acc = 0.0
            for k in range(taps):
                idx = base + k
                if 0 <= idx < length:
                    acc += ch[idx] * row[k]
            out[n] = acc
        out_channels.append(out)
    result = AudioBuffer(target_rate, out_channels, dict(buffer.source))
    result.source["resampled_from"] = buffer.sample_rate
    return result, {"resampled": True, "sample_rate": target_rate,
                    "quality": quality, "half_taps": half_taps, "cutoff": round(cutoff, 4),
                    "frames_in": buffer.frames, "frames_out": out_frames}


def map_channels(buffer, mode):
    """Channel layout transforms: mono, stereo, swap, dual-mono, select:N, strip:N."""
    c = _common()
    mode = (mode or "").strip().lower()
    if mode in ("mono", "downmix"):
        if buffer.channel_count == 1:
            return buffer.copy(), {"mode": mode, "channels": 1}
        count = buffer.channel_count
        frames = buffer.frames
        summed = array.array("d", bytes(8 * frames))
        for ch in buffer.channels:
            for i, value in enumerate(ch):
                summed[i] += value
        scale = 1.0 / count
        for i in range(frames):
            summed[i] *= scale
        return AudioBuffer(buffer.sample_rate, [summed], dict(buffer.source)), \
            {"mode": mode, "channels": 1, "method": "equal-gain average of %d channels" % count}
    if mode in ("stereo", "upmix"):
        if buffer.channel_count == 2:
            return buffer.copy(), {"mode": mode, "channels": 2}
        if buffer.channel_count == 1:
            source = buffer.channels[0]
            return AudioBuffer(buffer.sample_rate,
                               [array.array("d", source), array.array("d", source)],
                               dict(buffer.source)), {"mode": mode, "channels": 2, "method": "dual mono"}
        left = buffer.channels[0]
        right = buffer.channels[1]
        return AudioBuffer(buffer.sample_rate,
                           [array.array("d", left), array.array("d", right)],
                           dict(buffer.source)), {"mode": mode, "channels": 2,
                                                  "method": "first two channels kept"}
    if mode == "swap":
        if buffer.channel_count < 2:
            raise c.ToolError("invalid_argument", "swap needs at least two channels")
        chans = [array.array("d", ch) for ch in buffer.channels]
        chans[0], chans[1] = chans[1], chans[0]
        return AudioBuffer(buffer.sample_rate, chans, dict(buffer.source)), {"mode": mode, "channels": len(chans)}
    if mode.startswith("select:"):
        index = int(mode.split(":", 1)[1])
        if not 0 <= index < buffer.channel_count:
            raise c.ToolError("invalid_argument", "Channel index %s out of range (0-%s)"
                              % (index, buffer.channel_count - 1))
        return AudioBuffer(buffer.sample_rate, [array.array("d", buffer.channels[index])],
                           dict(buffer.source)), {"mode": mode, "channels": 1}
    if mode.startswith("strip:"):
        index = int(mode.split(":", 1)[1])
        if not 0 <= index < buffer.channel_count or buffer.channel_count == 1:
            raise c.ToolError("invalid_argument", "Cannot strip channel %s" % index)
        chans = [array.array("d", ch) for i, ch in enumerate(buffer.channels) if i != index]
        return AudioBuffer(buffer.sample_rate, chans, dict(buffer.source)), \
            {"mode": mode, "channels": len(chans)}
    raise c.ToolError("invalid_argument",
                      "Unknown channel mode %r" % mode,
                      hint="Use mono, stereo, swap, select:N or strip:N.")


def concatenate(buffers, crossfade_seconds=0.0, sample_rate=None):
    """Join buffers with an equal-power crossfade, click-safe by construction."""
    c = _common()
    if not buffers:
        raise c.ToolError("invalid_argument", "Nothing to concatenate")
    rate = sample_rate or buffers[0].sample_rate
    prepared = []
    for buf in buffers:
        if buf.sample_rate != rate:
            buf, _ = resample(buf, rate, "medium")
        prepared.append(buf)
    channel_count = max(buf.channel_count for buf in prepared)
    normalised = []
    for buf in prepared:
        if buf.channel_count != channel_count:
            buf, _ = map_channels(buf, "mono" if channel_count == 1 else "stereo")
        normalised.append(buf)
    fade = max(0.0, float(crossfade_seconds or 0.0))
    total_frames = sum(buf.frames for buf in normalised)
    fade_frames = int(fade * rate)
    if fade_frames:
        total_frames -= fade_frames * (len(normalised) - 1)
        if total_frames <= 0:
            raise c.ToolError("invalid_argument", "Crossfade is longer than the joined audio")
    out = [array.array("d", bytes(8 * total_frames)) for _ in range(channel_count)]
    cursor = 0
    for index, buf in enumerate(normalised):
        frames = buf.frames
        start = 0
        if index > 0 and fade_frames:
            start = fade_frames
            for f in range(fade_frames):
                t = (f + 0.5) / fade_frames
                gain_in = math.sin(t * math.pi / 2.0)
                gain_out = math.cos(t * math.pi / 2.0)
                for ch in range(channel_count):
                    position = cursor - fade_frames + f
                    previous = out[ch][position]
                    out[ch][position] = previous * gain_out + buf.channels[ch][f] * gain_in
            cursor_out = cursor
        else:
            cursor_out = cursor
        body_frames = frames - start
        for ch in range(channel_count):
            out[ch][cursor_out:cursor_out + body_frames] = buf.channels[ch][start:]
        cursor = cursor_out + body_frames
    return AudioBuffer(rate, out, {"joined": len(normalised), "crossfade_seconds": fade}), \
        {"segments": len(normalised), "crossfade_seconds": fade, "frames_out": total_frames}


def generate_test_signal(kind, frequency=1000.0, amplitude_db=-23.0, seconds=3.0,
                         sample_rate=48000, channels=2, phase_shift=0.0):
    """Deterministic signals for calibration and self-tests."""
    c = _common()
    frames = int(round(seconds * sample_rate))
    amp = c.db_to_lin(amplitude_db)
    out = []
    for ch in range(channels):
        data = array.array("d", bytes(8 * frames))
        offset = phase_shift if ch else 0.0
        for i in range(frames):
            t = i / float(sample_rate)
            if kind == "sine":
                value = amp * math.sin(2.0 * math.pi * frequency * t + offset)
            elif kind == "dual-sine":
                value = amp * 0.5 * (math.sin(2.0 * math.pi * frequency * t) +
                                     math.sin(2.0 * math.pi * frequency * 3 * t))
            elif kind == "noise":
                import random
                random.seed(1000 + ch)
                value = amp * (random.random() * 2.0 - 1.0)
            elif kind == "silence":
                value = 0.0
            elif kind == "click-train":
                value = 0.0
                if i % int(sample_rate // 2) == 0:
                    value = amp
            else:
                raise c.ToolError("invalid_argument", "Unknown test signal %r" % kind)
            data[i] = value
        out.append(data)
    return AudioBuffer(sample_rate, out, {"generated": kind})


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    text = """tools-audio-io.py - WAV container reader, inspector, writer, resampler.

This module is a library used by tools-audio-suite.py. Importable functions:
  inspect(path, max_seconds=None) -> structural facts and warnings
  read_wav(path, max_seconds=None) -> (AudioBuffer, metadata)
  write_wav(path, buffer, bit_depth=24, sample_format="pcm", dither=False, noise_shaping=0)
  resample(buffer, target_rate, quality="medium")
  map_channels(buffer, mode)      # mono, stereo, swap, dual-mono, select:N, strip:N
  concatenate(buffers, crossfade_seconds=0.0)
  generate_test_signal(kind, frequency, amplitude_db, seconds, sample_rate, channels)

Use the suite entry point for command line work, for example:
  python3 tools-audio-suite.py inspect --input take.wav
  python3 tools-audio-suite.py process --input in.wav --output out.wav --op resample:48000
"""
    if argv and argv[0] in ("--help", "-h", "help"):
        print(text)
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
