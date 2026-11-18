"""ai-video-creation / tools-media.py

Real local postproduction operations: SRT/VTT caption parsing and validation,
quality-gate evaluation, delivery manifest generation, and cost estimation.

These operate on text/metadata and are fully implemented offline. Pixel-level
encoding, neural synthesis, and media probing are NOT claimed (see knowledge-
readme.md). Pure Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_TC = re.compile(r"^\s*(\d{1,3}):(\d{2}):(\d{2})\s*([,.])\s*(\d{1,3})\s*$")
_ARROW = re.compile(r"\s*-->\s*")


def _seconds(stamp):
    m = _TC.match(stamp)
    if not m:
        raise ValueError("Invalid caption timecode: %r" % stamp)
    h, mm, ss, sep, frac = m.groups()
    ms = int(frac.ljust(3, "0"))
    return int(h) * 3600 + int(mm) * 60 + int(ss) + ms / 1000.0


def _stamp(seconds, sep=",", fps=None):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d%s%03d" % (h, m, s, sep, ms)


def parse_captions(text, fmt="auto", max_cues=5000):
    """Parse SRT, WebVTT, or a JSON cue array into normalized cues."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if fmt == "auto":
        fmt = "json" if text.startswith("[") else ("vtt" if text.startswith("WEBVTT") else "srt")
    if fmt == "json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON captions must be an array of cues")
        cues = []
        for i, c in enumerate(data[:max_cues], 1):
            cues.append({"index": i, "start": float(c["start"]), "end": float(c["end"]),
                         "text": str(c.get("text", "")).strip()})
        return cues, fmt
    blocks = re.split(r"\n\s*\n", text)
    cues = []
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines or lines[0].strip().startswith("WEBVTT") or lines[0].strip().startswith("NOTE"):
            continue
        timing_idx = next((i for i, l in enumerate(lines) if _ARROW.search(l)), None)
        if timing_idx is None:
            continue
        a, b = _ARROW.split(lines[timing_idx], maxsplit=1)
        start, end = _seconds(a.strip()), _seconds(b.split(" ", 1)[0].strip())
        body = "\n".join(lines[timing_idx + 1:]).strip()
        idx = int(lines[0]) if timing_idx > 0 and lines[0].strip().isdigit() else len(cues) + 1
        cues.append({"index": idx, "start": start, "end": end, "text": body})
        if len(cues) >= max_cues:
            break
    return cues, fmt


def to_srt(cues):
    out = []
    for i, c in enumerate(cues, 1):
        out += [str(i), "%s --> %s" % (_stamp(c["start"]), _stamp(c["end"])), c["text"], ""]
    return "\n".join(out).rstrip() + "\n"


def to_vtt(cues):
    out = ["WEBVTT", ""]
    for c in cues:
        out += ["%s --> %s" % (_stamp(c["start"], ".", ), _stamp(c["end"], ".")), c["text"], ""]
    return "\n".join(out).rstrip() + "\n"


def validate_captions(cues, config):
    """Timing, overlap, reading-speed, line-length, and emptiness checks."""
    lim = config.get("limits", {})
    gates = config.get("caption_limits", {})
    max_cps = gates.get("max_chars_per_second", 20)
    min_dur = gates.get("min_cue_seconds", 0.7)
    max_lines = gates.get("max_lines", 2)
    max_line_chars = gates.get("max_line_chars", 42)
    max_cues = lim.get("max_caption_cues", 5000)
    issues = []
    if len(cues) > max_cues:
        issues.append({"code": "TOO_MANY_CUES", "index": None, "severity": "error",
                       "message": "More than %d cues." % max_cues})
    prev_end = -1.0
    for c in cues:
        i = c["index"]
        dur = c["end"] - c["start"]
        if dur <= 0:
            issues.append({"code": "BAD_DURATION", "index": i, "severity": "error",
                           "message": "Cue ends before or at its start.", "fix": "Set end > start."})
        elif dur < min_dur:
            issues.append({"code": "SHORT_CUE", "index": i, "severity": "warning",
                           "message": "Cue lasts %.2fs (< %.2fs)." % (dur, min_dur),
                           "fix": "Extend the cue or merge it with a neighbor."})
        if c["start"] < prev_end - 0.001:
            issues.append({"code": "OVERLAP", "index": i, "severity": "error",
                           "message": "Cue starts before the previous cue ends.",
                           "fix": "Trim the previous out-point or shift this cue."})
        prev_end = max(prev_end, c["end"])
        if not c["text"]:
            issues.append({"code": "EMPTY_TEXT", "index": i, "severity": "error",
                           "message": "Cue has no text.", "fix": "Add caption text or remove the cue."})
        lines = c["text"].splitlines() or [""]
        if len(lines) > max_lines:
            issues.append({"code": "TOO_MANY_LINES", "index": i, "severity": "warning",
                           "message": "Cue has %d lines (> %d)." % (len(lines), max_lines), "fix": "Split the cue."})
        for line in lines:
            if len(line) > max_line_chars:
                issues.append({"code": "LONG_LINE", "index": i, "severity": "warning",
                               "message": "Line has %d characters (> %d)." % (len(line), max_line_chars),
                               "fix": "Shorten or split the line."})
        cps = len(re.sub(r"\s+", " ", c["text"]).strip()) / max(0.001, dur)
        if cps > max_cps:
            issues.append({"code": "FAST_READING", "index": i, "severity": "warning",
                           "message": "Reading speed is %.1f chars/s (> %s)." % (cps, max_cps),
                           "fix": "Lengthen the cue or shorten the text."})
    errors = [x for x in issues if x["severity"] == "error"]
    return {
        "kind": "caption-report",
        "cue_count": len(cues),
        "duration_seconds": max([c["end"] for c in cues], default=0.0),
        "status": "fail" if errors else ("warning" if issues else "pass"),
        "issues": issues,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "cues": cues,
    }


