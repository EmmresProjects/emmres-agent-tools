#!/usr/bin/env python3
"""tools-audio-qa.py - objective measurement and gating for audio deliverables.

Implements:
  * BS.1770-4 style K-weighted loudness: integrated (gated), short-term maximum,
    loudness range, momentary loudness
  * 4x oversampled true-peak estimation
  * peak/RMS, crest factor, DC offset, clipping runs, click detection
  * silence and cue-boundary detection, noise-floor estimate
  * stereo correlation, mono fold-down delta
  * octave-band spectral balance via an in-house radix-2 FFT
  * policy gating against a delivery preset (pass / warn / fail)
  * regression comparison with a sample-aligned null test

Standard library only. Run directly for its own help:
  python3 tools-audio-qa.py --help
"""

from __future__ import annotations

import array
import cmath
import math
import os
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
IO_FILENAME = "tools-audio-io.py"


def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import importlib.util

    def load(name, filename):
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, os.path.join(here, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    common = load("aps_audio_common", COMMON_FILENAME)
    aio = load("aps_audio_io", IO_FILENAME)
    return common, aio


common, aio = _load()

DEFAULT_QA_POLICY = "asset-qa-policy.json"
DEFAULT_PRESETS = "asset-delivery-presets.json"


# --- filters ----------------------------------------------------------------
def _biquad(sr, kind, f0, q, gain_db=0.0):
    """RBJ cookbook biquad coefficients (normalised by a0)."""
    w0 = 2.0 * math.pi * f0 / sr
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    if kind == "high-shelf":
        a = 10.0 ** (gain_db / 40.0)
        alpha = sin_w0 / (2.0 * q)
        b0 = a * ((a + 1) + (a - 1) * cos_w0 + 2 * math.sqrt(a) * alpha)
        b1 = -2 * a * ((a - 1) + (a + 1) * cos_w0)
        b2 = a * ((a + 1) + (a - 1) * cos_w0 - 2 * math.sqrt(a) * alpha)
        a0 = (a + 1) - (a - 1) * cos_w0 + 2 * math.sqrt(a) * alpha
        a1 = 2 * ((a - 1) - (a + 1) * cos_w0)
        a2 = (a + 1) - (a - 1) * cos_w0 - 2 * math.sqrt(a) * alpha
    elif kind == "highpass":
        alpha = sin_w0 / (2.0 * q)
        b0 = (1 + cos_w0) / 2.0
        b1 = -(1 + cos_w0)
        b2 = (1 + cos_w0) / 2.0
        a0 = 1 + alpha
        a1 = -2 * cos_w0
        a2 = 1 - alpha
    elif kind == "lowpass":
        alpha = sin_w0 / (2.0 * q)
        b0 = (1 - cos_w0) / 2.0
        b1 = 1 - cos_w0
        b2 = (1 - cos_w0) / 2.0
        a0 = 1 + alpha
        a1 = -2 * cos_w0
        a2 = 1 - alpha
    elif kind == "peak":
        a = 10.0 ** (gain_db / 40.0)
        alpha = sin_w0 / (2.0 * q)
        b0 = 1 + alpha * a
        b1 = -2 * cos_w0
        b2 = 1 - alpha * a
        a0 = 1 + alpha / a
        a1 = -2 * cos_w0
        a2 = 1 - alpha / a
    else:
        raise common.ToolError("invalid_argument", "Unknown biquad type %r" % kind)
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def apply_biquad(samples, coeffs):
    b0, b1, b2, a1, a2 = coeffs
    out = array.array("d", bytes(8 * len(samples)))
    x1 = x2 = y1 = y2 = 0.0
    for i, x0 in enumerate(samples):
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out[i] = y0
        x2, x1 = x1, x0
        y2, y1 = y1, y0
    return out


def k_weight_coefficients(sample_rate):
    """Canonical BS.1770-4 K-weighting coefficients for any sample rate.

    Derived with the pre-warped bilinear transform used by the standard, which
    reproduces the published 48 kHz coefficient set exactly and stays valid for
    other rates (verified at 44.1 kHz and 48 kHz against EBU Tech 3341 signals).
    """
    # stage 1: high shelf
    f0 = 1681.974450955533
    gain = 3.999843853973347
    q = 0.7071752369554196
    k = math.tan(math.pi * f0 / sample_rate)
    vh = 10.0 ** (gain / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0,
             2.0 * (k * k - vh) / a0,
             (vh - vb * k / q + k * k) / a0,
             2.0 * (k * k - 1.0) / a0,
             (1.0 - k / q + k * k) / a0)
    # stage 2: RLB high pass
    f0 = 38.13547087602444
    q = 0.5003270373253953
    k = math.tan(math.pi * f0 / sample_rate)
    a0 = 1.0 + k / q + k * k
    highpass = (1.0 / a0, -2.0 / a0, 1.0 / a0,
                2.0 * (k * k - 1.0) / a0,
                (1.0 - k / q + k * k) / a0)
    return shelf, highpass


def k_weight(samples, sample_rate):
    """ITU-R BS.1770-4 K-weighting: shelf then RLB high-pass."""
    shelf, highpass = k_weight_coefficients(sample_rate)
    return apply_biquad(apply_biquad(samples, shelf), highpass)


# --- loudness ---------------------------------------------------------------
def _block_mean_squares(samples, block, step):
    frames = len(samples)
    if frames < block:
        return []
    values = []
    position = 0
    while position + block <= frames:
        total = 0.0
        for i in range(position, position + block):
            value = samples[i]
            total += value * value
        values.append(total / block)
        position += step
    return values


def _loudness_from_ms(mean_square_total):
    if mean_square_total <= 0:
        return -math.inf
    return -0.691 + 10.0 * math.log10(mean_square_total)


def integrated_loudness(weighted_channels, sample_rate):
    """Gated integrated loudness per BS.1770-4 (400 ms blocks, 75 percent overlap)."""
    block = int(round(0.4 * sample_rate))
    step = int(round(0.1 * sample_rate))
    if block <= 0:
        return -math.inf, 0
    per_channel = [_block_mean_squares(ch, block, step) for ch in weighted_channels]
    if not per_channel or not per_channel[0]:
        return -math.inf, 0
    count = min(len(ch) for ch in per_channel)
    loudness = []
    energies = []
    for index in range(count):
        total = sum(ch[index] for ch in per_channel)
        value = _loudness_from_ms(total)
        loudness.append(value)
        energies.append(total)
    absolute = [(l, e) for l, e in zip(loudness, energies) if l > -70.0]
    if not absolute:
        return -math.inf, 0
    relative_mean = sum(e for _, e in absolute) / len(absolute)
    relative_gate = _loudness_from_ms(relative_mean) - 10.0
    kept = [e for l, e in absolute if l > relative_gate]
    if not kept:
        return -math.inf, 0
    return _loudness_from_ms(sum(kept) / len(kept)), len(kept)


def loudness_range(weighted_channels, sample_rate):
    """EBU Tech 3342 style loudness range with 3 s blocks and 1 s steps."""
    block = int(round(3.0 * sample_rate))
    step = int(round(1.0 * sample_rate))
    per_channel = [_block_mean_squares(ch, block, step) for ch in weighted_channels]
    if not per_channel or not per_channel[0]:
        return None, None
    count = min(len(ch) for ch in per_channel)
    values = []
    for index in range(count):
        total = sum(ch[index] for ch in per_channel)
        values.append(_loudness_from_ms(total))
    absolute = [v for v in values if v > -70.0]
    if not absolute:
        return None, None
    mean_energy = sum(10.0 ** (v / 10.0) for v in absolute) / len(absolute)
    relative = 10.0 * math.log10(mean_energy) - 20.0
    kept = sorted(v for v in absolute if v > relative)
    if len(kept) < 2:
        return 0.0, kept[0] if kept else None
    low = kept[int(math.floor(0.10 * (len(kept) - 1)))]
    high = kept[int(math.floor(0.95 * (len(kept) - 1)))]
    return high - low, high


def short_term_max(weighted_channels, sample_rate):
    block = int(round(3.0 * sample_rate))
    step = int(round(0.1 * sample_rate))
    per_channel = [_block_mean_squares(ch, block, step) for ch in weighted_channels]
    if not per_channel or not per_channel[0]:
        return None
    count = min(len(ch) for ch in per_channel)
    best = None
    for index in range(count):
        value = _loudness_from_ms(sum(ch[index] for ch in per_channel))
        if value > -70.0 and (best is None or value > best):
            best = value
    return best


def true_peak(samples, sample_rate, oversample=4, half_taps=4):
    """True-peak estimate by windowed-sinc interpolation at `oversample` rate."""
    frames = len(samples)
    if frames == 0:
        return 0.0
    peak = 0.0
    for value in samples:
        magnitude = value if value >= 0 else -value
        if magnitude > peak:
            peak = magnitude
    if peak == 0.0:
        return 0.0
    table = aio._kernel_table(half_taps, 1.0, phases=oversample)
    taps = 2 * half_taps
    best = peak
    for n in range(frames - 1):
        value = samples[n]
        magnitude = value if value >= 0 else -value
        if magnitude < best * 0.5:
            continue
        for phase in range(1, oversample):
            row = table[phase]
            base = n - half_taps + 1
            acc = 0.0
            for k in range(taps):
                idx = base + k
                if 0 <= idx < frames:
                    acc += samples[idx] * row[k]
            acc_magnitude = acc if acc >= 0 else -acc
            if acc_magnitude > best:
                best = acc_magnitude
    return best


# --- spectral ---------------------------------------------------------------
def _fft(values):
    n = len(values)
    if n & (n - 1):
        raise common.ToolError("internal_error", "FFT length must be a power of two")
    data = [complex(v, 0.0) for v in values]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]
    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        wlen = cmath.exp(complex(0.0, angle))
        half = length // 2
        for start in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = data[start + k]
                v = data[start + k + half] * w
                data[start + k] = u + v
                data[start + k + half] = u - v
                w *= wlen
        length <<= 1
    return data


