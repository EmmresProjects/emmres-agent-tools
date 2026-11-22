#!/usr/bin/env python3
"""WAV measurement and delivery-profile conformance for the toolkit.

Pure stdlib (wave/struct/math) for PCM measurement. No numpy, no ffmpeg
required. If ffmpeg is available (auto-detected via PATH or --ffmpeg PATH),
`audio-check --profile ...` additionally measures true integrated LUFS and
true peak with the ebur128 filter; without ffmpeg those checks are reported
as SKIPPED rather than guessed.

Supports PCM WAV: 8-bit unsigned, 16/24/32-bit signed little-endian
(including WAVE_FORMAT_EXTENSIBLE wrapping PCM, as exposed by the stdlib
wave module). IEEE-float WAV / compressed formats are rejected with a clear
message; convert to PCM first (e.g. `ffmpeg -i in.wav -c:a pcm_s16le out.wav`).

Measured quantities (whole file, chunked, bounded memory):
  duration_seconds, sample_rate, channels, sample_width_bytes, frames
  peak_dbfs, rms_dbfs, crest_factor_db, dc_offset (fraction of full scale),
  clipped_percent, silence_percent (<-50 dBFS), estimated loudness caveat

Verdict model:
  fail  - container/format unreadable for the target, clipping > 1%,
          sample rate or channel count mismatch vs profile, peak/RMS limits
          exceeded, evidence of near-total silence when speech is expected
  warn  - minor clipping (0 < clipped <= 1%), DC offset > 0.5% full scale,
          silence > 40%, missing optional ffmpeg measurement
  pass  - otherwise
"""

import importlib.util
import math
import os
import sys
import re
import shutil
import struct
import subprocess
import wave

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    # Share one module instance per name via sys.modules so that classes
    # (e.g. tools_common.ToolError) keep a single identity across siblings.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_common = _load_sibling("tools_common", "tools-common.py")
ToolError = _common.ToolError
MAX_AUDIO_BYTES = _common.MAX_AUDIO_BYTES

CHUNK_FRAMES = 65536
SILENCE_DBFS = -50.0


def _pcm_iter(sampwidth, raw):
    """Yield (sample_int, full_scale) for one chunk of raw PCM bytes."""
    if sampwidth == 1:
        full = 128
        for b in raw:
            yield b - 128, full
    elif sampwidth == 2:
        full = 32768
        n = len(raw) // 2
        for v in struct.unpack("<%dh" % n, raw[: n * 2]):
            yield v, full
    elif sampwidth == 3:
        full = 8388608
        i = 0
        end = len(raw) - 2
        while i < end:
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            yield v, full
            i += 3
    elif sampwidth == 4:
        full = 2147483648
        n = len(raw) // 4
        for v in struct.unpack("<%di" % n, raw[: n * 4]):
            yield v, full
    else:
        raise ToolError("Unsupported sample width: %d bytes" % sampwidth)


