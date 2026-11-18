#!/usr/bin/env python3
"""Voice Capability Toolkit - editing, mixing, mastering and delivery tool.

Commands
--------
    capabilities      Machine-readable command index
    presets           List format/loudness delivery presets
    inspect           Measure a file and compare it with a delivery preset
    encode            Transcode/resample/re-channel and normalise to a preset
    master            Loudness mastering with limiter, EQ, fades and trims
    repair            Ordered DSP repair chain (noise, clicks, ess, EQ, gate, comp)
    mix               Mix voice with a music/ambience bed using ducking
    effects           Apply an environment/effect rack to existing audio
    match-ambience    Match noise floor, echo and tone to a reference recording
    concat            Join clips with gaps and crossfades (pacing / dead-air edits)
    cue-sheet         Write JSON/CSV/SRT cue sheets from a brief or manifest
    package           Build a delivery manifest (hashes, metrics, disclosure)
    validate-package  Validate a delivery folder against a manifest and checklist
    checklist         Run a checklist from asset-checklists.json against a file
    capabilities ...  (see above)

Safety: source files are never modified in place; `release` actions need
`--approve`.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import numpy as np
import os
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load(filename, modname):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load("tools-runtime-common.py", "_voice_rt")
ENGINE = _load("tools-audio-engine.py", "_voice_engine")
PROG = "tools-audio-delivery.py"

CAPABILITIES = [
    {"command": "capabilities", "summary": "Print this command index.", "inputs": [], "outputs": ["JSON"]},
    {"command": "presets", "summary": "List loudness/format delivery presets (podcast, broadcast, telephony, game, archive, web).", "inputs": [], "outputs": ["JSON"]},
    {"command": "inspect", "summary": "Measure format, loudness, peak, noise, clipping, silence and pitch; compare against a preset.", "inputs": ["--input audio"], "outputs": ["measurement report"]},
    {"command": "encode", "summary": "Transcode to a preset's format/subtype/sample rate/channels with band-limiting and loudness normalisation.", "inputs": ["--input audio", "--preset"], "outputs": ["encoded audio"]},
    {"command": "master", "summary": "Master spoken audio: loudness target, look-ahead limiting, EQ, fades, trims, DC removal.", "inputs": ["--input audio"], "outputs": ["master audio", "report"]},
    {"command": "repair", "summary": "Ordered repair chain: declick, denoise, de-ess, high-pass, gate, compress, normalise.", "inputs": ["--input audio"], "outputs": ["repaired audio", "before/after report"]},
    {"command": "mix", "summary": "Mix a voice track with a music/ambience bed, with ducking and level matching.", "inputs": ["--voice", "--bed"], "outputs": ["mix audio", "report"]},
    {"command": "effects", "summary": "Apply an environment/effect rack (radio, helmet, cave, underwater, glitch, ...).", "inputs": ["--input audio", "--effect"], "outputs": ["processed audio"]},
    {"command": "match-ambience", "summary": "Match noise floor, echo and spectral tilt of a clean voice to a reference recording.", "inputs": ["--input voice", "--reference"], "outputs": ["matched audio", "report"]},
    {"command": "concat", "summary": "Join clips with configurable gaps/crossfades and optional dead-air trimming.", "inputs": ["--inputs a.wav b.wav"], "outputs": ["joined audio"]},
    {"command": "cue-sheet", "summary": "Write cue sheets (JSON, CSV, SRT) from a brief's segment timings.", "inputs": ["--input brief.json"], "outputs": ["cue sheet files"]},
    {"command": "package", "summary": "Build a delivery manifest with hashes, metrics, rights and disclosure for a folder of deliverables.", "inputs": ["--input dir"], "outputs": ["manifest JSON"]},
    {"command": "validate-package", "summary": "Validate a delivery folder against manifest hashes, naming rules, loudness, clipping and checklist.", "inputs": ["--input dir"], "outputs": ["validation report"]},
    {"command": "checklist", "summary": "Run one checklist (advertising, audiobook, podcast, game-voiceover, telephony, accessibility) on a file.", "inputs": ["--name", "--input audio"], "outputs": ["checklist report"]},
]


def _presets(assets_dir=None):
    return RT.load_asset_json("asset-voice-presets.json", assets_dir)[0]


def _schemas(assets_dir=None):
    return RT.load_asset_json("asset-schemas.json", assets_dir)[0]


def _policy(assets_dir=None):
    return RT.load_asset_json("asset-policy.json", assets_dir)[0]


def _checklists(assets_dir=None):
    return RT.load_asset_json("asset-checklists.json", assets_dir)[0]


def measure(path, *, thresholds=None):
    """Full measurement set for one audio file."""
    data, samplerate, info = ENGINE.read_audio(path)
    mono = ENGINE.to_mono(data)
    pitch = ENGINE.estimate_pitch(mono, samplerate)
    result = {
        "path": path,
        "format": info.format,
        "subtype": info.subtype,
        "samplerate": samplerate,
        "channels": int(data.shape[1]),
        "frames": int(data.shape[0]),
        "duration_seconds": round(data.shape[0] / samplerate, 4),
        "bytes": os.path.getsize(path),
        "peak_dbfs": round(ENGINE.peak_dbfs(mono), 2),
        "true_peak_dbtp": round(ENGINE.true_peak_dbtp(mono), 2),
        "rms_dbfs": round(ENGINE.rms_dbfs(mono), 2),
        "integrated_lufs": round(ENGINE.integrated_loudness(data, samplerate), 2),
        "noise_floor_dbfs": round(ENGINE.noise_floor_dbfs(mono, samplerate), 2),
        "dc_offset": ENGINE.dc_offset(mono),
        "clipping": ENGINE.clipping_report(mono),
        "silence_regions": ENGINE.silence_regions(mono, samplerate,
                                                 threshold_dbfs=thresholds or -45.0)[:50],
        "speech_segments": ENGINE.voice_activity(mono, samplerate,
                                                 threshold_dbfs=thresholds or -45.0)[:50],
        "pitch_median_hz": pitch.get("median_hz"),
        "pitch_range_semitones": pitch.get("range_semitones"),
        "spectral_centroid_hz": ENGINE.spectral_centroid(mono, samplerate),
        "zero_crossing_rate": ENGINE.zero_crossing_rate(mono),
        "sha256": RT.sha256_file(path),
    }
    return result, data, samplerate


def compare_to_preset(measurements, preset):
    checks = []
    if preset.get("lufs") is not None:
        delta = round(measurements["integrated_lufs"] - preset["lufs"], 2)
        checks.append({"check": "integrated_loudness", "target": preset["lufs"],
                       "actual": measurements["integrated_lufs"], "delta": delta,
                       "pass": abs(delta) <= 1.0, "tolerance": 1.0})
    if preset.get("true_peak_dbtp") is not None:
        over = round(measurements["true_peak_dbtp"] - preset["true_peak_dbtp"], 2)
        checks.append({"check": "true_peak", "target": preset["true_peak_dbtp"],
                       "actual": measurements["true_peak_dbtp"], "delta": over,
                       "pass": over <= 0.0, "tolerance": 0.0})
    if preset.get("sample_rate"):
        checks.append({"check": "sample_rate", "target": preset["sample_rate"],
                       "actual": measurements["samplerate"],
                       "pass": measurements["samplerate"] == preset["sample_rate"]})
    if preset.get("channels"):
        checks.append({"check": "channels", "target": preset["channels"],
                       "actual": measurements["channels"],
                       "pass": measurements["channels"] == preset["channels"]})
    if preset.get("format"):
        checks.append({"check": "format", "target": preset["format"].upper(),
                       "actual": measurements["format"].upper(),
                       "pass": measurements["format"].upper() == preset["format"].upper()})
    if preset.get("subtype"):
        checks.append({"check": "subtype", "target": preset["subtype"],
                       "actual": measurements["subtype"],
                       "pass": measurements["subtype"] == preset["subtype"]})
    checks.append({"check": "no_clipping", "target": 0,
                   "actual": measurements["clipping"]["clipped_samples"],
                   "pass": measurements["clipping"]["clipped_samples"] == 0})
    return {"preset_checks": checks, "passed": all(c["pass"] for c in checks)}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_presets(args):
    presets = _presets(args.assets_dir)
    items = []
    for name, spec in presets["delivery_targets"].items():
        items.append({"name": name, **spec})
    if args.name:
        match = next((i for i in items if i["name"] == args.name), None)
        if not match:
            raise RT.ToolError(
                f"Unknown preset '{args.name}'.",
                code=RT.EXIT_NOT_FOUND,
                details={"available": [i["name"] for i in items]})
        RT.emit({"status": "ok", "command": "presets", "preset": match,
                 "loudness_rules": presets["loudness"]},
                output=args.output, quiet=args.quiet)
        return RT.EXIT_OK
    RT.emit({"status": "ok", "command": "presets", "presets": items,
             "loudness_rules": presets["loudness"]}, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_inspect(args):
    presets = _presets(args.assets_dir)
    measurements, _, _ = measure(args.input, thresholds=args.silence_threshold)
    result = {"status": "ok", "command": "inspect", "measurements": measurements}
    if args.preset:
        spec = presets["delivery_targets"].get(args.preset)
        if not spec:
            raise RT.ToolError(f"Unknown preset '{args.preset}'.", code=RT.EXIT_NOT_FOUND,
                               details={"available": sorted(presets["delivery_targets"])})
        result["preset"] = args.preset
        result["comparison"] = compare_to_preset(measurements, spec)
        if not result["comparison"]["passed"]:
            result["status"] = "mismatch"
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def _apply_preset(data, samplerate, preset, *, band_limit=True):
    out = data
    channels = preset.get("channels")
    if channels:
        out = ENGINE.to_channels(out, channels)
    low, high = (preset.get("band_hz") or [None, None])
    if band_limit and low:
        out = ENGINE.apply_eq(out, samplerate, highpass_hz=low, lowpass_hz=high)
    target_rate = preset.get("sample_rate")
    if target_rate and target_rate != samplerate:
        out = ENGINE.resample(out, samplerate, target_rate)
        samplerate = target_rate
    return out, samplerate


def cmd_encode(args):
    presets = _presets(args.assets_dir)
    spec = presets["delivery_targets"].get(args.preset)
    if not spec:
        raise RT.ToolError(
            f"Unknown preset '{args.preset}'.",
            code=RT.EXIT_NOT_FOUND,
            details={"available": sorted(presets["delivery_targets"])})
    data, samplerate, info = ENGINE.read_audio(args.input)
    if os.path.abspath(args.input) == os.path.abspath(args.output_path or "") :
        raise RT.ToolError("Refusing to encode a file over itself.",
                           code=RT.EXIT_INVALID_INPUT,
                           hint="Choose a different output path.")
    report = {"preset": args.preset, "source": {"samplerate": samplerate,
                                                "channels": int(data.shape[1]),
                                                "format": info.format}}
    out, samplerate = _apply_preset(data, samplerate, spec,
                                   band_limit=not args.no_band_limit)
    if args.target_lufs is not None:
        target = args.target_lufs
    else:
        target = spec.get("lufs")
    loudness_report = None
    if target is not None:
        out, loudness_report = ENGINE.normalize_loudness(
            out, samplerate, target_lufs=target,
            limit_ceiling=spec.get("true_peak_dbtp", -1.0))
    fmt = args.format or spec.get("format", "wav")
    subtype = args.subtype or spec.get("subtype")
    out_path = args.output_path or os.path.join(
        args.out_dir or "out",
        f"{os.path.splitext(os.path.basename(args.input))[0]}-{args.preset}"
        f"-{samplerate}.{fmt}")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, out, samplerate, fmt=fmt, subtype=subtype,
                       overwrite=True)
    measurements, _, _ = measure(out_path)
    report.update({
        "status": "ok",
        "command": "encode",
        "output": out_path,
        "output_format": fmt,
        "output_subtype": measurements["subtype"],
        "output_samplerate": measurements["samplerate"],
        "output_channels": measurements["channels"],
        "loudness_normalization": loudness_report,
        "measurements": measurements,
        "comparison": compare_to_preset(measurements, {**spec, "format": fmt,
                                                       "subtype": measurements["subtype"]}),
    })
    RT.emit(report, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK if report["comparison"]["passed"] else RT.EXIT_INVALID_INPUT


def cmd_master(args):
    data, samplerate, _ = ENGINE.read_audio(args.input)
    before, _, _ = measure(args.input, thresholds=args.silence_threshold)
    out = ENGINE.to_channels(data, args.channels) if args.channels else data
    if args.dc_block:
        out = ENGINE.apply_eq(out, samplerate, highpass_hz=args.dc_block)
    if args.highpass_hz or args.lowpass_hz:
        out = ENGINE.apply_eq(out, samplerate, highpass_hz=args.highpass_hz,
                              lowpass_hz=args.lowpass_hz)
    if args.bands:
        eq_bands = []
        for spec in args.bands:
            try:
                frequency, gain = spec.split(":")
                eq_bands.append({"frequency_hz": float(frequency), "gain_db": float(gain)})
            except ValueError:
                raise RT.ToolError(
                    f"Bad --bands entry '{spec}'.",
                    code=RT.EXIT_INVALID_INPUT,
                    hint="Use FREQ:GAIN pairs, e.g. --bands 120:-2 4500:1.5")
        out = ENGINE.apply_eq(out, samplerate, bands=eq_bands)
    if args.compress:
        out = ENGINE.compress(out, samplerate, threshold_db=args.compress_threshold,
                              ratio=args.compress_ratio)
    if args.trim_silence:
        out = ENGINE.trim_silence(out, samplerate, threshold_dbfs=args.silence_threshold,
                                  lead=args.lead_in, tail=args.tail_time)
    if args.trim_start or args.trim_end:
        start = int((args.trim_start or 0) * samplerate)
        end = int(out.shape[0] - (args.trim_end or 0) * samplerate) if args.trim_end else out.shape[0]
        if end <= start:
            raise RT.ToolError("Trim points remove the whole file.",
                               code=RT.EXIT_INVALID_INPUT)
        out = out[start:end]
    if args.fade_in or args.fade_out:
        out = ENGINE.fade(out, samplerate, fade_in=args.fade_in, fade_out=args.fade_out)
    loudness_report = None
    target = args.target_lufs
    if target is None and args.preset:
        preset = _presets(args.assets_dir)["delivery_targets"].get(args.preset)
        if not preset:
            raise RT.ToolError(f"Unknown preset '{args.preset}'.",
                               code=RT.EXIT_NOT_FOUND)
        target = preset["lufs"]
    if target is not None:
        out, loudness_report = ENGINE.normalize_loudness(
            out, samplerate, target_lufs=target,
            limit_ceiling=args.true_peak_dbtp)
    else:
        out = ENGINE.normalize_peak(out, args.peak_dbfs)
    fmt = args.format or "wav"
    out_path = args.output_path or os.path.join(
        args.out_dir or "out",
        f"{os.path.splitext(os.path.basename(args.input))[0]}-master.{fmt}")
    if os.path.abspath(out_path) == os.path.abspath(args.input):
        raise RT.ToolError("Refusing to master a file over itself.",
                           code=RT.EXIT_INVALID_INPUT,
                           hint="Pass a different --output-path.")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, out, samplerate, fmt=fmt, subtype=args.subtype,
                       overwrite=True)
    after, _, _ = measure(out_path)
    RT.emit({"status": "ok", "command": "master", "input": args.input,
             "output": out_path, "before": before, "after": after,
             "loudness_normalization": loudness_report,
             "steps": [step for step, used in [
                 ("dc_block", bool(args.dc_block)),
                 ("eq", bool(args.highpass_hz or args.lowpass_hz or args.bands)),
                 ("compress", bool(args.compress)),
                 ("trim_silence", bool(args.trim_silence)),
                 ("trims", bool(args.trim_start or args.trim_end)),
                 ("fades", bool(args.fade_in or args.fade_out)),
                 ("loudness", target is not None),
                 ("limiter", target is not None),
                 ("peak_normalize", target is None)] if used]},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_repair(args):
    data, samplerate, _ = ENGINE.read_audio(args.input)
    before, _, _ = measure(args.input, thresholds=args.silence_threshold)
    out = data
    steps = []
    if args.declick:
        clicks = ENGINE.click_report(out, samplerate)
        out = ENGINE.declick(out, samplerate, threshold=args.declick_threshold)
        steps.append({"step": "declick", "detected": clicks["click_count"]})
    if args.highpass_hz:
        out = ENGINE.apply_eq(out, samplerate, highpass_hz=args.highpass_hz)
        steps.append({"step": "highpass", "frequency_hz": args.highpass_hz})
    if args.denoise_db:
        out = ENGINE.spectral_noise_reduction(out, samplerate,
                                              reduction_db=args.denoise_db)
        steps.append({"step": "spectral_noise_reduction", "reduction_db": args.denoise_db})
    if args.deesser:
        out = ENGINE.de_ess(out, samplerate, threshold_db=args.deesser_threshold,
                            reduction_db=args.deesser_db)
        steps.append({"step": "de_ess", "threshold_db": args.deesser_threshold})
    if args.gate_db:
        out = ENGINE.noise_gate(out, samplerate, threshold_dbfs=args.gate_db)
        steps.append({"step": "noise_gate", "threshold_dbfs": args.gate_db})
    if args.compress:
        out = ENGINE.compress(out, samplerate)
        steps.append({"step": "compress"})
    if args.trim_silence:
        out = ENGINE.trim_silence(out, samplerate, threshold_dbfs=args.silence_threshold)
        steps.append({"step": "trim_silence"})
    if args.target_lufs is not None:
        out, loudness = ENGINE.normalize_loudness(out, samplerate,
                                                  target_lufs=args.target_lufs,
                                                  limit_ceiling=args.true_peak_dbtp)
        steps.append({"step": "loudness", **loudness})
    if not steps:
        raise RT.ToolError(
            "No repair steps selected.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Enable at least one of --declick, --denoise-db, --deesser, "
                 "--gate-db, --highpass-hz, --compress, --trim-silence, --target-lufs.")
    fmt = args.format or "wav"
    out_path = args.output_path or os.path.join(
        args.out_dir or "out",
        f"{os.path.splitext(os.path.basename(args.input))[0]}-repaired.{fmt}")
    if os.path.abspath(out_path) == os.path.abspath(args.input):
        raise RT.ToolError("Refusing to overwrite the source recording.",
                           code=RT.EXIT_INVALID_INPUT,
                           hint="Repairs always write a new file; pass --output-path.")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, out, samplerate, fmt=fmt, subtype=args.subtype,
                       overwrite=True)
    after, _, _ = measure(out_path)
    improvement = round(after["noise_floor_dbfs"] - before["noise_floor_dbfs"], 2)
    RT.emit({"status": "ok", "command": "repair", "input": args.input,
             "output": out_path, "steps": steps,
             "before": before, "after": after,
             "noise_floor_improvement_db": improvement,
             "source_preserved": os.path.abspath(args.input) != os.path.abspath(out_path)},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_mix(args):
    voice, voice_rate, _ = ENGINE.read_audio(args.voice)
    bed, bed_rate, _ = ENGINE.read_audio(args.bed)
    if bed_rate != voice_rate:
        bed = ENGINE.resample(bed, bed_rate, voice_rate)
        bed_rate = voice_rate
    voice = ENGINE.to_channels(voice, 2) if args.stereo else voice
    bed = ENGINE.to_channels(bed, voice.shape[1])
    if bed.shape[0] < voice.shape[0]:
        repeat = int(voice.shape[0] / max(bed.shape[0], 1)) + 1
        bed = ENGINE.concatenate([bed] * repeat, gap_seconds=0.0, samplerate=voice_rate,
                                 crossfade_ms=20.0)
    bed = bed[:voice.shape[0]]
    voice_gain = ENGINE.peak_dbfs(voice)
    bed_gain = ENGINE.peak_dbfs(bed)
    bed = ENGINE.apply_gain(bed, (voice_gain + args.voice_to_bed_db) - bed_gain)
    if args.duck_db:
        # Voice-triggered ducking: reduce the bed while speech is present.
        segments = ENGINE.voice_activity(ENGINE.to_mono(voice), voice_rate)
        envelope = np.ones(voice.shape[0])
        depth = 10 ** (-abs(args.duck_db) / 20.0)
        for segment in segments:
            start = int(segment["start"] * voice_rate)
            end = int(segment["end"] * voice_rate)
            ramp = int(0.08 * voice_rate)
            envelope[start:end] = depth
            if start > ramp:
                envelope[start - ramp:start] = np.linspace(1.0, depth, ramp)
            if end + ramp < envelope.size:
                envelope[end:end + ramp] = np.linspace(depth, 1.0, ramp)
        bed = bed * (envelope[:, None] if bed.ndim > 1 else envelope)
    mix = voice + bed * (10 ** (args.bed_trim_db / 20.0))
    if args.target_lufs is not None:
        mix, loudness = ENGINE.normalize_loudness(mix, voice_rate,
                                                  target_lufs=args.target_lufs,
                                                  limit_ceiling=args.true_peak_dbtp)
    else:
        loudness = None
        mix = ENGINE.normalize_peak(mix, args.peak_dbfs)
    out_path = args.output_path or os.path.join(args.out_dir or "out",
                                                "mix.wav")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, mix, voice_rate, fmt="wav", overwrite=True)
    RT.emit({"status": "ok", "command": "mix", "output": out_path,
             "voice_to_bed_db": args.voice_to_bed_db, "duck_db": args.duck_db,
             "loudness_normalization": loudness,
             "measurements": measure(out_path)[0]},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_effects(args):
    presets = _presets(args.assets_dir)
    spec = presets["effects"].get(args.effect)
    if spec is None:
        raise RT.ToolError(
            f"Unknown effect rack '{args.effect}'.",
            code=RT.EXIT_NOT_FOUND,
            details={"available": sorted(presets["effects"])})
    data, samplerate, _ = ENGINE.read_audio(args.input)
    out = ENGINE.apply_effect_rack(data, samplerate, spec,
                                   intelligibility_floor=args.intelligibility_floor)
    if args.mix_dry > 0:
        wet = out if out.ndim == out.ndim else out
        dry = ENGINE.to_channels(data, wet.shape[1])
        length = min(dry.shape[0], wet.shape[0])
        out = dry[:length] * args.mix_dry + wet[:length] * (1.0 - args.mix_dry)
    out = ENGINE.normalize_peak(out, args.peak_dbfs)
    out_path = args.output_path or os.path.join(
        args.out_dir or "out",
        f"{os.path.splitext(os.path.basename(args.input))[0]}-{args.effect}.wav")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, out, samplerate, fmt="wav", overwrite=True)
    RT.emit({"status": "ok", "command": "effects", "effect": args.effect,
             "output": out_path, "rack": spec, "mix_dry": args.mix_dry,
             "measurements": measure(out_path)[0]},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_match_ambience(args):
    voice, voice_rate, _ = ENGINE.read_audio(args.input)
    reference, reference_rate, _ = ENGINE.read_audio(args.reference)
    if reference_rate != voice_rate:
        reference = ENGINE.resample(reference, reference_rate, voice_rate)
    ref_measure, _, _ = measure(args.reference)
    noise_target = ref_measure["noise_floor_dbfs"]
    out = ENGINE.to_channels(voice, 1)
    current_noise = ENGINE.noise_floor_dbfs(out, voice_rate)
    applied_noise = None
    if noise_target > current_noise + 1.0 and noise_target < -30:
        level = min(noise_target, -30.0)
        out = ENGINE.add_noise_floor(out, voice_rate, level_dbfs=level,
                                     seed=args.seed)
        applied_noise = level
    echo_report = None
    if args.add_echo and reference_rate:
        # Estimate echo from the reference by comparing early reflection energy.
        out = ENGINE.echo(out, voice_rate, delay_ms=args.echo_delay_ms,
                          decay=args.echo_decay, repeats=2)
        echo_report = {"delay_ms": args.echo_delay_ms, "decay": args.echo_decay}
    tilt = ENGINE.spectral_centroid(ENGINE.to_mono(reference), reference_rate) - \
        ENGINE.spectral_centroid(ENGINE.to_mono(out), voice_rate)
    if abs(tilt) > 50:
        out = ENGINE.apply_eq(out, voice_rate,
                              highpass_hz=None,
                              bands=[{"frequency_hz": 2500.0,
                                      "gain_db": RT.clamp(tilt / 400.0, -6.0, 6.0),
                                      "q": 0.8}])
    out_path = args.output_path or os.path.join(
        args.out_dir or "out",
        f"{os.path.splitext(os.path.basename(args.input))[0]}-ambience.wav")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, out, voice_rate, fmt="wav", overwrite=True)
    RT.emit({"status": "ok", "command": "match-ambience", "output": out_path,
             "reference": ref_measure, "noise_added_dbfs": applied_noise,
             "echo": echo_report, "spectral_tilt_hz": round(tilt, 1),
             "measurements": measure(out_path)[0]},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_concat(args):
    parts = []
    reports = []
    samplerate = None
    for path in args.inputs:
        data, rate, info = ENGINE.read_audio(path)
        if samplerate is None:
            samplerate = rate
        elif rate != samplerate:
            data = ENGINE.resample(data, rate, samplerate)
        if args.trim_dead_air:
            data = ENGINE.trim_silence(data, samplerate,
                                       threshold_dbfs=args.silence_threshold,
                                       lead=0.03, tail=0.08)
        parts.append(data)
        reports.append({"path": path, "duration_seconds": round(
            data.shape[0] / samplerate, 3)})
    combined = ENGINE.concatenate(parts, gap_seconds=args.gap,
                                  samplerate=samplerate,
                                  crossfade_ms=args.crossfade_ms)
    out_path = args.output_path or os.path.join(args.out_dir or "out", "joined.wav")
    if os.path.exists(out_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {out_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(out_path, combined, samplerate, fmt=args.format,
                       subtype=args.subtype, overwrite=True)
    RT.emit({"status": "ok", "command": "concat", "output": out_path,
             "inputs": reports, "gap_seconds": args.gap,
             "crossfade_ms": args.crossfade_ms,
             "total_duration_seconds": round(combined.shape[0] / samplerate, 3),
             "measurements": measure(out_path)[0]},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_cue_sheet(args):
    document = RT.read_json_file(args.input)
    if isinstance(document, dict) and "brief_example" in document:
        document = document["brief_example"]
    segments = document.get("segments")
    if not segments:
        raise RT.ToolError("Input has no 'segments' array to build cues from.",
                           code=RT.EXIT_INVALID_INPUT,
                           hint="Pass a production brief or an object with a "
                                "segments array of {id, text, start, end}.")
    cursor = 0.0
    cues = []
    for index, segment in enumerate(segments):
        start = float(segment.get("start", cursor))
        end = segment.get("end")
        if end is None:
            words = len(str(segment.get("text", "")).split())
            end = start + max(words / (args.wpm / 60.0), 0.5)
        end = float(end)
        if end <= start:
            raise RT.ToolError(
                f"Segment '{segment.get('id', index)}' has end <= start.",
                code=RT.EXIT_INVALID_INPUT)
        text = str(segment.get("text", "")).strip()
        cues.append({
            "index": index + 1,
            "id": segment.get("id", f"s{index + 1:03d}"),
            "speaker": segment.get("speaker"),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "text": text,
            "chars_per_second": round(len(text) / max(end - start, 0.001), 2),
            "words": len(text.split()),
        })
        cursor = end
    out_dir = RT.ensure_dir(args.out_dir or "out")
    written = []
    if args.output:
        RT.write_json_file(args.output, {"cues": cues,
                                        "project": document.get("project")})
        written.append(args.output)
    if args.formats and "json" in args.formats:
        path = os.path.join(out_dir, "cue-sheet.json")
        RT.write_json_file(path, {"project": document.get("project"),
                                  "generated_at": RT.now_iso(), "cues": cues})
        written.append(path)
    if args.formats and "csv" in args.formats:
        path = os.path.join(out_dir, "cue-sheet.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(cues[0].keys()))
            writer.writeheader()
            writer.writerows(cues)
        written.append(path)
    if args.formats and "srt" in args.formats:
        path = os.path.join(out_dir, "cue-sheet.srt")
        lines = []
        for cue in cues:
            lines.append(str(cue["index"]))
            lines.append(f"{RT.format_srt_time(cue['start'])} --> "
                         f"{RT.format_srt_time(cue['end'])}")
            lines.append(("[" + cue["speaker"] + "] " if cue.get("speaker") else "") + cue["text"])
            lines.append("")
        RT.atomic_write_text(path, "\n".join(lines))
        written.append(path)
    RT.emit({"status": "ok", "command": "cue-sheet", "project": document.get("project"),
             "cue_count": len(cues), "cues": cues, "files": written,
             "max_chars_per_second": max(c["chars_per_second"] for c in cues)},
            output=None, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_package(args):
    root = args.input
    if not os.path.isdir(root):
        raise RT.ToolError(f"--input must be a directory: {root}", code=RT.EXIT_NOT_FOUND)
    files = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path) or name.endswith("-manifest.json"):
            continue
        if os.path.splitext(name)[1].lower() not in (".wav", ".mp3", ".flac", ".ogg",
                                                    ".opus", ".aiff", ".m4a"):
            continue
        measurements, _, _ = measure(path)
        files.append({
            "path": name,
            "sha256": measurements["sha256"],
            "bytes": measurements["bytes"],
            "sample_rate": measurements["samplerate"],
            "channels": measurements["channels"],
            "duration_seconds": measurements["duration_seconds"],
            "format": measurements["format"].lower(),
            "integrated_lufs": measurements["integrated_lufs"],
            "true_peak_dbtp": measurements["true_peak_dbtp"],
            "disclosure": args.disclosure or "required",
        })
    if not files:
        raise RT.ToolError(
            f"No deliverable audio files found in '{root}'.",
            code=RT.EXIT_NOT_FOUND,
            hint="Run tools-audio-synthesis.py render-brief or put mastered "
                 "files in the folder first.")
    manifest = {
        "project": args.project or os.path.basename(os.path.abspath(root)),
        "generated_at": RT.now_iso(),
        "voice_profile": args.profile_id,
        "target_lufs": args.target_lufs,
        "files": files,
        "rights": {
            "license": args.license,
            "disclosure": args.disclosure or "required",
            "consent_id": args.consent_id,
        },
    }
    manifest = {k: v for k, v in manifest.items() if v is not None}
    manifest["rights"] = {k: v for k, v in manifest["rights"].items() if v is not None}
    RT.assert_valid(manifest, RT.schema_for(_schemas(args.assets_dir), "delivery_manifest"),
                    label="Delivery manifest")
    path = args.manifest or os.path.join(root, f"{RT.slugify(manifest['project'])}-manifest.json")
    RT.write_json_file(path, manifest, overwrite=args.force)
    RT.emit({"status": "ok", "command": "package", "manifest": path,
             "file_count": len(files), "manifest_data": manifest},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_validate_package(args):
    root = args.input
    if not os.path.isdir(root):
        raise RT.ToolError(f"--input must be a directory: {root}", code=RT.EXIT_NOT_FOUND)
    candidates = [args.manifest] if args.manifest else sorted(
        os.path.join(root, name) for name in os.listdir(root)
        if name.endswith("-manifest.json") or name == "manifest.json")
    if not candidates:
        raise RT.ToolError(
            f"No manifest found in '{root}'.",
            code=RT.EXIT_NOT_FOUND,
            hint="Run 'package --input <dir>' first, or pass --manifest <file>.")
    manifest_path = candidates[0]
    manifest = RT.read_json_file(manifest_path)
    problems = RT.validate_schema(
        manifest, RT.schema_for(_schemas(args.assets_dir), "delivery_manifest"))
    issues = [{"check": "schema", "severity": "error", "detail": p} for p in problems]
    verified = []
    for entry in manifest.get("files", []):
        path = entry["path"] if os.path.isabs(entry["path"]) else os.path.join(root, entry["path"])
        if not os.path.isfile(path):
            issues.append({"check": "missing_file", "severity": "error",
                           "detail": f"{entry['path']} is listed but missing."})
            continue
        actual_hash = RT.sha256_file(path)
        if actual_hash != entry["sha256"]:
            issues.append({"check": "hash_mismatch", "severity": "error",
                           "detail": f"{entry['path']} hash differs from the manifest."})
        measurements, _, _ = measure(path)
        verified.append({"path": entry["path"], "sha256_match": actual_hash == entry["sha256"],
                         "bytes": measurements["bytes"],
                         "duration_seconds": measurements["duration_seconds"],
                         "integrated_lufs": measurements["integrated_lufs"],
                         "true_peak_dbtp": measurements["true_peak_dbtp"]})
        if measurements["clipping"]["clipped_samples"] > 0:
            issues.append({"check": "clipping", "severity": "error",
                           "detail": f"{entry['path']} has "
                                     f"{measurements['clipping']['clipped_samples']} clipped samples."})
        baseline = manifest.get("target_lufs") or args.target_lufs
        if baseline is not None and abs(measurements["integrated_lufs"] - baseline) > args.tolerance_lu:
            issues.append({"check": "loudness", "severity": "warning",
                           "detail": f"{entry['path']} is {measurements['integrated_lufs']} LUFS "
                                     f"against a {baseline} LUFS target."})
        if args.naming_pattern:
            import fnmatch
            if not fnmatch.fnmatch(entry["path"], args.naming_pattern):
                issues.append({"check": "naming", "severity": "error",
                               "detail": f"{entry['path']} does not match "
                                         f"{args.naming_pattern}."})
        if " " in entry["path"]:
            issues.append({"check": "naming", "severity": "error",
                           "detail": f"{entry['path']} contains spaces."})
    rights = manifest.get("rights", {})
    policy = _policy(args.assets_dir)
    for field in policy["release_gate"]["required_right_checks"]:
        if not rights.get(field):
            issues.append({"check": "rights", "severity": "error",
                           "detail": f"Manifest rights.{field} is missing."})
    if rights.get("disclosure") in (None, "none"):
        issues.append({"check": "disclosure", "severity": "error",
                       "detail": "Manifest does not record a disclosure status."})
    passed = not [i for i in issues if i["severity"] == "error"]
    if args.release:
        if not passed:
            raise RT.ToolError(
                "Release blocked: the package has unresolved errors.",
                code=RT.EXIT_BLOCKED,
                details={"issues": [i for i in issues if i["severity"] == "error"][:20]},
                hint="Fix the reported issues, re-run validate-package, then release.")
        RT.require_approval(args, "release", detail=manifest.get("project", root))
    result = {
        "status": "ok" if passed else "failed",
        "command": "validate-package",
        "root": root,
        "manifest": manifest_path,
        "project": manifest.get("project"),
        "files_verified": verified,
        "issues": issues,
        "error_count": len([i for i in issues if i["severity"] == "error"]),
        "warning_count": len([i for i in issues if i["severity"] == "warning"]),
        "released": bool(args.release and passed),
    }
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK if passed else RT.EXIT_INVALID_INPUT


def cmd_checklist(args):
    checklists = _checklists(args.assets_dir)["checklists"]
    if args.name not in checklists:
        raise RT.ToolError(
            f"Unknown checklist '{args.name}'.",
            code=RT.EXIT_NOT_FOUND,
            details={"available": sorted(checklists)})
    checklist = checklists[args.name]
    measurements, data, samplerate = measure(args.input)
    measured = {
        "loudness": measurements["integrated_lufs"],
        "true_peak": measurements["true_peak_dbtp"],
        "peak": measurements["peak_dbfs"],
        "noise_floor": measurements["noise_floor_dbfs"],
        "clipping": measurements["clipping"]["clipped_samples"],
        "duration": measurements["duration_seconds"],
        "samplerate": measurements["samplerate"],
        "channels": measurements["channels"],
        "silence": measurements["silence_regions"],
        "pitch": measurements["pitch_median_hz"],
        "format": measurements["format"],
    }
    verdicts = []
    for check in checklist["checks"]:
        rule = check["rule"].lower()
        verdict = {"id": check["id"], "rule": check["rule"],
                   "command": check["command"], "kind": "manual"}
        if "lufs" in rule:
            verdict.update({"kind": "measured", "value": measurements["integrated_lufs"],
                            "pass": "-16" in rule and abs(measurements["integrated_lufs"] + 16) <= 1.0
                            or "-18" in rule and abs(measurements["integrated_lufs"] + 18) <= 1.0
                            or "-20" in rule and abs(measurements["integrated_lufs"] + 20) <= 1.0})
            if "pass" not in verdict:
                verdict["pass"] = None
        elif "true peak" in rule or "dbtp" in rule:
            target = -3.0 if "-3" in rule else -1.0
            verdict.update({"kind": "measured", "value": measurements["true_peak_dbtp"],
                            "pass": measurements["true_peak_dbtp"] <= target + 0.05})
        elif "clipped" in rule or "clipping" in rule:
            verdict.update({"kind": "measured",
                            "value": measurements["clipping"]["clipped_samples"],
                            "pass": measurements["clipping"]["clipped_samples"] == 0})
        elif "noise floor" in rule:
            verdict.update({"kind": "measured", "value": measurements["noise_floor_dbfs"],
                            "pass": measurements["noise_floor_dbfs"] <= -50.0})
        elif "silence" in rule:
            longest = max((s["duration"] for s in measurements["silence_regions"]), default=0.0)
            verdict.update({"kind": "measured", "value": longest,
                            "pass": longest <= (3.0 if "3 s" in rule else 1.2)})
        elif "sample rate" in rule:
            verdict.update({"kind": "measured", "value": measurements["samplerate"],
                            "pass": 48000 == measurements["samplerate"]
                            if "48" in rule else None})
        elif "duration" in rule:
            verdict.update({"kind": "measured", "value": measurements["duration_seconds"],
                            "pass": None})
        elif "semitones" in rule or "identity" in rule:
            verdict.update({"kind": "measured", "value": measurements["pitch_median_hz"],
                            "pass": None})
        verdicts.append(verdict)
    measured_checks = [v for v in verdicts if v["kind"] == "measured" and v["pass"] is not None]
    passed = all(v["pass"] for v in measured_checks) if measured_checks else None
    RT.emit({"status": "ok" if passed in (True, None) else "failed",
             "command": "checklist", "checklist": args.name,
             "title": checklist["title"], "input": args.input,
             "measurements": measured,
             "checks": verdicts,
             "measured_checks": len(measured_checks),
             "failed_checks": [v["id"] for v in measured_checks if not v["pass"]],
             "manual_checks": [v["id"] for v in verdicts if v["kind"] == "manual"]},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK if passed in (True, None) else RT.EXIT_INVALID_INPUT


def cmd_capabilities(args):
    RT.emit(RT.capabilities_report(
        PROG, CAPABILITIES,
        notes={"role": "editing, mixing, mastering, delivery validation",
               "assets": ["asset-voice-presets.json", "asset-checklists.json",
                          "asset-policy.json", "asset-schemas.json"],
               "never_overwrites_source": True}),
        output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = RT.build_parser(
        PROG,
        "Voice Capability Toolkit - repair, mix, master, package and validate "
        "spoken-audio deliverables.",
        epilog="Examples:\n"
               "  python3 tools-audio-delivery.py encode --input take.wav "
               "--preset podcast --out-dir out\n"
               "  python3 tools-audio-delivery.py validate-package --input out\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("presets", help="List delivery presets.")
    p.add_argument("--name", help="Show one preset in detail.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_presets)

    p = sub.add_parser("inspect", help="Measure a file and compare with a preset.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--preset")
    p.add_argument("--silence-threshold", type=float, default=-45.0)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("encode", help="Encode to a delivery preset.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--preset", required=True)
    p.add_argument("--format", help="Override the preset container (wav, mp3, flac, ogg, opus, aiff).")
    p.add_argument("--subtype")
    p.add_argument("--target-lufs", type=float)
    p.add_argument("--no-band-limit", action="store_true")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_encode)

    p = sub.add_parser("master", help="Master spoken audio.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--preset", help="Delivery preset supplying the loudness target.")
    p.add_argument("--target-lufs", type=float)
    p.add_argument("--true-peak-dbtp", type=float, default=-1.0)
    p.add_argument("--peak-dbfs", type=float, default=-1.0,
                   help="Peak normalisation when no loudness target is set.")
    p.add_argument("--dc-block", type=float, default=30.0,
                   help="High-pass frequency for DC/rumble removal (default 30 Hz).")
    p.add_argument("--highpass-hz", type=float)
    p.add_argument("--lowpass-hz", type=float)
    p.add_argument("--bands", nargs="*", help="EQ bands as FREQ:GAIN pairs.")
    p.add_argument("--compress", action="store_true")
    p.add_argument("--compress-threshold", type=float, default=-18.0)
    p.add_argument("--compress-ratio", type=float, default=3.0)
    p.add_argument("--trim-silence", action="store_true")
    p.add_argument("--trim-start", type=float)
    p.add_argument("--trim-end", type=float)
    p.add_argument("--silence-threshold", type=float, default=-45.0)
    p.add_argument("--lead-in", type=float, default=0.05)
    p.add_argument("--tail-time", type=float, default=0.15)
    p.add_argument("--fade-in", type=float, default=0.0)
    p.add_argument("--fade-out", type=float, default=0.0)
    p.add_argument("--channels", type=int)
    p.add_argument("--format", default="wav")
    p.add_argument("--subtype")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_master)

    p = sub.add_parser("repair", help="Run the repair chain.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--declick", action="store_true")
    p.add_argument("--declick-threshold", type=float, default=6.0)
    p.add_argument("--denoise-db", type=float)
    p.add_argument("--deesser", action="store_true")
    p.add_argument("--deesser-threshold", type=float, default=-28.0)
    p.add_argument("--deesser-db", type=float, default=6.0)
    p.add_argument("--gate-db", type=float)
    p.add_argument("--highpass-hz", type=float, default=80.0)
    p.add_argument("--compress", action="store_true")
    p.add_argument("--trim-silence", action="store_true")
    p.add_argument("--silence-threshold", type=float, default=-45.0)
    p.add_argument("--target-lufs", type=float)
    p.add_argument("--true-peak-dbtp", type=float, default=-1.0)
    p.add_argument("--format", default="wav")
    p.add_argument("--subtype")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_repair)

    p = sub.add_parser("mix", help="Mix voice with a bed.")
    p.add_argument("--voice", required=True)
    p.add_argument("--bed", required=True)
    p.add_argument("--voice-to-bed-db", type=float, default=6.0,
                   help="Voice peak above bed peak in dB (default 6).")
    p.add_argument("--bed-trim-db", type=float, default=0.0)
    p.add_argument("--duck-db", type=float, default=9.0)
    p.add_argument("--stereo", action="store_true")
    p.add_argument("--target-lufs", type=float, default=-16.0)
    p.add_argument("--true-peak-dbtp", type=float, default=-1.0)
    p.add_argument("--peak-dbfs", type=float, default=-1.0)
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_mix)

    p = sub.add_parser("effects", help="Apply an environment/effect rack.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--effect", required=True)
    p.add_argument("--mix-dry", type=float, default=0.0,
                   help="0 = fully wet (default), 1 = dry.")
    p.add_argument("--intelligibility-floor", type=float, default=0.35)
    p.add_argument("--peak-dbfs", type=float, default=-1.0)
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_effects)

    p = sub.add_parser("match-ambience", help="Match a voice to a reference ambience.")
    p.add_argument("-i", "--input", required=True, help="Clean voice file.")
    p.add_argument("--reference", required=True, help="Reference recording.")
    p.add_argument("--add-echo", action="store_true")
    p.add_argument("--echo-delay-ms", type=float, default=45.0)
    p.add_argument("--echo-decay", type=float, default=0.25)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_match_ambience)

    p = sub.add_parser("concat", help="Join clips with gaps and crossfades.")
    p.add_argument("--inputs", nargs="+", required=True)
    p.add_argument("--gap", type=float, default=0.15)
    p.add_argument("--crossfade-ms", type=float, default=8.0)
    p.add_argument("--trim-dead-air", action="store_true")
    p.add_argument("--silence-threshold", type=float, default=-45.0)
    p.add_argument("--format", default="wav")
    p.add_argument("--subtype")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--output-path")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_concat)

    p = sub.add_parser("cue-sheet", help="Write cue sheets from a brief.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--wpm", type=float, default=155.0,
                   help="Words per minute used when a segment has no end time.")
    p.add_argument("--formats", nargs="*", default=["json", "csv", "srt"],
                   choices=["json", "csv", "srt"])
    p.add_argument("--out-dir", default="out")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_cue_sheet)

    p = sub.add_parser("package", help="Build a delivery manifest.")
    p.add_argument("-i", "--input", required=True, help="Deliverable directory.")
    p.add_argument("--project")
    p.add_argument("--profile-id")
    p.add_argument("--target-lufs", type=float)
    p.add_argument("--license", default="internal-original-design")
    p.add_argument("--disclosure", default="required")
    p.add_argument("--consent-id")
    p.add_argument("--manifest", help="Manifest output path.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("validate-package", help="Validate a delivery folder.")
    p.add_argument("-i", "--input", required=True, help="Deliverable directory.")
    p.add_argument("--manifest", help="Explicit manifest path.")
    p.add_argument("--target-lufs", type=float)
    p.add_argument("--tolerance-lu", type=float, default=1.0)
    p.add_argument("--naming-pattern", help="fnmatch pattern every file must match.")
    p.add_argument("--release", action="store_true",
                   help="Mark the package released (requires --approve and a clean result).")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_validate_package)

    p = sub.add_parser("checklist", help="Run a delivery checklist on one file.")
    p.add_argument("--name", required=True)
    p.add_argument("-i", "--input", required=True)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_checklist)

    p = sub.add_parser("capabilities", help="Print the command index.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_capabilities)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    for attribute, default in (("assets_dir", None), ("force", False), ("approve", False),
                               ("timeout", 300.0), ("quiet", False)):
        if not hasattr(args, attribute):
            setattr(args, attribute, default)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(RT.guard(main)())
