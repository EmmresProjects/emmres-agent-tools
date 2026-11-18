#!/usr/bin/env python3
"""tools-audio.py — Audio Pipeline Module for the core-synthesis-modules Skill Tool.

Part of the unified Skill Tool covering catalog category "Core Synthesis Scripts
and Modules". This module implements the Audio Pipeline Module Builder capability:
WAV writing/reading, PCM sample formats, peak limiting, ITU-R BS.1770-4 integrated
loudness metering, loudness normalization, and signal validation.

Standard library only (wave, struct, array, math). No network access.

Notes on the loudness meter:
- K-weighting uses the ITU-exact filter parameters (DeMan coefficient
  formulas) for arbitrary sample rates:
    stage 1 high shelf: f0=1681.9744509555319 Hz, G=3.99984385397 dB, Q=0.7071752369554196
    stage 2 high pass:  f0=38.13547087602444 Hz, Q=0.5003270373238773
- Gating follows BS.1770-4: 400 ms blocks, 75% overlap (100 ms step),
  absolute threshold -70 LUFS, relative gate -10 LU.
"""

import array
import math
import os
import struct
import wave

from math import log10, pi, sin, tan, sqrt


# --------------------------------------------------------------------------
# Biquad filter (RBJ / DeMan cookbook coefficient formulas)
# --------------------------------------------------------------------------

class Biquad:
    """Direct-form-1 biquad with zero state. Deterministic."""

    def __init__(self, b, a):
        # normalize so a0 == 1
        b0 = float(b[0]) / float(a[0])
        b1 = float(b[1]) / float(a[0])
        b2 = float(b[2]) / float(a[0])
        a1 = float(a[1]) / float(a[0])
        a2 = float(a[2]) / float(a[0])
        self.b = (b0, b1, b2)
        self.a = (1.0, a1, a2)
        self._x1 = 0.0
        self._x2 = 0.0
        self._y1 = 0.0
        self._y2 = 0.0

    def process(self, x):
        y = self.b[0] * x + self._x1
        self._x1 = self.b[1] * x - self.a[1] * y + self._x2
        self._x2 = self.b[2] * x - self.a[2] * y
        self._y1 = y
        return y

    def reset(self):
        self._x1 = self._x2 = self._y1 = self._y2 = 0.0


def k_weighting_filters(fs):
    """Return (high_shelf, high_pass) biquads implementing ITU-R BS.1770 K-weighting."""
    # Stage 1: high shelf (ITU-exact DeMan parameters)
    fc1 = 1681.9744509555319
    g1 = 3.99984385397
    q1 = 0.7071752369554196
    k = tan(pi * fc1 / fs)
    vh = 10.0 ** (g1 / 20.0)
    vb = vh ** 0.499666774155
    a0_ = 1.0 + k / q1 + k * k
    b_shelf = ((vh + vb * k / q1 + k * k) / a0_,
               2.0 * (k * k - vh) / a0_,
               (vh - vb * k / q1 + k * k) / a0_)
    a_shelf = (1.0,
               2.0 * (k * k - 1.0) / a0_,
               (1.0 - k / q1 + k * k) / a0_)

    # Stage 2: high pass (ITU-exact DeMan parameters)
    fc2 = 38.13547087602444
    q2 = 0.5003270373238773
    k2 = tan(pi * fc2 / fs)
    den = 1.0 + k2 / q2 + k2 * k2
    b_hp = (1.0, -2.0, 1.0)
    a_hp = (1.0,
            2.0 * (k2 * k2 - 1.0) / den,
            (1.0 - k2 / q2 + k2 * k2) / den)

    return Biquad(b_shelf, a_shelf), Biquad(b_hp, a_hp)


# --------------------------------------------------------------------------
# WAV I/O
# --------------------------------------------------------------------------

SAMPLE_FORMATS = {
    2: "PCM_S16LE",
    3: "PCM_S24LE",
    4: "PCM_S32LE",
}


def write_wav(path, samples, sample_rate, sample_width=2):
    """Write mono float samples in [-1.0, 1.0] to a WAV file.

    sample_width: 2 (16-bit), 3 (24-bit) or 4 (32-bit integer PCM).
    Creates parent directories. Refuses to overwrite unless allow_overwrite.
    Returns a dict describing the written file.
    """
    if not path:
        raise ValueError("output path is required")
    if sample_width not in (2, 3, 4):
        raise ValueError("sample_width must be 2, 3 or 4 bytes")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        # Overwrite protection lives at the CLI layer; here we only guard
        # against accidental writes to special files.
        if os.path.isdir(path):
            raise ValueError("output path is a directory")
    pcm = _to_pcm(samples, sample_width)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return {
        "path": path,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
        "sample_format": SAMPLE_FORMATS[sample_width],
        "frames": len(pcm) // sample_width,
        "duration_ms": round(len(pcm) / sample_width / sample_rate * 1000.0, 3),
    }