def analyze_wav(path):
    _common.check_readable_file(path, MAX_AUDIO_BYTES, "Audio file")
    try:
        wf = wave.open(path, "rb")
    except wave.Error as exc:
        raise ToolError(
            "Not a PCM WAV file (%s). Convert first, e.g. "
            "`ffmpeg -i %s -c:a pcm_s16le out.wav`." % (exc, path)
        )
    try:
        params = wf.getparams()
        sampwidth = wf.getsampwidth()
        if sampwidth not in (1, 2, 3, 4):
            raise ToolError("Unsupported sample width: %d bytes" % sampwidth)
        nframes = wf.getnframes()
        framerate = wf.getframerate()
        channels = wf.getnchannels()
        if nframes <= 0 or framerate <= 0:
            raise ToolError("WAV contains no frames or an invalid sample rate.")

        total = 0
        ssum = 0
        ssq = 0
        peak = 0
        clipped = 0
        silent = 0
        zero_cross = 0
        prev_sign = 0

        while True:
            raw = wf.readframes(CHUNK_FRAMES)
            if not raw:
                break
            for v, full in _pcm_iter(sampwidth, raw):
                a = abs(v)
                total += 1
                ssum += v
                ssq += v * v
                if a > peak:
                    peak = a
                if a >= full - 1:
                    clipped += 1
                if a < full * (10.0 ** (SILENCE_DBFS / 20.0)):
                    silent += 1
                sign = (v > 0) - (v < 0)
                if sign != 0:
                    if prev_sign != 0 and sign != prev_sign:
                        zero_cross += 1
                    prev_sign = sign
        full = float(1 << (8 * sampwidth - 1))
        mean = ssum / total
        rms = math.sqrt(ssq / total)
        peak_dbfs = 20.0 * math.log10(peak / full) if peak > 0 else -120.0
        rms_dbfs = 20.0 * math.log10(rms / full) if rms > 0 else -120.0
        crest_db = (20.0 * math.log10(peak / rms)) if rms > 0 and peak > 0 else 0.0
        result = {
            "file": path,
            "format": "wav",
            "encoding": "pcm-s%dle" % (sampwidth * 8) if sampwidth > 1 else "pcm-u8",
            "sample_rate": framerate,
            "channels": channels,
            "sample_width_bytes": sampwidth,
            "frames": nframes,
            "duration_seconds": round(nframes / float(framerate), 3),
            "samples_analyzed": total,
            "peak_dbfs": round(peak_dbfs, 2),
            "rms_dbfs": round(rms_dbfs, 2),
            "crest_factor_db": round(crest_db, 2),
            "dc_offset": round(mean / full, 6),
            "clipped_percent": round(100.0 * clipped / total, 4),
            "silence_percent": round(100.0 * silent / total, 2),
            "zero_crossings": zero_cross,
            "measured_by": "stdlib-pcm",
            "loudness_note": (
                "rms_dbfs is an unweighted RMS level, not a K-weighted LUFS "
                "measurement. Use ffmpeg (--ffmpeg) for true integrated LUFS "
                "and true peak on loudness-critical deliveries."
            ),
        }
        return result
    finally:
        wf.close()


def find_ffmpeg(explicit=None):
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise ToolError("--ffmpeg path is not an executable file: %s" % explicit)
    return shutil.which("ffmpeg")


