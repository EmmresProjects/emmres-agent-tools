#!/usr/bin/env python3
"""tools-audio-suite.py - primary entry point for the Audio Production Suite skill tool.

A single command surface over 28 audio categories and 280 catalogued capabilities:
planning, contract sheets, capability routing, audio measurement and gating,
processing chains, batch runs, delivery manifests, and the approval ledger.

Quick start:
  python3 tools-audio-suite.py help
  python3 tools-audio-suite.py capabilities
  python3 tools-audio-suite.py selftest

Every command supports --json for machine output and --out to persist results.
Relative asset and template paths are resolved from this folder, not from the
working directory. Standard library only; Python 3.9 or newer.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
MODULES = {
    "registry": "tools-capability-registry.py",
    "aio": "tools-audio-io.py",
    "qa": "tools-audio-qa.py",
    "dsp": "tools-audio-dsp.py",
    "planner": "tools-audio-production-planner.py",
    "delivery": "tools-audio-delivery.py",
    "approvals": "tools-audio-approvals.py",
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID = 2
EXIT_GATE = 3

HELP_TEXT = """Audio Production Suite - skill tool for the Ultimate Audio, Music, and SFX catalog

USAGE
  python3 tools-audio-suite.py <command> [options]

CAPABILITY COMMANDS
  capabilities        List the 28 categories, or search the 280 capabilities
  describe            Full record for one capability or category
  route               Map free-text requirements to capabilities
  spec                Contract sheet: inputs, outputs, controls, checks, recovery
  plan                Turn a brief into a production plan (Markdown or JSON)

AUDIO COMMANDS
  inspect             Structural validation of a WAV file, no decoding
  analyze             Measure loudness, peaks, defects, stereo, spectrum; gate them
  regression          Compare a candidate render against an approved reference
  process             Apply an ordered operation chain to one file
  recipe              Apply a packaged recipe (level, cleanup, format targets)
  batch               Apply a recipe across a folder with per-file results
  concat              Join files with an equal-power crossfade
  split               Cut a file at times you give, or at detected silence

DELIVERY COMMANDS
  manifest            Build a manifest with SHA-256 checksums and preset checks
  validate-manifest   Validate a manifest against the packaged JSON schema
  verify-manifest     Re-verify checksums after copying or transfer
  approvals           Request, grant, revoke, list, or verify human approvals

SYSTEM
  selftest            Run the built-in verification suite and print the results
  version             Print version and packaged asset inventory
  help                Show this help, or help for one command

EXAMPLES
  python3 tools-audio-suite.py capabilities --query "mastering loudness"
  python3 tools-audio-suite.py capabilities --category 18
  python3 tools-audio-suite.py plan --goal "podcast episode master" --medium podcast --out plan.md --format markdown
  python3 tools-audio-suite.py analyze --input master.wav --preset podcast-speech --format markdown
  python3 tools-audio-suite.py recipe --input in.wav --output out.wav --recipe podcast-speech-master
  python3 tools-audio-suite.py manifest --input delivery --preset streaming-music --out delivery/manifest.json --format markdown

GLOBAL OPTIONS
  --json              Print machine-readable JSON (default for most commands)
  --format FORMAT     markdown | json | both (where supported)
  --out PATH          Write the result to a file
  --timeout SECONDS   Per-command time budget (default 300)
  --ledger PATH       Approval ledger location (default approval-ledger.json in the cwd)
  --approval ID       Approval id that authorises a destructive or publishing action
  --strict            Exit with code 3 when a verdict is fail

