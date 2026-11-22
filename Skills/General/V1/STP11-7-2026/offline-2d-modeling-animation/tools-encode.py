#!/usr/bin/env python3
"""tools-encode.py — export, encoding, delivery packaging and batch automation.

Built-in exporters cover image sequences, GIF, APNG, BMP/TGA/PPM and a WAV
muxer for review. Video (H.264 / FFV1 / ProRes) and WebP route through a
configurable external ffmpeg/cwebp when installed — never faked, always
reported as available or not. Includes: format compatibility routing,
manifest-driven batch export with failure isolation and resumable state,
export naming enforcement, encoder availability audit, checksum reports,
delivery packages (media + manifest + checksums + README), a local job
queue with priorities/dependencies/retries, workspace relinking and build
provenance recording. Pure stdlib.
"""
import json
import os

core = None
im = None
err = None
require = None


def _init():
    global core, im
    if core is None:
        import importlib.util as ilu
        import sys as sys_
        from pathlib import Path as P

        def sib(name):
            key = "studio." + name
            mod = sys_.modules.get(key)
            if mod is not None:
                return mod
            p = P(__file__).resolve().parent / ("tools-" + name + ".py")
            spec = ilu.spec_from_file_location(key, p)
            mod = ilu.module_from_spec(spec)
            sys_.modules[key] = mod
            spec.loader.exec_module(mod)
            return mod
        core = sib("core")
        im = sib("imaging")
    return core, im


def err(code, message, **details):
    _init()
    return core.err(code, message, **details)


def require(cond, code, message, **details):
    _init()
    return core.require(cond, code, message, **details)


# ------------------------------------------------------- sequence export ----

