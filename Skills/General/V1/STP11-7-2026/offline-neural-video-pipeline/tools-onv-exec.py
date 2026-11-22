"""Stage executors for the offline neural video pipeline.

Executors
---------
internal       preflight / validate / package stages delegated to other modules
ffmpeg-prepare  probe and normalise declared inputs
ffmpeg-proxy    deterministic ffmpeg implementations of each neural operation
ffmpeg-audio    loudness normalisation / mix / channel layout
ffmpeg-compose  concat, trim and programme assembly
ffmpeg-colour   finishing pass
ffmpeg-caption  subtitle burn-in or sidecar writer
ffmpeg-encode   delivery encode
backend         dispatch to an operator-configured local neural runner

Nothing here downloads, phones home, or fabricates model output. A neural stage
that has no configured backend is never simulated silently: it either blocks,
falls back to an explicitly labelled proxy, or is skipped, as declared by
``spec.fallback.on_backend_unavailable``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

SUPPORTED_TEMPLATE_FIELDS = (
    "job_id",
    "model_path",
    "model_dir",
    "input_image",
    "input_video",
    "input_audio",
    "mask",
    "out_dir",
    "width",
    "height",
    "fps",
    "frames",
    "duration_s",
    "duration",
    "seed",
    "steps",
    "scale",
    "interp_exp",
    "prompt",
    "negative_prompt",
    "preset",
    "capability",
)


# ---------------------------------------------------------------------------
# job state
# ---------------------------------------------------------------------------
class JobState:
    def __init__(self, path, job_id, tool_version):
        self.path = Path(path)
        self.job_id = job_id
        self.data = {
            "job_id": job_id,
            "tool_version": tool_version,
            "created_at": None,
            "updated_at": None,
            "status": "initialised",
            "current_media": None,
            "stages": {},
            "artifacts": [],
            "notes": [],
        }

    def load(self, common):
        if not self.path.exists():
            return self
        try:
            data = common.json_load(self.path, label="job state")
            if isinstance(data, dict):
                self.data.update(data)
        except common.OnvError:
            self.data["notes"].append("state file was unreadable and has been reset")
        return self

    def save(self, common):
        self.data["updated_at"] = common.utcnow()
        self.data["created_at"] = self.data.get("created_at") or self.data["updated_at"]
        common.json_write(self.path, self.data)

    def stage(self, stage_id):
        return self.data["stages"].setdefault(
            stage_id, {"status": "pending", "attempts": 0, "started_at": None, "ended_at": None, "message": None, "outputs": []}
        )

    def record(self, stage_id, status, message=None, outputs=None, extra=None):
        entry = self.stage(stage_id)
        entry["status"] = status
        entry["message"] = message
        if outputs:
            entry["outputs"] = outputs
            for output in outputs:
                if output not in self.data["artifacts"]:
                    self.data["artifacts"].append(output)
        if extra:
            entry.update(extra)
        if status in ("succeeded", "skipped"):
            entry["ended_at"] = entry.get("ended_at") or entry.get("started_at")
        return entry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _probe(ctx, runner, path):
    common = ctx.common
    ffprobe_bin = ctx.config.get("ffprobe", "ffprobe")
    result = runner.run(
        [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout_s=180,
        log_name="probe-%s" % Path(path).stem,
    )
    try:
        doc = json.loads(result["stdout"])
    except ValueError:
        common.fail("E_RUNTIME", "ffprobe output unparsable for %s" % path)
    video = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in doc.get("streams", []) if s.get("codec_type") == "audio"), None)
    info = {
        "path": str(path),
        "duration_s": _num((doc.get("format") or {}).get("duration")),
        "size_bytes": _num((doc.get("format") or {}).get("size")),
        "format_name": (doc.get("format") or {}).get("format_name"),
        "has_video": video is not None,
        "has_audio": audio is not None,
    }
    if video:
        info.update(
            {
                "width": video.get("width"),
                "height": video.get("height"),
                "fps": _fps(video),
                "video_codec": video.get("codec_name"),
                "pix_fmt": video.get("pix_fmt"),
                "nb_frames": _num(video.get("nb_frames")),
            }
        )
    if audio:
        info.update({"audio_codec": audio.get("codec_name"), "sample_rate": _num(audio.get("sample_rate")), "channels": _num(audio.get("channels"))})
    return info


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


def _even(value):
    value = int(value)
    return value if value % 2 == 0 else value - 1


def _run_ffmpeg(ctx, runner, argv, stage_id, timeout_s, retries=0):
    ffmpeg_bin = ctx.config.get("ffmpeg", "ffmpeg")
    full = [ffmpeg_bin, "-hide_banner", "-nostdin", "-y"] + [str(a) for a in argv]
    return runner.run(full, timeout_s=timeout_s, retries=retries, log_name=stage_id, cwd=ctx.workdir)


def _images(inputs):
    return [i for i in inputs if str(i.get("role", "")).lower() in ("image", "still", "portrait", "reference")]


def _videos(inputs):
    return [i for i in inputs if str(i.get("role", "")).lower() in ("video", "source", "clip", "driving", "reference-video")]


def _audios(inputs):
    return [i for i in inputs if str(i.get("role", "")).lower() in ("audio", "voice", "dialogue", "music", "speech")]


def _first(paths, default=None):
    return paths[0] if paths else default


# ---------------------------------------------------------------------------
# executors
# ---------------------------------------------------------------------------
def stage_prepare(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    inputs = spec.get("inputs") or []
    probed = []
    for entry in inputs:
        path = Path(entry["path"])
        if not path.exists():
            common.fail("E_INPUT", "input missing: %s" % path, role=entry.get("role"))
        info = _probe(ctx, runner, path)
        if entry.get("sha256"):
            actual = common.sha256_file(path)
            if actual.lower() != str(entry["sha256"]).lower():
                common.fail(
                    "E_INPUT",
                    "input hash mismatch for %s" % path.name,
                    expected=entry["sha256"],
                    actual=actual,
                )
        info["role"] = entry.get("role")
        probed.append(info)
    centre = ctx.paths["scratch"] / "inputs.json"
    common.json_write(centre, probed)
    images = _images(inputs)
    videos = _videos(inputs)
    primary = _first(videos) or _first(images)
    if primary:
        state.data["current_media"] = str(primary["path"])
    else:
        state.data["current_media"] = None
    return {
        "status": "succeeded",
        "message": "prepared %d input(s)" % len(probed),
        "outputs": [str(centre)],
        "facts": {"inputs": probed, "current_media": state.data["current_media"]},
    }


def stage_proxy(ctx, runner, spec, plan, stage, state):
    """Deterministic ffmpeg implementation of a neural operation."""
    common = ctx.common
    budget = plan["budget"]
    width = _even(budget["resolution"]["width"])
    height = _even(budget["resolution"]["height"])
    fps = int(budget["fps"])
    frames = int(budget["frames"])
    duration = float(budget["duration_s"])
    op = stage["params"].get("op", plan["operation"])
    proxy = stage["params"].get("proxy_filter") or ""
    scale_flags = (ctx.preset or {}).get("proxy_scale_flags", "bicubic")
    out = ctx.paths["scratch"] / ("%s.mp4" % stage["id"])

    inputs = spec.get("inputs") or []
    images = _images(inputs)
    videos = _videos(inputs)
    source = _first(videos) or _first(images)
    allow_placeholder = bool((spec.get("proxy") or {}).get("allow_placeholder"))
    placeholder = False

    if source is None:
        if not allow_placeholder:
            common.fail(
                "E_BLOCKED",
                "proxy path for op '%s' needs a reference image or video input; none declared" % op,
                remedy="Add an image/video input, set spec.proxy.allow_placeholder=true, or configure spec.backend.command.",
            )
        placeholder = True

    scale_pad = (
        "scale=%d:%d:force_original_aspect_ratio=decrease:flags=%s,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1" % (width, height, scale_flags, width, height)
    )

    if op in ("generate", "animate"):
        if placeholder:
            argv = [
                "-f", "lavfi", "-i", "testsrc2=size=%dx%d:rate=%d:duration=%.3f" % (width, height, fps, duration),
                "-vf", "format=yuv420p",
            ]
            note = "placeholder test pattern (spec.proxy.allow_placeholder=true)"
        else:
            if Path(source["path"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff") or source in images:
                zoom = "zoompan=z='min(zoom+0.0012,1.25)':d=%d:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=%dx%d:fps=%d" % (
                    max(2, frames),
                    width,
                    height,
                    fps,
                )
                argv = ["-loop", "1", "-i", str(source["path"]), "-t", "%.3f" % duration, "-vf", "%s,%s,format=yuv420p" % (scale_pad, zoom)]
            else:
                argv = ["-i", str(source["path"]), "-vf", "%s,format=yuv420p" % scale_pad]
            note = "Ken Burns / reframe animatic from %s" % Path(source["path"]).name
    elif op == "interpolate":
        if source is None:
            common.fail("E_BLOCKED", "interpolate proxy requires a video or image input")
        argv = [
            "-i", str(source["path"]),
            "-vf", "%s,minterpolate=fps=%d:mi_mode=mci:mc_mode=aobmc:vsbmc=1,format=yuv420p" % (scale_pad, fps),
            "-t", "%.3f" % duration,
        ]
        note = "minterpolate frame synthesis at %d fps" % fps
    elif op == "restore":
        if source is None:
            common.fail("E_BLOCKED", "restore proxy requires a video or image input")
        argv = [
            "-i", str(source["path"]),
            "-vf", "hqdn3d=4:3:6:4.5,deflicker=mode=pm:size=10,%s,unsharp=5:5:0.4:5:5:0.0,format=yuv420p" % scale_pad,
            "-t", "%.3f" % duration,
        ]
        note = "hqdn3d + deflicker + lanczos rescale restoration proxy"
    elif op == "edit":
        if source is None:
            common.fail("E_BLOCKED", "edit proxy requires a video or image input")
        argv = ["-i", str(source["path"]), "-vf", "%s,eq=brightness=0.05:contrast=1.08:saturation=1.05,format=yuv420p" % scale_pad, "-t", "%.3f" % duration]
        note = "global tone relight approximation (no object removal)"
    elif op == "transform":
        if source is None:
            common.fail("E_BLOCKED", "transform proxy requires a video or image input")
        argv = ["-i", str(source["path"]), "-vf", "%s,curves=preset=medium_contrast,hue=s=1.08,format=yuv420p" % scale_pad, "-t", "%.3f" % duration]
        note = "deterministic curve/hue restyle proxy"
    else:
        common.fail("E_BLOCKED", "no proxy implementation for operation '%s'" % op, operation=op)

    argv += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    _run_ffmpeg(ctx, runner, argv, stage["id"], stage["timeout_s"])
    if not out.exists() or out.stat().st_size == 0:
        common.fail("E_RUNTIME", "proxy stage produced no output: %s" % out)
    state.data["current_media"] = str(out)
    state.data.setdefault("proxy_stages", []).append(stage["id"])
    return {"status": "succeeded", "message": "proxy %s: %s" % (op, note), "outputs": [str(out)], "facts": {"placeholder": placeholder}}


def command_from_template(ctx, spec, plan, stage):
    """Resolve the backend argv from spec.backend.command or the registry template."""
    common = ctx.common
    routing = plan.get("routing", {})
    model = routing.get("model") or {}
    template = (spec.get("backend") or {}).get("command") or (model.get("backend") or {}).get("command_template")
    if not template:
        common.fail(
            "E_BACKEND",
            "no backend command configured for model '%s'" % (routing.get("model_id") or "n/a"),
            remedy="Set spec.backend.command with {output} and the input placeholders, or use mode=proxy.",
        )
    budget = plan["budget"]
    inputs = spec.get("inputs") or []
    images, videos, audios = _images(inputs), _videos(inputs), _audios(inputs)
    out = ctx.paths["scratch"] / ("%s%s" % (stage["id"], _container_ext(spec)))
    fields = {
        "job_id": spec["job_id"],
        "model_path": str(Path(spec["models"]["root"])),
        "model_dir": str(Path(spec["models"]["root"])),
        "input_image": _first([i["path"] for i in images], ""),
        "input_video": _first([i["path"] for i in videos], ""),
        "input_audio": _first([i["path"] for i in audios], ""),
        "mask": _first([i["path"] for i in inputs if str(i.get("role", "")).lower() == "mask"], ""),
        "out_dir": str(ctx.paths["scratch"]),
        "width": budget["resolution"]["width"],
        "height": budget["resolution"]["height"],
        "fps": budget["fps"],
        "frames": budget["frames"],
        "duration_s": budget["duration_s"],
        "duration": budget["duration_s"],
        "seed": (spec.get("params") or {}).get("seed", 0),
        "steps": (spec.get("params") or {}).get("steps", (ctx.preset or {}).get("steps") or 0),
        "scale": (spec.get("params") or {}).get("scale", 2),
        "interp_exp": (spec.get("params") or {}).get("interp_exp", 2),
        "prompt": spec.get("prompt") or "",
        "negative_prompt": (spec.get("params") or {}).get("negative_prompt") or "",
        "preset": spec["preset"],
        "capability": spec["capability"],
        "output": str(out),
    }
    is_list = isinstance(template, list)
    parts = list(template) if is_list else _split_template(template)
    resolved = []
    try:
        for part in parts:
            resolved.append(str(part).format(**fields))
    except KeyError as exc:
        common.fail(
            "E_SPEC",
            "backend command template uses unknown placeholder %s" % exc,
            supported=list(SUPPORTED_TEMPLATE_FIELDS) + ["output"],
        )
    if not any(fields["output"] in part for part in resolved):
        resolved.append(str(fields["output"]))
    return resolved, Path(fields["output"]), fields


def _split_template(template):
    import shlex

    try:
        return shlex.split(template)
    except ValueError as exc:
        raise RuntimeError("backend command template is not shell-parsable: %s" % exc)


def _container_ext(spec):
    container = (spec.get("delivery") or {}).get("container") or "mp4"
    return "." + container.lstrip(".")


def stage_backend(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    if stage["approval_required"] and not ctx.approve:
        common.fail(
            "E_APPROVAL",
            "neural backend dispatch for stage '%s' requires explicit approval" % stage["id"],
            remedy="Re-run with --approve after reviewing the resolved command via 'plan --show-command'.",
        )
    argv, out, fields = command_from_template(ctx, spec, plan, stage)
    executable = shutil.which(argv[0]) if not os.path.sep in argv[0] else argv[0]
    if not executable or not os.path.exists(str(executable)):
        common.fail(
            "E_BACKEND",
            "backend executable not found: %s" % argv[0],
            command=argv[:6],
            remedy="Pin the runner entry point in spec.backend.command or put it on PATH.",
        )
    started = time.time()
    runner.run(argv, timeout_s=stage["timeout_s"], retries=stage["retries"], log_name=stage["id"], cwd=ctx.workdir)
    if not out.exists() or out.stat().st_size == 0:
        common.fail(
            "E_BACKEND",
            "backend finished without producing the declared output",
            expected=str(out),
            elapsed_s=round(time.time() - started, 1),
            remedy="Confirm the runner writes to the {output} path passed on its command line.",
        )
    state.data["current_media"] = str(out)
    return {
        "status": "succeeded",
        "message": "backend dispatch completed: %s" % Path(argv[0]).name,
        "outputs": [str(out)],
        "facts": {"command": argv, "fields": {k: v for k, v in fields.items() if k not in ("prompt",)}},
    }


def stage_audio(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    media = state.data.get("current_media")
    audio_cfg = spec.get("audio") if isinstance(spec.get("audio"), dict) else {}
    target_lufs = float(audio_cfg.get("target_lufs") or (ctx.preset or {}).get("loudness_lufs") or -16.0)
    out = ctx.paths["scratch"] / "audio-stage.mp4"
    inputs = spec.get("inputs") or []
    audios = _audios(inputs)
    extra = []
    filter_parts = []
    index = 1

    if not media and not audios:
        common.fail("E_BLOCKED", "audio stage has no media and no audio input to work from")

    if audios:
        extra = ["-i", str(audios[0]["path"])]
        filter_parts.append("[%d:a]loudnorm=I=%.1f:TP=-1.5:LRA=11[aout]" % (index, target_lufs))
        audio_map = "[aout]"
        video_source = media
    else:
        audio_map = None
        video_source = media

    argv = []
    if video_source:
        argv += ["-i", str(video_source)]
    argv += extra
    if filter_parts:
        argv += ["-filter_complex", ";".join(filter_parts), "-map", "0:v:0" if video_source else "0:v:0", "-map", audio_map]
    argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", str(out)]
    if audio_map is None and video_source:
        argv = [
            "-i", str(video_source),
            "-af", "loudnorm=I=%.1f:TP=-1.5:LRA=11" % target_lufs,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out),
        ]
    _run_ffmpeg(ctx, runner, argv, stage["id"], stage["timeout_s"])
    state.data["current_media"] = str(out)
    return {
        "status": "succeeded",
        "message": "audio normalised to %.1f LUFS%s" % (target_lufs, "" if not audios else " with %s" % Path(audios[0]["path"]).name),
        "outputs": [str(out)],
        "facts": {"target_lufs": target_lufs, "audio_inputs": [a["path"] for a in audios]},
    }


def stage_compose(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    compose = spec.get("compose") or {}
    clips = compose.get("clips") or []
    media = state.data.get("current_media")
    if not clips and media:
        clips = [media]
    if not clips:
        common.fail("E_BLOCKED", "compose stage has no clips")
    paths = []
    for clip in clips:
        path = Path(clip if isinstance(clip, str) else clip.get("path", ""))
        if not path.is_absolute():
            path = Path(ctx.workdir) / path
        if not path.exists():
            common.fail("E_INPUT", "compose clip missing: %s" % path)
        paths.append(path)
    out = ctx.paths["scratch"] / "composed.mp4"
    if len(paths) == 1:
        shutil.copyfile(str(paths[0]), str(out))
        state.data["current_media"] = str(out)
        return {"status": "succeeded", "message": "single clip passed through", "outputs": [str(out)], "facts": {"clips": [str(p) for p in paths]}}

    budget = plan["budget"]
    width = _even(budget["resolution"]["width"])
    height = _even(budget["resolution"]["height"])
    fps = int(budget["fps"])
    argv = []
    for path in paths:
        argv += ["-i", str(path)]
    scaled = []
    for index in range(len(paths)):
        scaled.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d[v%d]"
            % (index, width, height, width, height, fps, index)
        )
    concat_inputs = "".join("[v%d]" % i for i in range(len(paths)))
    filter_complex = ";".join(scaled) + ";%sconcat=n=%d:v=1:a=0[vout]" % (concat_inputs, len(paths))
    argv += ["-filter_complex", filter_complex, "-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    _run_ffmpeg(ctx, runner, argv, stage["id"], stage["timeout_s"])
    state.data["current_media"] = str(out)
    return {"status": "succeeded", "message": "concatenated %d clips" % len(paths), "outputs": [str(out)], "facts": {"clips": [str(p) for p in paths]}}


def stage_colour(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    media = state.data.get("current_media")
    if not media:
        common.fail("E_BLOCKED", "colour stage has no upstream media")
    out = ctx.paths["scratch"] / "graded.mp4"
    argv = [
        "-i", str(media),
        "-vf", "eq=contrast=1.05:saturation=1.02:gamma=1.0,colorlevels=rimin=0:rimax=0.98:gimin=0:gimax=0.98:bimin=0:bimax=0.98",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", str(out),
    ]
    _run_ffmpeg(ctx, runner, argv, stage["id"], stage["timeout_s"])
    state.data["current_media"] = str(out)
    return {"status": "succeeded", "message": "finishing pass applied", "outputs": [str(out)], "facts": {}}


def stage_caption(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    media = state.data.get("current_media")
    if not media:
        common.fail("E_BLOCKED", "caption stage has no upstream media")
    captions = spec.get("captions") if isinstance(spec.get("captions"), dict) else {}
    srt = captions.get("srt")
    cues = captions.get("cues") or []
    out = ctx.paths["scratch"] / "captioned.mp4"

    if not srt and not cues:
        common.fail(
            "E_INPUT",
            "caption stage needs spec.captions.srt (path) or spec.captions.cues (list of {start,end,text})",
            remedy="Write an SRT file and point spec.captions.srt at it, or pass cues inline.",
        )
    if cues and not srt:
        srt_path = ctx.paths["scratch"] / "cues.srt"
        srt_path.write_text(render_srt(cues), encoding="utf-8")
        srt = str(srt_path)
    srt_path = Path(srt)
    if not srt_path.is_absolute():
        srt_path = Path(ctx.workdir) / srt_path
    if not srt_path.exists():
        common.fail("E_INPUT", "caption file missing: %s" % srt_path)

    mode = captions.get("mode") or "burn"
    caps = ctx.ffmpeg_caps() if hasattr(ctx, "ffmpeg_caps") else {}
    has_libass = "subtitles" in (caps.get("filters") or set())
    if mode == "burn" and not has_libass:
        common.fail(
            "E_BLOCKED",
            "this ffmpeg build has no 'subtitles' filter (libass missing)",
            remedy="Install an ffmpeg build with libass, or set spec.captions.mode='sidecar'.",
        )
    if mode == "sidecar":
        sidecar = ctx.paths["scratch"] / (Path(media).stem + ".srt")
        shutil.copyfile(str(srt_path), str(sidecar))
        return {"status": "succeeded", "message": "subtitle sidecar written", "outputs": [str(sidecar)], "facts": {"mode": "sidecar"}}

    escaped = str(srt_path).replace("\\", "/").replace(":", "\\:").replace("'", "")
    argv = [
        "-i", str(media),
        "-vf", "subtitles=%s:force_style='FontSize=20,Outline=1'" % escaped,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", str(out),
    ]
    _run_ffmpeg(ctx, runner, argv, stage["id"], stage["timeout_s"])
    state.data["current_media"] = str(out)
    return {"status": "succeeded", "message": "captions burned in from %s" % srt_path.name, "outputs": [str(out)], "facts": {"mode": "burn"}}


def render_srt(cues):
    def ts(value):
        if isinstance(value, str) and (":" in value or "," in value):
            return value.replace(".", ",")
        total = float(value)
        hours = int(total // 3600)
        minutes = int((total % 3600) // 60)
        seconds = total % 60
        return "%02d:%02d:%06.3f" % (hours, minutes, seconds)

    lines = []
    for index, cue in enumerate(cues, start=1):
        text = str(cue.get("text", "")).strip()
        lines.append(str(index))
        lines.append("%s --> %s" % (ts(cue.get("start", 0)), ts(cue.get("end", 1))))
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def stage_encode(ctx, runner, spec, plan, stage, state):
    common = ctx.common
    media = state.data.get("current_media")
    if not media:
        common.fail("E_BLOCKED", "encode stage has no upstream media")
    target = ctx.output_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not ctx.overwrite:
        common.fail(
            "E_APPROVAL",
            "refusing to overwrite existing output: %s" % target,
            remedy="Pass --overwrite together with --approve, or change spec.job_id / spec.output.filename.",
        )
    params = stage["params"]
    budget = plan["budget"]
    width = _even(budget["resolution"]["width"])
    height = _even(budget["resolution"]["height"])
    fps = int(budget["fps"])
    codec = params.get("video_codec") or "h264"
    encoder = {"h264": "libx264", "hevc": "libx265", "h265": "libx265", "av1": "libsvtav1", "vp9": "libvpx-vp9"}.get(codec, "libx264")
    audio_encoder = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame"}.get(params.get("audio_codec") or "aac", "aac")

    vf = "scale=%d:%d:force_original_aspect_ratio=decrease:flags=%s,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d,format=yuv420p" % (
        width,
        height,
        (ctx.preset or {}).get("proxy_scale_flags", "bicubic"),
        width,
        height,
        fps,
    )
    argv = [
        "-i", str(media),
        "-vf", vf,
        "-c:v", encoder,
        "-preset", str(params.get("x264_preset") or "medium"),
        "-crf", str(params.get("crf") or 20),
        "-pix_fmt", "yuv420p",
        "-c:a", audio_encoder,
        "-b:a", "%dk" % int(params.get("audio_bitrate_kbps") or 160),
        "-movflags", "+faststart",
        str(target),
    ]
    _run_ffmpeg(ctx, runner, argv, stage["id"], stage["timeout_s"])
    if not target.exists():
        common.fail("E_RUNTIME", "encode produced no file at %s" % target)
    size_mb = target.stat().st_size / 1024.0 / 1024.0
    limit_mb = int(spec["limits"]["max_output_mb"])
    if size_mb > limit_mb:
        common.fail(
            "E_VALIDATION",
            "encoded output %.1f MB exceeds spec.limits.max_output_mb=%d" % (size_mb, limit_mb),
            path=str(target),
            remedy="Raise spec.limits.max_output_mb or lower the preset/bitrate.",
        )
    state.data["current_media"] = str(target)
    return {
        "status": "succeeded",
        "message": "encoded %s (%.2f MB, %s/%s)" % (target.name, size_mb, encoder, audio_encoder),
        "outputs": [str(target)],
        "facts": {"path": str(target), "size_mb": round(size_mb, 3), "encoder": encoder},
    }


def stage_validate(ctx, runner, spec, plan, stage, state):
    validate = ctx.load("validate")
    media = state.data.get("current_media")
    if not media:
        common.fail("E_BLOCKED", "validate stage has no media to inspect")
    report = validate.validate_media(ctx, runner, spec, plan, Path(media))
    path = ctx.paths["reports"] / ("validation-%s.json" % spec["job_id"])
    ctx.common.json_write(path, report)
    state.data["validation_report"] = str(path)
    state.data["validation_passed"] = report["passed"]
    severity = (ctx.policy.get("fail_mode") or "error").lower()
    if not report["passed"]:
        should_fail = any(check["status"] == "fail" and check["severity"] == "error" for check in report["checks"])
        if should_fail and severity != "off":
            return {
                "status": "failed",
                "message": "validation failed: %s" % report["reason"],
                "outputs": [str(path)],
                "facts": {"summary": report["summary"], "reason": report["reason"]},
                "fatal": True,
            }
        return {
            "status": "succeeded",
            "message": "validation completed with warnings: %s" % report["reason"],
            "outputs": [str(path)],
            "facts": {"summary": report["summary"]},
        }
    return {
        "status": "succeeded",
        "message": "validation passed (%d checks)" % len(report["checks"]),
        "outputs": [str(path)],
        "facts": {"summary": report["summary"]},
    }


def stage_package(ctx, runner, spec, plan, stage, state):
    governance = ctx.load("governance")
    media = state.data.get("current_media")
    if not media:
        common.fail("E_BLOCKED", "package stage has no delivery media")
    if not (spec.get("delivery") or {}).get("package"):
        return {"status": "skipped", "message": "spec.delivery.package is false", "outputs": [], "facts": {}}
    if stage["approval_required"] and not ctx.approve:
        ctx.common.fail(
            "E_APPROVAL",
            "delivery packaging writes signed provenance and a redistribution archive; explicit approval is required",
            remedy="Re-run with --approve once the validation report has been reviewed.",
        )
    result = governance.package_delivery(ctx, spec, plan, state, Path(media))
    return {
        "status": "succeeded",
        "message": "delivery package written: %s" % Path(result["archive"]).name,
        "outputs": [result["archive"], result["provenance"], result["disclosure"]],
        "facts": result,
    }


EXECUTORS = {
    "ffmpeg-prepare": stage_prepare,
    "ffmpeg-proxy": stage_proxy,
    "ffmpeg-audio": stage_audio,
    "ffmpeg-compose": stage_compose,
    "ffmpeg-colour": stage_colour,
    "ffmpeg-caption": stage_caption,
    "ffmpeg-encode": stage_encode,
    "backend": stage_backend,
    "internal": None,  # dispatched by run_job
}


# ---------------------------------------------------------------------------
# job runner
# ---------------------------------------------------------------------------
def run_job(ctx, spec, plan, resume=False):
    common = ctx.common
    runner = common.Runner(cancel_event=ctx.cancel_event, log_dir=ctx.paths["logs"], verbose=ctx.verbose)
    state = JobState(ctx.paths["state_file"], spec["job_id"], common.TOOL_VERSION)
    if resume:
        state.load(common)
    state.data["status"] = "running"
    state.data["plan_path"] = str(ctx.paths["plan_file"])
    state.save(common)

    results = []
    stop_reason = None
    cancelled_run = False

    # governance gate before any media work
    governance = ctx.load("governance")
    gate = governance.preflight_gate(ctx, spec, plan)
    results.append(gate)
    if gate["status"] == "failed":
        state.record("governance", "failed", gate["message"])
        state.data["status"] = "blocked"
        state.save(common)
        return {"status": "blocked", "results": results, "state": state, "reason": gate["message"]}

    for stage in plan["stages"]:
        stage_id = stage["id"]
        entry = state.stage(stage_id)
        if resume and entry.get("status") == "succeeded":
            if all(Path(p).exists() for p in entry.get("outputs") or []) or not entry.get("outputs"):
                results.append(
                    {
                        "stage": stage_id,
                        "status": "skipped",
                        "message": "resumed: previous run already succeeded",
                        "outputs": entry.get("outputs") or [],
                        "facts": {},
                    }
                )
                if entry.get("outputs"):
                    for output in reversed(entry["outputs"]):
                        if Path(output).suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                            state.data["current_media"] = output
                            break
                continue
        if ctx.cancel_event.is_set():
            stop_reason = "cancelled by operator"
            results.append({"stage": stage_id, "status": "cancelled", "message": stop_reason, "outputs": [], "facts": {}})
            state.record(stage_id, "cancelled", stop_reason)
            break

        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["started_at"] = common.utcnow()
        started = time.time()
        state.save(common)

        try:
            result = dispatch_stage(ctx, runner, spec, plan, stage, state)
        except common.OnvError as err:
            if err.code == "E_CANCELLED":
                results.append({"stage": stage_id, "status": "cancelled", "message": err.message, "outputs": [], "facts": {}})
                state.record(stage_id, "cancelled", err.message)
                state.save(common)
                stop_reason = "cancelled by operator"
                cancelled_run = True
                break
            status = "blocked" if err.code in ("E_APPROVAL", "E_CONSENT", "E_BLOCKED", "E_BACKEND") else "failed"
            fallback = (spec.get("fallback") or {}).get("on_backend_unavailable") or "fail"
            recovered = None
            if stage["executor"] == "backend" and fallback in ("proxy", "skip"):
                recovered = recover_backend_stage(ctx, runner, spec, plan, stage, state, err, fallback)
            if recovered is not None:
                recovered["stage"] = stage_id
                recovered["duration_s"] = round(time.time() - started, 2)
                recovered["recovered_from"] = err.as_dict()
                results.append(recovered)
                state.record(stage_id, recovered["status"], recovered.get("message"), outputs=recovered.get("outputs"),
                             extra={"fallback": fallback, "duration_s": recovered["duration_s"]})
                state.save(common)
                continue
            results.append(
                {
                    "stage": stage_id,
                    "status": status,
                    "message": err.message,
                    "error": err.as_dict(),
                    "outputs": [],
                    "facts": {},
                }
            )
            state.record(stage_id, status, err.message, extra={"error": err.as_dict(), "duration_s": round(time.time() - started, 2)})
            state.data["status"] = status
            state.save(common)
            stop_reason = err.message
            break
        else:
            result["stage"] = stage_id
            result.setdefault("facts", {})
            result["duration_s"] = round(time.time() - started, 2)
            status = result.get("status", "succeeded")
            results.append(result)
            state.record(stage_id, result["status"], result.get("message"), outputs=result.get("outputs"), extra={"duration_s": result["duration_s"]})
            state.save(common)
            if result["status"] == "failed" or result.get("fatal"):
                stop_reason = result.get("message") or "stage failed"
                break
        finally:
            state.save(common)

    status = "succeeded"
    if cancelled_run:
        status = "cancelled"
    elif stop_reason:
        status = "blocked" if any(r["status"] == "blocked" for r in results) else "failed"
    elif any(r["status"] == "failed" for r in results):
        status = "failed"
    elif any(r["status"] == "blocked" for r in results):
        status = "blocked"
    state.data["status"] = status
    state.data["finished_at"] = common.utcnow()
    state.save(common)
    return {"status": status, "results": results, "state": state, "reason": stop_reason}


def dispatch_stage(ctx, runner, spec, plan, stage, state):
    executor = stage["executor"]
    if executor == "internal":
        if stage["id"] == "preflight":
            preflight = ctx.load("preflight")
            report = preflight.run_preflight(ctx, spec, plan)
            path = ctx.paths["reports"] / ("preflight-%s.json" % spec["job_id"])
            ctx.common.json_write(path, report)
            state.data["preflight_report"] = str(path)
            if not report["ready"]:
                ctx.common.fail(
                    "E_BLOCKED",
                    "preflight blocked the job: %s" % "; ".join(b["summary"] for b in report["blocking"][:3]),
                    blocking=report["blocking"],
                )
            return {
                "status": "succeeded",
                "message": "preflight ready (warn=%d)" % report["summary"].get("warn", 0),
                "outputs": [str(path)],
                "facts": {"summary": report["summary"]},
            }
        if stage["id"] == "validate":
            return stage_validate(ctx, runner, spec, plan, stage, state)
        if stage["id"] == "package":
            return stage_package(ctx, runner, spec, plan, stage, state)
        return {"status": "skipped", "message": "no internal handler for stage '%s'" % stage["id"], "outputs": [], "facts": {}}

    if executor == "backend" and spec["mode"] == "validate-only":
        return {"status": "skipped", "message": "validate-only mode", "outputs": [], "facts": {}}

    handler = EXECUTORS.get(executor)
    if handler is None:
        ctx.common.fail("E_RUNTIME", "unknown executor '%s' for stage '%s'" % (executor, stage["id"]))
    return handler(ctx, runner, spec, plan, stage, state)


def recover_backend_stage(ctx, runner, spec, plan, stage, state, err, fallback):
    """Recover a blocked/failed backend stage with an explicit, labelled fallback."""
    common = ctx.common
    if fallback == "skip":
        return {
            "status": "skipped",
            "message": "neural backend unavailable (%s); stage skipped by spec.fallback=skip" % err.code,
            "outputs": [],
            "facts": {"fallback": "skip", "upstream_error": err.as_dict()},
        }
    proxy_stage = dict(stage)
    routing = plan.get("routing") or {}
    proxy_meta = routing.get("proxy_equivalent") or {}
    proxy_stage["executor"] = "ffmpeg-proxy"
    proxy_stage["timeout_s"] = max(int(stage.get("timeout_s") or 600), 600)
    proxy_stage["params"] = dict(stage.get("params") or {})
    proxy_stage["params"].update(
        {
            "op": stage["params"].get("op", plan["operation"]),
            "proxy_filter": proxy_meta.get("filter"),
            "proxy_label": proxy_meta.get("label"),
        }
    )
    try:
        result = stage_proxy(ctx, runner, spec, plan, proxy_stage, state)
    except common.OnvError as proxy_err:
        return {
            "status": "blocked",
            "message": "backend unavailable and proxy fallback failed: %s" % proxy_err.message,
            "outputs": [],
            "facts": {"fallback": "proxy", "upstream_error": err.as_dict(), "proxy_error": proxy_err.as_dict()},
        }
    result["status"] = "succeeded"
    result["message"] = "proxy fallback used (%s): %s" % (err.code, result.get("message"))
    result.setdefault("facts", {})["fallback"] = "proxy"
    result["facts"]["upstream_error"] = err.as_dict()
    state.data.setdefault("notes", []).append("proxy fallback used for stage %s" % stage["id"])
    return result
