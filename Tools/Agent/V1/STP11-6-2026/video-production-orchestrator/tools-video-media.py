#!/usr/bin/env python3
"""Media engine for the video-production-orchestrator Skill Tool.

Module role: everything that touches real media bytes with FFmpeg/FFprobe.
Kept separate from the CLI because it is the only part with an external
dependency and is imported by both the CLI and the self-test.

Binary resolution order (never an absolute hard-coded path):
  1. environment variable VIDEO_FFMPEG / VIDEO_FFPROBE
  2. ``ffmpeg`` / ``ffprobe`` on PATH
  3. the wheel-installed ``imageio-ffmpeg`` binary (ffmpeg only; ffprobe is
     derived from ``ffmpeg -i`` output when a real ffprobe is absent)

Every operation is bounded by a timeout and returns structured dictionaries.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from importlib.machinery import SourceFileLoader


def _load_core():
    here = Path(__file__).resolve().parent
    return SourceFileLoader("video_core", str(here / "tools-video-core.py")).load_module()


core = _load_core()

DEFAULT_TIMEOUT = 300.0
THUMB_MAX = 500
TARGET_ASPECTS = {
    "16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5),
    "4:3": (4, 3), "2:1": (2, 1), "21:9": (21, 9), "3:4": (3, 4), "5:4": (5, 4),
}
PLATFORM_PRESETS = {
    "youtube-1080p": {"aspect": "16:9", "width": 1920, "height": 1080, "fps": 30},
    "youtube-4k": {"aspect": "16:9", "width": 3840, "height": 2160, "fps": 30},
    "reels-vertical": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30},
    "tiktok-vertical": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30},
    "shorts-vertical": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30},
    "instagram-square": {"aspect": "1:1", "width": 1080, "height": 1080, "fps": 30},
    "instagram-portrait": {"aspect": "4:5", "width": 1080, "height": 1350, "fps": 30},
    "linkedin-1080p": {"aspect": "16:9", "width": 1920, "height": 1080, "fps": 30},
    "broadcast-1080i": {"aspect": "16:9", "width": 1920, "height": 1080, "fps": 25},
    "cinema-2k": {"aspect": "2:1", "width": 2048, "height": 1024, "fps": 24},
}


def ffmpeg_bin():
    path = core.find_binary("ffmpeg", env_var="VIDEO_FFMPEG")
    if path:
        return path
    try:  # optional wheel fallback
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - depends on environment
        return None


def ffprobe_bin():
    path = core.find_binary("ffprobe", env_var="VIDEO_FFPROBE")
    if path:
        return path
    return None


def availability():
    ff, fp = ffmpeg_bin(), ffprobe_bin()
    return {
        "ffmpeg": ff,
        "ffprobe": fp,
        "ffmpeg_available": bool(ff),
        "ffprobe_available": bool(fp),
        "degraded": not (ff and fp),
        "note": None if (ff and fp) else (
            "ffprobe missing: probing falls back to parsing 'ffmpeg -i'. "
            "Install FFmpeg for full metadata coverage."
        ),
    }


def _require_ffmpeg():
    binary = ffmpeg_bin()
    if not binary:
        raise core.ToolError(
            "missing_dependency",
            "FFmpeg is not available",
            hint="Install FFmpeg (or `pip install imageio-ffmpeg`) or set VIDEO_FFMPEG to its path.",
            exit_code=core.EXIT_EXTERNAL,
        )
    return binary


# --------------------------------------------------------------------- probe
def _parse_ffmpeg_stderr(stderr):
    """Best-effort media metadata from `ffmpeg -i` when ffprobe is absent."""
    info = {"format": {}, "streams": []}
    container = re.search(r"Input #0, ([^,]+)", stderr)
    if container:
        info["format"]["format_name"] = container.group(1).strip()
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if dur:
        info["format"]["duration"] = f"{int(dur.group(1))*3600 + int(dur.group(2))*60 + float(dur.group(3)):.3f}"
    br = re.search(r"bitrate:\s*(\d+)\s*kb/s", stderr)
    if br:
        info["format"]["bit_rate"] = str(int(br.group(1)) * 1000)
    for m in re.finditer(r"Stream #\d+:\d+.*?: (Video|Audio|Subtitle): (.*)", stderr):
        kind, rest = m.group(1).lower(), m.group(2)
        stream = {"codec_type": kind, "codec_name": rest.split(",")[0].strip(), "parsed": True}
        size = re.search(r"(\d{2,5})x(\d{2,5})", rest)
        if size:
            stream["width"], stream["height"] = int(size.group(1)), int(size.group(2))
        fps = re.search(r"([\d.]+) fps", rest)
        if fps:
            stream["r_frame_rate"] = f"{round(float(fps.group(1))*1000)}/1000"
        pix = re.search(r"\b(yuv[a-z0-9]+|rgba|rgb24|bgra|gray|ya8|gbrp|p010le)\b", rest)
        if pix:
            stream["pix_fmt"] = pix.group(1)
        depth = re.search(r"(?:p10|p12|10-bit|12-bit|High 10|High 4:2:2)", rest)
        if depth:
            stream["bits_per_raw_sample"] = "10"
        sr = re.search(r"(\d{4,6}) Hz", rest)
        if sr:
            stream["sample_rate"] = sr.group(1)
        ch = re.search(r"(mono|stereo|5\.1|7\.1)", rest)
        if ch:
            stream["channel_layout"] = ch.group(1)
            stream["channels"] = {"mono": 1, "stereo": 2, "5.1": 6, "7.1": 8}[ch.group(1)]
        sbr = re.search(r", (\d+) kb/s", rest)
        if sbr:
            stream["bit_rate"] = str(int(sbr.group(1)) * 1000)
        field = re.search(r"\b(tb|tbr|progressive|interlaced)\b", rest)
        if field and field.group(1) in ("progressive", "interlaced"):
            stream["field_order"] = "progressive" if field.group(1) == "progressive" else "tt"
        info["streams"].append(stream)
    return info


def probe(path, timeout=DEFAULT_TIMEOUT):
    """Return normalised technical metadata for a media file."""
    media = core.ensure_file(path, "media file")
    probe_bin = ffprobe_bin()
    raw = None
    if probe_bin:
        rc, out, err = core.run_bounded(
            [probe_bin, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(media)], timeout=timeout)
        if rc != 0:
            raise core.external_error(
                f"ffprobe could not read {media.name}",
                hint=err.strip().splitlines()[-1] if err.strip() else None,
            )
        raw = json.loads(out or "{}")
    else:
        binary = _require_ffmpeg()
        rc, out, err = core.run_bounded([binary, "-hide_banner", "-i", str(media)], timeout=timeout)
        if "Invalid data" in err or "No such file" in err:
            raise core.external_error(f"FFmpeg could not read {media.name}",
                                      hint=err.strip().splitlines()[-1] if err.strip() else None)
        raw = _parse_ffmpeg_stderr(err)

    fmt = raw.get("format", {}) or {}
    streams = raw.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def rate(value):
        try:
            if isinstance(value, str) and "/" in value:
                num, den = value.split("/")
                return round(float(num) / float(den), 3) if float(den) else None
            return round(float(value), 3)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    duration = None
    for candidate in (fmt.get("duration"), (video or {}).get("duration"), (audio or {}).get("duration")):
        if candidate:
            try:
                duration = round(float(candidate), 3)
                break
            except (TypeError, ValueError):
                continue

    result = {
        "path": str(media),
        "size_bytes": int(fmt.get("size") or media.stat().st_size),
        "container": (fmt.get("format_name") or "").split(",")[0] or None,
        "duration_seconds": duration,
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate") or "").isdigit() else None,
        "has_video": bool(video),
        "has_audio": bool(audio),
        "video": None,
        "audio": None,
        "probe_source": "ffprobe" if probe_bin else "ffmpeg-stderr",
    }
    if video:
        result["video"] = {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": rate(video.get("r_frame_rate")) or rate(video.get("avg_frame_rate")),
            "pix_fmt": video.get("pix_fmt"),
            "bit_depth": int(video["bits_per_raw_sample"]) if str(video.get("bits_per_raw_sample") or "").isdigit() else None,
            "color_space": video.get("color_space"),
            "color_transfer": video.get("color_transfer"),
            "color_primaries": video.get("color_primaries"),
            "field_order": video.get("field_order"),
            "nb_frames": int(video["nb_frames"]) if str(video.get("nb_frames") or "").isdigit() else None,
            "sample_aspect_ratio": video.get("sample_aspect_ratio"),
            "is_interlaced": (video.get("field_order") not in (None, "progressive")),
            "has_alpha": any(x in (video.get("pix_fmt") or "") for x in ("a", "rgba", "ya", "argb"))
                        and "gray" not in (video.get("pix_fmt") or ""),
            "duration_seconds": rate(video.get("duration")) if video.get("duration") else None,
        }
        if result["video"]["width"] and result["video"]["height"]:
            g = _gcd(result["video"]["width"], result["video"]["height"])
            result["video"]["aspect_ratio"] = f"{result['video']['width']//g}:{result['video']['height']//g}"
    if audio:
        result["audio"] = {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio["sample_rate"]) if str(audio.get("sample_rate") or "").isdigit() else None,
            "channels": audio.get("channels"),
            "channel_layout": audio.get("channel_layout"),
            "bit_rate": int(audio["bit_rate"]) if str(audio.get("bit_rate") or "").isdigit() else None,
        }
    findings = []
    if not video and not audio:
        findings.append(core.finding("fail", "no_media_streams", "file contains no audio or video stream"))
    if video and result["video"]["is_interlaced"]:
        findings.append(core.finding("warn", "interlaced_source",
                                     "interlaced source detected; deinterlace before finishing"))
    if result["duration_seconds"] in (None, 0):
        findings.append(core.finding("warn", "no_duration",
                                     "duration could not be read; timecode maths may be rejected"))
    if video and video.get("field_order") is None and (result["video"]["fps"] or 0) > 120:
        findings.append(core.finding("warn", "implausible_fps", "frame rate above 120 fps reported"))
    result["findings"] = findings
    result["summary"] = core.summarise_findings(findings)
    return result


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a or 1


# ---------------------------------------------------------------- thumbnails
def thumbnails(path, out_dir, count=9, width=320, timeout=DEFAULT_TIMEOUT):
    """Extract an evenly spaced still strip for review (contact sheet source)."""
    binary = _require_ffmpeg()
    media = core.ensure_file(path, "media file")
    out = core.ensure_dir(out_dir, create=True)
    count = max(1, min(int(count), 60))
    meta = probe(media, timeout=timeout)
    duration = meta["duration_seconds"]
    if not duration:
        raise core.validation_error("cannot sample thumbnails without a readable duration")
    created = []
    for i in range(count):
        ts = duration * (i + 0.5) / count
        target = out / f"thumb-{i+1:02d}.jpg"
        rc, _, err = core.run_bounded(
            [binary, "-v", "error", "-y", "-ss", f"{ts:.3f}", "-i", str(media),
             "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", "3", str(target)],
            timeout=timeout)
        if rc != 0 or not target.exists():
            raise core.external_error(f"thumbnail extraction failed at {ts:.2f}s",
                                      hint=err.strip()[-300:] or None)
        created.append({"index": i + 1, "timestamp_seconds": round(ts, 3),
                        "timecode": core.seconds_to_tc(ts, meta["video"]["fps"] or 25.0),
                        "path": str(target)})
    return {"source": str(media), "count": len(created), "width": width, "thumbs": created}


# ----------------------------------------------------------------- quality QA
def _run_filter_scan(path, audio_filter=None, video_filter=None, timeout=DEFAULT_TIMEOUT):
    binary = _require_ffmpeg()
    cmd = [binary, "-hide_banner", "-nostdin", "-i", str(path)]
    if audio_filter:
        cmd += ["-af", audio_filter, "-f", "null", "-"]
    else:
        cmd += ["-vf", video_filter, "-an", "-f", "null", "-"]
    rc, out, err = core.run_bounded(cmd, timeout=timeout)
    if rc != 0:
        raise core.external_error("FFmpeg analysis pass failed", hint=err.strip()[-400:] or None)
    return err


def qa_scan(path, black_threshold=0.10, black_min_duration=0.5, silence_db=-50.0,
            silence_min_duration=2.0, freeze_min_duration=2.0, timeout=DEFAULT_TIMEOUT):
    """Detect black frames, freeze frames, silence and loudness in one FFmpeg pass
    per stream class. Returns structured findings suitable for the qa gate."""
    media = core.ensure_file(path, "media file")
    meta = probe(media, timeout=timeout)
    findings = []
    evidence = {}

    if meta["has_video"]:
        vf = (f"blackdetect=d={black_min_duration}:pix_th={black_threshold},"
              f"freezedetect=n=0.003:d={freeze_min_duration}")
        err = _run_filter_scan(media, video_filter=vf, timeout=timeout)
        blacks = [{"start": float(m.group(1)), "end": float(m.group(2)),
                   "duration": float(m.group(3))}
                  for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", err)]
        freezes = [{"start": float(m.group(1)), "duration": float(m.group(2))}
                   for m in re.finditer(r"freeze_start:\s*([\d.]+).*?freeze_duration:\s*([\d.]+)", err)]
        evidence["black_segments"] = blacks
        evidence["freeze_segments"] = freezes
        fps = meta["video"]["fps"] or 25.0
        for seg in blacks:
            findings.append(core.finding(
                "fail", "black_frames",
                f"black segment {core.seconds_to_tc(seg['start'], fps)} for {seg['duration']:.2f}s",
                start_timecode=core.seconds_to_tc(seg["start"], fps), duration=seg["duration"]))
        for seg in freezes:
            findings.append(core.finding(
                "warn", "frozen_frames",
                f"frozen picture from {core.seconds_to_tc(seg['start'], fps)} for {seg['duration']:.2f}s",
                start_timecode=core.seconds_to_tc(seg["start"], fps), duration=seg["duration"]))
        if meta["video"]["is_interlaced"]:
            findings.append(core.finding("warn", "interlaced_source", "interlaced source needs deinterlacing"))

    if meta["has_audio"]:
        af = f"silencedetect=n={silence_db}dB:d={silence_min_duration},ebur128=peak=true"
        err = _run_filter_scan(media, audio_filter=af, timeout=timeout)
        silences = []
        start = None
        for line in err.splitlines():
            m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
            if m:
                start = float(m.group(1))
            m = re.search(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", line)
            if m and start is not None:
                silences.append({"start": start, "end": float(m.group(1)),
                                 "duration": float(m.group(2))})
                start = None
        integrated = [float(x) for x in re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", err)]
        true_peak = [float(x) for x in re.findall(r"Peak:\s*(-?[\d.]+)\s*dBFS", err)]
        evidence["silence_segments"] = silences
        evidence["integrated_lufs"] = integrated[-1] if integrated else None
        evidence["true_peak_dbfs"] = max(true_peak) if true_peak else None
        total = meta["duration_seconds"] or 0
        silent_total = sum(s["duration"] for s in silences if s["start"] >= 0)
        if total and silent_total >= total * 0.9:
            findings.append(core.finding("fail", "audio_silent", "audio track is silent for most of the runtime"))
        elif silences:
            findings.append(core.finding("info", "silence_detected",
                                         f"{len(silences)} silent passage(s), {silent_total:.1f}s total"))
        if evidence["true_peak_dbfs"] is not None and evidence["true_peak_dbfs"] > -1.0:
            findings.append(core.finding("fail", "true_peak_over",
                                         f"true peak {evidence['true_peak_dbfs']:.1f} dBFS exceeds -1.0 dBFS",
                                         measured=evidence["true_peak_dbfs"]))
        target = -14.0
        lufs = evidence["integrated_lufs"]
        if lufs is not None and abs(lufs - target) > 4.0:
            findings.append(core.finding(
                "warn", "loudness_off_target",
                f"integrated loudness {lufs:.1f} LUFS differs from the -14 LUFS web target by "
                f"{abs(lufs - target):.1f} LU", measured=lufs, target=target))
    else:
        findings.append(core.finding("warn", "no_audio_stream", "no audio stream; captions or VO may be missing"))

    if meta["duration_seconds"] and meta["duration_seconds"] < 1.0:
        findings.append(core.finding("warn", "very_short",
                                     f"runtime {meta['duration_seconds']:.2f}s is shorter than 1s"))
    return {
        "path": str(media),
        "probe": {k: meta[k] for k in ("container", "duration_seconds", "has_video", "has_audio", "video", "audio")},
        "evidence": evidence,
        "findings": findings,
        "summary": core.summarise_findings(findings),
        "thresholds": {"black_threshold": black_threshold, "black_min_duration": black_min_duration,
                       "silence_db": silence_db, "silence_min_duration": silence_min_duration,
                       "freeze_min_duration": freeze_min_duration, "loudness_target_lufs": -14.0},
    }


# ------------------------------------------------------- aspect / versioning
def digest_master_config(config, platforms=None):
    """Expand a master configuration into a deterministic delivery matrix."""
    errors = core.validate_schema(config, _load_schema("asset-master-config.schema.json"))
    if errors:
        raise core.validation_error("master configuration failed validation",
                                    hint="Fix the listed fields.",
                                    details={"errors": errors[:40]})
    targets = platforms or config.get("platforms") or list(config.get("variants") or [])
    rows = []
    for name in targets:
        preset = PLATFORM_PRESETS.get(name)
        if preset is None:
            raise core.validation_error(
                f"unknown platform preset: {name}",
                hint="Known presets: " + ", ".join(sorted(PLATFORM_PRESETS)))
        langs = config.get("languages") or ["und"]
        for lang in langs:
            rows.append({
                "variant_id": f"{name}-{lang}".replace("_", "-"),
                "platform": name,
                **preset,
                "language": lang,
                "caption_file": config.get("captions", {}).get(lang),
                "duration_limit_seconds": config.get("duration_limits", {}).get(name),
                "compliance": config.get("compliance", []),
            })
    return {"master": config.get("master", {}), "variant_count": len(rows), "variants": rows}


def _load_schema(name):
    path = Path(__file__).resolve().parent / name
    return core.read_json(path, "schema")


# --------------------------------------------------------------- transforms
def _aspect_geometry(src_w, src_h, aspect):
    num, den = TARGET_ASPECTS[aspect]
    target = num / den
    source = src_w / src_h
    if abs(target - source) < 1e-4:
        return {"mode": "none", "crop": None}
    if target < source:   # target is narrower -> crop the sides
        new_w = int(round(src_h * target / 2) * 2)
        x = int((src_w - new_w) / 2 / 2) * 2
        return {"mode": "crop", "crop": f"{new_w}:{src_h}:{x}:0"}
    new_h = int(round(src_w / target / 2) * 2)
    y = int((src_h - new_h) / 2 / 2) * 2
    return {"mode": "crop", "crop": f"{src_w}:{new_h}:0:{y}"}


def reframe(src, out, aspect="9:16", mode="crop", blur_pad=True,
            crf=20, preset="medium", timeout=DEFAULT_TIMEOUT):
    """Render one aspect-ratio version of a source clip.

    mode = 'crop' (centre crop, no distortion) or 'pad' (letterbox with blurred
    fill background when blur_pad is true, solid black otherwise).
    """
    binary = _require_ffmpeg()
    src_path = core.ensure_file(src, "source media")
    meta = probe(src_path, timeout=timeout)
    if not meta["has_video"]:
        raise core.validation_error("reframing requires a video stream")
    if aspect not in TARGET_ASPECTS:
        raise core.validation_error(f"unsupported aspect ratio {aspect}",
                                    hint="Supported: " + ", ".join(sorted(TARGET_ASPECTS)))
    w, h = meta["video"]["width"], meta["video"]["height"]
    num, den = TARGET_ASPECTS[aspect]
    geo = _aspect_geometry(w, h, aspect)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target_w = min(w, int(round(h * num / den / 2) * 2)) if num / den < w / h else w
    target_h = int(round(target_w * den / num / 2) * 2)

    if mode == "crop":
        vf = f"crop={geo['crop']},scale={target_w}:{target_h}:flags=lanczos"
    elif mode == "pad":
        if blur_pad:
            vf = (f"split=2[bg][fg];"
                  f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                  f"crop={target_w}:{target_h},gblur=sigma=24[bg2];"
                  f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg2];"
                  f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2")
        else:
            vf = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                  f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black")
    else:
        raise core.validation_error(f"unknown reframe mode {mode!r}", "Use 'crop' or 'pad'.")

    cmd = [binary, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(src_path),
           "-filter_complex", vf, "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
           str(out_path)]
    rc, _, err = core.run_bounded(cmd, timeout=timeout)
    if rc != 0 or not out_path.exists():
        core.cleanup_temp([out_path])
        raise core.external_error("FFmpeg reframe failed", hint=err.strip()[-400:] or None)
    result = probe(out_path, timeout=timeout)
    return {"source": str(src_path), "output": str(out_path), "aspect": aspect, "mode": mode,
            "crop_applied": geo["crop"], "output_size": [result["video"]["width"], result["video"]["height"]],
            "duration_seconds": result["duration_seconds"], "bytes": result["size_bytes"]}


def transcode(src, out, crf=20, preset="medium", audio_bitrate="160k",
              max_width=None, timeout=DEFAULT_TIMEOUT):
    """Deterministic H.264/AAC review render (no creative changes)."""
    binary = _require_ffmpeg()
    src_path = core.ensure_file(src, "source media")
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = ["-vf", f"scale='min({int(max_width)},iw)':-2"] if max_width else []
    cmd = [binary, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(src_path),
           *vf, "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-c:a", "aac", "-b:a", audio_bitrate, str(out_path)]
    rc, _, err = core.run_bounded(cmd, timeout=timeout)
    if rc != 0 or not out_path.exists():
        core.cleanup_temp([out_path])
        raise core.external_error("FFmpeg transcode failed", hint=err.strip()[-400:] or None)
    return {"source": str(src_path), "output": str(out_path), "bytes": out_path.stat().st_size}


# ------------------------------------------------------------------ subtitles
SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text):
    """Parse SRT/VTT text into cue dictionaries. Raises on malformed timing."""
    cues, warnings = [], []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith(("WEBVTT", "NOTE")):
            continue
        idx = 0
        if re.fullmatch(r"\d+", lines[0].strip()):
            index = int(lines[0].strip())
            idx = 1
        else:
            index = len(cues) + 1
            warnings.append(f"cue {index}: missing numeric index")
        if idx >= len(lines):
            warnings.append(f"cue {index}: no timing line")
            continue
        m = SRT_TIME.search(lines[idx])
        if not m:
            raise core.validation_error(f"cue {index}: unparseable timing line: {lines[idx][:80]!r}")
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = "\n".join(lines[idx + 1:]).strip()
        if not body:
            warnings.append(f"cue {index}: empty text")
        if end <= start:
            warnings.append(f"cue {index}: end <= start")
        text_body = re.sub(r"<[^>]+>", "", body)
        cues.append({"index": index, "start": round(start, 3), "end": round(end, 3),
                     "duration": round(end - start, 3), "text": text_body})
    return cues, warnings


def _fmt_srt_time(seconds):
    seconds = max(0.0, float(seconds))
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues, path):
    blocks = []
    for i, cue in enumerate(sorted(cues, key=lambda c: c["start"]), 1):
        blocks.append(f"{i}\n{_fmt_srt_time(cue['start'])} --> {_fmt_srt_time(cue['end'])}\n{cue['text'].strip()}\n")
    return core.atomic_write_text(path, "\n".join(blocks))


def retime_srt(cues, offset=0.0, scale=1.0, min_duration=0.8, max_cps=21.0):
    """Shift/scale caption timing and report readability problems."""
    findings = []
    out = []
    for cue in cues:
        start = cue["start"] * scale + offset
        end = cue["end"] * scale + offset
        if start < 0:
            findings.append(core.finding("warn", "negative_start",
                                         f"cue {cue['index']} starts before zero after retime"))
            start = 0.0
        if end - start < min_duration:
            end = start + min_duration
            findings.append(core.finding("warn", "min_duration_applied",
                                         f"cue {cue['index']} padded to {min_duration}s"))
        chars = len(re.sub(r"\s+", " ", cue["text"]).strip())
        cps = chars / max(1e-6, end - start)
        if cps > max_cps:
            findings.append(core.finding("warn", "reading_speed",
                                         f"cue {cue['index']} reads at {cps:.1f} chars/s (limit {max_cps})",
                                         chars_per_second=round(cps, 1)))
        if len(cue["text"]) > 84 or cue["text"].count("\n") > 1:
            findings.append(core.finding("info", "line_length",
                                         f"cue {cue['index']} has {len(cue['text'])} characters"))
        out.append({**cue, "start": round(start, 3), "end": round(end, 3),
                    "duration": round(end - start, 3), "chars_per_second": round(cps, 1)})
    overlaps = sum(1 for a, b in zip(out, out[1:]) if b["start"] < a["end"])
    if overlaps:
        findings.append(core.finding("fail", "overlapping_cues", f"{overlaps} overlapping cue pair(s)"))
    return out, findings
