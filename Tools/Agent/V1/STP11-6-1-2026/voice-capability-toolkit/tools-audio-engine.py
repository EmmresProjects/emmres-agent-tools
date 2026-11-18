#!/usr/bin/env python3
"""Voice Capability Toolkit - shared audio engine.

Loaded by ``tools-audio-synthesis.py``, ``tools-audio-delivery.py``,
``tools-audio-analysis.py`` and ``tools-selftest.py``. It is not a user-facing
entry point (running it directly prints its role).

Contents
--------
* I/O:            read_audio, write_audio, audio_info
* Measurement:    peak/rms/dBFS, true peak, gated integrated loudness (BS.1770
                  style K-weighting), noise floor, clipping, silence regions,
                  VAD segments, pitch (autocorrelation), spectral centroid,
                  band energy, zero-crossing rate
* DSP:            biquad filters, EQ, high/low-pass, bandpass, noise gate,
                  spectral noise reduction, declick, de-esser, compressor,
                  look-ahead limiter, gain, normalize, trims, fades, echo,
                  saturation/drive, tremolo, resample, channel conversion,
                  simple stereo panning
* Synthesis:      deterministic formant speech synthesis from text with
                  prosody, emotion and attitude control (disclosed limitation:
                  intelligible and expressive, not neural-quality)

Everything is NumPy + SoundFile. No network, no shell, no arbitrary code.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys

sys.dont_write_bytecode = True
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(filename, modname):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load("tools-runtime-common.py", "_voice_rt")
np = RT.require_numpy()

SUPPORTED_FORMATS = {
    "wav": ("WAV", None),
    "flac": ("FLAC", None),
    "ogg": ("OGG", None),
    "opus": ("OGG", "OPUS"),
    "mp3": ("MP3", "MPEG_LAYER_III"),
    "aiff": ("AIFF", None),
}

SUB_TYPE_DEFAULTS = {
    "WAV": "PCM_16",
    "FLAC": "PCM_16",
    "OGG": "VORBIS",
    "MP3": "MPEG_LAYER_III",
    "AIFF": "PCM_16",
}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _soundfile():
    try:
        import soundfile as sf
    except ImportError:
        raise RT.ToolError(
            "SoundFile is required for audio input/output but is not installed.",
            code=RT.EXIT_UNAVAILABLE,
            hint="python3 -m pip install 'soundfile>=0.12'  (also install "
                 "libsndfile, e.g. apt-get install libsndfile1)",
            details={"import_error": "soundfile"},
        )
    return sf


def read_audio(path, *, max_seconds=None):
    """Read an audio file into float64 ``(samples, channels)`` plus samplerate."""
    if not path or not os.path.isfile(path):
        raise RT.ToolError(f"Audio file not found: {path}", code=RT.EXIT_NOT_FOUND)
    sf = _soundfile()
    try:
        info = sf.info(path)
    except Exception as exc:
        raise RT.ToolError(
            f"'{path}' is not a readable audio file: {exc}",
            code=RT.EXIT_INVALID_INPUT,
            hint="Supported: WAV, FLAC, OGG/Opus, MP3, AIFF. Convert first if "
                 "the format is unsupported.",
        )
    limit = max_seconds if max_seconds is not None else RT.MAX_AUDIO_SECONDS
    if info.frames / max(info.samplerate, 1) > limit:
        raise RT.ToolError(
            f"'{path}' is {info.frames / info.samplerate:.1f}s, above the "
            f"{limit}s processing limit.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Split the file, or raise the limit in the calling tool.",
        )
    try:
        data, samplerate = sf.read(path, dtype="float64", always_2d=True)
    except Exception as exc:
        raise RT.ToolError(f"Could not decode '{path}': {exc}",
                           code=RT.EXIT_INVALID_INPUT)
    return data, int(samplerate), info


def write_audio(path, data, samplerate, *, fmt="wav", subtype=None,
                metadata=None, overwrite=True):
    """Write audio, choosing the container from ``fmt`` (or the extension)."""
    sf = _soundfile()
    if not overwrite and os.path.exists(path):
        raise RT.ToolError(f"Refusing to overwrite existing file: {path}",
                           code=RT.EXIT_INVALID_INPUT,
                           hint="Pass --force to overwrite.")
    fmt_key = (fmt or os.path.splitext(path)[1].lstrip(".") or "wav").lower()
    if fmt_key not in SUPPORTED_FORMATS:
        raise RT.ToolError(
            f"Unsupported output format '{fmt}'.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Use one of: " + ", ".join(sorted(SUPPORTED_FORMATS)),
        )
    container, forced_subtype = SUPPORTED_FORMATS[fmt_key]
    chosen = subtype or forced_subtype or SUB_TYPE_DEFAULTS[container]
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    try:
        sf.write(path, data, samplerate, format=container, subtype=chosen,
                 **({"title": metadata} if metadata and container == "WAV" else {}))
    except Exception as exc:
        raise RT.ToolError(
            f"Could not write '{path}' as {container}/{chosen}: {exc}",
            code=RT.EXIT_UNAVAILABLE,
            hint="Try a WAV or FLAC output, or check that libsndfile was built "
                 "with this codec.",
        )
    return path


def audio_info(path):
    sf = _soundfile()
    info = sf.info(path)
    return {
        "path": path,
        "format": info.format,
        "subtype": info.subtype,
        "samplerate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_seconds": round(info.frames / max(info.samplerate, 1), 4),
        "bytes": os.path.getsize(path),
    }


def silence_of(seconds, samplerate, channels=1):
    return np.zeros((max(int(round(seconds * samplerate)), 1), channels))


def to_mono(data):
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        return data
    return data.mean(axis=1)


def to_channels(data, channels):
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    current = data.shape[1]
    if current == channels:
        return data
    if channels == 1:
        return data.mean(axis=1, keepdims=True)
    if current == 1:
        return np.repeat(data, channels, axis=1)
    if current > channels:
        return data[:, :channels]
    return np.concatenate([data, np.repeat(data[:, -1:], channels - current, axis=1)],
                          axis=1)


def resample(data, source_rate, target_rate):
    data = np.asarray(data, dtype=np.float64)
    if source_rate == target_rate:
        return data
    if source_rate <= 0 or target_rate <= 0:
        raise RT.ToolError("Sample rates must be positive.", code=RT.EXIT_INVALID_INPUT)
    if target_rate > source_rate:
        factor = target_rate // math.gcd(int(source_rate), int(target_rate))
        if factor > 16:
            factor = 16
        up = _resample_linear(data, source_rate, source_rate * factor)
        return _resample_linear(up, source_rate * factor, target_rate)
    return _resample_linear(data, source_rate, target_rate)


def _resample_linear(data, source_rate, target_rate):
    channels = data.shape[1] if data.ndim > 1 else 1
    flat = data.reshape(-1, channels)
    count = int(round(flat.shape[0] * target_rate / source_rate))
    if count <= 0:
        return np.zeros((1, channels))
    source_positions = np.arange(count) * (source_rate / target_rate)
    out = np.empty((count, channels), dtype=np.float64)
    xp = np.arange(flat.shape[0])
    for channel in range(channels):
        out[:, channel] = np.interp(source_positions, xp, flat[:, channel])
    return out if data.ndim > 1 else out[:, 0]


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

def peak_dbfs(data):
    mono = np.abs(to_mono(data))
    peak = float(np.max(mono)) if mono.size else 0.0
    return 20.0 * math.log10(max(peak, 1e-12))


def rms_dbfs(data):
    mono = to_mono(data)
    if mono.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(mono))))
    return 20.0 * math.log10(max(rms, 1e-12))


def true_peak_dbtp(data, *, oversample=4):
    """Estimate true peak with simple oversampling of the peak region."""
    mono = to_mono(data)
    if mono.size == 0:
        return -120.0
    if oversample <= 1:
        return peak_dbfs(mono)
    padded = np.concatenate([np.zeros(oversample), mono, np.zeros(oversample)])
    upsampled = _resample_linear(padded, 1, oversample)
    return peak_dbfs(upsampled)


def _k_weighting_filters(samplerate):
    """BS.1770 K-weighting pre-filter + RLB high-pass (biquad coefficients)."""
    # Coefficients are derived from the analog prototypes so the filters stay
    # stable at any sample rate we support.
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / samplerate)
    vh = 10 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b1 = (
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
    )
    a1 = (1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / samplerate)
    a0 = 1.0 + k / q + k * k
    b2 = (1.0, -2.0, 1.0)
    a2 = (1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0)
    return (b1, a1), (b2, a2)


def _scipy_lfilter():
    """Optional accelerators used only when SciPy is already installed."""
    try:
        from scipy.signal import lfilter
        return lfilter
    except Exception:
        return None


def _apply_biquad(data, coefficients):
    b, a = coefficients
    fast = _scipy_lfilter()
    if fast is not None:
        flat = data if data.ndim > 1 else data[:, None]
        filtered = fast(np.asarray(b, dtype=np.float64), np.asarray(a, dtype=np.float64),
                        flat, axis=0)
        return filtered if data.ndim > 1 else filtered[:, 0]
    flat = data.reshape(-1, data.shape[1] if data.ndim > 1 else 1)
    out = np.empty_like(flat)
    for channel in range(flat.shape[1]):
        x = flat[:, channel]
        y = np.empty_like(x)
        x1 = x2 = y1 = y2 = 0.0
        for index in range(x.size):
            value = (b[0] * x[index] + b[1] * x1 + b[2] * x2
                     - a[1] * y1 - a[2] * y2)
            x2, x1 = x1, x[index]
            y2, y1 = y1, value
            y[index] = value
        out[:, channel] = y
    return out if data.ndim > 1 else out[:, 0]


def integrated_loudness(data, samplerate, *, block_ms=400.0, hop_ms=100.0):
    """Gated integrated loudness in LUFS (BS.1770 style)."""
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    weighted = data.copy()
    for coefficients in _k_weighting_filters(samplerate):
        weighted = _apply_biquad(weighted, coefficients)
    block = max(int(samplerate * block_ms / 1000.0), 1)
    hop = max(int(samplerate * hop_ms / 1000.0), 1)
    if weighted.shape[0] < block:
        weighted = np.pad(weighted, ((0, block - weighted.shape[0]), (0, 0)))
    energies = []
    for start in range(0, weighted.shape[0] - block + 1, hop):
        segment = weighted[start:start + block]
        # Channel weights: 1.0 for L/R, 1.41 for centre (unused here).
        mean_square = float(np.mean(np.square(segment)))
        energies.append(mean_square)
    if not energies:
        return -70.0
    energies = np.asarray(energies)
    loudness = -0.691 + 10.0 * np.log10(np.maximum(energies, 1e-20))
    gated_absolute = loudness[loudness > -70.0]
    if gated_absolute.size == 0:
        return -70.0
    relative_threshold = (-0.691 + 10.0 * math.log10(
        float(np.mean(np.square(np.sqrt(np.power(10.0, (gated_absolute + 0.691) / 10.0))))))) - 10.0
    keep = gated_absolute[gated_absolute > relative_threshold]
    if keep.size == 0:
        keep = gated_absolute
    return float(-0.691 + 10.0 * math.log10(
        float(np.mean(np.power(10.0, (keep + 0.691) / 10.0)))))


def frame_rms(data, samplerate, *, frame_ms=25.0, hop_ms=10.0):
    mono = to_mono(data)
    frame = max(int(samplerate * frame_ms / 1000.0), 1)
    hop = max(int(samplerate * hop_ms / 1000.0), 1)
    if mono.size < frame:
        mono = np.pad(mono, (0, frame - mono.size))
    count = 1 + (mono.size - frame) // hop
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    frames = mono[index]
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    return rms, hop / samplerate


def silence_regions(data, samplerate, *, threshold_dbfs=-40.0, min_seconds=0.15):
    rms, hop = frame_rms(data, samplerate)
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    silent = db < threshold_dbfs
    regions = []
    start = None
    for index, flag in enumerate(silent):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            duration = (index - start) * hop
            if duration >= min_seconds:
                regions.append({"start": round(start * hop, 3),
                                "end": round(index * hop, 3),
                                "duration": round(duration, 3)})
            start = None
    if start is not None:
        duration = (len(silent) - start) * hop
        if duration >= min_seconds:
            regions.append({"start": round(start * hop, 3),
                            "end": round(len(silent) * hop, 3),
                            "duration": round(duration, 3)})
    return regions


def voice_activity(data, samplerate, *, threshold_dbfs=-40.0, min_seconds=0.12,
                   merge_gap=0.12):
    """Energy-based speech segments (bounded VAD, not a trained model)."""
    regions = silence_regions(data, samplerate, threshold_dbfs=threshold_dbfs,
                              min_seconds=0.01)
    mono = to_mono(data)
    duration = mono.size / max(samplerate, 1)
    speech = []
    cursor = 0.0
    for region in regions:
        if region["start"] - cursor > 0:
            speech.append([cursor, region["start"]])
        cursor = max(cursor, region["end"])
    if cursor < duration:
        speech.append([cursor, duration])
    merged = []
    for start, end in speech:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    segments = []
    for start, end in merged:
        if end - start >= min_seconds:
            segments.append({"start": round(start, 3), "end": round(end, 3),
                             "duration": round(end - start, 3)})
    return segments


def noise_floor_dbfs(data, samplerate, *, percentile=10.0):
    rms, _ = frame_rms(data, samplerate)
    if rms.size == 0:
        return -120.0
    value = float(np.percentile(rms, percentile))
    return max(20.0 * math.log10(max(value, 1e-12)), -120.0)


def clipping_report(data, *, threshold=0.999):
    mono = np.abs(to_mono(data))
    if mono.size == 0:
        return {"clipped_samples": 0, "clipped_runs": 0, "max_abs": 0.0,
                "peak_dbfs": -120.0}
    flags = mono >= threshold
    runs = 0
    previous = False
    for flag in flags:
        if flag and not previous:
            runs += 1
        previous = flag
    return {
        "clipped_samples": int(np.count_nonzero(flags)),
        "clipped_runs": int(runs),
        "max_abs": round(float(np.max(mono)), 6),
        "peak_dbfs": round(20.0 * math.log10(max(float(np.max(mono)), 1e-12)), 3),
    }


def dc_offset(data):
    mono = to_mono(data)
    return round(float(np.mean(mono)) if mono.size else 0.0, 8)


def zero_crossing_rate(data):
    mono = to_mono(data)
    if mono.size < 2:
        return 0.0
    return round(float(np.mean(np.abs(np.diff(np.signbit(mono).astype(np.int8))))), 5)


def spectral_centroid(data, samplerate):
    mono = to_mono(data)
    if mono.size < 1024:
        return 0.0
    window = np.hanning(min(2048, mono.size))
    frames = []
    hop = max(len(window) // 2, 1)
    for start in range(0, mono.size - len(window) + 1, hop * 8):
        segment = mono[start:start + len(window)] * window
        spectrum = np.abs(np.fft.rfft(segment))
        freqs = np.fft.rfftfreq(len(window), 1.0 / samplerate)
        total = spectrum.sum()
        if total > 0:
            frames.append(float((freqs * spectrum).sum() / total))
    return round(float(np.mean(frames)), 2) if frames else 0.0


def band_energy(data, samplerate, bands):
    mono = to_mono(data)
    if mono.size < 64:
        return {}
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(mono.size)))
    freqs = np.fft.rfftfreq(mono.size, 1.0 / samplerate)
    total = float(np.sum(np.square(spectrum))) or 1e-20
    result = {}
    for name, low, high in bands:
        mask = (freqs >= low) & (freqs < high)
        result[name] = round(10.0 * math.log10(
            max(float(np.sum(np.square(spectrum[mask]))), 1e-20) / total + 1e-20), 2)
    return result


def estimate_pitch(data, samplerate, *, fmin=60.0, fmax=500.0, frame_ms=40.0):
    """Autocorrelation pitch estimate over voiced frames."""
    mono = to_mono(data)
    frame = max(int(samplerate * frame_ms / 1000.0), 32)
    hop = frame // 2
    if mono.size < frame:
        return {"median_hz": None, "f0_contour": [], "voiced_ratio": 0.0}
    min_lag = max(int(samplerate / fmax), 2)
    max_lag = int(samplerate / fmin)
    values = []
    times = []
    for start in range(0, mono.size - frame + 1, hop):
        segment = mono[start:start + frame]
        if float(np.max(np.abs(segment))) < 1e-4:
            continue
        segment = segment - float(np.mean(segment))
        correlation = np.correlate(segment, segment, mode="full")[frame - 1:]
        if correlation[0] <= 0:
            continue
        window = correlation[min_lag:min(max_lag, correlation.size - 1)]
        if window.size == 0:
            continue
        peak_index = int(np.argmax(window)) + min_lag
        strength = float(correlation[peak_index] / correlation[0])
        if strength < 0.3:
            continue
        values.append(samplerate / peak_index)
        times.append(round(start / samplerate, 3))
    if not values:
        return {"median_hz": None, "f0_contour": [], "voiced_ratio": 0.0}
    values_array = np.asarray(values)
    return {
        "median_hz": round(float(np.median(values_array)), 2),
        "p10_hz": round(float(np.percentile(values_array, 10)), 2),
        "p90_hz": round(float(np.percentile(values_array, 90)), 2),
        "range_semitones": round(
            12.0 * math.log2(max(np.percentile(values_array, 90), 1e-6) /
                             max(np.percentile(values_array, 10), 1e-6)), 2),
        "voiced_ratio": round(len(values) / max(len(values) + 1, 1), 3),
        "f0_contour": [{"time": t, "hz": round(float(v), 2)}
                       for t, v in list(zip(times, values))[::4]][:400],
    }


def energy_contour(data, samplerate, *, hop_ms=50.0):
    rms, hop = frame_rms(data, samplerate, frame_ms=hop_ms, hop_ms=hop_ms)
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    stride = max(1, len(db) // 400)
    return [{"time": round(i * hop, 3), "db": round(float(v), 2)}
            for i, v in enumerate(db) if i % stride == 0]


def click_report(data, samplerate, *, threshold=6.0):
    """Detect impulsive discontinuities (clicks/pops) in the waveform."""
    mono = to_mono(data)
    if mono.size < 8:
        return {"click_count": 0, "events": []}
    derivative = np.abs(np.diff(mono))
    if derivative.size == 0:
        return {"click_count": 0, "events": []}
    median = float(np.median(derivative))
    mad = float(np.median(np.abs(derivative - median))) + 1e-9
    z = (derivative - median) / (1.4826 * mad)
    events = []
    last_time = -1.0
    for index in np.where(z > threshold)[0]:
        time = index / samplerate
        if time - last_time < 0.05:
            continue
        last_time = time
        events.append({"time": round(float(time), 3),
                       "magnitude": round(float(derivative[index]), 5)})
    return {"click_count": len(events), "events": events[:50]}


def spectral_flatness(data, samplerate=None):
    mono = to_mono(data)
    if mono.size < 512:
        return 0.0
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(mono.size))) + 1e-12
    geometric = float(np.exp(np.mean(np.log(spectrum))))
    arithmetic = float(np.mean(spectrum))
    return round(geometric / arithmetic, 5)


# ---------------------------------------------------------------------------
# DSP
# ---------------------------------------------------------------------------

def _biquad_peaking(samplerate, frequency, gain_db, q=1.0):
    a = 10 ** (gain_db / 40.0)
    omega = 2.0 * math.pi * frequency / samplerate
    alpha = math.sin(omega) / (2.0 * q)
    cos_omega = math.cos(omega)
    b = (1 + alpha * a, -2 * cos_omega, 1 - alpha * a)
    denom = 1 + alpha / a
    a_coeffs = (denom, -2 * cos_omega, 1 - alpha / a)
    return (tuple(v / denom for v in b), tuple(v / denom for v in a_coeffs))


def _biquad_highpass(samplerate, frequency, q=0.7071):
    omega = 2.0 * math.pi * min(frequency, samplerate * 0.49) / samplerate
    alpha = math.sin(omega) / (2.0 * q)
    cos_omega = math.cos(omega)
    b = ((1 + cos_omega) / 2, -(1 + cos_omega), (1 + cos_omega) / 2)
    a = (1 + alpha, -2 * cos_omega, 1 - alpha)
    return (tuple(v / a[0] for v in b), tuple(v / a[0] for v in a))


def _biquad_lowpass(samplerate, frequency, q=0.7071):
    omega = 2.0 * math.pi * min(frequency, samplerate * 0.49) / samplerate
    alpha = math.sin(omega) / (2.0 * q)
    cos_omega = math.cos(omega)
    b = ((1 - cos_omega) / 2, 1 - cos_omega, (1 - cos_omega) / 2)
    a = (1 + alpha, -2 * cos_omega, 1 - alpha)
    return (tuple(v / a[0] for v in b), tuple(v / a[0] for v in a))


def apply_eq(data, samplerate, *, highpass_hz=None, lowpass_hz=None, bands=None):
    out = np.asarray(data, dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    if highpass_hz:
        out = _apply_biquad(out, _biquad_highpass(samplerate, float(highpass_hz)))
    if lowpass_hz:
        out = _apply_biquad(out, _biquad_lowpass(samplerate, float(lowpass_hz)))
    for band in bands or []:
        frequency = float(band.get("frequency_hz", 0))
        if not (20.0 <= frequency <= samplerate / 2 - 100):
            continue
        out = _apply_biquad(out, _biquad_peaking(
            samplerate, frequency, float(band.get("gain_db", 0.0)),
            float(band.get("q", 1.0))))
    return out


def bandpass(data, samplerate, low_hz, high_hz):
    return apply_eq(data, samplerate, highpass_hz=low_hz, lowpass_hz=high_hz)


def apply_gain(data, gain_db):
    return np.asarray(data, dtype=np.float64) * (10 ** (gain_db / 20.0))


def normalize_peak(data, target_dbfs=-1.0):
    current = peak_dbfs(data)
    if current <= -119:
        return np.asarray(data, dtype=np.float64)
    return apply_gain(data, target_dbfs - current)


def fade(data, samplerate, *, fade_in=0.0, fade_out=0.0):
    out = np.array(data, dtype=np.float64, copy=True)
    total = out.shape[0]
    if fade_in > 0:
        length = min(int(fade_in * samplerate), total)
        if length > 1:
            ramp = np.linspace(0.0, 1.0, length)
            out[:length] *= ramp if out.ndim == 1 else ramp[:, None]
    if fade_out > 0:
        length = min(int(fade_out * samplerate), total)
        if length > 1:
            ramp = np.linspace(1.0, 0.0, length)
            out[-length:] *= ramp if out.ndim == 1 else ramp[:, None]
    return out


def trim_silence(data, samplerate, *, threshold_dbfs=-45.0, lead=0.05, tail=0.12,
                  keep_room=0.0):
    rms, hop = frame_rms(data, samplerate)
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    voiced = np.where(db >= threshold_dbfs)[0]
    if voiced.size == 0:
        return np.zeros((max(int(keep_room * samplerate), 1),
                         data.shape[1] if data.ndim > 1 else 1))
    start = max(int(voiced[0] * hop * samplerate) - int(lead * samplerate), 0)
    end = min(int(voiced[-1] * hop * samplerate) + int(tail * samplerate), data.shape[0])
    return data[start:end]


def noise_gate(data, samplerate, *, threshold_dbfs=-45.0, attack_ms=5.0,
               release_ms=120.0):
    mono = to_mono(data)
    rms, hop = frame_rms(data, samplerate)
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    gain = (db >= threshold_dbfs).astype(np.float64)
    attack = max(int((attack_ms / 1000.0) / hop), 1)
    release = max(int((release_ms / 1000.0) / hop), 1)
    smoothed = np.empty_like(gain)
    current = 0.0
    for index, target in enumerate(gain):
        coefficient = 1.0 / (attack if target > current else release)
        current += (target - current) * coefficient
        smoothed[index] = current
    per_sample = np.repeat(smoothed, max(int(round(hop * samplerate)), 1))
    if per_sample.size < mono.size:
        per_sample = np.pad(per_sample, (0, mono.size - per_sample.size), mode="edge")
    per_sample = per_sample[:mono.size]
    out = np.asarray(data, dtype=np.float64)
    if out.ndim == 1:
        return out * per_sample
    return out * per_sample[:, None]


def spectral_noise_reduction(data, samplerate, *, reduction_db=12.0,
                             frame_ms=32.0, hop_ratio=0.5):
    """Frame-wise spectral subtraction with a noise profile from quiet frames."""
    out = np.asarray(data, dtype=np.float64)
    mono_mode = out.ndim == 1
    if mono_mode:
        out = out[:, None]
    frame = int(samplerate * frame_ms / 1000.0)
    frame = max(256, 2 ** int(math.ceil(math.log2(frame))))
    hop = max(int(frame * hop_ratio), 1)
    window = np.hanning(frame)
    count = 1 + max((out.shape[0] - frame) // hop, 0)
    padded = np.pad(out, ((0, max(frame * 2, frame + hop * count - out.shape[0])), (0, 0)))
    result = np.zeros_like(padded)
    weight = np.zeros(padded.shape[0])
    noise_profile = None
    frames = []
    for index in range(count):
        start = index * hop
        chunk = padded[start:start + frame] * window[:, None]
        frames.append(np.fft.rfft(chunk, axis=0))
    if not frames:
        return out if not mono_mode else out[:, 0]
    magnitudes = np.abs(np.stack(frames))                     # frames x bins x ch
    energy = magnitudes.mean(axis=(1, 2))
    quiet = np.argsort(energy)[:max(1, count // 10)]
    noise_profile = np.mean(magnitudes[quiet], axis=0)
    floor = 10 ** (-abs(reduction_db) / 20.0)
    for index, spectrum in enumerate(frames):
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)
        cleaned = np.maximum(magnitude - noise_profile * 1.2, magnitude * floor)
        restored = np.fft.irfft(cleaned * np.exp(1j * phase), n=frame, axis=0)
        start = index * hop
        result[start:start + frame] += restored * window[:, None]
        weight[start:start + frame] += window ** 2
    weight[weight < 1e-8] = 1.0
    result /= weight[:, None] if result.ndim > 1 else weight
    result = result[:out.shape[0]]
    return result[:, 0] if mono_mode else result


def declick(data, samplerate, *, threshold=6.0, window=5):
    """Median-filter repair of impulsive clicks."""
    out = np.array(data, dtype=np.float64, copy=True)
    mono = to_mono(out)
    index = np.arange(window) - window // 2
    for position in range(mono.size):
        low = max(position + int(index[0]), 0)
        high = min(position + int(index[-1]) + 1, mono.size)
        if high - low < 3:
            continue
    derivative = np.abs(np.diff(mono))
    median = float(np.median(derivative)) if derivative.size else 0.0
    mad = float(np.median(np.abs(derivative - median))) + 1e-9
    flags = np.where((derivative - median) / (1.4826 * mad) > threshold)[0]
    for flag in flags:
        left = max(flag - window, 0)
        right = min(flag + window + 1, mono.size)
        segment = mono[left:right].copy()
        segment = np.sort(segment)[len(segment) // 2]
        out[flag, :] = segment if out.ndim > 1 else segment
    return out


def de_ess(data, samplerate, *, threshold_db=-28.0, frequency=6500.0,
           reduction_db=6.0):
    """Broadband-safe de-esser: reduces 5-9 kHz energy above a threshold."""
    out = np.asarray(data, dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    high = apply_eq(out, samplerate, highpass_hz=frequency, lowpass_hz=samplerate * 0.45)
    low = out - high
    rms, hop = frame_rms(high, samplerate)
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    factor = np.where(db > threshold_db,
                      10 ** (-abs(reduction_db) / 20.0), 1.0)
    per_sample = np.repeat(factor, max(int(round(hop * samplerate)), 1))[:out.shape[0]]
    if per_sample.size < out.shape[0]:
        per_sample = np.pad(per_sample, (0, out.shape[0] - per_sample.size), mode="edge")
    return low + high * per_sample[:, None]


def compress(data, samplerate, *, threshold_db=-18.0, ratio=3.0, attack_ms=8.0,
             release_ms=120.0, makeup_db=0.0):
    mono = to_mono(data)
    rms, hop = frame_rms(mono, samplerate, frame_ms=20.0, hop_ms=10.0)
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    gain_db = np.where(db > threshold_db,
                       (threshold_db - db) * (1.0 - 1.0 / ratio), 0.0)
    attack = max(int((attack_ms / 1000.0) / hop), 1)
    release = max(int((release_ms / 1000.0) / hop), 1)
    smoothed = np.empty_like(gain_db)
    current = 0.0
    for index, target in enumerate(gain_db):
        coefficient = 1.0 / (attack if target < current else release)
        current += (target - current) * coefficient
        smoothed[index] = current
    per_sample = np.repeat(smoothed, max(int(round(hop * samplerate)), 1))[:mono.size]
    if per_sample.size < mono.size:
        per_sample = np.pad(per_sample, (0, mono.size - per_sample.size), mode="edge")
    gains = 10 ** ((per_sample + makeup_db) / 20.0)
    out = np.asarray(data, dtype=np.float64)
    return out * gains if out.ndim == 1 else out * gains[:, None]


def limit(data, samplerate, *, ceiling_dbtp=-1.0, lookahead_ms=5.0,
          release_ms=60.0):
    """Look-ahead peak limiter with attack and release envelopes.

    The gain envelope never exceeds the value needed to keep the current sample
    under the ceiling, and it falls immediately (attack) while recovering
    smoothly (release), so a transient cannot slip through between frames.
    """
    out = np.asarray(data, dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    ceiling = 10 ** (ceiling_dbtp / 20.0)
    lookahead = max(int(samplerate * lookahead_ms / 1000.0), 1)
    magnitude = np.max(np.abs(out), axis=1)
    padded = np.pad(magnitude, (lookahead, 0), mode="edge")
    window = np.lib.stride_tricks.sliding_window_view(padded, lookahead + 1)
    peak_envelope = window.max(axis=1)[:magnitude.size]
    required = np.minimum(1.0, ceiling / np.maximum(peak_envelope, 1e-12))
    release_frames = max(int(samplerate * release_ms / 1000.0), 1)
    coefficient = math.exp(-1.0 / release_frames)
    smoothed = np.empty_like(required)
    current = 1.0
    for index in range(required.size):
        target = required[index]
        current = target if target < current else current * coefficient + target * (1.0 - coefficient)
        smoothed[index] = min(current, target) if target < 1.0 else current
        current = smoothed[index]
    limited = out * smoothed[:, None]
    # Hard safety clip: guarantees the sample peak never exceeds the ceiling.
    np.clip(limited, -ceiling, ceiling, out=limited)
    return limited.astype(np.float64)


def normalize_loudness(data, samplerate, target_lufs=-16.0, *, max_gain_db=24.0,
                       limit_ceiling=-1.0):
    """Iteratively match integrated loudness, then limit peaks."""
    out = np.asarray(data, dtype=np.float64)
    current = integrated_loudness(out, samplerate)
    if current <= -69.0:
        return out, {"applied_gain_db": 0.0, "lufs_before": round(current, 2),
                     "lufs_after": round(current, 2)}
    gain = RT.clamp(target_lufs - current, -max_gain_db, max_gain_db)
    out = apply_gain(out, gain)
    out = limit(out, samplerate, ceiling_dbtp=limit_ceiling)
    measured = integrated_loudness(out, samplerate)
    # Second pass trims the residual error created by limiting.
    for _ in range(3):
        error = target_lufs - measured
        if abs(error) < 0.15:
            break
        out = limit(apply_gain(out, error), samplerate, ceiling_dbtp=limit_ceiling)
        measured = integrated_loudness(out, samplerate)
    # Final sample-peak safety: inter-sample peaks can still sit above the
    # limiter ceiling, so trim once more using the measured true peak.
    measured_peak = true_peak_dbtp(out)
    if measured_peak > limit_ceiling:
        out = apply_gain(out, limit_ceiling - measured_peak - 0.05)
        measured = integrated_loudness(out, samplerate)
    return out, {"applied_gain_db": round(gain, 2),
                 "lufs_before": round(current, 2),
                 "lufs_after": round(measured, 2)}


def echo(data, samplerate, *, delay_ms=60.0, decay=0.25, repeats=3):
    out = np.array(data, dtype=np.float64, copy=True)
    if out.ndim == 1:
        out = out[:, None]
    delay = max(int(samplerate * delay_ms / 1000.0), 1)
    result = np.zeros((out.shape[0] + delay * repeats, out.shape[1]))
    result[:out.shape[0]] += out
    for repeat in range(1, repeats + 1):
        offset = delay * repeat
        result[offset:offset + out.shape[0]] += out * (decay ** repeat)
    return result


def saturate(data, samplerate, *, drive=0.0):
    if drive <= 0:
        return np.asarray(data, dtype=np.float64)
    amount = RT.clamp(drive, 0.0, 1.0)
    out = np.asarray(data, dtype=np.float64)
    shaped = np.tanh(out * (1.0 + amount * 6.0)) / math.tanh(1.0 + amount * 6.0)
    return out * (1.0 - amount) + shaped * amount


def tremolo(data, samplerate, *, rate_hz=5.0, depth=0.0):
    if depth <= 0:
        return np.asarray(data, dtype=np.float64)
    out = np.asarray(data, dtype=np.float64)
    times = np.arange(out.shape[0]) / samplerate
    envelope = 1.0 - depth * 0.5 * (1.0 + np.sin(2 * math.pi * rate_hz * times))
    return out * envelope if out.ndim == 1 else out * envelope[:, None]


def add_noise_floor(data, samplerate, *, level_dbfs=-45.0, seed=7):
    rng = np.random.default_rng(seed)
    out = np.asarray(data, dtype=np.float64)
    amplitude = 10 ** (level_dbfs / 20.0)
    noise = rng.normal(0.0, amplitude / 3.0, out.shape[0])
    if out.ndim > 1:
        noise = np.repeat(noise[:, None], out.shape[1], axis=1)
    return out + noise


def pan_stereo(data, position):
    """Place a mono source in a stereo field; ``position`` in [-1, 1]."""
    mono = to_mono(data)
    amount = RT.clamp(float(position), -1.0, 1.0)
    left = mono * math.sqrt(max((1.0 - amount) / 2.0, 1e-6))
    right = mono * math.sqrt(max((1.0 + amount) / 2.0, 1e-6))
    return np.stack([left, right], axis=1)


def concatenate(parts, *, gap_seconds=0.0, samplerate=24000, crossfade_ms=8.0):
    """Concatenate mono/stereo parts with a short equal-power crossfade."""
    parts = [np.asarray(p, dtype=np.float64) for p in parts if p is not None and len(p)]
    if not parts:
        return np.zeros((1, 1))
    channels = max(p.shape[1] if p.ndim > 1 else 1 for p in parts)
    normalized = [to_channels(p, channels) for p in parts]
    gap = np.zeros((max(int(gap_seconds * samplerate), 0), channels))
    crossfade = max(int(crossfade_ms / 1000.0 * samplerate), 1)
    out = normalized[0]
    for part in normalized[1:]:
        if gap.shape[0]:
            out = np.concatenate([out, gap], axis=0)
        if out.shape[0] > crossfade and part.shape[0] > crossfade:
            ramp = np.linspace(0.0, 1.0, crossfade)[:, None]
            tail = out[-crossfade:]
            head = part[:crossfade]
            blended = tail * (1 - ramp) + head * ramp
            out = np.concatenate([out[:-crossfade], blended, part[crossfade:]], axis=0)
        else:
            out = np.concatenate([out, part], axis=0)
    return out


# ---------------------------------------------------------------------------
# text -> phonemes -> speech
# ---------------------------------------------------------------------------

VOWELS = {
    "AA": {"f": (730, 1090, 2440), "dur": 0.135},
    "AE": {"f": (660, 1720, 2410), "dur": 0.140},
    "AH": {"f": (640, 1190, 2390), "dur": 0.115},
    "AO": {"f": (570, 840, 2410), "dur": 0.140},
    "AW": {"f": (570, 840, 2410), "dur": 0.150},
    "AY": {"f": (660, 1720, 2410), "dur": 0.155},
    "EH": {"f": (530, 1840, 2480), "dur": 0.125},
    "ER": {"f": (490, 1350, 1690), "dur": 0.140},
    "EY": {"f": (530, 1840, 2480), "dur": 0.150},
    "IH": {"f": (390, 1990, 2550), "dur": 0.105},
    "IY": {"f": (270, 2290, 3010), "dur": 0.120},
    "OW": {"f": (570, 840, 2410), "dur": 0.150},
    "OY": {"f": (570, 840, 2410), "dur": 0.160},
    "UH": {"f": (440, 1020, 2240), "dur": 0.105},
    "UW": {"f": (300, 870, 2240), "dur": 0.130},
}
DIPHTHONGS = {"AY", "EY", "OW", "AW", "OY"}

CONSONANTS = {
    "P": {"f": (400, 1100, 2100), "dur": 0.075, "type": "stop"},
    "B": {"f": (420, 1100, 2100), "dur": 0.070, "type": "stop", "voiced": True},
    "T": {"f": (400, 1600, 2600), "dur": 0.070, "type": "stop"},
    "D": {"f": (420, 1600, 2600), "dur": 0.065, "type": "stop", "voiced": True},
    "K": {"f": (400, 1900, 2400), "dur": 0.075, "type": "stop"},
    "G": {"f": (420, 1900, 2400), "dur": 0.070, "type": "stop", "voiced": True},
    "F": {"f": (400, 1400, 2400), "dur": 0.085, "type": "fricative", "noise_hz": 4200},
    "V": {"f": (400, 1400, 2400), "dur": 0.075, "type": "fricative", "noise_hz": 4000, "voiced": True},
    "TH": {"f": (400, 1600, 2500), "dur": 0.085, "type": "fricative", "noise_hz": 5200},
    "DH": {"f": (400, 1600, 2500), "dur": 0.070, "type": "fricative", "noise_hz": 4800, "voiced": True},
    "S": {"f": (400, 1700, 2600), "dur": 0.095, "type": "fricative", "noise_hz": 6400},
    "Z": {"f": (400, 1700, 2600), "dur": 0.080, "type": "fricative", "noise_hz": 6000, "voiced": True},
    "SH": {"f": (400, 1800, 2500), "dur": 0.095, "type": "fricative", "noise_hz": 3200},
    "ZH": {"f": (400, 1800, 2500), "dur": 0.080, "type": "fricative", "noise_hz": 3000, "voiced": True},
    "HH": {"f": (500, 1500, 2500), "dur": 0.060, "type": "fricative", "noise_hz": 1800},
    "CH": {"f": (400, 1800, 2500), "dur": 0.090, "type": "affricate", "noise_hz": 3000},
    "JH": {"f": (400, 1800, 2500), "dur": 0.085, "type": "affricate", "noise_hz": 2900, "voiced": True},
    "M": {"f": (280, 1100, 2200), "dur": 0.075, "type": "nasal", "voiced": True},
    "N": {"f": (280, 1600, 2500), "dur": 0.070, "type": "nasal", "voiced": True},
    "NG": {"f": (280, 2000, 2500), "dur": 0.075, "type": "nasal", "voiced": True},
    "L": {"f": (360, 1300, 2700), "dur": 0.070, "type": "liquid", "voiced": True},
    "R": {"f": (420, 1300, 1600), "dur": 0.070, "type": "liquid", "voiced": True},
    "W": {"f": (300, 700, 2200), "dur": 0.060, "type": "glide", "voiced": True},
    "Y": {"f": (280, 2200, 3000), "dur": 0.055, "type": "glide", "voiced": True},
}

LETTER_TO_PHONEME = {
    "a": "AE", "b": "B", "c": "K", "d": "D", "e": "EH", "f": "F", "g": "G",
    "h": "HH", "i": "IH", "j": "JH", "k": "K", "l": "L", "m": "M", "n": "N",
    "o": "AA", "p": "P", "q": "K", "r": "R", "s": "S", "t": "T", "u": "AH",
    "v": "V", "w": "W", "x": "KS", "y": "IY", "z": "Z",
}

IRREGULAR = {
    "a": "AH", "the": "DH AH", "of": "AH V", "to": "T UW", "and": "AE N D",
    "is": "IH Z", "are": "AA R", "was": "W AH Z", "were": "W ER", "been": "B IH N",
    "one": "W AH N", "two": "T UW", "three": "TH R IY", "four": "F AO R",
    "five": "F AY V", "six": "S IH K S", "seven": "S EH V AH N", "eight": "EY T",
    "nine": "N AY N", "ten": "T EH N", "eleven": "IH L EH V AH N",
    "twelve": "T W EH L V", "twenty": "T W EH N T IY", "hundred": "HH AH N D R AH D",
    "thousand": "TH AW Z AH N D", "million": "M IH L Y AH N",
    "voice": "V OY S", "voices": "V OY S IH Z", "people": "P IY P AH L",
    "would": "W UH D", "could": "K UH D", "should": "SH UH D",
    "because": "B IH K AH Z", "through": "TH R UW", "thought": "TH AO T",
    "enough": "IH N AH F", "laugh": "L AE F", "again": "AH G EH N",
    "great": "G R EY T", "break": "B R EY K", "heart": "HH AA R T",
    "friend": "F R EH N D", "language": "L AE NG G W IH JH",
    "answer": "AE N S ER", "island": "AY L AH N D", "colonel": "K ER N AH L",
    "business": "B IH Z N AH S", "beauty": "B Y UW T IY", "ocean": "OW SH AH N",
    "science": "S AY AH N S", "quiet": "K W AY AH T", "quite": "K W AY T",
    "their": "DH EH R", "there": "DH EH R", "they": "DH EY", "where": "W EH R",
    "what": "W AH T", "when": "W EH N", "who": "HH UW", "how": "HH AW",
    "your": "Y AO R", "you": "Y UW", "our": "AW ER", "my": "MAY",
    "audio": "AO D IY OW", "voiceover": "V OY S OW V ER",
    "synthetic": "S IH N TH EH T IH K", "production": "P R AH D AH K SH AH N",
    "narration": "N EH R EY SH AH N", "character": "K EH R AH K T ER",
    "emotion": "IH M OW SH AH N", "quality": "K W AA L AH T IY",
    "data": "D EY T AH", "either": "IY DH ER", "schedule": "S K EH JH UW L",
    "okay": "OW K EY", "ok": "OW K EY", "yes": "Y EH S", "no": "N OW",
    "please": "P L IY Z", "thank": "TH AE NG K", "thanks": "TH AE NG K S",
    "hello": "HH AH L OW", "goodbye": "G UH D B AY",
}

_VOWEL_LETTERS = set("aeiouy")
_DIGRAPHS = [
    ("ough", "AO"), ("augh", "AO"), ("tion", "SH AH N"), ("sion", "ZH AH N"),
    ("ture", "CH ER"), ("ight", "AY T"), ("eigh", "EY"),
    ("sh", "SH"), ("ch", "CH"), ("th", "TH"), ("ph", "F"), ("wh", "W"),
    ("ng", "NG"), ("ck", "K"), ("qu", "K W"), ("gh", "G"),
    ("ai", "EY"), ("ay", "EY"), ("ea", "IY"), ("ee", "IY"), ("ey", "EY"),
    ("ie", "IY"), ("oa", "OW"), ("oe", "OW"), ("oi", "OY"), ("oy", "OY"),
    ("oo", "UW"), ("ou", "AW"), ("ow", "AW"), ("au", "AO"), ("aw", "AO"),
    ("ue", "UW"), ("ui", "UW"), ("eu", "UW"), ("ew", "UW"),
    ("ar", "AA R"), ("er", "ER"), ("ir", "ER"), ("or", "AO R"), ("ur", "ER"),
]


def word_to_phonemes(word):
    """Grapheme-to-phoneme for English using rules plus a small exception list."""
    original = word
    word = re.sub(r"[^A-Za-z']", "", word).lower()
    if not word:
        return []
    if word in IRREGULAR:
        return IRREGULAR[word].split()
    if len(word) == 1:
        return [LETTER_TO_PHONEME.get(word, "AH")]
    if word.isupper() and len(word) <= 4 and original.isupper():
        return [LETTER_TO_PHONEME.get(ch, "AH") for ch in word]

    phonemes = []
    index = 0
    length = len(word)
    while index < length:
        matched = False
        for pattern, sound in _DIGRAPHS:
            if word.startswith(pattern, index):
                # Only treat "er/ar/or/ir/ur" as one unit in stressed positions.
                if pattern in ("ar", "er", "ir", "or", "ur") and index == 0 and length > 3:
                    continue
                phonemes.extend(sound.split())
                index += len(pattern)
                matched = True
                break
        if matched:
            continue
        char = word[index]
        if char == "e" and index == length - 1 and length > 2 and any(
                c in _VOWEL_LETTERS for c in word[:-1]):
            index += 1                      # silent final e
            continue
        if char == "'":
            index += 1
            continue
        if char == "c":
            following = word[index + 1] if index + 1 < length else ""
            phonemes.append("S" if following in "eiy" else "K")
        elif char == "g":
            following = word[index + 1] if index + 1 < length else ""
            phonemes.append("JH" if following in "eiy" else "G")
        elif char == "s":
            following = word[index + 1] if index + 1 < length else ""
            if following in {" ", ""} and index > 0:
                phonemes.append("Z")
            else:
                phonemes.append("S")
        elif char == "y":
            following = word[index + 1] if index + 1 < length else ""
            if index == 0:
                phonemes.append("Y")
            elif following in ("", " ", "s"):
                phonemes.append("IY")
            else:
                phonemes.append("IH")
        elif char == "x":
            phonemes.extend(["K", "S"])
        elif char in _VOWEL_LETTERS:
            if word[index:index + 2] in ("a:",):
                pass
            # Open syllable heuristic: vowel at end of word or before a single
            # consonant + e becomes "long".
            if index == length - 1 and char in "aiu":
                phonemes.append({"a": "AH", "i": "IY", "u": "UW"}[char])
            elif char == "i":
                phonemes.append("AY" if index + 1 < length and word[index + 1] == "n"
                                and index + 2 == length else "IH")
            else:
                phonemes.append(LETTER_TO_PHONEME[char])
            if char == "u" and index == 0 and length > 2 and word[1] in "bcdfghjklmnpqrstvwxz":
                phonemes.insert(len(phonemes) - 1, "Y")
        else:
            phonemes.append(LETTER_TO_PHONEME.get(char, "AH"))
        index += 1
    return [p for p in phonemes if p in VOWELS or p in CONSONANTS]


def _syllabify(phonemes):
    """Group phonemes into syllables: onsets plus a vowel nucleus."""
    syllables = []
    current = []
    for phoneme in phonemes:
        is_vowel = phoneme in VOWELS
        if is_vowel:
            current.append(phoneme)
            syllables.append(current)
            current = []
        else:
            if current:
                current.append(phoneme)
            else:
                # attach a consonant to the previous syllable onset when the
                # previous syllable already has a nucleus
                if syllables and len(syllables[-1]) == 1:
                    syllables[-1].append(phoneme)
                else:
                    current.append(phoneme)
    if current:
        if syllables:
            syllables[-1].extend(current)
        else:
            syllables.append(current)
    return [s for s in syllables if any(p in VOWELS for p in s)] or [["AH"]]


def text_to_syllables(text):
    """Tokenize text into words, syllables and punctuation-derived pauses."""
    tokens = []
    for match in re.finditer(r"[A-Za-z']+|[0-9][0-9.,]*|[,.;:!?\-—…]|\s+", text):
        token = match.group(0)
        if token.isspace():
            continue
        if re.fullmatch(r"[A-Za-z']+", token):
            syllables = _syllabify(word_to_phonemes(token))
            tokens.append({"kind": "word", "text": token, "syllables": syllables})
        elif re.fullmatch(r"[0-9][0-9.,]*", token):
            spoken = spell_number(token)
            for word in spoken.split():
                tokens.append({"kind": "word", "text": word,
                               "syllables": _syllabify(word_to_phonemes(word))})
        else:
            tokens.append({"kind": "punct", "text": token})
    return tokens


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]


def spell_number(text):
    """Spell an integer (or decimal) as English words for the TTS pipeline."""
    cleaned = text.replace(",", "")
    if "." in cleaned:
        whole, _, fraction = cleaned.partition(".")
        return f"{_spell_int(whole)} point {' '.join(_ONES[int(d)] for d in fraction if d.isdigit())}".strip()
    if cleaned.isdigit():
        return _spell_int(cleaned)
    return text


def _spell_int(text):
    value = int(text)
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, rest = divmod(value, 10)
        return _TENS[tens] + (f" {_ONES[rest]}" if rest else "")
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {_spell_int(str(rest))}" if rest else "")
    for unit, name in ((1_000_000_000, "billion"), (1_000_000, "million"),
                       (1_000, "thousand")):
        if value >= unit:
            count, rest = divmod(value, unit)
            return f"{_spell_int(str(count))} {name}" + (
                f" {_spell_int(str(rest))}" if rest else "")
    return str(value)


def _resonator(signal, samplerate, frequency, bandwidth, gain=1.0):
    """Two-pole resonator used as a formant filter."""
    radius = math.exp(-math.pi * bandwidth / samplerate)
    theta = 2.0 * math.pi * frequency / samplerate
    a1 = -2.0 * radius * math.cos(theta)
    a2 = radius * radius
    gain_scale = (1.0 - radius) * math.sqrt(max(1.0 - 2.0 * radius * math.cos(2 * theta)
                                                + radius * radius, 1e-9)) * gain
    out = np.empty_like(signal)
    y1 = y2 = 0.0
    for index in range(signal.size):
        value = gain_scale * signal[index] - a1 * y1 - a2 * y2
        y2, y1 = y1, value
        out[index] = value
    return out


def _harmonic_source(length, samplerate, f0_curve, *, jitter=0.02, slope=1.05,
                     seed=1):
    """Band-limited harmonic source: periodic, pulse-like and jitter-aware.

    Summing harmonics of the instantaneous pitch gives a signal whose
    periodicity is exactly the requested pitch, unlike an impulse train fed
    through high-Q resonators (which rings at the formant instead).
    """
    rng = np.random.default_rng(seed)
    times = np.arange(length)
    if len(f0_curve) >= 2:
        f0_samples = np.interp(np.linspace(0, 1, length),
                               np.linspace(0, 1, len(f0_curve)), f0_curve)
    else:
        f0_samples = np.full(length, float(f0_curve[0]) if f0_curve else 120.0)
    # Small period-to-period jitter keeps the voice from sounding synthetic.
    jitter_curve = 1.0 + rng.normal(0.0, jitter, length).cumsum() * 0.02
    f0_samples = np.clip(f0_samples * jitter_curve, 40.0, samplerate / 2.2)
    phase = 2.0 * math.pi * np.cumsum(f0_samples) / samplerate
    out = np.zeros(length)
    max_harmonic = int((samplerate / 2.0) / max(float(np.max(f0_samples)), 1.0))
    for harmonic in range(1, max(max_harmonic, 1) + 1):
        if harmonic * float(np.mean(f0_samples)) > samplerate / 2.0 - 100:
            break
        out += np.cos(harmonic * phase) / (harmonic ** slope)
    peak = float(np.max(np.abs(out))) or 1.0
    return out / peak


def _formant_gain(frequencies, formants, bandwidths):
    """Magnitude response of a cascade of two-pole resonators."""
    gain = np.ones_like(frequencies, dtype=np.float64)
    for formant, bandwidth in zip(formants, bandwidths):
        f = frequencies / max(formant, 1.0)
        denominator = np.sqrt((1.0 - f ** 2) ** 2
                              + (f * bandwidth / max(formant, 1.0)) ** 2)
        gain *= 1.0 / np.maximum(denominator, 1e-6)
    return gain


def _shape_with_formants(signal, samplerate, formants, bandwidths, *, tilt=0.0):
    """Static spectral shaping: the signal stays exactly periodic."""
    if signal.size < 32:
        return signal
    spectrum = np.fft.rfft(signal)
    frequencies = np.fft.rfftfreq(signal.size, 1.0 / samplerate)
    gain = _formant_gain(frequencies, formants, bandwidths)
    if tilt:
        gain *= (np.maximum(frequencies, 20.0) / 500.0) ** tilt
    gain /= max(float(np.max(gain)), 1e-9)
    return np.fft.irfft(spectrum * gain, n=signal.size)


def _noise_burst(length, samplerate, center_hz, seed):
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, length)
    if length < 8:
        return noise
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(length, 1.0 / samplerate)
    mask = np.exp(-0.5 * ((freqs - center_hz) / (center_hz * 0.55 + 1.0)) ** 2)
    return np.fft.irfft(spectrum * mask, n=length)


def synthesize_phoneme(phoneme, duration, samplerate, f0_curve, *,
                       formant_scale=1.0, breathiness=0.15, brightness=0.0,
                       voiced_energy=1.0, seed=1):
    """Render one phoneme: harmonic source + formant shaping + noise component."""
    length = max(int(duration * samplerate), 16)
    envelope = np.ones(length)
    ramp = max(int(0.008 * samplerate), 1)
    envelope[:ramp] = np.linspace(0.0, 1.0, min(ramp, length))
    envelope[-ramp:] = np.linspace(1.0, 0.0, min(ramp, length))

    if phoneme in VOWELS:
        spec = VOWELS[phoneme]
        f0 = [float(f0_curve[0]), float(f0_curve[-1])] if len(f0_curve) else [120.0, 120.0]
        if DIPHTHONGS and phoneme in DIPHTHONGS:
            f0 = [f0[0], f0[1] * 0.96]
        source = _harmonic_source(length, samplerate, f0, jitter=0.025, seed=seed)
        formants = [f * formant_scale for f in spec["f"]]
        bandwidths = [78.0 + breathiness * 90.0, 110.0, 160.0]
        voiced = _shape_with_formants(source, samplerate, formants, bandwidths,
                                      tilt=-0.35 - brightness * 0.3)
        # Breath noise is filtered through the same formants so it sounds vocal.
        noise = _noise_burst(length, samplerate, 2400, seed + 11)
        noise = _shape_with_formants(noise, samplerate, formants, bandwidths)
        noise_peak = float(np.max(np.abs(noise))) or 1.0
        voiced_peak = float(np.max(np.abs(voiced))) or 1.0
        mix = voiced / voiced_peak + (noise / noise_peak) * (0.05 + breathiness * 0.45)
        return mix * envelope * voiced_energy * 0.9

    spec = CONSONANTS.get(phoneme)
    if spec is None:
        return np.zeros(length)
    kind = spec.get("type", "fricative")
    voiced_flag = spec.get("voiced", False)
    noise = _noise_burst(length, samplerate, spec.get("noise_hz", 3000), seed + 5)
    noise_peak = float(np.max(np.abs(noise))) or 1.0

    if kind == "stop":
        burst = max(int(length * 0.25), 2)
        out = np.zeros(length)
        out[:burst] = (noise[:burst] / noise_peak) * 0.55
        if voiced_flag:
            voice = _shape_with_formants(
                _harmonic_source(length, samplerate, [112.0, 112.0], seed=seed),
                samplerate, [f * formant_scale for f in spec["f"]],
                [140.0, 220.0, 320.0], tilt=-0.4)
            out += (voice / (float(np.max(np.abs(voice))) or 1.0)) * 0.22
        return out * 0.85

    if kind in ("fricative", "affricate"):
        out = (noise / noise_peak) * (0.32 if not voiced_flag else 0.20)
        if voiced_flag:
            voice = _shape_with_formants(
                _harmonic_source(length, samplerate, [114.0, 114.0], seed=seed + 3),
                samplerate, [f * formant_scale for f in spec["f"]],
                [130.0, 200.0, 300.0], tilt=-0.4)
            out += (voice / (float(np.max(np.abs(voice))) or 1.0)) * 0.26
        return out * envelope

    # nasals, liquids and glides: voiced with a low formant focus
    source = _harmonic_source(length, samplerate, [116.0, 112.0], seed=seed + 7)
    out = _shape_with_formants(source, samplerate,
                               [f * formant_scale for f in spec["f"]],
                               [120.0, 200.0, 300.0], tilt=-0.45)
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak) * 0.42 * envelope * voiced_energy


# --- prosody --------------------------------------------------------------

PUNCT_PAUSES = {".": 0.34, "!": 0.34, "?": 0.34, ",": 0.18, ";": 0.24, ":": 0.20,
                "-": 0.12, "—": 0.20, "…": 0.45}


def plan_segment(text, *, pace_wpm=155.0, emotion="neutral", intensity=0.5,
                 attitude="professional", styles=None, presets=None,
                 seed=17, target_duration=None):
    """Build a deterministic prosody plan for one text segment."""
    presets = presets or {}
    emotion_spec = presets.get("emotions", {}).get(emotion, presets.get("emotions", {}).get("neutral", {}))
    attitude_spec = presets.get("attitudes", {}).get(attitude, presets.get("attitudes", {}).get("professional", {}))
    styles = styles or []
    style_deltas = {"pace": 0.0, "pitch": 0.0, "energy": 0.0}
    for style in styles:
        spec = presets.get("style_attributes", {}).get(style)
        if not spec:
            continue
        style_deltas["pace"] += spec.get("pace_wpm_delta", 0) / max(pace_wpm, 1)
        style_deltas["pitch"] += spec.get("pitch_st_delta", 0)
        style_deltas["energy"] += spec.get("energy_delta", 0)

    tokens = text_to_syllables(text)
    words = []
    pauses = []
    syllable_budget = 0
    for position, token in enumerate(tokens):
        if token["kind"] == "word":
            syllables = token["syllables"]
            stress_index = 0 if len(syllables) == 1 else (
                0 if position % 3 == 0 else min(1, len(syllables) - 1))
            words.append({"word": token["text"], "syllables": syllables,
                          "stress_index": stress_index,
                          "emphasis": position % 7 == 0 and len(words) > 2})
            syllable_budget += len(syllables)
        else:
            pause = PUNCT_PAUSES.get(token["text"])
            if pause:
                pauses.append({"after_token": position, "seconds": pause})
    if not words:
        raise RT.ToolError("Segment text contains no speakable words.",
                           code=RT.EXIT_INVALID_INPUT,
                           details={"text_preview": text[:80]})

    rate_mult = float(emotion_spec.get("rate_mult", 1.0)) * (
        1.0 + float(attitude_spec.get("rate_mult", 1.0) - 1.0) * 0.6)
    effective_wpm = max(60.0, pace_wpm * (1.0 + style_deltas["pace"]))
    syllable_rate = effective_wpm / 60.0 * 1.55          # syllables per second
    base_f0 = 140.0
    pitch_st = float(emotion_spec.get("pitch_st", 0.0)) * (0.5 + intensity) \
        + float(attitude_spec.get("pitch_st", 0.0)) * 0.6 + style_deltas["pitch"]
    range_mult = float(emotion_spec.get("range_mult", 1.0)) * float(
        attitude_spec.get("range_mult", 1.0)) ** 0.4
    energy = RT.clamp(float(emotion_spec.get("energy", 0.5)) * (0.7 + 0.6 * intensity)
                      + float(attitude_spec.get("energy", 0.5) - 0.5) * 0.5
                      + style_deltas["energy"], 0.05, 1.0)

    events = []
    cursor = 0.0
    total_syllables = 0
    pause_lookup = {}
    for pause in pauses:
        pause_lookup.setdefault(pause["after_token"], []).append(pause["seconds"])
    token_index = 0
    word_index = 0
    for position, token in enumerate(tokens):
        if token["kind"] == "word":
            word = words[word_index]
            for syllable_index, syllable in enumerate(word["syllables"]):
                nucleus = syllable[0] if syllable[0] in VOWELS else next(
                    (p for p in syllable if p in VOWELS), "AH")
                duration = VOWELS.get(nucleus, VOWELS["AH"])["dur"]
                stress = syllable_index == word["stress_index"]
                duration *= (1.22 if stress else 0.88)
                duration *= (1.0 / syllable_rate) / 0.148
                duration *= 1.0 + (0.10 if word["emphasis"] else 0.0)
                progress = (total_syllables / max(
                    1, sum(len(w["syllables"]) for w in words)))
                declination = -2.4 * progress
                accent = 1.6 if stress else 0.0
                jitter = ((seed + position * 7 + syllable_index * 13) % 9 - 4) * 0.12
                events.append({
                    "kind": "syllable",
                    "word": word["word"],
                    "phonemes": syllable,
                    "start": round(cursor, 4),
                    "duration": round(duration, 4),
                    "stress": stress,
                    "pitch_st": round(pitch_st + declination + accent * range_mult
                                      + jitter, 3),
                    "energy": round(RT.clamp(energy * (1.0 + (0.16 if stress else 0.0)),
                                             0.02, 1.0), 3),
                })
                cursor += duration
                total_syllables += 1
            word_index += 1
        for pause in pause_lookup.get(position, []):
            scaled = pause * (1.15 - 0.3 * intensity) * (
                0.85 if effective_wpm > 180 else 1.0)
            events.append({"kind": "pause", "start": round(cursor, 4),
                           "duration": round(scaled, 4)})
            cursor += scaled

    planned = cursor
    if target_duration:
        factor = float(target_duration) / max(planned, 1e-6)
        factor = RT.clamp(factor, 0.6, 1.6)
        for event in events:
            event["duration"] = round(event["duration"] * factor, 4)
        cursor = 0.0
        for event in events:
            event["start"] = round(cursor, 4)
            cursor += event["duration"]
        planned = cursor
        scale_note = f"scaled by {factor:.3f} to hit the requested duration"
    else:
        scale_note = None

    return {
        "text": text,
        "words": len(words),
        "syllables": total_syllables,
        "effective_wpm": round(effective_wpm, 1),
        "base_f0_hz": round(base_f0 * 2 ** (pitch_st / 12.0), 2),
        "range_mult": round(range_mult, 3),
        "energy": round(energy, 3),
        "emotion": emotion,
        "attitude": attitude,
        "styles": styles,
        "intensity": intensity,
        "planned_duration_seconds": round(planned, 3),
        "events": events,
        "duration_note": scale_note,
    }


def render_plan(plan, samplerate, *, formant_scale=1.0, breathiness=0.2,
                brightness=0.0, base_f0=120.0, seed=17):
    """Render a prosody plan to mono float audio (24 kHz by default)."""
    total = int(round((plan["planned_duration_seconds"] + 0.25) * samplerate))
    out = np.zeros(max(total, 16))
    for index, event in enumerate(plan["events"]):
        if event["kind"] != "syllable":
            continue
        start = int(round(event["start"] * samplerate))
        f0 = base_f0 * 2 ** (event["pitch_st"] / 12.0)
        duration = max(event["duration"], 0.02)
        for phoneme_index, phoneme in enumerate(event["phonemes"]):
            is_vowel = phoneme in VOWELS
            share = 0.62 if is_vowel else 0.38 / max(
                len([p for p in event["phonemes"] if p not in VOWELS]), 1)
            length = duration * share
            segment = synthesize_phoneme(
                phoneme, length, samplerate, [f0, f0 * 0.985],
                formant_scale=formant_scale,
                breathiness=breathiness,
                brightness=brightness,
                voiced_energy=event["energy"],
                seed=seed + index + phoneme_index,
            )
            position = start
            end = min(position + segment.size, out.size)
            if end > position:
                out[position:end] += segment[:end - position]
            start = end
    peak = float(np.max(np.abs(out)))
    if peak > 0:
        out = out / peak * 0.72
    return out


def add_breaths(audio, plan, samplerate, *, level=0.05, seed=23):
    """Insert short breath noises before long pauses."""
    out = np.array(audio, dtype=np.float64, copy=True)
    rng = np.random.default_rng(seed)
    for event in plan["events"]:
        if event["kind"] != "pause" or event["duration"] < 0.2:
            continue
        start = int(max(event["start"] * samplerate - 0.12 * samplerate, 0))
        length = int(0.12 * samplerate)
        if start + length > out.size:
            continue
        envelope = np.hanning(length) * level
        out[start:start + length] += rng.normal(0.0, 1.0, length) * envelope
    return out


def apply_effect_rack(audio, samplerate, effect_spec, *, intelligibility_floor=0.35):
    """Apply a named effect preset from asset-voice-presets.json."""
    if not effect_spec:
        return np.asarray(audio, dtype=np.float64)
    out = np.asarray(audio, dtype=np.float64)
    bands = effect_spec.get("bandpass_hz")
    if bands:
        low, high = bands
        out = bandpass(out, samplerate, low, high)
    drive = float(effect_spec.get("drive", 0.0)) * (1.0 - intelligibility_floor * 0.0)
    if drive > 0:
        out = saturate(out, samplerate, drive=RT.clamp(drive, 0.0, 0.9))
    echo_spec = effect_spec.get("echo")
    if echo_spec:
        out = echo(out, samplerate, delay_ms=echo_spec.get("delay_ms", 40.0),
                   decay=RT.clamp(echo_spec.get("decay", 0.25), 0.0, 0.7), repeats=2)
    noise_db = effect_spec.get("noise_db")
    if noise_db is not None:
        out = add_noise_floor(out, samplerate, level_dbfs=float(noise_db))
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.99:
        out = out / peak * 0.95
    return out[:, None] if out.ndim == 1 else out
