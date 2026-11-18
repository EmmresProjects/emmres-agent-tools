#!/usr/bin/env python3
"""offline-neural-video-pipeline - primary entry point.

A local, offline-first control plane, executor and validator for neural video
production work described by the Full Offline Neural Video Skill Catalog
(28 categories / 1235 capability concepts).

Quick start
-----------
    python3 tools-onv.py help
    python3 tools-onv.py capabilities
    python3 tools-onv.py describe offline-image-to-video-engine
    python3 tools-onv.py doctor
    python3 tools-onv.py spec-init --out job.json
    python3 tools-onv.py plan --spec job.json
    python3 tools-onv.py run --spec job.json --approve
    python3 tools-onv.py validate --file outputs/demo-001.mp4
    python3 tools-onv.py selftest

Exit codes: 0 ok, 2 invalid input, 3 validation failed, 4 blocked (approval,
consent or backend), 5 internal error, 6 cancelled.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import signal
import sys
import threading
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
_MODULE_CACHE = {}
MIN_PYTHON = (3, 8)

# Keep the Skill Tool folder read-only in practice: never drop __pycache__ next
# to the tools-* modules when they are loaded by path.
sys.dont_write_bytecode = True


# ---------------------------------------------------------------------------
# module loading (filenames keep the required tools- prefix)
# ---------------------------------------------------------------------------
def load_module(name):
    if name in _MODULE_CACHE:
        return _MODULE_CACHE[name]
    path = TOOL_DIR / ("tools-onv-%s.py" % name)
    if not path.exists():
        raise RuntimeError("required module missing next to the entry point: %s" % path.name)
    module_name = "onv_" + name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _MODULE_CACHE[name] = module
    return module


common = load_module("common")


# ---------------------------------------------------------------------------
# runtime context
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, args):
        self.args = args
        self.common = common
        self.cancel_event = threading.Event()
        self.json_mode = bool(getattr(args, "json", False))
        self.quiet = bool(getattr(args, "quiet", False))
        self.verbose = bool(getattr(args, "verbose", False))
        self.approve = bool(getattr(args, "approve", False))
        self.overwrite = bool(getattr(args, "overwrite", False))
        self.workdir = Path(getattr(args, "workdir", None) or Path.cwd()).expanduser().resolve()
        self.tool_dir = TOOL_DIR
        self._modules = {}
        self._asset_cache = {}
        self.ffmpeg_filters = None
        self.environment = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "ffmpeg": None,
        }

        config_path = getattr(args, "config", None)
        self.config = {
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "assets": {
                "catalog": "asset-capability-catalog.json",
                "registry": "asset-model-registry.json",
                "presets": "asset-preset-recipes.json",
                "policy": "asset-validation-policy.json",
                "governance": "asset-governance-policy.json",
                "disclosures": "asset-disclosure-templates.json",
            },
            "paths": {"work_root": ".onv-work", "package_root": "packages"},
        }
        if config_path:
            user_config = common.json_load(config_path, label="config file")
            self.config = common.deep_merge(self.config, user_config)

    # -- modules ---------------------------------------------------------
    def load(self, name):
        if name not in self._modules:
            self._modules[name] = load_module(name)
        return self._modules[name]

    # -- assets ----------------------------------------------------------
    def asset_path(self, key, override=None):
        if override:
            path = Path(override)
        else:
            configured = (self.config.get("assets") or {}).get(key)
            if not configured:
                common.fail("E_RUNTIME", "no default path configured for asset '%s'" % key)
            path = Path(configured)
        if not path.is_absolute():
            path = TOOL_DIR / path
        if not path.exists():
            common.fail("E_NOT_FOUND", "asset file not found: %s" % path, asset=key, path=str(path))
        return path

    def asset(self, key, override=None):
        if key in self._asset_cache:
            return self._asset_cache[key]
        path = self.asset_path(key, override)
        doc = common.json_load(path, label="%s asset" % key)
        self._asset_cache[key] = doc
        self._asset_cache[key + ":path"] = str(path)
        return doc

    def ensure_assets(self):
        catalog = self.asset("catalog", getattr(self.args, "catalog", None))
        registry = self.asset("registry", getattr(self.args, "registry", None))
        presets = self.asset("presets", getattr(self.args, "presets", None))
        policy = self.asset("policy", getattr(self.args, "policy", None))
        governance = self.asset("governance", getattr(self.args, "governance", None))
        disclosures = self.asset("disclosures", getattr(self.args, "disclosures", None))
        self.catalog = catalog
        self.registry = registry
        self.presets = presets
        self.policy = policy
        self.governance_policy = governance
        self.disclosures = disclosures
        return self

    # -- job paths -------------------------------------------------------
    def job_paths(self, job_id):
        work_root = self.workdir / (self.config.get("paths", {}).get("work_root") or ".onv-work") / job_id
        package_root = self.workdir / (self.config.get("paths", {}).get("package_root") or "packages")
        paths = {
            "job_root": work_root,
            "scratch": work_root / "scratch",
            "logs": work_root / "logs",
            "reports": work_root / "reports",
            "packages": package_root,
            "state_file": work_root / "state.json",
            "plan_file": work_root / "plan.json",
        }
        for key in ("scratch", "logs", "reports", "packages"):
            paths[key].mkdir(parents=True, exist_ok=True)
        return paths

    def output_path(self, spec):
        directory = Path(spec["output"]["dir"])
        filename = spec["output"].get("filename")
        if not filename:
            container = (spec.get("delivery") or {}).get("container") or "mp4"
            filename = "%s.%s" % (spec["job_id"], str(container).lstrip("."))
        return directory / filename

    def wire_paths(self, job_id):
        self.paths = self.job_paths(job_id)
        return self.paths

    # -- media tool capability cache -------------------------------------
    def ffmpeg_caps(self):
        """Cached ffmpeg encoder/filter capability probe."""
        if getattr(self, "_ffmpeg_caps", None) is None:
            preflight_mod = self.load("preflight")
            runner = common.Runner(cancel_event=self.cancel_event, verbose=self.verbose)
            self._ffmpeg_caps = preflight_mod.ffmpeg_capabilities(self.config.get("ffmpeg", "ffmpeg"), runner) or {}
        return self._ffmpeg_caps

    # -- misc ------------------------------------------------------------
    def install_signal_handlers(self):
        def handler(signum, _frame):
            if self.cancel_event.is_set():
                raise SystemExit(common.EXIT_CANCELLED)
            self.cancel_event.set()
            print("\nCancellation requested; finishing the current step and cleaning up...", file=sys.stderr)

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# helpers shared by commands
# ---------------------------------------------------------------------------
def read_spec(ctx, spec_path):
    path = Path(spec_path)
    if not path.exists():
        ctx.common.fail("E_NOT_FOUND", "job spec not found: %s" % path, path=str(path))
    raw_text = ctx.common.read_text(path, label="job spec")
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        ctx.common.fail("E_SPEC", "job spec is not valid JSON: %s" % exc, path=str(path), line=exc.lineno)
    spec, warnings = ctx.common.validate_spec(raw, base_dir=path.parent)
    if getattr(ctx.args, "op", None):
        spec["op"] = ctx.args.op
    spec["spec_path"] = str(path.resolve())
    spec["spec_sha256"] = ctx.common.sha256_text(raw_text)
    return spec, warnings


def build_plan_for(ctx, spec, spec_warnings=None):
    planning = ctx.load("planning")
    catalog = planning.Catalog(ctx.catalog, ctx.common)
    registry = planning.Registry(ctx.registry, ctx.common)
    resolution = catalog.resolve(spec["capability"])
    if spec.get("op"):
        requested_op = str(spec["op"]).lower()
        if requested_op not in planning.ALL_OPS:
            ctx.common.fail("E_SPEC", "spec.op must be one of %s" % ", ".join(planning.ALL_OPS), op=spec["op"])
        resolution = dict(resolution)
        resolution["op"] = requested_op
        resolution["label"] = catalog.operations.get(requested_op, {}).get("label", requested_op)
        resolution["op_source"] = "spec.op override"
    else:
        resolution["op_source"] = resolution.get("kind") + " routing hint"

    preset = planning.resolve_preset(ctx.presets, spec["preset"], resolution["op"], ctx.common)
    ctx.preset = preset
    preflight_mod = ctx.load("preflight")
    runner = ctx.common.Runner(cancel_event=ctx.cancel_event, verbose=ctx.verbose)
    hardware = preflight_mod.detect_hardware(runner, spec)
    routing = registry.route(resolution["op"], spec, hardware)
    budget = planning.estimate_budget(spec, routing, preset, registry, mode=spec["mode"])
    policy = ctx.policy or {}
    stages = planning.build_stages(spec, resolution, routing, budget, preset, registry, policy)
    plan = planning.build_plan(spec, resolution, routing, budget, stages, ctx, warnings=spec_warnings)
    plan["hardware"] = hardware
    return plan


def load_or_build_plan(ctx, spec):
    if getattr(ctx.args, "plan", None):
        plan = ctx.common.json_load(ctx.args.plan, label="plan file")
        return plan
    return build_plan_for(ctx, spec)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_help(ctx, _args):
    text = HELP_TEXT % {"version": common.TOOL_VERSION}
    try:
        planning = ctx.load("planning")
        catalog = planning.Catalog(ctx.catalog, ctx.common)
        for category in catalog.categories:
            text += "  %2d. %-58s %3d concepts\n" % (category["number"], category["title"][:58], category["concept_count"])
        text += "\n  total: %d concepts in %d categories\n" % (len(catalog.concepts), len(catalog.categories))
    except common.OnvError as err:
        text += "  (catalog unavailable: %s)\n" % err.message
    if ctx.json_mode:
        payload = {
            "tool": common.TOOL_NAME,
            "version": common.TOOL_VERSION,
            "commands": COMMAND_LIST,
            "exit_codes": {
                "0": "ok",
                "2": "invalid input / bad spec",
                "3": "media validation failed",
                "4": "blocked by approval, consent or backend gate",
                "5": "internal error",
                "6": "cancelled",
            },
        }
        print(json.dumps({"ok": True, "result": payload}, indent=2))
        return common.EXIT_OK
    print(text)
    return common.EXIT_OK


def cmd_version(ctx, _args):
    payload = {"tool": common.TOOL_NAME, "version": common.TOOL_VERSION, "python": platform.python_version()}
    if ctx.json_mode:
        print(json.dumps(payload, indent=2))
    else:
        print("%s %s (Python %s)" % (common.TOOL_NAME, common.TOOL_VERSION, platform.python_version()))
    return common.EXIT_OK


def cmd_capabilities(ctx, args):
    planning = ctx.load("planning")
    catalog = planning.Catalog(ctx.catalog, ctx.common)
    if args.concept:
        concept = catalog.by_slug.get(args.concept) or catalog.by_id.get(args.concept)
        if not concept:
            ctx.common.fail("E_NOT_FOUND", "concept '%s' not found" % args.concept, hint="run capabilities --search <text>")
        result = concept
        lines = planning.describe_concept_text(catalog.resolve(concept["slug"]))
        _emit(ctx, result, lines)
        return common.EXIT_OK
    if args.search:
        matches = catalog.search(args.search, limit=args.limit)
        result = {
            "query": args.search,
            "match_count": len(matches),
            "matches": [
                {
                    "id": m["id"],
                    "slug": m["slug"],
                    "name": m["name"],
                    "op": m["op"],
                    "category": m["category_title"],
                }
                for m in matches
            ],
        }
        lines = ["%d match(es) for '%s'" % (len(matches), args.search), "-" * 78]
        for item in result["matches"]:
            lines.append("  %-9s %-46s %s" % (item["id"], item["slug"], item["op"]))
        _emit(ctx, result, lines)
        return common.EXIT_OK
    if args.category:
        category = catalog.by_category.get(args.category)
        if not category:
            by_number = [c for c in catalog.categories if str(c["number"]) == str(args.category)]
            category = by_number[0] if by_number else None
        if not category:
            ctx.common.fail("E_NOT_FOUND", "category '%s' not found" % args.category, available=[c["slug"] for c in catalog.categories])
        result = {
            "category": category["slug"],
            "title": category["title"],
            "number": category["number"],
            "concept_count": category["concept_count"],
            "default_op": category.get("default_op"),
            "concepts": category["concepts"],
        }
        lines = [
            "%d. %s  (%d concepts, default op '%s')" % (category["number"], category["title"], category["concept_count"], category.get("default_op")),
            "-" * 78,
        ]
        for concept in category["concepts"]:
            lines.append("  %-9s %-46s %s" % (concept["id"], concept["slug"], concept["op"]))
        _emit(ctx, result, lines)
        return common.EXIT_OK

    if args.op:
        subset = [c for c in catalog.concepts if c["op"] == args.op]
        result = {
            "operation": args.op,
            "concept_count": len(subset),
            "concepts": [{"id": c["id"], "slug": c["slug"], "name": c["name"], "category": c["category_title"]} for c in subset[: args.limit]],
        }
        lines = ["%d concept(s) routed to op '%s'" % (len(subset), args.op), "-" * 78]
        for item in result["concepts"]:
            lines.append("  %-9s %-46s %s" % (item["id"], item["slug"], item["category"]))
        _emit(ctx, result, lines)
        return common.EXIT_OK

    summary = catalog.summary()
    result = summary
    lines = [
        "Capability catalog %s - %d categories, %d concepts"
        % (catalog.version, summary["category_count"], summary["concept_count"]),
        "Source: %s" % summary["source"],
        "-" * 78,
    ]
    for category in summary["categories"]:
        lines.append("  %2d. %-62s %3d  [%s]" % (category["number"], category["title"][:62], category["concept_count"], category["default_op"]))
    lines.append("-" * 78)
    lines.append("Operations: %s" % ", ".join(op["op"] for op in summary["operations"]))
    lines.append("Drill down: capabilities --category <slug|number> | --search <text> | --op <op> | --concept <slug>")
    _emit(ctx, result, lines)
    return common.EXIT_OK


def cmd_describe(ctx, args):
    planning = ctx.load("planning")
    registry = planning.Registry(ctx.registry, ctx.common)
    catalog = planning.Catalog(ctx.catalog, ctx.common)
    resolution = catalog.resolve(args.capability)
    preset_name = args.preset or "preview"
    preset = planning.resolve_preset(ctx.presets, preset_name, resolution["op"], ctx.common)
    preflight_mod = ctx.load("preflight")
    runner = ctx.common.Runner(cancel_event=ctx.cancel_event, verbose=ctx.verbose)
    hardware = preflight_mod.detect_hardware(runner, {"hardware": {"vram_gb": args.vram_gb, "allow_cpu": True}})
    routing = registry.route(resolution["op"], {"models": {"model_id": args.model_id}}, hardware)
    result = {
        "capability": resolution,
        "routing": routing,
        "preset": {"name": preset_name, "label": preset.get("label"), "width": preset.get("width"), "height": preset.get("height"), "fps": preset.get("fps")},
        "proxy": routing.get("proxy_equivalent"),
        "hardware": {"vram_gb": hardware.get("vram_gb"), "source": hardware.get("source")},
    }
    lines = planning.describe_concept_text(resolution)
    if resolution["kind"] == "category":
        category = catalog.by_category[resolution["slug"]]
        lines.append("concepts  : %d (run capabilities --category %s)" % (category["concept_count"], resolution["slug"]))
    lines.append("")
    lines.append("routing   : %s (%s)" % (routing.get("model_id") or "no neural model", routing["reason"]))
    if routing.get("license"):
        lines.append("license   : %s [redistribution: %s]" % (routing["license"].get("name"), routing["license"].get("redistribution")))
    proxy = routing.get("proxy_equivalent") or {}
    if proxy:
        lines.append("proxy     : %s - %s" % (proxy.get("label"), proxy.get("summary")))
    lines.append("preset    : %s %sx%s @ %s fps" % (preset_name, preset.get("width"), preset.get("height"), preset.get("fps")))
    lines.append("")
    lines.append("next      : tools-onv.py spec-init --capability %s --out job.json" % (resolution.get("slug") or resolution["op"]))
    _emit(ctx, result, lines)
    return common.EXIT_OK


def cmd_doctor(ctx, args):
    preflight_mod = ctx.load("preflight")
    runner = ctx.common.Runner(cancel_event=ctx.cancel_event, verbose=ctx.verbose)
    caps = preflight_mod.ffmpeg_capabilities(ctx.config.get("ffmpeg", "ffmpeg"), runner)
    hardware = preflight_mod.detect_hardware(runner, {"hardware": {"allow_cpu": True}})
    ctx.environment["ffmpeg"] = caps["version"] if caps else None
    if caps:
        ctx.ffmpeg_filters = caps["filters"]
    assets = {}
    for key in ("catalog", "registry", "presets", "policy", "governance", "disclosures"):
        try:
            path = ctx.asset_path(key, getattr(ctx.args, key, None))
            doc = ctx.asset(key, getattr(ctx.args, key, None))
            version = None
            for version_key in ("catalog_version", "registry_version", "presets_version", "policy_version", "templates_version"):
                if isinstance(doc, dict) and version_key in doc:
                    version = doc[version_key]
                    break
            assets[key] = {"path": str(path), "version": version, "bytes": path.stat().st_size}
        except common.OnvError as err:
            assets[key] = {"error": err.message}
    required_filters = ["zoompan", "minterpolate", "hqdn3d", "deflicker", "subtitles", "loudnorm", "blackdetect", "freezedetect", "eq", "curves"]
    filters = {name: (name in (caps["filters"] if caps else set())) for name in required_filters}
    encoders = {name: (name in (caps["encoders"] if caps else set())) for name in ("libx264", "libx265", "aac", "libopus", "libsvtav1")}
    free = common.disk_free_bytes(ctx.workdir)
    result = {
        "tool": {"name": common.TOOL_NAME, "version": common.TOOL_VERSION, "path": str(TOOL_DIR)},
        "python": {"version": platform.python_version(), "executable": sys.executable, "supported": sys.version_info >= MIN_PYTHON},
        "ffmpeg": {
            "path": caps["path"] if caps else None,
            "version": caps["version"] if caps else None,
            "encoders": encoders,
            "filters": filters,
        },
        "hardware": hardware,
        "workdir": str(ctx.workdir),
        "workdir_writable": os.access(str(ctx.workdir), os.W_OK),
        "disk_free_gb": round(free / 1024.0 / 1024.0 / 1024.0, 2) if free else None,
        "assets": assets,
        "network_required": False,
    }
    lines = [
        "Environment report for %s %s" % (common.TOOL_NAME, common.TOOL_VERSION),
        "-" * 78,
        "  python      : %-8s %s" % (platform.python_version(), "supported" if result["python"]["supported"] else "TOO OLD (need 3.8+)"),
        "  ffmpeg      : %s" % (caps["version"] if caps else "MISSING"),
        "  ffprobe     : %s" % ("available" if __import__("shutil").which(ctx.config.get("ffprobe", "ffprobe")) else "MISSING"),
        "  gpu         : %s" % (", ".join("%s (%.1f GB)" % (g["name"], g["vram_gb"]) for g in hardware.get("gpus", [])) or "none detected"),
        "  ram         : %s GB" % (hardware.get("ram_gb") or "unknown"),
        "  workdir     : %s (%s)" % (ctx.workdir, "writable" if result["workdir_writable"] else "NOT WRITABLE"),
        "  disk free   : %s" % (common.human_bytes(free) if free else "unknown"),
        "-" * 78,
        "  ffmpeg filters: %s" % ", ".join("%s=%s" % (k, "yes" if v else "no") for k, v in filters.items()),
        "  ffmpeg encoders: %s" % ", ".join("%s=%s" % (k, "yes" if v else "no") for k, v in encoders.items()),
        "-" * 78,
    ]
    for key, info in assets.items():
        if "error" in info:
            lines.append("  asset %-12s ERROR %s" % (key, info["error"]))
        else:
            lines.append("  asset %-12s v%-6s %s" % (key, info.get("version"), info["path"]))
    missing = [name for name, ok in filters.items() if not ok]
    if missing:
        lines.append("-" * 78)
        lines.append("  note: filters unavailable in this build: %s" % ", ".join(missing))
    _emit(ctx, result, lines)
    return common.EXIT_OK


def cmd_spec_init(ctx, args):
    template_path = ctx.asset_path("catalog").parent / "asset-job-spec.template.json"
    if not template_path.exists():
        ctx.common.fail("E_NOT_FOUND", "job spec template missing: %s" % template_path)
    template = ctx.common.json_load(template_path, label="job spec template")
    if args.capability:
        template["capability"] = args.capability
    if args.job_id:
        template["job_id"] = args.job_id
    if args.preset:
        template["preset"] = args.preset
    if args.mode:
        template["mode"] = args.mode
    if args.input:
        template["inputs"] = [{"role": args.input_role, "path": args.input}]
    if args.package:
        template["delivery"]["package"] = True
    out = Path(args.out)
    if out.exists() and not args.force:
        ctx.common.fail("E_INPUT", "output spec already exists: %s" % out, remedy="Pass --force to overwrite.")
    ctx.common.json_write(out, template)
    result = {"path": str(out), "spec": template}
    lines = ["Wrote job spec template: %s" % out, "", "Edit capability, inputs and params, then run:", "  python3 tools-onv.py plan --spec %s" % out]
    _emit(ctx, result, lines)
    return common.EXIT_OK


def cmd_plan(ctx, args):
    spec, warnings = read_spec(ctx, args.spec)
    ctx.wire_paths(spec["job_id"])
    plan = build_plan_for(ctx, spec, warnings)
    path = Path(args.out) if args.out else ctx.paths["plan_file"]
    ctx.common.json_write(path, plan)
    result = {
        "plan_path": str(path),
        "job_id": plan["job_id"],
        "capability": {"slug": plan["capability"].get("slug"), "name": plan["capability"].get("name"), "kind": plan["capability"]["kind"]},
        "operation": plan["operation"],
        "mode": plan["mode"],
        "preset": plan["preset"],
        "model_id": plan["routing"].get("model_id"),
        "budget": plan["budget"],
        "stages": plan["stages"],
        "approvals_required": plan["approvals_required"],
        "warnings": plan["warnings"],
    }
    lines = [
        "Plan for job '%s' -> %s" % (plan["job_id"], path),
        "-" * 78,
        "  capability : %s [%s]" % (plan["capability"].get("name"), plan["operation"]),
        "  mode       : %s (preset %s)" % (plan["mode"], plan["preset"]),
        "  model      : %s (%s)" % (plan["routing"].get("model_id") or "none - local operation", plan["routing"]["reason"]),
        "  resolution : %sx%s @ %s fps, %s frames = %.2fs"
        % (
            plan["budget"]["resolution"]["width"],
            plan["budget"]["resolution"]["height"],
            plan["budget"]["fps"],
            plan["budget"]["frames"],
            plan["budget"]["duration_s"],
        ),
        "  budget     : vram ~%.1f GB, ram ~%.1f GB, output ~%.1f MB, est %s"
        % (
            plan["budget"]["vram_gb_estimate"],
            plan["budget"]["ram_gb_estimate"],
            plan["budget"]["disk"]["output_mb"],
            common.human_duration(plan["budget"]["time"]["total_s"]),
        ),
        "-" * 78,
        "  stages:",
    ]
    for index, stage in enumerate(plan["stages"], start=1):
        lines.append(
            "   %2d. %-14s %-14s timeout=%-5ss%s"
            % (index, stage["id"], stage["executor"], stage["timeout_s"], "  [approval]" if stage["approval_required"] else "")
        )
    lines.append("-" * 78)
    lines.append("  approvals required : %s" % (", ".join(plan["approvals_required"]) or "none"))
    lines.append("  consent required   : %s" % plan["governance"]["consent_required"])
    if plan["warnings"]:
        for warning in plan["warnings"]:
            lines.append("  warning: %s" % warning)
    if args.show_command:
        try:
            exec_mod = ctx.load("exec")
            argv, out, _fields = exec_mod.command_from_template(ctx, spec, plan, plan["stages"][1] if len(plan["stages"]) > 1 else plan["stages"][0])
            lines.append("  resolved backend command: %s" % " ".join(argv))
            lines.append("  expected backend output : %s" % out)
        except ctx.common.OnvError as err:
            lines.append("  backend command: unavailable (%s)" % err.message)
    lines.append("")
    lines.append("next: python3 tools-onv.py run --spec %s%s" % (args.spec, " --approve" if plan["approvals_required"] else ""))
    _emit(ctx, result, lines)
    return common.EXIT_OK


def cmd_preflight(ctx, args):
    spec, warnings = read_spec(ctx, args.spec)
    ctx.wire_paths(spec["job_id"])
    plan = load_or_build_plan(ctx, spec)
    preflight_mod = ctx.load("preflight")
    report = preflight_mod.run_preflight(ctx, spec, plan)
    path = ctx.paths["reports"] / ("preflight-%s.json" % spec["job_id"])
    ctx.common.json_write(path, report)
    report["report_path"] = str(path)
    if ctx.json_mode:
        print(json.dumps({"ok": True, "result": report}, indent=2))
    else:
        for line in preflight_mod.preflight_lines(report):
            print(line)
        print("report: %s" % path)
    return common.EXIT_OK if report["ready"] else common.EXIT_BLOCKED


def cmd_run(ctx, args):
    spec, warnings = read_spec(ctx, args.spec)
    ctx.wire_paths(spec["job_id"])
    plan = load_or_build_plan(ctx, spec)
    if args.validate_only:
        spec["mode"] = "validate-only"
        plan["mode"] = "validate-only"
        for stage in plan["stages"]:
            if stage["executor"] == "backend":
                stage["executor"] = "internal"
    exec_mod = ctx.load("exec")
    if args.dry_run:
        result = {
            "dry_run": True,
            "job_id": spec["job_id"],
            "stages": [{"id": s["id"], "executor": s["executor"], "approval_required": s["approval_required"]} for s in plan["stages"]],
        }
        lines = ["Dry run: %d stage(s) would execute for job '%s'" % (len(plan["stages"]), spec["job_id"])]
        for stage in plan["stages"]:
            lines.append("  - %-14s %-16s%s" % (stage["id"], stage["executor"], "  [approval]" if stage["approval_required"] else ""))
        _emit(ctx, result, lines)
        return common.EXIT_OK

    outcome = exec_mod.run_job(ctx, spec, plan, resume=bool(args.resume))
    state = outcome["state"].data
    result = {
        "job_id": spec["job_id"],
        "status": outcome["status"],
        "reason": outcome.get("reason"),
        "output": state.get("current_media"),
        "validation_passed": state.get("validation_passed"),
        "state_file": str(ctx.paths["state_file"]),
        "stages": outcome["results"],
        "artifacts": state.get("artifacts", []),
        "proxy_path_used": bool(state.get("proxy_stages")),
    }
    lines = ["Run of job '%s' finished with status %s" % (spec["job_id"], outcome["status"].upper()), "-" * 78]
    for record in outcome["results"]:
        lines.append("  [%-9s] %-14s %s" % (record.get("status", "?").upper(), record.get("stage"), record.get("message") or ""))
    lines.append("-" * 78)
    if state.get("current_media") and outcome["status"] == "succeeded":
        lines.append("  delivery   : %s" % state["current_media"])
    if state.get("validation_report"):
        lines.append("  validation : %s (%s)" % (state["validation_report"], "passed" if state.get("validation_passed") else "failed"))
    if state.get("proxy_stages"):
        lines.append("  note       : proxy (non-neural) path used for %s" % ", ".join(state["proxy_stages"]))
    lines.append("  state      : %s" % ctx.paths["state_file"])
    if outcome.get("reason"):
        lines.append("  reason     : %s" % outcome["reason"])
    payload = _emit(ctx, result, lines)
    if outcome["status"] == "succeeded":
        return common.EXIT_OK
    if outcome["status"] == "cancelled":
        return common.EXIT_CANCELLED
    if outcome["status"] == "blocked":
        return common.EXIT_BLOCKED
    if state.get("validation_passed") is False:
        return common.EXIT_VALIDATION
    return common.EXIT_RUNTIME


def cmd_validate(ctx, args):
    spec = None
    plan = None
    if args.spec:
        spec, _warnings = read_spec(ctx, args.spec)
        ctx.wire_paths(spec["job_id"])
        plan = load_or_build_plan(ctx, spec)
    else:
        ctx.wire_paths(args.job_id or "validate")
    validate_mod = ctx.load("validate")
    runner = ctx.common.Runner(cancel_event=ctx.cancel_event, log_dir=ctx.paths["logs"], verbose=ctx.verbose)
    report = validate_mod.validate_media(ctx, runner, spec or {}, plan, Path(args.file))
    report["validated_at"] = ctx.common.utcnow()
    out = Path(args.out) if args.out else ctx.paths["reports"] / ("validation-%s.json" % Path(args.file).stem)
    ctx.common.json_write(out, report)
    report["report_path"] = str(out)
    if ctx.json_mode:
        print(json.dumps({"ok": report["passed"], "result": report}, indent=2))
    else:
        for line in validate_mod.validate_lines(report):
            print(line)
        print("report: %s" % out)
    return common.EXIT_OK if report["passed"] else common.EXIT_VALIDATION


def cmd_ledger(ctx, args):
    governance = ctx.load("governance")
    ledger_path = Path(args.file)
    if ledger_path.is_absolute() is False:
        ledger_path = (ctx.workdir / ledger_path)
    if args.action == "list":
        ledger = governance.load_ledger(ctx, ledger_path)
        if ctx.json_mode:
            print(json.dumps({"ok": True, "result": {"ledger": str(ledger_path), "records": ledger["records"]}}, indent=2))
        else:
            for line in governance.ledger_lines(ledger, ledger_path):
                print(line)
        return common.EXIT_OK
    if args.action == "add":
        if not ctx.approve:
            ctx.common.fail(
                "E_APPROVAL",
                "writing consent/rights records requires explicit approval",
                remedy="Re-run with --approve once the consent evidence has been verified.",
            )
        subjects = args.subject or []
        if not subjects:
            ctx.common.fail("E_INPUT", "ledger add requires --subject (repeatable)")
        records = [
            governance.add_record(
                ctx,
                ledger_path,
                subject_id=subject,
                status=args.status,
                scope=args.scope,
                evidence=args.evidence,
                license_name=args.license,
                allowed_use=args.allowed_use,
                kind=args.kind,
                expires_utc=args.expires,
                notes=args.notes,
                granted_by=args.granted_by,
            )
            for subject in subjects
        ]
        result = {"ledger": str(ledger_path), "records": records}
        lines = [
            "Recorded %s for %s (scope=%s, evidence=%s)"
            % (args.status, ", ".join(subjects), args.scope, args.evidence),
            "ledger: %s" % ledger_path,
        ]
        _emit(ctx, result, lines)
        return common.EXIT_OK
    if args.action == "check":
        subjects = args.subject or []
        if not subjects:
            ctx.common.fail("E_INPUT", "ledger check requires --subject (repeatable)")
        ledger = governance.load_ledger(ctx, ledger_path)
        gate = governance.evaluate_consent(ledger, subjects, args.scope)
        result = {"ledger": str(ledger_path), "gate": gate}
        lines = ["Consent check: %s" % ("PASS" if gate["ok"] else "BLOCKED")]
        for reason in gate["reasons"]:
            lines.append("  - %s" % reason)
        for subject, info in gate["subjects"].items():
            lines.append("  %s: %s" % (subject, info["status"]))
        _emit(ctx, result, lines)
        return common.EXIT_OK if gate["ok"] else common.EXIT_BLOCKED
    ctx.common.fail("E_USAGE", "unknown ledger action '%s'" % args.action)
    return common.EXIT_INPUT


def cmd_archive_verify(ctx, args):
    governance = ctx.load("governance")
    path = Path(args.archive)
    if not path.exists():
        ctx.common.fail("E_NOT_FOUND", "archive not found: %s" % path)
    check = governance.verify_archive(path)
    result = {"archive": str(path), "bytes": path.stat().st_size, **check}
    lines = ["Archive %s: %s (%d members)" % (path, "VALID" if check["ok"] else "INVALID", check["member_count"])]
    for item in check["files"]:
        lines.append("  %-40s %8d bytes" % (item["name"], item["bytes"]))
    for problem in check["problems"]:
        lines.append("  problem: %s" % problem)
    _emit(ctx, result, lines)
    return common.EXIT_OK if check["ok"] else common.EXIT_INPUT


def cmd_selftest(ctx, args):
    selftest = ctx.load("selftest")
    report = selftest.run_selftest(ctx, keep=bool(args.keep), timeout_s=int(args.timeout))
    if ctx.json_mode:
        print(json.dumps({"ok": report["passed"], "result": report}, indent=2))
    else:
        for line in selftest.selftest_lines(report):
            print(line)
    return common.EXIT_OK if report["passed"] else common.EXIT_VALIDATION


def _emit(ctx, result, lines):
    if ctx.json_mode:
        print(json.dumps({"ok": True, "tool": common.TOOL_NAME, "version": common.TOOL_VERSION, "result": result}, indent=2))
    elif not ctx.quiet:
        for line in lines:
            print(line)
    return result


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
COMMAND_LIST = [
    {"name": "help", "summary": "show this help and the capability list"},
    {"name": "capabilities", "summary": "list catalog categories, concepts, operations or search"},
    {"name": "describe", "summary": "explain one capability plus its default routing and proxy"},
    {"name": "doctor", "summary": "environment/dependency/asset diagnostics"},
    {"name": "spec-init", "summary": "write a job-spec template"},
    {"name": "plan", "summary": "resolve capability, route model, budget and stage graph"},
    {"name": "preflight", "summary": "hardware, tool, model-file and rights checks"},
    {"name": "run", "summary": "execute the planned pipeline end to end"},
    {"name": "validate", "summary": "validate any media file against the policy"},
    {"name": "ledger", "summary": "list/add/check consent and rights records"},
    {"name": "archive-verify", "summary": "inspect a delivery ZIP for safety and completeness"},
    {"name": "selftest", "summary": "run the built-in end-to-end regression suite"},
    {"name": "version", "summary": "print tool version"},
]

HELP_TEXT = """%(version)s  offline-neural-video-pipeline

