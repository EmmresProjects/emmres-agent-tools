#!/usr/bin/env python3
"""Media Intelligence Workbench - primary entry point.

A production-grade, dependency-light Skill Tool for interpreting, measuring,
validating and governing image, video, audio, subtitle and multimodal media
work. It is the runnable component of a larger Copilot skill and never assumes
a model, network connection or paid service.

Quick start
-----------
    python3 tools-miw.py help
    python3 tools-miw.py capabilities
    python3 tools-miw.py plan --category 9 --out media-plans/quality.json
    python3 tools-miw.py inspect media-input/clip.mp4
    python3 tools-miw.py analyze-audio media-input/take.wav --out media-reports/take.json

Every command supports --json (default for machine-readable output) and
--out PATH. Commands that create or modify files require --approve unless the
config sets policy.approval_mode to "auto".

Exit codes
----------
    0 success
    1 completed with failures/warnings needing attention
    2 usage or input validation error
    3 blocked by a policy gate (approval, safety, release gate)
    4 required optional integration is unavailable (e.g. ffmpeg for video frames)
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.0.0"
TOOL_NAME = "media-intelligence-workbench"

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_USAGE = 2
EXIT_GATE = 3
EXIT_INTEGRATION = 4


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


io = _load("tools_media_io_module", "tools-media-io.py")
metrics = _load("tools_metrics", "tools-metrics.py")
planner = _load("tools_plan", "tools-plan.py")
intake = _load("tools_intake", "tools-intake.py")

OPERATIONS = [
    ("help", "Show usage, operations and exit codes."),
    ("capabilities", "List this Skill Tool's operations and the capability catalog summary."),
    ("parse", "Parse a pasted plain-text/Markdown/HTML capability request into structured skills."),
    ("plan", "Build a unified, evidence-first work plan from a request, category or query."),
    ("probe", "Technical probe: container, codec, dimensions, duration, channels, limits."),
    ("inspect", "Ingestion safety scan + hash + technical probe for files or folders."),
    ("ingest", "Batch ingest: safety scan, hashes, asset graph, checkpointed manifest."),
    ("analyze-image", "Image quality, sharpness, noise, exposure, colour, banding, palette, hash."),
    ("analyze-audio", "Audio levels, clipping, silence map, hum, spectral balance, loudness, tempo."),
    ("analyze-video", "Video probe plus frame-sequence analysis (frames dir or ffmpeg adapter)."),
    ("subtitle-audit", "Caption/subtitle timing, reading speed, overlap, line-length and gap audit."),
    ("accessibility", "Flash/strobe risk over a frame series and WCAG contrast checks."),
    ("conformance", "Validate a file against a delivery specification JSON."),
    ("index", "Build and query a local media index (metadata, hashes, palette, perceptual hash)."),
    ("report", "Assemble a validated evidence-first report from plan + measurement outputs."),
    ("evaluate", "Validate a report and score evidence coverage, grounding and abstention."),
    ("validate", "Validate a config, plan or report document."),
    ("self-test", "Run the built-in end-to-end self-test in a temporary workspace."),
]

# --------------------------------------------------------------------------
# envelope / output helpers
# --------------------------------------------------------------------------


class CliError(Exception):
    def __init__(self, message, code=EXIT_USAGE, hint=None, kind="error"):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.kind = kind

    def to_dict(self):
        return {"kind": self.kind, "code": self.code, "message": self.message,
                "hint": self.hint}


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def envelope(operation, data, warnings=None, errors=None):
    return {
        "ok": not (errors or []),
        "tool": TOOL_NAME,
        "version": VERSION,
        "operation": operation,
        "generated_at": now_iso(),
        "data": data,
        "warnings": warnings or [],
        "errors": errors or [],
    }


@contextlib.contextmanager
def time_limit(seconds, label="stage"):
    """Raise CliError(EXIT_FAILURES) when a stage overruns its budget (POSIX)."""
    if not seconds or not hasattr(signal, "setitimer"):
        yield
        return

    def handler(signum, frame):
        raise TimeoutError("%s exceeded %ss budget" % (label, seconds))

    previous = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def write_output(path, payload, approve, config):
    """Write JSON or text payload with the approval gate applied."""
    if path is None:
        return None
    path = os.path.abspath(path)
    over = config.get("policy", {}).get("allow_overwrite_outputs", False)
    if os.path.exists(path) and not over:
        raise CliError("output already exists: %s" % path, EXIT_USAGE,
                       hint="pass a new --out path or set "
                            "policy.allow_overwrite_outputs=true")
    require_approval("write %s" % path, approve, config)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if isinstance(payload, (dict, list)):
        body = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        body = str(payload)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body.rstrip("\n") + "\n")
    os.replace(tmp, path)
    return path


def require_approval(action, approve, config):
    """Gate destructive/side-effecting work behind explicit approval."""
    mode = config.get("policy", {}).get("approval_mode", "prompt")
    if approve or mode == "auto":
        return True
    if mode == "deny":
        raise CliError("approval required for %s and approval_mode=deny" % action,
                       EXIT_GATE, hint="re-run with --approve")
    if sys.stdin.isatty():
        sys.stderr.write("Approve %s? [y/N] " % action)
        sys.stderr.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer in ("y", "yes"):
            return True
        raise CliError("approval declined for %s" % action, EXIT_GATE)
    raise CliError(
        "approval required for %s (non-interactive run)" % action, EXIT_GATE,
        hint="re-run with --approve in unattended pipelines",
    )


def emit(payload, out_path=None, human=None, quiet=False):
    """Print JSON to stdout (and the human summary when present)."""
    if out_path is None:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    if human and not quiet:
        sys.stderr.write(human.rstrip("\n") + "\n")


# --------------------------------------------------------------------------
# config / catalog context
# --------------------------------------------------------------------------


def load_config(path=None):
    cfg_path = path or os.environ.get("MIW_CONFIG") or os.path.join(
        HERE, "asset-default-config.json")
    if not os.path.isfile(cfg_path):
        raise CliError("config file not found: %s" % cfg_path, EXIT_USAGE,
                       hint="copy asset-default-config.json and pass --config")
    config = planner.load_json(cfg_path, "config")
    for key in ("paths", "limits", "runtime", "policy"):
        if key not in config:
            raise CliError("config missing required section %r" % key, EXIT_USAGE)
    config["_config_path"] = os.path.abspath(cfg_path)
    base = os.path.dirname(os.path.abspath(cfg_path))
    config["_base_dir"] = base
    return config


def resolve_asset(config, key, default_name):
    """Resolve a catalog/policy asset path from config, then package root."""
    rel = config.get("paths", {}).get(key, default_name)
    for base in (os.getcwd(), config.get("_base_dir"), HERE):
        cand = os.path.abspath(os.path.join(base, rel))
        if os.path.isfile(cand):
            return cand
    fallback = os.path.join(HERE, default_name)
    if os.path.isfile(fallback):
        return fallback
    raise CliError("cannot resolve asset %r (%s)" % (key, rel), EXIT_USAGE,
                   hint="check paths.%s in the config file" % key)


def context(config):
    catalog = planner.load_catalog(resolve_asset(config, "catalog",
                                                 "asset-capability-catalog.json"))
    policies = planner.load_json(resolve_asset(config, "policies",
                                               "asset-policy-presets.json"), "policies")
    return catalog, policies


# --------------------------------------------------------------------------
# file selection helpers
# --------------------------------------------------------------------------


def collect_inputs(targets, config):
    """Expand files/folders into a bounded list of media candidates."""
    limit = int(config.get("limits", {}).get("max_files", 1000))
    files = []
    missing = []
    for t in targets:
        if os.path.isdir(t):
            files.extend(io.list_media_files(t, recursive=True, limit=limit + 1))
        elif os.path.isfile(t):
            files.append(t)
        else:
            missing.append(t)
    if missing:
        raise CliError("input path(s) not found: %s" % ", ".join(missing), EXIT_USAGE,
                       hint="check the path or use a relative path from the "
                            "future skill root")
    if len(files) > limit:
        raise CliError("input contains %d files, over limits.max_files=%d"
                       % (len(files), limit), EXIT_USAGE,
                       hint="raise limits.max_files or narrow the selection")
    return files


def _size_guard(path, config):
    limit = int(config.get("limits", {}).get("max_file_bytes", 4 * 1024 ** 3))
    size = os.path.getsize(path)
    if size > limit:
        raise CliError("file exceeds limits.max_file_bytes: %s (%d bytes)" % (path, size),
                       EXIT_USAGE)
    return size


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------


def op_parse(args, config):
    text = None
    if args.infile:
        if not os.path.isfile(args.infile):
            raise CliError("request file not found: %s" % args.infile, EXIT_USAGE)
        with open(args.infile, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif args.text:
        text = args.text
    else:
        raise CliError("parse needs --in FILE or --text STRING", EXIT_USAGE)
    parsed = intake.parse_request(
        text, int(config.get("limits", {}).get("max_text_chars", 2_000_000)))
    warnings = list(parsed.get("warnings", []))
    if args.out:
        write_output(args.out, parsed, args.approve, config)
        warnings.append("parsed request written to %s" % args.out)
    human = "%d categories, %d capabilities, umbrella=%r" % (
        parsed["category_count"], parsed["skill_count"], parsed["umbrella"])
    return envelope("parse", parsed, warnings=warnings), human


def op_plan(args, config):
    catalog, policies = context(config)
    preset = args.preset or config.get("policy", {}).get("preset", "standard")
    request_text = ""
    warnings = []
    matches = []
    if args.infile or args.text:
        text = args.text
        if args.infile:
            if not os.path.isfile(args.infile):
                raise CliError("request file not found: %s" % args.infile, EXIT_USAGE)
            with open(args.infile, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        request_text = text
        parsed = intake.parse_request(
            text, int(config.get("limits", {}).get("max_text_chars", 2_000_000)))
        seen = set()
        for skill in parsed["skills"]:
            probe = "%s %s" % (skill["name"], skill["description"])
            for row in planner.search(catalog, probe, limit=1):
                if row["skill_id"] not in seen:
                    seen.add(row["skill_id"])
                    matches.append(row)
            else:
                warnings.append("no catalog match for requested capability %r"
                                % skill["name"])
        if not matches:
            # free-form request text (no list markers): fall back to keyword search
            matches = planner.search(catalog, text, limit=args.max_units)
            if matches:
                warnings.append("request parsed as free text; matched %d catalog "
                                "entries by keyword" % len(matches))
    elif args.category:
        cat = planner.resolve_categories(catalog, args.category)
        if cat is None:
            raise CliError("unknown category %r" % args.category, EXIT_USAGE,
                           hint="run `capabilities --categories` for valid ids")
        rows = planner.flatten(catalog)
        matches = [r for r in rows if r["category_id"] == cat["id"]]
        request_text = "category %s" % cat["name"]
    elif args.query:
        matches = planner.search(catalog, args.query, limit=args.max_units)
        request_text = args.query
    else:
        raise CliError("plan needs --in FILE, --text STRING, --category ID or --query TEXT",
                       EXIT_USAGE)

    if args.max_units and len(matches) > args.max_units:
        matches = matches[:args.max_units]
        warnings.append("plan truncated to the first %d units" % args.max_units)
    plan = planner.build_plan(matches, config, catalog, policies,
                              request_text=request_text, policy_preset=preset)
    errors = planner.validate_plan(plan, catalog, policies)
    if errors:
        raise CliError("generated plan failed validation: %s" % "; ".join(errors),
                       EXIT_USAGE)
    if args.out:
        write_output(args.out, plan, args.approve, config)
    if args.format == "md" and args.out:
        md = render_plan_markdown(plan)
        md_path = os.path.splitext(args.out)[0] + ".md"
        write_output(md_path, md, args.approve, config)
        warnings.append("markdown plan written to %s" % md_path)
    human = ("plan: %d units across %d categories; gates: %s"
             % (len(plan["units"]), len(plan["catalog"]["categories_touched"]),
                ", ".join(g["id"] for g in plan["gates"])))
    return envelope("plan", plan, warnings=warnings), human


def render_plan_markdown(plan):
    lines = ["# Work plan: %s" % plan["umbrella"], "",
             "- Policy preset: `%s` (%s)" % (plan["policy_preset"], plan["policy_summary"]),
             "- Units: %d" % len(plan["units"]),
             "- Categories: %s" % ", ".join(plan["catalog"]["categories_touched"]),
             "- Human approval required: %s" % ("yes" if plan["requires_human_approval"] else "no"),
             "", "## Analysis units", ""]
    for u in plan["units"]:
        lines.append("### %s %s (`%s`)" % (u["unit_id"], u["skill_name"], u["skill_id"]))
        lines.append("")
        lines.append("*Category:* %s  \n*Responsibility:* %s" % (u["category"], u["responsibility"]))
        lines.append("")
        lines.append("Steps:")
        for s in u["analysis_steps"]:
            lines.append("1. %s" % s)
        lines.append("")
        lines.append("Evidence required: %s" % ", ".join(u["required_evidence"]))
        lines.append("")
        lines.append("Produces: %s" % ", ".join(u["produces"]))
        if u["gates"]:
            lines.append("")
            lines.append("Gates: " + "; ".join("%s (%s)" % (g["id"], g["title"])
                                               for g in u["gates"]))
        if u["external_dependency"]:
            lines.append("")
            lines.append("External dependency: %s" % u["external_dependency"])
        lines.append("")
    lines.append("## Pipeline")
    lines.append("")
    for stage in plan["pipeline"]:
        lines.append("- **%s** - %s" % (stage["stage"], stage["description"]))
    lines.append("")
    lines.append("## Abstention rules")
    lines.append("")
    for r in plan["abstention_rules"]:
        lines.append("- %s" % r)
    return "\n".join(lines) + "\n"


def op_probe(args, config):
    files = collect_inputs(args.paths, config)
    results = []
    warnings = []
    errors = []
    timeout = float(config.get("runtime", {}).get("stage_timeout_s", 900))
    for path in files:
        try:
            _size_guard(path, config)
            with time_limit(timeout, "probe %s" % os.path.basename(path)):
                results.append(io.probe(path))
        except (CliError, io.MediaError, TimeoutError, OSError) as exc:
            errors.append({"path": path, "error": str(exc)})
    if args.out:
        write_output(args.out, {"files": results, "errors": errors}, args.approve, config)
    data = {"file_count": len(files), "files": results, "failures": errors,
            "optional_dependencies": io.optional_dependencies()}
    human = "probed %d file(s), %d failure(s)" % (len(results), len(errors))
    return envelope("probe", data, warnings=warnings, errors=errors), human


def op_inspect(args, config):
    files = collect_inputs(args.paths, config)
    limits = config.get("limits", {})
    rows = []
    warnings = []
    blocked = []
    errors = []
    for path in files:
        size = _size_guard(path, config)
        try:
            scan = io.safety_scan(
                path,
                max_pixels=int(limits.get("max_pixels", 80_000_000)),
                max_uncompressed_ratio=int(limits.get("max_uncompressed_ratio", 200)),
            )
        except (io.MediaError, OSError) as exc:
            errors.append({"path": path, "error": str(exc)})
            blocked.append(path)
            continue
        row = {
            "path": path,
            "size_bytes": size,
            "sha256": io.sha256_file(path, max_bytes=size),
            "safety": scan,
        }
        if args.deep:
            row["probe"] = io.probe(path)
        rows.append(row)
        if scan["verdict"] == "block":
            blocked.append(path)
        elif scan["verdict"] == "review":
            warnings.append("%s needs review: %s" % (path, ", ".join(
                f["code"] for f in scan["findings"] if f["severity"] != "info")))
    if args.out:
        write_output(args.out, {"files": rows}, args.approve, config)
    data = {"file_count": len(files), "files": rows, "blocked": blocked,
            "failures": errors}
    human = "inspected %d file(s); blocked: %d" % (len(files), len(blocked))
    env = envelope("inspect", data, warnings=warnings, errors=errors)
    if blocked:
        env["ok"] = False
        env["errors"].append({"kind": "safety", "code": EXIT_GATE,
                              "message": "ingestion blocked for: %s" % ", ".join(blocked),
                              "hint": "quarantine or repair the flagged files first"})
    return env, human


def op_ingest(args, config):
    files = collect_inputs(args.paths, config)
    out_dir = args.out_dir or config["paths"].get("plan_dir", "media-plans")
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = args.out or os.path.join(out_dir, "ingestion-manifest.json")
    ckpt_path = os.path.join(out_dir, "checkpoints.json")
    state = {"files": {}, "updated_at": now_iso()}
    if args.resume and os.path.isfile(ckpt_path):
        with open(ckpt_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    require_approval("write ingestion manifest %s" % manifest_path, args.approve, config)
    limits = config.get("limits", {})
    retries = int(config.get("runtime", {}).get("retries", 2))
    rows = []
    failures = []
    for path in files:
        key = os.path.abspath(path)
        prior = state["files"].get(key)
        try:
            size = _size_guard(path, config)
            digest = io.sha256_file(path, max_bytes=size)
        except (CliError, io.MediaError, OSError) as exc:
            failures.append({"path": path, "error": str(exc)})
            continue
        if args.resume and prior and prior.get("sha256") == digest \
                and prior.get("status") == "done":
            rows.append(prior)
            continue
        attempt = 0
        last_error = None
        while attempt <= retries:
            try:
                scan = io.safety_scan(
                    path, max_pixels=int(limits.get("max_pixels", 80_000_000)),
                    max_uncompressed_ratio=int(limits.get("max_uncompressed_ratio", 200)))
                probed = io.probe(path)
                row = {"path": path, "size_bytes": size, "sha256": digest,
                       "safety_verdict": scan["verdict"],
                       "safety_findings": scan["findings"],
                       "format": probed["detected"]["format"],
                       "kind": probed["detected"]["kind"],
                       "technical": probed.get("technical"),
                       "status": "done", "attempts": attempt + 1}
                rows.append(row)
                state["files"][key] = row
                break
            except (io.MediaError, OSError, TimeoutError) as exc:
                last_error = str(exc)
                attempt += 1
                time.sleep(float(config.get("runtime", {}).get("retry_backoff_s", 1.5))
                           * attempt)
        else:
            failures.append({"path": path, "error": last_error, "attempts": attempt})
            state["files"][key] = {"path": path, "sha256": digest, "status": "failed",
                                   "error": last_error}
    graph = io.asset_graph([r["path"] for r in rows if r.get("status") == "done"])
    manifest = {
        "manifest_version": "1.0.0",
        "generated_at": now_iso(),
        "root_files": len(files),
        "completed": len([r for r in rows if r.get("status") == "done"]),
        "failed": failures,
        "files": rows,
        "asset_graph": graph,
        "resumable": True,
        "checkpoint_file": ckpt_path,
    }
    write_output(manifest_path, manifest, True, config)
    state["updated_at"] = now_iso()
    with open(ckpt_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    warnings = ["resume checkpoint written to %s" % ckpt_path]
    blocked = [r["path"] for r in rows if r.get("safety_verdict") == "block"]
    if blocked:
        warnings.append("blocked files still listed in the manifest: %s" % ", ".join(blocked))
    human = "ingested %d/%d file(s); manifest %s" % (
        manifest["completed"], len(files), manifest_path)
    env = envelope("ingest", manifest, warnings=warnings, errors=failures)
    if failures:
        env["ok"] = False
    return env, human


def op_analyze_image(args, config):
    files = collect_inputs(args.paths, config)
    max_pixels = int(config.get("limits", {}).get("max_pixels", 80_000_000))
    max_side = int(config.get("runtime", {}).get("analysis_max_side_px", 640))
    timeout = float(config.get("runtime", {}).get("stage_timeout_s", 900))
    results = []
    errors = []
    for path in files:
        try:
            _size_guard(path, config)
            scan = io.safety_scan(path, max_pixels=max_pixels)
            if scan["verdict"] == "block":
                raise CliError("blocked by ingestion safety scan: %s" %
                               ", ".join(f["code"] for f in scan["findings"]
                                         if f["severity"] == "high"), EXIT_GATE)
            with time_limit(timeout, "analyze %s" % os.path.basename(path)):
                raster = io.decode_image(path, max_pixels=max_pixels)
                info = io.probe_image(path)
                working, factor = metrics.downsample(raster, max_side)
                report = metrics.image_quality_report(working, source=path)
                report["analysis_scale_factor"] = factor
                results.append({
                    "path": path,
                    "sha256": io.sha256_file(path),
                    "technical": info,
                    "quality": report,
                    "palette": metrics.dominant_colors(working, count=5),
                    "perceptual_hash": metrics.phash(working),
                    "decoder": raster.decoder,
                })
        except (CliError, io.MediaError, TimeoutError, OSError) as exc:
            entry = {"path": path, "error": str(exc)}
            if hasattr(exc, "hint") and exc.hint:
                entry["hint"] = exc.hint
            errors.append(entry)
    payload = {"file_count": len(files), "analysed": len(results),
               "results": results, "failures": errors}
    if args.out:
        write_output(args.out, payload, args.approve, config)
    human = "analysed %d/%d image(s)" % (len(results), len(files))
    for r in results:
        human += "\n  %s: score=%s issues=%s" % (
            os.path.basename(r["path"]), r["quality"]["quality_score"],
            ",".join(i["code"] for i in r["quality"]["issues"]) or "none")
    env = envelope("analyze-image", payload, errors=errors)
    return env, human


def op_analyze_audio(args, config):
    files = collect_inputs(args.paths, config)
    max_seconds = int(config.get("limits", {}).get("max_audio_seconds", 14400))
    timeout = float(config.get("runtime", {}).get("stage_timeout_s", 900))
    silence_db = float(config.get("analysis", {}).get("silence_threshold_dbfs", -45.0))
    results = []
    errors = []
    for path in files:
        try:
            _size_guard(path, config)
            fmt = io.detect_format(path)["format"]
            if fmt != "wav":
                raise CliError("%s audio needs a WAV (PCM) source for local "
                               "measurement; convert first" % fmt, EXIT_INTEGRATION,
                               hint="python3 tools-miw.py probe to confirm the "
                                    "format, then convert to WAV with your encoder")
            with time_limit(timeout, "analyze %s" % os.path.basename(path)):
                audio = io.decode_wav(path, max_seconds=max_seconds)
                chans = audio["samples"]
                mono = chans[0] if audio["channels"] == 1 else [
                    (a + b) / 2.0 for a, b in zip(chans[0], chans[1])]
                stats = metrics.audio_level_stats(mono, audio["sample_rate"])
                per_channel = [metrics.audio_level_stats(ch, audio["sample_rate"])
                               for ch in chans]
                results.append({
                    "path": path,
                    "sha256": io.sha256_file(path),
                    "technical": io.probe_audio(path),
                    "levels": stats,
                    "per_channel_levels": per_channel,
                    "silence": metrics.silence_map(mono, audio["sample_rate"],
                                                   threshold_dbfs=silence_db),
                    "hum": metrics.hum_detection(mono, audio["sample_rate"]),
                    "spectrum": metrics.spectral_profile(mono, audio["sample_rate"]),
                    "channel_balance": metrics.channel_balance(chans,
                                                               audio["sample_rate"]),
                    "loudness": metrics.loudness_estimate(mono, audio["sample_rate"]),
                    "tempo": _safe_tempo(mono, audio["sample_rate"]),
                })
        except (CliError, io.MediaError, TimeoutError, OSError) as exc:
            entry = {"path": path, "error": str(exc)}
            if getattr(exc, "hint", None):
                entry["hint"] = exc.hint
            errors.append(entry)
    payload = {"file_count": len(files), "analysed": len(results),
               "results": results, "failures": errors}
    if args.out:
        write_output(args.out, payload, args.approve, config)
    human = "analysed %d/%d audio file(s)" % (len(results), len(files))
    for r in results:
        human += "\n  %s: rms=%sdBFS peak=%sdBFS silence=%s hum=%s" % (
            os.path.basename(r["path"]), r["levels"]["rms_dbfs"],
            r["levels"]["peak_dbfs"], r["silence"]["silence_ratio"],
            r["hum"]["verdict"])
    env = envelope("analyze-audio", payload, errors=errors)
    return env, human


def _safe_tempo(samples, rate):
    try:
        env, env_rate = metrics.onset_envelope(samples, rate)
        return metrics.tempo_estimate(env, env_rate)
    except Exception as exc:  # defensive: tempo is optional enrichment
        return {"bpm": None, "confidence": 0.0, "note": "tempo unavailable: %s" % exc}


def _video_frames(args, config, tmpdir):
    """Return (frame_paths, fps, source_note). Frames dir or ffmpeg adapter."""
    if args.frames_dir:
        if not os.path.isdir(args.frames_dir):
            raise CliError("frames directory not found: %s" % args.frames_dir, EXIT_USAGE)
        frames = [os.path.join(args.frames_dir, n)
                  for n in sorted(os.listdir(args.frames_dir))
                  if os.path.splitext(n)[1].lower() in
                  (".png", ".jpg", ".jpeg", ".bmp", ".ppm", ".pgm", ".tif", ".tiff")]
        if not frames:
            raise CliError("no image frames in %s" % args.frames_dir, EXIT_USAGE)
        fps = float(args.fps or config.get("runtime", {}).get("sample_fps", 1.0))
        return frames, fps, "frames-directory"
    if not args.paths:
        raise CliError("analyze-video needs a video path or --frames-dir", EXIT_USAGE)
    ffmpeg = io.optional_dependencies()["ffmpeg"]
    if not ffmpeg:
        raise CliError(
            "ffmpeg is not available: this build probes containers but cannot "
            "extract video frames", EXIT_INTEGRATION,
            hint="install ffmpeg and re-run, or export frames with any tool and "
                 "pass --frames-dir (see knowledge-readme.md)")
    path = args.paths[0]
    fps = float(args.fps or config.get("runtime", {}).get("sample_fps", 1.0))
    max_frames = int(config.get("limits", {}).get("max_video_frames", 600))
    timeout = float(config.get("runtime", {}).get("stage_timeout_s", 900))
    outdir = os.path.join(tmpdir, "frames")
    os.makedirs(outdir, exist_ok=True)
    vf = "fps=%s,scale='min(640,iw)':-2" % fps
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", path, "-vf", vf, "-frames:v", str(max_frames),
           os.path.join(outdir, "frame-%05d.jpg")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          check=False)
    if proc.returncode != 0:
        raise CliError("ffmpeg frame extraction failed: %s"
                       % (proc.stderr.strip()[:400] or "unknown error"), EXIT_INTEGRATION)
    frames = [os.path.join(outdir, n) for n in sorted(os.listdir(outdir))]
    return frames, fps, "ffmpeg-adapter"


def op_analyze_video(args, config):
    max_pixels = int(config.get("limits", {}).get("max_pixels", 80_000_000))
    timeout = float(config.get("runtime", {}).get("stage_timeout_s", 900))
    max_frames = int(config.get("limits", {}).get("max_video_frames", 600))
    tmpdir = tempfile.mkdtemp(prefix="miw-frames-")
    warnings = []
    try:
        probe_info = None
        if args.paths:
            probe_info = io.probe(args.paths[0])
        frames, fps, source = _video_frames(args, config, tmpdir)
        if len(frames) > max_frames:
            warnings.append("frame list truncated from %d to %d"
                            % (len(frames), max_frames))
            frames = frames[:max_frames]
        luma = []
        rasters = []
        palettes = []
        hashes = []
        errors = []
        for path in frames:
            try:
                with time_limit(timeout, "frame %s" % os.path.basename(path)):
                    raster = io.decode_image(path, max_pixels=max_pixels)
                    working, _ = metrics.downsample(raster, 320)
                    rasters.append(working)
                    grid = metrics.luma_grid(working, step=2)
                    values = [v for row in grid for v in row]
                    luma.append(sum(values) / float(len(values)) if values else 0.0)
                    palettes.append(metrics.dominant_colors(working, count=3))
                    hashes.append(metrics.phash(working))
            except (io.MediaError, TimeoutError, OSError) as exc:
                errors.append({"frame": path, "error": str(exc)})
        pairs = list(zip(rasters, rasters[1:]))
        scores = metrics.scene_cut_scores(pairs)
        cuts = metrics.detect_scene_cuts(
            scores, threshold=float(config.get("analysis", {}).get(
                "scene_cut_threshold", 0.16)))
        flash = metrics.flash_risk(
            luma, fps=fps,
            relative_change=float(config.get("analysis", {}).get(
                "flash_relative_change", 0.2)))
        payload = {
            "source": args.paths[0] if args.paths else args.frames_dir,
            "frame_source": source,
            "frame_count": len(rasters),
            "sampled_fps": fps,
            "duration_s": round(len(rasters) / fps, 3) if fps else None,
            "container_probe": probe_info,
            "scene_boundaries": [
                {"timecode_s": round((c["pair_index"] + 1) / fps, 3),
                 "histogram_distance": c["histogram_distance"],
                 "mean_abs_diff": c["mean_abs_diff"]} for c in cuts[:500]],
            "scene_count": len(cuts) + 1,
            "flash_risk": flash,
            "motion": metrics.motion_energy(luma),
            "luma_series": [round(v, 2) for v in luma[:2000]],
            "frame_hashes": [h["dhash"] for h in hashes],
            "thumbnails_palette": palettes[:50],
            "failures": errors,
        }
        if len(cuts) > 500:
            warnings.append("scene boundary list truncated at 500 entries")
        if args.out:
            write_output(args.out, payload, args.approve, config)
        human = ("video: %d frames @ %.2ffps, %d scene(s), flash risk=%s, motion=%s"
                 % (len(rasters), fps, payload["scene_count"],
                    flash["risk"], payload["motion"]["verdict"]))
        env = envelope("analyze-video", payload, warnings=warnings, errors=errors)
        if source == "frames-directory":
            env["warnings"].append("frames supplied by directory: frame cadence is "
                                   "assumed from --fps, not read from the container")
        return env, human
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _parse_ts(value):
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        raise CliError("unparseable timestamp %r" % value, EXIT_USAGE)


def parse_subtitles(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    cue_re = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})")
    cues = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for block in blocks:
        m = cue_re.search(block)
        if not m:
            continue
        body_lines = [l for l in block.split("\n")
                      if l.strip() and not cue_re.search(l) and not l.strip().isdigit()
                      and not l.strip().upper().startswith("WEBVTT")]
        cues.append({"start_s": _parse_ts(m.group(1)), "end_s": _parse_ts(m.group(2)),
                     "text": " ".join(body_lines).strip(),
                     "raw_lines": body_lines})
    if not cues:
        raise CliError("no subtitle cues found in %s" % path, EXIT_USAGE,
                       hint="supported: SRT and WebVTT timestamp formats")
    return cues


def op_subtitle_audit(args, config):
    files = collect_inputs(args.paths, config)
    max_cps = float(args.max_cps)
    min_dur = float(args.min_duration)
    max_dur = float(args.max_duration)
    max_chars = int(args.max_line_chars)
    gap_tolerance = float(args.gap_tolerance)
    results = []
    errors = []
    for path in files:
        try:
            cues = parse_subtitles(path)
        except (CliError, OSError) as exc:
            errors.append({"path": path, "error": str(exc)})
            continue
        issues = []
        prev_end = None
        for i, cue in enumerate(cues, 1):
            dur = cue["end_s"] - cue["start_s"]
            chars = len(cue["text"])
            cps = chars / dur if dur > 0 else float("inf")
            longest = max((len(l) for l in cue["raw_lines"] or [""]), default=0)
            if dur <= 0:
                issues.append({"cue": i, "code": "non-positive-duration",
                               "detail": "end <= start"})
            elif dur < min_dur:
                issues.append({"cue": i, "code": "too-short", "detail": "%.2fs" % dur})
            elif dur > max_dur:
                issues.append({"cue": i, "code": "too-long", "detail": "%.2fs" % dur})
            if cps > max_cps:
                issues.append({"cue": i, "code": "reading-speed",
                               "detail": "%.1f chars/s over %.1f limit" % (cps, max_cps)})
            if longest > max_chars:
                issues.append({"cue": i, "code": "line-length",
                               "detail": "%d chars on one line" % longest})
            if len(cue["raw_lines"]) > args.max_lines:
                issues.append({"cue": i, "code": "too-many-lines",
                               "detail": "%d lines" % len(cue["raw_lines"])})
            if prev_end is not None and cue["start_s"] < prev_end - 0.001:
                issues.append({"cue": i, "code": "overlap",
                               "detail": "starts %.2fs before previous cue ends"
                                         % (prev_end - cue["start_s"])})
            prev_end = max(prev_end or 0.0, cue["end_s"])
        gaps = []
        for a, b in zip(cues, cues[1:]):
            gap = b["start_s"] - a["end_s"]
            if gap > gap_tolerance:
                gaps.append({"after_cue": cues.index(a) + 1, "gap_s": round(gap, 3)})
        total_duration = cues[-1]["end_s"] - cues[0]["start_s"]
        results.append({
            "path": path,
            "cue_count": len(cues),
            "span_s": round(total_duration, 3),
            "mean_cps": round(sum(len(c["text"]) for c in cues) /
                              max(1e-9, sum(c["end_s"] - c["start_s"] for c in cues)), 2),
            "issues": issues,
            "large_gaps": gaps,
            "verdict": "fail" if any(i["code"] in ("reading-speed", "overlap",
                                                   "non-positive-duration")
                                     for i in issues) else (
                "review" if issues else "pass"),
        })
    payload = {"file_count": len(files), "audited": len(results),
               "thresholds": {"max_cps": max_cps, "min_duration_s": min_dur,
                              "max_duration_s": max_dur, "max_line_chars": max_chars,
                              "max_lines": args.max_lines},
               "results": results, "failures": errors}
    if args.out:
        write_output(args.out, payload, args.approve, config)
    human = "audited %d/%d subtitle file(s)" % (len(results), len(files))
    for r in results:
        human += "\n  %s: cues=%d verdict=%s issues=%d" % (
            os.path.basename(r["path"]), r["cue_count"], r["verdict"], len(r["issues"]))
    return envelope("subtitle-audit", payload, errors=errors), human


def relative_luminance(rgb):
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(rgb_a, rgb_b):
    la, lb = relative_luminance(rgb_a), relative_luminance(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hex_rgb(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise CliError("colour must be hex like #ffffff, got %r" % value, EXIT_USAGE)
    try:
        return [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        raise CliError("colour must be hex like #ffffff, got %r" % value, EXIT_USAGE)


def op_accessibility(args, config):
    data = {"checks": []}
    warnings = []
    if args.frames_dir or args.paths:
        video_args = argparse.Namespace(**vars(args))
        video_env, _ = op_analyze_video(video_args, config)
        vdata = video_env["data"]
        data["flash"] = vdata["flash_risk"]
        data["motion"] = vdata["motion"]
        data["sampled_fps"] = vdata["sampled_fps"]
        warnings.extend(video_env.get("warnings", []))
        data["checks"].append({
            "check": "flash-and-pattern-risk",
            "criterion": "no more than three general luminance flashes in any 1s window",
            "measured": vdata["flash_risk"],
            "verdict": "review" if vdata["flash_risk"]["risk"] != "low" else "pass",
            "note": "luminance-transition aid only; human verification required "
                    "for accessibility sign-off",
        })
    for pair in args.contrast or []:
        if "," not in pair:
            raise CliError("--contrast expects 'FG,BG' hex pair, got %r" % pair, EXIT_USAGE)
        fg, bg = pair.split(",", 1)
        ratio = contrast_ratio(_hex_rgb(fg), _hex_rgb(bg))
        levels = {"AA_normal": ratio >= 4.5, "AA_large": ratio >= 3.0,
                  "AAA_normal": ratio >= 7.0, "AAA_large": ratio >= 4.5}
        data["checks"].append({"check": "contrast", "foreground": fg,
                               "background": bg, "ratio": round(ratio, 2),
                               "levels": levels,
                               "verdict": "pass" if levels["AA_normal"] else "fail"})
    if not data["checks"]:
        raise CliError("accessibility needs --frames-dir/VIDEO for flash risk or "
                       "--contrast FG,BG for contrast", EXIT_USAGE)
    if args.out:
        write_output(args.out, data, args.approve, config)
    human = "; ".join("%s=%s" % (c["check"], c["verdict"]) for c in data["checks"])
    return envelope("accessibility", data, warnings=warnings), human


def op_conformance(args, config):
    if not args.spec or not os.path.isfile(args.spec):
        raise CliError("--spec must point to a JSON delivery specification", EXIT_USAGE,
                       hint="see asset-conformance-spec-example.json")
    spec = planner.load_json(args.spec, "spec")
    files = collect_inputs(args.paths, config)
    results = []
    for path in files:
        probed = io.probe(path)
        tech = probed.get("technical") or {}
        violations = []
        meta = probed["detected"]
        if "format" in spec and meta["format"] not in spec["format"]:
            violations.append({"field": "format", "expected": spec["format"],
                               "actual": meta["format"]})
        if "kind" in spec and meta["kind"] not in spec["kind"]:
            violations.append({"field": "kind", "expected": spec["kind"],
                               "actual": meta["kind"]})
        dur = tech.get("duration_s")
        if "duration_s" in spec:
            lo = spec["duration_s"].get("min", 0)
            hi = spec["duration_s"].get("max", 10 ** 9)
            if dur is None:
                violations.append({"field": "duration_s", "expected": [lo, hi],
                                   "actual": None,
                                   "note": "duration unavailable from the probe"})
            elif not lo <= dur <= hi:
                violations.append({"field": "duration_s", "expected": [lo, hi],
                                   "actual": dur})
        for key in ("width", "height", "channels", "sample_rate", "bit_depth"):
            if key in spec and tech.get(key) is not None:
                allowed = spec[key] if isinstance(spec[key], list) else [spec[key]]
                if tech[key] not in allowed:
                    violations.append({"field": key, "expected": allowed,
                                       "actual": tech[key]})
        if "max_size_bytes" in spec and probed["size_bytes"] > spec["max_size_bytes"]:
            violations.append({"field": "size_bytes",
                               "expected": "<= %d" % spec["max_size_bytes"],
                               "actual": probed["size_bytes"]})
        if "loudness_lufs" in spec and args.audio_analysis:
            audio_env, _ = op_analyze_audio(
                argparse.Namespace(paths=[path], out=None, approve=False), config)
            for row in audio_env["data"]["results"]:
                lufs = row["loudness"]["lufs_estimate"]
                lo = spec["loudness_lufs"].get("min", -70)
                hi = spec["loudness_lufs"].get("max", 0)
                if lufs is None or not lo <= lufs <= hi:
                    violations.append({"field": "loudness_lufs_estimate",
                                       "expected": [lo, hi], "actual": lufs})
        else:
            if "loudness_lufs" in spec:
                violations.append({"field": "loudness_lufs",
                                   "expected": spec["loudness_lufs"], "actual": None,
                                   "note": "pass --audio-analysis with a WAV source "
                                           "to evaluate loudness"})
        results.append({"path": path, "compliant": not violations,
                        "violations": violations, "technical": tech,
                        "spec_name": spec.get("name", os.path.basename(args.spec))})
    payload = {"spec": os.path.abspath(args.spec), "file_count": len(files),
               "compliant": all(r["compliant"] for r in results), "results": results}
    if args.out:
        write_output(args.out, payload, args.approve, config)
    human = "%d/%d file(s) conform to %s" % (
        sum(1 for r in results if r["compliant"]), len(results),
        spec.get("name", os.path.basename(args.spec)))
    env = envelope("conformance", payload)
    if not payload["compliant"]:
        env["ok"] = False
        env["errors"].append({"kind": "conformance", "code": EXIT_FAILURES,
                              "message": "one or more files violate the delivery "
                                         "specification"})
    return env, human


def op_index(args, config):
    files = collect_inputs(args.paths, config)
    entries = []
    errors = []
    max_pixels = int(config.get("limits", {}).get("max_pixels", 80_000_000))
    for path in files:
        try:
            fmt = io.detect_format(path)
            entry = {"path": path, "format": fmt["format"], "kind": fmt["kind"],
                     "size_bytes": os.path.getsize(path),
                     "sha256": io.sha256_file(path)}
            if fmt["kind"] == "image":
                raster = io.decode_image(path, max_pixels=max_pixels)
                working, _ = metrics.downsample(raster, 320)
                entry["perceptual_hash"] = metrics.phash(working)["dhash"]
                entry["palette"] = metrics.dominant_colors(working, count=3)["palette"]
                entry["dimensions"] = [raster.width, raster.height]
            elif fmt["format"] == "wav":
                audio = io.decode_wav(path, max_seconds=int(
                    config.get("limits", {}).get("max_audio_seconds", 14400)))
                entry["duration_s"] = audio["duration_s"]
                entry["technical"] = io.probe_audio(path)
            else:
                probed = io.probe(path)
                entry["technical"] = probed.get("technical")
                if entry["technical"]:
                    entry["duration_s"] = entry["technical"].get("duration_s")
            entries.append(entry)
        except (io.MediaError, TimeoutError, OSError) as exc:
            errors.append({"path": path, "error": str(exc)})

    index = {"index_version": "1.0.0", "built_at": now_iso(),
             "root": args.paths, "entry_count": len(entries), "entries": entries,
             "failures": errors}
    results = []
    if args.query:
        q = args.query.lower()
        for e in entries:
            haystack = " ".join(str(x) for x in (e["path"], e["format"], e["kind"])).lower()
            score = sum(2.0 for token in q.split() if token in haystack)
            results.append({"path": e["path"], "score": score})
        results = [r for r in sorted(results, key=lambda r: -r["score"]) if r["score"] > 0]
        index["query"] = {"text": args.query, "matches": results,
                          "note": "metadata/text index only; semantic embeddings "
                                  "require an external provider (see providers in "
                                  "the config)"}
    if args.similar_to:
        base = None
        for e in entries:
            if os.path.abspath(e["path"]) == os.path.abspath(args.similar_to):
                base = e
        if base is None or "perceptual_hash" not in base:
            raise CliError("--similar-to must be an indexed image path", EXIT_USAGE)
        sims = []
        for e in entries:
            if "perceptual_hash" not in e:
                continue
            dist = metrics.hamming(base["perceptual_hash"], e["perceptual_hash"])
            sims.append({"path": e["path"], "hamming_distance": dist,
                         "similarity": round(1 - dist / 64.0, 3)})
        sims.sort(key=lambda r: r["hamming_distance"])
        index["similarity"] = {"reference": base["path"], "threshold": args.threshold,
                               "matches": [s for s in sims
                                           if s["similarity"] >= args.threshold]}
    if args.out:
        write_output(args.out, index, args.approve, config)
    human = "indexed %d/%d file(s)" % (len(entries), len(files))
    if args.query:
        human += "; %d text match(es)" % len(index["query"]["matches"])
    if args.similar_to:
        human += "; %d similar image(s)" % len(index["similarity"]["matches"])
    return envelope("index", index, errors=errors), human


def _load_measurements(paths):
    rows = []
    for p in paths:
        doc = planner.load_json(p, "measurement")
        rows.append({"file": p, "document": doc})
    return rows


def op_report(args, config):
    plan = planner.load_json(args.plan, "plan")
    catalog, policies = context(config)
    errors = planner.validate_plan(plan, catalog, policies)
    if errors:
        raise CliError("plan is invalid: %s" % "; ".join(errors), EXIT_USAGE)
    measurements = _load_measurements(args.measurements or [])
    evidence = []
    observations = []
    for m in measurements:
        doc = m["document"]
        payload = doc.get("data", doc)
        if isinstance(payload, dict) and payload.get("flash_risk") \
                and payload.get("scene_count") is not None:
            src = payload.get("source") or m["file"]
            evidence.append({
                "claim": "%d scene(s) detected across %d sampled frame(s); flash "
                         "risk %s; motion %s"
                         % (payload["scene_count"], payload["frame_count"],
                            payload["flash_risk"]["risk"],
                            payload["motion"]["verdict"]),
                "source": src, "kind": "observation",
                "confidence": 0.75,
                "timecode": "00:00:00.000",
                "start_s": 0.0,
                "notes": "frame metric: sampled at %s fps from %s"
                         % (payload.get("sampled_fps"), payload.get("frame_source")),
            })
            observations.append({
                "unit_guess": "video-temporal",
                "summary": "%d scene boundary(ies), flash risk %s"
                           % (len(payload.get("scene_boundaries", [])),
                              payload["flash_risk"]["risk"]),
            })
        for res in (payload.get("results") or []):
            path = res.get("path")
            if res.get("quality"):
                q = res["quality"]
                evidence.append({
                    "claim": "image quality score %s for %s"
                             % (q["quality_score"], os.path.basename(path)),
                    "source": path, "kind": "observation",
                    "confidence": round(min(1.0, 0.5 + q["quality_score"] / 200.0), 2),
                    "region": None, "timecode": None,
                    "offset": res.get("sha256", "")[:16] or None,
                    "notes": "metrics: sharpness=%s noise=%s exposure=%s"
                             % (q["sharpness"]["verdict"], q["noise"]["verdict"],
                                q["exposure"]["verdict"]),
                })
                observations.append({
                    "unit_guess": "image-quality",
                    "summary": "%s: %d issue(s) detected"
                               % (os.path.basename(path), len(q["issues"])),
                    "issues": q["issues"],
                })
            if res.get("levels"):
                lv = res["levels"]
                evidence.append({
                    "claim": "%s audio RMS %s dBFS, peak %s dBFS, %d clipped samples"
                             % (os.path.basename(path), lv["rms_dbfs"],
                                lv["peak_dbfs"], lv["clipped_samples"]),
                    "source": path, "kind": "observation",
                    "confidence": 0.9, "timecode": "00:00:00.000",
                    "notes": "measured on the decoded PCM stream",
                })
                observations.append({
                    "unit_guess": "audio-levels",
                    "summary": "%s: %s, silence ratio %s"
                               % (os.path.basename(path), lv["verdict"],
                                  res.get("silence", {}).get("silence_ratio")),
                })
            if res.get("flash_risk"):
                fr = res["flash_risk"]
                evidence.append({
                    "claim": "flash risk %s (%d luminance transitions)"
                             % (fr["risk"], fr["transitions"]),
                    "source": path, "kind": "observation",
                    "confidence": 0.6 if fr["risk"] != "low" else 0.8,
                    "timecode": "00:00:00.000",
                    "notes": "conservative luminance-transition metric",
                })
            if res.get("cue_count") is not None:
                evidence.append({
                    "claim": "%s subtitle audit verdict %s with %d issue(s)"
                             % (os.path.basename(path), res["verdict"],
                                len(res["issues"])),
                    "source": path, "kind": "observation",
                    "confidence": 0.85, "timecode": "00:00:00.000",
                })
    for row in (args.extra_evidence or []):
        doc = planner.load_json(row, "evidence")
        evidence.extend(doc.get("evidence_table", doc if isinstance(doc, list) else []))
    report = {
        "report_version": "1.0.0",
        "title": args.title,
        "generated_at": now_iso(),
        "plan_ref": os.path.abspath(args.plan),
        "umbrella": plan.get("umbrella"),
        "policy_preset": plan.get("policy_preset"),
        "scope": args.scope or ("Coverage of %d analysis unit(s): %s" % (
            len(plan["units"]), ", ".join(u["skill_name"] for u in plan["units"]))),
        "sources": sorted({e["source"] for e in evidence if e.get("source")}),
        "observations": observations,
        "interpretation": args.interpretation or [],
        "evidence_table": evidence,
        "limits": [
            "Local measurement only: no model, network or paid service was used.",
            "Absent provenance metadata is reported as unavailable, not as evidence "
            "of manipulation.",
            "Findings are observations unless listed under interpretation with a "
            "stated basis.",
        ] + list(args.limit or []),
        "next_steps": list(args.next_step or [
            "Route gated units to a human reviewer with the evidence table attached.",
            "Confirm delivery-spec conformance before publishing derived media.",
        ]),
        "gates": plan.get("gates", []),
    }
    validation = planner.validate_report(report)
    if validation:
        raise CliError("assembled report failed validation: %s" % "; ".join(validation),
                       EXIT_USAGE)
    if args.out:
        write_output(args.out, report, args.approve, config)
    if args.md:
        write_output(args.md, render_report_markdown(report), args.approve, config)
    human = "report with %d evidence row(s), %d observation group(s)" % (
        len(evidence), len(observations))
    return envelope("report", report), human


def render_report_markdown(report):
    lines = ["# %s" % report["title"], "",
             "*Generated %s - policy preset `%s`*" % (report["generated_at"],
                                                      report.get("policy_preset")),
             "", "## Scope", "", report["scope"], "",
             "## Sources", ""]
    for s in report["sources"] or ["(none recorded)"]:
        lines.append("- `%s`" % s)
    lines += ["", "## Observations", ""]
    for o in report["observations"]:
        lines.append("- %s" % o.get("summary", ""))
        for issue in o.get("issues", []):
            lines.append("  - %s (%s): %s" % (issue["code"], issue["severity"],
                                              issue["detail"]))
    lines += ["", "## Interpretation", ""]
    if report["interpretation"]:
        for i in report["interpretation"]:
            lines.append("- %s" % i)
    else:
        lines.append("_No interpretations recorded._")
    lines += ["", "## Evidence table", "",
              "| Claim | Source | Anchor | Kind | Confidence |",
              "| --- | --- | --- | --- | --- |"]
    for row in report["evidence_table"]:
        anchor = row.get("timecode") or row.get("region") or row.get("offset") or "-"
        lines.append("| %s | `%s` | %s | %s | %s |" % (
            row["claim"], os.path.basename(row.get("source", "")), anchor,
            row["kind"], row["confidence"]))
    lines += ["", "## Limits", ""]
    for limit in report["limits"]:
        lines.append("- %s" % limit)
    lines += ["", "## Next steps", ""]
    for step in report["next_steps"]:
        lines.append("- %s" % step)
    if report.get("gates"):
        lines += ["", "## Gates", ""]
        for g in report["gates"]:
            lines.append("- **%s** - %s" % (g["id"], g["title"]))
    return "\n".join(lines) + "\n"


def op_evaluate(args, config):
    report = planner.load_json(args.report, "report")
    errors = planner.validate_report(report)
    rows = report.get("evidence_table", []) or []
    obs = [r for r in rows if r.get("kind") == "observation"]
    interp = [r for r in rows if r.get("kind") == "interpretation"]
    anchored = [r for r in obs if any(r.get(k) for k in
                                     ("timecode", "start_s", "region", "offset"))]
    low_conf = [r for r in rows if float(r.get("confidence", 0)) <
                float(config.get("analysis", {}).get("confidence_review_threshold", 0.7))]
    missing_source = [r for r in rows if not r.get("source")]
    coverage = len(anchored) / len(obs) if obs else 1.0
    grounding = 1.0 - (len(missing_source) / len(rows) if rows else 0.0)
    metrics_out = {
        "evidence_rows": len(rows),
        "observations": len(obs),
        "interpretations": len(interp),
        "anchored_observations": len(anchored),
        "evidence_coverage": round(coverage, 3),
        "grounding_score": round(grounding, 3),
        "review_required": len(low_conf),
        "review_rows": [r.get("claim") for r in low_conf][:25],
        "validation_errors": errors,
        "verdict": ("fail" if errors or coverage < 0.9 or grounding < 1.0
                    else ("review" if low_conf else "pass")),
        "scoring_note": "coverage = anchored observations / observations; grounding = "
                        "rows with a source / all rows. Both are explicit and "
                        "reproducible; neither is a truth claim.",
    }
    if args.plan:
        plan = planner.load_json(args.plan, "plan")
        required = {u["unit_id"] for u in plan.get("units", [])}
        metrics_out["plan_units"] = len(required)
        metrics_out["gates_in_plan"] = [g["id"] for g in plan.get("gates", [])]
    if args.out:
        write_output(args.out, metrics_out, args.approve, config)
    human = ("evaluate: verdict=%s coverage=%.2f grounding=%.2f review=%d"
             % (metrics_out["verdict"], coverage, grounding, len(low_conf)))
    env = envelope("evaluate", metrics_out)
    if metrics_out["verdict"] == "fail":
        env["ok"] = False
        env["errors"].append({"kind": "evaluation", "code": EXIT_FAILURES,
                              "message": "report failed validation or grounding checks",
                              "hint": "add timecode/region anchors and source paths "
                                      "to every observation"})
    return env, human


def op_validate(args, config):
    catalog, policies = context(config)
    data = {"validated": []}
    errors = []
    if args.config_file:
        cfg = load_config(args.config_file)
        preset = cfg.get("policy", {}).get("preset", "standard")
        if preset not in policies["presets"]:
            errors.append("config policy preset %r is not defined" % preset)
        for key in ("paths", "limits", "runtime"):
            if not isinstance(cfg.get(key), dict):
                errors.append("config section %r must be an object" % key)
        data["validated"].append({"file": args.config_file, "kind": "config",
                                  "ok": not errors})
    if args.plan:
        plan = planner.load_json(args.plan, "plan")
        errs = planner.validate_plan(plan, catalog, policies)
        errors.extend(errs)
        data["validated"].append({"file": args.plan, "kind": "plan",
                                  "ok": not errs, "errors": errs})
    if args.report:
        report = planner.load_json(args.report, "report")
        errs = planner.validate_report(report)
        errors.extend(errs)
        data["validated"].append({"file": args.report, "kind": "report",
                                  "ok": not errs, "errors": errs})
    if not data["validated"]:
        raise CliError("validate needs --config, --plan or --report", EXIT_USAGE)
    data["error_count"] = len(errors)
    env = envelope("validate", data)
    if errors:
        env["ok"] = False
        env["errors"].append({"kind": "validation", "code": EXIT_FAILURES,
                              "message": "; ".join(errors)})
    return env, "validated %d document(s): %d error(s)" % (
        len(data["validated"]), len(errors))


def op_capabilities(args, config):
    catalog, policies = context(config)
    data = {
        "tool": TOOL_NAME,
        "version": VERSION,
        "entry_point": "tools-miw.py",
        "operations": [{"name": n, "description": d} for n, d in OPERATIONS],
        "exit_codes": {"0": "success", "1": "completed with failures",
                       "2": "usage or input validation error",
                       "3": "blocked by a policy gate",
                       "4": "required optional integration unavailable"},
        "catalog": {
            "source": catalog.get("source"),
            "category_count": catalog["category_count"],
            "skill_count": catalog["skill_count"],
            "categories": [{"id": c["id"], "name": c["name"],
                            "skills": len(c["skills"]),
                            "policy_class": c.get("policy_class")}
                           for c in catalog["categories"]],
        },
        "policy_presets": sorted(policies["presets"].keys()),
        "gates": sorted(policies["gates"].keys()),
        "optional_dependencies": io.optional_dependencies(),
        "documented_limitations": [
            "No model, network call or paid service is used; semantic labels, "
            "verbatim transcripts and translations are planned but not produced.",
            "Video frame extraction needs ffmpeg (or a pre-extracted frames "
            "directory); container probing always works without it.",
            "Local audio measurement accepts WAV/PCM; compressed audio must be "
            "converted first.",
            "PNG, BMP, PNM and WAV decode natively; JPEG/GIF/TIFF/WEBP need Pillow.",
        ],
    }
    if args.categories:
        data = {"categories": data["catalog"]["categories"],
                "skill_count": catalog["skill_count"]}
    return envelope("capabilities", data), "%d operations, %d catalog skills" % (
        len(OPERATIONS), catalog["skill_count"])


def op_selftest(args, config):
    sys.path.insert(0, HERE)
    tmp = tempfile.mkdtemp(prefix="miw-selftest-")
    checks = []

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        buf = __import__("io").StringIO()
        with contextlib.redirect_stdout(buf):
            rc = metrics.selftest()
        record("metric-engine-selftest", rc == 0,
               "%d synthetic signal checks" % len(buf.getvalue().splitlines()))
        # build a PNG fixture natively (no Pillow needed)
        png = os.path.join(tmp, "checker.png")
        _write_test_png(png, 96, 96)
        info = io.probe_image(png)
        record("png-probe", info["width"] == 96 and info["height"] == 96, str(info))
        raster = io.decode_image(png)
        record("png-decode", raster.width == 96 and raster.pixels == 9216)
        scan = io.safety_scan(png)
        record("safety-scan-pass", scan["verdict"] == "pass", str(scan["findings"]))
        wav = os.path.join(tmp, "tone.wav")
        _write_test_wav(wav, 1.0, 440.0)
        audio = io.decode_wav(wav)
        record("wav-decode", audio["channels"] == 1 and 0.9 < audio["duration_s"] < 1.1)
        state = {"paths": [png], "out": None, "approve": True, "deep": True}
        env, _ = op_inspect(argparse.Namespace(**state), load_config(None))
        record("inspect-operation", env["ok"] and env["data"]["file_count"] == 1)
        env, _ = op_analyze_image(argparse.Namespace(paths=[png], out=None,
                                                     approve=True), load_config(None))
        record("analyze-image-operation",
               env["ok"] and env["data"]["results"][0]["quality"]["quality_score"] >= 0)
        env, _ = op_analyze_audio(argparse.Namespace(paths=[wav], out=None,
                                                     approve=True), load_config(None))
        record("analyze-audio-operation",
               env["ok"] and env["data"]["results"][0]["levels"]["frames"] > 0)
        srt = os.path.join(tmp, "bad.srt")
        with open(srt, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:01,000 --> 00:00:01,200\nToo fast cue with far too "
                     "many characters to read in 200 milliseconds flat.\n\n"
                     "2\n00:00:01,100 --> 00:00:03,000\nOverlapping cue.\n")
        env, _ = op_subtitle_audit(argparse.Namespace(
            paths=[srt], out=None, approve=True, max_cps=17.0, min_duration=1.0,
            max_duration=7.0, max_line_chars=42, max_lines=2,
            gap_tolerance=2.0), load_config(None))
        codes = {i["code"] for i in env["data"]["results"][0]["issues"]}
        record("subtitle-audit", {"reading-speed", "overlap"} <= codes, str(sorted(codes)))
        env, _ = op_accessibility(argparse.Namespace(
            paths=None, frames_dir=None, contrast=["#000000,#ffffff"],
            out=None, approve=True, fps=None), load_config(None))
        record("contrast-check", env["data"]["checks"][0]["ratio"] == 21.0,
               str(env["data"]["checks"][0]["ratio"]))
        cat = load_config(None)
        env, _ = op_plan(argparse.Namespace(infile=None, text=None, category="9",
                                            query=None, preset="standard", out=None,
                                            approve=False, format="json",
                                            max_units=10), cat)
        record("plan-operation", len(env["data"]["units"]) == 10)
        record("plan-validation", planner.validate_plan(
            env["data"], *context(cat)) == [])
        env, _ = op_parse(argparse.Namespace(
            infile=None, out=None, approve=False,
            text="1. Skill Name: Alpha\nDescription: measure alpha\n"), cat)
        record("parse-operation", env["data"]["skill_count"] == 1)
        # recovery path: a corrupt file must fail cleanly, not crash
        bad = os.path.join(tmp, "broken.png")
        with open(bad, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        env, _ = op_analyze_image(argparse.Namespace(paths=[bad], out=None,
                                                     approve=True), load_config(None))
        record("corrupt-input-recovery", not env["ok"]
               and env["data"]["failures"][0]["error"])
        # gate: writing without approval must be refused in non-interactive mode
        refused = False
        try:
            require_approval("selftest write", False, {"policy": {"approval_mode": "prompt"}})
        except CliError as exc:
            refused = exc.code == EXIT_GATE
        record("approval-gate-refusal", refused)
        # missing input must fail through the documented exit path
        invalid = False
        try:
            op_subtitle_audit(argparse.Namespace(
                paths=[os.path.join(tmp, "does-not-exist.srt")], out=None,
                approve=False, max_cps=17.0, min_duration=1.0, max_duration=7.0,
                max_line_chars=42, max_lines=2, gap_tolerance=2.0), load_config(None))
        except CliError as exc:
            invalid = exc.code == EXIT_USAGE
        record("missing-input-rejected", invalid)
        # wrong content type must be reported per-file, not crash the run
        wrong_env, _ = op_subtitle_audit(argparse.Namespace(
            paths=[png], out=None, approve=False, max_cps=17.0, min_duration=1.0,
            max_duration=7.0, max_line_chars=42, max_lines=2,
            gap_tolerance=2.0), load_config(None))
        record("wrong-content-type-reported",
               (not wrong_env["ok"]) and wrong_env["data"]["failures"], "")
        # report + evaluate round trip
        mpath = os.path.join(tmp, "img-analysis.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump({"data": {"results": [
                {"path": png, "sha256": io.sha256_file(png),
                 "quality": metrics.image_quality_report(io.decode_image(png), png)}]}},
                fh)
        plan_path = os.path.join(tmp, "plan.json")
        penv, _ = op_plan(argparse.Namespace(infile=None, text=None, category="9",
                                             query=None, preset="standard",
                                             out=plan_path, approve=True,
                                             format="json", max_units=10), load_config(None))
        rep_path = os.path.join(tmp, "report.json")
        env, _ = op_report(argparse.Namespace(
            plan=plan_path, measurements=[mpath], out=rep_path, md=None, approve=True,
            title="Selftest report", scope=None, interpretation=None,
            extra_evidence=None, limit=None, next_step=None), load_config(None))
        record("report-operation", env["ok"] and os.path.isfile(rep_path))
        env, _ = op_evaluate(argparse.Namespace(report=rep_path, plan=plan_path,
                                                out=None, approve=False),
                             load_config(None))
        record("evaluate-operation", env["data"]["evidence_coverage"] == 1.0,
               str(env["data"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failures = [c for c in checks if not c["ok"]]
    data = {"checks": checks, "passed": len(checks) - len(failures),
            "failed": len(failures)}
    env = envelope("self-test", data)
    if failures:
        env["ok"] = False
        env["errors"].append({"kind": "self-test", "code": EXIT_FAILURES,
                              "message": "%d check(s) failed" % len(failures)})
    return env, "self-test: %d/%d checks passed" % (data["passed"], len(checks))


def _write_test_png(path, width, height):
    """Write a checkerboard PNG using only the standard library."""
    import struct as _struct
    import zlib as _zlib

    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            v = 255 if ((x // 8) + (y // 8)) % 2 == 0 else 20
            row.extend((v, v, v))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, body):
        return (_struct.pack(">I", len(body)) + tag + body +
                _struct.pack(">I", _zlib.crc32(tag + body) & 0xFFFFFFFF))

    ihdr = _struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", _zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)


def _write_test_wav(path, seconds, freq, rate=8000):
    import struct as _struct
    import wave as _wave

    frames = int(seconds * rate)
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        data = b"".join(_struct.pack("<h", int(0.4 * 32767 *
                                               math.sin(2 * math.pi * freq * i / rate)))
                        for i in range(frames))
        wf.writeframes(data)


def op_help(args, config):
    return envelope("help", {"usage": usage_text(), "operations":
                             [{"name": n, "description": d} for n, d in OPERATIONS]}), ""


def usage_text():
    return (
        "Media Intelligence Workbench %s\n\n"
        "USAGE (from the future skill root):\n"
        "  python3 media-intelligence-workbench/tools-miw.py <operation> [options]\n\n"
        "OPERATIONS:\n%s\n"
        "COMMON OPTIONS:\n"
        "  --config PATH    config JSON (default asset-default-config.json)\n"
        "  --out PATH       write the JSON result to PATH\n"
        "  --approve        approve file-writing actions in unattended runs\n"
        "  --quiet          suppress the human summary on stderr\n\n"
        "EXAMPLES:\n"
        "  python3 media-intelligence-workbench/tools-miw.py capabilities\n"
        "  python3 media-intelligence-workbench/tools-miw.py plan --text "
        "\"caption readability audit\" --out media-plans/captions.json\n"
        "  python3 media-intelligence-workbench/tools-miw.py analyze-image "
        "media-input/photo.png\n"
        "  python3 media-intelligence-workbench/tools-miw.py analyze-audio "
        "media-input/take.wav --out media-reports/take.json --approve\n"
        % (VERSION, "\n".join("  %-16s %s" % (n, d) for n, d in OPERATIONS))
    )


# --------------------------------------------------------------------------
# argument parsing / dispatch
# --------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="tools-miw.py", add_help=True,
        description="Media Intelligence Workbench - measure, validate and govern "
                    "media analysis work.",
        epilog="Run `python3 tools-miw.py capabilities` for the operation list.")
    p.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    # top-level copies so options may precede the operation; subparser copies use
    # SUPPRESS so they never clobber a value given before the operation name
    p.add_argument("--config", default=None, help="config JSON path")
    p.add_argument("--approve", action="store_true",
                   help="approve file-writing actions in unattended runs")
    p.add_argument("--quiet", action="store_true")
    sub = p.add_subparsers(dest="operation")

    def common(sp, paths=False):
        sp.add_argument("--config", default=argparse.SUPPRESS, help="config JSON path")
        sp.add_argument("--out", help="write JSON result to this path")
        sp.add_argument("--approve", action="store_true", default=argparse.SUPPRESS,
                        help="approve file-writing actions in unattended runs")
        sp.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
        if paths:
            sp.add_argument("paths", nargs="+", help="file or folder paths")

    common(sub.add_parser("help", help="show usage"))
    cap = sub.add_parser("capabilities", help="list operations and catalog summary")
    common(cap)
    cap.add_argument("--categories", action="store_true",
                     help="list catalog categories only")

    par = sub.add_parser("parse", help="parse a capability request")
    common(par)
    par.add_argument("--in", dest="infile")
    par.add_argument("--text")

    plan = sub.add_parser("plan", help="build a unified work plan")
    common(plan)
    plan.add_argument("--in", dest="infile")
    plan.add_argument("--text")
    plan.add_argument("--category")
    plan.add_argument("--query")
    plan.add_argument("--preset", help="policy preset (draft/standard/conservative)")
    plan.add_argument("--max-units", dest="max_units", type=int, default=25)
    plan.add_argument("--format", choices=["json", "md"], default="json")

    probe = sub.add_parser("probe", help="technical probe")
    common(probe, paths=True)

    insp = sub.add_parser("inspect", help="safety scan + hash + probe")
    common(insp, paths=True)
    insp.add_argument("--deep", action="store_true", help="also run the technical probe")

    ing = sub.add_parser("ingest", help="batch ingest with checkpoints")
    common(ing, paths=True)
    ing.add_argument("--out-dir", dest="out_dir")
    ing.add_argument("--no-resume", dest="resume", action="store_false", default=True)

    ai = sub.add_parser("analyze-image", help="image quality analysis")
    common(ai, paths=True)

    aa = sub.add_parser("analyze-audio", help="audio analysis")
    common(aa, paths=True)

    av = sub.add_parser("analyze-video", help="video probe + frame analysis")
    common(av)
    av.add_argument("paths", nargs="*")
    av.add_argument("--frames-dir", dest="frames_dir")
    av.add_argument("--fps", type=float)

    sub_aud = sub.add_parser("subtitle-audit", help="caption/subtitle audit")
    common(sub_aud, paths=True)
    sub_aud.add_argument("--max-cps", dest="max_cps", type=float, default=17.0)
    sub_aud.add_argument("--min-duration", dest="min_duration", type=float, default=1.0)
    sub_aud.add_argument("--max-duration", dest="max_duration", type=float, default=7.0)
    sub_aud.add_argument("--max-line-chars", dest="max_line_chars", type=int, default=42)
    sub_aud.add_argument("--max-lines", dest="max_lines", type=int, default=2)
    sub_aud.add_argument("--gap-tolerance", dest="gap_tolerance", type=float, default=2.0)

    acc = sub.add_parser("accessibility", help="flash risk and contrast checks")
    common(acc)
    acc.add_argument("paths", nargs="*")
    acc.add_argument("--frames-dir", dest="frames_dir")
    acc.add_argument("--fps", type=float)
    acc.add_argument("--contrast", action="append",
                     help='hex pair "FG,BG" (repeatable)')

    conf = sub.add_parser("conformance", help="delivery-spec conformance")
    common(conf, paths=True)
    conf.add_argument("--spec", required=True)
    conf.add_argument("--audio-analysis", dest="audio_analysis", action="store_true")

    idx = sub.add_parser("index", help="build/query a media index")
    common(idx, paths=True)
    idx.add_argument("--query")
    idx.add_argument("--similar-to", dest="similar_to")
    idx.add_argument("--threshold", type=float, default=0.85)

    rep = sub.add_parser("report", help="assemble an evidence-first report")
    common(rep)
    rep.add_argument("--plan", required=True)
    rep.add_argument("--measurements", nargs="*")
    rep.add_argument("--title", default="Media analysis report")
    rep.add_argument("--scope")
    rep.add_argument("--interpretation", action="append")
    rep.add_argument("--extra-evidence", dest="extra_evidence", action="append")
    rep.add_argument("--limit", action="append")
    rep.add_argument("--next-step", dest="next_step", action="append")
    rep.add_argument("--md", help="also write a Markdown rendering here")

    ev = sub.add_parser("evaluate", help="validate and score a report")
    common(ev)
    ev.add_argument("--report", required=True)
    ev.add_argument("--plan")

    val = sub.add_parser("validate", help="validate config/plan/report documents")
    common(val)
    val.add_argument("--config-file", dest="config_file")
    val.add_argument("--plan")
    val.add_argument("--report")

    st = sub.add_parser("self-test", help="run the built-in end-to-end self-test")
    common(st)

    return p


HANDLERS = {
    "help": op_help,
    "capabilities": op_capabilities,
    "parse": op_parse,
    "plan": op_plan,
    "probe": op_probe,
    "inspect": op_inspect,
    "ingest": op_ingest,
    "analyze-image": op_analyze_image,
    "analyze-audio": op_analyze_audio,
    "analyze-video": op_analyze_video,
    "subtitle-audit": op_subtitle_audit,
    "accessibility": op_accessibility,
    "conformance": op_conformance,
    "index": op_index,
    "report": op_report,
    "evaluate": op_evaluate,
    "validate": op_validate,
    "self-test": op_selftest,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        argv = ["help"]
    args = parser.parse_args(argv)
    if not getattr(args, "operation", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        config = load_config(getattr(args, "config", None))
    except CliError as exc:
        print(json.dumps({"ok": False, "errors": [exc.to_dict()]}, indent=2))
        return exc.code
    handler = HANDLERS.get(args.operation)
    if handler is None:
        print(json.dumps({"ok": False, "errors": [{"message": "unknown operation %r"
                                                              % args.operation}]},
                         indent=2))
        return EXIT_USAGE
    started = time.time()
    try:
        result, human = handler(args, config)
    except CliError as exc:
        payload = envelope(args.operation, {}, errors=[exc.to_dict()])
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.stderr.write("%s: %s\n%s\n" % (args.operation, exc.message,
                                           ("hint: " + exc.hint) if exc.hint else ""))
        return exc.code
    except planner.CatalogError as exc:
        payload = envelope(args.operation, {}, errors=[{"kind": "catalog",
                                                        "message": str(exc)}])
        print(json.dumps(payload, indent=2))
        sys.stderr.write("catalog error: %s\n" % exc)
        return EXIT_USAGE
    except KeyboardInterrupt:
        sys.stderr.write("cancelled by user\n")
        return 130
    except Exception as exc:  # defensive top-level guard
        payload = envelope(args.operation, {}, errors=[
            {"kind": "unexpected", "message": "%s: %s" % (type(exc).__name__, exc)}])
        print(json.dumps(payload, indent=2))
        return EXIT_FAILURES
    payload = result
    payload["elapsed_s"] = round(time.time() - started, 3)
    out_path = getattr(args, "out", None)
    # Operations that persist their own primary artifact have already written it.
    self_writing = {
        "parse", "plan", "ingest", "report", "subtitle-audit", "index",
        "analyze-image", "analyze-audio", "analyze-video", "probe", "inspect",
        "evaluate", "conformance", "accessibility",
    }
    if out_path and args.operation not in self_writing:
        try:
            write_output(out_path, payload, getattr(args, "approve", False), config)
        except CliError as exc:
            sys.stderr.write("%s\n" % exc.message)
            return exc.code
    if out_path and args.operation in self_writing:
        sys.stdout.write(json.dumps({
            "ok": payload["ok"], "tool": TOOL_NAME, "version": VERSION,
            "operation": payload["operation"], "out": os.path.abspath(out_path),
            "elapsed_s": payload["elapsed_s"],
            "errors": payload.get("errors", []),
            "warnings": payload.get("warnings", []),
        }, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    if human and not getattr(args, "quiet", False):
        sys.stderr.write(human + "\n")
    code = EXIT_OK if payload["ok"] else EXIT_FAILURES
    for err in payload.get("errors", []):
        if err.get("kind") in ("safety",) or err.get("code") == EXIT_GATE:
            code = EXIT_GATE
        elif err.get("code") == EXIT_INTEGRATION:
            code = EXIT_INTEGRATION
    return code


if __name__ == "__main__":
    sys.exit(main())
