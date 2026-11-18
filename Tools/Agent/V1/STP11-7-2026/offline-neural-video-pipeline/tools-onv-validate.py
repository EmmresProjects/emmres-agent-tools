"""Media validation suite: decode integrity, timing, picture, loudness, captions.

All checks are performed with ``ffprobe``/``ffmpeg`` on the local file. Nothing
is inferred from metadata alone: decode and level checks actually read every
frame and every audio sample.

Every check record has the shape:

    {"id": str, "status": "pass"|"warn"|"fail"|"skip", "severity": "error"|"warning"|"info",
     "summary": str, "measured": {...}, "threshold": {...}, "remedy": str|None}
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def _check(check_id, status, severity, summary, measured=None, threshold=None, remedy=None):
    return {
        "id": check_id,
        "status": status,
        "severity": severity,
        "summary": summary,
        "measured": measured or {},
        "threshold": threshold or {},
        "remedy": remedy,
    }


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fps(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if raw and "/" in str(raw):
            num, den = str(raw).split("/", 1)
            try:
                if float(den):
                    return round(float(num) / float(den), 3)
            except ValueError:
                continue
    return None


def probe(ctx, runner, path):
    common = ctx.common
    ffprobe_bin = ctx.config.get("ffprobe", "ffprobe")
    try:
        result = runner.run(
            [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-count_frames", str(path)],
            timeout_s=900,
            log_name="validate-probe",
        )
    except common.OnvError as err:
        common.fail(
            "E_INPUT",
            "media is not decodable by ffprobe: %s" % path,
            probe_error=err.message,
            detail=(err.details or {}).get("stderr_tail"),
        )
    try:
        return json.loads(result["stdout"])
    except ValueError:
        common.fail("E_INPUT", "ffprobe output unparsable for %s" % path)


def _null_run(ctx, runner, args, log_name):
    ffmpeg_bin = ctx.config.get("ffmpeg", "ffmpeg")
    argv = [ffmpeg_bin, "-hide_banner", "-nostdin"] + [str(a) for a in args] + ["-f", "null", "-"]
    result = runner.run(argv, timeout_s=1800, log_name=log_name)
    return result["stderr"] or ""


def decode_check(ctx, runner, path, policy, timeout_s):
    """Decode every frame and count real decoder errors."""
    stderr = _null_run(
        ctx,
        runner,
        ["-v", "error", "-i", str(path), "-map", "0:v:0?", "-map", "0:a:0?"],
        "validate-decode",
    )
    errors = [line for line in stderr.splitlines() if line.strip() and not line.strip().startswith("frame=")]
    max_errors = int((policy.get("checks", {}).get("decode", {}) or {}).get("max_error_lines", 0))
    status = "pass" if len(errors) <= max_errors else "fail"
    return _check(
        "decode",
        status,
        policy["severity"].get("decode", "error"),
        "clean decode" if status == "pass" else "%d decoder error line(s)" % len(errors),
        measured={"error_lines": len(errors), "samples": errors[:5]},
        threshold={"max_error_lines": max_errors},
        remedy=None if status == "pass" else "Re-encode from the source; the file is truncated or has corrupt packets.",
    )


def blackdetect(ctx, runner, path, cfg):
    stderr = _null_run(
        ctx,
        runner,
        ["-v", "info", "-i", str(path), "-an", "-vf", "blackdetect=d=%s:pix_th=%s" % (cfg.get("min_segment_s", 0.5), cfg.get("pixel_threshold", 0.10))],
        "validate-black",
    )
    durations = [float(m) for m in re.findall(r"black_duration:([0-9.]+)", stderr)]
    total = round(sum(durations), 3)
    max_total = float(cfg.get("max_total_s", 2.0))
    status = "pass" if total <= max_total else "warn"
    return _check(
        "black",
        status,
        "warning",
        "%d black segment(s), %.2fs total" % (len(durations), total),
        measured={"segments": durations, "total_s": total},
        threshold={"max_total_s": max_total, "min_segment_s": cfg.get("min_segment_s", 0.5)},
        remedy=None if status == "pass" else "Trim or regrade the black sections if they are unintentional.",
    )


def freezedetect(ctx, runner, path, cfg):
    stderr = _null_run(
        ctx,
        runner,
        [
            "-v", "info", "-i", str(path), "-an",
            "-vf", "freezedetect=n=%sdB:d=%s" % (cfg.get("noise_db", -60), cfg.get("min_segment_s", 2.0)),
        ],
        "validate-freeze",
    )
    starts = [float(m) for m in re.findall(r"freeze_start:\s*([0-9.]+)", stderr)]
    durations = [float(m) for m in re.findall(r"freeze_duration:\s*([0-9.]+)", stderr)]
    total = round(sum(durations), 3)
    max_total = float(cfg.get("max_total_s", 3.0))
    status = "pass" if total <= max_total else "warn"
    return _check(
        "freeze",
        status,
        "warning",
        "%d freeze(s), %.2fs total" % (len(starts), total),
        measured={"starts": starts, "durations": durations, "total_s": total},
        threshold={"max_total_s": max_total, "noise_db": cfg.get("noise_db", -60)},
        remedy=None if status == "pass" else "Increase motion in the generation prompt or interpolate frames.",
    )


def loudness(ctx, runner, path, cfg):
    stderr = _null_run(
        ctx,
        runner,
        ["-v", "info", "-i", str(path), "-map", "0:a:0", "-af", "loudnorm=print_format=json"],
        "validate-loudness",
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not match:
        return _check(
            "loudness",
            "skip",
            "info",
            "no audio stream to measure",
            measured={},
            threshold={"target_lufs": cfg.get("target_lufs")},
        )
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return _check("loudness", "warn", "warning", "loudnorm output unparsable", measured={"raw": match.group(0)[:200]})
    integrated = _num(data.get("input_i"))
    true_peak = _num(data.get("input_tp"))
    lra = _num(data.get("input_lra"))
    target = float(cfg.get("target_lufs", -16.0))
    tolerance = float(cfg.get("tolerance_lu", 3.0))
    max_tp = float(cfg.get("max_true_peak_dbtp", -1.0))
    max_lra = float(cfg.get("max_lra_lu", 20.0))
    problems = []
    if integrated is None or abs(integrated - target) > tolerance:
        problems.append("integrated %.1f LUFS outside %.1f±%.1f" % (integrated or -99, target, tolerance))
    if true_peak is not None and true_peak > max_tp:
        problems.append("true peak %.1f dBTP above %s" % (true_peak, max_tp))
    if lra is not None and lra > max_lra:
        problems.append("LRA %.1f LU above %s" % (lra, max_lra))
    status = "pass" if not problems else "warn"
    return _check(
        "loudness",
        status,
        "warning",
        "integrated %.1f LUFS, true peak %s dBTP" % (integrated if integrated is not None else -99, true_peak),
        measured={"integrated_lufs": integrated, "true_peak_dbtp": true_peak, "lra_lu": lra},
        threshold={"target_lufs": target, "tolerance_lu": tolerance, "max_true_peak_dbtp": max_tp, "max_lra_lu": max_lra},
        remedy=None if status == "pass" else "Re-run the audio stage with loudnorm matching the delivery target.",
    )


def clipping(ctx, runner, path, cfg):
    stderr = _null_run(ctx, runner, ["-v", "info", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect"], "validate-peak")
    matches = re.findall(r"max_volume:\s*(-?[0-9.]+) dB", stderr)
    if not matches:
        return _check("clipping", "skip", "info", "no audio stream to measure", measured={})
    peak = float(matches[0])
    limit = float(cfg.get("max_peak_dbfs", -0.05))
    status = "pass" if peak <= limit else "warn"
    return _check(
        "clipping",
        status,
        "warning",
        "sample peak %.2f dBFS" % peak,
        measured={"max_volume_dbfs": peak},
        threshold={"max_peak_dbfs": limit},
        remedy=None if status == "pass" else "Lower the mix gain before the final limiter.",
    )


def caption_check(ctx, runner, path, declared, cfg):
    if not declared:
        return _check("caption", "skip", "info", "no captions declared for this job", measured={})
    doc = probe(ctx, runner, path)
    subtitle_streams = [s for s in doc.get("streams", []) if s.get("codec_type") == "subtitle"]
    sidecars = sorted(Path(path).parent.glob(Path(path).stem + "*.srt")) + sorted(Path(path).parent.glob(Path(path).stem + "*.vtt"))
    cues = 0
    for sidecar in sidecars:
        try:
            cues += len([line for line in sidecar.read_text(encoding="utf-8", errors="replace").splitlines() if re.match(r"^\d+$", line.strip())])
        except OSError:
            pass
    min_cues = max(1, int(cfg.get("min_cue_count", 1))) if isinstance(cfg, dict) else 1
    ok = bool(subtitle_streams) or (cues >= min_cues)
    return _check(
        "caption",
        "pass" if ok else "warn",
        "warning",
        "%d subtitle stream(s), %d sidecar cue(s)" % (len(subtitle_streams), cues),
        measured={"subtitle_streams": len(subtitle_streams), "sidecar_cues": cues, "sidecars": [s.name for s in sidecars]},
        threshold={"min_cue_count": min_cues},
        remedy=None if ok else "Generate the caption sidecar or burn-in stage before delivery.",
    )


def validate_media(ctx, runner, spec, plan, path):
    """Run the full suite and return a report dict."""
    common = ctx.common
    policy = ctx.policy or {}
    checks_cfg = policy.get("checks", {})
    severity = policy.get("severity", {})
    path = Path(path)
    if not path.exists():
        common.fail("E_INPUT", "validation target missing: %s" % path, path=str(path))

    doc = probe(ctx, runner, path)
    streams = doc.get("streams", [])
    fmt = doc.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    budget = (plan or {}).get("budget", {}) or {}
    expected = {
        "width": (budget.get("resolution") or {}).get("width"),
        "height": (budget.get("resolution") or {}).get("height"),
        "fps": budget.get("fps"),
        "duration_s": budget.get("duration_s"),
    }

    checks = []

    # probe / structure
    structure_ok = video is not None
    checks.append(
        _check(
            "probe",
            "pass" if structure_ok else "fail",
            severity.get("probe", "error"),
            "container %s, %d stream(s)" % (fmt.get("format_name"), len(streams)),
            measured={
                "format_name": fmt.get("format_name"),
                "duration_s": _num(fmt.get("duration")),
                "size_bytes": _num(fmt.get("size")),
                "has_video": video is not None,
                "has_audio": audio is not None,
                "video_codec": (video or {}).get("codec_name"),
                "audio_codec": (audio or {}).get("codec_name"),
                "pix_fmt": (video or {}).get("pix_fmt"),
                "nb_frames": _num((video or {}).get("nb_read_frames") or (video or {}).get("nb_frames")),
            },
            remedy=None if structure_ok else "The file has no video stream; re-check the encode stage.",
        )
    )
    if not structure_ok:
        return _report(path, checks, policy, plan)

    # decode
    if (checks_cfg.get("decode") or {}).get("enabled", True):
        checks.append(decode_check(ctx, runner, path, policy, (checks_cfg.get("decode") or {}).get("timeout_s", 900)))

    # duration
    cfg = checks_cfg.get("duration") or {}
    measured_duration = _num(fmt.get("duration"))
    if cfg.get("enabled", True) and measured_duration is not None:
        if expected["duration_s"]:
            tolerance = max(float(cfg.get("tolerance_s", 0.35)), float(expected["duration_s"]) * float(cfg.get("tolerance_ratio", 0.08)))
            delta = abs(measured_duration - float(expected["duration_s"]))
            ok = delta <= tolerance
            checks.append(
                _check(
                    "duration",
                    "pass" if ok else "warn",
                    severity.get("duration", "warning"),
                    "%.3fs measured vs %.3fs expected" % (measured_duration, float(expected["duration_s"])),
                    measured={"duration_s": measured_duration, "delta_s": round(delta, 3)},
                    threshold={"expected_s": float(expected["duration_s"]), "tolerance_s": round(tolerance, 3)},
                    remedy=None if ok else "Check the trim/duration parameters passed to the generation stage.",
                )
            )
        else:
            checks.append(_check("duration", "pass", "info", "%.3fs" % measured_duration, measured={"duration_s": measured_duration}))

    # fps
    cfg = checks_cfg.get("fps") or {}
    measured_fps = _fps(video)
    if cfg.get("enabled", True) and measured_fps:
        if expected["fps"]:
            tolerance = abs(float(expected["fps"])) * float(cfg.get("tolerance_ratio", 0.02)) + 0.001
            delta = abs(measured_fps - float(expected["fps"]))
            ok = delta <= tolerance
            checks.append(
                _check(
                    "fps",
                    "pass" if ok else "warn",
                    severity.get("fps", "warning"),
                    "%.2f fps measured vs %s expected" % (measured_fps, expected["fps"]),
                    measured={"fps": measured_fps, "delta": round(delta, 4)},
                    threshold={"expected_fps": float(expected["fps"]), "tolerance": round(tolerance, 4)},
                    remedy=None if ok else "Verify the interpolate/retime stage and the encode -r setting.",
                )
            )
        else:
            checks.append(_check("fps", "pass", "info", "%.2f fps" % measured_fps, measured={"fps": measured_fps}))

    # resolution
    cfg = checks_cfg.get("resolution") or {}
    width, height = video.get("width"), video.get("height")
    if cfg.get("enabled", True):
        problems = []
        if expected["width"] and width != expected["width"]:
            problems.append("width %s != %s" % (width, expected["width"]))
        if expected["height"] and height != expected["height"]:
            problems.append("height %s != %s" % (height, expected["height"]))
        if cfg.get("require_even_dimensions", True) and (int(width or 0) % 2 or int(height or 0) % 2):
            problems.append("odd dimensions")
        checks.append(
            _check(
                "resolution",
                "pass" if not problems else "fail",
                severity.get("resolution", "error"),
                "%sx%s" % (width, height) if not problems else "; ".join(problems),
                measured={"width": width, "height": height},
                threshold={"expected_width": expected["width"], "expected_height": expected["height"]},
                remedy=None if not problems else "yuv420p delivery requires even dimensions; re-encode at the planned size.",
            )
        )

    # pixel format
    cfg = checks_cfg.get("pix_fmt") or {}
    pix_fmt = video.get("pix_fmt")
    if cfg.get("enabled", True):
        allowed = set(cfg.get("allowed") or [])
        ok = not allowed or pix_fmt in allowed
        checks.append(
            _check(
                "pix_fmt",
                "pass" if ok else "warn",
                severity.get("pix_fmt", "warning"),
                "pixel format %s" % pix_fmt,
                measured={"pix_fmt": pix_fmt},
                threshold={"allowed": sorted(allowed)},
                remedy=None if ok else "Re-encode with -pix_fmt yuv420p for broad player compatibility.",
            )
        )

    # black / freeze
    cfg = checks_cfg.get("black") or {}
    if cfg.get("enabled", True):
        checks.append(blackdetect(ctx, runner, path, cfg))
    cfg = checks_cfg.get("freeze") or {}
    if cfg.get("enabled", True):
        checks.append(freezedetect(ctx, runner, path, cfg))

    # audio checks
    cfg = checks_cfg.get("loudness") or {}
    if cfg.get("enabled", True) and audio is not None:
        checks.append(loudness(ctx, runner, path, cfg))
    cfg = checks_cfg.get("clipping") or {}
    if cfg.get("enabled", True) and audio is not None:
        checks.append(clipping(ctx, runner, path, cfg))
    cfg = checks_cfg.get("audio_presence") or {}
    if cfg.get("enabled", True):
        declared_audio = bool((spec or {}).get("audio")) or any(
            str(i.get("role", "")).lower() in ("audio", "voice", "dialogue", "music", "speech")
            for i in (spec or {}).get("inputs", [])
        )
        required = bool(cfg.get("require_when_declared", True)) and declared_audio
        ok = audio is not None or not required
        checks.append(
            _check(
                "audio_presence",
                "pass" if ok else "warn",
                severity.get("audio_presence", "info"),
                "audio stream %s" % ("present" if audio is not None else "absent"),
                measured={"has_audio": audio is not None, "declared": declared_audio},
                threshold={"required": required},
                remedy=None if ok else "The job declares audio but the delivery has no audio stream.",
            )
        )

    # captions
    cfg = checks_cfg.get("caption") or {}
    if cfg.get("enabled", True):
        declared = bool((spec or {}).get("captions")) or (plan or {}).get("operation") == "caption"
        checks.append(caption_check(ctx, runner, path, declared, cfg))

    # bitrate
    cfg = checks_cfg.get("bitrate") or {}
    if cfg.get("enabled", True) and measured_duration:
        size = _num(fmt.get("size")) or 0
        kbps = round((size * 8.0) / max(0.001, measured_duration) / 1000.0, 1)
        minimum = float(cfg.get("min_kbps", 200))
        checks.append(
            _check(
                "bitrate",
                "pass" if kbps >= minimum else "warn",
                severity.get("bitrate", "warning"),
                "%.1f kbps average" % kbps,
                measured={"kbps": kbps},
                threshold={"min_kbps": minimum},
                remedy=None if kbps >= minimum else "Raise the preset quality or CRF value.",
            )
        )

    # filesize
    cfg = checks_cfg.get("filesize") or {}
    if cfg.get("enabled", True):
        size_mb = path.stat().st_size / 1024.0 / 1024.0
        limit = float(((spec or {}).get("limits") or {}).get("max_output_mb") or 1e9)
        checks.append(
            _check(
                "filesize",
                "pass" if size_mb <= limit else "fail",
                severity.get("filesize", "error"),
                "%.2f MB" % size_mb,
                measured={"size_mb": round(size_mb, 3)},
                threshold={"max_output_mb": limit},
                remedy=None if size_mb <= limit else "Raise spec.limits.max_output_mb or lower the delivery bitrate.",
            )
        )

    return _report(path, checks, policy, plan)


def _report(path, checks, policy, plan):
    summary = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1
    blocking = [c for c in checks if c["status"] == "fail" and c["severity"] == "error"]
    warnings = [c for c in checks if c["status"] == "warn" or (c["status"] == "fail" and c["severity"] != "error")]
    if blocking:
        reason = "%d blocking check(s): %s" % (len(blocking), ", ".join("%s" % c["id"] for c in blocking))
    elif warnings:
        reason = "%d non-blocking warning(s): %s" % (len(warnings), ", ".join(c["id"] for c in warnings))
    else:
        reason = "all checks passed"
    return {
        "file": str(path),
        "bytes": path.stat().st_size if path.exists() else None,
        "validated_at": None,
        "policy_version": policy.get("policy_version"),
        "plan_job_id": (plan or {}).get("job_id"),
        "checks": checks,
        "summary": summary,
        "passed": not blocking,
        "reason": reason,
        "warnings": [{"id": c["id"], "summary": c["summary"]} for c in warnings],
    }


def validate_lines(report):
    icons = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    lines = ["Validation of %s" % report["file"], "-" * 78]
    for check in report["checks"]:
        lines.append("  [%s] %-20s %s" % (icons.get(check["status"], "????"), check["id"], check["summary"]))
        if check["status"] in ("fail", "warn") and check.get("remedy"):
            lines.append("         remedy: %s" % check["remedy"])
    lines.append("-" * 78)
    summary = report["summary"]
    lines.append(
        "Result: %s  (pass=%d warn=%d fail=%d skip=%d)"
        % ("PACKAGE VALID" if report["passed"] else "PACKAGE INVALID", summary.get("pass", 0), summary.get("warn", 0), summary.get("fail", 0), summary.get("skip", 0))
    )
    lines.append("Reason: %s" % report["reason"])
    return lines
