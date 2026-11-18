#!/usr/bin/env python3
"""
tools-music_ai_studio_cli.py  --  PRIMARY ENTRY POINT for music-ai-skill-studio.

Turns the Music + AI skill catalog (or any plain-text / Markdown / copied-HTML
capability list) into executable, reviewable production cases:

    case plans, Markdown briefs, editable checklists, delivery handoff packs,
    optional WAV quality control, and approval gating for sensitive actions.

Run:
    python3 music-ai-skill-studio/tools-music_ai_studio_cli.py --help
    python3 music-ai-skill-studio/tools-music_ai_studio_cli.py capabilities

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import importlib

try:
    core = importlib.import_module("tools-music_ai_studio")
except ModuleNotFoundError as exc:  # pragma: no cover - packaging guard
    sys.stderr.write(
        "ERROR: cannot import tools-music_ai_studio.py from "
        f"{_HERE}. Keep both files in the same Skill Tool folder.\n"
        f"({exc})\n"
    )
    raise SystemExit(3)

VERSION = "1.0.0"
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_LIMIT = 4
EXIT_APPROVAL = 5
EXIT_UNSUPPORTED = 6
EXIT_INTERRUPTED = 130

COMMAND_CATALOG = [
    {
        "command": "capabilities",
        "purpose": "List every command, the capability classes it implements, input and output.",
        "inputs": "none",
        "outputs": "JSON or text command map",
    },
    {
        "command": "list",
        "purpose": "Parse a catalog source into categories and capabilities (coverage check).",
        "inputs": "--source FILE | --text STRING [--category KEY] [--format json|text|markdown]",
        "outputs": "structured catalog listing",
    },
    {
        "command": "plan",
        "purpose": "Build a production case: plan JSON, Markdown brief, checklist CSV, handoff manifest.",
        "inputs": "--category / --capability / --title + --intake FILE_OR_INLINE [--source FILE]",
        "outputs": "case plan files in --out DIR (or --out-file for a single plan JSON)",
    },
    {
        "command": "verify-intake",
        "purpose": "Validate an intake document and approval tokens without producing files.",
        "inputs": "--intake FILE_OR_INLINE [--approve TOKEN ...]",
        "outputs": "normalized intake + blocking flags + approval status",
    },
    {
        "command": "batch",
        "purpose": "Render many cases from a jobs file, isolated per job, with retries and a report.",
        "inputs": "--jobs FILE --out DIR [--retries N] [--timeout SECONDS]",
        "outputs": "per-job folders + batch-report.json",
    },
    {
        "command": "qc-audio",
        "purpose": "WAV file integrity and basic level QC (peak, RMS, crest, clips, DC, duration).",
        "inputs": "--audio FILE [--audio-dir DIR] [--qc-profile FILE] [--out FILE]",
        "outputs": "QC report JSON (or text)",
    },
    {
        "command": "trim-silence",
        "purpose": "Trim leading/trailing silence from a 16-bit PCM WAV into a new file.",
        "inputs": "--audio FILE --out-file FILE [--threshold-dbfs -60]",
        "outputs": "new WAV + JSON summary",
    },
    {
        "command": "pack",
        "purpose": "Package a case folder into a validated, deterministic ZIP delivery.",
        "inputs": "--input DIR --archive FILE",
        "outputs": "delivery ZIP + manifest summary",
    },
    {
        "command": "unpack",
        "purpose": "Safely extract a delivery ZIP (traversal-guarded) and report contents.",
        "inputs": "--archive FILE --dest DIR",
        "outputs": "extracted files + report",
    },
]

# --------------------------------------------------------------------------
# Infrastructure: output, errors, time limits, retries
# --------------------------------------------------------------------------


class Output:
    def __init__(self, as_json: bool, quiet: bool = False) -> None:
        self.as_json = as_json
        self.quiet = quiet

    def emit(self, payload: Any, *, text: Optional[str] = None) -> None:
        if self.as_json:
            sys.stdout.write(core.dumps(payload) + "\n")
        else:
            sys.stdout.write((text if text is not None else core.dumps(payload)) + "\n")
        sys.stdout.flush()

    def info(self, msg: str) -> None:
        if not self.quiet:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()


@contextlib.contextmanager
def time_limit(seconds: Optional[float], label: str = "operation"):
    """Best-effort wall-clock guard. POSIX only; a documented limitation elsewhere."""
    if not seconds or seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum, frame):  # noqa: ARG001
        raise core.LimitExceeded(f"{label} exceeded timeout of {seconds}s", code="timeout")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def with_retries(fn, *, retries: int, label: str, sleep_seconds: float = 0.2):
    attempt = 0
    last: Optional[Exception] = None
    while attempt <= retries:
        try:
            return fn()
        except (core.ApprovalRequired, core.ValidationError, core.NotSupported):
            raise
        except (core.InputError, core.LimitExceeded, OSError) as exc:
            last = exc
            attempt += 1
            if attempt > retries:
                break
            time.sleep(sleep_seconds * attempt)
    assert last is not None
    raise last


def resolve_input(inline: Optional[str], path: Optional[str], *, what: str,
                  max_bytes: int = core.DEFAULT_LIMITS["max_input_bytes"]) -> Any:
    """Accept either an inline JSON string or a path to a JSON file."""
    if inline and path:
        raise core.InputError(f"pass either inline {what} or --{what}-file, not both",
                             code="conflicting_inputs")
    if inline:
        text = inline.strip()
        if not text:
            raise core.InputError(f"inline {what} is empty", code="empty_input")
        if len(text.encode("utf-8", "ignore")) > max_bytes:
            raise core.LimitExceeded(f"inline {what} exceeds {max_bytes} bytes")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise core.InputError(f"invalid JSON in inline {what}: {exc.msg} at char {exc.pos}",
                                 code="invalid_json")
    if path:
        return core.load_json_file(path, max_bytes=max_bytes)
    raise core.InputError(f"missing {what}: pass --{what} '<json>' or --{what}-file PATH",
                         code="missing_input")


def resolve_text(inline: Optional[str], path: Optional[str], *,
                 max_bytes: int = core.DEFAULT_LIMITS["max_input_bytes"]) -> str:
    if inline and path:
        raise core.InputError("pass either --text or --source, not both", code="conflicting_inputs")
    if inline:
        if len(inline.encode("utf-8", "ignore")) > max_bytes:
            raise core.LimitExceeded(f"--text exceeds {max_bytes} bytes")
        return inline
    if path:
        return core.read_text_file(path, max_bytes=max_bytes)
    raise core.InputError("missing catalog source: pass --source PATH or --text '...'",
                         code="missing_input")


def load_workflow(path: Optional[str]) -> Dict[str, Any]:
    wf = json.loads(json.dumps(core.DEFAULT_WORKFLOW))  # deep copy
    if not path:
        return wf
    data = core.load_json_file(path)
    if not isinstance(data, dict):
        raise core.InputError("workflow config must be a JSON object", code="invalid_config")
    for key, value in data.items():
        if key not in wf:
            wf[key] = value
            continue
        if isinstance(wf[key], dict) and isinstance(value, dict):
            wf[key].update(value)
        else:
            wf[key] = value
    # sanity-check limits
    lim = wf.get("limits", {})
    for key in ("max_input_bytes", "max_catalog_entries", "max_batch_jobs",
                "max_audio_bytes", "max_audio_scan_seconds"):
        if key in lim:
            try:
                lim[key] = float(lim[key]) if "seconds" in key else int(lim[key])
            except (TypeError, ValueError):
                raise core.InputError(f"workflow limits.{key} must be numeric", code="invalid_config")
            if lim[key] <= 0:
                raise core.InputError(f"workflow limits.{key} must be positive", code="invalid_config")
    r = wf.get("retries", 0)
    if not isinstance(r, int) or r < 0 or r > 5:
        raise core.InputError("workflow retries must be an integer 0..5", code="invalid_config")
    return wf


QC_THRESHOLD_KEYS = ("clip_ceiling_dbfs", "minimum_duration_seconds",
                     "maximum_duration_seconds", "require_non_silent")


def load_qc_profile(path: Optional[str], workflow: Dict[str, Any],
                    name: Optional[str] = None) -> Dict[str, Any]:
    """Thresholds for qc-audio: workflow defaults, then a profile file or bundle."""
    qc = {k: workflow.get("qc", core.DEFAULT_WORKFLOW["qc"])[k] for k in QC_THRESHOLD_KEYS}
    if not path:
        return qc
    data = core.load_json_file(path)
    if not isinstance(data, dict):
        raise core.InputError("QC profile must be a JSON object", code="invalid_config")
    bundle = data.get("profiles")
    if isinstance(bundle, dict):
        chosen = name or data.get("active_profile") or "default"
        if chosen not in bundle:
            raise core.InputError(
                f"QC profile '{chosen}' not in {os.path.basename(path)}",
                code="invalid_config", details={"available": sorted(bundle)})
        data = bundle[chosen]
        if not isinstance(data, dict):
            raise core.InputError("QC profile entry must be an object", code="invalid_config")
    for key in QC_THRESHOLD_KEYS:
        if key in data:
            value = data[key]
            if key == "require_non_silent":
                if not isinstance(value, bool):
                    raise core.InputError("QC threshold require_non_silent must be true/false",
                                          code="invalid_config")
                qc[key] = value
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise core.InputError(f"QC threshold {key} must be numeric", code="invalid_config")
            qc[key] = value
    if qc["minimum_duration_seconds"] > qc["maximum_duration_seconds"]:
        raise core.InputError("QC profile minimum_duration_seconds exceeds maximum", code="invalid_config")
    return qc


# --------------------------------------------------------------------------
# Resolving what a case covers
# --------------------------------------------------------------------------


def resolve_targets(args) -> Tuple[Optional[core.Catalog], List[Dict[str, Any]], str, Optional[str], Optional[str]]:
    """Return (catalog, profiles, kind, category_label, capability_label)."""
    wants_catalog = bool(args.source or args.text)
    catalog = None
    if wants_catalog:
        raw = resolve_text(args.text, args.source)
        catalog = core.parse_catalog(raw, source_kind=args.source_kind,
                                     default_category=args.default_category)
    elif args.category or args.capability:
        raise core.InputError(
            "a catalog source is required to look up categories/capabilities: "
            "pass --source PATH or --text '...'", code="missing_input")

    if args.capability:
        names: List[str] = args.capability
        caps: List[core.Capability] = []
        cat_name = None
        for n in names:
            found = catalog.capability(n) if catalog else None
            if found is None:
                caps.append(core.Capability(name=n, slug=core.slugify(n)))
            else:
                caps.append(found)
                cat_name = cat_name or found.category_slug
        cats = []
        for cap in caps:
            cat = None
            if catalog:
                for c in catalog.categories:
                    if any(k.slug == cap.slug for k in c.capabilities):
                        cat = c
                        break
            cats.append(cat or core.Category(name=cat_name or args.default_category.replace("-", " ").title()))
        profiles = [core.profile_capability(cap, cat) for cap, cat in zip(caps, cats)]
        label_cat = cats[0].name if cats else None
        return catalog, profiles, "capability", label_cat, ", ".join(c.name for c in caps)

    if args.category:
        cats: List[core.Category] = []
        for key in args.category:
            found = catalog.category(key)
            if found is None:
                raise core.InputError(
                    f"category not found: {key!r}. Run `list` to see "
                    f"{len(catalog.categories)} available categories.",
                    code="category_not_found",
                    details={"requested": key, "available": [c.name for c in catalog.categories][:40]})
            cats.append(found)
        profiles = []
        budget = int(args.max_capabilities)
        for cat in cats:
            for cap in cat.capabilities:
                if len(profiles) >= budget:
                    raise core.LimitExceeded(
                        f"case would cover more than max_capabilities={budget}; "
                        "raise --max-capabilities or split the case")
                profiles.append(core.profile_capability(cap, cat))
        label = " + ".join(c.name for c in cats)
        return catalog, profiles, "category", label, None

    # No category/capability: a single ad-hoc case from the title.
    title = (args.title or "Untitled music production case").strip()
    cap = core.Capability(name=title, slug=core.slugify(title))
    cat = core.Category(name=args.default_category.replace("-", " ").title())
    return catalog, [core.profile_capability(cap, cat)], "ad_hoc", None, None


# --------------------------------------------------------------------------
# Case building (shared by plan + batch)
# --------------------------------------------------------------------------


def build_case(*, args, out_dir: str, intake_raw: Any, catalog: Optional[core.Catalog],
               profiles: List[Dict[str, Any]], kind: str, category_label: Optional[str],
               capability_label: Optional[str], workflow: Dict[str, Any],
               write_files: bool = True) -> Dict[str, Any]:
    limits = dict(core.DEFAULT_LIMITS)
    limits.update(workflow.get("limits", {}))
    intake = core.validate_intake(intake_raw, limits=limits)
    approvals = core.check_approvals(intake.get("actions", []), getattr(args, "approve", []) or [])

    title = (args.title or (capability_label or category_label) or intake["goal"])[:200].strip()
    ts = getattr(args, "timestamp", None) or _now_utc()
    _ = core._safe_ts(ts)
    slug = core.slugify(title, max_words=workflow.get("naming", {}).get("max_slug_words", 6))
    case_id = args.case_id or core.new_case_id(slug)

    plan = core.build_plan(kind=kind, title=title, slug=slug, category=category_label,
                           capability=capability_label, intake=intake, profiles=profiles,
                           workflow=workflow, timestamp=ts, case_id=case_id,
                           approvals=approvals)

    files: List[Tuple[str, str]] = []
    paths: Dict[str, str] = {}
    if write_files:
        os.makedirs(out_dir, exist_ok=True)
        brief = core.render_markdown(plan)
        checklist = core.render_checklist_csv(plan)
        plan_json = core.dumps(plan)
        targets = {
            "plan_json": (f"{case_id}-plan.json", plan_json, "structured case plan"),
            "brief_md": (f"{case_id}-brief.md", brief, "editable production brief"),
            "checklist_csv": (f"{case_id}-checklist.csv", checklist, "delivery + validation checklist"),
        }
        for key, (name, content, role) in targets.items():
            p = os.path.join(out_dir, name)
            core.write_text(p, content)
            paths[key] = p
            files.append((name, role))
        manifest = core.render_file_manifest(plan, files=files)
        p = os.path.join(out_dir, f"{case_id}-handoff.csv")
        core.write_text(p, manifest)
        paths["handoff_csv"] = p
        files.append((os.path.basename(p), "handoff manifest"))
        if workflow.get("output", {}).get("archive", True) and getattr(args, "archive", None) is not False:
            archive_path = (getattr(args, "archive", None)
                            or os.path.join(out_dir, f"{case_id}-delivery.zip"))
            payload = []
            for name, _role in files:
                with open(os.path.join(out_dir, name), "rb") as fh:
                    payload.append((f"{slug}/{name}", fh.read()))
            info = core.write_archive(archive_path, payload, package_layout=False)
            paths["archive"] = archive_path
            plan["delivery_archive"] = info
    return {"plan": plan, "paths": paths, "files": files}


def _now_utc() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_capabilities(args, out: Output) -> int:
    payload = {
        "ok": True,
        "studio": core.STUDIO_NAME,
        "version": VERSION,
        "primary_entry_point": "tools-music_ai_studio_cli.py",
        "supporting_modules": [
            {"file": "tools-music_ai_studio.py",
             "role": "engine: parsing, planning, validation, rendering, WAV QC, archive I/O"},
            {"file": "tools-music_ai_studio_selftest.py",
             "role": "runnable end-to-end test suite with fixture assertions"},
        ],
        "capabilities": COMMAND_CATALOG,
        "capability_classes": [
            "catalog intake (plain text, Markdown, copied HTML, PDF-derived text)",
            "unified case planning across one or many catalog capabilities",
            "intake validation with blocking rights/consent flags",
            "approval gating for publish/sign/pay/credential/delete actions",
            "editable deliverables: Markdown brief, JSON plan, checklist CSV, handoff CSV",
            "batch rendering with isolation, retries, per-job timeouts and a report",
            "WAV integrity + level QC (peak, RMS, crest, clips, DC, duration, sample rate)",
            "silence trimming for 16-bit PCM WAV",
            "deterministic ZIP delivery packaging and traversal-guarded extraction",
        ],
        "limits": core.DEFAULT_LIMITS,
        "exit_codes": {
            "0": "success", "2": "usage error", "3": "input/validation error",
            "4": "limit exceeded", "5": "approval required", "6": "unsupported input",
            "130": "interrupted",
        },
        "runtime": {"python": ">=3.9, stdlib only", "third_party": []},
    }
    if out.as_json:
        out.emit(payload)
        return EXIT_OK
    lines = [f"{payload['studio']} v{VERSION}",
             f"primary entry point: {payload['primary_entry_point']}", ""]
    lines.append("Commands:")
    for c in COMMAND_CATALOG:
        lines.append(f"  {c['command']:<14} {c['purpose']}")
        lines.append(f"  {'':<14} inputs : {c['inputs']}")
        lines.append(f"  {'':<14} outputs: {c['outputs']}")
    lines.append("")
    lines.append("Capability classes:")
    lines.extend([f"  - {x}" for x in payload["capability_classes"]])
    lines.append("")
    lines.append("Exit codes: " + ", ".join(f"{k}={v}" for k, v in payload["exit_codes"].items()))
    out.emit(payload, text="\n".join(lines))
    return EXIT_OK


def cmd_list(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    raw = resolve_text(args.text, args.source)
    catalog = core.parse_catalog(raw, source_kind=args.source_kind,
                                 default_category=args.default_category,
                                 max_entries=int(workflow["limits"]["max_catalog_entries"]),
                                 max_bytes=int(workflow["limits"]["max_input_bytes"]))
    if args.category:
        selected = []
        for key in args.category:
            c = catalog.category(key)
            if c is None:
                raise core.InputError(f"category not found: {key!r}", code="category_not_found",
                                      details={"available": [x.name for x in catalog.categories]})
            selected.append(c)
        catalog.categories = selected

    payload = {"ok": True, "stats": catalog.stats(), "catalog": catalog.to_dict()}
    if args.out:
        core.write_text(args.out, core.dumps(payload))
        out.info(f"wrote {args.out}")
    if out.as_json:
        out.emit(payload)
        return EXIT_OK
    if args.format == "markdown":
        lines = ["# Catalog parsing result", ""]
        lines.append(f"- categories: {payload['stats']['categories']}")
        lines.append(f"- capabilities: {payload['stats']['capabilities']}")
        lines.append(f"- source kind: {payload['stats']['source_kind']}")
        lines.append("")
        for c in catalog.categories:
            lines.append(f"## {c.name}")
            if c.focus:
                lines.append(f"_{c.focus}_")
            lines.append("")
            lines.extend([f"- {k.name}" for k in c.capabilities])
            lines.append("")
        out.emit(payload, text="\n".join(lines))
        return EXIT_OK
    lines = [f"categories: {payload['stats']['categories']}  "
             f"capabilities: {payload['stats']['capabilities']}  "
             f"source: {payload['stats']['source_kind']}"]
    for i, c in enumerate(catalog.categories, 1):
        lines.append(f"{i:>3}. {c.name}  ({len(c.capabilities)})")
        for k in c.capabilities:
            lines.append(f"       - {k.name}  [{k.slug}]")
    if payload["stats"]["warnings"]:
        lines.append("")
        lines.append("warnings:")
        lines.extend([f"  - {w}" for w in payload["stats"]["warnings"]])
    out.emit(payload, text="\n".join(lines))
    return EXIT_OK


def cmd_plan(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    intake_raw = resolve_input(args.intake, args.intake_file, what="intake")
    catalog, profiles, kind, cat_label, cap_label = resolve_targets(args)

    if args.out_file:
        if os.path.isdir(args.out_file):
            raise core.InputError("--out-file must be a file path, not a directory",
                                 code="invalid_output")
    out_dir = args.out or os.path.join("out", core.slugify(args.title or cap_label or cat_label or "case"))

    if args.dry_run:
        intake = core.validate_intake(intake_raw, limits=dict(core.DEFAULT_LIMITS, **workflow["limits"]))
        approvals = core.check_approvals(intake.get("actions", []), args.approve or [])
        out.emit({"ok": True, "dry_run": True, "out_dir": out_dir,
                  "files": ["*-plan.json", "*-brief.md", "*-checklist.csv", "*-handoff.csv", "*-delivery.zip"],
                  "coverage": {"capabilities": len(profiles),
                               "categories": cat_label,
                               "family": [p["family"] for p in profiles]},
                  "approvals": approvals,
                  "blocking_flags": intake.get("blocking_flags", [])},
                 text=(f"[dry-run] would write case files for {len(profiles)} capability(ies) to {out_dir}\n"
                       f"[dry-run] blocking flags: {intake.get('blocking_flags') or 'none'}"))
        return EXIT_OK

    def _run():
        with time_limit(workflow.get("timeout_seconds"), "plan"):
            return build_case(args=args, out_dir=out_dir, intake_raw=intake_raw, catalog=catalog,
                              profiles=profiles, kind=kind, category_label=cat_label,
                              capability_label=cap_label, workflow=workflow,
                              write_files=not args.no_files)

    result = with_retries(_run, retries=int(workflow.get("retries", 0)), label="plan")
    plan = result["plan"]

    if args.out_file:
        core.write_text(args.out_file, core.dumps(plan))
        result["paths"]["plan_json"] = args.out_file

    payload = {"ok": True, "case_id": plan["case_id"], "kind": plan["kind"],
               "title": plan["title"], "outputs": result["paths"],
               "coverage": plan["coverage"],
               "counts": {"deliverables": len(plan["deliverables"]), "checks": len(plan["checks"]),
                          "capabilities": plan["coverage"]["capabilities_in_case"]},
               "blocking_flags": plan["intake"].get("blocking_flags", []),
               "plan": plan}
    out.emit(payload, text=_plan_text(plan, result["paths"]))
    return EXIT_OK


def _plan_text(plan: Dict[str, Any], paths: Dict[str, Any]) -> str:
    lines = [f"case: {plan['case_id']}  ({plan['kind']})",
             f"title: {plan['title']}",
             f"capabilities in case: {plan['coverage']['capabilities_in_case']}  "
             f"families: {', '.join(plan['coverage']['families'])}  "
             f"risk: {plan['coverage']['risk_level']}",
             f"deliverables: {len(plan['deliverables'])}  validation checks: {len(plan['checks'])}"]
    if plan["intake"].get("blocking_flags"):
        lines.append("BLOCKING FLAGS (resolve before publishing):")
        lines.extend([f"  - {f}" for f in plan["intake"]["blocking_flags"]])
    if paths:
        lines.append("written files:")
        lines.extend([f"  - {os.path.basename(v)}  [{k}]" for k, v in paths.items()
                      if k != "archive"])
        if "archive" in paths:
            lines.append(f"  - {os.path.basename(paths['archive'])}  [delivery zip]")
    lines.append("next: open the -brief.md file, fill the decision log, and work the -checklist.csv.")
    return "\n".join(lines)


def cmd_verify_intake(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    limits = dict(core.DEFAULT_LIMITS)
    limits.update(workflow["limits"])
    intake_raw = resolve_input(args.intake, args.intake_file, what="intake")
    intake = core.validate_intake(intake_raw, limits=limits)
    approvals = core.check_approvals(intake.get("actions", []), args.approve or [])
    payload = {"ok": True, "valid": True, "intake": intake, "approvals": approvals,
               "notes": ["judgement-based checks still require human review"]}
    lines = ["intake valid",
             f"  goal: {intake['goal']}",
             f"  inputs: {len(intake['inputs'])}",
             f"  rights_status: {intake['rights_status']}",
             f"  voice_consent: {intake['voice_consent']}",
             f"  blocking flags: {intake['blocking_flags'] or 'none'}",
             f"  gated actions approved: {approvals['approved_count']}/{len(approvals['actions'])}"]
    out.emit(payload, text="\n".join(lines))
    return EXIT_OK


def _resolve_relative(path: str, roots: Sequence[Optional[str]]) -> str:
    """Resolve a possibly-relative path against, in order: given roots, then as-is."""
    if not path or os.path.isabs(path):
        return path
    for root in roots:
        if root:
            candidate = os.path.join(root, path)
            if os.path.exists(candidate):
                return candidate
    return path


def cmd_batch(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    jobs_doc = resolve_input(None, args.jobs, what="jobs")
    args.jobs_root = os.path.dirname(os.path.abspath(args.jobs))
    jobs = jobs_doc.get("jobs") if isinstance(jobs_doc, dict) else jobs_doc
    if not isinstance(jobs, list) or not jobs:
        raise core.InputError("jobs file must contain a non-empty 'jobs' list", code="invalid_jobs")
    if len(jobs) > int(workflow["limits"]["max_batch_jobs"]):
        raise core.LimitExceeded(f"{len(jobs)} jobs exceeds max_batch_jobs={workflow['limits']['max_batch_jobs']}")
    defaults = jobs_doc.get("defaults", {}) if isinstance(jobs_doc, dict) else {}
    if not isinstance(defaults, dict):
        raise core.InputError("jobs 'defaults' must be an object", code="invalid_jobs")

    out_dir = args.out or os.path.join("out", "batch")
    os.makedirs(out_dir, exist_ok=True)
    started = time.monotonic()
    report: Dict[str, Any] = {"ok": True, "jobs_total": len(jobs), "jobs_ok": 0, "jobs_failed": 0,
                              "results": [], "out_dir": out_dir}

    def run_job(index_job):
        index, job = index_job
        merged = dict(defaults)
        merged.update(job if isinstance(job, dict) else {})
        try:
            if not isinstance(merged, dict):
                raise core.InputError(f"job {index} is not an object", code="invalid_jobs")
            intake_raw = merged.get("intake")
            if intake_raw is None:
                raise core.ValidationError(f"job {index} has no 'intake'")
            source = merged.get("source") or args.source
            text = merged.get("text") or (args.text if not source else None)
            catalog = None
            if source or text:
                raw = text if (text and not source) else core.read_text_file(
                    _resolve_relative(source, [getattr(args, "jobs_root", None),
                                               args.source_root, os.getcwd()]))
                catalog = core.parse_catalog(raw, source_kind="auto",
                                             default_category=merged.get("default_category", "general"))
            profiles: List[Dict[str, Any]] = []
            kind, cat_label, cap_label = "ad_hoc", None, None
            if merged.get("category") and catalog:
                keys = merged["category"] if isinstance(merged["category"], list) else [merged["category"]]
                for key in keys:
                    cat = catalog.category(str(key))
                    if cat is None:
                        raise core.InputError(f"job {index}: category not found: {key}",
                                              code="category_not_found")
                    cat_label = cat.name if cat_label is None else cat_label
                    for cap in cat.capabilities:
                        profiles.append(core.profile_capability(cap, cat))
                kind = "category"
            if merged.get("capability"):
                names = merged["capability"] if isinstance(merged["capability"], list) else [merged["capability"]]
                for n in names:
                    cap = catalog.capability(str(n)) if catalog else None
                    if cap is None:
                        cap = core.Capability(name=str(n))
                        cat = core.Category(name=args.default_category.replace("-", " ").title())
                    else:
                        cat = next(c for c in catalog.categories if any(k.slug == cap.slug for k in c.capabilities))
                    profiles.append(core.profile_capability(cap, cat))
                    cap_label = cap.name if cap_label is None else cap_label
                kind = "capability"
            if not profiles:
                title = merged.get("title") or "Untitled music production case"
                cap = core.Capability(name=title)
                cat = core.Category(name=args.default_category.replace("-", " ").title())
                profiles = [core.profile_capability(cap, cat)]

            ns = argparse.Namespace(
                title=merged.get("title"), timestamp=merged.get("timestamp") or args.timestamp,
                case_id=merged.get("case_id"), approve=merged.get("approve") or args.approve or [],
                archive=merged.get("archive"), no_files=False, default_category=args.default_category,
            )
            job_dir = os.path.join(out_dir, f"{index:03d}-{core.slugify(merged.get('title') or cap_label or cat_label or f'job-{index}')}")
            res = with_retries(
                lambda: build_case(args=ns, out_dir=job_dir, intake_raw=intake_raw, catalog=catalog,
                                   profiles=profiles, kind=kind, category_label=cat_label,
                                   capability_label=cap_label, workflow=workflow),
                retries=int(workflow.get("retries", 0)), label=f"job {index}")
            return {"index": index, "ok": True, "case_id": res["plan"]["case_id"],
                    "dir": job_dir, "files": [n for n, _ in res["files"]],
                    "capabilities": res["plan"]["coverage"]["capabilities_in_case"],
                    "blocking_flags": res["plan"]["intake"].get("blocking_flags", [])}
        except core.StudioError as exc:
            return {"index": index, "ok": False, "error": exc.as_dict()["error"]}
        except Exception as exc:  # noqa: BLE001 - a bad job must not kill the batch
            return {"index": index, "ok": False,
                    "error": {"code": "internal_error", "message": str(exc),
                              "type": type(exc).__name__}}

    workers = max(1, min(int(args.workers), 8))
    with time_limit(workflow.get("timeout_seconds"), "batch") if workers == 1 else _null():
        if workers == 1:
            results = [run_job((i, j)) for i, j in enumerate(jobs, 1)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(run_job, [(i, j) for i, j in enumerate(jobs, 1)]))

    for r in results:
        report["results"].append(r)
        if r["ok"]:
            report["jobs_ok"] += 1
        else:
            report["jobs_failed"] += 1
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = report["jobs_failed"] == 0
    report_path = os.path.join(out_dir, "batch-report.json")
    core.write_text(report_path, core.dumps(report))
    report["report_path"] = report_path

    lines = [f"batch: {report['jobs_ok']}/{report['jobs_total']} ok, "
             f"{report['jobs_failed']} failed, {report['elapsed_seconds']}s",
             f"report: {report_path}"]
    for r in report["results"]:
        if r["ok"]:
            lines.append(f"  [{r['index']:03d}] ok   {r['case_id']}  ({r['capabilities']} capabilities)")
        else:
            lines.append(f"  [{r['index']:03d}] FAIL {r['error']['code']}: {r['error']['message']}")
    out.emit(report, text="\n".join(lines))
    return EXIT_OK if report["ok"] else EXIT_INPUT


@contextlib.contextmanager
def _null():
    yield


def cmd_qc_audio(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    limits = dict(core.DEFAULT_LIMITS)
    limits.update(workflow["limits"])
    qc = load_qc_profile(args.qc_profile, workflow, getattr(args, "qc_profile_name", None))
    paths: List[str] = []
    if args.audio:
        paths.extend(args.audio)
    if args.audio_dir:
        if not os.path.isdir(args.audio_dir):
            raise core.InputError(f"audio dir not found: {args.audio_dir}", code="file_not_found")
        found = sorted(f for f in os.listdir(args.audio_dir) if f.lower().endswith(".wav"))
        paths.extend(os.path.join(args.audio_dir, f) for f in found)
    if not paths:
        raise core.InputError("pass --audio FILE ... or --audio-dir DIR", code="missing_input")
    if len(paths) > 200:
        raise core.LimitExceeded("qc-audio accepts at most 200 files per run")

    reports = []
    for p in paths:
        with time_limit(limits.get("max_audio_scan_seconds"), f"qc-audio {os.path.basename(p)}"):
            reports.append(core.wav_inspect(p, limits=limits, qc=qc,
                                            budget_seconds=float(limits.get("max_audio_scan_seconds", 30))))
    failed = [r for r in reports if not r["verdict"]["passed"]]
    payload = {"ok": not failed, "profile": args.qc_profile or "workflow-default",
               "files_checked": len(reports), "files_failed": len(failed), "reports": reports}
    if args.out:
        core.write_text(args.out, core.dumps(payload))
        payload["report_path"] = args.out
    if out.as_json:
        out.emit(payload)
    else:
        lines = []
        for r in reports:
            m = r["measurements"]
            status = "PASS" if r["verdict"]["passed"] else "FAIL"
            lines.append(f"{status} {r['file']}  "
                         f"{m.get('duration_seconds', '?')}s  {m.get('sample_rate', '?')}Hz  "
                         f"{m.get('channels', '?')}ch  {m.get('codec', '?')}")
            if m.get("peak_dbfs") is not None:
                lines.append(f"     peak {m['peak_dbfs']} dBFS | rms {m['rms_dbfs']} dBFS | "
                             f"crest {m['crest_factor_db']} dB | clips {m['clipped_sample_ratio']}")
            for issue in r["verdict"]["issues"]:
                lines.append(f"     issue: {issue}")
        lines.append(f"{len(reports) - len(failed)}/{len(reports)} passed")
        out.emit(payload, text="\n".join(lines))
    return EXIT_OK if not failed else EXIT_INPUT


def cmd_trim_silence(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    limits = dict(core.DEFAULT_LIMITS)
    limits.update(workflow["limits"])
    if not args.out_file:
        raise core.InputError("--out-file is required", code="missing_input")
    if os.path.abspath(args.audio) == os.path.abspath(args.out_file):
        raise core.InputError("--out-file must differ from --audio (non-destructive by design)",
                              code="unsafe_overwrite")
    with time_limit(workflow.get("timeout_seconds"), "trim-silence"):
        info = core.wav_silence(args.audio, out_path=args.out_file,
                                threshold_dbfs=float(args.threshold_dbfs), limits=limits)
    payload = {"ok": True, **info}
    out.emit(payload, text=(f"trimmed {info['file']} -> {info['output']}\n"
                            f"  kept {info['kept_seconds']}s ({info['kept_frames']} frames), "
                            f"threshold {info['threshold_dbfs']} dBFS"))
    return EXIT_OK


def cmd_pack(args, out: Output) -> int:
    workflow = load_workflow(args.workflow)
    src = args.input
    if not os.path.isdir(src):
        raise core.InputError(f"input folder not found: {src}", code="file_not_found")
    slug = core.slugify(args.name or os.path.basename(os.path.abspath(src)))
    payload_files: List[Tuple[str, bytes]] = []
    for root, dirs, names in os.walk(src):
        dirs.sort()
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, src).replace(os.sep, "/")
            if "/" in rel:
                raise core.ValidationError(
                    f"nested folder in delivery: {rel} - flatten it before packing",
                    code="bad_structure")
            with open(full, "rb") as fh:
                data = fh.read(core.DEFAULT_LIMITS["max_audio_bytes"])
            payload_files.append((f"{slug}/{rel}", data))
    if not payload_files:
        raise core.InputError("input folder has no files to pack", code="empty_input")
    archive = args.archive or f"{slug}-delivery.zip"
    info = core.write_archive(archive, payload_files, compress=not args.store,
                              package_layout=False)
    payload = {"ok": True, **info, "input": src, "slug": slug}
    if args.out_manifest:
        rows = ["file,bytes,sha256"]
        for name, data in payload_files:
            rows.append(f"{name},{len(data)},{core.sha256_bytes(data)}")
        core.write_text(args.out_manifest, "\n".join(rows) + "\n")
        payload["manifest"] = args.out_manifest
    out.emit(payload, text=(f"packed {info['entries']} files -> {info['archive']} "
                            f"({info['bytes']} bytes)\n" +
                            "\n".join(f"  {n}" for n in info["names"])))
    return EXIT_OK


def cmd_unpack(args, out: Output) -> int:
    if not args.archive:
        raise core.InputError("--archive is required", code="missing_input")
    dest = args.dest or "unpacked"
    entries = core.read_archive(args.archive)
    written = []
    os.makedirs(dest, exist_ok=True)
    for name, data in entries:
        target = os.path.join(dest, *name.split("/"))
        root = os.path.abspath(dest)
        if not os.path.abspath(target).startswith(root + os.sep) and os.path.abspath(target) != root:
            raise core.ValidationError(f"refusing to write outside destination: {name}", code="unsafe_path")
        core.ensure_parent_dir(target)
        with open(target, "wb") as fh:
            fh.write(data)
        written.append({"file": name, "bytes": len(data), "sha256": core.sha256_bytes(data)})
    payload = {"ok": True, "archive": args.archive, "dest": dest,
               "files": written, "count": len(written)}
    out.emit(payload, text=f"extracted {len(written)} files to {dest}\n" +
             "\n".join(f"  {w['file']} ({w['bytes']} bytes)" for w in written))
    return EXIT_OK


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true",
                   help="emit structured JSON on stdout (errors also become JSON)")
    p.add_argument("--out", default=None, metavar="PATH",
                   help="output directory (commands that write several files)")
    p.add_argument("--quiet", action="store_true", help="suppress progress notes on stderr")
    p.add_argument("--workflow", default=None, metavar="FILE",
                   help="workflow/config JSON (limits, retries, timeout, qc profile, output switches)")


def _add_source(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", default=None, metavar="FILE",
                   help="catalog file (.txt/.md/.html) or PDF-derived text")
    p.add_argument("--text", default=None, metavar="STRING", help="catalog text inline")
    p.add_argument("--source-kind", default="auto", choices=["auto", "text", "markdown", "html"],
                   help="force the parser mode (default auto)")
    p.add_argument("--default-category", default="general", metavar="SLUG",
                   help="umbrella category name when the source has no category headers")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-music_ai_studio_cli.py",
        description=("music-ai-skill-studio: turn a music/AI capability catalog into "
                     "executable production cases with editable deliverables and QC."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("examples:\n"
                "  # 1. check what the catalog contains\n"
                "  python3 music-ai-skill-studio/tools-music_ai_studio_cli.py list \\\n"
                "    --source music-ai-skill-studio/asset-catalog-music-ai-400.md\n\n"
                "  # 2. plan one category (all 10 capabilities unified into one case)\n"
                "  python3 music-ai-skill-studio/tools-music_ai_studio_cli.py plan \\\n"
                "    --source music-ai-skill-studio/asset-catalog-music-ai-400.md \\\n"
                "    --category 19 --intake music-ai-skill-studio/asset-intake-example.json \\\n"
                "    --out out/mixing-case\n\n"
                "  # 3. validate a WAV before delivery\n"
                "  python3 music-ai-skill-studio/tools-music_ai_studio_cli.py qc-audio \\\n"
                "    --audio master.wav --json\n"),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("capabilities", help="list commands and capability classes")
    _add_common(p)

    p = sub.add_parser("list", help="parse a catalog source")
    _add_common(p)
    _add_source(p)
    p.add_argument("--category", action="append", default=None, metavar="KEY",
                   help="restrict to one category (repeatable)")
    p.add_argument("--format", default="text", choices=["text", "markdown", "json"])
    p.add_argument("--out-file", dest="list_out", default=None, help="write the JSON listing to a file")

    p = sub.add_parser("plan", help="build a case: plan, brief, checklist, handoff, zip")
    _add_common(p)
    _add_source(p)
    p.add_argument("--category", action="append", default=None, metavar="KEY",
                   help="catalog category (number, slug or name fragment); repeatable")
    p.add_argument("--capability", action="append", default=None, metavar="NAME",
                   help="specific capability name (may be outside the catalog); repeatable")
    p.add_argument("--title", default=None, help="case title (defaults to category/capability)")
    p.add_argument("--intake", default=None, metavar="JSON", help="intake JSON inline")
    p.add_argument("--intake-file", default=None, metavar="FILE", help="intake JSON file")
    p.add_argument("--out-file", dest="out_file", default=None, metavar="FILE",
                   help="also write the plan JSON to this exact path")
    p.add_argument("--case-id", dest="case_id", default=None, help="override the generated case id")
    p.add_argument("--timestamp", default=None, help="UTC timestamp for reproducible output")
    p.add_argument("--max-capabilities", dest="max_capabilities", type=int, default=40,
                   help="cap on capabilities folded into one case (default 40)")
    p.add_argument("--approve", action="append", default=None, metavar="TOKEN",
                   help="approval token, e.g. APPROVE-RELEASE (repeatable)")
    p.add_argument("--archive", nargs="?", const=True, default=None, metavar="FILE",
                   help="write delivery ZIP to FILE (default alongside the case)")
    p.add_argument("--no-archive", dest="archive", action="store_const", const=False,
                   help="skip the delivery ZIP")
    p.add_argument("--no-files", action="store_true", help="compute the plan without writing files")
    p.add_argument("--dry-run", action="store_true", help="validate and report, write nothing")

    p = sub.add_parser("verify-intake", help="validate intake + approvals only")
    _add_common(p)
    p.add_argument("--intake", default=None, metavar="JSON")
    p.add_argument("--intake-file", default=None, metavar="FILE")
    p.add_argument("--approve", action="append", default=None, metavar="TOKEN")

    p = sub.add_parser("batch", help="render many cases from a jobs file")
    _add_common(p)
    p.add_argument("--jobs", required=True, metavar="FILE", help="jobs JSON file")
    p.add_argument("--source", default=None, metavar="FILE", help="default catalog source for jobs")
    p.add_argument("--source-root", default=None, metavar="DIR",
                   help="resolve relative job 'source' paths against this folder")
    p.add_argument("--text", default=None, metavar="STRING", help="default inline catalog text")
    p.add_argument("--workers", type=int, default=1, help="parallel jobs, 1..8 (default 1)")
    p.add_argument("--approve", action="append", default=None, metavar="TOKEN")
    p.add_argument("--timestamp", default=None)
    p.add_argument("--default-category", default="general", metavar="SLUG")

    p = sub.add_parser("qc-audio", help="WAV integrity and level QC")
    _add_common(p)
    p.add_argument("--audio", action="append", default=None, metavar="FILE", help="repeatable")
    p.add_argument("--audio-dir", default=None, metavar="DIR", help="all *.wav in a folder")
    p.add_argument("--qc-profile", default=None, metavar="FILE",
                   help="QC threshold profile JSON, or a bundle like asset-qc-profiles.json")
    p.add_argument("--qc-profile-name", dest="qc_profile_name", default=None, metavar="NAME",
                   help="which profile inside the bundle to use (default: 'default')")
    p.add_argument("--out-file", dest="qc_out", default=None, metavar="FILE")

    p = sub.add_parser("trim-silence", help="trim head/tail silence from a 16-bit WAV")
    _add_common(p)
    p.add_argument("--audio", required=True, metavar="FILE")
    p.add_argument("--out-file", dest="out_file", required=True, metavar="FILE")
    p.add_argument("--threshold-dbfs", dest="threshold_dbfs", default="-60.0")

    p = sub.add_parser("pack", help="package a folder into a validated delivery ZIP")
    _add_common(p)
    p.add_argument("--input", required=True, metavar="DIR")
    p.add_argument("--archive", default=None, metavar="FILE")
    p.add_argument("--name", default=None, metavar="SLUG", help="folder name inside the ZIP")
    p.add_argument("--store", action="store_true", help="no compression")
    p.add_argument("--out-manifest", dest="out_manifest", default=None, metavar="FILE")

    p = sub.add_parser("unpack", help="safely extract a delivery ZIP")
    _add_common(p)
    p.add_argument("--archive", required=True, metavar="FILE")
    p.add_argument("--dest", default=None, metavar="DIR")

    return parser


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["--help"]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    out = Output(as_json=bool(getattr(args, "json", False)),
                 quiet=bool(getattr(args, "quiet", False)))
    handlers = {
        "capabilities": cmd_capabilities,
        "list": cmd_list,
        "plan": cmd_plan,
        "verify-intake": cmd_verify_intake,
        "batch": cmd_batch,
        "qc-audio": cmd_qc_audio,
        "trim-silence": cmd_trim_silence,
        "pack": cmd_pack,
        "unpack": cmd_unpack,
    }
    try:
        if args.command == "list" and getattr(args, "list_out", None):
            args.out = args.out or None
        if args.command == "qc-audio" and getattr(args, "qc_out", None):
            args.out = args.qc_out
        return handlers[args.command](args, out)
    except core.ApprovalRequired as exc:
        return _fail(exc, out, EXIT_APPROVAL)
    except core.NotSupported as exc:
        return _fail(exc, out, EXIT_UNSUPPORTED)
    except core.LimitExceeded as exc:
        return _fail(exc, out, EXIT_LIMIT)
    except (core.ValidationError, core.InputError) as exc:
        return _fail(exc, out, EXIT_INPUT)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted; partial files are limited to *.tmp-* which were removed\n")
        _cleanup_tmp(os.getcwd())
        return EXIT_INTERRUPTED
    except BrokenPipeError:  # pragma: no cover
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": {"code": "internal_error", "message": str(exc),
                                          "type": type(exc).__name__}}
        if out.as_json:
            sys.stdout.write(core.dumps(payload) + "\n")
        else:
            sys.stderr.write(f"internal error: {exc}\n")
            if os.environ.get("MUSIC_AI_DEBUG"):
                traceback.print_exc()
            else:
                sys.stderr.write("set MUSIC_AI_DEBUG=1 for a traceback\n")
        return EXIT_INPUT


def _fail(exc: core.StudioError, out: Output, code: int) -> int:
    if out.as_json:
        sys.stdout.write(core.dumps(exc.as_dict()) + "\n")
    else:
        sys.stderr.write(f"error [{exc.code}]: {exc.message}\n")
        if exc.details:
            sys.stderr.write("details: " + json.dumps(exc.details, ensure_ascii=False) + "\n")
    return code


def _cleanup_tmp(root: str) -> None:
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if ".tmp-" in f:
                try:
                    os.remove(os.path.join(dirpath, f))
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