Run "python3 tools-audio-suite.py help <command>" for command-specific options.
"""


def _load_common():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import importlib.util

    spec = importlib.util.spec_from_file_location("aps_audio_common", os.path.join(here, COMMON_FILENAME))
    module = importlib.util.module_from_spec(spec)
    sys.modules["aps_audio_common"] = module
    spec.loader.exec_module(module)
    return module


common = _load_common()


def module(name):
    return common.load_sibling(MODULES[name])


# --- output helpers ---------------------------------------------------------
def output(args, result, text=None, exit_code=EXIT_OK):
    """Write the requested representation to stdout and/or --out, keeping stdout parseable."""
    fmt = getattr(args, "format", "json") or "json"
    out_path = getattr(args, "out", None)
    payload = _strip(result)
    if out_path:
        if fmt == "both":
            base = out_path
            for extension in (".md", ".json"):
                if base.lower().endswith(extension):
                    base = base[:-len(extension)]
            common.write_text(base + ".md", text if text is not None else json.dumps(payload, indent=2))
            common.write_json(base + ".json", payload)
            sys.stderr.write("Wrote %s.md and %s.json\n" % (base, base))
        elif fmt == "markdown":
            if text is None:
                raise common.ToolError("invalid_argument",
                                       "This command has no markdown rendering; use --format json")
            common.write_text(out_path, text)
            sys.stderr.write("Wrote %s\n" % common.resolve_path(out_path))
        else:
            common.write_json(out_path, payload)
            sys.stderr.write("Wrote %s\n" % common.resolve_path(out_path))
    if fmt == "markdown" and text is not None:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return exit_code


def _strip(result):
    return {k: v for k, v in result.items() if k not in ("markdown", "report_markdown")}


def fail(exc, args, exit_code=None):
    code = exit_code or EXIT_INVALID
    payload = exc.as_dict()
    payload["command"] = getattr(args, "command", None)
    if getattr(args, "json", True):
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stderr.write("error [%s]: %s\n" % (exc.code, exc.message))
        if exc.hint:
            sys.stderr.write("hint: %s\n" % exc.hint)
    return code


# --- approval helper --------------------------------------------------------
def require_approval(args, action, target, consume=True):
    approvals = module("approvals")
    result = approvals.verify(getattr(args, "approval", None), action, target,
                             path=getattr(args, "ledger", None), consume=consume,
                             consumed_by=getattr(args, "command", "unknown"))
    return result


# --- op parsing -------------------------------------------------------------
NUMERIC_OPS = {
    "gain": ("db", float),
    "normalize": ("target_lufs", float),
    "limit": ("ceiling_dbtp", float),
    "resample": ("rate", int),
    "channels": ("mode", str),
    "highpass": ("hz", float),
    "lowpass": ("hz", float),
    "fade": ("in_sec", float),
    "trim-silence": ("threshold_db", float),
    "polarity": (None, None),
    "dc-block": (None, None),
}


def parse_op_spec(spec):
    if ":" in spec:
        name, value = spec.split(":", 1)
    else:
        name, value = spec, None
    name = name.strip().lower()
    if name not in NUMERIC_OPS:
        raise common.ToolError("unknown_operation",
                               "Unknown --op %r" % spec,
                               {"available": sorted(NUMERIC_OPS)},
                               hint="Use name:value form, for example --op highpass:80 or --op normalize:-16")
    key, caster = NUMERIC_OPS[name]
    params = {"op": name}
    if key is None:
        if value:
            raise common.ToolError("invalid_argument", "--op %s takes no value" % name)
        return params
    if value is None or value == "":
        raise common.ToolError("invalid_argument", "--op %s needs a value, for example %s:<value>"
                               % (name, name))
    try:
        params[key] = caster(value)
    except ValueError:
        raise common.ToolError("invalid_argument", "--op %s value %r is not a valid %s"
                               % (name, value, caster.__name__))
    return params


def collect_ops(args):
    ops = []
    if getattr(args, "ops_json", None):
        data = common.read_json(args.ops_json, "ops file")
        if isinstance(data, dict):
            if "ops" not in data:
                raise common.ToolError("invalid_recipe", "ops file object must contain an 'ops' list")
            data = data["ops"]
        if not isinstance(data, list):
            raise common.ToolError("invalid_recipe", "ops file must contain a list of operations")
        ops.extend(data)
    for spec in getattr(args, "op", None) or []:
        ops.append(parse_op_spec(spec))
    if getattr(args, "recipe", None):
        recipes = common.load_asset("asset-processing-recipes.json")
        entry = recipes["recipes"].get(args.recipe)
        if entry is None:
            raise common.ToolError("unknown_recipe", "Unknown recipe %r" % args.recipe,
                                   {"available": sorted(recipes["recipes"].keys())})
        ops.extend(entry["ops"])
    if not ops:
        raise common.ToolError("invalid_argument", "No operations given",
                               hint="Pass --op name:value (repeatable), --ops-json file, or --recipe id.")
    return ops


# --- commands ---------------------------------------------------------------
def cmd_help(args):
    topic = args.topic
    if not topic:
        sys.stdout.write(HELP_TEXT)
        return EXIT_OK
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and topic in action.choices:
            action.choices[topic].print_help()
            return EXIT_OK
    raise common.ToolError("unknown_command", "No help topic named %r" % topic)


def cmd_version(_args):
    assets = sorted(name for name in os.listdir(common.SKILL_ROOT)
                    if name.startswith("asset-") or name.startswith("knowledge-"))
    tools = sorted(name for name in os.listdir(common.SKILL_ROOT) if name.startswith("tools-"))
    payload = {
        "ok": True,
        "suite": common.SUITE_NAME,
        "version": common.VERSION,
        "python": sys.version.split()[0],
        "skill_root": common.SKILL_ROOT,
        "tools": tools,
        "assets_and_knowledge": assets,
        "limits": common.LIMITS,
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    return EXIT_OK


def cmd_capabilities(args):
    registry = module("registry")
    if args.query:
        results = registry.search(args.query, category=args.category, limit=args.limit)
        payload = common.ok("capabilities", query=args.query, count=len(results),
                            matches=results,
                            stats=None if args.query else registry.stats())
        if not results:
            payload["note"] = "No capability matched. Try broader words such as 'mix', 'voice', 'rights'."
        text = "\n".join("- %s  %s (%s)" % (m["id"], m["name"], m["category"]) for m in results)
        return output(args, payload, text=text or "No matches.")
    if args.category:
        designator = registry.get(args.category)
        if designator["type"] == "category":
            skills = designator["skills"]
            payload = common.ok("capabilities", category=designator["category"]["name"],
                                count=len(skills), matches=[
                                    {"id": s["id"], "name": s["name"], "category": s["category_name"],
                                     "description": s["description"], "operations": s["operations"]}
                                    for s in skills[:args.limit]])
            text = "\n".join("%s  %s" % (s["id"], s["name"]) for s in skills[:args.limit])
            return output(args, payload, text=text)
        return cmd_describe(args)
    stats = registry.stats()
    categories = registry.categories()
    payload = common.ok("capabilities", stats=stats, categories=categories)
    lines = ["%s" % stats["catalog_title"], "",
             "%s categories, %s capabilities" % (stats["categories"], stats["capabilities"]), ""]
    for category in categories:
        lines.append("%2d. %s (%s capabilities) -> %s"
                     % (category["index"], category["name"], category["skill_count"], category["slug"]))
    lines.append("")
    lines.append("Search with --query, or list one category with --category <index|slug>.")
    return output(args, payload, text="\n".join(lines))


def cmd_describe(args):
    registry = module("registry")
    playbooks = common.load_asset("asset-category-playbooks.json")["playbooks"]
    designator = registry.resolve(args.capability)
    if designator["type"] == "skill":
        skill = designator["skill"]
        playbook = playbooks[skill["category_slug"]]
        payload = common.ok("describe", type="skill", capability=skill,
                            playbook={"umbrella": playbook["umbrella"], "stages": playbook["stages"],
                                      "controls": playbook["controls"], "checks": playbook["checks"]},
                            match=designator.get("match"))
        lines = ["%s  %s" % (skill["id"], skill["name"]), "",
                 "Category: %s" % skill["category_name"],
                 "Description: %s" % skill["description"],
                 "Toolkit operations: %s" % ", ".join(skill["operations"]), "",
                 "Typical stages:"]
        lines.extend("- %s" % s for s in playbook["stages"])
        lines.append("")
        lines.append("Run 'spec --capability %s' for the full contract sheet." % skill["id"])
        return output(args, payload, text="\n".join(lines))
    category = designator["category"]
    payload = common.ok("describe", type="category", category=category, skills=designator["skills"],
                        playbook=playbooks[category["slug"]])
    lines = ["%s (%s capabilities)" % (category["name"], len(designator["skills"])), ""]
    lines.extend("%s  %s" % (s["id"], s["name"]) for s in designator["skills"])
    return output(args, payload, text="\n".join(lines))


def cmd_route(args):
    planner = module("planner")
    if args.requirements and os.path.isfile(os.path.expanduser(args.requirements)):
        with open(os.path.expanduser(args.requirements), "r", encoding="utf-8") as handle:
            lines = [line.strip(" -*\t") for line in handle if line.strip()]
    else:
        lines = [part.strip() for part in (args.requirements or "").split(";") if part.strip()]
    if not lines:
        raise common.ToolError("invalid_argument", "Provide --requirements with a file or ';' separated text")
    routing = planner.route(lines, limit=args.limit)
    payload = common.ok("route", **routing)
    out = []
    for item in routing["assignments"]:
        out.append("- %s -> %s (%s) [score %s]" % (item["requirement"], item["primary"]["name"],
                                                   item["primary"]["id"], item["score"]))
    for item in routing["unmatched"]:
        out.append("- %s -> no catalog match" % item)
    out.append("")
    out.append("Coverage: %s" % routing["coverage"])
    return output(args, payload, text="\n".join(out))


def cmd_plan(args):
    planner = module("planner")
    data = None
    if args.brief:
        data = common.read_json(args.brief, "brief")
    overrides = {
        "title": args.title,
        "goal": args.goal,
        "audience": args.audience,
        "medium": args.medium,
        "preset": args.preset,
        "duration_seconds": args.duration,
        "deadline": args.deadline,
        "rights_status": args.rights_status,
        "notes": args.notes,
    }
    if args.constraint:
        overrides["constraints"] = args.constraint
    if args.capability:
        overrides["capabilities"] = args.capability
    brief = planner.normalize_brief(data, **overrides)
    plan = planner.build_plan(brief)
    payload = common.ok("plan", **plan)
    text = plan["markdown"]
    if args.out and args.format == "json":
        payload.pop("markdown", None)
    return output(args, payload, text=text)


def cmd_spec(args):
    planner = module("planner")
    spec = planner.build_spec(args.capability, preset_id=args.preset)
    payload = common.ok("spec", **spec)
    return output(args, payload, text=spec["markdown"])


def cmd_inspect(args):
    aio = module("aio")
    meta = aio.inspect(args.input, max_seconds=args.max_seconds)
    payload = common.ok("inspect", **{"file": meta["path"], "structure": meta})
    lines = ["%s" % meta["path"], "",
             "Container: %s  Format: %s  %s bits  %s Hz  %s channel(s)"
             % (meta["container"], meta["sample_format"], meta["bit_depth"], meta["sample_rate"],
                meta["channels"]),
             "Frames: %s  Duration: %s s  Size: %s bytes"
             % (meta["frames"], meta["duration_seconds"], meta["file_size_bytes"]),
             "Structurally valid: %s" % meta["structurally_valid"]]
    if meta["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend("- %s" % w for w in meta["warnings"])
    return output(args, payload, text="\n".join(lines))


def cmd_analyze(args):
    qa = module("qa")
    timer = common.Timer(args.timeout)
    report = qa.analyze(args.input, preset=args.preset, skip_true_peak=args.skip_true_peak,
                        max_seconds=args.max_seconds, timer=timer)
    payload = common.ok("analyze", elapsed_seconds=timer.elapsed, **report)
    text = qa.report_markdown(report)
    code = EXIT_GATE if (args.strict and report["verdict"] == "fail") else EXIT_OK
    return output(args, payload, text=text, exit_code=code)


def cmd_regression(args):
    qa = module("qa")
    timer = common.Timer(args.timeout)
    result = qa.regression(args.reference, args.candidate, timer=timer)
    payload = common.ok("regression", elapsed_seconds=timer.elapsed, **result)
    lines = ["# Regression comparison", "",
             "- Reference: `%s`" % result["reference"]["path"],
             "- Candidate: `%s`" % result["candidate"]["path"],
             "- Verdict: **%s**" % result["verdict"].upper(), "",
             "| Measurement | Delta |", "| --- | --- |"]
    for key, value in result["differences"].items():
        lines.append("| %s | %s |" % (key, value))
    null = result["null_test"]
    if null.get("comparable"):
        lines.append("| null-test residual | %s dB relative to reference |" % null["relative_db"])
        lines.append("| null-test residual level | %s dBFS |" % null["residual_dbfs"])
    else:
        lines.append("| null test | skipped (files differ in rate, channels or length) |")
    if result["findings"]:
        lines.append("")
        lines.append("## Findings")
        for finding in result["findings"]:
            lines.append("- [%s] %s: %s" % (finding["status"], finding["check"], finding["detail"]))
    else:
        lines.append("")
        lines.append("No differences beyond the regression tolerances.")
    code = EXIT_GATE if (args.strict and result["verdict"] == "fail") else EXIT_OK
    return output(args, payload, text="\n".join(lines), exit_code=code)


def _write_result(args, ops, input_path, output_path, timer):
    if args.dry_run:
        dsp = module("dsp")
        chain = dsp.validate_ops(ops)
        return {"dry_run": True, "input": common.resolve_path(input_path), "output": output_path,
                "chain": chain,
                "would_overwrite": os.path.exists(os.path.expanduser(output_path))}
    dsp = module("dsp")
    result = dsp.process_to_file(input_path, output_path, ops,
                                 bit_depth=args.bits, sample_format=args.sample_format,
                                 dither=args.dither, noise_shaping=args.shaping,
                                 max_seconds=args.max_seconds, timer=timer)
    return result


def _guard_output(args, output_path):
    target = os.path.abspath(os.path.expanduser(output_path))
    if os.path.exists(target) and not args.dry_run:
        require_approval(args, "destructive-overwrite", target)
    return target


def cmd_process(args):
    timer = common.Timer(args.timeout)
    ops = collect_ops(args)
    target = _guard_output(args, args.output)
    result = _write_result(args, ops, args.input, args.output, timer)
    payload = common.ok("process", elapsed_seconds=timer.elapsed, **result)
    if result.get("dry_run"):
        lines = ["Dry run: %d operation(s) validated, nothing written" % len(result["chain"])]
        for index, step in enumerate(result["chain"], 1):
            lines.append("  %d. %s %s" % (index, step["op"], step["params"] or ""))
        lines.append("Would overwrite: %s" % result["would_overwrite"])
        return output(args, payload, text="\n".join(lines))
    lines = ["Processed %s -> %s" % (result.get("input"), result.get("output"))]
    for step in result.get("chain", []):
        lines.append("  %d. %-13s peak %s -> %s dBFS  duration %s s"
                     % (step["step"], step["op"], step["before"]["peak_dbfs"],
                        step["after"]["peak_dbfs"], step["after"]["duration_seconds"]))
    return output(args, payload, text="\n".join(lines))


def cmd_recipe(args):
    args.recipe = args.recipe
    return cmd_process(args)


def cmd_batch(args):
    timer = common.Timer(args.timeout)
    source_dir = common.resolve_path(args.input, must_exist=True, kind="dir")
    output_dir = common.ensure_dir(common.resolve_path(args.output))
    pattern = args.pattern or "*.wav"
    files = sorted(glob.glob(os.path.join(source_dir, pattern)))
    if args.recursive:
        files = sorted(glob.glob(os.path.join(source_dir, "**", pattern), recursive=True))
    if not files:
        raise common.ToolError("no_input", "No files matched %r in %s" % (pattern, source_dir))
    if len(files) > (args.max_files or common.LIMITS["max_files_per_batch"]):
        raise common.ToolError("limit_exceeded",
                               "%s files matched, above the batch limit" % len(files),
                               {"limit": args.max_files or common.LIMITS["max_files_per_batch"]})
    ops = collect_ops(args)
    dsp = module("dsp")
    dsp.validate_ops(ops)
    planned = []
    for path in files:
        relative = os.path.relpath(path, source_dir)
        target = os.path.join(output_dir, relative)
        planned.append((path, target, os.path.exists(target)))
    overwrites = [target for _, target, exists in planned if exists]
    if not args.dry_run and (overwrites or len(planned) > 10):
        action = "bulk-batch" if len(planned) > 10 else "destructive-overwrite"
        if not args.approval:
            raise common.ToolError(
                "approval_required",
                ("%d output file(s) already exist in %s" % (len(overwrites), output_dir))
                if overwrites else
                ("A batch of %d files needs a bulk-batch approval" % len(planned)),
                {"action": action, "output_directory": output_dir, "examples": overwrites[:5]},
                hint=("Run 'approvals request --action %s --target %s', then grant it and pass "
                      "--approval <id>. Use --dry-run to preview without writing." % (action, output_dir)))
        require_approval(args, action, output_dir)
    results = []
    failures = 0
    for path, target, _exists in planned:
        try:
            common.ensure_dir(os.path.dirname(target) or ".")
            if args.dry_run:
                results.append({"input": path, "output": target, "status": "dry-run"})
                continue
            outcome = dsp.process_to_file(path, target, ops, bit_depth=args.bits,
                                          sample_format=args.sample_format, dither=args.dither,
                                          noise_shaping=args.shaping, max_seconds=args.max_seconds,
                                          timer=timer)
            results.append({"input": path, "output": target, "status": "ok",
                            "output_meta": outcome["output_meta"]})
        except common.ToolError as exc:
            failures += 1
            results.append({"input": path, "output": target, "status": "failed",
                            "error": exc.as_dict()["error"]})
            if not args.continue_on_error:
                break
    payload = common.ok("batch", elapsed_seconds=timer.elapsed, recipe=args.recipe,
                        ops=args.recipe and args.recipe or "inline", file_count=len(planned),
                        processed=len([r for r in results if r["status"] in ("ok", "dry-run")]),
                        failures=failures, results=results)
    lines = ["Batch: %d file(s), %d failure(s)" % (len(planned), failures)]
    for item in results:
        lines.append("  [%s] %s -> %s" % (item["status"], os.path.basename(item["input"]),
                                          item["output"]))
    return output(args, payload, text="\n".join(lines),
                  exit_code=EXIT_ERROR if failures else EXIT_OK)


def cmd_concat(args):
    aio = module("aio")
    timer = common.Timer(args.timeout)
    if len(args.input) < 2:
        raise common.ToolError("invalid_argument", "concat needs at least two --input files")
    buffers = []
    metas = []
    for path in args.input:
        buffer, meta = aio.read_wav(path, max_seconds=args.max_seconds)
        buffers.append(buffer)
        metas.append(meta)
    joined, info = aio.concatenate(buffers, crossfade_seconds=args.crossfade,
                                   sample_rate=args.sample_rate)
    timer.check("concatenate")
    target = _guard_output(args, args.output)
    write_info = aio.write_wav(target, joined, bit_depth=args.bits,
                               sample_format=args.sample_format, dither=args.dither,
                               noise_shaping=args.shaping)
    payload = common.ok("concat", elapsed_seconds=timer.elapsed, output=write_info,
                        join=info, inputs=[m["path"] for m in metas])
    text = ("Joined %d files into %s (%s s, crossfade %s s)"
            % (len(buffers), write_info["path"], round(joined.duration, 3), args.crossfade))
    return output(args, payload, text=text)


def cmd_split(args):
    aio = module("aio")
    qa = module("qa")
    timer = common.Timer(args.timeout)
    buffer, meta = aio.read_wav(args.input, max_seconds=args.max_seconds)
    out_dir = common.ensure_dir(common.resolve_path(args.outdir))
    cuts = []
    if args.at:
        for token in args.at.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = float(token)
            except ValueError:
                raise common.ToolError("invalid_argument", "Cut time %r is not a number" % token)
            if value <= 0 or value >= buffer.duration:
                raise common.ToolError("invalid_argument",
                                       "Cut time %s is outside the file (0 - %.3f s)"
                                       % (value, buffer.duration))
            cuts.append(value)
    elif args.silence:
        silence = qa.detect_silence(buffer.channels, buffer.sample_rate,
                                    threshold_dbfs=args.threshold_db, frame_ms=50)
        if not silence["silent_frames"]:
            raise common.ToolError("nothing_to_split",
                                   "No silence was detected at or below %s dBFS" % args.threshold_db,
                                   hint="Lower --threshold-db, or split explicitly with --at.")
        frame_seconds = 0.05
        merged = buffer.channels[0] if buffer.channel_count == 1 else None
        if merged is None:
            import array
            merged = array.array("d", bytes(8 * buffer.frames))
            for ch in buffer.channels:
                for i in range(buffer.frames):
                    merged[i] += ch[i] / buffer.channel_count
        threshold = common.db_to_lin(args.threshold_db)
        frame = max(1, int(buffer.sample_rate * frame_seconds))
        position = 0
        inside = False
        start = 0
        while position + frame <= buffer.frames:
            total = 0.0
            for i in range(position, position + frame):
                total += merged[i] * merged[i]
            silent = (total / frame) ** 0.5 < threshold
            if silent and not inside:
                inside = True
                start = position
            elif not silent and inside:
                inside = False
                length = (position - start) / float(buffer.sample_rate)
                if length >= args.silence:
                    cut = (start + position) / 2.0 / buffer.sample_rate
                    if 0 < cut < buffer.duration:
                        cuts.append(round(cut, 3))
            position += frame
    else:
        raise common.ToolError("invalid_argument", "split needs --at times or --silence")
    if args.silence:
        # drop cuts that would produce slivers or that sit in lead-in/lead-out silence
        minimum = max(0.1, float(args.min_segment))
        cuts = [cut for cut in cuts if cut >= minimum and (buffer.duration - cut) >= minimum]
        filtered = []
        for cut in sorted(set(cuts)):
            if not filtered or (cut - filtered[-1]) >= 0.1:
                filtered.append(cut)
        cuts = filtered
    cuts = sorted(set(cuts))
    boundaries = [0.0] + cuts + [buffer.duration]
    pieces = []
    for index in range(len(boundaries) - 1):
        start_seconds = boundaries[index]
        end_seconds = boundaries[index + 1]
        if end_seconds - start_seconds < 0.01:
            continue
        piece = buffer.slice(int(start_seconds * buffer.sample_rate),
                             int(end_seconds * buffer.sample_rate))
        guard = int(0.003 * buffer.sample_rate)
        for ch in piece.channels:
            for i in range(min(guard, len(ch))):
                ch[i] *= (i + 0.5) / guard
            for i in range(min(guard, len(ch))):
                ch[len(ch) - 1 - i] *= (i + 0.5) / guard
        name = "%s-%03d-%0.3f-%0.3f.wav" % (os.path.splitext(os.path.basename(meta["path"]))[0],
                                            index + 1, start_seconds, end_seconds)
        target = os.path.join(out_dir, name)
        if os.path.exists(target) and not args.dry_run:
            require_approval(args, "destructive-overwrite", target)
        if not args.dry_run:
            aio.write_wav(target, piece, bit_depth=args.bits, sample_format=args.sample_format,
                          dither=args.dither)
        pieces.append({"file": target, "start_seconds": common.r2(start_seconds, 3),
                       "end_seconds": common.r2(end_seconds, 3),
                       "duration_seconds": common.r2(piece.duration, 3)})
    if not pieces:
        raise common.ToolError("nothing_to_split", "The requested cuts produced no segments")
    payload = common.ok("split", elapsed_seconds=timer.elapsed, input=meta["path"],
                        outdir=out_dir, cuts=cuts, segments=pieces, dry_run=bool(args.dry_run))
    lines = ["Split %s into %d segment(s) in %s" % (meta["path"], len(pieces), out_dir)]
    for piece in pieces:
        lines.append("  %s  [%s - %s s]" % (piece["file"], piece["start_seconds"], piece["end_seconds"]))
    return output(args, payload, text="\n".join(lines))


def cmd_manifest(args):
    delivery = module("delivery")
    timer = common.Timer(args.timeout)
    sign_off = None
    if args.sign_off_name:
        approval = require_approval(args, "publish-deliverable", os.path.abspath(args.input))
        sign_off = {"approved_by": args.sign_off_name, "approval_id": approval["entry"]["id"],
                    "rights_status": args.rights_status}
    root_path = common.resolve_path(args.input, must_exist=True, kind="dir")
    out_path = common.resolve_path(args.out or os.path.join(root_path, "manifest.json"))
    excluded = [out_path]
    if args.report:
        excluded.append(common.resolve_path(args.report))
    manifest = delivery.build_manifest(args.input, preset=args.preset, notes=args.notes,
                                       analyze_audio=not args.no_analyze,
                                       max_files=args.max_files, sign_off=sign_off,
                                       exclude=excluded)
    manifest.pop("_root_path", None)
    common.write_json(out_path, manifest)
    sys.stderr.write("Wrote manifest %s\n" % out_path)
    payload = common.ok("manifest", elapsed_seconds=timer.elapsed, manifest_path=out_path, **manifest)
    text = delivery.manifest_markdown(manifest)
    text += "\nManifest written to `%s`.\n" % out_path
    if args.report:
        common.write_text(args.report, text)
        sys.stderr.write("Wrote report %s\n" % common.resolve_path(args.report))
    worst = "pass"
    for check in manifest["checks"]:
        if check["status"] == "fail":
            worst = "fail"
        elif check["status"] == "warn" and worst == "pass":
            worst = "warn"
    code = EXIT_GATE if (args.strict and worst == "fail") else EXIT_OK
    view = argparse.Namespace(**vars(args))
    view.out = None  # --out already holds the manifest itself; never overwrite it with a report
    return output(view, payload, text=text, exit_code=code)


def cmd_validate_manifest(args):
    delivery = module("delivery")
    result = delivery.validate_manifest(args.manifest, args.schema)
    payload = common.ok("validate-manifest", **result)
    text = ("Manifest %s is valid against the packaged schema (%d file entries)."
            % (result["manifest"], result["file_count"])) if result["valid"] else \
           ("Manifest %s has %d schema error(s):\n%s"
            % (result["manifest"], result["error_count"],
               "\n".join("- %s: %s" % (e["path"], e["error"]) for e in result["errors"])))
    return output(args, payload, text=text, exit_code=EXIT_OK if result["valid"] else EXIT_INVALID)


def cmd_verify_manifest(args):
    delivery = module("delivery")
    result = delivery.verify_manifest(args.manifest, root=args.root)
    payload = common.ok("verify-manifest", **result)
    if result["verified"]:
        text = "Verified %d file(s) against %s: all checksums match." % (result["checked"], result["root"])
    else:
        text = ("Verification failed: %d missing, %d mismatched\n- missing: %s\n- mismatched: %s"
                % (len(result["missing"]), len(result["mismatched"]),
                   ", ".join(result["missing"]) or "none",
                   ", ".join(result["mismatched"]) or "none"))
    return output(args, payload, text=text, exit_code=EXIT_OK if result["verified"] else EXIT_INVALID)


def _approvals_markdown(action, result):
    """Human-readable approval output; the JSON envelope stays available via --format json."""
    lines = ["# Approval ledger: %s" % action, ""]
    entries = result.get("entries")
    if entries is None and result.get("entry"):
        entries = [result["entry"]]
    if entries is not None:
        counts = result.get("counts")
        if counts:
            lines.append("Counts: " + ", ".join("%s=%s" % (k, v) for k, v in sorted(counts.items())))
            lines.append("")
        lines.append("| Id | Action | Target | Status | Approver | Expires |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for entry in entries:
            lines.append("| %s | %s | `%s` | %s | %s | %s |"
                         % (entry.get("id"), entry.get("action"), entry.get("target"),
                            entry.get("status"), entry.get("approver") or "-",
                            entry.get("expires_at") or "-"))
    lines.append("")
    lines.append("Ledger: `%s`%s" % (result.get("ledger", "?"),
                                    "" if result.get("exists", True) else " (not created yet)"))
    if result.get("next_step"):
        lines.append("")
        lines.append("Next: `%s`" % result["next_step"])
    if result.get("consumed"):
        lines.append("")
        lines.append("This approval has been consumed and cannot be reused.")
    return "\n".join(lines)


def cmd_approvals(args):
    approvals = module("approvals")
    action = args.action_name
    if action == "request":
        result = approvals.request(args.risk_action or args.target_action, args.target,
                                   requested_by=args.requested_by or "", note=args.note or "",
                                   path=args.ledger)
    elif action == "grant":
        result = approvals.grant(args.id, args.by, note=args.note or "", path=args.ledger)
    elif action == "revoke":
        result = approvals.revoke(args.id, note=args.note or "", path=args.ledger)
    elif action == "list":
        result = approvals.listing(path=args.ledger, status=args.status, action=None,
                                   limit=args.limit)
    elif action == "verify":
        result = approvals.verify(args.id, args.risk_action or args.target_action, args.target,
                                  path=args.ledger, consume=args.consume)
    else:  # pragma: no cover - argparse enforces choices
        raise common.ToolError("unknown_command", "Unknown approvals action %r" % action)
    payload = common.ok("approvals", action=action, **result)
    text = _approvals_markdown(action, result)
    return output(args, payload, text=text)


def cmd_selftest(args):
    """Real, reproducible checks of the packaged tool surface."""
    registry = module("registry")
    aio = module("aio")
    qa = module("qa")
    dsp = module("dsp")
    planner = module("planner")
    delivery = module("delivery")
    approvals = module("approvals")
    results = []

    def record(name, passed, detail, measured=None):
        results.append({"check": name, "status": "pass" if passed else "fail",
                        "detail": detail, "measured": measured})

    workdir = args.workdir or os.path.join(common.SKILL_ROOT, ".aps-selftest")
    if os.path.isdir(workdir) and args.clean:
        import shutil
        for name in os.listdir(workdir):
            path = os.path.join(workdir, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
    common.ensure_dir(workdir)

    # 1. catalog integrity
    try:
        stats = registry.stats()
        ok_catalog = stats["categories"] == 28 and stats["capabilities"] == 280 and stats["playbooks"] == 28
        record("catalog.integrity", ok_catalog,
               "28 categories, 280 capabilities, 28 playbooks", stats)
    except common.ToolError as exc:
        record("catalog.integrity", False, exc.message)

    # 2. EBU Tech 3341 style loudness calibration on a stereo 1 kHz sine at -23 dBFS
    calibration_file = os.path.join(workdir, "calibration-48k.wav")
    signal = aio.generate_test_signal("sine", 1000.0, -23.0, seconds=4.0, sample_rate=48000, channels=2)
    aio.write_wav(calibration_file, signal, bit_depth=24)
    report = qa.analyze(calibration_file, preset="broadcast-r128")
    measured = report["metrics"]["loudness"]["integrated_lufs"]
    record("qa.loudness_calibration_48k", abs((measured or -99) + 23.0) <= 0.5,
           "stereo 1 kHz sine at -23 dBFS should read -23.0 LUFS (+/- 0.5)", {"lufs": measured})

    # 3. same signal at 44.1 kHz to prove the K-weighting is rate aware
    calibration_44 = os.path.join(workdir, "calibration-44k.wav")
    signal_44 = aio.generate_test_signal("sine", 1000.0, -23.0, seconds=4.0, sample_rate=44100, channels=2)
    aio.write_wav(calibration_44, signal_44, bit_depth=24)
    report_44 = qa.analyze(calibration_44, preset="broadcast-r128")
    measured_44 = report_44["metrics"]["loudness"]["integrated_lufs"]
    record("qa.loudness_calibration_44k", abs((measured_44 or -99) + 23.0) <= 0.5,
           "same signal at 44.1 kHz should also read -23.0 LUFS (+/- 0.5)", {"lufs": measured_44})

    # 4. true peak sanity: -6 dBFS sine should measure about -6 dBTP
    tp_signal = aio.generate_test_signal("sine", 997.0, -6.0, seconds=1.0, sample_rate=48000, channels=1)
    tp = qa.true_peak(tp_signal.channels[0], tp_signal.sample_rate)
    tp_db = common.lin_to_db(tp)
    record("qa.true_peak_sanity", abs(tp_db + 6.0) <= 0.5,
           "-6 dBFS 997 Hz sine should measure about -6 dBTP (+/- 0.5)", {"dbtp": common.r2(tp_db)})

    # 5. processing gain is measurable and exact
    gain_file = os.path.join(workdir, "gain-minus6.wav")
    dsp.process_to_file(calibration_file, gain_file, [{"op": "gain", "db": -6.0}], bit_depth=24)
    gained, _ = aio.read_wav(gain_file)
    peak_before = common.lin_to_db(signal.peak())
    peak_after = common.lin_to_db(gained.peak())
    record("dsp.gain", abs((peak_after - peak_before) + 6.0) <= 0.05,
           "-6 dB gain should lower the peak by 6.00 dB (+/- 0.05)",
           {"before_dbfs": common.r2(peak_before), "after_dbfs": common.r2(peak_after)})

    # 6. normalization lands on target and the limiter holds the ceiling
    norm_file = os.path.join(workdir, "normalized.wav")
    dsp.process_to_file(calibration_file, norm_file,
                        [{"op": "normalize", "target_lufs": -14.0},
                         {"op": "limit", "ceiling_dbtp": -1.0}], bit_depth=24)
    norm_report = qa.analyze(norm_file, preset="streaming-music")
    norm_lufs = norm_report["metrics"]["loudness"]["integrated_lufs"]
    norm_tp = norm_report["metrics"]["level"]["true_peak_dbtp"]
    record("dsp.normalize_and_limit", abs((norm_lufs or -99) + 14.0) <= 0.6 and (norm_tp or 0) <= -1.0,
           "normalize to -14 LUFS (+/- 0.6) and hold true peak at or below -1.0 dBTP",
           {"lufs": norm_lufs, "dbtp": norm_tp})

    # 7. silence trim shortens the file as intended
    padded = aio.generate_test_signal("sine", 440.0, -20.0, seconds=1.0, sample_rate=48000, channels=1)
    import array as _array
    silence = _array.array("d", bytes(8 * int(0.5 * 48000)))
    padded.channels[0] = silence + padded.channels[0] + silence
    padded_file = os.path.join(workdir, "padded.wav")
    aio.write_wav(padded_file, padded, bit_depth=24)
    trimmed_file = os.path.join(workdir, "trimmed.wav")
    dsp.process_to_file(padded_file, trimmed_file,
                        [{"op": "trim-silence", "threshold_db": -50, "pad_sec": 0.1}], bit_depth=24)
    trimmed, trimmed_meta = aio.read_wav(trimmed_file)
    record("dsp.trim_silence", 1.0 < trimmed.duration < 1.6,
           "2.0 s padded signal should trim to about 1.2 s",
           {"duration_seconds": common.r2(trimmed.duration)})

    # 8. resampling preserves duration within a millisecond
    resampled_file = os.path.join(workdir, "resampled-44k.wav")
    dsp.process_to_file(calibration_file, resampled_file,
                        [{"op": "resample", "rate": 44100, "quality": "low"}], bit_depth=24)
    resampled, resampled_meta = aio.read_wav(resampled_file)
    record("dsp.resample", resampled_meta["sample_rate"] == 44100 and
           abs(resampled.duration - signal.duration) < 0.002,
           "48 kHz to 44.1 kHz keeps duration within 2 ms and reports the new rate",
           {"sample_rate": resampled_meta["sample_rate"],
            "duration_seconds": common.r2(resampled.duration, 4)})

    # 9. limiter holds the ceiling on a hot signal and stays transparent below it
    hot = aio.generate_test_signal("sine", 1000.0, -0.2, seconds=1.0, sample_rate=48000, channels=2)
    hot_file = os.path.join(workdir, "hot.wav")
    aio.write_wav(hot_file, hot, bit_depth=24)
    limited_file = os.path.join(workdir, "hot-limited.wav")
    dsp.process_to_file(hot_file, limited_file, [{"op": "limit", "ceiling_dbtp": -1.0}], bit_depth=24)
    limited, _ = aio.read_wav(limited_file)
    limited_tp = common.lin_to_db(max(qa.true_peak(ch, 48000) for ch in limited.channels))
    record("dsp.limiter_ceiling", -1.05 <= limited_tp <= -0.95,
           "A -0.2 dBFS sine limited at -1.0 dBTP should land on the ceiling",
           {"input_peak_dbfs": -0.2, "limited_dbtp": common.r2(limited_tp)})

    quiet = aio.generate_test_signal("sine", 1000.0, -30.0, seconds=1.0, sample_rate=48000, channels=2)
    quiet_file = os.path.join(workdir, "quiet.wav")
    aio.write_wav(quiet_file, quiet, bit_depth=24)
    quiet_limited_file = os.path.join(workdir, "quiet-limited.wav")
    dsp.process_to_file(quiet_file, quiet_limited_file, [{"op": "limit", "ceiling_dbtp": -1.0}],
                        bit_depth=24)
    quiet_limited, _ = aio.read_wav(quiet_limited_file)
    quiet_source, _ = aio.read_wav(quiet_file)
    largest_delta = max(abs(quiet_source.channels[0][i] - quiet_limited.channels[0][i])
                        for i in range(0, quiet_source.frames, 7))
    record("dsp.limiter_transparency", largest_delta == 0.0,
           "Material below the ceiling passes through sample-identical (zero latency, no time shift)",
           {"max_sample_delta": largest_delta})

    # 10. float32 round-trip keeps the signal intact
    float_file = os.path.join(workdir, "float32.wav")
    aio.write_wav(float_file, signal, bit_depth=32, sample_format="float")
    float_back, float_meta = aio.read_wav(float_file)
    float_error = max(abs(float_back.channels[0][i] - signal.channels[0][i])
                      for i in range(0, signal.frames, 11))
    record("io.float32_roundtrip", float_meta["sample_format"] == "float32" and float_error < 1e-6,
           "32-bit float WAV round-trips with no measurable loss",
           {"max_error": float_error, "sample_format": float_meta["sample_format"]})

    # 11. invalid input is rejected with a structured error
    try:
        aio.read_wav(os.path.join(workdir, "does-not-exist.wav"))
        record("errors.missing_file", False, "Reading a missing file should raise a ToolError")
    except common.ToolError as exc:
        record("errors.missing_file", exc.code == "not_found",
               "Missing file raises not_found", {"code": exc.code})

    try:
        aio.generate_test_signal("not-a-signal")
        record("errors.bad_argument", False, "An unknown signal type should raise")
    except common.ToolError as exc:
        record("errors.bad_argument", exc.code == "invalid_argument",
               "Bad argument raises invalid_argument", {"code": exc.code})

    # 12. corrupted file is rejected before processing
    corrupt = os.path.join(workdir, "corrupt.wav")
    with open(calibration_file, "rb") as handle:
        raw = handle.read()
    with open(corrupt, "wb") as handle:
        handle.write(raw[:len(raw) // 2])
    try:
        aio.read_wav(corrupt)
        record("errors.truncated", False, "A truncated WAV should be rejected")
    except common.ToolError as exc:
        record("errors.truncated", exc.code in ("corrupt_audio", "invalid_audio"),
               "Truncated WAV rejected before decoding", {"code": exc.code})

    # 13. plan generation fills every template placeholder
    brief = planner.normalize_brief({"title": "Selftest plan", "goal": "master a stereo single for streaming",
                                     "medium": "music", "rights_status": "cleared"})
    plan = planner.build_plan(brief)
    record("planner.plan", "{{" not in plan["markdown"] and plan["capability_count"] >= 3,
           "Plan renders with no leftover placeholders and routed capabilities",
           {"capabilities": plan["capability_count"], "preset": plan["preset"]["id"]})

    # 14. capability routing resolves requirement lines
    routing = planner.route(["master a podcast episode to -16 LUFS", "design a creature voice"])
    record("planner.route", len(routing["assignments"]) == 2,
           "Two requirement lines resolve to capabilities",
           {"coverage": routing["coverage"]})

    # 15. manifest build, schema validation, and checksum verification
    delivery_dir = os.path.join(workdir, "delivery")
    import shutil
    if os.path.isdir(delivery_dir):
        shutil.rmtree(delivery_dir, ignore_errors=True)
    common.ensure_dir(delivery_dir)
    aio.write_wav(os.path.join(delivery_dir, "track-master.wav"), signal, bit_depth=24)
    with open(os.path.join(delivery_dir, "notes.txt"), "w", encoding="utf-8") as handle:
        handle.write("Selftest delivery package\n")
    manifest_path = os.path.join(delivery_dir, "manifest.json")
    manifest = delivery.build_manifest(delivery_dir, preset="broadcast-r128", analyze_audio=False,
                                       exclude=[manifest_path])
    manifest.pop("_root_path", None)
    common.write_json(manifest_path, manifest)
    validation = delivery.validate_manifest(manifest_path)
    verification = delivery.verify_manifest(manifest_path, root=delivery_dir)
    record("delivery.manifest_roundtrip", validation["valid"] and verification["verified"],
           "Manifest validates against the packaged schema and checksums re-verify",
           {"files": manifest["totals"]["file_count"], "schema_errors": validation["error_count"]})

    # 16. tampering is detected
    with open(os.path.join(delivery_dir, "notes.txt"), "a", encoding="utf-8") as handle:
        handle.write("edit\n")
    tampered = delivery.verify_manifest(manifest_path, root=delivery_dir)
    record("delivery.tamper_detection", not tampered["verified"] and tampered["mismatched"],
           "Appending to a delivered file is reported as a checksum mismatch",
           {"mismatched": tampered["mismatched"]})

    # 17. approval ledger lifecycle
    ledger = os.path.join(workdir, "approvals.json")
    requested = approvals.request("destructive-overwrite", os.path.join(workdir, "target.wav"),
                                 requested_by="selftest", path=ledger)
    approval_id = requested["entry"]["id"]
    pending_blocked = False
    try:
        approvals.verify(approval_id, "destructive-overwrite", os.path.join(workdir, "target.wav"),
                         path=ledger)
    except common.ToolError as exc:
        pending_blocked = exc.code == "approval_not_granted"
    approvals.grant(approval_id, "Selftest Approver", path=ledger)
    approved = approvals.verify(approval_id, "destructive-overwrite",
                                os.path.join(workdir, "target.wav"), path=ledger, consume=True)
    reuse_blocked = False
    try:
        approvals.verify(approval_id, "destructive-overwrite", os.path.join(workdir, "target.wav"),
                         path=ledger)
    except common.ToolError as exc:
        reuse_blocked = exc.code == "approval_consumed"
    record("approvals.lifecycle", pending_blocked and approved["consumed"] and reuse_blocked,
           "Pending is blocked, granted is accepted once, reuse is refused")

    # 18. regression detects a level change and passes on an identical file
    same = qa.regression(calibration_file, calibration_file)
    changed = qa.regression(calibration_file, gain_file)
    record("qa.regression", same["verdict"] == "pass" and
           any(f["check"] == "loudness" for f in changed["findings"]),
           "Identical files pass; a -6 dB version fails the loudness comparison",
           {"same": same["verdict"], "changed": changed["verdict"]})

    passed = len([r for r in results if r["status"] == "pass"])
    failed = [r for r in results if r["status"] == "fail"]
    payload = common.ok("selftest", checks=len(results), passed=passed, failed=len(failed),
                        workdir=workdir, results=results,
                        verdict="pass" if not failed else "fail")
    lines = ["Selftest: %d checks, %d passed, %d failed" % (len(results), passed, len(failed)), ""]
    for item in results:
        lines.append("[%s] %-28s %s" % (item["status"].upper(), item["check"], item["detail"]))
    lines.append("")
    lines.append("Artifacts: %s" % workdir)
    code = EXIT_OK if not failed else EXIT_ERROR
    if args.strict and failed:
        code = EXIT_GATE
    return output(args, payload, text="\n".join(lines), exit_code=code)


# --- parser -----------------------------------------------------------------
def _global_parent():
    """Options accepted both before and after the command name."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--ledger", default=argparse.SUPPRESS, help="approval ledger path")
    parent.add_argument("--approval", default=argparse.SUPPRESS, help="approval id for gated actions")
    parent.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="per-command time budget in seconds")
    parent.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output (default)")
    return parent


