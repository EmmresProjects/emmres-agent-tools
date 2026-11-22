#!/usr/bin/env python3
"""tools-nv-master.py - waveform mastering, render and export for offline neural voice.

Loaded by the entry point; safe to import. Self test:

    python tools-nv-master.py selftest

Capabilities
------------
trim             remove leading/trailing silence with a natural lead-in
highpass         remove low-frequency rumble (DC, room, breath thumps)
deess            gentle sibilance reduction
equalize         tilt / presence shaping with fixed, documented curves
resample         band-limited resample to a delivery sample rate
normalize        two-pass EBU R128 style loudness normalisation to a target LUFS
limit            look-ahead true-peak limiter to a ceiling
fade             micro fades to avoid clicks at boundaries
assemble         concatenate segments with explicit pauses
render-wav       write a deterministic 16-bit PCM WAV (atomic)
encode-mp3       high quality mono MP3 via lameenc, then decode-verify
pipeline         run the documented chain end to end and report each stage

Every measurement is reported; nothing is changed silently. Mastering never
overwrites an input: it writes a new artifact and records what it did.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
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
from nvcore import (InputError, NVError, as_number, atomic_write_bytes, decode_audio_any,
                    highpass_int16, limiter, measure_loudness, read_wav_int16, resample_int16,
                    slugify, trim_silence, true_peak_dbfs, utc_now, wav_statistics,
                    write_wav_int16)

#: Documented mastering chain order. Stages are skipped when disabled.
CHAIN = ["trim", "highpass", "deess", "equalize", "resample", "normalize", "limit", "fade"]

#: Fixed equaliser curves (dB per band) used by `equalize`.
EQ_CURVES = {
    "neutral": {},
    "warm": {"low_shelf_db": 1.2, "presence_db": -0.4},
    "bright": {"presence_db": 1.4, "air_db": 1.0},
    "broadcast": {"low_shelf_db": -1.0, "presence_db": 1.2},
    "soft": {"presence_db": -1.2, "air_db": -0.8},
}


def apply_high_shelf(samples, rate, gain_db, corner_hz=6000.0):
    if abs(gain_db) < 0.01:
        return array("h", samples)
    data = [v / 32768.0 for v in samples]
    coeffs = _shelf_coeffs(rate, corner_hz, gain_db)
    b0, b1, b2, a1, a2 = coeffs
    out = [0.0] * len(data)
    x1 = x2 = y1 = y2 = 0.0
    for idx, value in enumerate(data):
        y = b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, value
        y2, y1 = y1, y
        out[idx] = y
    return nvcore.float_to_int16(out)


def apply_low_shelf(samples, rate, gain_db, corner_hz=250.0):
    if abs(gain_db) < 0.01:
        return array("h", samples)
    data = [v / 32768.0 for v in samples]
    b0, b1, b2, a1, a2 = _shelf_coeffs(rate, corner_hz, gain_db, shelf="low")
    out = [0.0] * len(data)
    x1 = x2 = y1 = y2 = 0.0
    for idx, value in enumerate(data):
        y = b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, value
        y2, y1 = y1, y
        out[idx] = y
    return nvcore.float_to_int16(out)


def _shelf_coeffs(rate, corner_hz, gain_db, shelf="high", slope=1.0):
    """RBJ-cookbook shelving filter coefficients."""
    a = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * corner_hz / rate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / 2 * math.sqrt((a + 1 / a) * (1 / slope - 1) + 2)
    two_sqrt_a_alpha = 2 * math.sqrt(a) * alpha
    if shelf == "high":
        b0 = a * ((a + 1) + (a - 1) * cos_w0 + two_sqrt_a_alpha)
        b1 = -2 * a * ((a - 1) + (a + 1) * cos_w0)
        b2 = a * ((a + 1) + (a - 1) * cos_w0 - two_sqrt_a_alpha)
        a0 = (a + 1) - (a - 1) * cos_w0 + two_sqrt_a_alpha
        a1 = 2 * ((a - 1) - (a + 1) * cos_w0)
        a2 = (a + 1) - (a - 1) * cos_w0 - two_sqrt_a_alpha
    else:
        b0 = a * ((a + 1) - (a - 1) * cos_w0 + two_sqrt_a_alpha)
        b1 = 2 * a * ((a - 1) - (a + 1) * cos_w0)
        b2 = a * ((a + 1) - (a - 1) * cos_w0 - two_sqrt_a_alpha)
        a0 = (a + 1) + (a - 1) * cos_w0 + two_sqrt_a_alpha
        a1 = -2 * ((a - 1) + (a + 1) * cos_w0)
        a2 = (a + 1) + (a - 1) * cos_w0 - two_sqrt_a_alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def apply_equalize(samples, rate, curve="neutral"):
    if curve not in EQ_CURVES:
        raise InputError("unknown equaliser curve %r" % curve,
                         hint="curves: %s" % ", ".join(sorted(EQ_CURVES)))
    shape = EQ_CURVES[curve]
    out = array("h", samples)
    if "low_shelf_db" in shape:
        out = apply_low_shelf(out, rate, shape["low_shelf_db"])
    if "presence_db" in shape:
        out = apply_high_shelf(out, rate, shape["presence_db"], corner_hz=3000.0)
    if "air_db" in shape:
        out = apply_high_shelf(out, rate, shape["air_db"], corner_hz=9000.0)
    return out


def normalize_loudness(samples, rate, target_lufs, *, max_gain_db=24.0, tolerance=1.0,
                       iterations=3):
    """Two-pass (plus refinement) loudness normalisation to a LUFS target."""
    target = float(target_lufs)
    gain_total = 0.0
    measured = measure_loudness(samples, rate)
    history = []
    current = array("h", samples)
    for attempt in range(iterations):
        lufs = measured.get("integrated_lufs")
        if lufs is None:
            raise NVError("cannot normalise a silent signal",
                          hint="check the synthesis output before mastering")
        delta = target - lufs
        history.append({"pass": attempt + 1, "measured_lufs": lufs, "delta_db": round(delta, 3)})
        if abs(delta) <= tolerance * 0.5:
            break
        capped = max(-max_gain_db, min(max_gain_db, delta))
        if abs(capped) < 0.01:
            break
        current = nvcore.apply_gain_db(current, capped)
        gain_total += capped
        measured = measure_loudness(current, rate)
    final = measured.get("integrated_lufs")
    return {
        "samples": current,
        "gain_db": round(gain_total, 3),
        "target_lufs": target,
        "measured_lufs": final,
        "residual_lu": None if final is None else round(target - final, 3),
        "passes": history,
        "clipped": False,
    }


def normalize_loudness_linear(samples, rate, target_lufs, *, max_gain_db=24.0):
    """Single measured gain stage (pre-limiter); keeps dynamics intact."""
    measured = measure_loudness(samples, rate)
    lufs = measured.get("integrated_lufs")
    if lufs is None:
        raise NVError("cannot normalise a silent signal",
                      hint="check that the synthesis produced audible audio")
    gain = max(-max_gain_db, min(max_gain_db, float(target_lufs) - lufs))
    return nvcore.apply_gain_db(samples, gain), {"gain_db": round(gain, 3),
                                                "measured_lufs_before": lufs}


def master_pipeline(samples, rate, *, cfg, target_lufs=None, ceiling_dbfs=None,
                    eq_curve="neutral", do_trim=True, do_highpass=True, do_deess=True,
                    do_resample=True, do_fade=True, cancel=None, stages_log=None):
    """Run the documented mastering chain; returns (samples, rate, report)."""
    target_lufs = as_number(target_lufs if target_lufs is not None else cfg.get("mastering.target_lufs", -14.0),
                            name="target_lufs", minimum=-40.0, maximum=-5.0)
    ceiling_dbfs = as_number(ceiling_dbfs if ceiling_dbfs is not None else cfg.get("mastering.max_true_peak_dbfs", -1.0),
                             name="ceiling_dbfs", minimum=-12.0, maximum=0.0)
    tolerance = float(cfg.get("mastering.loudness_tolerance_lu", 1.0))
    report = {"stages": [], "settings": {"target_lufs": target_lufs,
                                         "ceiling_dbfs": ceiling_dbfs,
                                         "eq_curve": eq_curve}}
    current = array("h", samples)
    current_rate = int(rate)

    def stage(name, fn):
        nonlocal current, current_rate
        started = time.monotonic()
        if cancel is not None:
            cancel.check()
        before = wav_statistics(current, current_rate) if current else {}
        result = fn()
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
            current, detail = result
        elif isinstance(result, tuple):
            current, current_rate = result
            detail = {"sample_rate": current_rate}
        else:
            current = result
            detail = {}
        after = wav_statistics(current, current_rate) if current else {}
        entry = {"stage": name, "ms": round((time.monotonic() - started) * 1000, 2),
                 "frames_in": before.get("frames"), "frames_out": after.get("frames"),
                 "peak_dbfs_in": before.get("peak_dbfs"), "peak_dbfs_out": after.get("peak_dbfs"),
                 "changed": before.get("frames") != after.get("frames") or
                            before.get("peak_dbfs") != after.get("peak_dbfs"),
                 "detail": detail}
        report["stages"].append(entry)
        if stages_log is not None:
            stages_log.append(entry)

    if do_trim:
        stage("trim", lambda: trim_silence(current, current_rate, threshold_db=-55.0, keep_ms=60))
    if do_highpass:
        cutoff = float(cfg.get("mastering.highpass_hz", 70.0) or 0.0)
        if cutoff > 0:
            stage("highpass", lambda: highpass_int16(current, current_rate, cutoff, order=2))
    if do_deess:
        stage("deess", lambda: (nvcore.deess_int16(current, current_rate, amount=0.25), {}))
    if eq_curve and eq_curve != "neutral":
        stage("equalize", lambda: (apply_equalize(current, current_rate, eq_curve),
                                   {"curve": eq_curve}))
    wanted_rate = cfg.get("mastering.resample_hz")
    if do_resample and wanted_rate:
        wanted_rate = int(wanted_rate)
        if wanted_rate != current_rate:
            stage("resample", lambda: (resample_int16(current, current_rate, wanted_rate), wanted_rate))
    stage("normalize", lambda: _normalize_stage(current, current_rate, target_lufs))
    if ceiling_dbfs > -12.0:
        stage("limit", lambda: _limit_stage(current, current_rate, ceiling_dbfs,
                                            float(cfg.get("mastering.limiter_lookahead_ms", 20.0))))
        # Peak limiting lowers integrated loudness, so the result can sit ~1 LU under
        # target while the ceiling is already reached — and then no amount of plain gain
        # helps, because the limiter just takes it straight back. The fix is to drive
        # gain INTO the limiter: crest factor drops, loudness rises, peak stays clamped.
        max_drive = float(cfg.get("mastering.max_limiter_drive_db", 6.0))
        driven = 0.0
        lookahead = float(cfg.get("mastering.limiter_lookahead_ms", 20.0))
        for attempt in range(int(cfg.get("mastering.loudness_correction_passes", 3))):
            measured = measure_loudness(current, current_rate).get("integrated_lufs")
            if measured is None:
                break
            error = target_lufs - measured
            if abs(error) <= 0.05 or driven >= max_drive:
                report.setdefault("loudness_correction", []).append(
                    {"attempt": attempt + 1, "skipped": True,
                     "lufs": round(measured, 3), "driven_db": round(driven, 3)})
                break
            step = max(-max_drive - driven, min(error, max_drive - driven))
            if abs(step) < 0.02:
                break
            factor = 10.0 ** (step / 20.0)
            current = array("h", [max(-32768, min(32767, int(round(v * factor))))
                                  for v in current])
            current = _limit_stage(current, current_rate, ceiling_dbfs, lookahead)[0]
            driven += step
            report.setdefault("loudness_correction", []).append(
                {"attempt": attempt + 1, "drive_db": round(step, 3),
                 "total_drive_db": round(driven, 3),
                 "lufs_before": round(measured, 3),
                 "lufs_after": round(measure_loudness(current, current_rate)
                                     .get("integrated_lufs") or 0.0, 3),
                 "peak_after": true_peak_dbfs(current, current_rate)["true_peak_dbfs"]})
    if do_fade:
        stage("fade", lambda: (nvcore.apply_fade(current, current_rate, fade_in_ms=5.0,
                                                 fade_out_ms=25.0), {}))

    final_stats = wav_statistics(current, current_rate)
    final_loudness = measure_loudness(current, current_rate)
    final_peak = true_peak_dbfs(current, current_rate)
    report.update({
        "final": {"sample_rate": current_rate, "frames": len(current),
                  "duration_s": round(len(current) / float(current_rate), 4),
                  "stats": final_stats, "loudness": final_loudness, "true_peak": final_peak},
        "compliant": {
            "loudness_ok": (final_loudness.get("integrated_lufs") is not None and
                            abs(final_loudness["integrated_lufs"] - target_lufs) <= tolerance),
            "true_peak_ok": final_peak["true_peak_dbfs"] <= ceiling_dbfs + 0.05,
            "no_clipping": final_stats.get("clipped_samples", 0) == 0,
        },
    })
    return current, current_rate, report


def _normalize_stage(samples, rate, target_lufs):
    out, detail = normalize_loudness_linear(samples, rate, target_lufs)
    return out, detail


def _limit_stage(samples, rate, ceiling_dbfs, lookahead_ms):
    out, detail = limiter(samples, rate, ceiling_dbfs=ceiling_dbfs, lookahead_ms=lookahead_ms)
    return out, detail


def assemble_segments(segments, rate, *, pauses_ms=None):
    """Concatenate [(samples, rate)] with optional pauses between segments."""
    pauses_ms = pauses_ms or []
    out = array("h")
    last_rate = rate
    for index, (samples, seg_rate) in enumerate(segments):
        if seg_rate != rate:
            samples = resample_int16(samples, seg_rate, rate)
        out.extend(samples)
        if index < len(segments) - 1:
            pause = pauses_ms[index] if index < len(pauses_ms) else 0
            if pause:
                out.extend(array("h", bytes(2 * int(rate * pause / 1000.0))))
        last_rate = rate
    return out, last_rate


def export_wav(path, samples, rate, *, overwrite=False, dither=True):
    """Write deterministic 16-bit PCM mono WAV with TPDF dither on quiet detail."""
    payload = array("h", samples)
    if dither:
        # deterministic triangular dither from a fixed LCG; amplitude < 1 LSB
        state = 0x2545F491
        for idx in range(len(payload)):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            first = (state >> 16) & 0xFFFF
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            second = (state >> 16) & 0xFFFF
            offset = (first - second) / 65536.0
            value = payload[idx] + offset
            if value > 32767:
                value = 32767
            elif value < -32768:
                value = -32768
            payload[idx] = int(round(value))
    data = array("h", payload)
    if sys.byteorder == "big":
        data.byteswap()
    header = nvcore._wav_header(len(data.tobytes()), int(rate), 1, 2)
    if sys.byteorder == "big":
        data.byteswap()
    return atomic_write_bytes(path, header + data.tobytes(), overwrite=overwrite)


def export_mp3(path, samples, rate, *, bitrate_kbps=192, overwrite=False):
    payload = nvcore.encode_mp3(samples, rate, bitrate_kbps=bitrate_kbps)
    return atomic_write_bytes(path, payload, overwrite=overwrite)


def verify_mp3(path, *, max_extra_seconds=0.12):
    """Decode the MP3 back and compare duration and silence with the source contract."""
    samples, rate = decode_audio_any(path)
    stats = wav_statistics(samples, rate)
    return {"decoded_frames": len(samples), "decoded_sr": rate,
            "decoded_duration_s": stats.get("duration_s"),
            "decoded_peak_dbfs": stats.get("peak_dbfs"),
            "decoded_silence_ratio": stats.get("silence_ratio"),
            "decodable": len(samples) > 0}


def selftest(argv=None):
    parser = argparse.ArgumentParser(prog="tools-nv-master.py selftest")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--out", default=None, help="directory for test artifacts")
    args = parser.parse_args(argv)
    import tempfile
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

    workdir = Path(args.out) if args.out else Path(tempfile.mkdtemp())
    workdir.mkdir(parents=True, exist_ok=True)

    def tone(rate=22050, seconds=2.0, amp=0.25, freq=220.0):
        count = int(rate * seconds)
        return array("h", [int(32768 * amp * math.sin(2 * math.pi * freq * i / rate))
                           for i in range(count)]), rate

    def t_chain():
        samples, rate = tone()
        cfg = nvcore.load_config(None, root=nvcore.skill_root())
        mastered, mrate, report = master_pipeline(samples, rate, cfg=cfg,
                                                  target_lufs=-14.0, ceiling_dbfs=-1.0)
        assert mrate == rate
        assert report["compliant"]["true_peak_ok"], report["compliant"]
        lufs = report["final"]["loudness"]["integrated_lufs"]
        assert abs(lufs + 14.0) < 1.5, lufs
        return "chain produced %.2f LUFS at %.2f dBTP" % (lufs,
                                                          report["final"]["true_peak"]["true_peak_dbfs"])

    def t_wav_export():
        samples, rate = tone(seconds=1.0)
        target = workdir / "master-selftest.wav"
        export_wav(target, samples, rate, overwrite=True)
        info = nvcore.read_wav_info(target)
        assert info["sample_rate"] == rate and info["channels"] == 1
        return "wrote %d bytes / %.3f s PCM" % (info["bytes"], info["duration_s"])

    def t_mp3():
        try:
            import lameenc  # noqa: F401
        except Exception:
            return "skipped: lameenc not installed"
        samples, rate = tone(seconds=1.0)
        target = workdir / "master-selftest.mp3"
        export_mp3(target, samples, rate, bitrate_kbps=128, overwrite=True)
        verify = verify_mp3(target)
        assert verify["decodable"], verify
        return "MP3 %d bytes decoded to %.3f s" % (target.stat().st_size,
                                                  verify["decoded_duration_s"])

    def t_guard_no_overwrite():
        samples, rate = tone(seconds=0.2)
        target = workdir / "master-selftest.wav"
        try:
            export_wav(target, samples, rate, overwrite=False)
            raise AssertionError("overwrite protection failed")
        except InputError:
            return "existing artifact is not overwritten without --overwrite"

    check("mastering-chain", t_chain)
    check("wav-export", t_wav_export)
    check("mp3-encode-decode", t_mp3)
    check("overwrite-protection", t_guard_no_overwrite)

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "tool": "offline-neural-voice/master", "version": nvcore.__version__,
               "checks": checks}
    if args.format == "json":
        nvcore.print_json(payload)
    else:
        for item in checks:
            print("[%s] %s (%sms) %s" % ("PASS" if item["ok"] else "FAIL",
                                         item["check"], item["ms"], item["detail"]))
        print("\n%s: %d/%d mastering checks passed" % ("PASS" if ok else "FAIL",
                                                       sum(1 for c in checks if c["ok"]), len(checks)))
        if not args.out:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
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
