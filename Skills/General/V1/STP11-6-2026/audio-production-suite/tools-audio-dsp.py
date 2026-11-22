#!/usr/bin/env python3
"""tools-audio-dsp.py - bounded, reversible-by-design audio processing operations.

Supported operations (also usable as an ordered recipe list):
  gain        gain in dB
  polarity    invert polarity
  dc-block    high-pass at 10 Hz to remove offset
  highpass    biquad high-pass with configurable corner and Q
  lowpass     biquad low-pass with configurable corner and Q
  channels    mono / stereo / swap / select:N / strip:N
  resample    sample-rate conversion (low/medium/high quality)
  trim        start/end trim with guaranteed click-safe edges
  trim-silence  remove leading and trailing silence with padding
  fade        equal-power fade in and out
  normalize   to an integrated loudness target or a sample-peak target
  limit       lookahead peak limiter to a true-peak-style ceiling

Every op returns before/after measurements so a run can be audited. Nothing is
written until the whole chain succeeds. Standard library only.

Run directly for its own help: python3 tools-audio-dsp.py --help
"""

from __future__ import annotations

import array
import math
import os
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
IO_FILENAME = "tools-audio-io.py"
QA_FILENAME = "tools-audio-qa.py"

OP_NAMES = ("gain", "polarity", "dc-block", "highpass", "lowpass", "channels",
            "resample", "trim", "trim-silence", "fade", "normalize", "limit")


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
    qa = load("aps_audio_qa", QA_FILENAME)
    return common, aio, qa


common, aio, qa = _load()


def _peak(buffer):
    peak = 0.0
    for ch in buffer.channels:
        for value in ch:
            magnitude = value if value >= 0 else -value
            if magnitude > peak:
                peak = magnitude
    return peak


def _measure(buffer):
    return {
        "duration_seconds": common.r2(buffer.duration, 3),
        "sample_rate": buffer.sample_rate,
        "channels": buffer.channel_count,
        "peak_dbfs": common.r2(common.lin_to_db(_peak(buffer))),
        "rms_dbfs": common.r2(common.lin_to_db(buffer.rms())),
    }


# --- primitives -------------------------------------------------------------
def op_gain(buffer, params):
    db = float(params.get("db", 0.0))
    if not -120.0 <= db <= 60.0:
        raise common.ToolError("invalid_argument", "gain db must be between -120 and 60")
    factor = common.db_to_lin(db)
    for ch in buffer.channels:
        for i in range(len(ch)):
            ch[i] *= factor
    return buffer, {"db": db}


def op_polarity(buffer, params):
    if params.get("invert", True):
        for ch in buffer.channels:
            for i in range(len(ch)):
                ch[i] = -ch[i]
    return buffer, {"inverted": bool(params.get("invert", True))}


def op_filter(buffer, params, kind):
    hz = float(params.get("hz", 80.0))
    q = float(params.get("q", 0.7071))
    order = int(params.get("order", 2))
    if not 10.0 <= hz <= 20000.0:
        raise common.ToolError("invalid_argument", "%s hz must be between 10 and 20000" % kind)
    if not 0.1 <= q <= 20.0:
        raise common.ToolError("invalid_argument", "%s q must be between 0.1 and 20" % kind)
    if order not in (2, 4):
        raise common.ToolError("invalid_argument", "%s order must be 2 or 4" % kind)
    coefficient = qa._biquad(buffer.sample_rate, kind, hz, q)
    for index, ch in enumerate(buffer.channels):
        out = qa.apply_biquad(ch, coefficient)
        if order == 4:
            out = qa.apply_biquad(out, coefficient)
        buffer.channels[index] = out
    return buffer, {"hz": hz, "q": q, "order": order}


def op_channels(buffer, params):
    mode = params.get("mode", "mono")
    buffer_out, info = aio.map_channels(buffer, mode)
    return buffer_out, info


def op_resample(buffer, params):
    rate = int(params.get("rate", 48000))
    quality = params.get("quality", "medium")
    buffer_out, info = aio.resample(buffer, rate, quality)
    return buffer_out, info


def _apply_edges(buffer, fade_frames_in, fade_frames_out):
    frames = buffer.frames
    fade_in = min(frames, max(0, int(fade_frames_in)))
    fade_out = min(frames, max(0, int(fade_frames_out)))
    for ch in buffer.channels:
        for i in range(fade_in):
            ch[i] *= math.sin((i + 0.5) / fade_in * math.pi / 2.0)
        for i in range(fade_out):
            ch[frames - 1 - i] *= math.sin((i + 0.5) / fade_out * math.pi / 2.0)
    return buffer