def build_parser():
    parser = argparse.ArgumentParser(prog="tools-audio-suite.py", add_help=False,
                                     description="Audio Production Suite skill tool")
    parser.add_argument("--json", action="store_true", default=True, help="machine-readable output")
    parser.add_argument("--ledger", default=None, help="approval ledger path")
    parser.add_argument("--approval", default=None, help="approval id for gated actions")
    parser.add_argument("--timeout", type=float, default=common.LIMITS["timeout_seconds"])
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("-h", "--help", action="store_true", dest="wants_help")
    sub = parser.add_subparsers(dest="command")
    parent = _global_parent()

    def subparser(name, **kwargs):
        kwargs.setdefault("parents", [parent])
        return sub.add_parser(name, **kwargs)

    def add_common(node, markdown=True):
        node.add_argument("--out", default=None)
        if markdown:
            node.add_argument("--format", default="json", choices=["json", "markdown", "both"])
        return node

    help_parser = subparser("help", help="show help")
    help_parser.add_argument("topic", nargs="?", default=None)

    subparser("version", help="version and inventory")

    caps = add_common(subparser("capabilities", help="list or search capabilities"))
    caps.add_argument("--query", default=None)
    caps.add_argument("--category", default=None)
    caps.add_argument("--limit", type=int, default=10)

    describe = add_common(subparser("describe", help="one capability record"))
    describe.add_argument("--capability", required=True)

    route = add_common(subparser("route", help="map requirements to capabilities"))
    route.add_argument("--requirements", required=True)
    route.add_argument("--limit", type=int, default=3)

    plan = add_common(subparser("plan", help="build a production plan"))
    plan.add_argument("--brief", default=None)
    plan.add_argument("--title", default=None)
    plan.add_argument("--goal", default=None)
    plan.add_argument("--audience", default=None)
    plan.add_argument("--medium", default="music")
    plan.add_argument("--preset", default=None)
    plan.add_argument("--duration", type=float, default=None)
    plan.add_argument("--deadline", default=None)
    plan.add_argument("--rights-status", dest="rights_status", default="unknown")
    plan.add_argument("--constraint", action="append", default=None)
    plan.add_argument("--capability", action="append", default=None)
    plan.add_argument("--notes", default=None)

    spec = add_common(subparser("spec", help="capability contract sheet"))
    spec.add_argument("--capability", required=True)
    spec.add_argument("--preset", default="streaming-music")

    inspect = add_common(subparser("inspect", help="structural WAV validation"))
    inspect.add_argument("--input", required=True)
    inspect.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)

    analyze = add_common(subparser("analyze", help="measure and gate audio"))
    analyze.add_argument("--input", required=True)
    analyze.add_argument("--preset", default="streaming-music")
    analyze.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)
    analyze.add_argument("--skip-true-peak", dest="skip_true_peak", action="store_true")
    analyze.add_argument("--strict", action="store_true")

    regression = add_common(subparser("regression", help="compare a candidate render to a reference"))
    regression.add_argument("--reference", required=True)
    regression.add_argument("--candidate", required=True)
    regression.add_argument("--strict", action="store_true")

    for name, help_text in (("process", "apply an operation chain"),
                            ("recipe", "apply a packaged recipe")):
        node = add_common(subparser(name, help=help_text))
        node.add_argument("--input", required=True)
        node.add_argument("--output", required=True)
        node.add_argument("--op", action="append", default=None)
        node.add_argument("--ops-json", dest="ops_json", default=None)
        if name == "recipe":
            node.add_argument("--recipe", required=True)
        else:
            node.add_argument("--recipe", default=None)
        node.add_argument("--bits", type=int, default=24)
        node.add_argument("--sample-format", dest="sample_format", default="pcm", choices=["pcm", "float"])
        node.add_argument("--dither", action="store_true")
        node.add_argument("--shaping", type=int, default=0, choices=[0, 1, 2])
        node.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)
        node.add_argument("--dry-run", dest="dry_run", action="store_true")

    batch = add_common(subparser("batch", help="apply a recipe across a folder"))
    batch.add_argument("--input", required=True)
    batch.add_argument("--output", required=True)
    batch.add_argument("--recipe", required=True)
    batch.add_argument("--pattern", default="*.wav")
    batch.add_argument("--recursive", action="store_true")
    batch.add_argument("--max-files", dest="max_files", type=int, default=None)
    batch.add_argument("--bits", type=int, default=24)
    batch.add_argument("--sample-format", dest="sample_format", default="pcm", choices=["pcm", "float"])
    batch.add_argument("--dither", action="store_true")
    batch.add_argument("--shaping", type=int, default=0, choices=[0, 1, 2])
    batch.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)
    batch.add_argument("--op", action="append", default=None)
    batch.add_argument("--ops-json", dest="ops_json", default=None)
    batch.add_argument("--continue-on-error", dest="continue_on_error", action="store_true")
    batch.add_argument("--dry-run", dest="dry_run", action="store_true")

    concat = add_common(subparser("concat", help="join files with a crossfade"))
    concat.add_argument("--input", action="append", required=True)
    concat.add_argument("--output", required=True)
    concat.add_argument("--crossfade", type=float, default=0.05)
    concat.add_argument("--sample-rate", dest="sample_rate", type=int, default=None)
    concat.add_argument("--bits", type=int, default=24)
    concat.add_argument("--sample-format", dest="sample_format", default="pcm", choices=["pcm", "float"])
    concat.add_argument("--dither", action="store_true")
    concat.add_argument("--shaping", type=int, default=0, choices=[0, 1, 2])
    concat.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)
    concat.add_argument("--dry-run", dest="dry_run", action="store_true")

    split = add_common(subparser("split", help="cut a file at times or silence"))
    split.add_argument("--input", required=True)
    split.add_argument("--outdir", required=True)
    split.add_argument("--at", default=None)
    split.add_argument("--silence", type=float, default=None,
                       help="minimum silence length in seconds to cut on")
    split.add_argument("--threshold-db", dest="threshold_db", type=float, default=-45.0)
    split.add_argument("--min-segment", dest="min_segment", type=float, default=1.0,
                       help="shortest segment to keep when splitting on silence")
    split.add_argument("--bits", type=int, default=24)
    split.add_argument("--sample-format", dest="sample_format", default="pcm", choices=["pcm", "float"])
    split.add_argument("--dither", action="store_true")
    split.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)
    split.add_argument("--dry-run", dest="dry_run", action="store_true")

    manifest = add_common(subparser("manifest", help="build a delivery manifest"))
    manifest.add_argument("--input", required=True)
    manifest.add_argument("--preset", default="streaming-music")
    manifest.add_argument("--notes", default="")
    manifest.add_argument("--no-analyze", dest="no_analyze", action="store_true")
    manifest.add_argument("--max-files", dest="max_files", type=int, default=None)
    manifest.add_argument("--sign-off-name", dest="sign_off_name", default=None)
    manifest.add_argument("--rights-status", dest="rights_status", default="unknown")
    manifest.add_argument("--report", default=None, help="also write the markdown summary here")
    manifest.add_argument("--strict", action="store_true")

    validate = add_common(subparser("validate-manifest", help="schema validation"))
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--schema", default=None)

    verify = add_common(subparser("verify-manifest", help="re-verify checksums"))
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--root", default=None)

    approvals_parser = add_common(subparser("approvals", help="approval ledger"))
    approvals_parser.add_argument("action_name", choices=["request", "grant", "revoke", "list", "verify"])
    approvals_parser.add_argument("--action", dest="risk_action", default=None,
                                  help="risk action id, e.g. destructive-overwrite")
    approvals_parser.add_argument("--target", dest="target", default=None)
    approvals_parser.add_argument("--target-action", dest="target_action", default=None)
    approvals_parser.add_argument("--id", default=None)
    approvals_parser.add_argument("--by", default=None)
    approvals_parser.add_argument("--requested-by", dest="requested_by", default=None)
    approvals_parser.add_argument("--note", default=None)
    approvals_parser.add_argument("--status", default=None)
    approvals_parser.add_argument("--limit", type=int, default=50)
    approvals_parser.add_argument("--consume", action="store_true")

    selftest = add_common(subparser("selftest", help="run built-in verification"))
    selftest.add_argument("--workdir", default=None)
    selftest.add_argument("--clean", action="store_true")
    selftest.add_argument("--strict", action="store_true")
    return parser