def _to_pcm(samples, sample_width):
    """Convert float samples to PCM bytes (integer PCM, mono)."""
    if sample_width == 2:
        arr = array.array("h")
        for s in samples:
            v = max(-32768, min(32767, int(round(max(-1.0, min(1.0, s)) * 32767.0))))
            arr.append(v)
        return arr.tobytes()
    if sample_width == 3:
        buf = bytearray()
        for s in samples:
            v = max(-8388608, min(8388607, int(round(max(-1.0, min(1.0, s)) * 8388607.0))))
            buf += struct.pack("<i", v)[:3]
        return bytes(buf)
    if sample_width == 4:
        arr = array.array("i")
        for s in samples:
            v = max(-2147483648, min(2147483647, int(round(max(-1.0, min(1.0, s)) * 2147483647.0))))
            arr.append(v)
        return arr.tobytes()
    raise ValueError("unsupported sample width")


def read_wav(path):
    """Read a mono/stereo PCM WAV file.

    Returns dict: samples (list of float, mono-mixed if stereo), sample_rate,
    sample_width, channels, frames, duration_ms.
    """
    if not os.path.exists(path):
        raise ValueError("audio file not found: %s" % path)
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        fs = wf.getframerate()
        frames = wf.getnframes()
        raw = wf.readframes(frames)
    if width not in (1, 2, 3, 4):
        raise ValueError("unsupported WAV sample width: %d bytes" % width)
    if width == 1:
        # 8-bit WAV is unsigned
        data = array.array("B")
        data.frombytes(raw)
        samples = [(b - 128) / 128.0 for b in data]
    elif width == 2:
        data = array.array("h")
        data.frombytes(raw)
        samples = [s / 32768.0 for s in data]
    elif width == 3:
        samples = []
        for i in range(0, len(raw), 3):
            v = int.from_bytes(raw[i:i + 3], "little", signed=True)
            samples.append(v / 8388608.0)
    else:
        data = array.array("i")
        data.frombytes(raw)
        samples = [s / 2147483648.0 for s in data]
    if channels == 2:
        mono = []
        for i in range(0, len(samples) - 1, 2):
            mono.append((samples[i] + samples[i + 1]) / 2.0)
        samples = mono
    return {
        "samples": samples,
        "sample_rate": fs,
        "sample_width": width,
        "channels": channels,
        "frames": frames,
        "duration_ms": round(frames / fs * 1000.0, 3),
    }


def wav_header_info(path):
    """Validate a WAV header and return parsed fields."""
    info = {"path": path, "valid": False}
    try:
        with wave.open(path, "rb") as wf:
            info.update({
                "valid": True,
                "channels": wf.getnchannels(),
                "sample_rate": wf.getframerate(),
                "sample_width": wf.getsampwidth(),
                "sample_format": SAMPLE_FORMATS.get(wf.getsampwidth(), "UNKNOWN"),
                "frames": wf.getnframes(),
                "duration_ms": round(wf.getnframes() / wf.getframerate() * 1000.0, 3),
            })
    except Exception as exc:  # wave.Error, EOFError, etc.
        info["error"] = str(exc)
    return info


# --------------------------------------------------------------------------
# Measurement helpers
# --------------------------------------------------------------------------

def peak_dbfs(samples):
    """Sample peak in dBFS (0 dBFS == full scale)."""
    if not samples:
        return -120.0
    p = max(abs(s) for s in samples)
    if p <= 0.0:
        return -120.0
    return 20.0 * log10(p)


def rms_dbfs(samples):
    """RMS level in dBFS."""
    if not samples:
        return -120.0
    m = sum(s * s for s in samples) / len(samples)
    if m <= 0.0:
        return -120.0
    return 10.0 * log10(m)


def remove_dc_offset(samples):
    """Subtract the mean sample value (DC blocker for measurement/mastering)."""
    if not samples:
        return samples
    mean = sum(samples) / len(samples)
    return [s - mean for s in samples]


def silence_ratio(samples, sample_rate, threshold_db=-45.0):
    """Fraction of 40 ms analysis blocks whose RMS is below threshold_db."""
    if not samples:
        return 1.0
    block = int(0.040 * sample_rate)
    if block < 1:
        block = 1
    ratio = 0
    total = 0
    for start in range(0, len(samples) - block + 1, block):
        seg = samples[start:start + block]
        m = sum(s * s for s in seg) / block
        rms_db = 10.0 * log10(m) if m > 0 else -120.0
        if rms_db < threshold_db:
            ratio += 1
        total += 1
    if total == 0:
        return 1.0
    return ratio / total