def op_trim(buffer, params):
    start = float(params.get("start_sec", 0.0) or 0.0)
    end = params.get("end_sec")
    if start < 0:
        raise common.ToolError("invalid_argument", "trim start_sec must be zero or positive")
    if end is not None and float(end) <= start:
        raise common.ToolError("invalid_argument", "trim end_sec must be greater than start_sec",
                               {"start_sec": start, "end_sec": end})
    start_frame = int(round(start * buffer.sample_rate))
    end_frame = int(round(float(end) * buffer.sample_rate)) if end is not None else buffer.frames
    end_frame = min(end_frame, buffer.frames)
    if start_frame >= buffer.frames:
        raise common.ToolError("invalid_argument", "trim start is beyond the end of the audio",
                               {"frames": buffer.frames, "start_frame": start_frame})
    trimmed = buffer.slice(start_frame, end_frame)
    guard = int(0.002 * buffer.sample_rate)
    _apply_edges(trimmed, guard, guard)
    return trimmed, {"removed_head_seconds": common.r2(start_frame / float(buffer.sample_rate), 3),
                     "removed_tail_seconds": common.r2((buffer.frames - end_frame) / float(buffer.sample_rate), 3),
                     "click_guard_ms": 2.0}


def op_trim_silence(buffer, params):
    threshold_db = float(params.get("threshold_db", -50.0))
    pad = float(params.get("pad_sec", 0.1))
    if pad < 0:
        raise common.ToolError("invalid_argument", "pad_sec must be zero or positive")
    frame = max(1, int(0.01 * buffer.sample_rate))
    threshold = common.db_to_lin(threshold_db)
    merged = array.array("d", bytes(8 * buffer.frames))
    for ch in buffer.channels:
        for i in range(buffer.frames):
            merged[i] += ch[i] / buffer.channel_count
    loud = []
    position = 0
    while position + frame <= buffer.frames:
        total = 0.0
        for i in range(position, position + frame):
            total += merged[i] * merged[i]
        loud.append(math.sqrt(total / frame) > threshold)
        position += frame
    if not any(loud):
        raise common.ToolError("nothing_to_keep",
                               "The whole file is below the silence threshold; nothing would remain",
                               {"threshold_db": threshold_db},
                               hint="Lower --threshold-db, or disable trim-silence for this file.")
    first = loud.index(True) * frame
    last = (len(loud) - loud[::-1].index(True)) * frame
    pad_frames = int(pad * buffer.sample_rate)
    start = max(0, first - pad_frames)
    end = min(buffer.frames, last + pad_frames)
    trimmed = buffer.slice(start, end)
    guard = int(0.002 * buffer.sample_rate)
    _apply_edges(trimmed, guard, guard)
    return trimmed, {"threshold_db": threshold_db, "pad_sec": pad,
                     "removed_head_seconds": common.r2(start / float(buffer.sample_rate), 3),
                     "removed_tail_seconds": common.r2((buffer.frames - end) / float(buffer.sample_rate), 3)}


def op_fade(buffer, params):
    fade_in = float(params.get("in_sec", 0.0) or 0.0)
    fade_out = float(params.get("out_sec", 0.0) or 0.0)
    if fade_in < 0 or fade_out < 0:
        raise common.ToolError("invalid_argument", "fade durations must be zero or positive")
    if fade_in + fade_out > buffer.duration + 1e-9:
        raise common.ToolError("invalid_argument", "fades are longer than the audio",
                               {"duration": common.r2(buffer.duration, 3)})
    _apply_edges(buffer, fade_in * buffer.sample_rate, fade_out * buffer.sample_rate)
    return buffer, {"in_sec": fade_in, "out_sec": fade_out, "curve": "equal-power sine"}


