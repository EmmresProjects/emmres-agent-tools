#!/usr/bin/env python3
"""Audio DSP: mastering chain and signal validation.

Uses numpy when importable (recommended: the offline venv provides it).
Pure-python fallbacks cover WAV stats/loudness; spectral checks are skipped
with an explicit note when numpy is unavailable.

Loudness approximates ITU-R BS.1770-4 (K-weighting + gated blocks).
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:  # pragma: no cover
    _np = None
    HAVE_NUMPY = False


def dbfs_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def lin_to_dbfs(x: float) -> float:
    return 20.0 * math.log10(x) if x > 0 else -math.inf


# ---------- K-weighting ----------

def _high_shelf_coefs(fs, f0=1681.974450955533, gain_db=3.99984385397, q=0.7071752369554196):
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    cw = math.cos(w0)
    sqrtA2 = 2 * math.sqrt(A) * alpha
    b0 = A * ((A + 1) + (A - 1) * cw + sqrtA2)
    b1 = -2 * A * ((A - 1) + (A + 1) * cw)
    b2 = A * ((A + 1) + (A - 1) * cw - sqrtA2)
    a0 = (A + 1) - (A - 1) * cw + sqrtA2
    a1 = 2 * ((A - 1) - (A + 1) * cw)
    a2 = (A + 1) - (A - 1) * cw - sqrtA2
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _high_pass_coefs(fs, f0=38.13547087602444, q=0.5003270373238773):
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    cw = math.cos(w0)
    b0 = (1 + cw) / 2
    b1 = -(1 + cw)
    b2 = (1 + cw) / 2
    a0 = 1 + alpha
    a1 = -2 * cw
    a2 = 1 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _biquad(samples, coefs):
    b0, b1, b2, a1, a2 = coefs
    x1 = x2 = y1 = y2 = 0.0
    out = []
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out.append(y)
        x2, x1 = x1, x
        y2, y1 = y1, y
    return out


def k_weight(samples, rate: int):
    return _biquad(_biquad(samples, _high_shelf_coefs(rate)), _high_pass_coefs(rate))


def integrated_lufs(samples, rate: int, channels: int = 1):
    """BS.1770-style gated integrated loudness (mono or dual-mono)."""
    if not samples:
        return -math.inf
    weighted = k_weight(samples, rate)
    block = int(0.400 * rate)
    hop = int(0.100 * rate)
    if block <= 0:
        return -math.inf
    z_blocks = []
    if len(weighted) < block:
        ms = sum(s * s for s in weighted) / len(weighted)
        if ms > 0:
            z_blocks.append(channels * ms)
    else:
        for start in range(0, len(weighted) - block + 1, hop):
            window = weighted[start:start + block]
            ms = sum(s * s for s in window) / block
            if ms > 0:
                z_blocks.append(channels * ms)
    if not z_blocks:
        return -math.inf
    ABS_GATE = 10.0 ** ((-70.0 + 0.691) / 10.0)
    gated = [z for z in z_blocks if z > ABS_GATE]
    if not gated:
        return -math.inf
    mean_gated = sum(gated) / len(gated)
    REL_GATE = mean_gated * (10.0 ** (-10.0 / 10.0))
    final = [z for z in gated if z > REL_GATE]
    if not final:
        final = gated
    mean_final = sum(final) / len(final)
    return -0.691 + 10.0 * math.log10(mean_final)


# ---------- basic signal stats ----------

def sample_peak(samples) -> float:
    return max((abs(s) for s in samples), default=0.0)


def dc_offset(samples) -> float:
    return sum(samples) / len(samples) if samples else 0.0


def estimate_true_peak(samples) -> float:
    """4x oversampled peak via linear interpolation (estimate)."""
    peak = sample_peak(samples)
    if len(samples) < 2:
        return peak
    best = peak
    for frac in (0.25, 0.5, 0.75):
        for i in range(len(samples) - 1):
            v = abs(samples[i] + (samples[i + 1] - samples[i]) * frac)
            if v > best:
                best = v
    return best


def resample_linear(samples, src_rate: int, dst_rate: int):
    if src_rate == dst_rate:
        return list(samples)
    ratio = dst_rate / src_rate
    out_len = max(1, int(round(len(samples) * ratio)))
    out = []
    last = len(samples) - 1
    for i in range(out_len):
        pos = i / ratio
        i0 = int(pos)
        frac = pos - i0
        i1 = min(i0 + 1, last)
        out.append(samples[i0] * (1 - frac) + samples[i1] * frac)
    return out


def remove_dc(samples):
    if not samples:
        return list(samples), 0.0
    mean = dc_offset(samples)
    return [s - mean for s in samples], mean


def declick(samples, absolute: float = 0.35, factor: float = 8.0):
    """Replace isolated impulse-like samples (possible clicks/pops).

    A sample is treated as a click only if it is an isolated spike: its
    deviation from the linear midpoint of its neighbours exceeds both an
    absolute floor and `factor` x the median local residual.
    Returns (samples, clicks_fixed).
    """
    n = len(samples)
    if n < 8:
        return list(samples), 0
    residuals = [abs(samples[i] - (samples[i - 1] + samples[i + 1]) / 2.0)
                 for i in range(1, n - 1)]
    rs = sorted(residuals)
    median = rs[len(rs) // 2] or 1e-6
    threshold = max(absolute, median * factor)
    out = list(samples)
    fixed = 0
    for idx, residual in enumerate(residuals, start=1):
        if residual > threshold:
            out[idx] = (samples[idx - 1] + samples[idx + 1]) / 2.0
            fixed += 1
    return out, fixed


def compress(samples, rate: int, threshold_db: float = -14.0, ratio: float = 3.0,
             attack_ms: float = 5.0, release_ms: float = 90.0):
    """Simple feed-forward peak compressor (crest-factor control before limiting).

    Returns (samples, {"threshold_db":..., "ratio":..., "max_gain_reduction_db":...}).
    """
    if not samples:
        return list(samples), {"threshold_db": threshold_db, "ratio": ratio,
                               "max_gain_reduction_db": 0.0}
    thr = dbfs_to_lin(threshold_db)
    attack = math.exp(-1.0 / (attack_ms / 1000.0 * rate))
    release = math.exp(-1.0 / (release_ms / 1000.0 * rate))
    env = 0.0
    out = []
    max_gr = 0.0
    for x in samples:
        level = abs(x)
        coeff = attack if level > env else release
        env = coeff * env + (1.0 - coeff) * level
        if env > thr:
            gain = (thr + (env - thr) / ratio) / env
            gr = lin_to_dbfs(gain)
            if gr < max_gr:
                max_gr = gr
        else:
            gain = 1.0
        out.append(x * gain)
    return out, {"threshold_db": threshold_db, "ratio": ratio,
                 "max_gain_reduction_db": round(max_gr, 2)}


def trim_silence(samples, rate: int, threshold_db: float = -45.0, pad_ms: int = 40, min_keep_ms: int = 50):
    """Trim leading/trailing silence. Returns (samples, trimmed_ms_total)."""
    if not samples:
        return samples, 0.0
    thr = dbfs_to_lin(threshold_db)
    pad = int(rate * pad_ms / 1000)
    min_keep = int(rate * min_keep_ms / 1000)

    def frame_active(i):
        return abs(samples[i]) > thr

    start = 0
    while start < len(samples) and not frame_active(start):
        start += 1
    end = len(samples) - 1
    while end > start and not frame_active(end):
        end -= 1
    if start >= end:
        return list(samples), 0.0
    begin = max(0, start - pad)
    finish = min(len(samples), end + 1 + pad)
    if (finish - begin) < min_keep:
        begin, finish = 0, len(samples)
    trimmed = (begin + (len(samples) - finish)) / rate * 1000.0
    return list(samples[begin:finish]), trimmed


def apply_fades(samples, rate: int, fade_ms: int = 6):
    n = len(samples)
    fade = min(int(rate * fade_ms / 1000), n // 2)
    if fade <= 0:
        return list(samples)
    out = list(samples)
    for i in range(fade):
        g = i / fade
        out[i] *= g
        out[n - 1 - i] *= g
    return out


def soft_clip(samples, peak_dbfs: float = -1.0):
    """Attenuate (not clip) so sample peak <= peak_dbfs. Returns (samples, gain_db)."""
    target = dbfs_to_lin(peak_dbfs)
    peak = sample_peak(samples)
    if peak <= target or peak == 0.0:
        return list(samples), 0.0
    gain = target / peak
    return [s * gain for s in samples], lin_to_dbfs(gain)


def limit_peaks(samples, ceiling_lin: float, rate: int,
                lookahead_ms: float = 5.0, release_ms: float = 80.0):
    """Lookahead peak limiter guaranteeing |out| <= ceiling_lin.

    Required per-sample gain r_i = min(1, ceiling/|x_i|); each output sample's
    gain is the running minimum over the next `lookahead` samples (instant
    attack) followed by a smooth release recovery. Returns (samples, report).
    """
    n = len(samples)
    if n == 0:
        return list(samples), {"limited": False, "max_reduction_db": 0.0}
    lookahead = max(1, int(rate * lookahead_ms / 1000))
    release = math.exp(-1.0 / (release_ms / 1000.0 * rate))
    required = [ceiling_lin / abs(x) if abs(x) > ceiling_lin and x != 0.0 else 1.0
                for x in samples]
    out = []
    gain_env = 1.0
    min_gain = 1.0
    limited = False
    for i in range(n):
        lo = min(required[i:i + lookahead])
        if lo < gain_env:
            gain_env = lo  # instant attack
            limited = True
        else:
            gain_env = release * gain_env + (1.0 - release) * lo
        out.append(samples[i] * gain_env)
        if gain_env < min_gain:
            min_gain = gain_env
    return out, {"limited": limited,
                 "max_reduction_db": round(lin_to_dbfs(min_gain), 2) if limited else 0.0}


def normalize_loudness(samples, rate: int, target_lufs: float = -14.0, peak_dbfs: float = -1.0):
    """Gain to target LUFS, then brickwall-limit peaks to the dBFS ceiling."""
    measured = integrated_lufs(samples, rate)
    if math.isinf(measured):
        return list(samples), {"measured_lufs": None, "gain_db": 0.0, "limited": False,
                               "final_lufs": None, "limiter_max_reduction_db": 0.0}
    gain_db = target_lufs - measured
    gain = dbfs_to_lin(gain_db)
    out = [s * gain for s in samples]
    out, limiter = limit_peaks(out, dbfs_to_lin(peak_dbfs), rate)
    final = integrated_lufs(out, rate)
    return out, {"measured_lufs": round(measured, 2), "gain_db": round(gain_db, 2),
                 "limited": limiter["limited"],
                 "limiter_max_reduction_db": limiter["max_reduction_db"],
                 "final_lufs": (None if math.isinf(final) else round(final, 2))}


def spectral_flatness(samples):
    """Geometric mean / arithmetic mean of magnitude spectrum (numpy only)."""
    if not HAVE_NUMPY or len(samples) < 64:
        return None
    window = _np.hanning(len(samples))
    spec = _np.abs(_np.fft.rfft(_np.asarray(samples, dtype=_np.float64) * window)) + 1e-12
    geo = math.exp(float(_np.mean(_np.log(spec))))
    arith = float(_np.mean(spec))
    return geo / arith if arith > 0 else None


def noise_floor_dbfs(samples, rate: int, window_ms: int = 50):
    """Approx. noise floor: 10th percentile of windowed RMS, in dBFS."""
    win = max(1, int(rate * window_ms / 1000))
    rms_values = []
    for start in range(0, len(samples), win):
        seg = samples[start:start + win]
        if not seg:
            break
        rms = math.sqrt(sum(s * s for s in seg) / len(seg))
        rms_values.append(rms)
    if not rms_values:
        return -math.inf
    rms_values.sort()
    p10 = rms_values[max(0, len(rms_values) // 10 - 1)]
    return lin_to_dbfs(p10) if p10 > 0 else -math.inf


def silence_ratio(samples, rate: int, threshold_db: float = -45.0, window_ms: int = 20):
    thr = dbfs_to_lin(threshold_db)
    win = max(1, int(rate * window_ms / 1000))
    if not samples:
        return 1.0
    silent = 0
    total = 0
    for start in range(0, len(samples), win):
        seg = samples[start:start + win]
        if not seg:
            break
        rms = math.sqrt(sum(s * s for s in seg) / len(seg))
        total += 1
        if rms < thr:
            silent += 1
    return silent / total if total else 1.0


def count_clicks(samples, absolute: float = 0.5):
    """Count physically suspicious consecutive-sample jumps (|x[n]-x[n-1]| >= 0.5 FS).

    At 22-48 kHz, ordinary speech effectively never produces a >50% full-scale
    single-sample jump; such events indicate clicks, pops or corruption.
    """
    return sum(1 for i in range(1, len(samples)) if abs(samples[i] - samples[i - 1]) >= absolute)


def buzz_heuristic(samples, rate: int):
    """Heuristic only — flags harmonic-heavy, noise-poor signals as possible buzz.

    This is NOT a substitute for listening review. Returns dict with suspicion
    level and the flatness value (None when numpy is unavailable).
    """
    flatness = spectral_flatness(samples)
    if flatness is None:
        return {"suspicion": "unknown", "spectral_flatness": None,
                "note": "numpy unavailable; spectral buzz heuristic skipped"}
    if flatness < 0.02:
        suspicion = "high"
    elif flatness < 0.05:
        suspicion = "moderate"
    else:
        suspicion = "low"
    return {"suspicion": suspicion, "spectral_flatness": round(flatness, 5),
            "note": "heuristic only; a human listening check remains authoritative"}


def validate_signal(samples, rate: int, channels: int, frames_declared: int | None = None,
                    duration_min: float | None = None, duration_max: float | None = None,
                    loudness_target: float | None = None, peak_dbfs: float = -1.0,
                    silence_threshold_db: float = -45.0, require_mono: bool = True) -> dict:
    errors, warnings = [], []
    duration = len(samples) / rate if rate else 0.0
    peak = sample_peak(samples)
    peak_db = lin_to_dbfs(peak)
    tp_db = lin_to_dbfs(estimate_true_peak(samples))
    clipped = sum(1 for s in samples if abs(s) >= 0.999)
    dc = dc_offset(samples)
    lufs = integrated_lufs(samples, rate, channels=1)
    sil = silence_ratio(samples, rate, silence_threshold_db)
    noise_floor = noise_floor_dbfs(samples, rate)
    clicks = count_clicks(samples)
    buzz = buzz_heuristic(samples, rate)

    if not samples:
        errors.append("audio contains zero frames")
    if frames_declared is not None and len(samples) != frames_declared:
        errors.append(f"declared frame count {frames_declared} != decoded {len(samples)}")
    if require_mono and channels != 1:
        warnings.append(f"expected mono, got {channels} channels")
    if duration_min is not None and duration < duration_min:
        errors.append(f"duration {duration:.2f}s below minimum {duration_min:.2f}s")
    if duration_max is not None and duration > duration_max:
        errors.append(f"duration {duration:.2f}s above maximum {duration_max:.2f}s")
    if peak_db > peak_dbfs + 0.05:
        errors.append(f"sample peak {peak_db:.2f} dBFS exceeds limit {peak_dbfs} dBFS")
    if tp_db > peak_dbfs + 1.0:
        warnings.append(f"estimated true peak {tp_db:.2f} dBTP exceeds {peak_dbfs} dBFS")
    if clipped:
        errors.append(f"{clipped} clipped sample(s) detected")
    if abs(dc) > 0.02:
        warnings.append(f"DC offset {dc:+.4f} exceeds 0.02; run master to remove it")
    if not math.isinf(lufs) and loudness_target is not None:
        if abs(lufs - loudness_target) > 2.0:
            warnings.append(f"integrated loudness {lufs:.1f} LUFS deviates from target {loudness_target} LUFS by >2 LU")
    if sil > 0.85:
        warnings.append(f"{sil * 100:.0f}% of windows are silent; check for missing speech or trailing silence")
    if clicks > 30:
        warnings.append(f"{clicks} impulsive jump(s) detected; possible clicks/pops")
    if buzz["suspicion"] == "high":
        warnings.append("harmonic-heavy spectrum flagged by buzz heuristic; verify by listening")

    ok = not errors
    return {"ok": ok, "errors": errors, "warnings": warnings,
            "duration_s": round(duration, 3), "sample_rate": rate, "channels": channels,
            "frames": len(samples),
            "peak_dbfs": (None if math.isinf(peak_db) else round(peak_db, 2)),
            "true_peak_estimate_dbfs": (None if math.isinf(tp_db) else round(tp_db, 2)),
            "loudness_lufs": (None if math.isinf(lufs) else round(lufs, 2)),
            "dc_offset": round(dc, 5), "clipped_samples": clipped,
            "silence_ratio": round(sil, 3),
            "noise_floor_dbfs": (None if math.isinf(noise_floor) else round(noise_floor, 1)),
            "click_events": clicks, "buzz": buzz,
            "numpy_used": HAVE_NUMPY}