Local, offline-first control plane, executor and validator for the Full Offline
Neural Video Skill Catalog (28 categories, 1235 capability concepts).

USAGE
  python3 tools-onv.py <command> [options]

COMMANDS
  help                 Show this help
  capabilities         List catalog categories, concepts, operations or search
  describe <cap>       Explain a capability with routing, license and proxy plan
  doctor               Environment, dependency and asset diagnostics
  spec-init            Write a job-spec template to edit
  plan                 Resolve capability -> model route -> budgets -> stage graph
  preflight            Hardware, tool, model-file and rights checks
  run                  Execute the planned pipeline (real ffmpeg stages + backend)
  validate             Validate a media file against asset-validation-policy.json
  ledger               Consent/rights ledger: list | add | check
  archive-verify       Inspect a delivery ZIP (no nested archives, safe paths)
  selftest             Built-in end-to-end regression suite
  version              Print the tool version

GLOBAL OPTIONS
  --json               Machine-readable JSON output
  --config PATH        Config file with ffmpeg/ffprobe paths and asset overrides
  --catalog PATH       Override asset-capability-catalog.json
  --registry PATH      Override asset-model-registry.json
  --presets PATH       Override asset-preset-recipes.json
  --policy PATH        Override asset-validation-policy.json
  --governance PATH    Override asset-governance-policy.json
  --disclosures PATH   Override asset-disclosure-templates.json
  --workdir PATH       Project working directory (default: current directory)
  --approve            Approve gated actions (backend dispatch, packaging, ledger)
  --overwrite          Allow replacing an existing output (also needs --approve)
  --quiet / --verbose  Output control