def op_normalize(buffer, params):
    target_lufs = params.get("target_lufs")
    target_dbtp = params.get("target_dbtp")
    if target_lufs is None and target_dbtp is None:
        raise common.ToolError("invalid_argument",
                               "normalize needs target_lufs or target_dbtp",
                               hint='Example: {"op":"normalize","target_lufs":-16}')
    if target_lufs is not None:
        if not -50.0 <= float(target_lufs) <= 0.0:
            raise common.ToolError("invalid_argument", "target_lufs must be between -50 and 0")
        weighted = [qa.k_weight(ch, buffer.sample_rate) for ch in buffer.channels]
        integrated, _ = qa.integrated_loudness(weighted, buffer.sample_rate)
        if integrated is None or integrated == float("-inf"):
            raise common.ToolError("nothing_to_measure",
                                   "Cannot normalize: no measurable program loudness",
                                   hint="The audio may be silent or shorter than 0.4 s.")
        delta = float(target_lufs) - integrated
        if delta > 60.0:
            raise common.ToolError("unsafe_gain",
                                   "Normalization would add %.1f dB, which is outside the safe range" % delta,
                                   {"measured_lufs": common.r2(integrated), "requested_lufs": float(target_lufs)},
                                   hint="Check the source; provide a louder reference or a per-op gain first.")
        factor = common.db_to_lin(delta)
        for ch in buffer.channels:
            for i in range(len(ch)):
                ch[i] *= factor
        return buffer, {"target_lufs": float(target_lufs), "measured_lufs": common.r2(integrated),
                        "gain_applied_db": common.r2(delta)}
    peak = _peak(buffer)
    if peak <= 0:
        raise common.ToolError("nothing_to_measure", "Cannot normalize: the audio is silent")
    delta = float(target_dbtp) - common.lin_to_db(peak)
    factor = common.db_to_lin(delta)
    for ch in buffer.channels:
        for i in range(len(ch)):
            ch[i] *= factor
    return buffer, {"target_dbtp": float(target_dbtp), "measured_peak_dbfs": common.r2(common.lin_to_db(peak)),
                    "gain_applied_db": common.r2(delta)}


def op_limit(buffer, params):
    ceiling_db = float(params.get("ceiling_dbtp", -1.0))
    if not -20.0 <= ceiling_db <= 0.0:
        raise common.ToolError("invalid_argument", "ceiling_dbtp must be between -20 and 0")
    lookahead_ms = float(params.get("lookahead_ms", 5.0))
    release_ms = float(params.get("release_ms", 80.0))
    if not 0.5 <= lookahead_ms <= 50.0:
        raise common.ToolError("invalid_argument", "lookahead_ms must be between 0.5 and 50")
    if not 5.0 <= release_ms <= 2000.0:
        raise common.ToolError("invalid_argument", "release_ms must be between 5 and 2000")
    ceiling = common.db_to_lin(ceiling_db)
    frames = buffer.frames
    look = max(1, int(buffer.sample_rate * lookahead_ms / 1000.0))
    release = max(1, int(buffer.sample_rate * release_ms / 1000.0))
    peak_curve = [0.0] * frames
    for ch in buffer.channels:
        for i in range(frames):
            value = ch[i] if ch[i] >= 0 else -ch[i]
            if value > peak_curve[i]:
                peak_curve[i] = value
    # required gain per sample, then a sliding minimum over the lookahead window
    required = [1.0] * frames
    for i in range(frames):
        peak = peak_curve[i]
        required[i] = 1.0 if peak <= ceiling else ceiling / peak
    from collections import deque
    # Gain at sample i is the minimum required gain across the lookahead window
    # [i, i + look]. Because that window reaches into the future, no signal delay
    # is needed and unlimited passages stay sample-identical.
    window = deque()
    gain = [1.0] * frames
    for i in range(frames - 1, -1, -1):
        while window and required[window[-1]] >= required[i]:
            window.pop()
        window.append(i)
        while window[0] > i + look:
            window.popleft()
        gain[i] = required[window[0]]
    smoothed = [1.0] * frames
    previous = 1.0
    coefficient = 1.0 - math.exp(-1.0 / release)
    for i in range(frames):
        target = gain[i]
        previous = target if target < previous else previous + (target - previous) * coefficient
        smoothed[i] = previous
    reduced = 0.0
    peak_before = _peak(buffer)
    for ch in buffer.channels:
        for i in range(frames):
            ch[i] *= smoothed[i]
    peak_after = _peak(buffer)
    if peak_after > ceiling * 1.0001:
        scale = ceiling / peak_after
        for ch in buffer.channels:
            for i in range(frames):
                ch[i] *= scale
        reduced = 1.0
    return buffer, {"ceiling_dbtp": ceiling_db, "lookahead_ms": lookahead_ms, "release_ms": release_ms,
                    "latency_samples": 0,
                    "lookahead_window_samples": look,
                    "peak_before_dbfs": common.r2(common.lin_to_db(peak_before)),
                    "peak_after_dbfs": common.r2(common.lin_to_db(_peak(buffer))),
                    "safety_scale_applied": reduced}


