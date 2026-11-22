#!/usr/bin/env python3
"""Voice Capability Toolkit - analysis, detection and diagnostics tool.

Measurements and evidence-based diagnostics. Every claim is reported with the
measurement it came from, and detection outputs carry the documented limits:
heuristics support review, they are not proof of provenance, identity or intent.

Commands
--------
    capabilities   Machine-readable command index
    metrics        Duration, loudness, peaks, noise, silence, pitch, energy, bands
    vad            Speech / non-speech segmentation
    diarize        Heuristic 2-4 speaker turn labelling (pitch+energy clustering)
    alignment      Align script sentences to detected speech spans
    artifacts      Clicks, clipping, dropouts, spectral jumps, metallic indicators
    environment    Noise floor, DC, band tilt, reverb estimate, mic/processing clues
    attributes     Pitch range, pace, energy, timbre descriptors
    suitability    Is this source audio usable for conversion/voice-design?
    compare        Golden-sample / A-B comparison between two renders
    diagnostic     Likely causes of quality issues with the smallest repair step
"""

from __future__ import annotations

import argparse
import importlib.util
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
np = RT.require_numpy()
PROG = "tools-audio-analysis.py"

CAPABILITIES = [
    {"command": "capabilities", "summary": "Print this command index.", "inputs": [], "outputs": ["JSON"]},
    {"command": "metrics", "summary": "Compute a configurable set of measurements (duration, level, loudness, clipping, silence, noise, pitch, energy, bands, clicks).", "inputs": ["--input audio", "--ops"], "outputs": ["metrics JSON"]},
    {"command": "vad", "summary": "Detect speech and non-speech spans with an energy VAD (documented limits).", "inputs": ["--input audio"], "outputs": ["segments"]},
    {"command": "diarize", "summary": "Label speaker turns with heuristic pitch/energy clustering (2-4 clusters).", "inputs": ["--input audio", "--speakers"], "outputs": ["turns"]},
    {"command": "alignment", "summary": "Align script sentences to detected speech spans and report fit quality.", "inputs": ["--input audio", "--text"], "outputs": ["alignment table"]},
    {"command": "artifacts", "summary": "Detect clicks, clipping, dropouts, abrupt spectral changes and metallic/flatness indicators.", "inputs": ["--input audio"], "outputs": ["artifact report"]},
    {"command": "environment", "summary": "Estimate noise floor, DC offset, band tilt, reverberation and processing clues.", "inputs": ["--input audio"], "outputs": ["environment report"]},
    {"command": "attributes", "summary": "Estimate pitch range, pace, energy and timbre descriptors for a recording.", "inputs": ["--input audio"], "outputs": ["attribute report"]},
    {"command": "suitability", "summary": "Score whether a source recording is clean and sufficient for conversion or voice design.", "inputs": ["--input audio", "--purpose"], "outputs": ["suitability report"]},
    {"command": "compare", "summary": "Compare a candidate render against a golden reference on loudness, pitch, pace, timbre and duration.", "inputs": ["--input", "--reference"], "outputs": ["comparison report"]},
    {"command": "diagnostic", "summary": "Explain likely quality problems and recommend the smallest repair step for each.", "inputs": ["--input audio"], "outputs": ["findings + repair commands"]},
]


def _policy(assets_dir=None):
    return RT.load_asset_json("asset-policy.json", assets_dir)[0]


BANDS = [("sub", 20, 60), ("bass", 60, 250), ("low_mid", 250, 800),
         ("mid", 800, 2000), ("presence", 2000, 5000), ("brilliance", 5000, 12000),
         ("air", 12000, 20000)]

OPS = ["duration", "level", "loudness", "clipping", "silence", "noise", "pitch",
       "energy", "bands", "centroid", "dc", "zcr", "clicks", "flatness"]


def load(path):
    data, samplerate, info = ENGINE.read_audio(path)
    return data, samplerate, info