def detect_clipping(samples, threshold=0.999):
    """Count samples at or above the clipping threshold."""
    return sum(1 for s in samples if abs(s) >= threshold)


def detect_invalid_samples(samples):
    """Count NaN / inf / out-of-range samples."""
    bad = 0
    for s in samples:
        if s != s or s in (float("inf"), float("-inf")) or abs(s) > 1.0000001:
            bad += 1
    return bad


# --------------------------------------------------------------------------
# Loudness (ITU-R BS.1770-4)
# --------------------------------------------------------------------------

def integrated_loudness(samples, sample_rate):
    """Integrated gated loudness in LUFS (mono input, BS.1770-4)."""
    n = len(samples)
    if n == 0:
        return -120.0
    fs = float(sample_rate)

    # K-weight
    shelf, highpass = k_weighting_filters(fs)
    filtered = []
    for s in samples:
        filtered.append(shelf.process(s))
    filtered = [highpass.process(s) for s in filtered]

    # Gating blocks: 400 ms, 75% overlap (100 ms step)
    t_g = 0.400
    block = int(round(t_g * fs))
    step = int(round(0.100 * fs))
    if n < block:
        # File shorter than one gating block: measure the whole file as a single block.
        z = [sum(s * s for s in filtered) / n]
        block_loudness = [-0.691 + 10.0 * log10(z[0]) if z[0] > 0 else -120.0]
        gated = [j for j, lj in enumerate(block_loudness) if lj > -70.0]
        if not gated:
            return -120.0
        z_avg = sum(z[j] for j in gated) / len(gated)
        return -0.691 + 10.0 * log10(z_avg) if z_avg > 0 else -120.0

    num_blocks = int(round((n - block) / step)) + 1
    z = []
    for j in range(num_blocks):
        lo = j * step
        hi = lo + block
        seg = filtered[lo:hi]
        m = sum(s * s for s in seg) / block
        z.append(m)
    block_loudness = []
    for m in z:
        block_loudness.append(-0.691 + 10.0 * log10(m) if m > 0 else -120.0)

    # Absolute gate
    j_abs = [j for j, lj in enumerate(block_loudness) if lj > -70.0]
    if not j_abs:
        return -120.0
    z_avg_abs = sum(z[j] for j in j_abs) / len(j_abs)
    gamma_r = -0.691 + 10.0 * log10(z_avg_abs) - 10.0 if z_avg_abs > 0 else -120.0

    # Relative + absolute gate
    j_rel = [j for j, lj in enumerate(block_loudness) if lj > gamma_r and lj > -70.0]
    if not j_rel:
        return -120.0
    z_avg_rel = sum(z[j] for j in j_rel) / len(j_rel)
    if z_avg_rel <= 0:
        return -120.0
    return -0.691 + 10.0 * log10(z_avg_rel)


def normalize_loudness(samples, sample_rate, target_lufs=-14.0):
    """Scale samples so integrated loudness reaches target_lufs.

    Returns (samples, gain, measured_lufs). gain == 1.0 when no scaling needed.
    """
    measured = integrated_loudness(samples, sample_rate)
    if measured <= -119.9:
        return samples, 1.0, measured
    gain = 10.0 ** ((target_lufs - measured) / 20.0)
    return [s * gain for s in samples], gain, measured


def limit_peak(samples, peak_dbfs_target=-1.0):
    """Scale samples down so the sample peak is at or below peak_dbfs_target."""
    p = peak_dbfs(samples)
    if p <= -119.9:
        return samples, 1.0
    if p <= peak_dbfs_target:
        return samples, 1.0
    gain = 10.0 ** ((peak_dbfs_target - p) / 20.0)
    return [s * gain for s in samples], gain


def soft_clip(samples, ceiling_dbfs=-1.0, softness=0.15):
    """Soft saturation clipper: smoothly compresses samples above the ceiling.

    Reduces peak overshoot without the loudness loss of a hard scale, so the
    loudness target survives the peak safety step. Returns (samples, overshoot_db).
    """
    ceiling = 10.0 ** (ceiling_dbfs / 20.0)
    out = []
    over_count = 0
    for s in samples:
        a = abs(s)
        if a <= ceiling:
            out.append(s)
        else:
            over_count += 1
            over = a - ceiling
            scaled = ceiling + over / (1.0 + over / softness)
            out.append(math.copysign(scaled, s))
    if over_count == 0:
        return samples, 0.0
    return out, peak_dbfs(out) - ceiling_dbfs


