"""Preflight checks: runtime, media tools, hardware, disk, model files, rights.

Every check returns a structured record so the report can be machine-consumed:

    {"id": str, "status": "ok"|"warn"|"fail"|"skip", "severity": str,
     "summary": str, "facts": {...}, "remedy": str|None}
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from pathlib import Path

REQUIRED_FILTERS_BY_OP = {
    "generate": ["zoompan"],
    "animate": ["zoompan"],
    "transform": ["curves"],
    "interpolate": ["minterpolate"],
    "restore": ["hqdn3d", "deflicker", "scale"],
    "edit": ["eq"],
    "audio": ["loudnorm"],
    "caption": ["subtitles"],
    "color": ["eq"],
    "encode": ["scale"],
}


def _record(check_id, status, summary, severity="error", facts=None, remedy=None):
    return {
        "id": check_id,
        "status": status,
        "severity": severity if status != "ok" else "info",
        "summary": summary,
        "facts": facts or {},
        "remedy": remedy,
    }


def ffmpeg_capabilities(binary, runner):
    """Return encoders/filters/version for an ffmpeg binary, or None if unusable."""
    resolved = shutil.which(binary) if binary else None
    if not resolved:
        return None
    try:
        version = runner.run([resolved, "-hide_banner", "-version"], timeout_s=30)
        encoders = runner.run([resolved, "-hide_banner", "-encoders"], timeout_s=30)
        filters = runner.run([resolved, "-hide_banner", "-filters"], timeout_s=30)
    except Exception:  # noqa: BLE001 - preflight must never raise on probe failure
        return None
    enc = set()
    for line in encoders["stdout"].splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.match(r"^[VAS][\w.]{5}$", parts[0]):
            enc.add(parts[1])
    fil = set()
    for line in filters["stdout"].splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.match(r"^[T.][S.][C.]{1}", parts[0]):
            fil.add(parts[1].split("->")[0])
    first = (version["stdout"].splitlines() or ["ffmpeg"])[0]
    return {"path": resolved, "version": first.strip(), "encoders": enc, "filters": fil}


def detect_hardware(runner, spec):
    hardware = {"gpus": [], "cpu_count": os.cpu_count(), "ram_gb": None, "source": "detected"}
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        hardware["ram_gb"] = round(int(line.split()[1]) / 1024.0 / 1024.0, 1)
                        break
        except OSError:
            pass
    if shutil.which("nvidia-smi"):
        try:
            out = runner.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                timeout_s=30,
            )
            for line in out["stdout"].strip().splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 2 and parts[1]:
                    hardware["gpus"].append(
                        {
                            "name": parts[0],
                            "vram_gb": round(float(parts[1]) / 1024.0, 1),
                            "driver": parts[2] if len(parts) > 2 else None,
                        }
                    )
            hardware["source"] = "nvidia-smi"
        except Exception:  # noqa: BLE001
            hardware["source"] = "nvidia-smi-unavailable"
    override = (spec.get("hardware") or {}).get("vram_gb")
    if override is not None:
        hardware["gpus"] = [{"name": "declared-by-spec", "vram_gb": float(override), "driver": None}]
        hardware["source"] = "spec.hardware.vram_gb"
    ram_override = (spec.get("hardware") or {}).get("ram_gb")
    if ram_override is not None:
        hardware["ram_gb"] = float(ram_override)
        hardware["source"] += "+spec"
    hardware["vram_gb"] = max([gpu["vram_gb"] for gpu in hardware["gpus"]], default=None)
    hardware["cpu_capable"] = bool(((spec.get("hardware") or {}).get("allow_cpu", True)))
    return hardware


def run_preflight(ctx, spec, plan, reporter=None):
    common = ctx.common
    checks = []
    runner = common.Runner(cancel_event=ctx.cancel_event, log_dir=None, verbose=ctx.verbose)
    budget = plan.get("budget", {})
    routing = plan.get("routing", {})
    policy = ctx.policy or {}

    # 1. runtime ---------------------------------------------------------
    py_ok = sys.version_info >= (3, 8)
    checks.append(
        _record(
            "runtime.python",
            "ok" if py_ok else "fail",
            "Python %s" % platform.python_version(),
            facts={"version": platform.python_version(), "executable": sys.executable},
            remedy=None if py_ok else "Use Python 3.8 or newer.",
        )
    )
    checks.append(
        _record(
            "runtime.platform",
            "ok",
            "%s %s" % (platform.system(), platform.release()),
            severity="info",
            facts={"machine": platform.machine()},
        )
    )

    # 2. media tools -----------------------------------------------------
    ffmpeg_bin = ctx.config.get("ffmpeg", "ffmpeg")
    ffprobe_bin = ctx.config.get("ffprobe", "ffprobe")
    caps = ffmpeg_capabilities(ffmpeg_bin, runner)
    if caps:
        checks.append(
            _record(
                "media.ffmpeg",
                "ok",
                "ffmpeg available: %s" % caps["version"],
                facts={"path": caps["path"], "encoder_count": len(caps["encoders"])},
            )
        )
    else:
        checks.append(
            _record(
                "media.ffmpeg",
                "fail",
                "ffmpeg not found or not executable",
                facts={"requested": ffmpeg_bin},
                remedy="Install ffmpeg (apt-get install ffmpeg / brew install ffmpeg) or set ffmpeg in the config file.",
            )
        )
    ffprobe_ok = bool(shutil.which(ffprobe_bin))
    checks.append(
        _record(
            "media.ffprobe",
            "ok" if ffprobe_ok else "fail",
            "ffprobe %s" % ("available" if ffprobe_ok else "missing"),
            facts={"requested": ffprobe_bin, "path": shutil.which(ffprobe_bin)},
            remedy=None if ffprobe_ok else "Install ffmpeg (ffprobe ships with it) or set ffprobe in the config file.",
        )
    )

    needed_encoders = set()
    codec = (spec.get("delivery") or {}).get("video_codec", "h264")
    if codec in ("h264", "libx264"):
        needed_encoders.add("libx264")
    elif codec in ("hevc", "h265", "libx265"):
        needed_encoders.add("libx265")
    elif codec in ("av1", "libsvtav1", "libaom-av1"):
        needed_encoders.update({"libsvtav1"})
    audio_codec = (spec.get("delivery") or {}).get("audio_codec", "aac")
    if audio_codec == "aac":
        needed_encoders.add("aac")
    elif audio_codec in ("opus", "libopus"):
        needed_encoders.add("libopus")
    if caps:
        missing = sorted(needed_encoders - caps["encoders"])
        checks.append(
            _record(
                "media.encoders",
                "ok" if not missing else "fail",
                "delivery encoders present" if not missing else "missing encoders: %s" % ", ".join(missing),
                facts={"required": sorted(needed_encoders), "missing": missing},
                remedy=None if not missing else "Install an ffmpeg build with %s, or change spec.delivery." % ", ".join(missing),
            )
        )
    else:
        checks.append(_record("media.encoders", "skip", "encoder check skipped (no ffmpeg)", facts={}))

    op = plan.get("operation")
    needed_filters = REQUIRED_FILTERS_BY_OP.get(op, [])
    if routing.get("requires_neural_backend") and spec.get("mode") != "backend":
        needed_filters = needed_filters + ["minterpolate"] if op == "interpolate" else needed_filters
    if caps and needed_filters:
        missing_filters = sorted(set(needed_filters) - caps["filters"])
        checks.append(
            _record(
                "media.filters",
                "ok" if not missing_filters else "warn",
                "required filters present" if not missing_filters else "filters unavailable: %s" % ", ".join(missing_filters),
                severity="warning",
                facts={"required": sorted(set(needed_filters)), "missing": missing_filters},
                remedy=None if not missing_filters else "Install an ffmpeg build with %s, or switch spec.mode to backend." % ", ".join(missing_filters),
            )
        )

    # 3. hardware --------------------------------------------------------
    hardware = detect_hardware(runner, spec)
    if routing.get("requires_neural_backend"):
        estimated = budget.get("vram_gb_estimate")
        if hardware.get("vram_gb") is None:
            status = "warn" if hardware.get("cpu_capable") else "fail"
            checks.append(
                _record(
                    "hardware.gpu",
                    status,
                    "no GPU detected; planning estimate %.1f GB VRAM" % (estimated or 0.0),
                    severity="warning",
                    facts={"vram_gb_estimate": estimated, "cpu_capable": hardware.get("cpu_capable")},
                    remedy="Install NVIDIA drivers, pass spec.hardware.vram_gb, or run with mode=proxy.",
                )
            )
        else:
            enough = hardware["vram_gb"] >= (budget.get("vram_gb_estimate") or 0)
            checks.append(
                _record(
                    "hardware.gpu",
                    "ok" if enough else "fail",
                    "GPU %.1f GB VRAM vs %.1f GB estimate"
                    % (hardware["vram_gb"], budget.get("vram_gb_estimate") or 0.0),
                    facts={"gpus": hardware["gpus"], "vram_gb_estimate": budget.get("vram_gb_estimate")},
                    remedy=None
                    if enough
                    else "Lower spec.params width/height/frames, choose a smaller model, or use the proxy path.",
                )
            )
    else:
        checks.append(
            _record(
                "hardware.gpu",
                "skip",
                "operation '%s' runs locally and needs no GPU" % op,
                facts={"gpus": hardware["gpus"], "source": hardware["source"]},
            )
        )
    checks.append(
        _record(
            "hardware.memory",
            "ok" if hardware.get("ram_gb") else "warn",
            "host RAM %s GB" % (hardware.get("ram_gb") or "unknown"),
            severity="warning",
            facts={"ram_gb": hardware.get("ram_gb"), "cpu_count": hardware.get("cpu_count")},
        )
    )

    # 4. disk ------------------------------------------------------------
    need_gb = float((budget.get("disk") or {}).get("total_free_required_gb") or 0)
    free_bytes = common.disk_free_bytes(spec["output"]["dir"]) or common.disk_free_bytes(spec.get("base_dir", "."))
    if free_bytes is None:
        checks.append(_record("storage.free_space", "warn", "free space could not be determined", severity="warning"))
    else:
        free_gb = free_bytes / 1024.0 / 1024.0 / 1024.0
        enough = free_gb >= need_gb
        checks.append(
            _record(
                "storage.free_space",
                "ok" if enough else "fail",
                "%.2f GB free vs %.2f GB required" % (free_gb, need_gb),
                facts={"free_gb": round(free_gb, 2), "required_gb": need_gb},
                remedy=None if enough else "Free disk space or lower spec.limits.max_output_mb.",
            )
        )

    # 5. output dir + overwrite -----------------------------------------
    out_dir = Path(spec["output"]["dir"])
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(str(out_dir), os.W_OK)
    except OSError:
        writable = False
    checks.append(
        _record(
            "storage.output_dir",
            "ok" if writable else "fail",
            "output directory %s" % ("writable" if writable else "not writable"),
            facts={"path": str(out_dir)},
            remedy=None if writable else "Create the directory with write permission or change spec.output.dir.",
        )
    )
    target = ctx.output_path(spec)
    if target.exists() and not ctx.overwrite:
        checks.append(
            _record(
                "storage.overwrite",
                "warn",
                "target output already exists: %s" % target,
                severity="warning",
                facts={"path": str(target)},
                remedy="Pass --overwrite (requires --approve) or change spec.output.filename / spec.job_id.",
            )
        )

    # 6. model files -----------------------------------------------------
    model_files = check_model_files(ctx, spec, plan, runner)
    checks.extend(model_files)

    # 7. inputs ----------------------------------------------------------
    for entry in spec.get("inputs") or []:
        path = Path(entry["path"])
        if not path.exists():
            checks.append(
                _record(
                    "input.exists:%s" % entry["role"],
                    "fail",
                    "input missing: %s" % path,
                    facts={"path": str(path), "role": entry["role"]},
                    remedy="Place the input file at the declared path or fix spec.inputs.",
                )
            )
            continue
        size = common.file_size(path) or 0
        probe = None
        if ffprobe_ok:
            try:
                probe = probe_streams(ctx, str(path), runner)
            except common.OnvError as err:
                checks.append(
                    _record(
                        "input.decodable:%s" % entry["role"],
                        "fail",
                        "input is not decodable: %s" % path.name,
                        facts={"path": str(path), "error": err.message},
                        remedy="Re-export the input with a supported codec, or check the file is not truncated.",
                    )
                )
                continue
        checks.append(
            _record(
                "input.decodable:%s" % entry["role"],
                "ok",
                "input %s (%s)%s"
                % (path.name, common.human_bytes(size), "" if not probe else ", %s/%s" % (probe.get("codec_name"), probe.get("width"))),
                facts={"path": str(path), "bytes": size, "probe": probe},
            )
        )

    # 8. backend command -------------------------------------------------
    backend_cmd = (spec.get("backend") or {}).get("command")
    if routing.get("requires_neural_backend") and spec.get("mode") in ("backend",):
        if not backend_cmd:
            checks.append(
                _record(
                    "backend.command",
                    "fail",
                    "mode=backend requires spec.backend.command",
                    facts={"mode": spec.get("mode")},
                    remedy="Provide spec.backend.command with {output} and {input_image}/{input_video} placeholders.",
                )
            )
        else:
            checks.append(
                _record(
                    "backend.command",
                    "ok",
                    "backend command configured",
                    facts={"command": backend_cmd if isinstance(backend_cmd, str) else " ".join(backend_cmd)},
                )
            )
    else:
        checks.append(
            _record(
                "backend.command",
                "skip",
                "no neural backend dispatch planned for mode=%s" % spec.get("mode"),
                facts={"mode": spec.get("mode")},
            )
        )

    # 9. rights / consent ------------------------------------------------
    rights_check = rights_gate_preview(ctx, spec, plan)
    checks.append(rights_check)

    summary = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1
    hard_fail = [c for c in checks if c["status"] == "fail"]
    report = {
        "job_id": spec["job_id"],
        "checked_at": common.utcnow(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "ffmpeg": caps["version"] if caps else None,
            "hardware": {k: hardware.get(k) for k in ("gpus", "ram_gb", "cpu_count", "vram_gb", "source")},
            "tool_version": common.TOOL_VERSION,
        },
        "checks": checks,
        "summary": summary,
        "ready": not hard_fail,
        "blocking": [{"id": c["id"], "summary": c["summary"], "remedy": c["remedy"]} for c in hard_fail],
        "warnings": [{"id": c["id"], "summary": c["summary"]} for c in checks if c["status"] == "warn"],
    }
    if reporter is not None:
        reporter.emit(report, preflight_lines(report))
    return report


def probe_streams(ctx, path, runner):
    """Minimal ffprobe wrapper used by preflight only."""
    common = ctx.common
    ffprobe_bin = ctx.config.get("ffprobe", "ffprobe")
    result = runner.run(
        [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout_s=120,
    )
    doc = _parse_json(common, result["stdout"], path)
    video = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in doc.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        common.fail("E_INPUT", "no audio or video stream in %s" % path)
    info = {
        "duration_s": _float((doc.get("format") or {}).get("duration")),
        "format_name": (doc.get("format") or {}).get("format_name"),
        "has_audio": audio is not None,
    }
    if video:
        info.update(
            {
                "codec_name": video.get("codec_name"),
                "width": video.get("width"),
                "height": video.get("height"),
                "fps": _fps(video),
                "pix_fmt": video.get("pix_fmt"),
                "nb_frames": _int(video.get("nb_frames")),
            }
        )
    else:
        info.update({"codec_name": (audio or {}).get("codec_name"), "width": None, "height": None})
    return info


def _parse_json(common, text, path):
    import json as _json

    try:
        return _json.loads(text)
    except ValueError:
        common.fail("E_INPUT", "ffprobe returned unparsable output for %s" % path)


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _fps(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if raw and "/" in str(raw):
            num, den = str(raw).split("/", 1)
            try:
                if float(den) != 0:
                    return round(float(num) / float(den), 3)
            except ValueError:
                continue
    return _float(stream.get("r_frame_rate"))


def check_model_files(ctx, spec, plan, runner):
    common = ctx.common
    checks = []
    routing = plan.get("routing", {})
    model = routing.get("model") or {}
    weights = model.get("weights") or {}
    root = Path(spec["models"]["root"])
    declared = spec["models"].get("files") or []
    check_hashes = bool(spec["models"].get("check_hashes", True))

    if not routing.get("requires_neural_backend") and not declared:
        checks.append(
            _record(
                "models.files",
                "skip",
                "no neural model required for this operation (mode=%s)" % spec.get("mode"),
                facts={"mode": spec.get("mode")},
            )
        )
        return checks

    globs = weights.get("filename_globs") or []
    found = []
    if root.exists() and globs:
        for pattern in globs:
            found.extend(sorted(str(p) for p in root.glob(pattern) if p.is_file()))
    found = sorted(set(found))

    if declared:
        for entry in declared:
            if isinstance(entry, str):
                entry = {"path": entry}
            path = Path(entry.get("path") or "")
            if not path.is_absolute():
                path = root / path
            if not path.exists():
                checks.append(
                    _record(
                        "models.declared:%s" % path.name,
                        "fail",
                        "declared model file missing: %s" % path,
                        facts={"path": str(path)},
                        remedy="Copy the weights into spec.models.root or remove the declaration.",
                    )
                )
                continue
            size = common.file_size(path) or 0
            facts = {"path": str(path), "bytes": size}
            status, summary = "ok", "declared model file present: %s (%s)" % (path.name, common.human_bytes(size))
            expected = entry.get("sha256")
            if check_hashes and expected:
                actual = common.sha256_file(path)
                facts.update({"expected_sha256": expected, "actual_sha256": actual})
                if actual.lower() != str(expected).lower():
                    status = "fail"
                    summary = "sha256 mismatch for %s" % path.name
            checks.append(
                _record(
                    "models.declared:%s" % path.name,
                    status,
                    summary,
                    facts=facts,
                    remedy=None if status == "ok" else "Re-download or re-copy the weights and update the declared hash.",
                )
            )

    if routing.get("requires_neural_backend"):
        if found:
            total = sum(common.file_size(p) or 0 for p in found if Path(p).is_file())
            checks.append(
                _record(
                    "models.weights",
                    "ok",
                    "registry weights found: %s (%s)" % (", ".join(Path(p).name for p in found[:4]), common.human_bytes(total)),
                    facts={"matches": found[:32], "bytes": total, "model_id": model.get("id")},
                )
            )
        elif not declared:
            status = "fail" if spec.get("mode") == "backend" else "warn"
            checks.append(
                _record(
                    "models.weights",
                    status,
                    "no weights matched registry globs %s under %s" % (globs, root),
                    severity="warning" if status == "warn" else "error",
                    facts={"model_id": model.get("id"), "glob patterns": globs, "models_root": str(root)},
                    remedy="Place the pinned weights under the models root, or run with mode=proxy / fallback.on_backend_unavailable=proxy.",
                )
            )
    return checks


def rights_gate_preview(ctx, spec, plan):
    rights = spec.get("rights") or {}
    if not rights.get("require_consent"):
        return _record(
            "rights.consent",
            "skip",
            "consent gate disabled by spec.rights.require_consent=false",
            facts={"subject_ids": rights.get("subject_ids") or []},
        )
    if not rights.get("subject_ids"):
        return _record(
            "rights.consent",
            "fail",
            "require_consent=true but no subject_ids declared",
            facts={},
            remedy="Declare every depicted subject in spec.rights.subject_ids.",
        )
    ledger_path = rights.get("ledger")
    if not ledger_path:
        return _record(
            "rights.consent",
            "fail",
            "require_consent=true but spec.rights.ledger is not set",
            facts={},
            remedy="Point spec.rights.ledger at the rights ledger JSON file.",
        )
    try:
        ledger = ctx.common.json_load(ledger_path, label="rights ledger", required=False)
    except ctx.common.OnvError as err:
        return _record("rights.consent", "fail", "rights ledger unreadable: %s" % err.message, facts={"ledger": ledger_path})
    governance = ctx.load("governance")
    gate = governance.evaluate_consent(ledger or {"records": []}, rights["subject_ids"], rights.get("allowed_use"))
    if gate["ok"]:
        return _record("rights.consent", "ok", "consent granted for %d subject(s)" % len(rights["subject_ids"]), facts=gate)
    return _record(
        "rights.consent",
        "fail",
        "consent gate would block this job: %s" % "; ".join(gate["reasons"]),
        facts=gate,
        remedy="Record consent with: tools-onv.py ledger add --file <ledger> --subject <id> --status granted --scope <use> --evidence <ref> --approve",
    )


def preflight_lines(report):
    icons = {"ok": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    lines = [
        "Preflight for job '%s' (%s)" % (report["job_id"], report["checked_at"]),
        "-" * 78,
    ]
    for check in report["checks"]:
        lines.append("  [%s] %-28s %s" % (icons.get(check["status"], "????"), check["id"], check["summary"]))
        if check["status"] in ("fail", "warn") and check.get("remedy"):
            lines.append("         remedy: %s" % check["remedy"])
    lines.append("-" * 78)
    summary = report["summary"]
    lines.append(
        "Result: %s  (ok=%d warn=%d fail=%d skip=%d)"
        % ("READY" if report["ready"] else "BLOCKED", summary.get("ok", 0), summary.get("warn", 0), summary.get("fail", 0), summary.get("skip", 0))
    )
    return lines