def compute_metrics(path, ops=None, *, silence_threshold=-45.0):
    ops = ops or ["duration", "level", "loudness", "clipping", "silence", "noise",
                  "pitch", "energy", "centroid"]
    unknown = [op for op in ops if op not in OPS]
    if unknown:
        raise RT.ToolError(
            f"Unknown metric op(s): {', '.join(unknown)}.",
            code=RT.EXIT_INVALID_INPUT,
            details={"available": OPS})
    data, samplerate, info = load(path)
    mono = ENGINE.to_mono(data)
    out = {"path": path, "ops": ops}
    if "duration" in ops:
        out["duration"] = {"seconds": round(data.shape[0] / samplerate, 4),
                           "frames": int(data.shape[0]),
                           "samplerate": samplerate,
                           "channels": int(data.shape[1]),
                           "format": info.format, "subtype": info.subtype,
                           "bytes": os.path.getsize(path)}
    if "level" in ops:
        out["level"] = {"peak_dbfs": round(ENGINE.peak_dbfs(mono), 2),
                        "true_peak_dbtp": round(ENGINE.true_peak_dbtp(mono), 2),
                        "rms_dbfs": round(ENGINE.rms_dbfs(mono), 2)}
    if "loudness" in ops:
        out["loudness"] = {"integrated_lufs": round(
            ENGINE.integrated_loudness(data, samplerate), 2)}
    if "clipping" in ops:
        out["clipping"] = ENGINE.clipping_report(mono)
    if "silence" in ops:
        regions = ENGINE.silence_regions(mono, samplerate,
                                         threshold_dbfs=silence_threshold)
        out["silence"] = {"threshold_dbfs": silence_threshold,
                          "region_count": len(regions),
                          "total_seconds": round(sum(r["duration"] for r in regions), 3),
                          "longest_seconds": round(max((r["duration"] for r in regions),
                                                       default=0.0), 3),
                          "regions": regions[:60]}
    if "noise" in ops:
        out["noise"] = {"noise_floor_dbfs": round(
            ENGINE.noise_floor_dbfs(mono, samplerate), 2)}
    if "pitch" in ops:
        pitch = ENGINE.estimate_pitch(mono, samplerate)
        out["pitch"] = {k: v for k, v in pitch.items() if k != "f0_contour"}
    if "energy" in ops:
        contour = ENGINE.energy_contour(mono, samplerate)
        values = [point["db"] for point in contour]
        out["energy"] = {"mean_db": round(float(np.mean(values)), 2) if values else None,
                         "max_db": round(max(values), 2) if values else None,
                         "contour_points": len(values),
                         "contour": contour[:200]}
    if "bands" in ops:
        out["bands"] = ENGINE.band_energy(mono, samplerate, BANDS)
    if "centroid" in ops:
        out["spectral_centroid_hz"] = ENGINE.spectral_centroid(mono, samplerate)
    if "dc" in ops:
        out["dc_offset"] = ENGINE.dc_offset(mono)
    if "zcr" in ops:
        out["zero_crossing_rate"] = ENGINE.zero_crossing_rate(mono)
    if "clicks" in ops:
        out["clicks"] = ENGINE.click_report(mono, samplerate)
    if "flatness" in ops:
        out["spectral_flatness"] = ENGINE.spectral_flatness(mono, samplerate)
    return out