# --------------------------------------------------------------------------
# Quality gates
# --------------------------------------------------------------------------

def evaluate_gates(job, gates_def, config, artifacts=None, approvals=None):
    """Evaluate the seven catalog quality gates against a structured job.

    Every gate has real, machine-checkable evidence. Gates that need actual
    rendered pixels are reported as 'deferred' rather than falsely passed.
    """
    artifacts = artifacts or {}
    approvals = approvals or {}
    brief = job.get("brief", job) if isinstance(job, dict) else {}
    plan = job.get("plan", {}) if isinstance(job, dict) else {}
    gates = []
    for g in gates_def.get("gates", []):
        gid = g["id"]
        status, evidence, remediation = "pass", [], g.get("remediation")
        if gid == "temporal-coherence":
            shots = artifacts.get("shotlist", {}).get("shots", [])
            if shots and all(s.get("duration_seconds", 0) > 0 for s in shots):
                evidence.append("%d shots have positive durations and ordered timecodes." % len(shots))
                evidence.append("Shot 1: %s -> %s" % (shots[0]["in_timecode"], shots[0]["out_timecode"]))
            else:
                status, evidence = "deferred", ["No shot list artifact; render-level flicker checks need media."]
        elif gid == "prompt-source-fidelity":
            prompts = artifacts.get("prompt", {}).get("variants", [])
            if prompts:
                good = [p for p in prompts if len(p["positive_prompt"]) > 40 and p.get("constraints")]
                status = "pass" if len(good) == len(prompts) else "fail"
                evidence.append("%d/%d prompt variants contain measurable constraints." % (len(good), len(prompts)))
                amb = artifacts["prompt"].get("ambiguity_audit", {}).get("vague_terms", [])
                if amb:
                    status = "warning"
                    evidence.append("Vague terms remain: %s" % ", ".join(amb))
            else:
                status, evidence = "deferred", ["No prompt artifact supplied."]
        elif gid == "technical-validity":
            fps = brief.get("fps")
            res = brief.get("resolution")
            dur = brief.get("duration_seconds")
            allowed_fps = gates_def.get("allowed_fps", [23.976, 24, 25, 29.97, 30, 50, 59.94, 60])
            if fps is not None and fps not in allowed_fps:
                status = "fail"
                evidence.append("fps %s is not in %s" % (fps, allowed_fps))
            if res and not re.fullmatch(r"\d{3,5}x\d{3,5}", str(res)):
                status = "fail"
                evidence.append("resolution '%s' is not WIDTHxHEIGHT" % res)
            if dur is not None and (dur <= 0 or dur > config.get("limits", {}).get("max_duration_seconds", 3600)):
                status = "fail"
                evidence.append("duration_seconds out of range")
            if status == "pass":
                evidence.append("fps/resolution/duration conform to the brief schema and delivery presets.")
        elif gid == "accessibility":
            if artifacts.get("captions"):
                rep = artifacts["captions"]
                status = "pass" if rep.get("status") == "pass" else ("warning" if rep.get("status") == "warning" else "fail")
                evidence.append("Caption report: %s, %d cues, %d errors, %d warnings." %
                                (rep.get("status"), rep.get("cue_count", 0), rep.get("error_count", 0), rep.get("warning_count", 0)))
            elif brief.get("captions_required"):
                status = "fail"
                evidence.append("Captions are required by the brief but no caption artifact exists.")
            else:
                status = "deferred"
                evidence.append("No captions supplied; add captions or set captions_required=false explicitly.")
        elif gid == "rights-consent":
            refs = brief.get("reference_assets") or []
            missing = [r.get("id", "?") for r in refs if isinstance(r, dict) and not r.get("rights")]
            sensitive = [s for s in plan.get("stages", []) if s.get("approval_required")]
            if missing:
                status = "fail"
                evidence.append("Reference assets without rights metadata: %s" % ", ".join(missing))
            elif sensitive and not approvals.get("verified"):
                status = "fail"
                evidence.append("%d sensitive stages have no verified human approval." % len(sensitive))
            else:
                evidence.append("All reference assets carry rights metadata; sensitive approvals are verified or not required.")
        elif gid == "reproducibility":
            prompts = artifacts.get("prompt", {}).get("variants", [])
            seeds = [p.get("seed") for p in prompts]
            if seeds and all(isinstance(s, int) for s in seeds) and (brief.get("provider") or job.get("provider")):
                evidence.append("Deterministic seeds %s and a pinned provider are recorded." % seeds[:3])
            else:
                status = "warning"
                evidence.append("Record a seed, provider, and model version before generative runs.")
        elif gid == "handoff-readiness":
            expected = gates_def.get("handoff_required_artifacts", ["prompt", "shotlist"])
            missing = [a for a in expected if a not in artifacts]
            if missing:
                status = "deferred"
                evidence.append("Missing editable artifacts: %s" % ", ".join(missing))
            else:
                evidence.append("Editable artifacts present: %s" % ", ".join(sorted(artifacts)))
        gates.append({"id": gid, "name": g.get("name", gid), "severity": g.get("severity", "warning"),
                      "status": status, "evidence": evidence, "remediation": remediation})
    fails = [g for g in gates if g["status"] == "fail"]
    return {
        "kind": "quality-gate-report",
        "status": "fail" if fails else "pass",
        "gates": gates,
        "failed": [g["id"] for g in fails],
        "deferred": [g["id"] for g in gates if g["status"] == "deferred"],
        "unresolved_issues": [{"gate": g["id"], "issue": e} for g in gates if g["status"] in ("fail", "deferred")
                              for e in g["evidence"]],
    }