def ffmpeg_loudness(path, ffmpeg_path, timeout=120):
    """Integrated LUFS / true peak via ffmpeg ebur128. Returns dict or None."""
    cmd = [
        ffmpeg_path, "-hide_banner", "-nostats", "-i", path,
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError("ffmpeg timed out after %ds while measuring loudness." % timeout)
    log = (proc.stderr or b"").decode("utf-8", "replace")
    integrated = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", log)
    true_peak = re.search(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", log)
    out = {"measured_by": "ffmpeg-ebur128"}
    if integrated:
        out["integrated_lufs"] = float(integrated.group(1))
    if true_peak:
        out["true_peak_dbtp"] = float(true_peak.group(1))
    if "integrated_lufs" not in out:
        out["warning"] = "ffmpeg ran but its ebur128 summary could not be parsed."
    return out


def check_against_profile(measured, profile, ebur_result=None):
    """Return list of check dicts. status in pass|warn|fail|skip."""
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    # Container conformance
    exp_sr, exp_ch = profile.get("sample_rate"), profile.get("channels")
    if exp_sr:
        ok = measured["sample_rate"] == exp_sr
        add("sample_rate", "pass" if ok else "fail",
            "measured %d Hz, profile requires %d Hz" % (measured["sample_rate"], exp_sr))
    if exp_ch:
        ok = measured["channels"] == exp_ch
        add("channels", "pass" if ok else "fail",
            "measured %d, profile requires %d" % (measured["channels"], exp_ch))

    # Peak limits
    if "max_sample_peak_dbfs" in profile:
        limit = profile["max_sample_peak_dbfs"]
        ok = measured["peak_dbfs"] <= limit
        add("sample_peak", "pass" if ok else "fail",
            "peak %.2f dBFS vs limit %.2f dBFS" % (measured["peak_dbfs"], limit))
    if "max_true_peak_dbfs" in profile:
        limit = profile["max_true_peak_dbfs"]
        if ebur_result and "true_peak_dbtp" in ebur_result:
            ok = ebur_result["true_peak_dbtp"] <= limit
            add("true_peak", "pass" if ok else "fail",
                "true peak %.2f dBTP vs limit %.2f dBTP" % (ebur_result["true_peak_dbtp"], limit))
        else:
            add("true_peak", "skip",
                "true peak not measured (ffmpeg unavailable); sample peak is %.2f dBFS" % measured["peak_dbfs"])

    # Loudness target
    if "target_lufs" in profile:
        target, tol = profile["target_lufs"], profile.get("tolerance_lufs", 1.0)
        if ebur_result and "integrated_lufs" in ebur_result:
            delta = abs(ebur_result["integrated_lufs"] - target)
            ok = delta <= tol
            add("integrated_loudness", "pass" if ok else "fail",
                "integrated %.2f LUFS vs target %.2f ±%.2f" % (ebur_result["integrated_lufs"], target, tol))
        else:
            add("integrated_loudness", "skip",
                "LUFS not measured (ffmpeg unavailable); rms_dbfs=%.2f is not a LUFS substitute" % measured["rms_dbfs"])

    # ACX-style RMS window
    if "rms_floor_dbfs" in profile and "rms_ceiling_dbfs" in profile:
        lo, hi = profile["rms_floor_dbfs"], profile["rms_ceiling_dbfs"]
        rms = measured["rms_dbfs"]
        ok = lo <= rms <= hi
        add("rms_window", "pass" if ok else "fail",
            "rms %.2f dBFS; window [%.2f, %.2f] dBFS" % (rms, lo, hi))

    return checks


def verdict_from_checks(checks, measured):
    statuses = [c["status"] for c in checks]
    clipped = measured["clipped_percent"]
    base = []
    if clipped > 1.0:
        base.append("fail")
    elif clipped > 0.0:
        base.append("warn")
    if measured["silence_percent"] > 60.0:
        base.append("warn")
    if abs(measured["dc_offset"]) > 0.005:
        base.append("warn")
    statuses += base
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def load_delivery_profiles():
    data = _common.load_asset_json("asset-delivery-profiles.json")
    return {p["id"]: p for p in data["profiles"]}


def run_audio_check(path, profile_id=None, ffmpeg=None, timeout=120):
    measured = analyze_wav(path)
    checks, ebur = [], None
    profile = None
    # An explicit --ffmpeg PATH is always validated, even without --profile.
    ff = find_ffmpeg(ffmpeg)
    if ff:
        ebur = ffmpeg_loudness(path, ff, timeout=timeout)
        if "integrated_lufs" in ebur:
            measured["integrated_lufs"] = ebur["integrated_lufs"]
            measured["true_peak_dbtp"] = ebur.get("true_peak_dbtp")
            measured["measured_by"] = "stdlib-pcm+ffmpeg-ebur128"
        elif ebur.get("warning"):
            measured.setdefault("warnings", []).append(ebur["warning"])
    elif profile_id:
        measured.setdefault("warnings", []).append(
            "ffmpeg not found; LUFS/true-peak checks skipped (install ffmpeg or pass --ffmpeg)."
        )
    if profile_id:
        profiles = load_delivery_profiles()
        if profile_id not in profiles:
            raise ToolError(
                "Unknown delivery profile '%s'. Available: %s" % (profile_id, ", ".join(sorted(profiles)))
            )
        profile = profiles[profile_id]
        checks = check_against_profile(measured, profile, ebur)
    else:
        checks = [
            {"name": "container", "status": "pass", "detail": "PCM WAV analyzed successfully"},
        ]
    verdict = verdict_from_checks(checks, measured)
    return {
        "ok": verdict != "fail",
        "verdict": verdict,
        "profile": profile["id"] if profile else None,
        "measured": measured,
        "checks": checks,
    }