HANDLERS = {
    "gain": lambda buf, params: op_gain(buf, params),
    "polarity": lambda buf, params: op_polarity(buf, params),
    "dc-block": lambda buf, params: op_filter(buf, {"hz": 10.0, "q": 0.7071,
                                                    "order": params.get("order", 2)}, "highpass"),
    "highpass": lambda buf, params: op_filter(buf, params, "highpass"),
    "lowpass": lambda buf, params: op_filter(buf, params, "lowpass"),
    "channels": op_channels,
    "resample": op_resample,
    "trim": op_trim,
    "trim-silence": op_trim_silence,
    "fade": op_fade,
    "normalize": op_normalize,
    "limit": op_limit,
}


def validate_ops(ops):
    if not isinstance(ops, list) or not ops:
        raise common.ToolError("invalid_recipe", "A recipe must be a non-empty list of operations")
    if len(ops) > 32:
        raise common.ToolError("invalid_recipe", "A recipe may contain at most 32 operations")
    cleaned = []
    for index, entry in enumerate(ops):
        if not isinstance(entry, dict):
            raise common.ToolError("invalid_recipe", "Operation %d is not an object" % (index + 1))
        name = entry.get("op") or entry.get("operation")
        if name not in HANDLERS:
            raise common.ToolError("unknown_operation",
                                   "Operation %d uses unknown op %r" % (index + 1, name),
                                   {"available": list(OP_NAMES)},
                                   hint="Use 'capabilities' or read knowledge-readme.md for the op list.")
        params = dict(entry)
        params.pop("op", None)
        params.pop("operation", None)
        cleaned.append({"op": name, "params": params})
    return cleaned


def apply_ops(buffer, ops, timer=None):
    chain = validate_ops(ops)
    trace = []
    current = buffer
    for index, entry in enumerate(chain, 1):
        before = _measure(current)
        try:
            current, info = HANDLERS[entry["op"]](current, entry["params"])
        except common.ToolError:
            raise
        except Exception as exc:  # keep the chain atomic and the error structured
            raise common.ToolError("operation_failed",
                                   "Operation %d (%s) failed: %s" % (index, entry["op"], exc),
                                   {"op": entry["op"], "params": entry["params"]})
        after = _measure(current)
        trace.append({"step": index, "op": entry["op"], "params": entry["params"],
                      "info": info, "before": before, "after": after})
        if timer:
            timer.check("op %s" % entry["op"])
    return current, trace


def resolve_recipe(recipe_id, recipes=None):
    recipes = recipes or common.load_asset("asset-processing-recipes.json")
    entry = recipes["recipes"].get(recipe_id)
    if entry is None:
        raise common.ToolError("unknown_recipe", "Unknown recipe: %s" % recipe_id,
                               {"available": sorted(recipes["recipes"].keys())})
    return entry


def process_to_file(input_path, output_path, ops, bit_depth=24, sample_format="pcm",
                    dither=False, noise_shaping=0, max_seconds=None, timer=None,
                    source_meta=None):
    """Decode, apply the chain, then write once. Nothing is written if any op fails."""
    buffer, meta = aio.read_wav(input_path, max_seconds=max_seconds)
    result, trace = apply_ops(buffer, ops, timer=timer)
    if result.frames == 0:
        raise common.ToolError("invalid_result", "The processing chain produced zero frames")
    write_info = aio.write_wav(output_path, result, bit_depth=bit_depth,
                               sample_format=sample_format, dither=dither,
                               noise_shaping=noise_shaping)
    return {
        "input": meta["path"],
        "output": write_info["path"],
        "input_meta": {k: meta[k] for k in ("sample_rate", "channels", "bit_depth", "duration_seconds")},
        "output_meta": {k: write_info[k] for k in ("sample_rate", "channels", "bit_depth", "bytes")},
        "chain": trace,
        "warnings": result.warnings,
    }


def main(argv=None):
    print("""tools-audio-dsp.py - audio processing operations (library + help).

Operations: %s

Each operation is a dict such as:
  {"op": "highpass", "hz": 80, "q": 0.707, "order": 2}
  {"op": "normalize", "target_lufs": -16}
  {"op": "limit", "ceiling_dbtp": -1.0, "lookahead_ms": 5}

Chain them through the recipe asset, or call the suite entry point:
  python3 tools-audio-suite.py process --input in.wav --output out.wav --op highpass:80 --op normalize:-16
  python3 tools-audio-suite.py recipe --input in.wav --output out.wav --recipe podcast-speech-master
""" % ", ".join(OP_NAMES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