COMMANDS = {
    "help": cmd_help,
    "version": cmd_version,
    "capabilities": cmd_capabilities,
    "describe": cmd_describe,
    "route": cmd_route,
    "plan": cmd_plan,
    "spec": cmd_spec,
    "inspect": cmd_inspect,
    "analyze": cmd_analyze,
    "regression": cmd_regression,
    "process": cmd_process,
    "recipe": cmd_recipe,
    "batch": cmd_batch,
    "concat": cmd_concat,
    "split": cmd_split,
    "manifest": cmd_manifest,
    "validate-manifest": cmd_validate_manifest,
    "verify-manifest": cmd_verify_manifest,
    "approvals": cmd_approvals,
    "selftest": cmd_selftest,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_INVALID
    if getattr(args, "wants_help", False) and not args.command:
        sys.stdout.write(HELP_TEXT)
        return EXIT_OK
    if not args.command:
        sys.stdout.write(HELP_TEXT)
        return EXIT_OK
    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse guards this
        sys.stderr.write("Unknown command %r\n" % args.command)
        return EXIT_INVALID
    try:
        return handler(args)
    except common.ToolError as exc:
        return fail(exc, args)
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted; no partial output was left behind.\n")
        return EXIT_ERROR
    except Exception as exc:  # last resort: never leak a raw traceback as the only output
        if os.environ.get("APS_DEBUG"):
            raise
        sys.stdout.write(json.dumps({"ok": False, "command": getattr(args, "command", None),
                                     "error": {"code": "unexpected_error", "message": str(exc)}},
                                    indent=2) + "\n")
        sys.stderr.write("Set APS_DEBUG=1 to see the traceback.\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