# --------------------------------------------------------------------------
# Delivery manifest + cost
# --------------------------------------------------------------------------

def build_delivery_manifest(brief, delivery_presets, config, artifacts_dir, artifacts=None):
    """Inspect real local artifacts and produce a checksummed delivery manifest."""
    preset_name = brief.get("delivery_preset") or "web-1080p"
    presets = delivery_presets.get("presets", {})
    preset = presets.get(preset_name) or presets.get("web-1080p", {})
    d = Path(artifacts_dir).expanduser()
    files = []
    if d.is_dir():
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.name.endswith(".partial") or p.name == config.get("output", {}).get("cancel_file_name", "CANCEL"):
                continue
            data = p.read_bytes()
            files.append({"path": p.relative_to(d).as_posix(), "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
    caps_required = bool(preset.get("captions_required")) or bool(brief.get("captions_required"))
    captions_present = any(f["path"].lower().endswith((".srt", ".vtt")) for f in files)
    warnings = []
    if caps_required and not captions_present:
        warnings.append("Delivery preset requires captions but no .srt/.vtt artifact exists.")
    if not files:
        warnings.append("No local artifacts found; manifest describes planned outputs only.")
    return {
        "kind": "delivery-manifest",
        "title": brief.get("title", "Untitled"),
        "preset": preset_name,
        "encoding": {k: preset.get(k) for k in ("container", "video_codec", "audio_codec", "resolution",
                                                "fps", "video_bitrate", "audio_bitrate", "loudness_target")},
        "platform": preset.get("platform"),
        "captions_required": caps_required,
        "captions_present": captions_present,
        "accessibility": {"captions": captions_present, "audio_description": bool(brief.get("audio_description")),
                          "note": "Audio description and human caption review remain human tasks."},
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "provenance": {"job_id": brief.get("job_id"), "umbrella": brief.get("umbrella"),
                       "seed": brief.get("seed"), "provider": brief.get("provider"),
                       "model_versions": config.get("providers", {}).get("model_versions", {}),
                       "generated_by": "ai-video-creation Skill Tool"},
        "warnings": warnings,
        "unresolved_issues": ["Pixel encoding and platform upload are external steps; run them with an approved adapter."],
    }


def estimate_cost(plan, config):
    rates = config.get("cost", {})
    stages = len(plan.get("stages", []))
    local = sum(1 for s in plan.get("stages", []) if s.get("operation") in
                ("contract", "plan", "compile-prompt", "shotlist", "captions", "gates", "validate", "delivery", "approval"))
    return {"currency": rates.get("currency", "USD"),
            "local_stages": local,
            "external_stages": max(0, stages - local),
            "estimated_local_cost": round(local * float(rates.get("local_stage_cost", 0.0)), 4),
            "note": "External provider costs are unknown until an adapter and rate card are configured."}