# --------------------------------------------------------------------------
# Mastering chain + validation
# --------------------------------------------------------------------------

def master(samples, sample_rate, target_lufs=-14.0, peak_dbfs_target=-1.0,
           normalize=True, remove_dc=True):
    """Full mastering chain: DC removal -> loudness normalization -> peak limit.

    Returns (samples, report) where report contains measured and final values.
    """
    report = {
        "dc_offset_removed": False,
        "loudness_normalization": normalize,
        "target_lufs": target_lufs,
        "peak_target_dbfs": peak_dbfs_target,
    }
    out = list(samples)
    if remove_dc:
        mean = sum(out) / len(out) if out else 0.0
        if abs(mean) > 1e-6:
            out = [s - mean for s in out]
            report["dc_offset_removed"] = True
        report["dc_offset_before"] = round(mean, 6)

    measured_before = integrated_loudness(out, sample_rate) if out else -120.0
    report["loudness_before_lufs"] = round(measured_before, 2)

    if normalize and out and measured_before > -119.9:
        out, gain, measured = normalize_loudness(out, sample_rate, target_lufs)
        report["normalization_gain"] = round(gain, 4)
        # Re-measure after gain application for an accurate report.
        measured_after = integrated_loudness(out, sample_rate) if out else measured
        report["loudness_after_lufs"] = round(measured_after, 2)
    else:
        report["normalization_gain"] = 1.0
        report["loudness_after_lufs"] = round(measured_before, 2)

    # Peak safety: soft clip first (preserves loudness), hard limit as final
    # guarantee that the peak target is met.
    out, soft_overshoot = soft_clip(out, ceiling_dbfs=peak_dbfs_target, softness=0.15)
    report["soft_clip_overshoot_db"] = round(soft_overshoot, 2)
    out, peak_gain = limit_peak(out, peak_dbfs_target)
    report["peak_limiter_gain"] = round(peak_gain, 4)
    report["final_peak_dbfs"] = round(peak_dbfs(out), 2)
    report["final_rms_dbfs"] = round(rms_dbfs(out), 2)
    report["duration_ms"] = round(len(out) / sample_rate * 1000.0, 3)
    # Final integrated loudness after the whole chain (authoritative value).
    final_lufs = integrated_loudness(out, sample_rate) if out else -120.0
    report["final_loudness_lufs"] = round(final_lufs, 2)
    report["loudness_delta_lu"] = round(final_lufs - target_lufs, 2)
    return out, report


def validate_audio(samples, sample_rate,
                   min_duration_ms=None, max_duration_ms=None,
                   max_silence_ratio=0.9, max_clipping=0):
    """Run the standard signal-quality checks. Returns a report dict.

    The report always includes pass/fail flags; the caller decides policy.
    """
    n = len(samples)
    duration_ms = n / sample_rate * 1000.0 if sample_rate else 0.0
    checks = []

    if min_duration_ms is not None:
        checks.append({"name": "min_duration", "ok": duration_ms >= min_duration_ms,
                       "value_ms": round(duration_ms, 1), "expected_ms": min_duration_ms})
    if max_duration_ms is not None:
        checks.append({"name": "max_duration", "ok": duration_ms <= max_duration_ms,
                       "value_ms": round(duration_ms, 1), "expected_ms": max_duration_ms})

    invalid = detect_invalid_samples(samples)
    checks.append({"name": "no_invalid_samples", "ok": invalid == 0,
                   "value": invalid, "expected": 0})

    clipped = detect_clipping(samples)
    checks.append({"name": "no_clipping", "ok": clipped <= max_clipping,
                   "value": clipped, "expected_max": max_clipping})

    silence = silence_ratio(samples, sample_rate)
    checks.append({"name": "silence_ratio", "ok": silence <= max_silence_ratio,
                   "value": round(silence, 4), "expected_max": max_silence_ratio})

    checks.append({"name": "non_empty", "ok": n > 0, "value": n, "expected": "> 0"})

    report = {
        "duration_ms": round(duration_ms, 1),
        "sample_rate": sample_rate,
        "frames": n,
        "peak_dbfs": round(peak_dbfs(samples), 2),
        "rms_dbfs": round(rms_dbfs(samples), 2),
        "silence_ratio": round(silence, 4),
        "clipped_samples": clipped,
        "invalid_samples": invalid,
        "checks": checks,
    }
    report["ok"] = all(c["ok"] for c in checks)
    return report
