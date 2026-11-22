#!/usr/bin/env python3
"""Primary entry point for the full-offline-image-capabilities Skill Tool.

Usage:
    python3 tools-image-capabilities.py --help
    python3 tools-image-capabilities.py capabilities [--catalog]
    python3 tools-image-capabilities.py <command> [options]

Run from the Skill Tool folder, or from anywhere with an absolute path. All
paths are configurable and resolved relative to the current working
directory. No command performs network access.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load(filename, module_name):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(_HERE, filename))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


core = _load("tools-image-core.py", "foic_core")
qc = _load("tools-image-qc.py", "foic_qc")
security = _load("tools-image-security.py", "foic_security")
ops = _load("tools-image-ops.py", "foic_ops")
pipeline = _load("tools-image-pipeline.py", "foic_pipeline")
recover = _load("tools-image-recover.py", "foic_recover")

ToolError = core.ToolError
LOG = core.LOG

CAPABILITIES = [
    ("capabilities", "List implemented capabilities and map the 32-category source catalog."),
    ("doctor", "Audit environment readiness: runtimes, GPU, disk, model dir, no-download policy."),
    ("inspect", "Decode and report format, dimensions, channels, alpha, ICC/EXIF, hashes."),
    ("validate", "Run the QC check suite over images and build a JSON/Markdown report."),
    ("compare", "Compare two images with dHash/aHash distance and a pixel-diff metric."),
    ("convert", "Export PNG/JPEG/WebP/TIFF with quality, resize, metadata and alpha control."),
    ("mask", "Build and clean masks (alpha/luma/threshold, feather, islands, hole fill)."),
    ("canvas", "Expand a canvas for outpainting and emit the matching inpaint mask."),
    ("manifest", "Build or verify a SHA-256 integrity manifest for a model/asset tree."),
    ("screen", "Screen files for pickle, executable, archive-bomb, traversal and secret risk."),
    ("license-audit", "Audit licences, redistribution rights and the release-rights gate."),
    ("quarantine", "List or restore quarantined files (approval gated)."),
    ("plan", "Build a validated, reproducible offline job spec for 9 generation kinds."),
    ("prompt", "Compile prompt templates with variables, weighting, budget and injection filter."),
    ("batch", "Build, run and resume bounded job queues with retries and timeouts."),
    ("package", "Package a handoff ZIP with manifests, licences and offline instructions."),
    ("recover", "Salvage truncated PNGs, rebuild manifests, restore quarantined files."),
    ("cleanup", "Remove bounded tool temp artifacts (dry-run by default)."),
    ("selftest", "Run the built-in offline test suite and report pass/fail."),
]


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------
def emit(payload, as_json=False, out_path=None, cfg=None):
    indent = (cfg.get("output.indent") or 2) if cfg else 2
    json_out = getattr(ARGS, "json_out", None) if ARGS else None
    for target in [t for t in (out_path, json_out) if t]:
        core.write_json(target, payload, overwrite=bool(ARGS and ARGS.overwrite), indent=indent)
        LOG.info("wrote %s" % target)
    if False:
        core.write_json(out_path, payload, overwrite=bool(ARGS and ARGS.overwrite), indent=indent)
        LOG.info("wrote %s" % out_path)
    if as_json:
        print(json.dumps(payload, indent=indent, ensure_ascii=False))
    return payload


def print_kv(title, pairs):
    print("\n== %s ==" % title)
    width = max((len(str(k)) for k, _ in pairs), default=4)
    for key, value in pairs:
        print("  %-*s : %s" % (width, key, value))


SUPPRESS = argparse.SUPPRESS
ARGS = None


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_capabilities(args, cfg):
    if args.catalog:
        catalog = pipeline.load_capability_catalog()
        if args.category:
            matches = [c for c in catalog["categories"] if c["id"] == args.category]
            if not matches:
                raise ToolError("no category with id %s" % args.category, code="not-found")
            cat = matches[0]
            payload = {
                "category": cat,
                "mapped_to": sorted({s["capability"] for s in cat["skills"]}),
            }
            if args.json or args.out:
                return emit(payload, True, args.out, cfg)
            print("\n== %d. %s ==" % (cat["id"], cat["category"]))
            for skill in cat["skills"]:
                print("  - %-58s -> %-14s [%s]" % (skill["name"], skill["capability"], skill["mode"]))
            return payload
        payload = {
            "schema": catalog["schema"],
            "source": catalog["source"],
            "category_count": catalog["category_count"],
            "skill_count": catalog["skill_count"],
            "categories": [
                {"id": c["id"], "category": c["category"], "primary_capability": c["primary_capability"],
                 "skills": len(c["skills"])} for c in catalog["categories"]
            ],
        }
        if args.json or args.out:
            return emit(payload, True, args.out, cfg)
        print("\n== Source catalog: %s ==" % catalog["source"])
        print("   %d categories, %d proposed skills, all mapped to %d implemented capabilities\n"
              % (catalog["category_count"], catalog["skill_count"], len(CAPABILITIES)))
        for entry in payload["categories"]:
            print("  %2d. %-64s -> %s" % (entry["id"], entry["category"][:64], entry["primary_capability"]))
        print("\nUse --category <id> to list the 15 skills in a category and their capability mapping.")
        return payload
    payload = {
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "capabilities": [{"command": name, "description": desc} for name, desc in CAPABILITIES],
        "environment": {"python": sys.version.split()[0], "pillow": core.pillow_available()},
    }
    if args.json or args.out:
        return emit(payload, True, args.out, cfg)
    print("\n== %s %s ==" % (core.TOOL_NAME, core.TOOL_VERSION))
    print("   offline image capabilities: %d commands\n" % len(CAPABILITIES))
    for name, desc in CAPABILITIES:
        print("  %-14s %s" % (name, desc))
    print("\nRun `python3 tools-image-capabilities.py <command> --help` for arguments.")
    print("Run `python3 tools-image-capabilities.py capabilities --catalog` for the source catalog map.")
    return payload


def cmd_doctor(args, cfg):
    report = pipeline.doctor(cfg, base_dir=os.getcwd())
    if args.json or args.out:
        return emit(report, True, args.out, cfg)
    print_kv("Environment readiness", [
        ("status", report["status"].upper()),
        ("platform", "%s / python %s" % (report["platform"]["system"], report["platform"]["python"])),
        ("config", "%s (fp %s)" % (report["config"]["source"], report["config"]["fingerprint"])),
        ("network", report["config"]["network"]),
        ("image backend", report["config"]["backend"]),
    ])
    print("\n== Runtimes ==")
    for rt in report["runtimes"]:
        state = "yes" if rt["available"] else "no "
        print("  [%s] %-18s %s" % (state, rt["name"], rt["version"] or ""))
    print("\n== Hardware ==")
    gpu = report["gpu"]
    print("  nvidia-smi         : %s" % gpu["nvidia_smi"])
    print("  torch cuda         : %s (%d device(s))" % (gpu["torch_cuda"], gpu["torch_device_count"]))
    for dev in gpu["devices"]:
        print("  device             : %s (%s)" % (dev["name"], dev["memory"]))
    if "free_human" in report["disk"]:
        print("  free disk          : %s of %s" % (report["disk"]["free_human"], report["disk"]["total_human"]))
    print("\n== Checks ==")
    for check in report["checks"]:
        print("  [%-4s] %-22s %s" % (check["status"], check["id"], check["message"]))
    print("\n== Diffusion execution ==")
    print("  %s" % ("runtime present; plans still require local weights" if report["diffusion_ready"]
                    else "BLOCKED: no local diffusion runtime (plans are emitted, not executed)"))
    return report


def cmd_inspect(args, cfg):
    results = []
    for path in args.input:
        results.append(qc.inspect_image(path, cfg, with_stats=not args.no_stats,
                                        with_fingerprints=not args.no_fingerprints))
    payload = {"schema": "foic.inspect/1", "generated_at": core.iso_now(), "count": len(results), "results": results}
    if args.json or args.out:
        return emit(payload, True, args.out, cfg)
    for info in results:
        print_kv(info["path"], [
            ("format", info.get("format")),
            ("dimensions", "%sx%s (%.2f MP)" % (info.get("width"), info.get("height"), info.get("megapixels", 0))),
            ("mode", "%s (%d channels, alpha=%s)" % (info.get("mode"), info.get("channels", 0), info.get("has_alpha"))),
            ("aspect", info.get("aspect_ratio")),
            ("bit depth", info.get("bit_depth")),
            ("icc/exif", "%s / %s" % (info.get("has_icc"), info.get("has_exif"))),
            ("size", info.get("file_size_human")),
            ("sha256", (info.get("sha256") or "")[:32] + "..."),
            ("backend", (info.get("decode") or {}).get("backend")),
        ])
        fp = info.get("fingerprints")
        if fp:
            print("  dhash               : %s" % fp["dhash"])
            print("  ahash               : %s" % fp["ahash"])
        if info.get("stats"):
            print("  stats               : %s" % json.dumps(
                {k: info["stats"][k] for k in ("std_luma", "mean_saturation", "edge_density", "flat_block_ratio")}))
    return payload


def cmd_validate(args, cfg):
    policy = qc.load_policy(cfg, policy_path=args.policy, preset=args.preset, overrides=args.set_policy)
    paths = []
    for item in args.input:
        if os.path.isdir(item):
            for path in core.iter_files(item):
                with open(path, "rb") as fh:
                    head = fh.read(16)
                if core.sniff_format(head) != "UNKNOWN":
                    paths.append(path)
        else:
            paths.append(item)
    if not paths:
        raise ToolError("no input images found", code="no-input")
    deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "validating images")
    report = qc.validate_images(paths, cfg, policy, checks=args.check, deadline=deadline)
    problems = core.validate_schema(report, core.read_json(os.path.join(core.TOOL_DIR, core.DEFAULT_QC_SCHEMA_ASSET)))
    report["schema_validation"] = {"valid": not problems, "problems": problems[:10]}
    if args.report:
        core.atomic_write_text(args.report, qc.report_to_markdown(report, cfg), overwrite=args.overwrite)
        LOG.info("wrote Markdown report %s" % args.report)
    if args.json or args.out:
        payload = emit(report, True, args.out if args.out else None, cfg)
    else:
        print_kv("QC summary", [(k, str(v)) for k, v in report["summary"].items()])
        for entry in report["results"]:
            print("\n  %s [%s]" % (entry["path"], entry["status"]))
            for chk in entry["checks"]:
                if chk["status"] in ("pass", "skip") and not args.verbose:
                    continue
                print("    [%-4s] %-13s %s" % (chk["status"], chk["id"], chk["message"]))
        payload = report
    verdict = report["summary"]["verdict"]
    order = {"pass": 0, "warn": 1, "fail": 2}
    if order[verdict] > order[args.fail_on]:
        raise ToolError(
            "quality gate failed: verdict=%s, --fail-on=%s" % (verdict, args.fail_on),
            code="qc-failed",
            exit_code=core.EXIT_VALIDATION,
            details=report["summary"],
        )
    return payload


def cmd_compare(args, cfg):
    result = qc.compare_images(args.a, args.b, cfg, tolerance=args.tolerance)
    if args.json or args.out:
        return emit(result, True, args.out, cfg)
    print_kv("Comparison", [
        ("A", "%s (%sx%s %s)" % (result["a"]["path"], result["a"]["width"], result["a"]["height"], result["a"]["mode"])),
        ("B", "%s (%sx%s %s)" % (result["b"]["path"], result["b"]["width"], result["b"]["height"], result["b"]["mode"])),
        ("dimensions match", result["dimensions_match"]),
        ("dHash distance", result.get("dhash_distance")),
        ("aHash distance", result.get("ahash_distance")),
        ("mean abs diff", result.get("mean_abs_diff")),
        ("changed pixel ratio", result.get("changed_pixel_ratio")),
        ("verdict", result["verdict"]),
    ])
    return result


def cmd_convert(args, cfg):
    result = ops.convert_image(
        args.input, args.output, cfg, fmt=args.format, quality=args.quality,
        strip_metadata=args.strip_metadata, background=args.background,
        flatten_alpha=args.flatten_alpha, max_width=args.max_width, max_height=args.max_height,
        scale=args.scale, overwrite=args.overwrite,
    )
    if args.json or args.out:
        return emit(result, True, args.out, cfg)
    print_kv("Converted", [
        ("source", result["source"]),
        ("output", result["output"]),
        ("format", result["format"]),
        ("dimensions", "%sx%s (%s)" % (result["width"], result["height"], result["mode"])),
        ("backend", result["backend"]),
        ("bytes", core.human_bytes(result["bytes"])),
    ])
    for note in result["notes"]:
        print("  note                : %s" % note)
    return result


def cmd_mask(args, cfg):
    result = ops.build_mask(
        args.input, args.output, cfg, mode=args.mode, threshold=args.threshold,
        invert=args.invert, feather=args.feather, min_island_px=args.min_island_px,
        fill_holes=args.fill_holes, overwrite=args.overwrite,
    )
    if args.json or args.out:
        return emit(result, True, args.out, cfg)
    print_kv("Mask", [
        ("source", result["source"]),
        ("output", result["output"]),
        ("mode", "%s (invert=%s)" % (result["mode"], result["invert"])),
        ("dimensions", "%sx%s" % (result["width"], result["height"])),
        ("coverage", "%.1f%%" % (result["coverage"] * 100)),
        ("island px removed", result["island_pixels_removed"]),
        ("holes filled", result["holes_filled"]),
        ("components", result["components"]),
    ])
    return result


def cmd_canvas(args, cfg):
    result = ops.expand_canvas(
        args.input, args.output, cfg, left=args.left, right=args.right, top=args.top,
        bottom=args.bottom, fill=args.fill, background=args.background,
        mask_out=args.mask_out, overwrite=args.overwrite,
    )
    if args.json or args.out:
        return emit(result, True, args.out, cfg)
    pairs = [
        ("source", result["source"]),
        ("output", result["output"]),
        ("original", "x".join(str(v) for v in result["original"])),
        ("expanded", "x".join(str(v) for v in result["expanded"])),
        ("expand", json.dumps(result["expand"])),
    ]
    if result.get("mask_output"):
        pairs.append(("mask", "%s (%.1f%% region)" % (result["mask_output"], result["mask_region_ratio"] * 100)))
    print_kv("Canvas", pairs)
    return result


def cmd_manifest(args, cfg):
    if args.action == "build":
        deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "building manifest")
        manifest = security.build_manifest(args.root, cfg, deadline=deadline)
        core.write_json(args.out, manifest, overwrite=args.overwrite)
        payload = {"schema": "foic.manifest-built/1", "output": os.path.abspath(args.out),
                   "root": os.path.abspath(args.root), "file_count": manifest["file_count"],
                   "total_bytes": manifest["total_bytes"], "total_bytes_human": manifest["total_bytes_human"]}
        if args.json:
            return emit(payload, True, None, cfg)
        print_kv("Manifest built", [(k, str(v)) for k, v in payload.items() if k != "schema"])
        return payload
    manifest = core.read_json(args.manifest)
    deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "verifying manifest")
    result = security.verify_manifest(args.root, manifest, cfg, deadline=deadline)
    if args.json or args.out:
        return emit(result, True, args.out, cfg)
    print_kv("Manifest verification", [
        ("root", result["root"]),
        ("verified", result["verified"]),
        ("missing", len(result["missing"])),
        ("mismatched", len(result["mismatched"])),
        ("extra", len(result["extra"])),
        ("status", result["status"].upper()),
    ])
    for item in result["missing"][:10]:
        print("  MISSING   %s" % item["path"])
    for item in result["mismatched"][:10]:
        print("  MISMATCH  %s" % item["path"])
    for item in result["extra"][:10]:
        print("  EXTRA     %s" % item["path"])
    return result


def cmd_screen(args, cfg):
    paths = list(args.input or [])
    if args.root:
        paths.extend(core.iter_files(args.root))
    if not paths:
        raise ToolError("provide files or --root DIR", code="usage", exit_code=core.EXIT_USAGE)
    deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "screening files")
    if args.root and not args.input:
        report = security.screen_tree(args.root, cfg, deadline=deadline)
    else:
        results = []
        for path in paths:
            deadline.check("screening")
            results.append(security.screen_file(path, cfg))
        report = {
            "schema": "foic.screen/1",
            "generated_at": core.iso_now(),
            "root": None,
            "summary": {
                "scanned": len(results),
                "failed": sum(1 for r in results if r["status"] == "fail"),
                "warned": sum(1 for r in results if r["status"] == "warn"),
                "passed": sum(1 for r in results if r["status"] == "pass"),
                "errored": sum(1 for r in results if r["status"] == "error"),
            },
            "results": results,
        }
        report["summary"]["verdict"] = ("fail" if report["summary"]["failed"]
                                        else "warn" if report["summary"]["warned"] else "pass")
    quarantined = []
    if args.quarantine:
        qdir = core.resolve_under(os.getcwd(), cfg.get("paths.quarantine_dir"))
        for entry in report["results"]:
            if entry["status"] == "fail":
                quarantined.append(security.quarantine_file(
                    entry["path"], cfg, qdir, approved=args.approve,
                    reason="security screening failure"))
        report["quarantined"] = quarantined
    if args.json or args.out:
        return emit(report, True, args.out, cfg)
    print_kv("Screening", [
        ("scanned", report["summary"]["scanned"]),
        ("failed", report["summary"]["failed"]),
        ("warned", report["summary"]["warned"]),
        ("passed", report["summary"]["passed"]),
        ("verdict", report["summary"]["verdict"].upper()),
    ])
    for entry in report["results"]:
        if entry["status"] == "pass" and not args.verbose:
            continue
        print("\n  %s [%s]" % (entry["filename"], entry["status"]))
        for finding in entry["findings"]:
            print("    [%-5s] %-22s %s" % (finding["severity"], finding["id"], finding["message"]))
    if quarantined:
        print("\nQuarantined %d file(s) to %s" % (len(quarantined), core.resolve_under(os.getcwd(), cfg.get("paths.quarantine_dir"))))
    return report


def cmd_license_audit(args, cfg):
    deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "auditing licences")
    report = security.license_audit(args.root, cfg, policy=args.policy and core.read_json(args.policy),
                                    deadline=deadline)
    if args.json or args.out:
        return emit(report, True, args.out, cfg)
    print_kv("License and rights audit", [
        ("root", report["root"]),
        ("components", report["summary"]["components"]),
        ("allow", report["summary"]["allow"]),
        ("review", report["summary"]["review"]),
        ("deny", report["summary"]["deny"]),
        ("release-rights gate", report["summary"]["release_rights_gate"].upper()),
    ])
    for comp in report["components"]:
        print("  [%-6s] %-40s %s" % (comp["classification"], comp["path"][:40], comp["license"]))
    for blocker in report["blockers"]:
        print("  BLOCKER: %s -> %s (%s)" % (blocker["path"], blocker["license"], blocker["redistribution"]))
    return report


def cmd_quarantine(args, cfg):
    qdir = core.resolve_under(os.getcwd(), cfg.get("paths.quarantine_dir"))
    if args.action == "list":
        records = security.list_quarantine(qdir)
        payload = {"schema": "foic.quarantine-list/1", "dir": qdir, "count": len(records), "records": records}
        if args.json:
            return emit(payload, True, None, cfg)
        print_kv("Quarantine", [("dir", qdir), ("records", len(records))])
        for rec in records:
            print("  - %s\n      from %s\n      reason: %s" % (rec["quarantined_path"], rec["original_path"], rec["reason"]))
        return payload
    if not args.record:
        raise ToolError("restore requires --record PATH", code="usage", exit_code=core.EXIT_USAGE)
    result = recover.restore(args.record, cfg, approved=args.approve)
    if args.json:
        return emit(result, True, None, cfg)
    print_kv("Restored", [("path", result["restored"]), ("record", result["record_removed"])])
    return result


def cmd_plan(args, cfg):
    inputs = {}
    if args.input_image:
        inputs["image"] = os.path.abspath(args.input_image)
    if args.mask:
        inputs["mask"] = os.path.abspath(args.mask)
    if args.control_image:
        inputs["control_image"] = os.path.abspath(args.control_image)
    job = pipeline.build_plan(
        cfg, args.kind, model_name=args.model, preset=args.preset, overrides=args.set,
        prompt=args.prompt, negative_prompt=args.negative, seed=args.seed,
        width=args.width, height=args.height, inputs=inputs, loras=args.lora,
    )
    validation = pipeline.validate_plan(job)
    job["spec_validation"] = validation
    if not validation["valid"] and not args.allow_invalid:
        raise ToolError(
            "job spec failed schema validation: %s" % json.dumps(validation["problems"][:5]),
            code="invalid-spec",
        )
    if args.out:
        core.write_json(args.out, job, overwrite=args.overwrite)
        LOG.info("wrote job spec %s" % args.out)
    if args.prov_out:
        pipeline.write_provenance(job, args.prov_out, overwrite=args.overwrite)
        LOG.info("wrote provenance %s" % args.prov_out)
    emitted = None
    if args.emit_script:
        emitted = pipeline.emit_runner_script(job, args.emit_script, overwrite=args.overwrite)
        LOG.info("emitted runner script %s (not executed)" % args.emit_script)
    payload = {"job": job, "emitted_script": emitted}
    if args.json:
        return emit(payload, True, None, cfg)
    print_kv("Job spec %s" % job["job_id"], [
        ("kind", job["kind"]),
        ("model", "%s (%s, %s)" % (job["model"]["name"], job["model"]["family"], job["model"]["runtime"])),
        ("resolution", "%dx%d" % (job["parameters"]["width"], job["parameters"]["height"])),
        ("sampler", "%s / %s, %d steps, cfg %.1f" % (
            job["parameters"]["sampler"], job["parameters"]["scheduler"],
            job["parameters"]["steps"], job["parameters"]["guidance"])),
        ("seed", job["parameters"]["seed"]),
        ("device", "%s (vram %s)" % (job["memory_plan"]["device"], job["memory_plan"]["vram_gb"])),
        ("fallbacks", ", ".join(job["memory_plan"]["fallbacks"]) or "none"),
        ("schema valid", validation["valid"]),
        ("status", job["status"].upper()),
    ])
    if job["prompt"]:
        print("  prompt              : %s" % job["prompt"])
    for blocker in job["blockers"]:
        print("  BLOCKER             : %s" % blocker)
    for note in job["memory_plan"]["notes"]:
        print("  memory note         : %s" % note)
    return payload


def cmd_prompt(args, cfg):
    variables = {}
    for item in args.set:
        if "=" not in item:
            raise ToolError("--set expects key=value", code="usage", exit_code=core.EXIT_USAGE)
        key, _, value = item.partition("=")
        variables[key.strip()] = value.strip()
    recipes = pipeline.load_recipes(args.recipes)
    result = pipeline.compile_prompt(
        args.template, variables=variables, recipes=recipes, negative=args.negative,
        weight_style=args.weight_style, model=args.model,
    )
    if args.out:
        core.write_json(args.out, result, overwrite=args.overwrite)
    if args.json:
        return emit(result, True, None, cfg)
    print_kv("Prompt %s" % result["template"], [
        ("prompt", result["prompt"]),
        ("negative", result["negative_prompt"] or "(none)"),
        ("negative library", result["negative_library"] or "(none)"),
        ("weight style", result["weight_style"]),
        ("token estimate", "%s tokens (heuristic)" % result["token_estimate"]["tokens"]),
        ("tokenizer limit", result["tokenizer_limit"]),
    ])
    for warning in result["warnings"]:
        print("  warning             : %s" % warning)
    return result


def _load_json_arg(value, what):
    if value.startswith("@"):
        return core.read_json(value[1:])
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ToolError("%s must be inline JSON or @file.json (%s)" % (what, exc), code="usage", exit_code=core.EXIT_USAGE)


def _offline_executor(cfg):
    def run(job, out_dir):
        op = job.get("op")
        params = dict(job.get("params", {}))
        job_id = job.get("job_id", "job")
        if op == "inspect":
            return qc.inspect_image(params["src"], cfg)
        if op == "validate":
            policy = qc.load_policy(cfg, preset=params.get("preset"))
            report = qc.validate_images(params["src"] if isinstance(params["src"], list) else [params["src"]], cfg, policy)
            return report["summary"]
        if op == "convert":
            dst = params.get("dst") or os.path.join(out_dir, job_id + "." + params.get("format", "png"))
            return ops.convert_image(params["src"], dst, cfg, fmt=params.get("format", "png"),
                                     quality=params.get("quality", 95),
                                     strip_metadata=params.get("strip_metadata", False),
                                     flatten_alpha=params.get("flatten_alpha", False),
                                     background=params.get("background"), overwrite=True)
        if op == "mask":
            dst = params.get("dst") or os.path.join(out_dir, job_id + "-mask.png")
            return ops.build_mask(params["src"], dst, cfg, mode=params.get("mode", "alpha"),
                                  threshold=params.get("threshold", 128), invert=params.get("invert", False),
                                  feather=params.get("feather", 0), min_island_px=params.get("min_island_px", 0),
                                  fill_holes=params.get("fill_holes", 0), overwrite=True)
        if op == "canvas":
            dst = params.get("dst") or os.path.join(out_dir, job_id + "-canvas.png")
            return ops.expand_canvas(params["src"], dst, cfg, left=params.get("left", 0),
                                     right=params.get("right", 0), top=params.get("top", 0),
                                     bottom=params.get("bottom", 0), fill=params.get("fill", "edge"),
                                     mask_out=params.get("mask_out"), overwrite=True)
        if op == "screen":
            return security.screen_file(params["src"], cfg)
        if op == "manifest":
            manifest = security.build_manifest(params["src"], cfg)
            path = os.path.join(out_dir, job_id + "-manifest.json")
            core.write_json(path, manifest, overwrite=True)
            return {"output": path, "file_count": manifest["file_count"]}
        raise ToolError("unsupported offline op %r" % op, code="unknown-op")
    return run


def cmd_batch(args, cfg):
    if args.action == "build":
        plan = core.read_json(args.plan)
        matrix = _load_json_arg(args.matrix, "--matrix") if args.matrix else {}
        out_dir = args.out_dir or core.resolve_under(os.getcwd(), cfg.get("paths.output_dir"))
        queue = pipeline.build_batch(plan, matrix, out_dir, cfg, max_jobs=args.max_jobs, name=args.name)
        out_path = args.out or os.path.join(out_dir, "%s-queue.json" % args.name)
        core.write_json(out_path, queue, overwrite=args.overwrite)
        payload = {"schema": "foic.batch-built/1", "queue": out_path, "job_count": queue["job_count"],
                   "output_dir": queue["output_dir"]}
        if args.json:
            return emit(payload, True, None, cfg)
        print_kv("Batch queue", [("queue", out_path), ("jobs", queue["job_count"]), ("output dir", queue["output_dir"])])
        return payload
    if args.action == "run":
        queue = core.read_json(args.queue)
        out_dir = args.out_dir or queue.get("output_dir") or core.resolve_under(os.getcwd(), cfg.get("paths.output_dir"))
        state_path = args.state or os.path.join(out_dir, "batch-state.json")
        state = None
        if args.resume and os.path.isfile(state_path):
            state = core.read_json(state_path).get("state")
        deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "running batch")
        run = pipeline.run_batch(queue, cfg, out_dir, deadline=deadline, resume_state=state,
                                 executor=_offline_executor(cfg))
        core.write_json(state_path, run, overwrite=True)
        if args.json or args.out:
            return emit(run, True, args.out, cfg)
        print_kv("Batch run", [(k, str(v)) for k, v in run["summary"].items()])
        for entry in run["results"]:
            if entry["status"] == "blocked":
                print("  BLOCKED %s : %s" % (entry["job_id"], entry["reason"][:90]))
            elif entry["status"] == "failed":
                print("  FAILED  %s : %s" % (entry["job_id"], entry.get("error", "")))
        print("\n  state file: %s" % state_path)
        return run
    state = core.read_json(args.state)
    if args.json:
        return emit(state, True, None, cfg)
    print_kv("Batch state", [(k, str(v)) for k, v in state["summary"].items()])
    return state


def cmd_package(args, cfg):
    import zipfile

    root = os.path.abspath(args.root)
    if not os.path.exists(root):
        raise ToolError("package root not found: %s" % root, code="not-found")
    out_path = os.path.abspath(args.out)
    if os.path.exists(out_path) and not args.overwrite:
        raise ToolError("refusing to overwrite %s without --overwrite" % out_path, code="would-overwrite")
    include = set((args.include or "config,presets").split(","))
    max_total = int(args.max_total_mb) * 1024 * 1024
    prefix = args.name or (os.path.basename(root) or "package") + "-offline-package"
    deadline = core.Deadline(args.timeout or cfg.get("runtime.timeout_seconds"), "packaging")
    files = []
    total = 0
    skipped = []
    for path in core.iter_files(root, skip_dirs={".foic-cache", "quarantine", "__pycache__"}):
        deadline.check("packaging")
        rel = os.path.relpath(path, root).replace("\\", "/")
        if rel.endswith(".foic-tmp"):
            continue
        size = os.path.getsize(path)
        if total + size > max_total:
            skipped.append({"path": rel, "reason": "max_total_mb exceeded"})
            continue
        files.append((rel, path))
        total += size
    core.ensure_dir(os.path.dirname(out_path) or ".")
    with zipfile.ZipFile(out_path + ".foic-tmp", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, path in files:
            zf.write(path, "%s/content/%s" % (prefix, rel))
        manifest = security.build_manifest(root, cfg, deadline=deadline)
        zf.writestr("%s/manifest/checksums.json" % prefix, json.dumps(manifest, indent=2))
        zf.writestr("%s/manifest/version-manifest.json" % prefix, json.dumps(
            {"schema": "foic.version-manifest/1", "generated_at": core.iso_now(),
             "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
             "python": sys.version.split()[0], "files": manifest["file_count"],
             "total_bytes": manifest["total_bytes"]}, indent=2))
        if "licenses" in include:
            audit = security.license_audit(root, cfg, deadline=deadline)
            zf.writestr("%s/manifest/license-manifest.json" % prefix, json.dumps(audit, indent=2))
            notices = ["# Attribution and notices", ""]
            for comp in audit["components"]:
                notices.append("- %s" % comp["attribution"])
            zf.writestr("%s/NOTICES.md" % prefix, "\n".join(notices) + "\n")
        if "presets" in include:
            for asset in ("asset-pipeline-presets.json", "asset-prompt-recipes.json", "asset-qc-policy.json"):
                src = os.path.join(core.TOOL_DIR, asset)
                if os.path.isfile(src):
                    zf.write(src, "%s/config/%s" % (prefix, asset))
        if "config" in include:
            zf.writestr("%s/config/runtime-config.json" % prefix, json.dumps(cfg.raw, indent=2))
        install = [
            "# Offline installation",
            "",
            "Generated by %s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
            "",
            "1. Copy this archive to the offline workstation and extract it.",
            "2. Stage pinned wheels locally, then install with no network access:",
            "   python3 -m pip install --no-index --find-links ./wheelhouse -r tools-requirements.txt",
            "3. Verify integrity before use:",
            "   python3 tools-image-capabilities.py manifest verify --root ./content --manifest ./manifest/checksums.json",
            "4. Check environment readiness:",
            "   python3 tools-image-capabilities.py doctor",
            "5. Review rights before redistribution: see NOTICES.md and manifest/license-manifest.json.",
            "",
            "No step contacts the network. A missing local component fails loudly.",
        ]
        zf.writestr("%s/INSTALL.md" % prefix, "\n".join(install) + "\n")
        selftest = [
            "# Self-test",
            "",
            "Run from the Skill Tool folder with no network access:",
            "",
            "```",
            "python3 tools-image-capabilities.py selftest --out ./selftest-out",
            "python3 tools-image-capabilities.py doctor",
            "python3 tools-image-capabilities.py manifest verify --root ./content --manifest ./manifest/checksums.json",
            "python3 tools-image-capabilities.py validate ./content --preset photo --report qc.md",
            "```",
            "",
            "Expected: selftest exits 0 with all checks passing.",
        ]
        zf.writestr("%s/SELFTEST.md" % prefix, "\n".join(selftest) + "\n")
    os.replace(out_path + ".foic-tmp", out_path)
    payload = {
        "schema": "foic.package/1",
        "output": out_path,
        "root": root,
        "prefix": prefix,
        "files": len(files),
        "total_bytes": total,
        "total_bytes_human": core.human_bytes(total),
        "skipped": skipped,
        "contains_nested_archives": False,
        "sha256": core.sha256_file(out_path)[0],
    }
    if args.json or args.out:
        return emit(payload, True, None, cfg)
    print_kv("Offline package", [(k, str(v)) for k, v in payload.items() if k != "schema"])
    return payload


def cmd_recover(args, cfg):
    if args.action == "png":
        result = recover.salvage_png(args.input, args.output, cfg, overwrite=args.overwrite)
        if args.json or args.out:
            return emit(result, True, args.out, cfg)
        print_kv("PNG salvage", [
            ("source", result["source"]),
            ("output", result["output"]),
            ("status", result["status"].upper()),
            ("rows recovered", "%d of %d" % (result["rows_recovered"], result["expected_height"])),
            ("padded rows", result["rows_padded"]),
            ("recovery ratio", "%.1f%%" % (result["recovery_ratio"] * 100)),
            ("output size", "%dx%d" % (result["width"], result["output_height"])),
        ])
        return result
    if args.action == "manifest":
        result = recover.rebuild_manifest(args.root, args.out, cfg, overwrite=args.overwrite)
        if args.json:
            return emit(result, True, None, cfg)
        print_kv("Manifest rebuilt", [("output", result["output"]), ("files", result["file_count"]),
                                      ("bytes", core.human_bytes(result["total_bytes"]))])
        return result
    if args.action == "restore":
        result = recover.restore(args.record, cfg, approved=args.approve)
        if args.json:
            return emit(result, True, None, cfg)
        print_kv("Restored", [("path", result["restored"])])
        return result
    raise ToolError("unknown recover action", code="usage", exit_code=core.EXIT_USAGE)


def cmd_cleanup(args, cfg):
    result = recover.cleanup(args.dir, cfg, approve=args.approve, older_than_hours=args.older_than,
                             dry_run=not args.approve)
    if args.json:
        return emit(result, True, None, cfg)
    print_kv("Cleanup", [
        ("root", result["root"]),
        ("dry run", result["dry_run"]),
        ("removed", result["removed_count"]),
        ("kept", result["kept_count"]),
        ("bytes freed", core.human_bytes(result["bytes_freed"])),
        ("policy", result["policy"]),
    ])
    for item in result["removed"][:20]:
        print("  - %s" % item["path"])
    if result["dry_run"] and result["removed_count"]:
        print("\n  Dry run only. Re-run with --approve to delete these tool temp artifacts.")
    return result


def cmd_selftest(args, cfg):
    suite = _load("tools-image-selftest.py", "foic_selftest")
    result = suite.run(args, cfg)
    if not result.get("ok"):
        raise ToolError(
            "self-test failed: %d check(s) did not pass" % result["failures"],
            code="selftest-failed",
            exit_code=core.EXIT_VALIDATION,
        )
    return result


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-image-capabilities.py",
        description="Offline image capabilities: inspect, validate, convert, mask, plan, screen, package, recover.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="All commands run offline. Destructive or security-sensitive actions require --approve.",
    )
    parser.add_argument("--config", help="JSON config merged over asset-default-config.json")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a config value by dotted path (repeatable)")
    parser.add_argument("--json", action="store_true", help="force JSON output on stdout")
    parser.add_argument("--json-out", dest="json_out", help="also write the JSON result to this path")
    parser.add_argument("--overwrite", action="store_true", help="allow overwriting existing outputs")
    parser.add_argument("--approve", action="store_true", help="approve gated (destructive/security) actions")
    parser.add_argument("--timeout", type=int, help="override runtime.timeout_seconds for this run")
    parser.add_argument("--quiet", action="store_true", help="suppress log output")
    parser.add_argument("--verbose", action="store_true", help="show passing checks and extra detail")
    parser.add_argument("--version", action="version", version="%s %s" % (core.TOOL_NAME, core.TOOL_VERSION))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capabilities", help="list capabilities and the source catalog map")
    p.add_argument("--catalog", action="store_true", help="show the 32-category source catalog")
    p.add_argument("--category", type=int, help="show one catalog category with its 15 skills")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("doctor", help="environment readiness audit")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("inspect", help="decode and report image metadata")
    p.add_argument("input", nargs="+")
    p.add_argument("--no-stats", action="store_true")
    p.add_argument("--no-fingerprints", action="store_true")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("validate", help="run the QC check suite")
    p.add_argument("input", nargs="+", help="image files or directories")
    p.add_argument("--policy", help="path to a QC policy JSON")
    p.add_argument("--preset", help="policy preset: photo, product-cutout, upscale-output, typography, thumbnail")
    p.add_argument("--check", help="comma separated subset of: " + ", ".join(qc.CHECK_IDS))
    p.add_argument("--report", help="write a Markdown report here")
    p.add_argument("--fail-on", choices=["pass", "warn", "fail"], default="warn",
                   help="exit non-zero when the verdict is worse than this (default: warn)")
    p.add_argument("--policy-set", dest="set_policy", action="append", default=[], metavar="KEY=VALUE",
                   help="override a policy default or threshold (prefix thresholds with thresholds.)")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("compare", help="compare two images")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--tolerance", type=int, default=0, help="dHash distance still counted as within tolerance")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("convert", help="export to another format")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--format", default="png", choices=list(ops.SUPPORTED_OUTPUT_FORMATS))
    p.add_argument("--quality", type=int, default=95)
    p.add_argument("--strip-metadata", action="store_true", help="drop EXIF/ICC on export")
    p.add_argument("--flatten-alpha", action="store_true")
    p.add_argument("--background", default="#ffffff", help="flatten colour as #RRGGBB")
    p.add_argument("--max-width", type=int)
    p.add_argument("--max-height", type=int)
    p.add_argument("--scale", type=float)
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("mask", help="build and clean a mask")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--mode", choices=["alpha", "luma", "threshold"], default="alpha")
    p.add_argument("--threshold", type=int, default=128)
    p.add_argument("--invert", action="store_true")
    p.add_argument("--feather", type=int, default=0, help="box blur radius in pixels")
    p.add_argument("--min-island-px", type=int, default=0, help="drop components smaller than N pixels")
    p.add_argument("--fill-holes", type=int, default=0, help="fill enclosed holes up to N pixels")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_mask)

    p = sub.add_parser("canvas", help="expand canvas for outpainting")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--left", type=int, default=0)
    p.add_argument("--right", type=int, default=0)
    p.add_argument("--top", type=int, default=0)
    p.add_argument("--bottom", type=int, default=0)
    p.add_argument("--fill", choices=["edge", "color"], default="edge")
    p.add_argument("--background", default="#ffffff")
    p.add_argument("--mask-out", help="also write the inpaint mask here")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_canvas)

    p = sub.add_parser("manifest", help="build or verify a checksum manifest")
    p.add_argument("action", choices=["build", "verify"])
    p.add_argument("--root", help="root directory to scan")
    p.add_argument("--out", help="manifest output path (build)")
    p.add_argument("--manifest", help="manifest path (verify)")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("screen", help="screen files for security risk")
    p.add_argument("input", nargs="*")
    p.add_argument("--root", help="scan a whole directory tree")
    p.add_argument("--quarantine", action="store_true", help="quarantine files that fail (needs --approve)")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("license-audit", help="audit licences and redistribution rights")
    p.add_argument("--root", required=True)
    p.add_argument("--policy", help="alternate licence policy JSON")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.add_argument("--out")
    p.set_defaults(func=cmd_license_audit)

    p = sub.add_parser("quarantine", help="list or restore quarantined files")
    p.add_argument("action", choices=["list", "restore"])
    p.add_argument("--record", help="quarantine record path (restore)")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_quarantine)

    p = sub.add_parser("plan", help="build a reproducible offline job spec")
    p.add_argument("--kind", required=True, choices=pipeline.KINDS)
    p.add_argument("--model", default=pipeline.DEFAULT_MODEL)
    p.add_argument("--preset", help="path to a preset JSON or a preset name from the asset file")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override operation parameters")
    p.add_argument("--prompt")
    p.add_argument("--negative")
    p.add_argument("--seed", type=int)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--input-image")
    p.add_argument("--mask")
    p.add_argument("--control-image")
    p.add_argument("--lora", action="append", default=[], metavar="NAME:WEIGHT")
    p.add_argument("--out", help="write the job spec JSON here")
    p.add_argument("--prov-out", dest="prov_out", help="write a provenance sidecar here")
    p.add_argument("--emit-script", dest="emit_script", help="emit a standalone runner script (never executed)")
    p.add_argument("--allow-invalid", action="store_true", help="keep the spec even if schema validation fails")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("prompt", help="compile a prompt template")
    p.add_argument("--template", required=True)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--negative", help="negative prompt library name")
    p.add_argument("--weight-style", default="none", choices=["a1111", "compel", "none"])
    p.add_argument("--model", help="model preset used for the token budget")
    p.add_argument("--recipes", help="alternate prompt recipes JSON")
    p.add_argument("--out")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("batch", help="build, run and resume job queues")
    p.add_argument("action", choices=["build", "run", "status"])
    p.add_argument("--plan", help="base job spec (build)")
    p.add_argument("--matrix", help="inline JSON or @file.json: seeds, resolutions, models, loras")
    p.add_argument("--queue", help="queue file (run)")
    p.add_argument("--state", help="state file (run/status)")
    p.add_argument("--out", help="write the run report here (run) or the queue here (build)")
    p.add_argument("--out-dir", dest="out_dir", help="output directory")
    p.add_argument("--max-jobs", type=int)
    p.add_argument("--name", default="batch")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("package", help="package an offline handoff ZIP")
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--include", default="config,presets,licenses", help="comma list: config,presets,licenses")
    p.add_argument("--name", help="folder name inside the archive")
    p.add_argument("--max-total-mb", type=int, default=20480)
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("recover", help="salvage, rebuild and restore")
    p.add_argument("action", choices=["png", "manifest", "restore"])
    p.add_argument("--input")
    p.add_argument("--output")
    p.add_argument("--root")
    p.add_argument("--out")
    p.add_argument("--record")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_recover)

    p = sub.add_parser("cleanup", help="remove bounded tool temp artifacts")
    p.add_argument("--dir", required=True)
    p.add_argument("--older-than", type=float, default=0.0, help="only remove artifacts older than N hours")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("selftest", help="run the built-in offline test suite")
    p.add_argument("--out", default="selftest-out", help="scratch directory for test artifacts")
    p.add_argument("--json", action="store_true", default=SUPPRESS)
    p.set_defaults(func=cmd_selftest)

    for name, subp in sub._name_parser_map.items():
        subp.add_argument("--overwrite", action="store_true", default=SUPPRESS,
                          help="allow overwriting existing outputs (same as the global flag)")
        subp.add_argument("--approve", action="store_true", default=SUPPRESS,
                          help="approve gated actions (same as the global flag)")
    return parser


def main(argv=None):
    global ARGS
    parser = build_parser()
    args = parser.parse_args(argv)
    ARGS = args
    try:
        cfg = core.load_config(config_path=args.config, overrides=args.set)
    except ToolError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return exc.exit_code
    if args.quiet:
        cfg.set_dotted("logging.quiet", True)
        LOG.level = core.Logger.LEVELS["silent"]
    if args.verbose:
        cfg.set_dotted("logging.level", "debug")
        LOG.level = core.Logger.LEVELS["debug"]
    if args.timeout:
        cfg.set_dotted("runtime.timeout_seconds", args.timeout)
    try:
        args.func(args, cfg)
        return core.EXIT_OK
    except ToolError as exc:
        print("ERROR [%s]: %s" % (exc.code, exc), file=sys.stderr)
        if exc.details:
            print("  details: %s" % json.dumps(exc.details, indent=2)[:2000], file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return core.EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - surface as a structured failure
        print("UNEXPECTED ERROR: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return core.EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
