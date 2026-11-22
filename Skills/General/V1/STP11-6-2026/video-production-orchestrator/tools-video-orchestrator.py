#!/usr/bin/env python3
"""video-production-orchestrator - primary entry point.

A unified, production-grade Skill Tool for the whole video production lifecycle
described by the Ultimate Video Maker Skill Catalog (25 categories, 250
capabilities): planning, writing, boarding, ingest, editing, motion, graphics,
text, compositing, VFX, generative, animation, colour, audio, image work,
restoration, genre formats, work video, localisation, versioning, mastering,
QA/rights, pipeline and autonomous orchestration.

Run `python3 tools-video-orchestrator.py capabilities` for the capability list
and `python3 tools-video-orchestrator.py help` for full usage.

Exit codes: 0 ok, 2 usage/validation, 3 not found, 4 gate/approval,
5 external tool failure, 6 timeout, 7 internal error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True

_HERE = Path(__file__).resolve().parent
from importlib.machinery import SourceFileLoader  # noqa: E402

core = SourceFileLoader("video_core", str(_HERE / "tools-video-core.py")).load_module()
_catalog = SourceFileLoader("video_plan", str(_HERE / "tools-video-plan.py")).load_module()
_input = SourceFileLoader("video_input", str(_HERE / "tools-video-input.py")).load_module()
try:
    _media = SourceFileLoader("video_media", str(_HERE / "tools-video-media.py")).load_module()
    MEDIA_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only if the file is damaged
    _media = None
    MEDIA_IMPORT_ERROR = str(exc)

TOOL = "video-production-orchestrator"
VERSION = core.TOOL_VERSION

COMMANDS = [
    ("capabilities", "List every capability, category, phase and the matching CLI command."),
    ("parse", "Parse plain text, Markdown or copied HTML into categories and clean capability names."),
    ("search", "Search the 250-capability catalog by keyword."),
    ("plan", "Resolve a request into a validated production plan (.json/.md)."),
    ("coverage", "Report requirement coverage, phases, approvals and blocking risks for a plan."),
    ("brief", "Emit an execution-ready task brief for one capability in a plan."),
    ("digest", "Expand a master delivery configuration into a version matrix."),
    ("variant", "Generate distinct creative variants (hook, pacing, visual direction)."),
    ("probe", "Inspect real media: codec, resolution, fps, colour, audio layout, duration."),
    ("thumbs", "Extract an evenly spaced still strip for review."),
    ("qa", "Scan media for black frames, freezes, silence, loudness and true peak."),
    ("reframe", "Render one aspect-ratio version (crop or blurred-pad letterbox)."),
    ("transcode", "Render a deterministic H.264/AAC review copy."),
    ("captions", "Validate, retime and re-write SRT/VTT caption files."),
    ("manifest", "Build or verify a delivery manifest with sha256 checksums."),
    ("stack", "Run a per-file media stack policy (probe + checks) and report pass/fail."),
    ("approve", "Two-step approval gate for irreversible or sensitive actions."),
    ("selftest", "Run the built-in validation suite against real generated media."),
    ("doctor", "Report runtime, dependency and asset availability."),
]


# --------------------------------------------------------------------- helpers
def _read_request(args):
    """Load request text from --request, --request-file or stdin."""
    if args.request:
        return args.request, "inline"
    if args.request_file:
        path = core.ensure_file(args.request_file, "request file")
        return core.read_text(path, "request file"), str(path)
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data, "stdin"
    raise core.usage_error("no request supplied",
                           hint="Pass --request \"...\", --request-file PATH, or pipe text on stdin.")


def _emit(payload, args, text_renderer=None):
    fmt = getattr(args, "format", "json")
    out = getattr(args, "output", None)
    if fmt == "json":
        body = json.dumps(payload, indent=2, ensure_ascii=False)
    elif fmt in ("md", "markdown"):
        body = text_renderer(payload) if text_renderer else core.render_markdown(payload)
    else:
        body = core.render_text(payload)
    if out:
        path = core.resolve(out)
        core.atomic_write_text(path, body + "\n")
        print(json.dumps({"written": str(path), "format": fmt, "bytes": len(body) + 1}))
    else:
        print(body)
    return payload


def _emit_stdout(payload, args, renderer=None):
    """Emit the result to stdout even when --output was used for the artifact."""
    clone = argparse.Namespace(**vars(args))
    clone.output = None
    return _emit(payload, clone, renderer)


def _media_guard():
    if _media is None:
        raise core.ToolError("missing_module", f"media module failed to load: {MEDIA_IMPORT_ERROR}",
                             exit_code=core.EXIT_INTERNAL)
    return _media


def _load_plan(path, validate=True):
    plan = core.read_json(path, "production plan")
    if validate:
        schema = core.read_json(_HERE / _catalog.PLAN_SCHEMA, "plan schema")
        errors = core.validate_schema(plan, schema)
        if errors:
            raise core.validation_error(
                f"plan failed schema validation: {path}",
                hint="Regenerate with the 'plan' command or fix the listed fields.",
                details={"errors": errors[:40]})
    return plan


# -------------------------------------------------------------------- commands
def cmd_capabilities(args):
    data, _ = _catalog.load_catalog(args.catalog)
    categories = data["categories"]
    if args.category:
        wanted = {c.lower() for c in args.category}
        categories = [c for c in categories
                      if c["id"].lower() in wanted or c["slug"] in wanted or c["category"].lower() in wanted]
        if not categories:
            raise core.not_found(f"no category matched: {', '.join(args.category)}",
                                 hint="Run 'capabilities' with no filter to list category ids.")
    rows = []
    for cat in categories:
        for sid in cat["skills"]:
            skill = next(s for s in data["skills"] if s["id"] == sid)
            rows.append({
                "id": skill["id"], "name": skill["name"], "category": cat["category"],
                "category_id": cat["id"], "family": skill["family"], "phase": skill["phase"],
                "requires_approval": skill["requires_approval"],
                "description": skill["description"],
                "command": f"python3 tools-video-orchestrator.py brief --plan <plan.json> --capability {skill['id']}",
            })
    if args.phase:
        rows = [r for r in rows if r["phase"] in args.phase]
    if args.family:
        rows = [r for r in rows if r["family"] in {f.lower() for f in args.family}]
    payload = {
        "tool": TOOL, "version": VERSION,
        "catalog": {"categories": len(categories), "capabilities": len(rows),
                    "total_catalog_skills": data["catalog"]["skill_count"],
                    "source": data["catalog"]["source"]},
        "phases": data["catalog"]["phases"],
        "cli_commands": [{"command": c, "purpose": p} for c, p in COMMANDS],
        "capabilities": rows,
    }
    if args.count_only:
        payload.pop("capabilities")
    return _emit(payload, args, _render_capabilities)


def _render_capabilities(payload):
    lines = [f"# {payload['tool']} {payload['version']}", "",
             f"Catalog: {payload['catalog']['categories']} categories / "
             f"{payload['catalog']['total_catalog_skills']} capabilities", "",
             "## CLI commands", "", "| command | purpose |", "| --- | --- |"]
    lines += [f"| `{c['command']}` | {c['purpose']} |" for c in payload["cli_commands"]]
    if "capabilities" in payload:
        lines += ["", "## Capabilities", "", "| id | name | category | phase | approval |",
                  "| --- | --- | --- | --- | --- |"]
        lines += [f"| {r['id']} | {r['name']} | {r['category']} | {r['phase']} | "
                  f"{'yes' if r['requires_approval'] else 'no'} |" for r in payload["capabilities"]]
    return "\n".join(lines) + "\n"


def cmd_parse(args):
    text, source = _read_request(args)
    parsed = _input.normalise_request(text)
    payload = {
        "tool": TOOL, "source": source, "source_format": parsed["source_format"],
        "umbrella_name": parsed["umbrella_name"], "category_count": parsed["category_count"],
        "skill_count": parsed["skill_count"], "categories": parsed["categories"],
        "skills": parsed["skills"], "warnings": parsed["warnings"],
    }
    return _emit(payload, args, lambda p: _render_parse(p))


def _render_parse(p):
    lines = [f"# Parsed request ({p['source_format']})", "",
             f"- umbrella name: `{p['umbrella_name']}`",
             f"- categories: {p['category_count']}",
             f"- capabilities: {p['skill_count']}", ""]
    for cat in p["categories"]:
        lines.append(f"## {cat['name'] or '(no category heading)'}")
        lines.append("")
        for sk in cat["skills"]:
            desc = sk["description"] or "_(no description supplied)_"
            lines.append(f"- **{sk['name']}** — {desc}")
        lines.append("")
    if p["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in p["warnings"]]
    return "\n".join(lines).rstrip() + "\n"


def cmd_search(args):
    data, _ = _catalog.load_catalog(args.catalog)
    result = _catalog.search_capabilities(args.query, data, limit=max(args.limit * 4, 20))
    if args.family:
        wanted = {f.lower() for f in _input.split_values(args.family)}
        result["results"] = [r for r in result["results"] if r["family"] in wanted]
        result["family_filter"] = sorted(wanted)
    result["results"] = result["results"][: args.limit]
    result["returned"] = len(result["results"])
    result["tool"] = TOOL
    return _emit(result, args)


def cmd_plan(args):
    data, _ = _catalog.load_catalog(args.catalog)
    text, source = None, "catalog"
    resolved = None
    if args.plan_from:
        resolved = _resolve_from_manifest(args)
    else:
        text, source = _read_request(args)
        resolved = _catalog.resolve(text, (data, _catalog.load_catalog(args.catalog)[1]),
                                    max_capabilities=args.max_capabilities,
                                    requested=_input.split_values(args.capability))
    plan = _catalog.build_plan(
        resolved, data,
        goal=args.goal,
        audience=args.audience,
        platform=args.platform,
        duration_seconds=core.parse_duration(args.duration) if args.duration else None,
        outputs=_parse_outputs(args.deliverable),
        source_label=source,
        plan_id=args.plan_id,
    )
    coverage = _catalog.coverage_report(plan, data, request_text=resolved["request"])
    out_path = core.resolve(args.output or f"{plan['plan_id']}.json")
    if args.format in ("md", "markdown"):
        out_path = out_path.with_suffix(".md")
        core.atomic_write_text(out_path, _render_plan(plan, coverage))
    else:
        core.atomic_write_json(out_path, plan)
    payload = {
        "tool": TOOL, "plan_id": plan["plan_id"], "plan_path": str(out_path),
        "capability_count": len(plan["capabilities"]),
        "phases": sorted({c["phase"] for c in plan["capabilities"]}),
        "gates": [g["id"] for g in plan["gates"]],
        "risks": [r["code"] for r in plan["risks"]],
        "coverage": coverage["status"], "uncovered": coverage["uncovered"],
        "umbrella_name": plan["umbrella_name"],
    }
    return _emit_stdout(payload, args, lambda p: _render_plan_summary(p))


def _resolve_from_manifest(args):
    manifest = core.read_json(args.plan_from, "request manifest")
    if not isinstance(manifest, dict) or "request" not in manifest:
        raise core.validation_error("request manifest must be an object with a 'request' field")
    data, index = _catalog.load_catalog(args.catalog)
    requested = _input.split_values(args.capability) + list(manifest.get("capabilities") or [])
    return _catalog.resolve(manifest["request"], (data, index),
                            max_capabilities=args.max_capabilities, requested=requested)


def _parse_outputs(values):
    out = []
    for raw in values or []:
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                kind, path = part.split(":", 1)
                out.append({"kind": kind.strip(), "path": path.strip()})
            else:
                out.append(part)
    return out


def _render_plan_summary(p):
    return core.render_markdown({k: v for k, v in p.items() if k != "tool"})


def _render_plan(plan, coverage):
    lines = [f"# Production plan: {plan['plan_id']}", "",
             f"**Goal.** {plan['goal']}", ""]
    for key in ("audience", "platform", "duration_seconds"):
        if plan.get(key):
            lines.append(f"- **{key.replace('_', ' ')}**: {plan[key]}")
    lines += ["", "## Deliverables", "", "| id | kind | path | status |", "| --- | --- | --- | --- |"]
    lines += [f"| {d['id']} | {d['kind']} | `{d['path']}` | {d['status']} |" for d in plan["deliverables"]]
    lines += ["", "## Capabilities by phase", ""]
    for phase in sorted({c["phase"] for c in plan["capabilities"]}):
        lines.append(f"### Phase {phase} — {_catalog.PHASE_NAMES.get(phase, '')}")
        lines.append("")
        for cap in [c for c in plan["capabilities"] if c["phase"] == phase]:
            flag = " **[approval]**" if cap["requires_approval"] else ""
            lines.append(f"- `{cap['id']}` **{cap['name']}**{flag} — {cap['category']}")
        lines.append("")
    lines += ["## Quality gates", "", "| id | check | blocking | status |", "| --- | --- | --- | --- |"]
    lines += [f"| {g['id']} | {g['check']} | {'yes' if g['blocking'] else 'no'} | {g['status']} |"
              for g in plan["gates"]]
    if plan["risks"]:
        lines += ["", "## Risks", ""]
        lines += [f"- **{r['severity']}** `{r['code']}` — {r['message']}  \n  _Mitigation:_ {r.get('mitigation','')}"
                  for r in plan["risks"]]
    lines += ["", "## Coverage", "", f"Status: **{coverage['status']}**",
              f"Uncovered requirements: {', '.join(coverage['uncovered']) or 'none'}"]
    return "\n".join(lines) + "\n"


def cmd_coverage(args):
    plan = _load_plan(args.plan)
    data, _ = _catalog.load_catalog(args.catalog)
    report = _catalog.coverage_report(plan, data,
                                      request_text=args.request if args.request else None)
    report["tool"] = TOOL
    status = _emit(report, args, lambda p: core.render_markdown(
        {k: v for k, v in p.items() if k != "tool"}))
    if args.fail_on_gap and report["status"] == "fail":
        raise core.gate_error("coverage gate failed", details={"uncovered": report["uncovered"]})
    return status


def cmd_brief(args):
    plan = _load_plan(args.plan)
    data, _ = _catalog.load_catalog(args.catalog)
    brief = _catalog.task_brief(plan, args.capability, data)
    brief["tool"] = TOOL
    return _emit(brief, args, lambda p: _render_brief(p))


def _render_brief(p):
    cap = p["capability"]
    lines = [f"# Task brief — {cap['name']} (`{cap['id']}`)", "",
             f"- **Plan**: {p['plan_id']}", f"- **Category**: {cap['category']}",
             f"- **Phase**: {cap['phase']} — {cap['phase_name']}",
             f"- **Approval required**: {'yes' if p['approval_required'] else 'no'}", "",
             f"**Source description.** {cap['description']}", "",
             f"**Why it is in this plan.** {p['why']}", "",
             "## Inputs", ""]
    lines += [f"- **{k}**: {v}" for k, v in p["inputs"].items()]
    lines += ["", "## Procedure", ""] + [f"{i}. {step}" for i, step in enumerate(p["procedure"], 1)]
    lines += ["", "## Outputs", ""] + [f"- `{o}`" for o in p["outputs"]]
    lines += ["", "## Checks that must pass", ""] + [f"- {c}" for c in p["checks"]]
    lines += ["", "## Done when", ""] + [f"- {d}" for d in p["done_when"]]
    lines += ["", "## Limits", ""] + [f"- **{k}**: {v}" for k, v in p["limits"].items()]
    return "\n".join(lines) + "\n"


def cmd_digest(args):
    config = core.read_json(args.config, "master configuration")
    media = _media_guard()
    matrix = media.digest_master_config(config, platforms=_input.split_values(args.platform))
    duration_note = None
    if args.check_duration and config.get("master", {}).get("source"):
        source = core.resolve(config["master"]["source"])
        if not source.exists():
            duration_note = (f"master.source not found ({config['master']['source']}); "
                             "duration limits were not verified")
        if source.exists():
            probe = media.probe(source, timeout=args.timeout)
            duration = probe["duration_seconds"]
            for row in matrix["variants"]:
                limit = row.get("duration_limit_seconds")
                row["duration_ok"] = (limit is None or duration is None or duration <= limit)
                row["source_duration_seconds"] = duration
    elif args.check_duration:
        duration_note = "no master.source declared; duration limits were not verified"
    if duration_note:
        matrix["duration_check_note"] = duration_note
    matrix["tool"] = TOOL
    out = args.output or "delivery-matrix.json"
    path = core.write_output(core.resolve(out), matrix, fmt="json" if args.format == "json" else args.format)
    matrix["output"] = str(path)
    return _emit_stdout(matrix, args, lambda p: core.render_markdown(
        {"variant_count": p["variant_count"], "output": p.get("output"),
         "variants": p["variants"]}))


def cmd_variant(args):
    result = _catalog.explore_variants(args.count, base=args.base, seed=args.seed)
    result["tool"] = TOOL
    return _emit(result, args)


def cmd_probe(args):
    media = _media_guard()
    result = media.probe(core.resolve(args.input), timeout=args.timeout)
    result["tool"] = TOOL
    result["runtime"] = media.availability()
    return _emit(result, args)


def cmd_thumbs(args):
    media = _media_guard()
    out_dir = core.resolve(args.output_dir or "thumbs")
    result = media.thumbnails(core.resolve(args.input), out_dir, count=args.count,
                              width=args.width, timeout=args.timeout)
    result["tool"] = TOOL
    return _emit(result, args)


def cmd_qa(args):
    media = _media_guard()
    result = media.qa_scan(
        core.resolve(args.input),
        black_threshold=args.black_threshold,
        black_min_duration=args.black_min_duration,
        silence_db=args.silence_db,
        silence_min_duration=args.silence_min,
        freeze_min_duration=args.freeze_min,
        timeout=args.timeout)
    result["tool"] = TOOL
    if args.output:
        core.atomic_write_json(core.resolve(args.output), result)
    _emit(result, args, lambda p: core.render_markdown(
        {"path": p["path"], "summary": p["summary"], "evidence": p["evidence"]}))
    if result["summary"]["status"] == "fail" and args.fail_on_error:
        raise core.gate_error("QA gate failed",
                              details={"findings": [f for f in result["findings"] if f["severity"] == "fail"]})
    return result


def cmd_reframe(args):
    media = _media_guard()
    out = args.output or f"reframed-{args.aspect.replace(':','x')}.mp4"
    result = media.reframe(core.resolve(args.input), core.resolve(out), aspect=args.aspect,
                           mode=args.mode, blur_pad=not args.no_blur_pad, crf=args.crf,
                           timeout=args.timeout)
    result["tool"] = TOOL
    return _emit_stdout(result, args)


def cmd_transcode(args):
    media = _media_guard()
    out = args.output or "review.mp4"
    result = media.transcode(core.resolve(args.input), core.resolve(out), crf=args.crf,
                             max_width=args.max_width, timeout=args.timeout)
    result["tool"] = TOOL
    return _emit_stdout(result, args)


def cmd_captions(args):
    media = _media_guard()
    path = core.ensure_file(core.resolve(args.input), "caption file")
    text = core.read_text(path, "caption file")
    cues, parse_warnings = media.parse_srt(text)
    if not cues:
        raise core.validation_error("no cues parsed from the caption file",
                                    details={"warnings": parse_warnings[:20]})
    offset = core.parse_duration(args.offset) if args.offset else 0.0
    scale = args.scale
    if args.stretch_to:
        current_end = max(c["end"] for c in cues)
        if current_end <= 0:
            raise core.validation_error("cannot stretch captions with zero duration")
        scale = core.parse_duration(args.stretch_to) / current_end
    retimed, findings = media.retime_srt(cues, offset=offset, scale=scale,
                                         min_duration=args.min_duration,
                                         max_cps=args.max_cps)
    findings = [core.finding("info", "parse_warning", w) for w in parse_warnings] + findings
    payload = {
        "tool": TOOL, "input": str(path), "cue_count": len(retimed),
        "offset_seconds": offset, "scale": round(scale, 4),
        "cues": retimed, "findings": findings, "summary": core.summarise_findings(findings),
    }
    if args.output:
        out = core.resolve(args.output)
        media.write_srt(retimed, out)
        payload["output"] = str(out)
        payload["bytes"] = out.stat().st_size
    _emit_stdout(payload, args, lambda p: core.render_markdown(
        {"input": p["input"], "output": p.get("output"), "cue_count": p["cue_count"],
         "summary": p["summary"], "findings": p["findings"]}))
    if payload["summary"]["status"] == "fail" and args.fail_on_error:
        raise core.gate_error("caption validation failed",
                              details={"findings": [f for f in findings if f["severity"] == "fail"]})
    return payload


def cmd_manifest(args):
    if args.verify:
        manifest = core.read_json(args.verify, "delivery manifest")
        result = _catalog.verify_manifest(manifest, root=args.root)
        result.update({"tool": TOOL, "manifest": str(core.resolve(args.verify)), "mode": "verify"})
        _emit(result, args)
        if result["summary"]["status"] == "fail" and args.fail_on_error:
            raise core.gate_error("manifest verification failed")
        return result

    entries = []
    for item in args.entry or []:
        for part in item.split(";"):
            part = part.strip()
            if not part:
                continue
            fields = part.split(",")
            entry = {"path": fields[0]}
            for field in fields[1:]:
                if "=" in field:
                    key, value = field.split("=", 1)
                    entry[key.strip()] = value.strip()
            entries.append(entry)
    if not entries:
        raise core.usage_error("no entries supplied",
                               hint="Use --entry \"path/to/master.mp4,role=master,version=v1\" "
                                    "or --verify manifest.json")
    manifest = _catalog.build_manifest(entries, root=args.root, title=args.title,
                                       include_checksums=not args.no_checksums)
    out = core.resolve(args.output or "delivery-manifest.json")
    core.atomic_write_json(out, manifest)
    result = {"tool": TOOL, "mode": "build", "output": str(out),
              "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"],
              "summary": manifest["summary"], "problems": manifest["problems"]}
    _emit_stdout(result, args)
    if manifest["summary"]["status"] == "fail" and args.fail_on_error:
        raise core.gate_error("manifest build failed: missing files", details=manifest["problems"])
    return result


def cmd_stack(args):
    media = _media_guard()
    policy = core.read_json(args.policy, "stack policy")
    errors = core.validate_schema(policy, core.read_json(_HERE / "asset-media-stack.schema.json", "stack schema"))
    if errors:
        raise core.validation_error("stack policy failed validation", details={"errors": errors[:20]})
    files = [core.resolve(f) for f in _input.split_values(args.input, limit=200)]
    if not files:
        raise core.usage_error("no input files supplied", hint="Pass --input a.mp4,b.mp4 or a glob list.")
    checks = set(policy.get("checks") or ["probe"])
    delivery = policy.get("delivery", {}) or {}
    findings, rows = [], []
    for path in files:
        row = {"path": str(path)}
        if not path.exists():
            findings.append(core.finding("fail", "missing_file", f"missing input: {path.name}", path=str(path)))
            row["status"] = "missing"
            rows.append(row)
            continue
        probe = media.probe(path, timeout=args.timeout)
        row.update({"duration_seconds": probe["duration_seconds"],
                    "resolution": [probe["video"]["width"], probe["video"]["height"]] if probe["video"] else None,
                    "fps": probe["video"]["fps"] if probe["video"] else None,
                    "bytes": probe["size_bytes"]})
        local = []
        if "aspect" in checks and delivery.get("aspect") and probe["video"]:
            num, den = media.TARGET_ASPECTS[delivery["aspect"]]
            target = num / den
            actual = probe["video"]["width"] / probe["video"]["height"]
            if abs(target - actual) > 0.02:
                local.append(core.finding("fail", "aspect_mismatch",
                                          f"{path.name}: aspect {probe['video'].get('aspect_ratio')} != "
                                          f"{delivery['aspect']}", path=str(path)))
        if "duration" in checks and delivery.get("max_duration_seconds"):
            if (probe["duration_seconds"] or 0) > delivery["max_duration_seconds"]:
                local.append(core.finding("fail", "duration_over",
                                          f"{path.name}: {probe['duration_seconds']:.1f}s exceeds "
                                          f"{delivery['max_duration_seconds']}s", path=str(path)))
        if "size" in checks and delivery.get("max_bytes"):
            if probe["size_bytes"] > delivery["max_bytes"]:
                local.append(core.finding("warn", "size_over",
                                          f"{path.name}: {probe['size_bytes']} bytes over budget", path=str(path)))
        if delivery.get("require_audio") and not probe["has_audio"]:
            local.append(core.finding("fail", "no_audio", f"{path.name}: required audio stream missing",
                                      path=str(path)))
        if "interlace" in checks and probe["video"] and probe["video"]["is_interlaced"]:
            local.append(core.finding("warn", "interlaced", f"{path.name}: interlaced source", path=str(path)))
        if "alpha" in checks and probe["video"] and not probe["video"]["has_alpha"]:
            local.append(core.finding("warn", "no_alpha", f"{path.name}: no alpha channel", path=str(path)))
        if ("loudness" in checks or "captions" in checks) and probe["has_audio"]:
            qa = media.qa_scan(path, timeout=args.timeout)
            lufs = qa["evidence"].get("integrated_lufs")
            peak = qa["evidence"].get("true_peak_dbfs")
            target = delivery.get("loudness_target_lufs")
            max_peak = delivery.get("max_true_peak_dbfs", -1.0)
            if "loudness" in checks and target is not None and lufs is not None and abs(lufs - target) > 2.0:
                local.append(core.finding("warn", "loudness_off_target",
                                          f"{path.name}: {lufs:.1f} LUFS vs target {target} LUFS",
                                          path=str(path)))
            if peak is not None and peak > max_peak:
                local.append(core.finding("fail", "true_peak_over",
                                          f"{path.name}: true peak {peak:.1f} dBFS", path=str(path)))
        if "captions" in checks and delivery.get("captions"):
            cap = core.resolve(delivery["captions"])
            if not cap.exists():
                local.append(core.finding("fail", "captions_missing",
                                          f"{path.name}: caption file missing: {delivery['captions']}",
                                          path=str(path)))
        row["findings"] = local
        row["status"] = core.summarise_findings(local)["status"]
        findings.extend(local)
        rows.append(row)
    payload = {"tool": TOOL, "policy": policy.get("name"), "file_count": len(rows),
               "files": rows, "findings": findings, "summary": core.summarise_findings(findings)}
    _emit(payload, args, lambda p: core.render_markdown(
        {"policy": p["policy"], "file_count": p["file_count"], "summary": p["summary"],
         "findings": p["findings"]}))
    if payload["summary"]["status"] == "fail" and args.fail_on_error:
        raise core.gate_error("media stack gate failed")
    return payload


def cmd_approve(args):
    targets = _input.split_values(args.target)
    if not targets:
        raise core.usage_error("at least one --target is required",
                               hint="List the files or actions the approval covers.")
    if not args.approve_plan and not args.approve:
        expected = core.approval_token(args.action, targets)
        payload = {"tool": TOOL, "command": args.action, "targets": targets,
                   "approval_required": True, "token": expected,
                   "next_step": f"--approve {expected}"}
        return _emit(payload, args)
    token = core.require_approval(args.action, targets, args.approve,
                                  log=core.Log("info"), reason=args.action)
    payload = {"tool": TOOL, "command": args.action, "targets": targets,
               "approval_granted": True, "token": token}
    if args.record:
        record_path = core.resolve(args.record)
        history = []
        if record_path.exists():
            try:
                history = core.read_json(record_path, "approval record").get("approvals", [])
            except core.ToolError:
                history = []
        history.append({"command": args.action, "targets": targets, "token": token,
                        "approved_at": core.time.strftime("%Y-%m-%dT%H:%M:%SZ", core.time.gmtime())})
        core.atomic_write_json(record_path, {"approvals": history})
        payload["record"] = str(record_path)
    return _emit(payload, args)


def cmd_doctor(args):
    media = _media_guard()
    assets = {}
    for name in ("asset-catalog-data.json", "asset-production-plan.schema.json",
                 "asset-master-config.schema.json", "asset-media-stack.schema.json",
                 "asset-approval-policy.json", "asset-type-scale.json",
                 "asset-sample-master-config.json", "asset-sample-media-stack.json",
                 "asset-fixture-qa-sample.mp4"):
        path = _HERE / name
        assets[name] = {"present": path.exists(),
                        "bytes": path.stat().st_size if path.exists() else 0}
    data, _ = _catalog.load_catalog(args.catalog)
    payload = {
        "tool": TOOL, "version": VERSION, "python": sys.version.split()[0],
        "platform": sys.platform,
        "media_runtime": media.availability(),
        "catalog": {"categories": data["catalog"]["category_count"],
                    "capabilities": data["catalog"]["skill_count"]},
        "assets": assets,
        "all_assets_present": all(a["present"] for a in assets.values()),
        "commands": len(COMMANDS),
    }
    return _emit(payload, args)


def cmd_selftest(args):
    here = _HERE
    from importlib.machinery import SourceFileLoader as _L
    tests = _L("video_tests", str(here / "tools-video-selftest.py")).load_module()
    result = tests.run_suite(workdir=args.workdir, keep=args.keep, timeout=args.timeout)
    return _emit(result, args, lambda p: tests.render_report(p))


# ------------------------------------------------------------------- argparse
def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-video-orchestrator.py",
        description=f"{TOOL} {VERSION} - unified video production capability runtime.",
        epilog="Run 'capabilities' for the full capability list, or 'selftest' to validate the install.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{TOOL} {VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, help_text):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("--format", choices=["json", "md", "text"], default="json",
                       help="output format (default: json)")
        p.add_argument("--output", help="write the result to this path instead of stdout")
        return p

    p = add("capabilities", "List capabilities, categories, phases and CLI commands.")
    p.add_argument("--category", action="append", help="filter by category id (C01), slug or exact name")
    p.add_argument("--family", action="append", help="filter by capability family, e.g. audio")
    p.add_argument("--phase", action="append", type=int, choices=[1, 2, 3, 4, 5])
    p.add_argument("--catalog", help="path to asset-catalog-data.json")
    p.add_argument("--count-only", action="store_true", help="omit the capability rows")

    p = add("parse", "Parse text/Markdown/HTML into categories and clean capability names.")
    p.add_argument("--request", help="inline request or capability list")
    p.add_argument("--request-file", help="read the request from this file")

    p = add("search", "Search the catalog by keyword.")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--family", action="append", help="restrict to these capability families")
    p.add_argument("--catalog")

    p = add("plan", "Resolve a request into a validated production plan.")
    p.add_argument("--request", help="inline request text")
    p.add_argument("--request-file", help="read the request from a file")
    p.add_argument("--plan-from", help="JSON manifest with a 'request' field and optional 'capabilities'")
    p.add_argument("--capability", action="append", help="force a capability id/slug/name (repeatable)")
    p.add_argument("--max-capabilities", type=int, default=60)
    p.add_argument("--goal")
    p.add_argument("--audience")
    p.add_argument("--platform")
    p.add_argument("--duration", help="target runtime: 90, 1m30s or 00:01:30")
    p.add_argument("--deliverable", action="append",
                   help="kind:path pair, e.g. final-master:masters/final.mp4")
    p.add_argument("--plan-id")
    p.add_argument("--catalog")
    p.set_defaults(output=None)

    p = add("coverage", "Report requirement coverage for a plan.")
    p.add_argument("--plan", required=True)
    p.add_argument("--request", help="override the request text used for requirement inference")
    p.add_argument("--catalog")
    p.add_argument("--fail-on-gap", action="store_true", help="exit 4 when coverage fails")

    p = add("brief", "Execution-ready brief for one capability in a plan.")
    p.add_argument("--plan", required=True)
    p.add_argument("--capability", required=True)
    p.add_argument("--catalog")

    p = add("digest", "Expand a master delivery configuration into a version matrix.")
    p.add_argument("--config", required=True)
    p.add_argument("--platform", action="append", help="override platform presets")
    p.add_argument("--check-duration", action="store_true", help="probe master.source and test limits")
    p.add_argument("--timeout", type=float, default=120.0)

    p = add("variant", "Generate distinct creative variants.")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--base")
    p.add_argument("--seed")

    p = add("probe", "Inspect real media metadata.")
    p.add_argument("--input", required=True)
    p.add_argument("--timeout", type=float, default=120.0)

    p = add("thumbs", "Extract an evenly spaced still strip.")
    p.add_argument("--input", required=True)
    p.add_argument("--output-dir", default="thumbs")
    p.add_argument("--count", type=int, default=9)
    p.add_argument("--width", type=int, default=320)
    p.add_argument("--timeout", type=float, default=120.0)

    p = add("qa", "Scan media for black frames, freezes, silence and loudness.")
    p.add_argument("--input", required=True)
    p.add_argument("--black-threshold", type=float, default=0.10)
    p.add_argument("--black-min-duration", type=float, default=0.5)
    p.add_argument("--silence-db", type=float, default=-50.0)
    p.add_argument("--silence-min", type=float, default=2.0)
    p.add_argument("--freeze-min", type=float, default=2.0)
    p.add_argument("--fail-on-error", action="store_true", help="exit 4 when QA finds a failure")
    p.add_argument("--timeout", type=float, default=300.0)

    p = add("reframe", "Render one aspect-ratio version of a clip (--output is the video path).")
    p.add_argument("--input", required=True)
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--mode", choices=["crop", "pad"], default="crop")
    p.add_argument("--no-blur-pad", action="store_true")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--timeout", type=float, default=600.0)

    p = add("transcode", "Render a deterministic H.264/AAC review copy (--output is the video path).")
    p.add_argument("--input", required=True)
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--max-width", type=int)
    p.add_argument("--timeout", type=float, default=600.0)

    p = add("captions", "Validate, retime and rewrite SRT/VTT captions.")
    p.add_argument("--input", required=True)
    p.add_argument("--offset", help="shift all cues, e.g. -1.5s or +00:00:02")
    p.add_argument("--scale", type=float, default=1.0, help="time multiplier (0.98 = 2%% tighter)")
    p.add_argument("--stretch-to", help="rescale the whole file to this total duration")
    p.add_argument("--min-duration", type=float, default=0.8)
    p.add_argument("--max-cps", type=float, default=21.0)
    p.add_argument("--fail-on-error", action="store_true")

    p = add("manifest", "Build or verify a delivery manifest with checksums.")
    p.add_argument("--entry", action="append",
                   help="path[,role=master][,version=v1]; repeatable, ';' separated")
    p.add_argument("--verify", help="verify an existing manifest")
    p.add_argument("--root", help="resolve relative paths against this root")
    p.add_argument("--title")
    p.add_argument("--no-checksums", action="store_true")
    p.add_argument("--fail-on-error", action="store_true")

    p = add("stack", "Run a media stack policy over a set of files.")
    p.add_argument("--policy", required=True)
    p.add_argument("--input", action="append", help="media file(s), comma separated")
    p.add_argument("--fail-on-error", action="store_true")
    p.add_argument("--timeout", type=float, default=300.0)

    p = add("approve", "Two-step approval gate for sensitive actions.")
    p.add_argument("--action", required=True, help="short action name, e.g. 'publish-master'")
    p.add_argument("--target", action="append", help="target path or object (repeatable)")
    p.add_argument("--approve-plan", action="store_true", help="stage 1: print the expected token")
    p.add_argument("--approve", help="stage 2: pass the token to grant approval")
    p.add_argument("--record", help="append granted approvals to this JSON file")

    p = add("selftest", "Run the validation suite against real generated media.")
    p.add_argument("--workdir", help="scratch directory (default: a temp dir)")
    p.add_argument("--keep", action="store_true", help="keep the scratch directory")
    p.add_argument("--timeout", type=float, default=600.0)

    p = add("doctor", "Report runtime, dependency and asset availability.")
    p.add_argument("--catalog")

    add("help", "Show this help and the command list.")
    return parser


def cmd_help(args):
    build_parser().print_help()
    print("\nCommands:\n" + "\n".join(f"  {c:<14} {p}" for c, p in COMMANDS))
    return {"tool": TOOL, "version": VERSION,
            "commands": [{"command": c, "purpose": p} for c, p in COMMANDS]}


DISPATCH = {
    "help": cmd_help,
    "capabilities": cmd_capabilities, "parse": cmd_parse, "search": cmd_search,
    "plan": cmd_plan, "coverage": cmd_coverage, "brief": cmd_brief, "digest": cmd_digest,
    "variant": cmd_variant, "probe": cmd_probe, "thumbs": cmd_thumbs, "qa": cmd_qa,
    "reframe": cmd_reframe, "transcode": cmd_transcode, "captions": cmd_captions,
    "manifest": cmd_manifest, "stack": cmd_stack, "approve": cmd_approve,
    "selftest": cmd_selftest, "doctor": cmd_doctor,
}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        print("\nCommands:\n" + "\n".join(f"  {c:<14} {p}" for c, p in COMMANDS))
        return core.EXIT_OK
    handler = DISPATCH[args.command]
    try:
        handler(args)
        return core.EXIT_OK
    except core.ToolError as exc:
        print(json.dumps({"error": exc.as_dict()}, indent=2, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({"error": {"code": "cancelled", "message": "interrupted by user"}}), file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort structured error
        print(json.dumps({"error": {"code": "internal_error", "message": str(exc),
                                    "type": type(exc).__name__}}, indent=2), file=sys.stderr)
        return core.EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
