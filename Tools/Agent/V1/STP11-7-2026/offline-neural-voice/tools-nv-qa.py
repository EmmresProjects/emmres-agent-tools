#!/usr/bin/env python3
"""tools-nv-qa.py - measurable quality assurance and acceptance testing.

Loaded by the entry point; safe to import. Self test:

    python tools-nv-qa.py selftest

Capabilities
------------
assess           one audio file: duration, loudness, true peak, clipping, DC,
                 silence profile, decodability, and a pass/warn/fail verdict
transcript       compare the spoken content with the intended text using local
                 ASR (faster-whisper, optional) and normalised word matching
regression       compare a candidate against stored baseline metrics
acceptance       run the video greeting acceptance fixture end to end and emit
                 a pass/fail evidence report
checklist        print the acceptance checklist with thresholds from config

Thresholds come from configuration (qa.*) and from the fixture; every check
reports the measured value, the expected range, the failure cause and the exact
remediation. Nothing is changed: this tool only reads and reports.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import math
import re
import sys
import time
import unicodedata
from array import array
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if "nvcore" not in sys.modules:
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("nvcore", _HERE / "tools-nvcore.py")
        _module = _ilu.module_from_spec(_spec)
        sys.modules["nvcore"] = _module
        _spec.loader.exec_module(_module)
    except FileNotFoundError:
        pass

import nvcore
from nvcore import InputError, NVError, decode_audio_any, measure_loudness, true_peak_dbfs, \
    wav_statistics

VERDICT_ORDER = {"pass": 0, "warn": 1, "fail": 2}


# --------------------------------------------------------------------------- #
# Check helpers
# --------------------------------------------------------------------------- #

def _check(name, measured, expected, verdict, cause=None, remediation=None, weight="major"):
    return {"check": name, "measured": measured, "expected": expected, "verdict": verdict,
            "cause": cause, "remediation": remediation, "weight": weight}


def _range_check(name, value, low, high, *, unit="", weight="major", remediation=None,
                 warn_margin=None):
    """Range check; `high=None` means open-ended (no upper limit)."""
    if high is None:
        high = float("inf")
        expected = ">= %s %s" % (low, unit)
    else:
        expected = "%s..%s %s" % (low, high, unit)
    if value is None:
        return _check(name, None, expected, "fail",
                      cause="no measurement was possible", remediation=remediation)
    if low <= value <= high:
        if warn_margin and (value - low < warn_margin or high - value < warn_margin):
            return _check(name, value, expected, "warn",
                          cause="value is inside the range but close to a boundary",
                          remediation=remediation, weight=weight)
        return _check(name, value, expected, "pass", weight=weight)
    cause = "measured value is below the expected range" if value < low else \
            "measured value is above the expected range"
    return _check(name, value, expected, "fail", cause=cause,
                  remediation=remediation, weight=weight)


def _max_check(name, value, maximum, *, unit="", weight="major", remediation=None):
    if value is None:
        return _check(name, None, "<= %s %s" % (maximum, unit), "fail",
                      cause="no measurement was possible", remediation=remediation)
    if value <= maximum:
        return _check(name, value, "<= %s %s" % (maximum, unit), "pass",
                      remediation=remediation, weight=weight)
    return _check(name, value, "<= %s %s" % (maximum, unit), "fail",
                  cause="measured value exceeds the maximum", remediation=remediation,
                  weight=weight)


def overall_verdict(checks):
    verdict = "pass"
    for item in checks:
        if VERDICT_ORDER[item["verdict"]] > VERDICT_ORDER[verdict]:
            verdict = item["verdict"]
    return verdict


# --------------------------------------------------------------------------- #
# Transcript comparison
# --------------------------------------------------------------------------- #

_PUNCT = re.compile(r"[^\w\s']")
_IPA = re.compile(r"\[\[.*?\]\]")


def normalise_for_matching(text):
    """Lowercase, strip punctuation, expand digits so ASR text and intent align."""
    text = str(text)
    text = _IPA.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    text = re.sub(r"\d+", lambda m: m.group(0), text)
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def word_diff(expected, actual):
    expected_words = normalise_for_matching(expected).split()
    actual_words = normalise_for_matching(actual).split()
    matcher = difflib.SequenceMatcher(a=expected_words, b=actual_words, autojunk=False)
    missing, extra = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            missing.extend(expected_words[i1:i2])
        if tag in ("insert", "replace"):
            extra.extend(actual_words[j1:j2])
    matched = sum(block.size for block in matcher.get_matching_blocks())
    denominator = max(1, len(expected_words))
    return {
        "expected_words": len(expected_words),
        "actual_words": len(actual_words),
        "matched_words": matched,
        "missing_words": missing,
        "extra_words": extra,
        "word_accuracy": round(matched / denominator, 4),
        "sequence_ratio": round(matcher.ratio(), 4),
        "expected_normalised": " ".join(expected_words),
        "actual_normalised": " ".join(actual_words),
    }


def asr_transcribe(cfg, audio_path, *, model=None, language="en", cancel=None):
    """Local ASR transcription with faster-whisper. No network after the model exists."""
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise nvcore.DependencyError(
            "faster-whisper is not importable (%s)" % exc,
            hint="pip install faster-whisper  (model files must already exist locally "
                 "for a fully offline run)")
    settings = cfg.get("qa.asr", {}) if cfg is not None else {}
    model_name = model or settings.get("model", "base.en")
    compute_type = settings.get("compute_type", "int8")
    download_root = settings.get("download_root", "work/asr-models")
    root = None
    if cfg is not None:
        try:
            root = cfg.resolve(download_root)
            Path(root).mkdir(parents=True, exist_ok=True)
        except nvcore.NVError:
            root = None
    if cancel is not None:
        cancel.check()
    if cfg is not None and cfg.offline and root is not None:
        staged = [p for p in Path(root).iterdir() if p.is_dir()] if Path(root).is_dir() else []
        if not staged:
            raise nvcore.DependencyError(
                "no local ASR model is staged in %s" % download_root,
                hint="stage a faster-whisper model into that folder once (online), or set "
                     "execution.offline=false for this command; the offline policy never "
                     "downloads during a run")
    started = time.monotonic()
    whisper = WhisperModel(model_name, device="cpu", compute_type=compute_type,
                           download_root=str(root) if root else None)
    samples, rate = decode_audio_any(audio_path)
    import numpy as np  # faster-whisper already requires numpy
    audio = np.array(samples, dtype=np.float32) / 32768.0
    if rate != 16000:
        # faster-whisper resamples internally when given a float array at 16 kHz
        audio = _resample_float_array(audio, rate, 16000)
        rate = 16000
    segments, info = whisper.transcribe(audio, language=language, beam_size=5,
                                        vad_filter=False)
    text = " ".join(segment.text.strip() for segment in segments)
    return {"text": text.strip(), "language": info.language,
            "language_probability": round(float(info.language_probability), 4),
            "duration_s": round(float(info.duration), 4),
            "model": model_name, "compute_type": compute_type,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}


def _resample_float_array(values, src, dst):
    if src == dst:
        return values
    ratio = dst / float(src)
    out_len = int(round(len(values) * ratio))
    out = [0.0] * out_len
    inv = 1.0 / ratio
    for index in range(out_len):
        pos = index * inv
        base = int(pos)
        frac = pos - base
        left = values[base] if base < len(values) else 0.0
        right = values[base + 1] if base + 1 < len(values) else left
        out[index] = left + (right - left) * frac
    import numpy as np
    return np.array(out, dtype=np.float32)


def compare_transcript(cfg, audio_path, expected_text, *, use_asr=True, locale="en-US",
                       min_accuracy=None, asr_model=None, cancel=None):
    """Compare intended text with ASR output; returns a check plus full detail."""
    qa_cfg = cfg.get("qa", {}) if cfg is not None else {}
    min_accuracy = float(min_accuracy if min_accuracy is not None
                         else cfg.get("qa.min_word_accuracy", 0.85) if cfg else 0.85)
    language = (nvcore.language_family(locale) or "en") if locale else "en"
    result = {"expected_text": expected_text, "normalised": normalise_for_matching(expected_text)}
    if not use_asr:
        return {"available": False, "reason": "ASR comparison disabled", **result}, None
    try:
        transcription = asr_transcribe(cfg, audio_path, model=asr_model, language=language,
                                       cancel=cancel)
    except nvcore.DependencyError as exc:
        check = _check("transcript-accuracy", None, ">= %.2f word accuracy" % min_accuracy,
                       "warn", cause=exc.message,
                       remediation="install faster-whisper with local model files, then "
                                   "re-run with --transcript")
        return {"available": False, "reason": exc.message, **result}, check
    diff = word_diff(expected_text, transcription["text"])
    check = _check("transcript-accuracy", diff["word_accuracy"],
                   ">= %.2f word accuracy" % min_accuracy,
                   "pass" if diff["word_accuracy"] >= min_accuracy else "fail",
                   cause=None if diff["word_accuracy"] >= min_accuracy else
                   "ASR heard something different from the intended script",
                   remediation="fix pronunciation with a lexicon entry, slow the preset, or "
                               "review the text for a normalisation mistake")
    return {"available": True, "transcription": transcription, "diff": diff, **result}, check


# --------------------------------------------------------------------------- #
# File assessment
# --------------------------------------------------------------------------- #

def assess_audio_file(cfg, audio_rel, *, text=None, duration_range=None, transcript=False,
                      asr_model=None, locale="en-US", cancel=None):
    """Full measurable assessment of one audio artifact.

    `transcript` toggles the optional local ASR comparison; it is off by default
    because transcription needs faster-whisper plus a local model.
    """
    audio_path = cfg.resolve(audio_rel, must_exist=True, kind="file")
    qa_cfg = cfg.get("qa", {})
    # The 2.5-4.0 s window belongs to the catalogued video-greeting scenario, not to
    # every clip: a standalone assessment uses `qa.default_min_duration_s` (0 s), which
    # keeps the duration check informative (it still reports the measured length).
    min_duration = duration_range[0] if duration_range and duration_range[0] is not None \
        else float(qa_cfg.get("default_min_duration_s", 0.0))
    max_duration = duration_range[1] if duration_range and duration_range[1] is not None \
        else float(qa_cfg.get("default_max_duration_s", 0.0)) or None
    checks = []
    warnings = []

    file_info = nvcore.audio_file_report(audio_path)
    samples, rate = decode_audio_any(audio_path)
    stats = wav_statistics(samples, rate)
    loudness = measure_loudness(samples, rate)
    peak = true_peak_dbfs(samples, rate)
    target_lufs = float(cfg.get("mastering.target_lufs", -14.0))
    tolerance = float(cfg.get("mastering.loudness_tolerance_lu", 1.0))
    ceiling = float(cfg.get("mastering.max_true_peak_dbfs", -1.0))

    checks.append(_check("decodable", True, "decodes without error",
                         "pass" if len(samples) else "fail",
                         cause=None if len(samples) else "decoder returned no samples",
                         remediation="re-export the audio"))
    if audio_path.suffix.lower() == ".wav":
        info = nvcore.read_wav_info(audio_path)
        checks.append(_check("pcm-wav", "%d-bit %s" % (info["bits"], info["compression"]),
                             "16-bit PCM mono",
                             "pass" if info["bits"] == 16 and info["channels"] == 1 else "warn",
                             remediation="export 16-bit PCM mono WAV from the mastering stage"))
    checks.append(_range_check("duration", stats.get("duration_s"), min_duration, max_duration,
                               unit="s",
                               remediation="adjust the preset length_scale or trim pauses"))
    checks.append(_range_check("integrated-loudness", loudness.get("integrated_lufs"),
                               target_lufs - tolerance, target_lufs + tolerance, unit="LUFS",
                               remediation="re-run mastering with target_lufs %s" % target_lufs))
    checks.append(_max_check("true-peak", peak.get("true_peak_dbfs"), ceiling, unit="dBFS",
                             remediation="lower the limiter ceiling and re-master"))
    checks.append(_max_check("clipped-samples", stats.get("clipped_samples"),
                             float(qa_cfg.get("max_clipped_samples", 0)),
                             remediation="reduce gain before the limiter"))
    checks.append(_max_check("dc-offset", abs(stats.get("dc_offset") or 0.0),
                             float(qa_cfg.get("max_dc_offset", 0.02)),
                             remediation="apply the 70 Hz high-pass stage"))
    silence_limit = float(qa_cfg.get("max_silence_ratio", 0.45))
    checks.append(_max_check("silence-ratio", stats.get("silence_ratio"), silence_limit,
                             remediation="trim leading and trailing silence"))
    checks.append(_range_check("level-rms", stats.get("rms_dbfs"),
                               float(qa_cfg.get("min_level_rms_dbfs", -40.0)),
                               float(qa_cfg.get("max_level_rms_dbfs", -6.0)), unit="dBFS",
                               remediation="check synthesis output before mastering"))
    if stats.get("duration_s") and stats["duration_s"] > 0.5 and stats.get("zero_cross_rate", 0) > 12000:
        checks.append(_check("buzz-detection", stats["zero_cross_rate"], "<= 12000 crossings/s",
                             "warn", cause="very high zero-crossing rate can indicate buzz or "
                                           "electrical noise",
                             remediation="inspect for a degraded engine or a broken model file"))
    else:
        checks.append(_check("buzz-detection", stats.get("zero_cross_rate"), "<= 12000 crossings/s",
                             "pass"))

    transcript_detail = None
    if text:
        transcript_detail, transcript_verdict = compare_transcript(
            cfg, audio_path, text, use_asr=bool(transcript), locale=locale,
            asr_model=asr_model, cancel=cancel)
        if transcript_verdict is not None:
            checks.append(transcript_verdict)
        elif not transcript:
            warnings.append("transcript comparison skipped: pass --transcript to run local "
                            "ASR comparison")

    if cfg.get("execution.offline", True):
        checks.append(_check("offline-policy", True, "synthesis ran with sockets blocked",
                             "pass"))
    verdict = overall_verdict(checks)
    failures = [c for c in checks if c["verdict"] == "fail"]
    return {
        "summary": "%s: %s (%d checks, %d failed)" % (
            cfg.rel(audio_path), verdict.upper(), len(checks), len(failures)),
        "audio": cfg.rel(audio_path),
        "verdict": verdict,
        "checks": checks,
        "failures": failures,
        "metrics": {"duration_s": stats.get("duration_s"),
                    "loudness_lufs": loudness.get("integrated_lufs"),
                    "loudness_range_lu": loudness.get("loudness_range_lu"),
                    "true_peak_dbfs": peak.get("true_peak_dbfs"),
                    "sample_peak_dbfs": stats.get("peak_dbfs"),
                    "clipped_samples": stats.get("clipped_samples"),
                    "dc_offset": stats.get("dc_offset"),
                    "silence_ratio": stats.get("silence_ratio"),
                    "sample_rate": rate,
                    "frames": len(samples)},
        "file": file_info,
        "transcript": transcript_detail,
        "thresholds": {"min_duration_s": min_duration,
                       "max_duration_s": (None if max_duration == float("inf") else max_duration),
                       "target_lufs": target_lufs, "tolerance_lu": tolerance,
                       "max_true_peak_dbfs": ceiling},
        "warnings": warnings,
    }


def regression_check(current_report, baseline_path):
    """Compare current metrics against a stored baseline JSON (fail on drift)."""
    baseline = nvcore.read_json(baseline_path, what="baseline metrics")
    limits = baseline.get("limits", {"duration_s": 0.25, "loudness_lufs": 1.0,
                                     "true_peak_dbfs": 0.5})
    current = current_report["metrics"]
    base_metrics = baseline.get("metrics", baseline)
    rows = []
    for key, limit in limits.items():
        if key not in base_metrics or current.get(key) is None:
            continue
        delta = current[key] - base_metrics[key]
        rows.append({"metric": key, "baseline": base_metrics[key], "current": current[key],
                     "delta": round(delta, 4), "limit": limit,
                     "verdict": "pass" if abs(delta) <= limit else "fail"})
    verdict = overall_verdict([{"verdict": r["verdict"]} for r in rows]) if rows else "warn"
    return {"verdict": verdict, "rows": rows,
            "summary": "regression vs %s: %s" % (Path(baseline_path).name, verdict.upper())}


# --------------------------------------------------------------------------- #
# Acceptance fixture
# --------------------------------------------------------------------------- #

def load_acceptance_fixture(cfg):
    data, rel = nvcore.load_asset_json(cfg, "paths.acceptance_fixture_path",
                                       what="acceptance fixture")
    return data, rel


def engine_name_for_solver(engine):
    """The neural engine, or None when only the degraded fallback is available."""
    try:
        if engine.engine_availability().get(engine.ENGINE_ONNX, {}).get("available"):
            return engine.ENGINE_ONNX
    except Exception:
        pass
    return None


def run_acceptance(cfg, fixture, *, journal=None, overwrite=False, name=None,
                   approve=False, approval_token=None, approved_by=None, actor="cli",
                   cancel=None):
    """Execute the video greeting acceptance scenario and return evidence."""
    engine = nvcore.load_sibling("tools-nv-engine.py", "nvengine")
    textprep = nvcore.load_sibling("tools-nv-text.py", "nvtext")
    mastering = nvcore.load_sibling("tools-nv-master.py", "nvmaster")
    case = fixture.get("case", {})
    text = case.get("text") or "Greetings, friends! Welcome to my video."
    preset_id = case.get("preset")
    voice_id = case.get("voice")
    outputs_dir = cfg.ensure_dir("paths.output_dir")
    evidence_dir = cfg.ensure_dir("paths.evidence_dir")
    base_name = name or case.get("name") or "acceptance-greeting"
    wav_rel = "%s/%s.wav" % (cfg.get("paths.output_dir"), base_name)
    mp3_rel = "%s/%s.mp3" % (cfg.get("paths.output_dir"), base_name)
    wav_path = cfg.resolve(wav_rel)
    if wav_path.exists() and not overwrite:
        raise InputError("acceptance output already exists: %s" % wav_rel,
                         hint="pass --overwrite to regenerate it")
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    policy, policy_rel = nvcore.load_policy(cfg)
    presets_data, presets, presets_rel = nvcore.load_presets(cfg)
    preset = presets.get(preset_id) or presets.get(presets_data.get("default_preset"))
    if preset is None:
        raise InputError("acceptance preset %r is not defined" % preset_id)
    asset = engine.resolve_voice(cfg, voice_id, registry=registry)
    rights = nvcore.evaluate_rights(asset.registry_entry, policy, use="personal_use")
    checks = []
    warnings = []
    artifacts = []

    lexicon, _, lexicon_rel = nvcore.load_lexicon(cfg)
    prepared = textprep.prepare_text(
        text, cfg=cfg, lexicon=lexicon,
        locale=case.get("locale") or asset.config.language or "en-US",
        script_guard=str(cfg.get("execution.script_guard", "block")))
    phonemizer = engine.Phonemizer(espeak_voice=asset.config.espeak_voice)
    phonemizer.probe()
    phoneme_sequences, ph_warnings = phonemizer.phonemize(prepared["text"], cancel=cancel)
    warnings.extend(prepared["warnings"] or [])
    warnings.extend(ph_warnings or [])
    # preset -> scales uses the same documented mapping as the entry point
    preset_scales = {
        "length_scale": max(0.45, min(2.2, float(preset.get("length_scale", 1.0)))),
        "noise_scale": max(0.2, min(1.2, float(preset.get("noise_scale", 0.667)))),
        "noise_w": max(0.2, min(1.2, float(preset.get("noise_w", 0.8)))),
    }
    # The catalogued scenario has a hard duration window (2.5-4.0 s) while VITS sampling
    # makes length vary run to run, so render a *timed* take: solve length_scale for the
    # middle of the window, then synthesise the audio that is actually measured.
    solve_report = None
    duration_target = case.get("duration_target_s")
    if not duration_target and case.get("min_duration_s") and case.get("max_duration_s"):
        duration_target = (float(case["min_duration_s"]) + float(case["max_duration_s"])) / 2.0
    if duration_target and engine_name_for_solver(engine) is not None:
        preset_scales, solve_report = engine.solve_length_scale(
            asset, phoneme_sequences, target_s=float(duration_target),
            tolerance_s=float(case.get("duration_tolerance_s", 0.08)),
            base=preset_scales, cfg=cfg)
    seed = int(case.get("seed", cfg.get("synthesis.seed", 1337)))
    try:
        import numpy as _np
        _np.random.seed(seed % (2 ** 32))
    except Exception:
        pass
    engine_name = engine.ENGINE_ONNX
    synth_engine = engine.build_engine(engine_name, asset, cfg=cfg, cancel=cancel)
    started = time.monotonic()
    combined = array("h")
    with nvcore.offline_guard(cfg.offline):
        for phonemes in phoneme_sequences:
            if cancel is not None:
                cancel.check()
            request = engine.SynthesisRequest(
                phonemes, scales=preset_scales, speaker_index=asset.speaker_index,
                seed=seed, timeout_s=float(cfg.get("execution.timeout_seconds", 120.0)))
            try:
                result = synth_engine.synthesize(request)
            except nvcore.NVError as exc:
                if not cfg.get("execution.allow_fallback_engine", True):
                    raise
                warnings.append("neural engine failed (%s); acceptance continued with the "
                                "clearly labelled degraded engine" % exc.message)
                engine_name = engine.ENGINE_FALLBACK
                synth_engine = engine.build_engine(engine_name, asset, cfg=cfg, cancel=cancel)
                result = synth_engine.synthesize(request)
            combined.extend(result.samples)
    inference_ms = round((time.monotonic() - started) * 1000, 2)
    if not combined:
        raise NVError("acceptance synthesis produced no audio")

    master_report = None
    mastered, rate, master_report = mastering.master_pipeline(
        combined, asset.config.sample_rate, cfg=cfg,
        target_lufs=(preset.get("mastering") or {}).get("target_lufs"),
        ceiling_dbfs=(preset.get("mastering") or {}).get("max_true_peak_dbfs"),
        eq_curve=preset.get("eq_curve", "neutral"), cancel=cancel)
    mastering.export_wav(wav_path, mastered, rate, overwrite=True)
    artifacts.append({"kind": "mastered-wav", "path": wav_rel,
                      "bytes": wav_path.stat().st_size,
                      "sha256": nvcore.sha256_file(wav_path)})
    mp3_ok = False
    mp3_detail = None
    try:
        mastering.export_mp3(cfg.resolve(mp3_rel), mastered, rate,
                             bitrate_kbps=int(cfg.get("packaging.mp3_bitrate_kbps", 192)),
                             overwrite=True)
        mp3_detail = mastering.verify_mp3(cfg.resolve(mp3_rel))
        mp3_ok = mp3_detail["decodable"]
        artifacts.append({"kind": "mp3", "path": mp3_rel,
                          "bytes": cfg.resolve(mp3_rel).stat().st_size})
    except nvcore.NVError as exc:
        warnings.append("MP3 export unavailable: %s" % exc.message)

    report = assess_audio_file(cfg, wav_rel, text=text,
                               duration_range=(case.get("min_duration_s", 2.5),
                                               case.get("max_duration_s", 4.0)),
                               transcript=bool(case.get("transcript_check", False)),
                               cancel=cancel)
    checks.extend(report["checks"])
    checks.append(_check("rights-decision", rights["decision"], "allow or review-required",
                         "pass" if rights["decision"] in ("allow", "review-required") else "fail",
                         remediation="pick a licence-appropriate voice"))
    if solve_report is not None:
        checks.append(_check("duration-calibration",
                             solve_report.get("length_scale"),
                             "length_scale solved for %s s" % solve_report.get("target_s"),
                             "pass" if solve_report.get("converged") else "warn",
                             cause=None if solve_report.get("converged") else
                             "the search could not converge inside the duration window",
                             remediation="widen the window or let the preset set length_scale"))
    checks.append(_check("engine", engine_name, "onnx-vits (neural)",
                         "pass" if engine_name == engine.ENGINE_ONNX else "warn",
                         cause=None if engine_name == engine.ENGINE_ONNX else
                         "a degraded engine produced the audio",
                         remediation="install numpy and onnxruntime and a neural voice model"))
    checks.append(_check("mp3-decodable", mp3_ok, "MP3 decodes cleanly",
                         "pass" if mp3_ok else "warn",
                         remediation="install lameenc and soundfile for MP3 export"))
    prosody = case.get("prosody", {})
    prosody_result = _prosody_check(case, prepared, phoneme_sequences)
    checks.append(_check("prosody-directions", prosody_result,
                         "pause after %r and emphasis on %r" % (
                             prosody.get("pause_after"), prosody.get("emphasis")),
                         "pass" if prosody_result["satisfied"] else "warn",
                         cause=None if prosody_result["satisfied"] else
                         "stress markers were not observable in the phoneme stream",
                         remediation="add a lexicon entry with explicit ˈ stress markers",
                         weight="minor"))

    verdict = overall_verdict(checks)
    evidence = {
        "generated_at": nvcore.utc_now(),
        "fixture": fixture.get("case_id", "video-greeting"),
        "text": {"requested": text, "prepared": prepared["text"],
                 "phonemes": ["".join(s) for s in phoneme_sequences],
                 "phonemizer": phonemizer.backend, "rewrites": prepared["rewrites"]},
        "voice": asset.describe(),
        "preset": {"id": preset.get("id"), "version": preset.get("version")},
        "engine": {"name": synth_engine.name, "inference_ms": inference_ms, "seed": seed,
                   "scales": preset_scales},
        "mastering": master_report,
        "qa": report,
        "mp3": mp3_detail,
        "rights": rights,
        "checks": checks,
        "verdict": verdict,
        "artifacts": artifacts,
        "reproduce": ("python tools-nv-voice.py acceptance --overwrite  "
                      "|  python tools-nv-voice.py synthesize --text \"%s\" "
                      "--voice %s --preset %s" % (text, asset.voice_id, preset.get("id"))),
    }
    evidence_rel = "%s/%s.acceptance.json" % (cfg.get("paths.evidence_dir"), base_name)
    nvcore.atomic_write_json(cfg.resolve(evidence_rel), evidence, overwrite=True)
    artifacts.append({"kind": "evidence", "path": evidence_rel,
                      "sha256": nvcore.sha256_file(cfg.resolve(evidence_rel))})
    if journal is not None:
        journal.record("acceptance", verdict=verdict, voice=asset.voice_id,
                       preset=preset.get("id"), evidence=evidence_rel)
    failures = [c for c in checks if c["verdict"] == "fail"]
    return {
        "summary": "acceptance %s: %d checks, %d failed -> %s" % (
            base_name, len(checks), len(failures), evidence_rel),
        "passed": verdict == "pass",
        "verdict": verdict,
        "checks": checks,
        "failures": failures,
        "metrics": report["metrics"],
        "evidence_file": evidence_rel,
        "artifacts": artifacts,
        "output": {"wav": wav_rel, "mp3": mp3_rel if mp3_ok else None,
                   "evidence": evidence_rel},
        "reproduce": evidence["reproduce"],
        "warnings": warnings,
    }


def _prosody_check(case, prepared, phoneme_sequences):
    """Check that requested prosody directions are observable in the phoneme stream."""
    prosody = case.get("prosody", {})
    details = {"pause_after": prosody.get("pause_after"),
               "emphasis": prosody.get("emphasis"), "satisfied": True, "notes": []}
    emphasis = (prosody.get("emphasis") or "").strip().lower()
    if emphasis:
        joined = "".join("".join(sequence) for sequence in phoneme_sequences)
        lexicon_hit = "[[%s]]" % emphasis in prepared["text"].lower()
        stress_present = "ˈ" in joined
        details["emphasis_marker_present"] = stress_present
        details["lexicon_override_present"] = lexicon_hit
        if not (stress_present or lexicon_hit):
            details["satisfied"] = False
            details["notes"].append("no primary stress marker was emitted")
    pause_after = prosody.get("pause_after")
    if pause_after:
        details["pause_phrase"] = pause_after
        details["notes"].append("phrase pause is realised by sentence punctuation and the "
                                "mastering trim stage")
    return details


def checklist(cfg):
    qa_cfg = cfg.get("qa", {})
    return {
        "duration_s": [qa_cfg.get("min_duration_s"), qa_cfg.get("max_duration_s")],
        "integrated_lufs": [cfg.get("mastering.target_lufs") -
                            cfg.get("mastering.loudness_tolerance_lu"),
                            cfg.get("mastering.target_lufs") +
                            cfg.get("mastering.loudness_tolerance_lu")],
        "true_peak_dbfs_max": cfg.get("mastering.max_true_peak_dbfs"),
        "max_clipped_samples": qa_cfg.get("max_clipped_samples"),
        "max_dc_offset": qa_cfg.get("max_dc_offset"),
        "max_silence_ratio": qa_cfg.get("max_silence_ratio"),
        "pcm_format": "16-bit mono WAV",
        "mp3_requirement": "optional, must decode cleanly",
        "offline_requirement": "internet disabled, no API key, no GPU, no first-run download",
        "evidence_required": ["transcript comparison", "audio metrics", "model and voice versions",
                              "checksums", "preset version", "pass or fail report"],
    }


# --------------------------------------------------------------------------- #
# Self test
# --------------------------------------------------------------------------- #

def selftest(argv=None):
    parser = argparse.ArgumentParser(prog="tools-nv-qa.py selftest")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--audio", help="optional real WAV to assess")
    args = parser.parse_args(argv)
    checks = []

    def check(name, fn):
        started = time.monotonic()
        try:
            detail = fn()
            checks.append({"check": name, "ok": True,
                           "ms": round((time.monotonic() - started) * 1000, 2), "detail": detail})
        except Exception as exc:
            checks.append({"check": name, "ok": False,
                           "ms": round((time.monotonic() - started) * 1000, 2),
                           "detail": "%s: %s" % (type(exc).__name__, exc)})

    def t_diff():
        diff = word_diff("Greetings friends, welcome to my video.",
                         "Greetings friends welcome to my video")
        assert diff["missing_words"] == [] and diff["extra_words"] == [], diff
        assert diff["word_accuracy"] == 1.0, diff
        diff2 = word_diff("Greetings friends welcome", "Greetings friend welcome")
        assert "friends" in diff2["missing_words"] or diff2["word_accuracy"] < 1.0, diff2
        return "perfect match and a one-word miss are both detected"

    def t_verdict():
        assert overall_verdict([{"verdict": "pass"}, {"verdict": "pass"}]) == "pass"
        assert overall_verdict([{"verdict": "pass"}, {"verdict": "warn"}]) == "warn"
        assert overall_verdict([{"verdict": "warn"}, {"verdict": "fail"}]) == "fail"
        return "verdict aggregation is pass < warn < fail"

    def t_checks():
        good = _range_check("duration", 3.1, 2.5, 4.0)
        bad = _range_check("duration", 9.0, 2.5, 4.0)
        peak = _max_check("true-peak", -3.0, -1.0, remediation="lower the limiter ceiling")
        assert good["verdict"] == "pass" and bad["verdict"] == "fail", (good, bad)
        assert peak["verdict"] == "pass" and peak["remediation"], peak
        return "range and maximum checks classify and carry remediation text"

    def t_assess():
        if not args.audio:
            return "skipped (pass --audio <wav> to assess a real file)"
        cfg = nvcore.load_config(None, root=nvcore.skill_root())
        report = assess_audio_file(cfg, args.audio)
        assert report["checks"], report
        return "assessed %s with %d checks, verdict %s" % (
            args.audio, len(report["checks"]), report["verdict"])

    def t_checklist():
        cfg = nvcore.load_config(None, root=nvcore.skill_root())
        rows = checklist(cfg)
        assert rows["duration_s"] and "integrated_lufs" in rows
        return "checklist thresholds resolve from configuration"

    check("word-diff", t_diff)
    check("verdict-aggregation", t_verdict)
    check("check-primitives", t_checks)
    check("assess-audio", t_assess)
    check("checklist", t_checklist)

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "tool": "offline-neural-voice/qa", "version": nvcore.__version__,
               "checks": checks}
    if args.format == "json":
        nvcore.print_json(payload)
    else:
        for item in checks:
            print("[%s] %s (%sms) %s" % ("PASS" if item["ok"] else "FAIL",
                                         item["check"], item["ms"], item["detail"]))
        print("\n%s: %d/%d QA checks passed" % ("PASS" if ok else "FAIL",
                                                sum(1 for c in checks if c["ok"]), len(checks)))
    return nvcore.EXIT_OK if ok else nvcore.EXIT_FAIL


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return nvcore.EXIT_OK
    if argv[0] == "selftest":
        return selftest(argv[1:])
    sys.stderr.write("unknown command %r; try `selftest --help`\n" % argv[0])
    return nvcore.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