def confidence(note):
    return note


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_metrics(args):
    ops = [op.strip() for op in args.ops.split(",") if op.strip()] if args.ops else None
    result = compute_metrics(args.input, ops, silence_threshold=args.silence_threshold)
    RT.emit({"status": "ok", "command": "metrics", **result},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_vad(args):
    data, samplerate, _ = load(args.input)
    mono = ENGINE.to_mono(data)
    segments = ENGINE.voice_activity(mono, samplerate,
                                     threshold_dbfs=args.threshold,
                                     min_seconds=args.min_seconds,
                                     merge_gap=args.merge_gap)
    total = sum(segment["duration"] for segment in segments)
    RT.emit({
        "status": "ok", "command": "vad", "input": args.input,
        "segments": segments, "segment_count": len(segments),
        "speech_seconds": round(total, 3),
        "duration_seconds": round(mono.size / samplerate, 3),
        "speech_ratio": round(total / max(mono.size / samplerate, 1e-6), 3),
        "method": "energy threshold with hangover merging",
        "confidence_note": confidence(
            "Energy VAD, not a trained model: quiet speech near the threshold may "
            "be missed and loud breath or music may be included."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_diarize(args):
    data, samplerate, _ = load(args.input)
    mono = ENGINE.to_mono(data)
    segments = ENGINE.voice_activity(mono, samplerate, threshold_dbfs=args.threshold,
                                     min_seconds=0.25)
    if len(segments) < args.speakers:
        raise RT.ToolError(
            f"Only {len(segments)} speech segment(s) detected; need at least "
            f"{args.speakers} for {args.speakers}-speaker clustering.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Lower --threshold, raise --speakers, or use a longer recording.")
    features = []
    for segment in segments:
        start = int(segment["start"] * samplerate)
        end = int(segment["end"] * samplerate)
        chunk = mono[start:end]
        pitch = ENGINE.estimate_pitch(chunk, samplerate)
        features.append([
            pitch.get("median_hz") or 0.0,
            ENGINE.rms_dbfs(chunk),
            ENGINE.spectral_centroid(chunk, samplerate) / 1000.0,
        ])
    matrix = np.asarray(features, dtype=np.float64)
    matrix = (matrix - matrix.mean(axis=0)) / np.maximum(matrix.std(axis=0), 1e-6)
    clusters = _kmeans(matrix, args.speakers, seed=args.seed, iterations=60)
    turns = []
    for index, (segment, cluster) in enumerate(zip(segments, clusters)):
        turns.append({"turn": index + 1, "speaker": f"spk-{int(cluster) + 1}",
                      "start": segment["start"], "end": segment["end"],
                      "duration": segment["duration"],
                      "median_pitch_hz": round(features[index][0], 1)})
    speaker_stats = []
    for speaker in sorted({turn["speaker"] for turn in turns}):
        subset = [turn for turn in turns if turn["speaker"] == speaker]
        speaker_stats.append({
            "speaker": speaker, "turn_count": len(subset),
            "speech_seconds": round(sum(t["duration"] for t in subset), 3),
            "median_pitch_hz": round(float(np.median(
                [t["median_pitch_hz"] for t in subset if t["median_pitch_hz"]])), 1)
            if any(t["median_pitch_hz"] for t in subset) else None,
        })
    RT.emit({"status": "ok", "command": "diarize", "input": args.input,
             "requested_speakers": args.speakers, "turns": turns,
             "speakers": speaker_stats, "turn_count": len(turns),
             "method": "k-means over (median pitch, RMS dB, spectral centroid)",
             "confidence_note": confidence(
                 "Heuristic diarization for clean recordings with distinct voices. "
                 "It cannot verify identity and will confuse similar voices or "
                 "overlapping speech; treat labels as provisional.")},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def _kmeans(matrix, k, *, seed=5, iterations=50):
    rng = np.random.default_rng(seed)
    if matrix.shape[0] <= k:
        return np.arange(matrix.shape[0]) % k
    centres = matrix[rng.choice(matrix.shape[0], size=k, replace=False)]
    labels = np.zeros(matrix.shape[0], dtype=int)
    for _ in range(iterations):
        distances = np.linalg.norm(matrix[:, None, :] - centres[None, :, :], axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for index in range(k):
            members = matrix[labels == index]
            if len(members):
                centres[index] = members.mean(axis=0)
    return labels


def cmd_alignment(args):
    data, samplerate, _ = load(args.input)
    mono = ENGINE.to_mono(data)
    if args.text:
        text = args.text
    elif args.input_text:
        text, _ = RT.read_text_input(argparse.Namespace(input=args.input_text, text=None))
    else:
        raise RT.ToolError("Pass --text or --input-text with the script.",
                           code=RT.EXIT_INVALID_INPUT)
    import re
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    segments = ENGINE.voice_activity(mono, samplerate, threshold_dbfs=-45.0,
                                     min_seconds=0.05, merge_gap=0.25)
    if not segments:
        raise RT.ToolError("No speech detected to align against.",
                           code=RT.EXIT_INVALID_INPUT)
    total_speech = sum(s["duration"] for s in segments)
    words = [max(len(sentence.split()), 1) for sentence in sentences]
    total_words = sum(words)
    rows = []
    cursor = segments[0]["start"]
    speech_cursor = 0.0
    for sentence, word_count in zip(sentences, words):
        share = word_count / total_words
        duration = total_speech * share
        start = cursor
        end = start + duration
        rows.append({
            "sentence": sentence,
            "words": word_count,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "words_per_minute": round(word_count / max(duration, 1e-6) * 60.0, 1),
        })
        cursor = end
        speech_cursor += duration
    drift = round(cursor - (segments[-1]["end"]), 3)
    RT.emit({
        "status": "ok", "command": "alignment", "input": args.input,
        "sentence_count": len(rows),
        "segments": rows,
        "detected_speech_seconds": round(total_speech, 3),
        "alignment_end_drift_seconds": drift,
        "method": "proportional word-weighted fit to detected speech spans",
        "confidence_note": confidence(
            "Estimated alignment: sentence boundaries are distributed by word count "
            "over detected speech. It does not do phoneme-level forced alignment; "
            "use it for cue drafts and run a listening check before delivery."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_artifacts(args):
    data, samplerate, _ = load(args.input)
    mono = ENGINE.to_mono(data)
    clicks = ENGINE.click_report(mono, samplerate, threshold=args.click_threshold)
    clipping = ENGINE.clipping_report(mono)
    segments = ENGINE.voice_activity(mono, samplerate, min_seconds=0.05)
    gaps = []
    for previous, current in zip(segments, segments[1:]):
        gap = current["start"] - previous["end"]
        if gap > 0:
            gaps.append(round(gap, 3))
    abrupt = _spectral_jumps(mono, samplerate)
    flatness = ENGINE.spectral_flatness(mono)
    findings = []
    if clicks["click_count"] > args.max_clicks:
        findings.append({"artifact": "clicks", "severity": "warning",
                         "detail": f"{clicks['click_count']} impulsive events detected.",
                         "repair": "tools-audio-delivery.py repair --declick"})
    if clipping["clipped_samples"] > 0:
        findings.append({"artifact": "clipping", "severity": "error",
                         "detail": f"{clipping['clipped_samples']} samples at or above full scale.",
                         "repair": "re-render with a lower target loudness; clipping "
                                   "cannot be repaired after the fact"})
    if abrupt:
        findings.append({"artifact": "spectral_jump", "severity": "info",
                         "detail": f"{len(abrupt)} abrupt spectral change(s) detected.",
                         "repair": "check edits for seams; tools-audio-delivery.py "
                                   "concat --crossfade-ms 15"})
    if flatness > 0.35:
        findings.append({"artifact": "noise_like_spectrum", "severity": "warning",
                         "detail": f"High spectral flatness ({flatness}) suggests noise "
                                   "or heavy processing.",
                         "repair": "tools-audio-delivery.py repair --denoise-db 10"})
    RT.emit({
        "status": "ok", "command": "artifacts", "input": args.input,
        "clicks": clicks["click_count"], "click_events": clicks["events"][:20],
        "clipping": clipping, "silence_gaps": gaps[:40],
        "spectral_jumps": abrupt[:20],
        "spectral_flatness": flatness,
        "findings": findings,
        "confidence_note": confidence(
            "Signal-statistics heuristics. A 'synthetic' or 'processed' indicator is "
            "never proof of origin, and the absence of findings is never proof of "
            "authenticity."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK if not [f for f in findings if f["severity"] == "error"] \
        else RT.EXIT_INVALID_INPUT


def _spectral_jumps(mono, samplerate, *, frame_ms=50.0, threshold_db=18.0):
    window = int(samplerate * frame_ms / 1000.0)
    if mono.size < window * 4:
        return []
    hop = window // 2
    centroids = []
    times = []
    for start in range(0, mono.size - window, hop):
        chunk = mono[start:start + window]
        centroids.append(ENGINE.spectral_centroid(chunk, samplerate))
        times.append(round(start / samplerate, 3))
    jumps = []
    for index in range(1, len(centroids)):
        previous = max(centroids[index - 1], 1.0)
        delta_db = 20.0 * np.log10(max(centroids[index], 1.0) / previous)
        if abs(delta_db) > threshold_db:
            jumps.append({"time": times[index],
                          "centroid_hz": centroids[index],
                          "delta_db": round(float(delta_db), 2)})
    return jumps


def cmd_environment(args):
    data, samplerate, info = load(args.input)
    mono = ENGINE.to_mono(data)
    segments = ENGINE.voice_activity(mono, samplerate, min_seconds=0.1)
    noise = ENGINE.noise_floor_dbfs(mono, samplerate)
    bands = ENGINE.band_energy(mono, samplerate, BANDS)
    reverb = _estimate_reverb(mono, samplerate, segments)
    clues = []
    if bands.get("air", 0) < -60 and samplerate >= 44100:
        clues.append("Very little energy above 12 kHz: possible low-pass, lossy "
                     "codec, or narrowband processing.")
    if bands.get("sub", 0) > -25:
        clues.append("Strong sub-bass energy: possible rumble, handling noise or DC.")
    if abs(ENGINE.dc_offset(mono)) > 0.002:
        clues.append("Non-trivial DC offset: a high-pass at 30-80 Hz is recommended.")
    if noise > -45:
        clues.append("Noise floor above -45 dBFS: denoise or re-record before delivery.")
    RT.emit({
        "status": "ok", "command": "environment", "input": args.input,
        "samplerate": samplerate, "channels": int(data.shape[1]),
        "noise_floor_dbfs": round(noise, 2),
        "dc_offset": ENGINE.dc_offset(mono),
        "band_energy_db": bands,
        "reverb_estimate": reverb,
        "speech_segments": len(segments),
        "clues": clues,
        "confidence_note": confidence(
            "Reverb and microphone clues are estimated from decay behaviour and "
            "spectral tilt; they identify likely conditions, not a specific room, "
            "microphone or processing chain."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def _estimate_reverb(mono, samplerate, segments):
    if len(segments) < 2:
        return {"available": False,
                "note": "Not enough speech offsets to estimate decay."}
    decays = []
    for segment in segments[:20]:
        end = int(segment["end"] * samplerate)
        window = mono[end:end + int(0.4 * samplerate)]
        if window.size < samplerate // 20:
            continue
        envelope = np.abs(window)
        smooth = np.convolve(envelope, np.ones(64) / 64, mode="same")
        peak = float(np.max(smooth)) or 1e-9
        threshold = peak / 10.0
        index = int(np.argmax(smooth < threshold))
        if index > 0:
            decays.append(index / samplerate)
    if not decays:
        return {"available": False, "note": "Decay could not be measured."}
    median = float(np.median(decays))
    return {"available": True,
            "decay_seconds_to_minus20db": round(median, 3),
            "reverb_likelihood": "high" if median > 0.12 else
                                 "medium" if median > 0.06 else "low"}


def cmd_attributes(args):
    data, samplerate, _ = load(args.input)
    mono = ENGINE.to_mono(data)
    pitch = ENGINE.estimate_pitch(mono, samplerate)
    segments = ENGINE.voice_activity(mono, samplerate, min_seconds=0.08)
    speech = sum(s["duration"] for s in segments)
    syllables_estimate = speech * args.syllables_per_second
    words_estimate = syllables_estimate / 1.55
    RT.emit({
        "status": "ok", "command": "attributes", "input": args.input,
        "pitch": {k: v for k, v in pitch.items() if k != "f0_contour"},
        "pace_estimate": {
            "speech_seconds": round(speech, 3),
            "estimated_syllables": int(round(syllables_estimate)),
            "estimated_words": int(round(words_estimate)),
            "estimated_wpm": round(words_estimate / max(speech, 1e-6) * 60.0, 1),
        },
        "timbre": {
            "spectral_centroid_hz": ENGINE.spectral_centroid(mono, samplerate),
            "zero_crossing_rate": ENGINE.zero_crossing_rate(mono),
            "spectral_flatness": ENGINE.spectral_flatness(mono),
            "band_energy_db": ENGINE.band_energy(mono, samplerate, BANDS),
        },
        "energy": {"rms_dbfs": round(ENGINE.rms_dbfs(mono), 2),
                   "crest_factor_db": round(ENGINE.peak_dbfs(mono) -
                                            ENGINE.rms_dbfs(mono), 2)},
        "accent_markers": {
            "available": False,
            "note": "Accent and language markers are not inferred: this build does "
                    "not ship a trained accent classifier, and guessing would be "
                    "unreliable and inappropriate.",
        },
        "confidence_note": confidence(
            "Pitch and timbre values are measured. Pace is estimated from speech "
            "duration and an assumed syllable rate; use a transcript for exact WPM."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_suitability(args):
    data, samplerate, info = load(args.input)
    mono = ENGINE.to_mono(data)
    duration = mono.size / samplerate
    noise = ENGINE.noise_floor_dbfs(mono, samplerate)
    clipping = ENGINE.clipping_report(mono)
    speech = ENGINE.voice_activity(mono, samplerate)
    speech_seconds = sum(s["duration"] for s in speech)
    pitch = ENGINE.estimate_pitch(mono, samplerate)
    checks = []

    def check(name, passed, detail, weight=1):
        checks.append({"check": name, "pass": bool(passed), "detail": detail,
                       "weight": weight})

    check("duration", duration >= args.min_seconds,
          f"{duration:.2f}s of audio (minimum {args.min_seconds}s).", 2)
    check("speech_coverage", speech_seconds / max(duration, 1e-6) >= 0.35,
          f"{speech_seconds / max(duration, 1e-6):.0%} of the file is speech.", 2)
    check("noise_floor", noise <= -45.0,
          f"Noise floor {noise:.1f} dBFS (target at or below -45).", 2)
    check("clipping", clipping["clipped_samples"] == 0,
          f"{clipping['clipped_samples']} clipped samples.", 3)
    check("peak_headroom", ENGINE.peak_dbfs(mono) <= -1.0,
          f"Peak {ENGINE.peak_dbfs(mono):.1f} dBFS (target at or below -1).", 1)
    check("pitch_measurable", pitch.get("median_hz") is not None,
          f"Median pitch {pitch.get('median_hz')} Hz.", 1)
    total_weight = sum(c["weight"] for c in checks)
    earned = sum(c["weight"] for c in checks if c["pass"])
    score = round(earned / total_weight * 100.0, 1)
    verdict = ("suitable" if score >= 85 else
               "suitable-with-repair" if score >= 60 else "unsuitable")
    RT.emit({
        "status": "ok", "command": "suitability", "input": args.input,
        "purpose": args.purpose,
        "score": score, "verdict": verdict, "checks": checks,
        "recommended_steps": [c["check"] for c in checks if not c["pass"]],
        "rights_note": "Technical suitability only. A recognizable real person still "
                       "requires a consent record: run tools-voice-profile.py "
                       "check-use before any conversion.",
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_compare(args):
    candidate = compute_metrics(args.input, ["duration", "level", "loudness", "pitch",
                                             "energy", "centroid", "noise"])
    reference = compute_metrics(args.reference, ["duration", "level", "loudness", "pitch",
                                                 "energy", "centroid", "noise"])
    diffs = []
    pairs = [
        ("duration_seconds", candidate["duration"]["seconds"],
         reference["duration"]["seconds"], 0.15, "relative"),
        ("integrated_lufs", candidate["loudness"]["integrated_lufs"],
         reference["loudness"]["integrated_lufs"], 1.0, "absolute"),
        ("true_peak_dbtp", candidate["level"]["true_peak_dbtp"],
         reference["level"]["true_peak_dbtp"], 1.0, "absolute"),
        ("pitch_median_hz", candidate["pitch"].get("median_hz"),
         reference["pitch"].get("median_hz"), 0.08, "relative"),
        ("energy_mean_db", candidate["energy"]["mean_db"],
         reference["energy"]["mean_db"], 3.0, "absolute"),
        ("spectral_centroid_hz", candidate["spectral_centroid_hz"],
         reference["spectral_centroid_hz"], 0.25, "relative"),
        ("noise_floor_dbfs", candidate["noise"]["noise_floor_dbfs"],
         reference["noise"]["noise_floor_dbfs"], 6.0, "absolute"),
    ]
    for name, left, right, tolerance, mode in pairs:
        if left is None or right is None:
            diffs.append({"metric": name, "candidate": left, "reference": right,
                          "pass": None, "note": "metric unavailable"})
            continue
        if mode == "relative":
            delta = abs(left - right) / max(abs(right), 1e-6)
            passed = delta <= tolerance
        else:
            delta = abs(left - right)
            passed = delta <= tolerance
        diffs.append({"metric": name, "candidate": left, "reference": right,
                      "delta": round(delta, 4), "tolerance": tolerance,
                      "mode": mode, "pass": passed})
    evaluated = [d for d in diffs if d["pass"] is not None]
    passed = all(d["pass"] for d in evaluated)
    RT.emit({
        "status": "ok" if passed else "mismatch",
        "command": "compare", "candidate": args.input, "reference": args.reference,
        "comparisons": diffs,
        "passed": passed,
        "failed_metrics": [d["metric"] for d in evaluated if not d["pass"]],
        "golden_sample_verdict": "matches reference" if passed
        else "differs from reference",
        "confidence_note": confidence(
            "Comparisons use measured values with fixed tolerances. Perceptual "
            "quality still requires a listening test (see asset-checklists.json)."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_diagnostic(args):
    data, samplerate, info = load(args.input)
    mono = ENGINE.to_mono(data)
    findings = []
    noise = ENGINE.noise_floor_dbfs(mono, samplerate)
    clipping = ENGINE.clipping_report(mono)
    clicks = ENGINE.click_report(mono, samplerate)
    centroid = ENGINE.spectral_centroid(mono, samplerate)
    bands = ENGINE.band_energy(mono, samplerate, BANDS)
    loudness = ENGINE.integrated_loudness(data, samplerate)
    silence = ENGINE.silence_regions(mono, samplerate)

    if clipping["clipped_samples"] > 0:
        findings.append({
            "issue": "clipping", "severity": "error",
            "cause": "Input exceeded full scale during recording or rendering.",
            "smallest_repair": "Re-render with a lower target loudness; clipping "
                               "cannot be undone.",
            "command": "python3 tools-audio-synthesis.py render --text '<script>' "
                       "--target-lufs -18"})
    if clicks["click_count"] > 3:
        findings.append({
            "issue": "clicks_and_pops", "severity": "warning",
            "cause": "Impulsive noise from edits, mouth noise or digital dropouts.",
            "smallest_repair": "Declick pass, then listen for residual transients.",
            "command": f"python3 tools-audio-delivery.py repair --input {args.input} "
                       "--declick --out-dir out"})
    if noise > -45:
        findings.append({
            "issue": "high_noise_floor", "severity": "warning",
            "cause": f"Measured noise floor {noise:.1f} dBFS.",
            "smallest_repair": "Spectral denoise with a modest reduction.",
            "command": f"python3 tools-audio-delivery.py repair --input {args.input} "
                       "--denoise-db 10 --out-dir out"})
    if bands.get("low_mid", 0) > bands.get("mid", -99) + 3:
        findings.append({
            "issue": "low_mid_buildup", "severity": "info",
            "cause": "Warmth or boxiness between 250-800 Hz.",
            "smallest_repair": "Cut 2-3 dB around 300 Hz.",
            "command": f"python3 tools-audio-delivery.py master --input {args.input} "
                       "--bands 300:-2.5 --out-dir out"})
    if centroid and centroid < 900 and samplerate >= 44100:
        findings.append({
            "issue": "dull_tone", "severity": "info",
            "cause": f"Spectral centroid {centroid:.0f} Hz is low for speech.",
            "smallest_repair": "Add presence lift around 3-5 kHz.",
            "command": f"python3 tools-audio-delivery.py master --input {args.input} "
                       "--bands 3500:2 --out-dir out"})
    if abs(loudness + 16.0) > 3.0:
        findings.append({
            "issue": "loudness_off_target", "severity": "warning",
            "cause": f"Integrated loudness {loudness:.1f} LUFS.",
            "smallest_repair": "Normalise to the delivery target.",
            "command": f"python3 tools-audio-delivery.py master --input {args.input} "
                       "--target-lufs -16 --out-dir out"})
    longest = max((s["duration"] for s in silence), default=0.0)
    if longest > 2.0 and args.purpose == "podcast":
        findings.append({
            "issue": "long_silence", "severity": "info",
            "cause": f"Longest silent region {longest:.2f}s.",
            "smallest_repair": "Trim dead air between phrases.",
            "command": f"python3 tools-audio-delivery.py master --input {args.input} "
                       "--trim-silence --out-dir out"})
    if not findings:
        findings.append({"issue": "none", "severity": "info",
                         "cause": "No problems detected by the measured checks.",
                         "smallest_repair": "None required.",
                         "command": None})
    RT.emit({
        "status": "ok", "command": "diagnostic", "input": args.input,
        "purpose": args.purpose,
        "measurements": {"integrated_lufs": round(loudness, 2),
                         "noise_floor_dbfs": round(noise, 2),
                         "clicks": clicks["click_count"],
                         "clipped_samples": clipping["clipped_samples"],
                         "spectral_centroid_hz": centroid,
                         "longest_silence_seconds": round(longest, 2),
                         "samplerate": samplerate, "format": info.format},
        "findings": findings,
        "confidence_note": confidence(
            "Each finding is derived from the measurement listed beside it. Fixes are "
            "suggestions ordered by invasiveness; verify by re-measuring after each "
            "change."),
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_capabilities(args):
    RT.emit(RT.capabilities_report(
        PROG, CAPABILITIES,
        notes={"role": "measurement, detection and diagnostics",
               "metric_ops": OPS,
               "limits": _policy(args.assets_dir)["analysis_limits"]}),
        output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def build_parser():
    parser = RT.build_parser(
        PROG,
        "Voice Capability Toolkit - measure and diagnose speech audio with "
        "explicit confidence limits.",
        epilog="Examples:\n"
               "  python3 tools-audio-analysis.py metrics --input take.wav "
               "--ops loudness,clipping,pitch\n"
               "  python3 tools-audio-analysis.py diagnostic --input take.wav\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("metrics", help="Compute measurements.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--ops", help="Comma-separated subset of: " + ",".join(OPS))
    p.add_argument("--silence-threshold", type=float, default=-45.0)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("vad", help="Detect speech spans.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--threshold", type=float, default=-40.0)
    p.add_argument("--min-seconds", type=float, default=0.12)
    p.add_argument("--merge-gap", type=float, default=0.12)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_vad)

    p = sub.add_parser("diarize", help="Heuristic speaker turns.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--speakers", type=int, default=2)
    p.add_argument("--threshold", type=float, default=-40.0)
    p.add_argument("--seed", type=int, default=5)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_diarize)

    p = sub.add_parser("alignment", help="Align script sentences to speech spans.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--text", help="Script text.")
    p.add_argument("--input-text", help="Script file.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_alignment)

    p = sub.add_parser("artifacts", help="Detect artefacts.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--click-threshold", type=float, default=6.0)
    p.add_argument("--max-clicks", type=int, default=3)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_artifacts)

    p = sub.add_parser("environment", help="Estimate acoustics and processing clues.")
    p.add_argument("-i", "--input", required=True)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_environment)

    p = sub.add_parser("attributes", help="Estimate vocal attributes.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--syllables-per-second", type=float, default=4.0)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_attributes)

    p = sub.add_parser("suitability", help="Score source suitability.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--purpose", default="conversion",
                   choices=["conversion", "voice-design", "dubbing", "training"])
    p.add_argument("--min-seconds", type=float, default=10.0)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_suitability)

    p = sub.add_parser("compare", help="Compare a render with a golden reference.")
    p.add_argument("-i", "--input", required=True, help="Candidate file.")
    p.add_argument("--reference", required=True, help="Golden reference file.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("diagnostic", help="Diagnose quality problems.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--purpose", default="general",
                   choices=["general", "podcast", "audiobook", "telephony",
                            "game", "advertising"])
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_diagnostic)

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