def export_sequence(images, out_dir, name_pattern="{frame:04d}.png", start=1,
                    fmt=None, depth=8, quality=None, bg=None):
    """Write numbered stills with padding, format and alpha control."""
    _, _ = _init()
    from pathlib import Path as P
    require(images, "invalid_input", "no frames to export")
    out_dir = P(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    core.check_limits(None, frames=len(images))
    files = []
    for i, img in enumerate(images):
        if bg is not None:
            img = im.flatten(img, bg)
        fname = name_pattern.format(frame=start + i, index=i)
        path = out_dir / fname
        im.save_image(str(path), img, fmt=fmt or path.suffix.lstrip(".") or "png")
        files.append(str(path))
    return {"dir": str(out_dir), "count": len(files), "files": files,
            "first": files[0], "last": files[-1]}


def export_animation(images, out_path, fps=12, loop=0, dither=False,
                     max_colors=256, delay_ms=None, default_image=False):
    """GIF / APNG export with palette + dither + timing options."""
    _, _ = _init()
    require(images, "invalid_input", "no frames to export")
    delay = delay_ms or int(round(1000.0 / fps))
    frames = [{"image": f, "delay_ms": delay} for f in images]
    ext = str(out_path).rsplit(".", 1)[-1].lower()
    if ext == "gif":
        data = im.encode_gif_bytes(frames, loop=loop, dither=dither,
                                   max_colors=max_colors)
    elif ext in ("png", "apng"):
        data = im.encode_apng_bytes(frames, loop=loop, default_image=default_image)
    else:
        raise err("unsupported_format",
                  "animation export needs .gif or .png/.apng (got .%s)" % ext)
    core.write_bytes(str(out_path), data)
    return {"path": str(out_path), "frames": len(images), "fps": fps,
            "bytes": len(data), "bytes_human": core.human_size(len(data))}


def export_gif_from_files(paths, out_path, fps=12, **opts):
    _, _ = _init()
    imgs = [im.load_image(p)[0] for p in paths]
    return export_animation(imgs, out_path, fps=fps, **opts)


def export_pdf(images, out_path, page_w=512, bg=(255, 255, 255)):
    _, _ = _init()
    require(images, "invalid_input", "no pages")
    data = im.pdf_from_images(images, page_w=page_w, bg=bg)
    core.write_bytes(str(out_path), data)
    return {"path": str(out_path), "pages": len(images), "bytes": len(data)}


# ------------------------------------------------------------ video ute ----

def ffmpeg_available():
    _, _ = _init()
    cfg = core.load_config()
    exe = core.cfg_get(cfg, "apps.ffmpeg")
    found = core.find_executable(exe) if exe else core.find_executable("ffmpeg")
    return found


def encoder_audit():
    """Encoder Availability Auditor: what can encode what, right here."""
    _, _ = _init()
    ff = ffmpeg_available()
    cfg = core.load_config()
    caps = []
    caps.append({"kind": "png/apng/gif/bmp/tga/ppm/wav/pdf", "available": True,
                 "engine": "built-in"})
    if ff:
        res = core.run_command([ff, "-hide_banner", "-encoders"], timeout=30)
        encs = res.get("stdout_tail", "")
        for label, needle in (("h264", "libx264"), ("ffv1", "ffv1"),
                              ("prores", "prores"), ("webp", "libwebp_anim"),
                              ("vp9", "libvpx-vp9"), ("aac", "aac"),
                              ("flac", "flac"), ("mp3", "libmp3lame"),
                              ("tiff", "tiff"), ("jpeg", "mjpeg")):
            caps.append({"kind": label, "available": needle in encs,
                         "engine": "ffmpeg"})
    else:
        caps.append({"kind": "video/webp/jpeg/tiff", "available": False,
                     "engine": "ffmpeg",
                     "hint": "install ffmpeg and set apps.ffmpeg in "
                             "studio-config.json to enable video export"})
    for exe_name in ("cwebp", "magick"):
        exe = core.cfg_get(cfg, "apps." + exe_name)
        found = core.find_executable(exe) if exe else core.find_executable(exe_name)
        caps.append({"kind": exe_name, "available": bool(found), "engine": exe_name})
    return {"encoders": caps,
            "ffmpeg_path": ff,
            "note": "availability only; licensing of codecs is a project decision"}


def export_video(image_paths, out_path, fps=24, preset="h264_review", audio=None,
                 scale=None, pix_fmt=None):
    """Encode an image sequence to video through ffmpeg (bounded, logged).
    Raises encoder_unavailable when ffmpeg is missing — no silent fallback."""
    _, _ = _init()
    ff = ffmpeg_available()
    if not ff:
        raise err("encoder_unavailable",
                  "ffmpeg is not installed/configured; set apps.ffmpeg in "
                  "studio-config.json. Built-in alternatives: GIF/APNG (no audio) "
                  "or PNG sequence + WAV")
    cfg = core.load_config()
    presets = {"h264_review": ["-c:v", "libx264", "-crf",
                               str(core.cfg_get(cfg, "export.h264_crf", 18)),
                               "-preset", core.cfg_get(cfg, "export.h264_preset", "medium"),
                               "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
               "h264_proxy": ["-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
                              "-pix_fmt", "yuv420p", "-vf", "scale=iw/2:ih/2"],
               "master_lossless": ["-c:v", "ffv1", "-level", "3", "-g", "1",
                                   "-slices", "24", "-pix_fmt", "bgra"],
               "master_mezzanine": ["-c:v", "prores_ks", "-profile:v", "3",
                                    "-pix_fmt", "yuva444p10le"]}
    require(preset in presets, "invalid_input", "unknown preset '%s'" % preset)
    require(image_paths, "invalid_input", "no frames")
    first = str(image_paths[0])
    argv = [ff, "-y", "-framerate", str(fps), "-start_number",
            str(_seq_start(first)), "-i", _seq_pattern(first)]
    argv += presets[preset]
    if scale:
        argv += ["-vf", "scale=iw*%s:ih*%s" % (scale, scale)]
    if pix_fmt:
        argv += ["-pix_fmt", pix_fmt]
    if audio:
        argv += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    argv += [str(out_path)]
    res = core.run_command(argv, timeout=core.cfg_get(cfg, "subprocess_timeout", 600))
    if res["returncode"] != 0 or not os.path.isfile(str(out_path)):
        raise err("encode_failed", "ffmpeg failed (%s): %s"
                  % (res["returncode"], res["stderr_tail"][-400:]), argv=argv)
    return {"path": str(out_path), "frames": len(image_paths), "fps": fps,
            "preset": preset, "argv": argv, "bytes": os.path.getsize(str(out_path))}


def _seq_start(path):
    import re as _re
    m = _re.search(r"(\d+)\.\w+$", str(path))
    return int(m.group(1)) if m else 1


def _seq_pattern(path):
    import re as _re
    return _re.sub(r"(\d+)(\.\w+)$", lambda m: "%%0%dd%s" % (len(m.group(1)), m.group(2)),
                   str(path))


def transparent_video_plan(frames_count, fps=24, has_audio=False):
    """Plan an alpha-capable delivery (Transparent Video Export Planner)."""
    _, _ = _init()
    options = []
    if ffmpeg_available():
        options.append({"option": "prores 4444 mov", "alpha": True,
                        "cmd": "export_video preset=master_mezzanine",
                        "alpha_type": "straight (verify in consumer)"})
        options.append({"option": "vp9 webm yuva420p", "alpha": True,
                        "note": "smaller, slower to encode"})
    options.append({"option": "png sequence + wav", "alpha": True,
                    "cmd": "export sequence (built-in)", "alpha_type": "straight",
                    "built_in": True, "frames": frames_count})
    options.append({"option": "apng", "alpha": True, "built_in": True,
                    "note": "best for short loops without audio"})
    return {"options": options, "fps": fps,
            "premultiplied_warning": "video pipelines often deliver premultiplied "
                                     "alpha; composite tests before final delivery"}


def format_router(target, needs=("alpha",), animation=False, quality="standard"):
    """Route output format by target consumer + requirements."""
    _, _ = _init()
    matrix = core.load_json(core.ROOT / "asset-format-matrix.json")["matrix"]
    require(target in matrix, "invalid_input",
            "unknown target '%s' (available: %s)" % (target, ", ".join(sorted(matrix))))
    wants_alpha = "alpha" in needs
    key = None
    if animation:
        key = "animation_with_alpha" if wants_alpha else "animation_small"
    else:
        key = "still_with_alpha" if wants_alpha else "still"
    entry = matrix[target].get(key) or matrix[target].get("master") or \
        matrix[target].get("sprites_with_alpha") or {}
    if not entry:
        entry = {"primary": "png", "fallback": None}
    if entry.get("requires") and not ffmpeg_available():
        entry = dict(entry)
        entry["warning"] = ("required encoder (%s) not installed; using built-in "
                            "fallback" % entry["requires"])
        entry.setdefault("fallback", "png sequence / gif / apng")
    return {"target": target, "needs": list(needs), "animation": animation,
            "quality": quality, "choice": entry}


# ------------------------------------------------------------- naming -------

def export_name(project, seq, shot, task, version, variant=None, frames=None,
                res=None, ext="png"):
    """Collision-free export filenames (Export Naming and Versioning Enforcer)."""
    _, _ = _init()
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise err("invalid_input", "version must be an integer (got %r)" % (version,))
    name = core.render_name(core.NAMING_RULES["pattern_default"],
                            {"project": project, "seq": seq, "shot": shot,
                             "task": task, "version": version})
    if variant:
        name += "-" + core.slugify(variant)
    if frames:
        name += "-f%05d-%05d" % (int(frames[0]), int(frames[-1]))
    if res:
        name += "-%dx%d" % (int(res[0]), int(res[1]))
    name += "." + ext.lstrip(".")
    return name


# ---------------------------------------------------- batch from manifest ---

def run_batch(manifest_path, work_fn=None, state_path=None, stop_on_error=False):
    """Manifest-Driven Batch Processor: CSV/JSON manifest of items, each row
    processed by work_fn(item) (defaults to convert image). Resumable: a
    state file records completed items; reruns skip them. Failures are
    isolated and reported per item."""
    _, _ = _init()
    path = str(manifest_path)
    if path.endswith(".json"):
        manifest = core.load_json(path)
        items = manifest.get("items") or manifest.get("rows") or []
    else:
        rows = core.load_data_file(path)
        items = [dict(r) for r in rows]
    require(items, "invalid_input", "manifest has no items")
    state_path = str(state_path) if state_path else path + ".state.json"
    state = {"done": {}, "failed": {}}
    if os.path.isfile(state_path):
        state = core.load_json(state_path)
    results = []
    for i, item in enumerate(items):
        key = item.get("id") or ("row%03d" % i)
        if key in state["done"]:
            results.append({"id": key, "status": "skipped_already_done"})
            continue
        try:
            out = (work_fn or _default_batch_work)(item)
            state["done"][key] = out
            results.append({"id": key, "status": "ok", "result": out})
        except Exception as e:
            state["failed"][key] = {"error": str(e)[:200], "code": getattr(e, "code", "error")}
            results.append({"id": key, "status": "failed", "error": str(e)[:200],
                            "code": getattr(e, "code", "error")})
            if stop_on_error:
                break
        core.save_json(state_path, state)
    return {"items": len(items), "results": results,
            "done": len(state["done"]), "failed": len(state["failed"]),
            "state_path": state_path}


def _default_batch_work(item):
    _, _ = _init()
    src = item.get("input") or item.get("in") or item.get("source")
    dst = item.get("output") or item.get("out") or item.get("destination")
    require(src and dst, "invalid_input", "batch item needs input/output")
    img, _ = im.load_image(src)
    if item.get("width") or item.get("height"):
        img = im.resize(img, int(item.get("width") or img.w),
                        int(item.get("height") or img.h),
                        item.get("method", "bilinear"))
    if item.get("flatten_bg"):
        img = im.flatten(img, item["flatten_bg"])
    im.save_image(str(dst), img, fmt=item.get("format"))
    return {"output": str(dst), "size": [img.w, img.h]}


# --------------------------------------------------------- checksums --------

def checksum_report(paths, algo="sha256", out_path=None):
    """Generate hashes for files (verify=true mode compares existing)."""
    _, _ = _init()
    import hashlib
    rows = []
    for p in paths:
        p = str(p)
        require(os.path.isfile(p), "invalid_input", "not a file: %s" % p)
        h = core.sha256_file(p) if algo == "sha256" else core.md5_str(core.read_bytes(p))
        rows.append({"file": p, "algo": algo, "hash": h,
                     "bytes": os.path.getsize(p)})
    report = {"algo": algo, "count": len(rows), "files": rows,
              "generated": core.utc_now()}
    if out_path:
        core.save_json(out_path, report)
    return report


def verify_checksums(report_path):
    """Verify a previously written checksum report."""
    _, _ = _init()
    rep = core.load_json(report_path)
    results = []
    for row in rep.get("files", []):
        ok = os.path.isfile(row["file"]) and \
            core.sha256_file(row["file"]) == row["hash"]
        results.append({"file": row["file"], "ok": ok})
    bad = [r for r in results if not r["ok"]]
    return {"verified": len(results), "ok": len(results) - len(bad),
            "failed": bad, "verdict": "pass" if not bad else "%d mismatch" % len(bad)}


# ------------------------------------------------------- delivery pack ------

def delivery_package(media, out_dir, name, include=None, readme=None,
                     licenses_dir=None):
    """Assemble final delivery: media + checksums + manifest + README
    (+ licenses when provided). Never destructive; output is a new folder."""
    _, _ = _init()
    from pathlib import Path as P
    require(media, "invalid_input", "no media to package")
    out = P(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for m in media:
        src = P(str(m))
        require(src.is_file(), "invalid_input", "missing media file: %s" % src)
        dst = out / src.name
        core.write_bytes(str(dst), core.read_bytes(str(src)))
        written.append(str(dst))
    for extra in (include or []):
        src = P(str(extra))
        if src.is_file():
            dst = out / src.name
            core.write_bytes(str(dst), core.read_bytes(str(src)))
            written.append(str(dst))
    report = checksum_report(written, out_path=str(out / "checksums.json"))
    manifest = {"package": name, "created": core.utc_now(),
                "files": [P(w).name for w in written],
                "checksums": "checksums.json",
                "provenance": build_provenance()}
    core.save_json(str(out / "manifest.json"), manifest)
    core.save_text(str(out / "README.txt"), readme or (
        "DELIVERY: %s\n\nContents: see manifest.json; integrity: checksums.json "
        "(sha256).\nPlayback/import: use any offline-capable player or editor.\n"
        "Licenses and attribution: see licenses/ when present.\n" % name))
    if licenses_dir and P(str(licenses_dir)).is_dir():
        ldst = out / "licenses"
        ldst.mkdir(exist_ok=True)
        for f in P(str(licenses_dir)).iterdir():
            if f.is_file():
                core.write_bytes(str(ldst / f.name), core.read_bytes(str(f)))
    return {"dir": str(out), "files": len(written) + 3,
            "checksums": report["count"], "path": str(out)}


def build_provenance():
    """Build Provenance Recorder: machine + tool versions for this build."""
    _, _ = _init()
    rep = core.machine_report()
    rep["tool"] = {"name": core.TOOL_NAME, "version": core.TOOL_VERSION}
    rep["recorded"] = core.utc_now()
    return rep


# ------------------------------------------------------------- queue --------

def job_queue(jobs, state_path="jobs-state.json", work_fn=None, max_retries=2):
    """Offline Job Queue: local jobs with priority, dependencies, retries,
    and resumable state. jobs=[{id, command?, depends_on, priority}]."""
    _, _ = _init()
    from pathlib import Path as P
    state = {"done": {}, "failed": {}}
    sp = P(str(state_path))
    if sp.is_file():
        state = core.load_json(str(sp))
    pending = sorted(jobs, key=lambda j: (-int(j.get("priority", 0))))
    results = []
    for job in pending:
        jid = job.get("id")
        require(jid, "invalid_input", "job missing id")
        if jid in state["done"]:
            results.append({"id": jid, "status": "skipped_already_done"})
            continue
        deps = job.get("depends_on") or []
        missing = [d for d in deps if d not in state["done"] and d not in state["failed"]]
        blocked = [d for d in deps if d in state["failed"]]
        if missing:
            results.append({"id": jid, "status": "blocked",
                            "waiting_on": missing})
            continue
        if blocked:
            state["failed"][jid] = {"reason": "dependency failed: %s" % blocked}
            results.append({"id": jid, "status": "failed", "reason": "dependency failed"})
            continue
        try:
            if work_fn:
                out = work_fn(job)
            else:
                argv = job.get("command")
                require(argv, "invalid_input", "job needs command or work_fn")
                cfg = core.load_config()
                res = core.run_command([str(a) for a in argv],
                                       timeout=job.get("timeout") or
                                       core.cfg_get(cfg, "subprocess_timeout", 600))
                require(res["returncode"] == 0, "command_failed",
                        "exit %s: %s" % (res["returncode"], res["stderr_tail"][-200:]))
                out = {"exit": res["returncode"]}
            state["done"][jid] = out
            results.append({"id": jid, "status": "ok", "result": out})
        except Exception as e:
            attempt = state["failed"].get(jid, {}).get("attempts", 0)
            if attempt < max_retries:
                state["failed"][jid] = {"attempts": attempt + 1, "error": str(e)[:200],
                                        "retry": True}
                results.append({"id": jid, "status": "retry_scheduled",
                                "attempt": attempt + 1})
            else:
                state["failed"][jid] = {"attempts": attempt + 1, "error": str(e)[:200]}
                results.append({"id": jid, "status": "failed", "error": str(e)[:200]})
        core.save_json(str(sp), state)
    return {"jobs": len(jobs), "results": results,
            "done": len(state["done"]), "failed": len([f for f in state["failed"].values()
                                                       if not f.get("retry")]),
            "state": str(sp)}


# ------------------------------------------------------------ relink --------

def relink_workspace(project_dir, mapping, dry_run=True, extensions=(".json", ".csv",
                                                                    ".txt", ".md")):
    """Repair moved project paths: rewrite absolute -> relative references in
    text files. dry_run reports planned changes without writing."""
    _, _ = _init()
    from pathlib import Path as P
    root = P(str(project_dir))
    require(root.is_dir(), "invalid_input", "project dir not found: %s" % root)
    require(mapping, "invalid_input", "mapping of old->new required")
    changes = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in extensions:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        rel = f.relative_to(root)
        for old, new_path in mapping.items():
            if old in new:
                replacement = new_path
                new = new.replace(old, replacement)
        if new != text:
            changes.append({"file": str(rel), "occurrences": sum(
                text.count(o) for o in mapping if o in text)})
            if not dry_run:
                core.save_text(str(f), new)
    return {"scanned_suffixes": list(extensions), "changes": changes,
            "count": len(changes), "dry_run": bool(dry_run),
            "hint": "rerun with dry_run=False to apply" if dry_run else "applied"}


# ------------------------------------------------------------ smoke test ----

def pipeline_smoke_test(out_dir):
    """Tiny reference project exercising ingest->draw->animate->audio->export-
    >validate->restore. Writes a report; never claims untested things."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    steps = []

    def step(name, fn):
        try:
            detail = fn()
            steps.append({"step": name, "ok": True, "detail": detail})
        except Exception as e:
            steps.append({"step": name, "ok": False, "detail": str(e)[:160]})
    step("draw", lambda: (lambda img: (im.draw_rect(img, 2, 2, 12, 12, (200, 30, 30, 255)),
                                       im.save_image(str(out / "draw.png"), img),
                                       "100x80 canvas with rect")[2])(im.new_image(100, 80)))
    step("ingest", lambda: (im.load_image(str(out / "draw.png"))[1]["format"] + " decoded"))
    step("animate", lambda: (core.write_bytes(str(out / "anim.gif"),
                                            im.encode_gif_bytes(
                                                [{"image": im.new_image(40, 30, (20, 60, 120, 255)),
                                                  "delay_ms": 80},
                                                 {"image": im.new_image(40, 30, (60, 120, 20, 255)),
                                                  "delay_ms": 80}])),
                             "2-frame gif written")[1])
    step("audio", lambda: str(_write_smoke_wav(str(out / "tone.wav"))))
    step("export", lambda: (core.write_bytes(str(out / "doc.pdf"),
                                           im.pdf_from_images([im.new_image(80, 60, (240, 240, 240, 255))])),
                            "pdf written")[1])
    step("validate", lambda: "%d checksums" % checksum_report(
        [str(out / "draw.png")])["count"])
    step("restore", lambda: (verify_checksums(str(out / "checksums.json"))["verdict"]
                             if (out / "checksums.json").is_file() else
                             checksum_report([str(out / "draw.png")],
                                             out_path=str(out / "checksums.json")) and
                             verify_checksums(str(out / "checksums.json"))["verdict"]))
    ok = all(s["ok"] for s in steps)
    report = {"ok": ok, "steps": steps, "generated": core.utc_now(),
              "dir": str(out)}
    core.save_json(str(out / "smoke-report.json"), report)
    return report


def _write_smoke_wav(path):
    _, _ = _init()
    import math
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    p = P(__file__).resolve().parent / "tools-audio.py"
    spec = ilu.spec_from_file_location("studio.audio", p)
    aud = sys_.modules.get("studio.audio")
    if aud is None:
        aud = ilu.module_from_spec(spec)
        sys_.modules["studio.audio"] = aud
        spec.loader.exec_module(aud)
    rate = 22050
    samples = [0.4 * math.sin(2 * math.pi * 440 * i / rate) for i in range(rate // 2)]
    core.write_bytes(str(path), aud.encode_wav(aud.Audio(rate, 1, [samples]), bits=16))
    return path


# ------------------------------------------------------------ automation ----

def export_webp(image_paths, out_path, quality=85, lossless=False):
    """Animated/single WebP through the configured cwebp or ffmpeg.
    Structured error when neither is installed — never a silent fallback."""
    _, _ = _init()
    cfg = core.load_config()
    cwebp = core.find_executable(core.cfg_get(cfg, "apps.cwebp") or "cwebp")
    ff = ffmpeg_available()
    if cwebp and len(image_paths) == 1:
        argv = [cwebp, "-q", str(quality)] + (["-lossless"] if lossless else []) + \
            [str(image_paths[0]), "-o", str(out_path)]
    elif ff:
        first = str(image_paths[0])
        argv = [ff, "-y", "-framerate", "12", "-start_number", str(_seq_start(first)),
                "-i", _seq_pattern(first)]
        if len(image_paths) == 1:
            argv = [ff, "-y", "-i", first]
        argv += ["-c:v", "libwebp", "-q:v", str(quality),
                 "-lossless", "1" if lossless else "0"]
        if len(image_paths) > 1:
            argv += ["-loop", "0"]
        argv += [str(out_path)]
    else:
        raise err("encoder_unavailable",
                  "WebP export needs cwebp or ffmpeg configured "
                  "(apps.cwebp / apps.ffmpeg); built-in alternatives: PNG (still) "
                  "or GIF/APNG (animation)")
    res = core.run_command(argv, timeout=core.cfg_get(cfg, "subprocess_timeout", 600))
    if res["returncode"] != 0 or not os.path.isfile(str(out_path)):
        raise err("encode_failed", "webp encode failed (%s): %s"
                  % (res["returncode"], res["stderr_tail"][-300:]), argv=argv)
    return {"path": str(out_path), "frames": len(image_paths), "quality": quality,
            "bytes": os.path.getsize(str(out_path)), "argv": argv}


def generate_script(out_path, commands, kind="sh", header=None):
    """Reproducible Export Script Builder: emit a shell/python script that
    re-runs the exact recorded command lines."""
    _, _ = _init()
    lines = ["#!/bin/sh"] if kind == "sh" else ["#!/usr/bin/env python3"]
    lines.append("# generated by %s %s at %s" % (core.TOOL_NAME, core.TOOL_VERSION,
                                                 core.utc_now()))
    if header:
        lines.append("# " + str(header))
    for cmd in commands:
        require(isinstance(cmd, (list, tuple)) and cmd, "invalid_input",
                "each command must be an argv list")
        import shlex
        if kind == "sh":
            lines.append(" ".join(shlex.quote(str(c)) for c in cmd))
        else:
            lines.append("import subprocess\nsubprocess.run(%r, check=True)" % ([str(c) for c in cmd],))
    core.save_text(str(out_path), "\n".join(lines) + "\n")
    if kind == "sh":
        os.chmod(str(out_path), 0o755)
    return {"path": str(out_path), "commands": len(commands), "kind": kind}


def sandbox_run(command, inputs, staging_dir, timeout=120):
    """Script Safety Sandbox: copy inputs to a staged folder, run the command
    bounded there, report every file it created or changed."""
    _, _ = _init()
    from pathlib import Path as P
    stage = P(str(staging_dir))
    stage.mkdir(parents=True, exist_ok=True)
    for i in inputs or []:
        src = P(str(i))
        require(src.is_file(), "invalid_input", "input not found: %s" % src)
        core.write_bytes(str(stage / src.name), core.read_bytes(str(src)))
    before = {str(p): p.stat().st_mtime for p in stage.iterdir()}
    require(isinstance(command, (list, tuple)) and command, "invalid_input",
            "command must be an argv list")
    res = core.run_command([str(c) for c in command], cwd=str(stage), timeout=timeout)
    changed = []
    for p in sorted(stage.iterdir()):
        if str(p) not in before:
            changed.append({"file": p.name, "change": "created"})
        elif p.stat().st_mtime != before[str(p)]:
            changed.append({"file": p.name, "change": "modified"})
    return {"result": res, "changed_files": changed,
            "staging": str(stage),
            "note": "originals untouched; inspect changed files before promoting"}


def cache_build(out_dir, inputs, seed, version, note=""):
    """Deterministic Cache Builder: cache dir + manifest of inputs, seed and
    tool version with invalidation info."""
    _, _ = _init()
    from pathlib import Path as P
    d = P(str(out_dir))
    d.mkdir(parents=True, exist_ok=True)
    entries = []
    for i in inputs or []:
        p = P(str(i))
        if p.is_file():
            entries.append({"file": p.name, "sha256": core.sha256_file(str(p))})
    manifest = {"seed": str(seed), "version": str(version),
                "tool": {"name": core.TOOL_NAME, "version": core.TOOL_VERSION},
                "inputs": entries, "note": str(note),
                "invalidation": "rebuild when any input hash, the seed or the "
                                "tool version changes",
                "created": core.utc_now()}
    core.save_json(str(d / "cache-manifest.json"), manifest)
    return {"dir": str(d), "inputs": len(entries), "manifest":
            str(d / "cache-manifest.json"), "seed": str(seed)}


def headless_render(command, log_path, cwd=None, timeout=None):
    """Headless Render Operator: run an app/renderer without a GUI, capture
    per-shot logs and exit status. Errors carry the log tail."""
    _, _ = _init()
    cfg = core.load_config()
    require(isinstance(command, (list, tuple)) and command, "invalid_input",
            "command must be an argv list")
    res = core.run_command([str(c) for c in command], cwd=cwd,
                           timeout=timeout or core.cfg_get(cfg, "subprocess_timeout", 600))
    core.save_text(str(log_path),
                   "argv: %s\nexit: %s\nduration: %ss\n--- stdout ---\n%s\n"
                   "--- stderr ---\n%s\n"
                   % (res["argv"], res["returncode"], res["duration_s"],
                      res["stdout_tail"], res["stderr_tail"]))
    return {"ok": res["returncode"] == 0, "exit": res["returncode"],
            "log": str(log_path), "duration_s": res["duration_s"]}


# ------------------------------------------- cross-app pipeline (22.4) ------

def pipeline_adapter(steps, app, project_dir, cfg=None):
    """Cross-Application Pipeline Adapter: translate generic pipeline steps
    (render, export-sequence, export-gif, batch-convert) into the target
    app's headless command idiom. Returns a structured plan; executes
    nothing. App availability is reported, never assumed."""
    core, im = _init()
    idioms = {
        "krita": {
            "render": ["krita", "--export", "--export-filename", "{out}",
                       "{file}"],
            "export-sequence": ["krita", "--export", "--export-filename",
                                "{out_dir}/%04d.png", "{file}"],
            "batch-convert": ["krita", "--export", "--export-filename",
                              "{out}", "{file}"],
            "notes": "HEADLESS export of .kra; animation via .KRA render "
                     "needs GUI once to configure"},
        "inkscape": {
            "render": ["inkscape", "{file}", "--export-filename={out}"],
            "export-sequence": ["inkscape", "{file}", "--export-filename="
                                "{out_dir}/page.png"],
            "batch-convert": ["inkscape", "{file}", "--export-filename={out}"],
            "notes": "1:1 CLI export of SVG/PDF/eps"},
        "aseprite": {
            "render": ["aseprite", "-b", "{file}", "--save-as", "{out}"],
            "export-sequence": ["aseprite", "-b", "{file}", "--save-as",
                                "{out_dir}/frame{frame1}.png"],
            "export-gif": ["aseprite", "-b", "{file}", "--save-as", "{out}"],
            "batch-convert": ["aseprite", "-b", "{file}", "--save-as", "{out}"],
            "notes": "-b batch mode; {frame1} expands per frame"},
        "opentoonz": {"notes": "no stable headless CLI; drive via "
                               "interchange_package + GUI renders"},
        "synfig": {
            "render": ["synfig", "-t", "png", "-o", "{out}", "{file}"],
            "export-sequence": ["synfig", "-t", "png", "-w", "{w}", "-h",
                                "{h}", "-o", "{out_dir}/frame.png", "{file}"],
            "notes": "renders .sif to png sequence or target"},
        "blender": {
            "render": ["blender", "-b", "{file}", "-o", "{out}",
                       "-f", "{frame}"],
            "export-sequence": ["blender", "-b", "{file}", "-o",
                                "{out_dir}/####", "-a"],
            "notes": "grease pencil scenes render like any scene"},
        "ffmpeg": {
            "export-gif": ["ffmpeg", "-i", "{in}", "-vf",
                           "fps={fps},scale={w}:-1:flags=lanczos,split[a][b];"
                           "[a]palettegen[p];[b][p]paletteuse", "{out}"],
            "export-video": ["ffmpeg", "-i", "{in}", "-c:v", "libx264",
                             "-crf", "18", "-pix_fmt", "yuv420p", "{out}"],
            "notes": "used by export_gif/export_video when present"},
    }
    require(app in idioms, "invalid_input",
            "app must be one of %s" % ", ".join(sorted(idioms)))
    if cfg is None:
        cfg = {}
        cfg_path = None
        from pathlib import Path as P
        for cand in (P(str(project_dir)) / "studio-config.json",
                     P(str(project_dir)).parent / "studio-config.json"):
            if cand.is_file():
                cfg_path = cand
                break
        cfg = core.load_config(cfg_path) if cfg_path else {}
    apps = core.detect_apps(cfg)
    avail = apps.get(app, {"path": None})
    rows = []
    for s in steps or []:
        step = str(s.get("step", s) if isinstance(s, dict) else s)
        cmd = idioms[app].get(step)
        rows.append({"step": step,
                     "command": cmd or None,
                     "status": "unavailable_in_app" if not cmd else
                     ("ready" if avail.get("path") else "app_not_found"),
                     "file": s.get("file") if isinstance(s, dict) else None})
    return {"app": app, "available": bool(avail.get("path")),
            "path": avail.get("path"), "plan": rows,
            "notes": idioms[app]["notes"],
            "rule": "review the plan, then run commands yourself or via "
                    "run_command with a timeout"}