EXAMPLES
  python3 tools-onv.py capabilities --category 1
  python3 tools-onv.py capabilities --search "lip sync"
  python3 tools-onv.py describe offline-image-to-video-engine --vram-gb 12
  python3 tools-onv.py spec-init --capability offline-image-to-video-engine --out job.json
  python3 tools-onv.py plan --spec job.json --show-command
  python3 tools-onv.py preflight --spec job.json
  python3 tools-onv.py run --spec job.json --approve
  python3 tools-onv.py run --spec job.json --resume
  python3 tools-onv.py validate --file outputs/demo-001.mp4 --spec job.json
  python3 tools-onv.py ledger add --file ledger/rights-ledger.json --subject actor-001 \\
      --status granted --scope internal-preview --evidence consent-2026.pdf --approve
  python3 tools-onv.py archive-verify --archive packages/demo-001-delivery.zip
  python3 tools-onv.py selftest

EXIT CODES
  0 ok   2 invalid input   3 validation failed   4 blocked   5 internal   6 cancelled

CAPABILITY SUMMARY
""" % {"version": "v" + common.TOOL_VERSION}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-onv.py",
        description="Offline neural video pipeline: plan, execute, validate and govern local neural-video jobs.",
        add_help=True,
    )
    parser.add_argument("command", nargs="?", default="help", help="command to run (see help)")
    parser.add_argument("capability_positional", nargs="?", help="capability for 'describe'")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser.add_argument("--quiet", action="store_true", help="suppress human output")
    parser.add_argument("--verbose", action="store_true", help="verbose diagnostics")
    parser.add_argument("--approve", action="store_true", help="approve gated actions")
    parser.add_argument("--overwrite", action="store_true", help="allow replacing existing outputs")
    parser.add_argument("--workdir", help="project working directory")
    parser.add_argument("--config", help="JSON config file")
    parser.add_argument("--catalog", help="capability catalog asset path")
    parser.add_argument("--registry", help="model registry asset path")
    parser.add_argument("--presets", help="preset asset path")
    parser.add_argument("--policy", help="validation policy asset path")
    parser.add_argument("--governance", help="governance policy asset path")
    parser.add_argument("--disclosures", help="disclosure templates asset path")

    # per-command options (flat namespace keeps the CLI simple and stable)
    parser.add_argument("--capability", help="spec-init: capability slug, id or operation")
    parser.add_argument("--category", help="capabilities: category slug or number")
    parser.add_argument("--search", help="capabilities: free-text search")
    parser.add_argument("--op", help="capabilities: filter by operation, or override the operation in a spec")
    parser.add_argument("--concept", help="capabilities: show one concept by slug or id")
    parser.add_argument("--limit", type=int, default=25, help="capabilities: result limit (default 25)")
    parser.add_argument("--preset", help="preset name (draft|preview|delivery|archive)")
    parser.add_argument("--model-id", help="force a registry model id")
    parser.add_argument("--vram-gb", type=float, help="assumed GPU VRAM in GB")
    parser.add_argument("--out", help="output path for generated files")
    parser.add_argument("--force", action="store_true", help="spec-init: overwrite existing file")
    parser.add_argument("--job-id", help="job id for spec-init / validate")
    parser.add_argument("--mode", help="spec-init: execution mode")
    parser.add_argument("--input", help="spec-init: input media path")
    parser.add_argument("--input-role", default="image", help="spec-init: input role (default image)")
    parser.add_argument("--package", action="store_true", help="spec-init: enable delivery packaging")
    parser.add_argument("--spec", help="job spec path")
    parser.add_argument("--plan", help="plan file path (load instead of rebuilding)")
    parser.add_argument("--show-command", action="store_true", help="plan: print the resolved backend command")
    parser.add_argument("--file", help="media file (validate) or ledger file (ledger)")
    parser.add_argument("--resume", action="store_true", help="run: resume from saved state")
    parser.add_argument("--dry-run", action="store_true", help="run: show stages without executing")
    parser.add_argument("--validate-only", action="store_true", help="run: skip neural stages, validate only")
    parser.add_argument("--keep", action="store_true", help="selftest: keep the temporary workspace")
    parser.add_argument("--timeout", type=int, default=600, help="selftest: overall timeout seconds")
    parser.add_argument("--action", choices=["list", "add", "check"], help="ledger action")
    parser.add_argument("--subject", action="append", help="ledger: subject id (repeatable)")
    parser.add_argument("--status", default="granted", help="ledger add: granted|denied|withdrawn|unknown")
    parser.add_argument("--scope", default="internal-preview", help="ledger: consent scope / allowed use")
    parser.add_argument("--evidence", help="ledger add: evidence reference")
    parser.add_argument("--license", help="ledger add: license name")
    parser.add_argument("--allowed-use", help="ledger add: allowed use")
    parser.add_argument("--kind", default="person", help="ledger add: subject kind")
    parser.add_argument("--expires", help="ledger add: expiry (ISO-8601 UTC)")
    parser.add_argument("--granted-by", help="ledger add: who recorded the consent")
    parser.add_argument("--notes", help="ledger add: free-text note")
    parser.add_argument("--archive", help="archive-verify: delivery ZIP path")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "help"
    if command == "describe" and not getattr(args, "capability_positional", None):
        parser.error("describe requires a capability argument")
    if args.overwrite and not args.approve:
        print("ERROR [E_APPROVAL] --overwrite also requires --approve", file=sys.stderr)
        return common.EXIT_BLOCKED
    ctx = Ctx(args)
    ctx.install_signal_handlers()

    handlers = {
        "help": cmd_help,
        "version": cmd_version,
        "capabilities": cmd_capabilities,
        "describe": cmd_describe,
        "doctor": cmd_doctor,
        "spec-init": cmd_spec_init,
        "plan": cmd_plan,
        "preflight": cmd_preflight,
        "run": cmd_run,
        "validate": cmd_validate,
        "ledger": cmd_ledger,
        "archive-verify": cmd_archive_verify,
        "selftest": cmd_selftest,
    }
    if command not in handlers:
        print("ERROR [E_USAGE] unknown command '%s'" % command, file=sys.stderr)
        print("Run: python3 tools-onv.py help", file=sys.stderr)
        return common.EXIT_INPUT

    try:
        ctx.ensure_assets()
        handler = handlers[command]
        if command == "describe":
            args.capability = args.capability_positional
        if command == "ledger" and not args.action and args.capability_positional:
            args.action = args.capability_positional
        if command == "help":
            return cmd_help(ctx, args)
        if command == "version":
            return cmd_version(ctx, args)
        return handler(ctx, args)
    except common.OnvError as err:
        payload = {
            "ok": False,
            "tool": common.TOOL_NAME,
            "version": common.TOOL_VERSION,
            "command": command,
            "generated_at": common.utcnow(),
            "error": err.as_dict(),
        }
        if ctx.json_mode:
            print(json.dumps(payload, indent=2))
        else:
            print("ERROR [%s] %s" % (err.code, err.message), file=sys.stderr)
            for key, value in sorted((err.details or {}).items()):
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                print("  %s: %s" % (key, value), file=sys.stderr)
        return err.exit_code
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return common.EXIT_CANCELLED
    except BrokenPipeError:
        return common.EXIT_OK
    except Exception as exc:  # noqa: BLE001 - last-resort guard, never hide the trace id
        import traceback

        if ctx.json_mode:
            print(json.dumps({"ok": False, "error": {"code": "E_RUNTIME", "message": str(exc)}}, indent=2))
        else:
            print("ERROR [E_RUNTIME] %s" % exc, file=sys.stderr)
            if ctx.verbose:
                traceback.print_exc()
            else:
                print("  hint: re-run with --verbose for the full traceback", file=sys.stderr)
        return common.EXIT_RUNTIME


if __name__ == "__main__":
    sys.exit(main())