BANDS = [
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 120.0),
    ("low-mid", 120.0, 400.0),
    ("mid", 400.0, 1000.0),
    ("upper-mid", 1000.0, 3000.0),
    ("presence", 3000.0, 6000.0),
    ("brilliance", 6000.0, 12000.0),
    ("air", 12000.0, 20000.0),
]


def spectral_bands(samples, sample_rate, frame=None, max_frames=48):
    frame = frame or common.LIMITS["analysis_frame"]
    frames = len(samples)
    if frames < frame:
        frame = 1 << max(4, int(math.log(max(16, frames), 2)))
        if frame < 16:
            return {}, 0.0
    hop = frame // 2
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (frame - 1)) for i in range(frame)]
    powers = [0.0] * (frame // 2 + 1)
    used = 0
    position = 0
    while position + frame <= frames and used < max_frames:
        segment = [samples[position + i] * window[i] for i in range(frame)]
        spectrum = _fft(segment)
        for index in range(frame // 2 + 1):
            magnitude = spectrum[index]
            powers[index] += (magnitude.real * magnitude.real + magnitude.imag * magnitude.imag)
        used += 1
        position += hop
    if not used:
        return {}, 0.0
    bin_hz = sample_rate / float(frame)
    total = sum(powers) or 1e-30
    out = {}
    for name, low, high in BANDS:
        low_index = max(1, int(low / bin_hz))
        high_index = min(len(powers) - 1, int(high / bin_hz))
        energy = sum(powers[low_index:high_index + 1])
        out[name] = common.r2(10.0 * math.log10(max(energy, 1e-30) / total), 2)
    centroid = 0.0
    for index in range(1, len(powers)):
        centroid += (index * bin_hz) * powers[index]
    return out, common.r2(centroid / total, 1)


# --- event detection --------------------------------------------------------
def detect_clipping(samples, channels=1, threshold=0.9995, min_run=3):
    events = 0
    samples_at_ceiling = 0
    for ch in samples:
        run = 0
        for value in ch:
            if (value if value >= 0 else -value) >= threshold:
                run += 1
                samples_at_ceiling += 1
            else:
                if run >= min_run:
                    events += 1
                run = 0
        if run >= min_run:
            events += 1
    return {"clipped_runs": events, "ceiling_samples": samples_at_ceiling}


def detect_clicks(samples, sample_rate, sensitivity=8.0, min_gap_ms=20):
    """Isolated second-difference outliers, gated by local signal energy."""
    total = 0
    detail = []
    for index, ch in enumerate(samples):
        frames = len(ch)
        if frames < 8:
            continue
        window = max(32, int(0.005 * sample_rate))
        gaps = max(1, int(min_gap_ms * sample_rate / 1000.0))
        last = -gaps
        count = 0
        energy = 0.0
        for i in range(1, frames - 1):
            d2 = abs(ch[i - 1] - 2.0 * ch[i] + ch[i + 1])
            energy += d2 * d2
            if i % window == 0:
                rms = math.sqrt(energy / window)
                energy = 0.0
                threshold = max(0.02, sensitivity * rms)
                if d2 > threshold and (i - last) >= gaps:
                    last = i
                    count += 1
        total += count
        if count:
            detail.append({"channel": index, "clicks": count})
    return {"click_count": total, "channels": detail}


def detect_silence(samples, sample_rate, threshold_dbfs=-60.0, frame_ms=50):
    mono = samples[0] if len(samples) == 1 else None
    if mono is None:
        frames = min(len(ch) for ch in samples)
        mono = array.array("d", bytes(8 * frames))
        for ch in samples:
            for i in range(frames):
                mono[i] += ch[i] / len(samples)
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    threshold = common.db_to_lin(threshold_dbfs)
    silent = []
    position = 0
    while position + frame <= len(mono):
        total = 0.0
        for i in range(position, position + frame):
            total += mono[i] * mono[i]
        silent.append(math.sqrt(total / frame) < threshold)
        position += frame
    if not silent:
        return {"leading_silence": 0.0, "trailing_silence": 0.0, "silence_ratio": 0.0,
                "longest_silence": 0.0, "silent_frames": 0}
    leading = 0
    for flag in silent:
        if not flag:
            break
        leading += 1
    trailing = 0
    for flag in reversed(silent):
        if not flag:
            break
        trailing += 1
    longest = 0
    run = 0
    for flag in silent:
        if flag:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    seconds = frame / float(sample_rate)
    return {
        "leading_silence": common.r2(leading * seconds, 3),
        "trailing_silence": common.r2(trailing * seconds, 3),
        "silence_ratio": common.r2(sum(1 for f in silent if f) / float(len(silent)), 4),
        "longest_silence": common.r2(longest * seconds, 3),
        "silent_frames": sum(1 for f in silent if f),
    }


def noise_floor(samples, sample_rate, frame_ms=50, percentile=0.10):
    merged = array.array("d", bytes(8 * min(len(ch) for ch in samples)))
    frames = len(merged)
    if frames == 0:
        return None
    for ch in samples:
        for i in range(frames):
            merged[i] += ch[i] / len(samples)
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    values = []
    position = 0
    while position + frame <= frames:
        total = 0.0
        for i in range(position, position + frame):
            total += merged[i] * merged[i]
        values.append(math.sqrt(total / frame))
        position += frame
    if not values:
        return None
    values.sort()
    index = min(len(values) - 1, max(0, int(percentile * len(values))))
    return common.r2(common.lin_to_db(values[index]), 2)


def stereo_correlation(samples, sample_rate, frame_ms=100):
    if len(samples) < 2:
        return {"correlation": None, "min_frame_correlation": None, "mono_delta_db": None}
    left, right = samples[0], samples[1]
    frames = min(len(left), len(right))
    if frames == 0:
        return {"correlation": None, "min_frame_correlation": None, "mono_delta_db": None}
    sum_l = sum_r = sum_ll = sum_rr = sum_lr = 0.0
    for i in range(frames):
        l = left[i]
        r = right[i]
        sum_l += l
        sum_r += r
        sum_ll += l * l
        sum_rr += r * r
        sum_lr += l * r
    mean_l = sum_l / frames
    mean_r = sum_r / frames
    covariance = sum_lr / frames - mean_l * mean_r
    var_l = sum_ll / frames - mean_l * mean_l
    var_r = sum_rr / frames - mean_r * mean_r
    denominator = math.sqrt(max(var_l, 1e-30) * max(var_r, 1e-30))
    overall = covariance / denominator
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    minimum = None
    position = 0
    while position + frame <= frames:
        s_l = s_r = s_ll = s_rr = s_lr = 0.0
        for i in range(position, position + frame):
            l = left[i]
            r = right[i]
            s_l += l
            s_r += r
            s_ll += l * l
            s_rr += r * r
            s_lr += l * r
        ml = s_l / frame
        mr = s_r / frame
        cov = s_lr / frame - ml * mr
        vl = s_ll / frame - ml * ml
        vr = s_rr / frame - mr * mr
        if vl > 1e-12 and vr > 1e-12:
            value = cov / math.sqrt(vl * vr)
            minimum = value if minimum is None else min(minimum, value)
        position += frame
    mono = [(left[i] + right[i]) / 2.0 for i in range(frames)]
    mono_rms = math.sqrt(sum(v * v for v in mono) / frames) if frames else 0.0
    side_rms = math.sqrt(sum(((left[i] - right[i]) / 2.0) ** 2 for i in range(frames)) / frames) if frames else 0.0
    stereo_rms = math.sqrt(sum(left[i] ** 2 + right[i] ** 2 for i in range(frames)) / (2 * frames)) if frames else 0.0
    mono_loss = common.lin_to_db(mono_rms) - common.lin_to_db(stereo_rms) if frames else None
    return {
        "correlation": common.r2(overall, 3),
        "min_frame_correlation": common.r2(minimum, 3),
        "mono_rms_dbfs": common.r2(common.lin_to_db(mono_rms)),
        "side_rms_dbfs": common.r2(common.lin_to_db(side_rms)),
        "mono_fold_loss_db": common.r2(mono_loss),
    }


# --- analysis entry point ---------------------------------------------------
def analyze(path, policy=None, preset=None, max_seconds=None, skip_true_peak=False,
            timer=None):
    """Run the full objective analysis on one WAV file."""
    policy = policy or common.load_asset(DEFAULT_QA_POLICY)
    presets = common.load_asset(DEFAULT_PRESETS)
    buffer, meta = aio.read_wav(path, max_seconds=max_seconds)
    if timer:
        timer.check("decode")
    weighted = [k_weight(ch, buffer.sample_rate) for ch in buffer.channels]
    if timer:
        timer.check("k-weighting")
    integrated, gated_blocks = integrated_loudness(weighted, buffer.sample_rate)
    lra, lra_high = loudness_range(weighted, buffer.sample_rate)
    short_max = short_term_max(weighted, buffer.sample_rate)
    if timer:
        timer.check("loudness")
    peaks = []
    for ch in buffer.channels:
        peak = 0.0
        for value in ch:
            magnitude = value if value >= 0 else -value
            if magnitude > peak:
                peak = magnitude
        peaks.append(peak)
    sample_peak = max(peaks) if peaks else 0.0
    if skip_true_peak:
        tp = sample_peak
        tp_note = "skipped (sample peak reported)"
    else:
        tp = 0.0
        for ch in buffer.channels:
            tp = max(tp, true_peak(ch, buffer.sample_rate))
        tp_note = "4x windowed-sinc oversampling"
    if timer:
        timer.check("true peak")
    rms_values = []
    dc_values = []
    for ch in buffer.channels:
        total = 0.0
        for value in ch:
            total += value * value
        rms_values.append(math.sqrt(total / len(ch)) if len(ch) else 0.0)
        dc_values.append(sum(ch) / len(ch) if len(ch) else 0.0)
    overall_rms = math.sqrt(sum(v * v for v in rms_values) / len(rms_values)) if rms_values else 0.0
    clipping = detect_clipping(buffer.channels)
    clicks = detect_clicks(buffer.channels, buffer.sample_rate,
                           sensitivity=policy.get("click_sensitivity", 8.0))
    silence = detect_silence(buffer.channels, buffer.sample_rate,
                             threshold_dbfs=policy.get("silence_threshold_dbfs", -60.0))
    floor = noise_floor(buffer.channels, buffer.sample_rate)
    correlation = stereo_correlation(buffer.channels, buffer.sample_rate)
    bands, centroid = spectral_bands(buffer.channels[0] if buffer.channel_count == 1
                                     else [(buffer.channels[0][i] + buffer.channels[1][i]) / 2.0
                                           for i in range(min(len(buffer.channels[0]), len(buffer.channels[1])))],
                                     buffer.sample_rate)
    if timer:
        timer.check("event detection")
    metrics = {
        "integrity": {
            "structurally_valid": meta["structurally_valid"],
            "problems": meta["warnings"],
            "container": meta["container"],
            "sample_format": meta["sample_format"],
            "bit_depth": meta["bit_depth"],
            "sample_rate": meta["sample_rate"],
            "channels": meta["channels"],
            "frames": meta["frames"],
            "duration_seconds": meta["duration_seconds"],
            "file_size_bytes": meta["file_size_bytes"],
            "truncated": meta["truncated"],
        },
        "level": {
            "sample_peak_dbfs": common.r2(common.lin_to_db(sample_peak)),
            "true_peak_dbtp": common.r2(common.lin_to_db(tp)),
            "true_peak_method": tp_note,
            "rms_dbfs": common.r2(common.lin_to_db(overall_rms)),
            "crest_factor_db": common.r2(common.lin_to_db(sample_peak) - common.lin_to_db(overall_rms)),
            "channel_peaks_dbfs": [common.r2(common.lin_to_db(p)) for p in peaks],
            "clipped_runs": clipping["clipped_runs"],
            "ceiling_samples": clipping["ceiling_samples"],
        },
        "loudness": {
            "integrated_lufs": common.r2(integrated),
            "short_term_max_lufs": common.r2(short_max),
            "loudness_range_lu": common.r2(lra),
            "gated_blocks": gated_blocks,
            "standard": "ITU-R BS.1770-4 gating / EBU Tech 3342 range",
        },
        "stereo": correlation,
        "defects": {
            "click_count": clicks["click_count"],
            "click_channels": clicks["channels"],
            "dc_offset": [common.r2(v, 5) for v in dc_values],
            "dc_offset_max": common.r2(max((abs(v) for v in dc_values), default=0.0), 5),
            "noise_floor_dbfs": floor,
        },
        "structure": dict(silence, **{"spectral_centroid_hz": centroid}),
        "spectrum_db_relative": bands,
    }
    gates = evaluate_gates(metrics, preset or policy.get("default_preset", "streaming-music"),
                           presets, policy)
    report = {
        "file": meta["path"],
        "metrics": metrics,
        "gates": gates,
        "verdict": "fail" if any(g["status"] == "fail" for g in gates) else
                   ("warn" if any(g["status"] == "warn" for g in gates) else "pass"),
    }
    return report


# --- gating -----------------------------------------------------------------
def evaluate_gates(metrics, preset_id, presets, policy):
    presets = presets or common.load_asset(DEFAULT_PRESETS)
    policy = policy or common.load_asset(DEFAULT_QA_POLICY)
    preset = presets["presets"].get(preset_id)
    if preset is None:
        raise common.ToolError("unknown_preset", "Unknown delivery preset: %s" % preset_id,
                               {"available": sorted(presets["presets"].keys())})
    targets = preset["targets"]
    gates = []

    def add(gate, metric, value, limit, status, note):
        gates.append({"gate": gate, "metric": metric, "value": value, "limit": limit,
                      "status": status, "note": note, "preset": preset_id})

    loud = metrics["loudness"]["integrated_lufs"]
    if loud is None or loud == float("-inf"):
        add("loudness.integrated", "integrated_lufs", None, targets.get("integrated_lufs"),
            "fail", "No measurable program loudness (silence or too short)")
    else:
        target = targets.get("integrated_lufs")
        tolerance = targets.get("integrated_tolerance_lu", policy.get("integrated_tolerance_lu", 1.0))
        if target is None:
            add("loudness.integrated", "integrated_lufs", loud, None, "pass", "No target for this preset")
        else:
            delta = abs(loud - target)
            status = "pass" if delta <= tolerance else ("warn" if delta <= tolerance * 2 else "fail")
            add("loudness.integrated", "integrated_lufs", loud,
                {"target": target, "tolerance_lu": tolerance},
                status, "Delta %.2f LU from target" % (loud - target))

    tp_limit = targets.get("true_peak_dbtp")
    tp = metrics["level"]["true_peak_dbtp"]
    if tp_limit is not None:
        if tp is None:
            add("level.true_peak", "true_peak_dbtp", None, tp_limit, "fail", "Unmeasurable true peak")
        else:
            status = "pass" if tp <= tp_limit else ("warn" if tp <= tp_limit + 0.5 else "fail")
            add("level.true_peak", "true_peak_dbtp", tp, tp_limit, status,
                "Ceiling %.1f dBTP" % tp_limit)
    if targets.get("loudness_range_max") is not None:
        lra = metrics["loudness"]["loudness_range_lu"]
        limit = targets["loudness_range_max"]
        if lra is None:
            add("loudness.range", "loudness_range_lu", None, limit, "warn", "Not enough material for LRA")
        else:
            within = lra <= limit
            add("loudness.range", "loudness_range_lu", lra, limit,
                "pass" if within else "warn",
                "Inside the style guide" if within else "Creative dynamics exceed the guide")

    if policy.get("check_integrity", True):
        integrity = metrics["integrity"]
        add("integrity.structure", "structurally_valid", integrity["structurally_valid"], True,
            "pass" if integrity["structurally_valid"] else "fail",
            "; ".join(integrity["problems"]) or "Header and chunk table consistent")
        expected_rate = targets.get("sample_rate")
        if expected_rate:
            add("integrity.sample_rate", "sample_rate", integrity["sample_rate"], expected_rate,
                "pass" if integrity["sample_rate"] == expected_rate else "warn",
                "Preset expects %s Hz" % expected_rate)
        expected_bits = targets.get("bit_depth")
        if expected_bits:
            add("integrity.bit_depth", "bit_depth", integrity["bit_depth"], expected_bits,
                "pass" if integrity["bit_depth"] >= expected_bits else "warn",
                "Preset expects at least %s bits" % expected_bits)
        expected_channels = targets.get("channels")
        if expected_channels and preset_id not in ("sfx-library", "film-score-stems"):
            add("integrity.channels", "channels", integrity["channels"], expected_channels,
                "pass" if integrity["channels"] == expected_channels else "warn",
                "Preset expects %s channel(s)" % expected_channels)

    clip_limit = policy.get("max_clipped_runs", 0)
    if policy.get("check_clipping", True):
        runs = metrics["level"]["clipped_runs"]
        add("level.clipping", "clipped_runs", runs, clip_limit,
            "pass" if runs <= clip_limit else "fail",
            "No clipped runs" if runs == 0 else
            "%s runs of >=3 consecutive ceiling samples" % runs)

    if policy.get("check_dc", True):
        dc = metrics["defects"]["dc_offset_max"]
        limit = policy.get("max_dc_offset", 0.002)
        within = dc <= limit
        add("defects.dc_offset", "dc_offset_max", dc, limit,
            "pass" if within else "warn",
            "Offset within tolerance" if within else "DC offset shifts headroom")

    if policy.get("check_clicks", True):
        clicks = metrics["defects"]["click_count"]
        limit = policy.get("max_clicks_per_minute", 2)
        duration_min = max(1e-6, metrics["integrity"]["duration_seconds"] / 60.0)
        allowed = int(math.ceil(limit * duration_min))
        within = clicks <= allowed
        add("defects.clicks", "click_count", clicks, allowed,
            "pass" if within else "warn",
            "No transient outliers" if not clicks else
            ("Within the allowed count" if within else "Transient outliers detected"))

    if policy.get("check_stereo", True) and metrics["stereo"]["correlation"] is not None:
        correlation = metrics["stereo"]["correlation"]
        limit = policy.get("min_correlation", -0.2)
        add("stereo.correlation", "correlation", correlation, limit,
            "pass" if correlation >= limit else "fail",
            "Mono-safe correlation" if correlation >= limit else
            "Negative correlation risks mono cancellation")
        mono_limit = policy.get("max_mono_loss_db", -6.0)
        loss = metrics["stereo"]["mono_fold_loss_db"]
        if loss is not None:
            add("stereo.mono_fold", "mono_fold_loss_db", loss, mono_limit,
                "pass" if loss >= mono_limit else "warn",
                "Fold-down keeps %.1f dB of the stereo energy" % loss)

    if policy.get("check_silence", True):
        ratio = metrics["structure"]["silence_ratio"]
        limit = policy.get("max_silence_ratio", 0.98)
        add("structure.silence", "silence_ratio", ratio, limit,
            "pass" if ratio <= limit else "warn",
            "Programme audio present" if ratio <= limit else "Mostly silent material")

    return gates


# --- regression -------------------------------------------------------------
def regression(reference_path, candidate_path, tolerance=None, timer=None):
    tolerance = tolerance or {"loudness_lu": 0.5, "true_peak_db": 0.5, "duration_ms": 10,
                              "null_residual_db": -60.0}
    ref, ref_meta = aio.read_wav(reference_path)
    cand, cand_meta = aio.read_wav(candidate_path)
    if timer:
        timer.check("decode pair")
    findings = []
    differences = {}

    differences["duration_ms"] = common.r2((cand.duration - ref.duration) * 1000.0, 2)
    if abs(differences["duration_ms"]) > tolerance["duration_ms"]:
        findings.append({"check": "duration", "status": "fail",
                         "detail": "Duration difference of %.1f ms" % differences["duration_ms"]})
    if cand.sample_rate != ref.sample_rate:
        findings.append({"check": "sample_rate", "status": "warn",
                         "detail": "%s Hz vs reference %s Hz" % (cand.sample_rate, ref.sample_rate)})
    if cand.channel_count != ref.channel_count:
        findings.append({"check": "channels", "status": "warn",
                         "detail": "%s channels vs reference %s" % (cand.channel_count, ref.channel_count)})

    ref_integrated, _ = integrated_loudness([k_weight(ch, ref.sample_rate) for ch in ref.channels],
                                            ref.sample_rate)
    cand_integrated, _ = integrated_loudness([k_weight(ch, cand.sample_rate) for ch in cand.channels],
                                             cand.sample_rate)
    differences["loudness_delta_lu"] = common.r2(cand_integrated - ref_integrated, 3)
    if abs(differences["loudness_delta_lu"] or 0) > tolerance["loudness_lu"]:
        findings.append({"check": "loudness", "status": "fail",
                         "detail": "%.2f LU difference" % differences["loudness_delta_lu"]})

    ref_tp = max(true_peak(ch, ref.sample_rate) for ch in ref.channels) if ref.channels else 0.0
    cand_tp = max(true_peak(ch, cand.sample_rate) for ch in cand.channels) if cand.channels else 0.0
    differences["true_peak_delta_db"] = common.r2(common.lin_to_db(cand_tp) - common.lin_to_db(ref_tp), 3)
    if abs(differences["true_peak_delta_db"] or 0) > tolerance["true_peak_db"]:
        findings.append({"check": "true_peak", "status": "warn",
                         "detail": "%.2f dB difference in true peak" % differences["true_peak_delta_db"]})

    null = {"comparable": False}
    if (cand.sample_rate == ref.sample_rate and cand.channel_count == ref.channel_count and
            cand.frames == ref.frames):
        null["comparable"] = True
        residual = 0.0
        reference_energy = 0.0
        for ch_index in range(ref.channel_count):
            for i in range(ref.frames):
                diff = cand.channels[ch_index][i] - ref.channels[ch_index][i]
                residual += diff * diff
                reference_energy += ref.channels[ch_index][i] ** 2
        count = ref.frames * ref.channel_count
        residual_db = common.lin_to_db(math.sqrt(residual / count)) if count else -120.0
        reference_db = common.lin_to_db(math.sqrt(reference_energy / count)) if count else -120.0
        null.update({"residual_dbfs": common.r2(residual_db),
                     "reference_dbfs": common.r2(reference_db),
                     "relative_db": common.r2(residual_db - reference_db)})
        if residual_db - reference_db > tolerance["null_residual_db"]:
            findings.append({"check": "null_test", "status": "warn",
                             "detail": "Residual is %.1f dB below reference signal"
                                       % (residual_db - reference_db)})
    else:
        findings.append({"check": "null_test", "status": "skipped",
                         "detail": "Files differ in rate, channel count or length"})

    if timer:
        timer.check("regression")
    return {
        "reference": {"path": common.resolve_path(reference_path), "sample_rate": ref.sample_rate,
                      "channels": ref.channel_count, "duration_seconds": common.r2(ref.duration, 3)},
        "candidate": {"path": common.resolve_path(candidate_path), "sample_rate": cand.sample_rate,
                      "channels": cand.channel_count, "duration_seconds": common.r2(cand.duration, 3)},
        "tolerance": tolerance,
        "differences": differences,
        "null_test": null,
        "findings": findings,
        "verdict": "fail" if any(f["status"] == "fail" for f in findings) else
                   ("warn" if any(f["status"] == "warn" for f in findings) else "pass"),
    }


# --- markdown ---------------------------------------------------------------
def report_markdown(report):
    metrics = report["metrics"]
    integrity = metrics["integrity"]
    level = metrics["level"]
    loudness = metrics["loudness"]
    lines = []
    lines.append("# Audio QA report")
    lines.append("")
    lines.append("- File: `%s`" % report["file"])
    lines.append("- Verdict: **%s**" % report["verdict"].upper())
    lines.append("- Format: %s, %s bits, %s Hz, %s channel(s), %s s"
                 % (integrity["sample_format"], integrity["bit_depth"], integrity["sample_rate"],
                    integrity["channels"], integrity["duration_seconds"]))
    lines.append("")
    lines.append("## Measured values")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append("| Integrated loudness | %s LUFS |" % loudness["integrated_lufs"])
    lines.append("| Short-term max | %s LUFS |" % loudness["short_term_max_lufs"])
    lines.append("| Loudness range | %s LU |" % loudness["loudness_range_lu"])
    lines.append("| Sample peak | %s dBFS |" % level["sample_peak_dbfs"])
    lines.append("| True peak | %s dBTP |" % level["true_peak_dbtp"])
    lines.append("| RMS | %s dBFS |" % level["rms_dbfs"])
    lines.append("| Crest factor | %s dB |" % level["crest_factor_db"])
    lines.append("| Stereo correlation | %s |" % metrics["stereo"]["correlation"])
    lines.append("| Clicks detected | %s |" % metrics["defects"]["click_count"])
    lines.append("| Noise floor estimate | %s dBFS |" % metrics["defects"]["noise_floor_dbfs"])
    lines.append("| Spectral centroid | %s Hz |" % metrics["structure"]["spectral_centroid_hz"])
    lines.append("")
    lines.append("## Gates")
    lines.append("")
    lines.append("| Gate | Value | Limit | Status | Note |")
    lines.append("| --- | --- | --- | --- | --- |")
    for gate in report["gates"]:
        limit = gate["limit"]
        if isinstance(limit, dict):
            limit = ", ".join("%s=%s" % (k, v) for k, v in limit.items())
        lines.append("| %s | %s | %s | %s | %s |"
                     % (gate["gate"], gate["value"], limit, gate["status"], gate["note"]))
    lines.append("")
    lines.append("Measured objectively; creative judgements still need a listening pass.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    text = """tools-audio-qa.py - objective audio measurement and gating (library + help).

Functions:
  analyze(path, policy=None, preset=None, max_seconds=None, skip_true_peak=False)
  evaluate_gates(metrics, preset_id, presets, policy)
  regression(reference_path, candidate_path, tolerance=None)
  report_markdown(report)
  k_weight / integrated_loudness / loudness_range / true_peak / spectral_bands

Command line use goes through the suite entry point:
  python3 tools-audio-suite.py analyze --input master.wav --preset streaming-music
  python3 tools-audio-suite.py regression --reference v1.wav --candidate v2.wav
"""
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
