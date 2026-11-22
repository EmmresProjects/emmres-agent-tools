#!/usr/bin/env python3
"""powerpoint-agent — a unified PowerPoint production skill tool.

One runnable entry point for the 50-category PowerPoint Agent Skill Catalog:
plan, build, format, audit, repair, optimize, inspect, preview, export, batch,
and deliver presentation work while keeping the artifact editable.

Run ``--help`` for the command list, or ``capabilities`` for a machine-readable
capability map with exit codes and safety rules.

Examples
--------
python tools-pptx-agent.py capabilities
python tools-pptx-agent.py plan --brief asset-brief-example.md --out plan.json
python tools-pptx-agent.py build --spec asset-deck-spec-example.json --out deck.pptx
python tools-pptx-agent.py audit --in deck.pptx --out report.json --markdown report.md --fail-on error
python tools-pptx-agent.py repair --in deck.pptx --out deck.repaired.pptx --dry-run
python tools-pptx-agent.py preview --in deck.pptx --out-dir previews
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import traceback

# The tool imports its sibling modules from its own folder; never leave
# __pycache__ directories behind inside the skill folder.
sys.dont_write_bytecode = True
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# module loading (flat, prefixed skill-tool folder)
# ---------------------------------------------------------------------------

def _load(module_name: str, filename: str):
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        sys.stderr.write("FATAL: missing required skill-tool module: %s\n" % filename)
        sys.stderr.write("All tool files must sit beside the entry point in one flat folder.\n")
        raise SystemExit(3)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(3)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


CORE = _load("tools_deck_core", "tools-deck-core.py")
AUDIT = _load("tools_audit_engine", "tools-audit-engine.py")
REPAIR = _load("tools_repair_engine", "tools-repair-engine.py")
MEDIA = _load("tools_media_optimizer", "tools-media-optimizer.py")
RENDER = _load("tools_render_adapter", "tools-render-adapter.py")
EDIT = _load("tools_edit_engine", "tools-edit-engine.py")
BATCH = _load("tools_batch_runner", "tools-batch-runner.py")

AUDIT.bind(CORE)
REPAIR.bind(CORE, AUDIT)
MEDIA.bind(CORE)
RENDER.bind(CORE)
EDIT.bind(CORE)
BATCH.bind(CORE)


# ---------------------------------------------------------------------------
# run context
# ---------------------------------------------------------------------------

@dataclass
class Context:
    config: Dict[str, Any]
    log: Any
    deadline: Any
    quiet: bool = False


class CommandSpec:
    def __init__(self, handler, summary: str, role: str = "", writes: bool = False, approval: bool = False):
        self.handler = handler
        self.summary = summary
        self.role = role
        self.writes = writes
        self.approval = approval


COMMANDS: Dict[str, CommandSpec] = {}
PARSERS: Dict[str, argparse.ArgumentParser] = {}


def command(name: str, summary: str, *, role: str = "", writes: bool = False, approval: bool = False):
    def decorator(function):
        COMMANDS[name] = CommandSpec(function, summary, role=role, writes=writes, approval=approval)
        return function

    return decorator


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

DECK_EXT = (".pptx",)

ROLE_BY_COMMAND = {
    "plan": "Architect",
    "build": "Builder",
    "format": "Formatter",
    "audit": "Auditor",
    "repair": "Repairer",
    "optimize": "Optimizer",
    "batch": "Automator",
    "deliver": "Orchestrator",
}


def _iso() -> str:
    return CORE.utc_now_iso()


def _read_text_file(path: str, *, label: str = "text file", max_bytes: int = 2_000_000) -> str:
    resolved = CORE.resolve_input(path, exts=(".md", ".txt", ".markdown", ".rst"), label=label)
    if os.path.getsize(resolved) > max_bytes:
        raise CORE.ValidationError("%s is larger than %d bytes." % (label.capitalize(), max_bytes))
    with open(resolved, "r", encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def _parse_slide_range(spec: str, total: int) -> List[int]:
    if not spec:
        return []
    selected: List[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                raise CORE.UsageError("Invalid slide range '%s'; use e.g. 1-4,7." % part)
            if start <= 0 or end < start:
                raise CORE.UsageError("Invalid slide range '%s'." % part)
            selected.extend(range(start, end + 1))
        else:
            try:
                selected.append(int(part))
            except ValueError:
                raise CORE.UsageError("Invalid slide number '%s'." % part)
    if not selected:
        return []
    bad = [number for number in selected if number < 1 or number > total]
    if bad:
        raise CORE.ValidationError(
            "Slide number(s) %s are outside this deck (1..%d)." % (", ".join(map(str, bad)), total)
        )
    return sorted(set(selected))


def _require_approval(args, action: str) -> None:
    if not getattr(args, "approve", False):
        raise CORE.SafetyError(
            "%s needs explicit approval." % action,
            hint="Review the dry run, then rerun with --approve.",
            details={"command": getattr(args, "command", "")},
        )


def _write_if_requested(path: Optional[str], writer: Callable[[str], str], payload: str, *, overwrite: bool) -> Optional[str]:
    if not path:
        return None
    destination = CORE.resolve_output(path, overwrite=overwrite, label="report")
    CORE.write_text(payload, destination)
    return destination


# ---------------------------------------------------------------------------
# command: capabilities / help / doctor / themes / catalog
# ---------------------------------------------------------------------------

EXIT_CODES = [
    (0, "success"),
    (2, "usage error (bad arguments)"),
    (3, "missing dependency or external renderer"),
    (4, "validation error (bad input, spec, or manifest)"),
    (5, "quality gate failed (audit findings at or above --fail-on)"),
    (6, "safety refusal (approval missing or destructive request blocked)"),
    (7, "runtime error"),
    (130, "interrupted"),
]


def _audit_rule_inventory() -> Dict[str, Any]:
    """Count the shipped audit rules so an agent can discover the pack size."""

    pack_path = os.path.join(CORE.skill_root(), "asset-audit-rules.json")
    try:
        pack = CORE.load_json(pack_path)
    except Exception:
        return {"pack": "asset-audit-rules.json", "count": 0, "ids": [], "categories": {}}
    categories: Dict[str, int] = {}
    ids = []
    for rule in pack.get("rules", []):
        rule_id = str(rule.get("id") or "").strip()
        if not rule_id:
            continue
        ids.append(rule_id)
        category = str(rule.get("category") or "general")
        categories[category] = categories.get(category, 0) + 1
    return {
        "pack": "asset-audit-rules.json",
        "count": len(ids),
        "ids": sorted(ids),
        "categories": categories,
    }


@command("capabilities", "List every command, role, safety rule, and exit code.", role="meta")
def cmd_capabilities(args, ctx: Context) -> Dict[str, Any]:
    capabilities = []
    for name, spec in COMMANDS.items():
        capabilities.append(
            {
                "command": name,
                "summary": spec.summary,
                "role": spec.role or ROLE_BY_COMMAND.get(name, ""),
                "writes_files": spec.writes,
                "requires_approval": spec.approval,
            }
        )
    data = {
        "tool": "powerpoint-agent",
        "version": TOOL_VERSION,
        "entry_point": "tools-pptx-agent.py",
        "commands": capabilities,
        "exit_codes": [{"code": code, "meaning": meaning} for code, meaning in EXIT_CODES],
        "supported_inputs": {
            "presentations": [".pptx", ".pptm", ".potx", ".ppsx"],
            "specs": [".json"],
            "briefs": [".md", ".txt", ".markdown", ".rst"],
            "images": [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"],
        },
        "layouts": sorted(CORE.LAYOUTS.keys()),
        "theme_presets": [item["name"] for item in CORE.list_theme_presets()],
        "audit_rules": _audit_rule_inventory(),
        "dependencies": {
            "required": ["python>=3.9", "python-pptx>=1.0.0"],
            "recommended": ["Pillow>=10.0.0"],
            "optional_external": ["LibreOffice (faithful PDF export)", "poppler-utils (PNG export)"],
        },
    }
    lines = ["powerpoint-agent %s — capabilities" % TOOL_VERSION, ""]
    lines.append("%-14s %-13s %-9s %s" % ("COMMAND", "ROLE", "APPROVAL", "PURPOSE"))
    for item in capabilities:
        lines.append(
            "%-14s %-13s %-9s %s"
            % (item["command"], item["role"] or "-", "yes" if item["requires_approval"] else "-", item["summary"])
        )
    lines.append("")
    rules = data.get("audit_rules") or {}
    if rules.get("count"):
        lines.append(
            "Audit rules: %d in %d categories (%s)"
            % (rules["count"], len(rules.get("categories") or {}), rules.get("pack"))
        )
    lines.append("Layouts: %s" % ", ".join(sorted(CORE.LAYOUTS)))
    lines.append("Themes: %s" % ", ".join(data["theme_presets"]))
    lines.append("Exit codes: " + ", ".join("%d=%s" % (code, meaning.split(" (")[0]) for code, meaning in EXIT_CODES))
    return {"summary": "\n".join(lines), "data": data}


@command("doctor", "Check the runtime, dependencies, assets, and export backends.", role="meta")
def cmd_doctor(args, ctx: Context) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, status: str, detail: str, fix: str = "") -> None:
        checks.append({"check": name, "status": status, "detail": detail, "fix": fix})

    version = "%d.%d.%d" % sys.version_info[:3]
    add("python", "ok" if sys.version_info >= (3, 9) else "fail", version, "Use Python 3.9 or newer.")
    add(
        "python-pptx",
        "ok" if CORE.PPTX_AVAILABLE else "fail",
        "imported" if CORE.PPTX_AVAILABLE else CORE.PPTX_IMPORT_ERROR,
        "python -m pip install -r tools-requirements.txt",
    )
    add(
        "pillow",
        "ok" if CORE.PIL_AVAILABLE else "warn",
        "imported" if CORE.PIL_AVAILABLE else CORE.PIL_IMPORT_ERROR,
        "python -m pip install -r tools-requirements.txt (needed for previews, metrics, optimization)",
    )
    font_file = CORE.find_font_file("body", ctx.config) if CORE.PIL_AVAILABLE else None
    add(
        "font-metrics",
        "ok" if font_file else ("warn" if CORE.PIL_AVAILABLE else "warn"),
        font_file or "no TrueType font found; text metrics fall back to estimates",
        "Install DejaVu/Liberation fonts or set render.font_search_paths in the config.",
    )
    backends = RENDER.backend_status(ctx.config)
    add(
        "libreoffice",
        "ok" if backends["libreoffice"]["available"] else "warn",
        backends["libreoffice"]["binary"] or "not found: faithful PDF/PNG export unavailable",
        "Install LibreOffice and/or set export.libreoffice_binary; use --format preview-pdf meanwhile.",
    )
    add(
        "pdf-rasterizer",
        "ok" if backends["pdf_rasterizer"]["available"] else "warn",
        backends["pdf_rasterizer"]["binary"] or "pdftoppm/mutool not found: LibreOffice PNG export unavailable",
        "Install poppler-utils for PNG export.",
    )
    assets = [
        ("asset-capability-catalog.json", "catalog"),
        ("asset-default-config.json", "config"),
        ("asset-audit-rules.json", "rule pack"),
        ("asset-repair-policy.json", "repair policy"),
        ("asset-deck-spec-example.json", "spec example"),
        ("asset-brand-preset-corporate-blue.json", "theme"),
        ("asset-brand-preset-editorial-warm.json", "theme"),
        ("asset-brand-preset-midnight-teal.json", "theme"),
        ("asset-brief-example.md", "brief example"),
    ]
    missing = []
    for filename, label in assets:
        path = CORE.asset_path(filename)
        if not os.path.isfile(path):
            missing.append(filename)
            add("asset:%s" % label, "fail", "missing %s" % filename, "Restore the asset next to the entry point.")
    if not missing:
        asset_files = [name for name in sorted(os.listdir(HERE)) if name.startswith("asset-")]
        add("assets", "ok", "%d required assets present, %d asset files total" % (len(assets), len(asset_files)))
    try:
        catalog = CORE.load_json(CORE.asset_path("asset-capability-catalog.json"))
        add(
            "catalog-integrity",
            "ok" if catalog.get("skill_count") == 350 and catalog.get("category_count") == 50 else "fail",
            "%s categories / %s skills" % (catalog.get("category_count"), catalog.get("skill_count")),
            "Regenerate asset-capability-catalog.json from the source catalog.",
        )
    except Exception as exc:
        add("catalog-integrity", "fail", str(exc), "Restore asset-capability-catalog.json.")
    try:
        pack = AUDIT.load_rule_pack(core=CORE)
        implemented = len([rule_id for rule_id in pack["by_id"] if rule_id in AUDIT.RULE_FUNCTIONS])
        add(
            "rule-pack",
            "ok" if implemented == len(pack["by_id"]) else "warn",
            "%d rules declared, %d implemented" % (len(pack["by_id"]), implemented),
            "Remove unimplemented rules or add their implementation.",
        )
    except Exception as exc:
        add("rule-pack", "fail", str(exc), "Restore asset-audit-rules.json.")
    for filename in ("asset-brand-preset-corporate-blue.json", "asset-brand-preset-editorial-warm.json",
                     "asset-brand-preset-midnight-teal.json"):
        path = CORE.asset_path(filename)
        if not os.path.isfile(path):
            continue
        try:
            problems = CORE.validate_theme(CORE.load_json(path))
            add(
                "theme:%s" % filename.replace("asset-brand-preset-", "").replace(".json", ""),
                "ok" if not problems else "warn",
                "valid" if not problems else "; ".join(problems),
            )
        except Exception as exc:
            add("theme:%s" % filename, "fail", str(exc))
    try:
        spec = CORE.load_json(CORE.asset_path("asset-deck-spec-example.json"))
        CORE.validate_spec(spec, config=ctx.config, spec_path=CORE.asset_path("asset-deck-spec-example.json"))
        add("spec-example", "ok", "asset-deck-spec-example.json validates")
    except Exception as exc:
        add("spec-example", "fail", str(exc), "Fix the example spec; it is also the schema reference.")
    try:
        probe = os.path.join(HERE, ".write-probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        add("writable-skill-root", "ok", HERE)
    except Exception as exc:
        add("writable-skill-root", "warn", "%s (use an --out path outside the skill folder)" % exc)
    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    lines = ["powerpoint-agent doctor", ""]
    for check in checks:
        marker = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[fail]"}[check["status"]]
        lines.append("%s %-22s %s" % (marker, check["check"], check["detail"]))
        if check.get("fix") and check["status"] != "ok":
            lines.append("        fix: %s" % check["fix"])
    lines.append("")
    lines.append("%d checks, %d warnings, %d failures" % (len(checks), len(warnings), len(failures)))
    return {
        "summary": "\n".join(lines),
        "data": {"checks": checks, "warnings": len(warnings), "failures": len(failures)},
        "exit_code": CORE.EXIT_OK if not failures else CORE.EXIT_DEPENDENCY,
    }


@command("themes", "List or validate bundled brand/theme presets.", role="Formatter")
def cmd_themes(args, ctx: Context) -> Dict[str, Any]:
    action = (args.action or "list").lower()
    if action == "list":
        presets = CORE.list_theme_presets()
        lines = ["Bundled theme presets:", ""]
        for item in presets:
            lines.append("- %s (%s)%s" % (item["name"], item["file"], "" if not item["problem_count"] else "  [%d validation problems]" % item["problem_count"]))
            if item["description"]:
                lines.append("    %s" % item["description"])
        return {"summary": "\n".join(lines), "data": {"presets": presets}}
    if action == "validate":
        target = args.theme or ""
        if not target:
            raise CORE.UsageError("themes --action validate needs --theme <preset-name-or-json-path>.")
        data = CORE.load_json(target) if os.path.isfile(target) else CORE.load_json(
            CORE.asset_path(
                {
                    "corporate-blue": "asset-brand-preset-corporate-blue.json",
                    "editorial-warm": "asset-brand-preset-editorial-warm.json",
                    "midnight-teal": "asset-brand-preset-midnight-teal.json",
                }.get(target, "")
            )
        )
        problems = CORE.validate_theme(data)
        theme = CORE.Theme(data)
        summary = "Theme '%s' is valid." % theme.name if not problems else "Theme '%s' has %d problem(s):\n%s" % (
            theme.name,
            len(problems),
            "\n".join("- %s" % problem for problem in problems),
        )
        return {
            "summary": summary,
            "data": {"name": theme.name, "problems": problems, "colors": theme.colors, "fonts": theme.fonts},
            "exit_code": CORE.EXIT_OK if not problems else CORE.EXIT_VALIDATION,
        }
    raise CORE.UsageError("Unknown themes action '%s' (use list or validate)." % action)


@command("catalog", "Search the 50-category / 350-skill capability catalog.", role="meta")
def cmd_catalog(args, ctx: Context) -> Dict[str, Any]:
    catalog = CORE.load_json(CORE.asset_path("asset-capability-catalog.json"))
    action = (args.action or "list").lower()
    categories = catalog.get("categories") or []

    if action == "categories":
        rows = [
            {"number": item["number"], "name": item["name"], "slug": item["slug"], "skills": len(item["skills"])}
            for item in categories
        ]
        lines = ["%3s  %-62s %s" % ("#", "CATEGORY", "SKILLS")]
        lines.extend("%3d  %-62s %d" % (row["number"], row["name"], row["skills"]) for row in rows)
        return {"summary": "\n".join(lines), "data": {"category_count": len(rows), "categories": rows}}

    if action == "show":
        needle = (args.skill or "").strip().lower()
        if not needle:
            raise CORE.UsageError("catalog --action show needs --skill <id|name>.")
        match = None
        for category in categories:
            for skill in category["skills"]:
                if skill["skill_id"].lower() == needle or skill["name"].lower() == needle:
                    match = (category, skill)
                    break
            if match:
                break
        if not match:
            raise CORE.ValidationError(
                "No catalog skill matches '%s'." % args.skill,
                hint="Run 'catalog --action list --search <text>' to find ids.",
            )
        category, skill = match
        lines = [
            "%s — %s" % (skill["skill_id"], skill["name"]),
            "Category %d: %s" % (category["number"], category["name"]),
            "Role: %s (operation: %s)" % (skill["role"], skill["operation"]),
            "",
            skill["description"],
            "",
            "Run it with: pptx-agent %s ..." % skill["operation"],
        ]
        role_meta = (catalog.get("roles") or {}).get(skill["role"], {})
        if role_meta.get("use_when"):
            lines.append("Use when: %s" % role_meta["use_when"])
        if role_meta.get("core_checks"):
            lines.append("Core checks: %s" % role_meta["core_checks"])
        return {"summary": "\n".join(lines), "data": {"category": category["name"], "skill": skill}}

    # list
    selected_categories = categories
    if args.category:
        needle = str(args.category).strip().lower()
        selected_categories = [
            item
            for item in categories
            if str(item["number"]) == needle or item["slug"] == needle or item["name"].lower() == needle
        ]
        if not selected_categories:
            raise CORE.ValidationError("No category matches '%s'." % args.category)
    search = (args.search or "").strip().lower()
    role = (args.role or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for category in selected_categories:
        for skill in category["skills"]:
            if role and skill["role"].lower() != role:
                continue
            if search and search not in ("%s %s %s" % (skill["name"], skill["description"], category["name"])).lower():
                continue
            rows.append(
                {
                    "skill_id": skill["skill_id"],
                    "name": skill["name"],
                    "role": skill["role"],
                    "operation": skill["operation"],
                    "category": category["name"],
                    "description": skill["description"],
                }
            )
    limited = rows[: args.limit or 200]
    lines = ["%d matching skill(s) of %d total" % (len(rows), catalog.get("skill_count", 0)), ""]
    for row in limited:
        lines.append("%s  %-11s %s" % (row["skill_id"], row["role"], row["name"]))
    if len(rows) > len(limited):
        lines.append("... %d more (raise --limit)" % (len(rows) - len(limited)))
    return {
        "summary": "\n".join(lines),
        "data": {"match_count": len(rows), "returned": len(limited), "skills": limited},
    }


# ---------------------------------------------------------------------------
# command: plan
# ---------------------------------------------------------------------------

NARRATIVES: Dict[str, List[Dict[str, Any]]] = {
    "exec-brief": [
        {"layout": "title", "title": "Title slide", "purpose": "State the decision, audience, and date.", "category": 6},
        {"layout": "bullets", "title": "Executive summary", "purpose": "Answer the question in three bullets.", "category": 3},
        {"layout": "bullets", "title": "Situation and context", "purpose": "Establish the shared starting point.", "category": 3},
        {"layout": "kpi", "title": "Key metrics", "purpose": "Show the three or four numbers that matter.", "category": 16},
        {"layout": "chart", "title": "Evidence", "purpose": "Plot the trend behind the recommendation.", "category": 16},
        {"layout": "bullets", "title": "Recommendation", "purpose": "State the decision and the trade-offs.", "category": 3},
        {"layout": "table", "title": "Plan and owners", "purpose": "Sequence work with owners and dates.", "category": 15},
        {"layout": "bullets", "title": "Risks and mitigations", "purpose": "Name what could go wrong and the guardrails.", "category": 8},
        {"layout": "bullets", "title": "Next steps", "purpose": "Close with the specific ask.", "category": 3},
        {"layout": "closing", "title": "Closing", "purpose": "Thank the room and give a contact.", "category": 43},
    ],
    "training": [
        {"layout": "title", "title": "Title slide", "purpose": "Course name, audience, duration.", "category": 48},
        {"layout": "bullets", "title": "Learning objectives", "purpose": "Three measurable outcomes.", "category": 42},
        {"layout": "bullets", "title": "Agenda", "purpose": "Set expectations for the session.", "category": 3},
        {"layout": "bullets", "title": "Core concept", "purpose": "Teach the model in one screen.", "category": 8},
        {"layout": "image-right", "title": "Worked example", "purpose": "Walk through a real case.", "category": 13},
        {"layout": "bullets", "title": "Practice", "purpose": "Give an exercise with clear steps.", "category": 42},
        {"layout": "quote", "title": "Common pitfall", "purpose": "Name the mistake learners make.", "category": 42},
        {"layout": "bullets", "title": "Recap", "purpose": "Repeat the three takeaways.", "category": 3},
        {"layout": "table", "title": "Assessment", "purpose": "List the checks for understanding.", "category": 42},
        {"layout": "closing", "title": "Closing", "purpose": "Point to the follow-up materials.", "category": 48},
    ],
    "research": [
        {"layout": "title", "title": "Title slide", "purpose": "Paper title, authors, venue.", "category": 44},
        {"layout": "bullets", "title": "Research question", "purpose": "State the question and why it matters.", "category": 44},
        {"layout": "bullets", "title": "Method", "purpose": "Describe the design and data.", "category": 44},
        {"layout": "table", "title": "Results", "purpose": "Report the primary numbers in a table.", "category": 15},
        {"layout": "chart", "title": "Primary result", "purpose": "Plot the effect size with uncertainty.", "category": 16},
        {"layout": "bullets", "title": "Discussion", "purpose": "Interpret the finding against prior work.", "category": 44},
        {"layout": "bullets", "title": "Limitations", "purpose": "Be explicit about scope and threats.", "category": 44},
        {"layout": "bullets", "title": "Conclusion", "purpose": "State what is now known.", "category": 44},
        {"layout": "bullets", "title": "References", "purpose": "Cite the key sources.", "category": 44},
        {"layout": "closing", "title": "Closing", "purpose": "Invite questions and share the contact.", "category": 44},
    ],
    "sales": [
        {"layout": "title", "title": "Title slide", "purpose": "Customer name and the promise.", "category": 43},
        {"layout": "bullets", "title": "The problem", "purpose": "Mirror the customer's situation.", "category": 43},
        {"layout": "kpi", "title": "Business impact", "purpose": "Quantify the cost of the status quo.", "category": 16},
        {"layout": "bullets", "title": "The solution", "purpose": "Show how the offer removes the friction.", "category": 43},
        {"layout": "chart", "title": "Proof", "purpose": "Show measured results.", "category": 16},
        {"layout": "quote", "title": "Customer voice", "purpose": "Let a reference customer speak.", "category": 45},
        {"layout": "table", "title": "Commercials", "purpose": "Present packages and terms.", "category": 15},
        {"layout": "bullets", "title": "Next steps", "purpose": "Agree the next meeting and owner.", "category": 43},
        {"layout": "closing", "title": "Closing", "purpose": "Thank the customer and confirm the follow-up.", "category": 45},
    ],
    "general": [
        {"layout": "title", "title": "Title slide", "purpose": "Name the topic and audience.", "category": 6},
        {"layout": "bullets", "title": "Agenda", "purpose": "Preview the structure.", "category": 4},
        {"layout": "bullets", "title": "Context", "purpose": "Explain why this matters now.", "category": 3},
        {"layout": "bullets", "title": "Key points", "purpose": "Deliver the core message.", "category": 8},
        {"layout": "chart", "title": "Data", "purpose": "Support the message with evidence.", "category": 16},
        {"layout": "bullets", "title": "Recommendation", "purpose": "State what should happen.", "category": 3},
        {"layout": "bullets", "title": "Next steps", "purpose": "Close with owners and timing.", "category": 3},
        {"layout": "closing", "title": "Closing", "purpose": "Thank the audience and share a contact.", "category": 43},
    ],
}

BRIEF_FIELDS = {
    "goal": ["goal", "objective", "purpose"],
    "audience": ["audience", "attendees", "readers"],
    "constraints": ["constraint", "constraints", "limits", "requirements", "must"],
    "success_criteria": ["success", "acceptance", "definition of done", "criteria"],
    "deliverable": ["deliverable", "output", "format"],
    "brand": ["brand", "style", "theme", "template"],
    "length": ["length", "slide count", "duration", "minutes"],
    "avoid": ["avoid", "do not", "don't", "exclude"],
}


def _join_constraints(items: Sequence[str]) -> str:
    """Join brief lines into one printable sentence without doubling punctuation."""

    parts: List[str] = []
    for item in items:
        text = str(item).strip().rstrip(".;,")
        if text:
            parts.append(text)
    return "; ".join(parts)


def _parse_brief(text: str) -> Dict[str, Any]:
    lines = [line.strip() for line in text.splitlines()]
    fields: Dict[str, List[str]] = {key: [] for key in BRIEF_FIELDS}
    bullets: List[str] = []
    current: Optional[str] = None
    for line in lines:
        if not line:
            current = None
            continue
        cleaned = re.sub(r"^#+\s*", "", line)
        cleaned = re.sub(r"^[-*+]\s+", "", cleaned)
        cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)
        stripped = cleaned.strip()
        key_hit = None
        for key, aliases in BRIEF_FIELDS.items():
            for alias in aliases:
                pattern = r"^%s\s*[:\-]\s*(.*)$" % re.escape(alias)
                match = re.match(pattern, stripped, flags=re.IGNORECASE)
                if match:
                    key_hit = key
                    value = match.group(1).strip()
                    if value:
                        fields[key].append(value)
                    break
            if key_hit:
                break
        if key_hit:
            current = key_hit
            continue
        if current and stripped:
            fields[current].append(stripped)
            continue
        if len(stripped) > 3:
            bullets.append(stripped)
    goal = " ".join(fields["goal"]) or (bullets[0] if bullets else "")
    keywords = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", text)]
    stop = {"about", "after", "before", "between", "could", "should", "would", "their", "there", "these", "those",
            "which", "while", "with", "from", "that", "this", "have", "will", "make", "must", "each", "into",
            "need", "needs", "using", "used", "your", "yours", "presentation", "slides", "slide"}
    counts: Dict[str, int] = {}
    for word in keywords:
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    topics = [word for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]]
    return {
        "goal": goal or "(goal not stated in the brief; confirm it before building)",
        "audience": " ".join(fields["audience"]) or "(audience not stated; confirm before building)",
        "constraints": fields["constraints"],
        "success_criteria": fields["success_criteria"],
        "deliverable": " ".join(fields["deliverable"]),
        "brand": " ".join(fields["brand"]),
        "length": " ".join(fields["length"]),
        "avoid": fields["avoid"],
        "topics": topics,
        "bullet_count": len(bullets),
        "raw_excerpt": CORE.truncate(" ".join(bullets), 600),
    }


@command("plan", "Design a presentation plan, outline, routing, and acceptance checks from a brief.", role="Architect")
def cmd_plan(args, ctx: Context) -> Dict[str, Any]:
    if args.brief and args.brief_text:
        raise CORE.UsageError("Use either --brief or --brief-text, not both.")
    if not args.brief and not args.brief_text:
        raise CORE.UsageError("plan needs --brief <file.md> or --brief-text \"...\".")
    text = _read_text_file(args.brief, label="brief") if args.brief else str(args.brief_text)
    if not text.strip():
        raise CORE.ValidationError("The brief is empty.")
    narrative = (args.narrative or "general").lower()
    if narrative not in NARRATIVES:
        raise CORE.UsageError(
            "Unknown narrative '%s'. Choose from: %s" % (narrative, ", ".join(sorted(NARRATIVES)))
        )
    parsed = _parse_brief(text)
    catalog = CORE.load_json(CORE.asset_path("asset-capability-catalog.json"))
    category_by_number = {item["number"]: item for item in catalog["categories"]}

    template = NARRATIVES[narrative]
    outline: List[Dict[str, Any]] = []
    for index, item in enumerate(template, start=1):
        category = category_by_number.get(item["category"], {})
        outline.append(
            {
                "number": index,
                "layout": item["layout"],
                "title": item["title"],
                "purpose": item["purpose"],
                "category": category.get("name", ""),
                "category_number": item["category"],
                "skills": [skill["name"] for skill in (category.get("skills") or [])][:7],
            }
        )
    warnings: List[str] = []
    if args.slides:
        budget = int(args.slides)
        if budget < len(outline):
            warnings.append(
                "Slide budget %d is below the %d-slide narrative template; trim the outline to fit." % (budget, len(outline))
            )
        elif budget > len(outline):
            warnings.append(
                "Slide budget %d exceeds the %d-slot template; add detail slides from the brief's topics." % (budget, len(outline))
            )
    if parsed["audience"].startswith("("):
        warnings.append("No audience was stated in the brief; the plan assumes a general business audience.")

    try:
        pack = AUDIT.load_rule_pack(core=CORE)
        acceptance = [
            {
                "check": rule["title"],
                "rule_ids": [rule["id"]],
                "severity": rule["severity"],
                "how_to_verify": "pptx-agent audit --in <deck.pptx> --fail-on error",
            }
            for rule in pack["rules"]
            if rule["id"] in {"GEO001", "TYP001", "TYP004", "COL001", "A11Y001", "A11Y002", "A11Y003", "STR001",
                              "STR003", "PERF001", "CMP004", "MED003"}
        ]
    except Exception:
        acceptance = []
    plan = {
        "generated_at": _iso(),
        "tool": "powerpoint-agent %s" % TOOL_VERSION,
        "brief": {
            "goal": parsed["goal"],
            "audience": parsed["audience"],
            "constraints": parsed["constraints"],
            "success_criteria": parsed["success_criteria"],
            "deliverable": parsed["deliverable"],
            "brand": parsed["brand"],
            "length": parsed["length"],
            "avoid": parsed["avoid"],
            "topics": parsed["topics"],
            "excerpt": parsed["raw_excerpt"],
        },
        "narrative": {"name": narrative, "slide_slots": len(outline)},
        "theme": {"recommended_preset": ctx.config.get("defaults", {}).get("theme", "corporate-blue"),
                  "note": "Override with --theme at build time, or theme.overrides inside the deck spec."},
        "outline": outline,
        "acceptance_checks": acceptance,
        "workflow": [
            "1. Draft the deck spec from this outline (schema: asset-deck-spec-example.json).",
            "2. Build: pptx-agent build --spec <spec.json> --out <deck.pptx> --audit",
            "3. Format: pptx-agent format --in <deck.pptx> --out <deck.formatted.pptx> --theme <preset>",
            "4. Audit: pptx-agent audit --in <deck.pptx> --out report.json --markdown report.md --fail-on error",
            "5. Repair: pptx-agent repair --in <deck.pptx> --out <deck.fixed.pptx> --dry-run, then --approve --write",
            "6. Preview: pptx-agent preview --in <deck.pptx> --out-dir previews",
            "7. Export: pptx-agent export --in <deck.pptx> --format preview-pdf --out <deck.pdf>",
        ],
        "risks": [
            {"risk": "Audience or goal not stated in the brief", "mitigation": "Confirm both before writing slide copy."},
            {"risk": "Brand fonts unavailable on the target machine", "mitigation": "Audit with TYP002/CMP004 and substitute approved fonts."},
            {"risk": "Media or charts omitted from the plan", "mitigation": "Add image/chart/table layouts explicitly in the spec."},
        ],
        "warnings": warnings,
    }
    written = []
    if args.out:
        destination = CORE.resolve_output(args.out, overwrite=args.overwrite, exts=(".json",), label="plan output")
        CORE.dump_json(plan, destination)
        written.append(destination)
    lines = ["Presentation plan (%s narrative, %d slots)" % (narrative, len(outline)), ""]
    lines.append("Goal: %s" % parsed["goal"])
    lines.append("Audience: %s" % parsed["audience"])
    if parsed["constraints"]:
        lines.append("Constraints: %s" % _join_constraints(parsed["constraints"]))
    lines.append("")
    lines.append("%3s %-13s %-34s %s" % ("#", "LAYOUT", "TITLE", "PURPOSE"))
    for item in outline:
        lines.append("%3d %-13s %-34s %s" % (item["number"], item["layout"], item["title"][:34], item["purpose"]))
    lines.append("")
    lines.append("%d acceptance checks derived from the active rule pack." % len(acceptance))
    for warning in warnings:
        lines.append("WARN: %s" % warning)
    if written:
        lines.append("")
        lines.append("Plan written to %s" % os.path.relpath(written[0]))
    return {"summary": "\n".join(lines), "data": plan, "warnings": warnings}


# ---------------------------------------------------------------------------
# command: build
# ---------------------------------------------------------------------------

@command("build", "Build an editable deck from a validated deck spec.", role="Builder", writes=True)
def cmd_build(args, ctx: Context) -> Dict[str, Any]:
    spec_path = CORE.resolve_input(args.spec, exts=(".json",), label="deck spec")
    spec = CORE.load_json(spec_path)
    if args.theme:
        theme_section = spec.get("theme")
        if isinstance(theme_section, dict):
            theme_section["preset"] = args.theme
        elif theme_section:
            spec["theme"] = {"preset": args.theme, "overrides": {}}
        else:
            spec["theme"] = {"preset": args.theme, "overrides": {}}
    normalized, spec_warnings = CORE.validate_spec(spec, config=ctx.config, spec_path=spec_path)
    destination = CORE.resolve_output(args.out, overwrite=args.overwrite, exts=DECK_EXT, label="output deck")
    ctx.log.step("building deck", spec=os.path.basename(spec_path), slides=len(normalized["slides"]))
    prs, build_warnings, meta = CORE.build_deck(
        normalized, config=ctx.config, log=ctx.log, into=args.into, deadline=ctx.deadline
    )
    CORE.save_presentation(prs, destination)

    verification: Dict[str, Any] = {"reopened": False, "slide_count": 0, "issues": []}
    try:
        reopened = CORE.open_presentation(destination)
        verification["reopened"] = True
        verification["slide_count"] = len(reopened.slides)
        if verification["slide_count"] != meta["slide_count"]:
            verification["issues"].append(
                "Reopened deck has %d slides but %d were built." % (verification["slide_count"], meta["slide_count"])
            )
        titles_missing = [
            index + 1 for index, slide in enumerate(reopened.slides) if not CORE.slide_title_text(slide).strip()
        ]
        if titles_missing:
            verification["issues"].append("Slides without a detectable title: %s" % titles_missing)
        verification["sections"] = CORE.read_sections(reopened)
        verification["bytes"] = os.path.getsize(destination)
    except Exception as exc:
        verification["issues"].append("Reopen failed: %s: %s" % (type(exc).__name__, exc))

    audit_summary = None
    if args.audit:
        pack = AUDIT.load_rule_pack(core=CORE)
        report = AUDIT.audit_deck(destination, config=ctx.config, pack=pack, log=ctx.log, deadline=ctx.deadline, core=CORE)
        audit_summary = {
            "score": report["score"],
            "counts": report["counts"],
            "gate": report["gate"],
        }
        if args.audit_out:
            CORE.dump_json(report, CORE.resolve_output(args.audit_out, overwrite=True, exts=(".json",), label="audit output"))

    warnings = list(spec_warnings) + [item["message"] for item in build_warnings]
    result = {
        "output": os.path.basename(destination),
        "output_path": destination,
        "slide_count": meta["slide_count"],
        "theme": meta["theme"],
        "sections": verification.get("sections", []),
        "bytes": os.path.getsize(destination),
        "verification": verification,
        "audit": audit_summary,
        "warnings": warnings,
        "slides": meta["built"],
    }
    lines = [
        "Built %d slides -> %s (%s)" % (meta["slide_count"], os.path.basename(destination), CORE.human_bytes(result["bytes"])),
        "Theme: %s" % meta["theme"],
        "Reopened and verified: %s" % ("yes" if verification["reopened"] else "NO"),
    ]
    if verification.get("sections"):
        lines.append("Sections: %s" % ", ".join("%s (%d slides)" % (s["name"], len(s["slide_indexes"])) for s in verification["sections"]))
    if audit_summary:
        lines.append(
            "Audit: score %s · %s errors · %s warnings · gate %s"
            % (
                audit_summary["score"],
                audit_summary["counts"].get("error", 0),
                audit_summary["counts"].get("warning", 0),
                "PASS" if audit_summary["gate"]["passed"] else "FAIL",
            )
        )
    for warning in warnings[:20]:
        lines.append("WARN: %s" % warning)
    for issue in verification["issues"]:
        lines.append("ISSUE: %s" % issue)
    if not verification["reopened"] or verification["issues"]:
        return {"summary": "\n".join(lines), "data": result, "warnings": warnings, "exit_code": CORE.EXIT_VALIDATION}
    return {"summary": "\n".join(lines), "data": result, "warnings": warnings}


# ---------------------------------------------------------------------------
# command: format
# ---------------------------------------------------------------------------

@command("format", "Normalise typography, theme canvas, placeholders, footer, and slide numbers.", role="Formatter", writes=True)
def cmd_format(args, ctx: Context) -> Dict[str, Any]:
    result = EDIT.format_deck(
        args.input,
        args.out,
        config=ctx.config,
        theme_name=args.theme,
        force_fonts=(True if args.force_fonts else (False if args.keep_fonts else None)),
        min_font_pt=args.min_font_size,
        add_footer=not args.no_footer,
        add_slide_numbers=not args.no_slide_numbers,
        footer_text=args.footer_text or "",
        remove_empty_placeholders=(False if args.keep_empty_placeholders else None),
        repaint_backgrounds=(True if args.repaint_backgrounds else (False if args.keep_backgrounds else None)),
        overwrite=args.overwrite,
        log=ctx.log,
        deadline=ctx.deadline,
    )
    stats = result["stats"]
    lines = [
        "Formatted %s -> %s (theme %s)" % (result["file"], result["output"], result["theme"]),
        "Slides touched: %d of %d" % (stats["slides_touched"], len(result["slides"])),
        "Fonts changed: %d · sizes raised: %d · geometry fixed: %d" % (
            stats["fonts_changed"], stats["sizes_raised"], stats["geometry_normalised"]),
        "Empty placeholders removed: %d · footers added: %d · slide numbers added: %d" % (
            stats["placeholders_removed"], stats["footers_added"], stats["slide_numbers_added"]),
        "Backgrounds repainted: %d · kept: %d · text colours adjusted: %d"
        % (stats["backgrounds_repainted"], stats["backgrounds_kept"], stats["colors_adjusted"]),
        "Reopened and verified: %s" % ("yes" if result["verification"].get("reopened") else "NO"),
    ]
    return {"summary": "\n".join(lines), "data": result}


# ---------------------------------------------------------------------------
# command: audit
# ---------------------------------------------------------------------------

@command("audit", "Audit a deck against the rule pack; score, findings, gate, and reports.", role="Auditor")
def cmd_audit(args, ctx: Context) -> Dict[str, Any]:
    pack = AUDIT.load_rule_pack(args.rule_pack, core=CORE)
    only = [item.strip().upper() for item in (args.only or "").split(",") if item.strip()]
    if only:
        unknown = [rule_id for rule_id in only if rule_id not in pack["by_id"]]
        if unknown:
            raise CORE.ValidationError("Unknown rule id(s) in --only: %s" % ", ".join(unknown))
        pack["disabled"] = {rule_id for rule_id in pack["by_id"] if rule_id not in only}
    report = AUDIT.audit_deck(
        args.input,
        config=ctx.config,
        pack=pack,
        log=ctx.log,
        deadline=ctx.deadline,
        fail_on=args.fail_on,
        core=CORE,
    )
    written: Dict[str, str] = {}
    if args.out:
        destination = CORE.resolve_output(args.out, overwrite=True, exts=(".json",), label="audit JSON")
        CORE.dump_json(report, destination)
        written["json"] = destination
    if args.markdown:
        destination = CORE.resolve_output(args.markdown, overwrite=True, exts=(".md",), label="audit Markdown")
        CORE.write_text(AUDIT.report_to_markdown(report), destination)
        written["markdown"] = destination
    if args.csv:
        destination = CORE.resolve_output(args.csv, overwrite=True, exts=(".csv",), label="audit CSV")
        CORE.write_text(AUDIT.report_to_csv(report), destination)
        written["csv"] = destination
    counts = report["counts"]
    lines = [
        "Audit of %s: score %s/100 · %d errors · %d warnings · %d info"
        % (report["file"], report["score"], counts.get("error", 0), counts.get("warning", 0), counts.get("info", 0)),
        "Gate: fail on %s -> %s" % (report["gate"]["fail_on"], "PASS" if report["gate"]["passed"] else "FAIL"),
        "Rules executed: %d (skipped %d)" % (report["checks_run"], len(report["checks_skipped"])),
    ]
    for finding in report["findings"][:25]:
        where = "slide %s" % finding["slide"] if finding["slide"] else "deck"
        lines.append("  [%s] %s %s: %s" % (finding["severity"][:4].upper(), finding["rule_id"], where, finding["message"]))
    if len(report["findings"]) > 25:
        lines.append("  ... %d more findings" % (len(report["findings"]) - 25))
    for label, path in written.items():
        lines.append("%s report: %s" % (label.upper(), os.path.relpath(path)))
    exit_code = CORE.EXIT_OK if report["gate"]["passed"] else CORE.EXIT_GATE_FAILED
    return {"summary": "\n".join(lines), "data": dict(report, reports=written), "exit_code": exit_code}


# ---------------------------------------------------------------------------
# command: repair
# ---------------------------------------------------------------------------

@command("repair", "Apply safe, recorded fixes to a deck; dry run by default.", role="Repairer", writes=True, approval=True)
def cmd_repair(args, ctx: Context) -> Dict[str, Any]:
    dry_run = not args.write
    if not dry_run:
        _require_approval(args, "Repair --write")
    policy = REPAIR.load_policy(args.policy, core=CORE)
    rules = [item.strip().upper() for item in (args.rules or "").split(",") if item.strip()]
    result = REPAIR.repair_deck(
        args.input,
        args.out,
        config=ctx.config,
        policy=policy,
        pack=AUDIT.load_rule_pack(args.rule_pack, core=CORE),
        rules=rules or None,
        approve=args.approve,
        dry_run=dry_run,
        theme_name=args.theme,
        log=ctx.log,
        deadline=ctx.deadline,
        overwrite=args.overwrite,
        core=CORE,
        audit=AUDIT,
    )
    written = {}
    if args.report:
        destination = CORE.resolve_output(args.report, overwrite=True, exts=(".md",), label="repair report")
        CORE.write_text(REPAIR.repair_report_markdown(result), destination)
        written["markdown"] = destination
    if args.json_report:
        destination = CORE.resolve_output(args.json_report, overwrite=True, exts=(".json",), label="repair JSON")
        CORE.dump_json(result, destination)
        written["json"] = destination
    before = result["before"]
    after = result.get("after") or {}
    if result["change_count"]:
        lines = [
            "%s %s" % ("Dry run:" if dry_run else "Repaired:", result["file"]),
            "Changes: %d across rules %s" % (result["change_count"], ", ".join(result["applied_rule_ids"])),
            "Score before: %s (%s errors) -> after: %s (%s errors)"
            % (
                before.get("score"),
                (before.get("counts") or {}).get("error", 0),
                after.get("score", "-"),
                (after.get("counts") or {}).get("error", 0) if after else "-",
            ),
            "Manual findings remaining: %d" % result["manual_count"],
        ]
    else:
        lines = [
            "%s %s" % ("Dry run:" if dry_run else "Repaired:", result["file"]),
            "No fixable findings: score %s, %s errors, %s warnings."
            % (before.get("score"), (before.get("counts") or {}).get("error", 0),
               (before.get("counts") or {}).get("warning", 0)),
            "Manual findings reported: %d" % result["manual_count"],
        ]
    if dry_run and result["change_count"]:
        lines.append(
            "Nothing was written. Rerun with --write --approve to publish the repaired deck to %s"
            % os.path.basename(result.get("output_path") or args.out)
        )
    elif dry_run:
        lines.append("Nothing to write: no fix was applied in this run.")
    elif result.get("output"):
        lines.append("Output: %s" % result["output"])
    for label, path in written.items():
        lines.append("%s report: %s" % (label.upper(), os.path.relpath(path)))
    return {"summary": "\n".join(lines), "data": dict(result, reports=written)}


# ---------------------------------------------------------------------------
# command: optimize
# ---------------------------------------------------------------------------

@command("optimize", "Downscale/recompress media and report size savings; dry run by default.", role="Optimizer", writes=True, approval=True)
def cmd_optimize(args, ctx: Context) -> Dict[str, Any]:
    dry_run = not args.write
    if not dry_run:
        _require_approval(args, "Optimize --write")
    result = MEDIA.optimize_deck(
        args.input,
        args.out,
        config=ctx.config,
        approve=args.approve,
        dry_run=dry_run,
        max_dimension=args.max_dimension,
        jpeg_quality=args.quality,
        drop_unused=args.drop_unused_media if args.drop_unused_media else None,
        log=ctx.log,
        deadline=ctx.deadline,
        overwrite=args.overwrite,
        core=CORE,
    )
    written = {}
    if args.report:
        destination = CORE.resolve_output(args.report, overwrite=True, exts=(".md",), label="optimize report")
        CORE.write_text(MEDIA.optimize_report_markdown(result), destination)
        written["markdown"] = destination
    before_bytes = result.get("before_bytes") or 0
    projected = before_bytes - (result.get("saved_total_bytes") or 0)
    lines = [
        "%s %s" % ("Dry run:" if dry_run else "Optimized:", result["file"]),
        "Size: %s -> %s (%s %s, %s%%)"
        % (
            CORE.human_bytes(before_bytes),
            CORE.human_bytes(projected if dry_run else (result.get("after_bytes") or 0)),
            "projected saving" if dry_run else "saved",
            CORE.human_bytes(result.get("saved_total_bytes") or 0),
            result.get("saved_percent", 0.0),
        ),
        "Images recompressed: %d · unused parts dropped: %d"
        % (result.get("change_count", 0), len(result.get("dropped_parts") or [])),
    ]
    verification = result.get("verification") or {}
    if verification:
        lines.append(
            "Staged copy verified: reopened %s · %s slides · %s pictures decoded"
            % ("yes" if verification.get("reopened") else "NO", verification.get("slides", 0), verification.get("pictures_checked", 0))
        )
    if dry_run:
        lines.append("Nothing was written. Rerun with --write --approve to publish the optimized deck.")
    elif result.get("output"):
        lines.append("Output: %s" % result["output"])
    for label, path in written.items():
        lines.append("%s report: %s" % (label.upper(), os.path.relpath(path)))
    return {"summary": "\n".join(lines), "data": dict(result, reports=written)}


# ---------------------------------------------------------------------------
# command: inspect / outline
# ---------------------------------------------------------------------------

@command("inspect", "Report structure, fonts, colours, media, and per-slide statistics.", role="Auditor")
def cmd_inspect(args, ctx: Context) -> Dict[str, Any]:
    prs = CORE.open_presentation(args.input)
    stats = CORE.deck_stats(prs, CORE.resolve_input(args.input))
    if args.out:
        destination = CORE.resolve_output(args.out, overwrite=True, exts=(".json",), label="stats output")
        CORE.dump_json(stats, destination)
        stats["output_path"] = destination
    lines = [
        "Inspection: %s" % stats["file"],
        "Slides: %d · size %.2f x %.2f in (%s) · %s"
        % (stats["slide_count"], stats["slide_size"]["width_in"] or 0, stats["slide_size"]["height_in"] or 0,
           stats["aspect_ratio"], CORE.human_bytes(stats["bytes"] or 0)),
        "Content: %d words · %d pictures · %d charts · %d tables · notes on %d slides"
        % (stats["total_words"], stats["pictures"], stats["charts"], stats["tables"], stats["slides_with_notes"]),
        "Media: %d part(s), %s" % (len(stats["media"]), CORE.human_bytes(stats["media_bytes"])),
    ]
    if stats["fonts"]:
        lines.append("Fonts: %s" % ", ".join("%s (%d)" % (name, count) for name, count in list(stats["fonts"].items())[:8]))
    if stats["sections"]:
        lines.append("Sections: %s" % ", ".join("%s [%s]" % (section["name"], ",".join(map(str, section["slide_indexes"]))) for section in stats["sections"]))
    lines.append("")
    lines.append("%4s %-46s %6s %6s %s" % ("#", "TITLE", "SHAPES", "WORDS", "NOTES"))
    for slide in stats["slides"]:
        lines.append(
            "%4d %-46s %6d %6d %s"
            % (slide["index"], CORE.truncate(slide["title"], 46), slide["shape_count"], slide["words"], "yes" if slide["has_notes"] else "-")
        )
    return {"summary": "\n".join(lines), "data": stats}


@command("outline", "Export the deck outline as Markdown, JSON, or plain text.", role="Auditor")
def cmd_outline(args, ctx: Context) -> Dict[str, Any]:
    fmt = (args.format or "md").lower()
    if fmt not in ("md", "markdown", "json", "txt", "text"):
        raise CORE.UsageError("--format must be md, json, or txt.")
    prs = CORE.open_presentation(args.input)
    outline = CORE.extract_outline(prs)
    if fmt in ("md", "markdown"):
        payload = CORE.outline_to_markdown(outline, title=args.title or "Deck outline")
    elif fmt == "json":
        payload = json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
    else:
        payload = CORE.outline_to_text(outline)
    written = None
    if args.out:
        extension = ".json" if fmt == "json" else (".md" if fmt in ("md", "markdown") else ".txt")
        destination = CORE.resolve_output(args.out, overwrite=True, exts=(extension,), label="outline output")
        CORE.write_text(payload, destination)
        written = destination
    lines = ["Outline of %s (%d slides)" % (os.path.basename(args.input), outline["slide_count"]), ""]
    lines.append(payload if not written else payload[:4000])
    if written:
        lines.append("")
        lines.append("Written to %s" % os.path.relpath(written))
    return {"summary": "\n".join(lines), "data": dict(outline, output_path=written)}


# ---------------------------------------------------------------------------
# command: notes
# ---------------------------------------------------------------------------

@command("notes", "Check note coverage, scaffold structured notes, or export notes to Markdown.", role="Builder", writes=True)
def cmd_notes(args, ctx: Context) -> Dict[str, Any]:
    action = (args.action or "check").lower()
    prs = CORE.open_presentation(args.input)
    if action == "check":
        status = EDIT.notes_status(prs)
        lines = [
            "Speaker notes: %d of %d slides covered (%s%%)"
            % (status["slides_with_notes"], status["slide_count"], status["coverage_percent"]),
            "Total notes: %d words ≈ %.1f minutes at 130 wpm"
            % (status["total_notes_words"], status["estimated_speaking_minutes"]),
        ]
        missing = [slide["slide"] for slide in status["slides"] if not slide["has_notes"]]
        if missing:
            lines.append("Slides without notes: %s" % ", ".join(map(str, missing[:40])))
        return {"summary": "\n".join(lines), "data": status}
    if action == "scaffold":
        if not args.out:
            raise CORE.UsageError("notes --action scaffold needs --out <new-deck.pptx>.")
        destination = CORE.resolve_output(args.out, overwrite=args.overwrite, exts=DECK_EXT, label="output deck")
        if os.path.abspath(destination) == os.path.abspath(CORE.resolve_input(args.input)):
            raise CORE.SafetyError("Notes scaffolding must write to a new file.")
        result = EDIT.scaffold_notes(prs)
        CORE.save_presentation(prs, destination)
        lines = [
            "Scaffolded notes for %d slide(s) -> %s" % (result["notes_added"], os.path.basename(destination)),
            "Scaffolds contain TITLE / TALKING POINTS / TRANSITION / TIMING placeholders to fill in, not generated script.",
        ]
        return {"summary": "\n".join(lines), "data": dict(result, output_path=destination)}
    if action == "export":
        if not args.out:
            raise CORE.UsageError("notes --action export needs --out <notes.md>.")
        payload = EDIT.export_notes_markdown(prs, title=args.title or "Speaker notes")
        destination = CORE.resolve_output(args.out, overwrite=True, exts=(".md",), label="notes export")
        CORE.write_text(payload, destination)
        return {
            "summary": "Exported speaker notes to %s (%d characters)" % (os.path.relpath(destination), len(payload)),
            "data": {"output_path": destination, "characters": len(payload)},
        }
    raise CORE.UsageError("Unknown notes action '%s' (check, scaffold, export)." % action)


# ---------------------------------------------------------------------------
# command: sections
# ---------------------------------------------------------------------------

@command("sections", "List or rewrite PowerPoint sections.", role="Builder", writes=True)
def cmd_sections(args, ctx: Context) -> Dict[str, Any]:
    action = (args.action or "list").lower()
    prs = CORE.open_presentation(args.input)
    if action == "list":
        result = EDIT.sections_list(prs)
        lines = ["%s: %d section(s) across %d slides" % (os.path.basename(args.input), result["section_count"], result["slide_count"])]
        for section in result["sections"]:
            lines.append("- %s: slides %s" % (section["name"], ", ".join(map(str, section["slide_indexes"]))))
        if not result["sections"]:
            lines.append("  (no section list found in this deck)")
        return {"summary": "\n".join(lines), "data": result}
    if action == "set":
        if not args.out:
            raise CORE.UsageError("sections --action set needs --out <new-deck.pptx>.")
        if not args.map and not args.sections_json:
            raise CORE.UsageError("sections --action set needs --map \"Name:1,2;Name2:3-5\" or --sections-json <file>.")
        if args.map and args.sections_json:
            raise CORE.UsageError("Use either --map or --sections-json, not both.")
        mapping: List[Dict[str, Any]] = []
        if args.sections_json:
            payload = CORE.load_json(CORE.resolve_input(args.sections_json, exts=(".json",), label="sections JSON"))
            if isinstance(payload, dict):
                payload = payload.get("sections")
            if not isinstance(payload, list):
                raise CORE.ValidationError("sections JSON must be a list or an object with a 'sections' list.")
            mapping = payload
        else:
            for chunk in str(args.map).split(";"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                name, _, indexes = chunk.partition(":")
                if not indexes:
                    raise CORE.UsageError("Section entry '%s' needs the form Name:1,2,3." % chunk)
                parsed: List[int] = []
                for token in indexes.split(","):
                    token = token.strip()
                    if not token:
                        continue
                    if "-" in token:
                        start_text, _, end_text = token.partition("-")
                        parsed.extend(range(int(start_text), int(end_text) + 1))
                    else:
                        parsed.append(int(token))
                mapping.append({"name": name.strip(), "slide_indexes": parsed})
        result = EDIT.sections_set(prs, mapping)
        destination = CORE.resolve_output(args.out, overwrite=args.overwrite, exts=DECK_EXT, label="output deck")
        if os.path.abspath(destination) == os.path.abspath(CORE.resolve_input(args.input)):
            raise CORE.SafetyError("Section rewriting must write to a new file.")
        CORE.save_presentation(prs, destination)
        lines = ["Wrote %d section(s) covering %d slides -> %s" % (result["sections_written"], result["slides_assigned"], os.path.basename(destination))]
        for section in result["sections"]:
            lines.append("- %s: slides %s" % (section["name"], ", ".join(map(str, section["slide_indexes"]))))
        return {"summary": "\n".join(lines), "data": dict(result, output_path=destination)}
    raise CORE.UsageError("Unknown sections action '%s' (list, set)." % action)


# ---------------------------------------------------------------------------
# command: search / replace
# ---------------------------------------------------------------------------

@command("search", "Find text across slides and shapes.", role="Auditor")
def cmd_search(args, ctx: Context) -> Dict[str, Any]:
    prs = CORE.open_presentation(args.input)
    result = EDIT.search_text(
        prs, args.pattern, regex=bool(args.regex), case_sensitive=bool(args.case_sensitive), core=CORE
    )
    lines = ["%d match(es) for %r across %d slide(s)" % (result["match_count"], result["pattern"], len(result["slides_with_matches"]))]
    for match in result["matches"][:40]:
        lines.append("  slide %-3d %-24s %s" % (match["slide"], CORE.truncate(match["shape"], 24), match["text"]))
    if result["match_count"] > 40:
        lines.append("  ... %d more" % (result["match_count"] - 40))
    return {"summary": "\n".join(lines), "data": result}


@command("replace", "Replace text across runs, with a paragraph-level fallback.", role="Formatter", writes=True, approval=True)
def cmd_replace(args, ctx: Context) -> Dict[str, Any]:
    if args.replacement is None:
        raise CORE.UsageError("replace needs --replacement (use \"\" to delete the text).")
    dry_run = not args.write
    if not dry_run:
        _require_approval(args, "Replace --write")
    prs = CORE.open_presentation(args.input)
    preview = EDIT.search_text(prs, args.pattern, regex=bool(args.regex), case_sensitive=bool(args.case_sensitive), core=CORE)
    result = EDIT.replace_text(
        prs,
        args.pattern,
        args.replacement,
        regex=bool(args.regex),
        case_sensitive=bool(args.case_sensitive),
        paragraph_fallback=not args.no_paragraph_fallback,
        core=CORE,
    )
    written = None
    if not dry_run:
        destination = CORE.resolve_output(args.out, overwrite=args.overwrite, exts=DECK_EXT, label="output deck")
        if os.path.abspath(destination) == os.path.abspath(CORE.resolve_input(args.input)):
            raise CORE.SafetyError("Replace must write to a new file.")
        CORE.save_presentation(prs, destination)
        written = destination
    lines = [
        "%s %d replacement(s) in %d slide(s)"
        % ("Would apply" if dry_run else "Applied", result["total_replacements"], len(result["slides_touched"])),
        "Run-level: %d · paragraph-level: %d" % (result["run_level_replacements"], result["paragraph_level_replacements"]),
    ]
    if preview["match_count"] and not result["total_replacements"]:
        lines.append("NOTE: %d paragraph(s) mention the pattern but no run could be rewritten." % preview["match_count"])
    if result["formatting_warnings"]:
        lines.append(
            "NOTE: %d paragraph(s) had the phrase split across runs; formatting collapsed into the first run."
            % len(result["formatting_warnings"])
        )
    if dry_run:
        lines.append("Nothing was written. Rerun with --write --approve to publish.")
    else:
        lines.append("Output: %s" % os.path.basename(written or ""))
    return {"summary": "\n".join(lines), "data": dict(result, dry_run=dry_run, output_path=written or "", preview_matches=preview["match_count"])}


# ---------------------------------------------------------------------------
# command: compare
# ---------------------------------------------------------------------------

@command("compare", "Diff two decks: slides, titles, structure, and media.", role="Auditor")
def cmd_compare(args, ctx: Context) -> Dict[str, Any]:
    if not args.a or not args.b:
        raise CORE.UsageError("compare needs --a <baseline.pptx> and --b <comparison.pptx>.")
    result = EDIT.compare_decks(args.a, args.b, core=CORE)
    written = None
    if args.markdown:
        destination = CORE.resolve_output(args.markdown, overwrite=True, exts=(".md",), label="comparison report")
        CORE.write_text(EDIT.compare_markdown(result), destination)
        written = destination
    delta = result["delta"]
    lines = [
        "Comparison: %s vs %s" % (result["baseline"]["file"], result["comparison"]["file"]),
        "Slides: %s -> %s (%+d)" % (result["baseline"]["slides"], result["comparison"]["slides"], delta["slides"]),
        "Words: %s -> %s (%+d)" % (result["baseline"]["words"], result["comparison"]["words"], delta["words"]),
        "Bytes: %s -> %s (%+d)" % (CORE.human_bytes(result["baseline"]["bytes"] or 0), CORE.human_bytes(result["comparison"]["bytes"] or 0), delta["bytes"]),
        "Added titles: %d · removed titles: %d · changed positions: %d"
        % (len(result["added_titles"]), len(result["removed_titles"]), len(result["changed_positions"])),
    ]
    if result["fonts_added"]:
        lines.append("Fonts added: %s" % ", ".join(result["fonts_added"][:10]))
    if written:
        lines.append("Report: %s" % os.path.relpath(written))
    return {"summary": "\n".join(lines), "data": dict(result, report_path=written or "")}


@command("validate", "Validate OOXML package integrity (zip, XML, rels, content types).", role="Auditor")
def cmd_validate(args, ctx: Context) -> Dict[str, Any]:
    result = CORE.validate_package(args.input)
    written = None
    if args.out:
        destination = CORE.resolve_output(args.out, overwrite=True, exts=(".json",), label="validation JSON")
        CORE.dump_json(result, destination)
        written = destination
    lines = [
        "Package validation: %s (%s)" % (result["file"], CORE.human_bytes(result["bytes"])),
        "Checks: %d · XML parts: %d · media parts: %d · slides: %d"
        % (result["checks"], result["xml_parts"], result["media_parts"], result["slide_count"]),
        "Result: %s (%d errors, %d warnings)"
        % ("PASS" if result["passed"] else "FAIL", result["error_count"], result["warning_count"]),
    ]
    for item in result["issues"][:20]:
        lines.append("  [%s] %s: %s" % (item["severity"][:4].upper(), item["kind"], item["message"]))
    if len(result["issues"]) > 20:
        lines.append("  ... %d more issues" % (len(result["issues"]) - 20))
    if written:
        lines.append("JSON report: %s" % os.path.relpath(written))
    return {
        "summary": "\n".join(lines),
        "data": dict(result, report_path=written or ""),
        "exit_code": CORE.EXIT_OK if result["passed"] else CORE.EXIT_VALIDATION,
    }


# ---------------------------------------------------------------------------
# command: preview / export
# ---------------------------------------------------------------------------

@command("preview", "Render approximate PNG previews (and optional PDF) for visual QA.", role="Auditor", writes=True)
def cmd_preview(args, ctx: Context) -> Dict[str, Any]:
    prs = CORE.open_presentation(args.input)
    total = len(prs.slides)
    selected = _parse_slide_range(args.slides or "", total) if args.slides else None
    out_dir = CORE.resolve_output_dir(args.out_dir, overwrite=args.overwrite)
    result = RENDER.render_previews(
        args.input,
        out_dir,
        config=ctx.config,
        slides=selected,
        width_px=args.width,
        log=ctx.log,
        deadline=ctx.deadline,
        core=CORE,
    )
    pdf_path = None
    if args.pdf:
        pdf_path = CORE.resolve_output(args.pdf, overwrite=True, exts=(".pdf",), label="preview PDF")
        RENDER.previews_to_pdf([os.path.join(out_dir, item["file"]) for item in result["rendered"]], pdf_path)
    lines = [
        "Rendered %d slide preview(s) at %dpx wide into %s"
        % (len(result["rendered"]), result["width_px"], os.path.relpath(out_dir)),
        "Backend: %s" % result["backend"],
        result["note"],
    ]
    if pdf_path:
        lines.append("Preview PDF: %s" % os.path.relpath(pdf_path))
    return {"summary": "\n".join(lines), "data": dict(result, out_dir=out_dir, pdf_path=pdf_path or "")}


@command("export", "Export PDF/PNG through LibreOffice, or an approximate preview PDF/PNG.", role="Automator", writes=True)
def cmd_export(args, ctx: Context) -> Dict[str, Any]:
    fmt = (args.format or "pdf").lower()
    if fmt in ("pdf", "preview-pdf"):
        if not args.out:
            raise CORE.UsageError("export --format %s needs --out <file.pdf>." % fmt)
    else:
        if not args.out:
            raise CORE.UsageError("export --format %s needs --out <folder>." % fmt)
    if fmt == "pdf":
        result = RENDER.export_pdf(args.input, args.out, config=ctx.config, log=ctx.log, core=CORE)
        lines = ["PDF exported -> %s (%s, %s)" % (result["output"], CORE.human_bytes(result["bytes"]), result["backend"])]
        return {"summary": "\n".join(lines), "data": result}
    if fmt == "preview-pdf":
        result = RENDER.export_preview_pdf(
            args.input, args.out, config=ctx.config, slides=None, log=ctx.log, deadline=ctx.deadline, core=CORE
        )
        lines = [
            "Approximate preview PDF -> %s (%s, %d slides)" % (result["output"], CORE.human_bytes(result["bytes"]), result["slides"]),
            result["note"],
        ]
        return {"summary": "\n".join(lines), "data": result}
    if fmt == "png":
        out_dir = CORE.resolve_output_dir(args.out, overwrite=args.overwrite)
        result = RENDER.export_pngs(args.input, out_dir, config=ctx.config, dpi=args.dpi, log=ctx.log, core=CORE)
        return {
            "summary": "Exported %d PNG page(s) at %d DPI into %s (backend %s)"
            % (len(result["files"]), result["dpi"], os.path.relpath(out_dir), result["backend"]),
            "data": dict(result, out_dir=out_dir),
        }
    if fmt == "preview-png":
        out_dir = CORE.resolve_output_dir(args.out, overwrite=args.overwrite)
        result = RENDER.render_previews(
            args.input, out_dir, config=ctx.config, width_px=args.width, log=ctx.log, deadline=ctx.deadline, core=CORE
        )
        return {
            "summary": "Rendered %d approximate PNG(s) at %dpx into %s (backend %s)"
            % (len(result["rendered"]), result["width_px"], os.path.relpath(out_dir), result["backend"]),
            "data": dict(result, out_dir=out_dir),
        }
    raise CORE.UsageError("Unknown --format '%s' (pdf, preview-pdf, png, preview-png)." % fmt)


# ---------------------------------------------------------------------------
# command: batch
# ---------------------------------------------------------------------------

def _dict_to_argv(values: Dict[str, Any]) -> List[str]:
    argv: List[str] = []
    for key, value in values.items():
        if value is None or value is False:
            continue
        flag = "--" + str(key).replace("_", "-")
        if value is True:
            argv.append(flag)
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                argv.extend([flag, str(item)])
            continue
        if isinstance(value, dict):
            argv.extend([flag, json.dumps(value, ensure_ascii=False)])
            continue
        argv.extend([flag, str(value)])
    return argv


def batch_dispatch(command: str, args: Dict[str, Any], deadline=None) -> Dict[str, Any]:
    """Execute one command from a batch manifest through the real parser."""

    if command not in COMMANDS:
        raise CORE.UsageError("Unknown batch command '%s'." % command)
    parser = PARSERS.get(command)
    if parser is None:
        raise CORE.DependencyError("Command parser for '%s' is not registered." % command)
    argv = _dict_to_argv(dict(args or {}))
    try:
        namespace = parser.parse_args(argv)
    except SystemExit:
        raise CORE.UsageError("Batch job arguments for '%s' could not be parsed: %s" % (command, " ".join(argv)))
    namespace.command = command
    context = Context(
        config=batch_dispatch.config,
        log=batch_dispatch.log,
        deadline=deadline,
        quiet=True,
    )
    result = COMMANDS[command].handler(namespace, context)
    payload = result.get("data", {k: v for k, v in result.items() if k not in ("summary", "exit_code")})
    exit_code = result.get("exit_code", 0)
    if exit_code not in (0, None) and exit_code != CORE.EXIT_GATE_FAILED:
        raise CORE.AgentError(
            "Batch job '%s' finished with exit code %s." % (command, exit_code),
            details={"result": payload},
        )
    return {"exit_code": exit_code, "data": payload}


def _batch_output_note(summary: Dict[str, Any]) -> str:
    """Describe where a batch's outputs ended up, without implying staging ran."""

    if summary.get("dry_run"):
        return "dry run, nothing written"
    if summary.get("stage_outputs"):
        return "staged outputs, published %d file target(s)" % len(summary.get("published") or [])
    root = summary.get("output_root") or ""
    if root:
        return "outputs written directly under %s" % root
    return "outputs written directly beside the manifest"


@command("batch", "Run a validated job manifest with staging, retries, and rollback.", role="Automator", writes=True)
def cmd_batch(args, ctx: Context) -> Dict[str, Any]:
    manifest_path = CORE.resolve_input(args.manifest, exts=(".json",), label="batch manifest")
    manifest = CORE.load_json(manifest_path)
    batch_dispatch.config = ctx.config
    batch_dispatch.log = ctx.log
    summary = BATCH.run_batch(
        manifest,
        manifest_path=manifest_path,
        dispatch=batch_dispatch,
        allowed_commands=sorted(COMMANDS),
        config=ctx.config,
        log=ctx.log,
        approve=bool(args.approve),
        dry_run=bool(args.dry_run),
        out_dir=args.out_dir,
        core=CORE,
    )
    written: Dict[str, str] = {}
    base = args.out_dir or os.path.dirname(manifest_path)
    json_path = os.path.join(base, "batch-summary.json")
    markdown_path = os.path.join(base, "batch-summary.md")
    CORE.dump_json(summary, json_path)
    CORE.write_text(BATCH.batch_report_markdown(summary), markdown_path)
    written["json"] = json_path
    written["markdown"] = markdown_path
    lines = [
        "Batch %s: %d job(s), %d succeeded, %d failed"
        % ("plan" if summary["dry_run"] else "run", summary["job_count"], summary["succeeded"], summary["failed"]),
        "Duration: %.2fs · %s"
        % (summary["duration_s"], _batch_output_note(summary)),
    ]
    for job in summary["jobs"]:
        lines.append("  %-22s %-10s %s" % (job["job_id"], job["status"], job.get("error", "") if job["status"] != "succeeded" else ""))
    lines.append("Summary: %s" % os.path.relpath(markdown_path))
    exit_code = CORE.EXIT_OK if summary["failed"] == 0 else CORE.EXIT_RUNTIME
    return {"summary": "\n".join(lines), "data": dict(summary, reports=written), "exit_code": exit_code}


# ---------------------------------------------------------------------------
# command: deliver
# ---------------------------------------------------------------------------

@command("deliver", "Run the end-to-end pipeline: build/format, audit, repair, optimize, preview, export.", role="Orchestrator", writes=True)
def cmd_deliver(args, ctx: Context) -> Dict[str, Any]:
    if not args.spec and not args.input:
        raise CORE.UsageError("deliver needs --spec <spec.json> (build) or --input <deck.pptx> (existing deck).")
    if args.spec and args.input:
        raise CORE.UsageError("Use either --spec or --input, not both.")
    out_dir = CORE.resolve_output_dir(args.out_dir, overwrite=args.overwrite)
    stages: List[Dict[str, Any]] = []
    warnings: List[str] = []
    current_deck = ""

    def stage(name: str, status: str, detail: Any = None) -> None:
        stages.append({"stage": name, "status": status, "detail": detail})

    # 1. source deck
    if args.spec:
        spec_path = CORE.resolve_input(args.spec, exts=(".json",), label="deck spec")
        spec = CORE.load_json(spec_path)
        if args.theme:
            spec["theme"] = {"preset": args.theme, "overrides": (spec.get("theme") or {}).get("overrides", {})}
        normalized, spec_warnings = CORE.validate_spec(spec, config=ctx.config, spec_path=spec_path)
        warnings.extend(spec_warnings)
        prs, build_warnings, meta = CORE.build_deck(normalized, config=ctx.config, log=ctx.log, deadline=ctx.deadline)
        warnings.extend(item["message"] for item in build_warnings)
        current_deck = os.path.join(out_dir, "deck.pptx")
        CORE.save_presentation(prs, current_deck)
        stage("build", "ok", {"slides": meta["slide_count"], "theme": meta["theme"]})
    else:
        source = CORE.resolve_input(args.input)
        current_deck = os.path.join(out_dir, "deck.pptx")
        CORE.atomic_copy(source, current_deck)
        if args.theme:
            formatted = os.path.join(out_dir, "deck.formatted.pptx")
            result = EDIT.format_deck(
                current_deck, formatted, config=ctx.config, theme_name=args.theme, overwrite=True,
                log=ctx.log, deadline=ctx.deadline,
            )
            current_deck = formatted
            stage("format", "ok", result["stats"])
        else:
            stage("intake", "ok", {"source": os.path.basename(source)})

    # 2. audit
    pack = AUDIT.load_rule_pack(core=CORE)
    report = AUDIT.audit_deck(current_deck, config=ctx.config, pack=pack, log=ctx.log, deadline=ctx.deadline, core=CORE)
    audit_json = os.path.join(out_dir, "audit.json")
    audit_md = os.path.join(out_dir, "audit.md")
    CORE.dump_json(report, audit_json)
    CORE.write_text(AUDIT.report_to_markdown(report), audit_md)
    stage("audit", "ok" if report["gate"]["passed"] else "gate-failed", {"score": report["score"], "counts": report["counts"]})
    if not report["gate"]["passed"]:
        warnings.append("Audit gate failed: %s" % AUDIT.summarize_counts(report))

    # 3. optional repair
    if args.repair:
        if not args.approve:
            stage("repair", "skipped", "needs --approve")
            warnings.append("Repair was requested but skipped because --approve was not supplied.")
        else:
            repaired_path = os.path.join(out_dir, "deck.repaired.pptx")
            repair_result = REPAIR.repair_deck(
                current_deck, repaired_path, config=ctx.config, pack=pack, approve=True, dry_run=False,
                log=ctx.log, deadline=ctx.deadline, overwrite=True, core=CORE, audit=AUDIT,
            )
            CORE.write_text(REPAIR.repair_report_markdown(repair_result), os.path.join(out_dir, "repair.md"))
            if repair_result["change_count"]:
                current_deck = repaired_path
            stage("repair", "ok", {"changes": repair_result["change_count"]})
            report = AUDIT.audit_deck(current_deck, config=ctx.config, pack=pack, log=ctx.log, deadline=ctx.deadline, core=CORE)
            CORE.dump_json(report, audit_json)
            CORE.write_text(AUDIT.report_to_markdown(report), audit_md)

    # 4. optional optimize
    if args.optimize:
        if not args.approve:
            stage("optimize", "skipped", "needs --approve")
            warnings.append("Optimize was requested but skipped because --approve was not supplied.")
        else:
            optimized_path = os.path.join(out_dir, "deck.optimized.pptx")
            optimize_result = MEDIA.optimize_deck(
                current_deck, optimized_path, config=ctx.config, approve=True, dry_run=False,
                log=ctx.log, deadline=ctx.deadline, overwrite=True, core=CORE,
            )
            CORE.write_text(MEDIA.optimize_report_markdown(optimize_result), os.path.join(out_dir, "optimize.md"))
            if optimize_result.get("output"):
                current_deck = optimized_path
            stage("optimize", "ok", {"saved_percent": optimize_result.get("saved_percent", 0.0)})

    # 5. previews
    preview_dir = os.path.join(out_dir, "previews")
    preview_result = RENDER.render_previews(
        current_deck, preview_dir, config=ctx.config, width_px=args.width, log=ctx.log, deadline=ctx.deadline, core=CORE
    )
    stage("preview", "ok", {"rendered": len(preview_result["rendered"])})

    # 6. export
    export_result = None
    if args.format and args.format != "none":
        if args.format == "pdf":
            export_result = RENDER.export_pdf(current_deck, os.path.join(out_dir, "deck.pdf"), config=ctx.config, log=ctx.log, core=CORE)
        elif args.format == "preview-pdf":
            export_result = RENDER.export_preview_pdf(
                current_deck, os.path.join(out_dir, "deck.preview.pdf"), config=ctx.config, log=ctx.log,
                deadline=ctx.deadline, core=CORE,
            )
        else:
            raise CORE.UsageError("--format must be none, pdf, or preview-pdf.")
        stage("export", "ok", {"output": export_result["output"], "bytes": export_result["bytes"]})
    else:
        stage("export", "skipped", "no export format requested")

    # 7. delivery report
    report_lines = [
        "# Delivery report",
        "",
        "- Generated: %s" % _iso(),
        "- Tool: powerpoint-agent %s" % TOOL_VERSION,
        "- Deck: `%s`" % os.path.basename(current_deck),
        "- Output folder: `%s`" % os.path.basename(out_dir) or out_dir,
        "",
        "## Stages",
        "",
        "| Stage | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for item in stages:
        detail = item.get("detail")
        detail_text = json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else str(detail or "")
        report_lines.append("| %s | %s | %s |" % (item["stage"], item["status"], detail_text.replace("|", "/")[:160]))
    report_lines.append("")
    report_lines.append("## Final audit")
    report_lines.append("")
    report_lines.append("| Metric | Value |")
    report_lines.append("| --- | --- |")
    report_lines.append("| Score | %s / 100 |" % report["score"])
    report_lines.append("| Errors | %s |" % report["counts"].get("error", 0))
    report_lines.append("| Warnings | %s |" % report["counts"].get("warning", 0))
    report_lines.append("| Gate | %s |" % ("PASS" if report["gate"]["passed"] else "FAIL"))
    report_lines.append("")
    report_lines.append("## Files")
    report_lines.append("")
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name)
        if os.path.isdir(path):
            report_lines.append("- `%s/` (%d files)" % (name, len(os.listdir(path))))
        else:
            report_lines.append("- `%s` (%s)" % (name, CORE.human_bytes(os.path.getsize(path))))
    if warnings:
        report_lines.append("")
        report_lines.append("## Warnings")
        report_lines.append("")
        for warning in warnings:
            report_lines.append("- %s" % warning)
    delivery_md = os.path.join(out_dir, "delivery-report.md")
    CORE.write_text("\n".join(report_lines).rstrip() + "\n", delivery_md)

    data = {
        "out_dir": out_dir,
        "deck": current_deck,
        "stages": stages,
        "audit": {"score": report["score"], "counts": report["counts"], "gate": report["gate"]},
        "preview": preview_result,
        "export": export_result,
        "report": delivery_md,
        "warnings": warnings,
    }
    lines = [
        "Delivered to %s" % os.path.relpath(out_dir),
        "Deck: %s (%s)" % (os.path.basename(current_deck), CORE.human_bytes(os.path.getsize(current_deck))),
        "Stages: " + ", ".join("%s=%s" % (item["stage"], item["status"]) for item in stages),
        "Final audit: score %s, %s errors, gate %s" % (report["score"], report["counts"].get("error", 0), "PASS" if report["gate"]["passed"] else "FAIL"),
        "Delivery report: %s" % os.path.relpath(delivery_md),
    ]
    for warning in warnings[:10]:
        lines.append("WARN: %s" % warning)
    exit_code = CORE.EXIT_OK if report["gate"]["passed"] else CORE.EXIT_GATE_FAILED
    return {"summary": "\n".join(lines), "data": data, "warnings": warnings, "exit_code": exit_code}


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------

MAIN_EPILOG = """\
commands
  plan         design an outline, routing, and acceptance checks from a brief
  build        build an editable deck from a deck spec
  format       normalise typography, geometry, footer, and slide numbers
  audit        quality, accessibility, and compatibility audit with gate
  repair       safe fixes with an undo record (dry run by default)
  optimize     downscale/recompress media and drop unused parts (dry run by default)
  inspect      structure, fonts, colours, media, per-slide statistics
  outline      deck outline as Markdown, JSON, or text
  notes        note coverage, structured note scaffolding, Markdown export
  sections     list or rewrite PowerPoint sections
  search       find text across slides
  replace      replace text (write requires --approve)
  compare      diff two decks
  preview      approximate PNG previews for visual QA
  export       PDF/PNG via LibreOffice, or approximate preview PDF/PNG
  batch        run a job manifest with staging, retries, rollback
  deliver      end-to-end pipeline into one delivery folder
  catalog      search the 50-category / 350-skill catalog
  themes       list or validate theme presets
  doctor       runtime, dependency, asset, and backend checks
  capabilities machine-readable capability map and exit codes

exit codes
  0 success · 2 usage · 3 dependency · 4 validation · 5 gate failed
  6 safety refusal · 7 runtime · 130 interrupted

examples
  python tools-pptx-agent.py capabilities
  python tools-pptx-agent.py plan --brief asset-brief-example.md --out plan.json --narrative exec-brief
  python tools-pptx-agent.py build --spec asset-deck-spec-example.json --out deck.pptx --audit
  python tools-pptx-agent.py audit --in deck.pptx --out audit.json --markdown audit.md --fail-on error
  python tools-pptx-agent.py repair --in deck.pptx --out deck.fixed.pptx --dry-run
  python tools-pptx-agent.py repair --in deck.pptx --out deck.fixed.pptx --write --approve
  python tools-pptx-agent.py preview --in deck.pptx --out-dir previews --pdf previews/deck.pdf
  python tools-pptx-agent.py export --in deck.pptx --out deck.pdf --format preview-pdf
  python tools-pptx-agent.py deliver --spec asset-deck-spec-example.json --out-dir delivery --format preview-pdf
"""


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit one JSON envelope on stdout")
    parser.add_argument("--config", help="path to a JSON config that overrides asset-default-config.json")
    parser.add_argument("--log-file", help="append JSONL run events to this file")
    parser.add_argument("--timeout", type=float, help="cooperative timeout in seconds for the whole command")
    parser.add_argument("--overwrite", action="store_true", help="allow overwriting existing outputs")
    parser.add_argument("--quiet", action="store_true", help="suppress human-readable output (JSON still prints)")


def build_parser() -> argparse.ArgumentParser:
    global_parser = argparse.ArgumentParser(add_help=False)
    _add_global_flags(global_parser)

    parser = argparse.ArgumentParser(
        prog="tools-pptx-agent.py",
        description="powerpoint-agent %s — plan, build, audit, repair, optimize, preview, export, and deliver PowerPoint work." % TOOL_VERSION,
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="powerpoint-agent %s" % TOOL_VERSION)
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, help_text: str, description: str, epilog: str = "") -> argparse.ArgumentParser:
        sp = sub.add_parser(
            name,
            parents=[global_parser],
            help=help_text,
            description=description,
            epilog=epilog or None,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        PARSERS[name] = sp
        return sp

    def deck_input(sp: argparse.ArgumentParser, help_text: str = "input presentation (.pptx/.pptm)") -> None:
        sp.add_argument("--in", "--input", dest="input", metavar="DECK", help=help_text)

    # -- meta ------------------------------------------------------------
    sp = add("capabilities", "list commands, roles, safety rules, and exit codes",
             "Print the full capability map: commands, roles, write/approval rules, supported inputs, "
             "layouts, themes, dependencies, and exit codes. Use --json for machine consumption.")

    sp = add("doctor", "check runtime, dependencies, assets, and backends",
             "Run environment checks: Python version, python-pptx, Pillow, font metrics, LibreOffice, "
             "PDF rasterizer, bundled assets, catalog integrity, rule pack, theme validation, example spec, "
             "and whether the skill folder is writable.")

    sp = add("themes", "list or validate theme presets",
             "List the bundled brand presets or validate a preset JSON before using it.",
             "python tools-pptx-agent.py themes --action list\n"
             "python tools-pptx-agent.py themes --action validate --theme corporate-blue")
    sp.add_argument("--action", choices=("list", "validate"), default="list")
    sp.add_argument("--theme", help="preset name or path to a theme JSON")

    sp = add("catalog", "search the 50-category / 350-skill catalog",
             "Query the capability catalog that maps every catalog category and skill to one runnable operation.",
             "python tools-pptx-agent.py catalog --action categories\n"
             "python tools-pptx-agent.py catalog --action list --category 32 --role auditor\n"
             "python tools-pptx-agent.py catalog --action show --skill S221")
    sp.add_argument("--action", choices=("list", "show", "categories"), default="list")
    sp.add_argument("--category", help="category number, slug, or exact name")
    sp.add_argument("--role", help="Architect, Builder, Formatter, Auditor, Repairer, Automator, Optimizer")
    sp.add_argument("--search", help="substring search across names, descriptions, categories")
    sp.add_argument("--skill", help="skill id (S001..S350) or exact skill name")
    sp.add_argument("--limit", type=int, default=200, help="maximum rows to print (default 200)")

    # -- plan ------------------------------------------------------------
    sp = add("plan", "design a plan, outline, routing, and acceptance checks",
             "Turn a plain-text or Markdown brief into a structured plan: parsed goal/audience/constraints, "
             "a narrative arc, a slide outline with layout and catalog category per slot, acceptance checks "
             "derived from the live rule pack, risks, and the exact command sequence to produce the deck.",
             "python tools-pptx-agent.py plan --brief brief.md --out plan.json --narrative exec-brief --slides 12")
    sp.add_argument("--brief", help="path to a .md/.txt brief")
    sp.add_argument("--brief-text", help="brief text passed inline")
    sp.add_argument("--narrative", choices=tuple(sorted(NARRATIVES)), default="general")
    sp.add_argument("--slides", type=int, help="target slide budget; reported if it conflicts with the template")
    sp.add_argument("--out", help="write the plan JSON here")

    # -- build -----------------------------------------------------------
    sp = add("build", "build an editable deck from a JSON deck spec",
             "Validate a deck spec, build every slide through the layout engine, write the .pptx, reopen it, "
             "and optionally run the full audit.",
             "python tools-pptx-agent.py build --spec asset-deck-spec-example.json --out deck.pptx --audit")
    sp.add_argument("--spec", required=False, help="deck spec JSON")
    sp.add_argument("--out", help="output .pptx path")
    sp.add_argument("--into", help="append the spec's slides to an existing presentation")
    sp.add_argument("--theme", help="theme preset name or path, overriding the spec")
    sp.add_argument("--audit", action="store_true", help="run the audit after building")
    sp.add_argument("--audit-out", help="write the post-build audit JSON here")

    # -- format ----------------------------------------------------------
    sp = add("format", "normalise typography, canvas, footer, slide numbers",
             "Apply theme-aware, deterministic formatting: font families, minimum sizes, body margins, "
             "theme canvas repaint with re-measured ink, broken-title repair, empty-placeholder cleanup, "
             "footer text, and slide numbers. Valid designed geometry is left untouched.")
    deck_input(sp)
    sp.add_argument("--out", help="output .pptx path")
    sp.add_argument("--theme", help="theme preset name or path")
    sp.add_argument("--force-fonts", action="store_true", help="override every run font with the theme font")
    sp.add_argument("--keep-fonts", action="store_true", help="leave explicit fonts untouched")
    sp.add_argument("--min-font-size", type=float, help="raise explicit sizes below this value")
    sp.add_argument("--footer-text", help="footer text to add where missing")
    sp.add_argument("--no-footer", action="store_true", help="do not add footer text")
    sp.add_argument("--no-slide-numbers", action="store_true", help="do not add slide numbers")
    sp.add_argument("--keep-empty-placeholders", action="store_true")
    background_group = sp.add_mutually_exclusive_group()
    background_group.add_argument("--repaint-backgrounds", action="store_true",
                                  help="force the theme canvas onto every slide")
    background_group.add_argument("--keep-backgrounds", action="store_true",
                                  help="never change existing slide backgrounds")

    # -- audit -----------------------------------------------------------
    sp = add("audit", "run the audit rule pack with scoring and a gate",
             "Run every enabled rule in the rule pack: geometry, typography, colour/contrast, accessibility, "
             "structure, content, links, media, compatibility, performance, and security. Findings carry "
             "severity, slide, location, fix hint, and evidence. The gate controls the exit code.",
             "python tools-pptx-agent.py audit --in deck.pptx --out audit.json --markdown audit.md --csv audit.csv --fail-on error")
    deck_input(sp)
    sp.add_argument("--out", help="audit JSON output")
    sp.add_argument("--markdown", help="Markdown report output")
    sp.add_argument("--csv", help="CSV findings output")
    sp.add_argument("--rule-pack", help="alternative rule pack JSON")
    sp.add_argument("--only", help="comma-separated rule ids to run exclusively")
    sp.add_argument("--fail-on", choices=("error", "warning", "info", "none"), help="gate threshold (default from config: error)")

    # -- repair ----------------------------------------------------------
    sp = add("repair", "apply safe, recorded fixes (dry run by default)",
             "Audit, apply only the fixers allowed by asset-repair-policy.json, verify the result on a temp copy, "
             "and publish it to --out only with --write --approve. Every change is recorded before/after.")
    deck_input(sp)
    sp.add_argument("--out", help="output .pptx path (never the input)")
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="report planned changes only (default)")
    group.add_argument("--write", action="store_true", help="publish the repaired deck (requires --approve)")
    sp.add_argument("--approve", action="store_true", help="confirm the write")
    sp.add_argument("--rules", help="comma-separated rule ids to repair")
    sp.add_argument("--policy", help="alternative repair policy JSON")
    sp.add_argument("--rule-pack", help="alternative rule pack JSON")
    sp.add_argument("--theme", help="theme used for colour substitutions")
    sp.add_argument("--report", help="write a Markdown repair report")
    sp.add_argument("--json-report", help="write the repair result JSON")

    # -- optimize --------------------------------------------------------
    sp = add("optimize", "recompress media and cut file size (dry run by default)",
             "Downscale and recompress embedded raster images in place, optionally drop unreferenced media "
             "parts, verify the rebuilt package, and report before/after sizes.")
    deck_input(sp)
    sp.add_argument("--out", help="output .pptx path (never the input)")
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="report projections only (default)")
    group.add_argument("--write", action="store_true", help="publish the optimized deck (requires --approve)")
    sp.add_argument("--approve", action="store_true", help="confirm the write")
    sp.add_argument("--max-dimension", type=int, help="maximum image edge in pixels")
    sp.add_argument("--quality", type=int, help="JPEG/WebP quality 30-95")
    sp.add_argument("--drop-unused-media", action="store_true", help="remove media parts no relationship references")
    sp.add_argument("--report", help="write a Markdown optimization report")

    # -- inspect / outline -----------------------------------------------
    sp = add("inspect", "structure, fonts, colours, media, per-slide stats",
             "Report slide count, canvas size, words, pictures, charts, tables, note coverage, font and colour "
             "usage, media inventory, sections, and per-slide shape/word counts.")
    deck_input(sp)
    sp.add_argument("--out", help="write the statistics JSON here")

    sp = add("outline", "export the deck outline",
             "Extract sections, titles, bullets, and note presence as Markdown, JSON, or plain text.")
    deck_input(sp)
    sp.add_argument("--format", default="md", help="md (default), json, or txt")
    sp.add_argument("--out", help="write the outline here")
    sp.add_argument("--title", help="heading for Markdown output")

    # -- notes / sections ------------------------------------------------
    sp = add("notes", "note coverage, scaffolding, and Markdown export",
             "check reports coverage and speaking time; scaffold writes structured TITLE/TALKING POINTS/"
             "TRANSITION/TIMING scaffolds to slides without notes; export writes notes.md.")
    deck_input(sp)
    sp.add_argument("--action", choices=("check", "scaffold", "export"), default="check")
    sp.add_argument("--out", help="output .pptx (scaffold) or .md (export)")
    sp.add_argument("--title", help="title for the Markdown export")

    sp = add("sections", "list or rewrite PowerPoint sections",
             "Read the p14 section list, or write a new one from --map \"Name:1,2;Name2:3-5\" or a JSON mapping.")
    deck_input(sp)
    sp.add_argument("--action", choices=("list", "set"), default="list")
    sp.add_argument("--out", help="output .pptx for --action set")
    sp.add_argument("--map", help="sections as \"Name:1,2;Name2:3-5\"")
    sp.add_argument("--sections-json", help="JSON list of {name, slide_indexes}")

    # -- search / replace / compare --------------------------------------
    sp = add("search", "find text across slides and shapes",
             "Search paragraph text with an optional regular expression and case-sensitive matching.")
    deck_input(sp)
    sp.add_argument("--pattern", help="text or regular expression")
    sp.add_argument("--regex", action="store_true")
    sp.add_argument("--case-sensitive", action="store_true")

    sp = add("replace", "replace text (write requires approval)",
             "Replace text run by run, with an optional paragraph-level fallback for phrases split across runs. "
             "Dry run by default; publishing requires --write --approve.")
    deck_input(sp)
    sp.add_argument("--out", help="output .pptx path")
    sp.add_argument("--pattern", help="text or regular expression")
    sp.add_argument("--replacement", help="replacement text (empty string deletes)")
    sp.add_argument("--regex", action="store_true")
    sp.add_argument("--case-sensitive", action="store_true")
    sp.add_argument("--no-paragraph-fallback", action="store_true")
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--write", action="store_true")
    sp.add_argument("--approve", action="store_true")

    sp = add("compare", "diff two decks",
             "Compare slide counts, titles, media, and size between a baseline and a comparison deck.")
    sp.add_argument("--a", help="baseline presentation")
    sp.add_argument("--b", help="comparison presentation")
    sp.add_argument("--markdown", help="write a Markdown comparison report")

    # -- preview / export ------------------------------------------------
    sp = add("validate", "validate OOXML package integrity",
             "Check the zip container, XML well-formedness of every part, relationship targets, declared "
             "content types, orphan parts, and whether python-pptx can reopen the deck. This catches the "
             "defects that make PowerPoint demand a repair.",
             "python tools-pptx-agent.py validate --in deck.pptx --out validation.json")
    deck_input(sp)
    sp.add_argument("--out", help="write the validation JSON here")

    sp = add("preview", "render approximate PNG previews",
             "Render each slide to PNG with the internal Pillow renderer for geometry, wrapping, and hierarchy "
             "QA, and optionally assemble a preview PDF. This is an approximation, not a faithful render.")
    deck_input(sp)
    sp.add_argument("--out-dir", help="folder for slide-NNN.png files")
    sp.add_argument("--slides", help="slide selection such as 1-4,7 (default: all, capped by config)")
    sp.add_argument("--width", type=int, help="canvas width in pixels (default 1280)")
    sp.add_argument("--pdf", help="also write a preview PDF here")

    sp = add("export", "PDF/PNG export, faithful or approximate",
             "pdf and png use a local LibreOffice install for faithful output; preview-pdf and preview-png use "
             "the internal renderer and always work offline.")
    deck_input(sp)
    sp.add_argument("--out", help="output file (pdf/preview-pdf) or folder (png/preview-png)")
    sp.add_argument("--format", choices=("pdf", "preview-pdf", "png", "preview-png"), default="pdf")
    sp.add_argument("--dpi", type=int, default=150, help="PNG DPI for --format png")
    sp.add_argument("--width", type=int, help="canvas width in pixels for preview-png")

    # -- batch / deliver -------------------------------------------------
    sp = add("batch", "run a job manifest with staging and rollback",
             "Execute a validated manifest of commands through the same dispatcher the CLI uses. Outputs are "
             "staged and published only when the whole batch succeeds. Write commands require --approve.",
             "python tools-pptx-agent.py batch --manifest asset-batch-example.json --out-dir batch-out")
    sp.add_argument("--manifest", help="batch manifest JSON")
    sp.add_argument("--out-dir", help="folder for logs and summaries")
    sp.add_argument("--dry-run", action="store_true", help="validate and print the plan without running jobs")
    sp.add_argument("--approve", action="store_true", help="allow write commands in the batch")

    sp = add("deliver", "end-to-end pipeline into one folder",
             "Build or intake a deck, audit it, optionally repair and optimize (with --approve), render previews, "
             "optionally export, and write a delivery report with every artifact.",
             "python tools-pptx-agent.py deliver --spec asset-deck-spec-example.json --out-dir delivery --format preview-pdf")
    sp.add_argument("--spec", help="deck spec to build")
    sp.add_argument("--input", dest="input", help="existing deck to deliver")
    sp.add_argument("--out-dir", help="delivery folder")
    sp.add_argument("--theme", help="theme preset name or path")
    sp.add_argument("--format", choices=("none", "pdf", "preview-pdf"), default="none")
    sp.add_argument("--repair", action="store_true", help="repair before final audit (needs --approve)")
    sp.add_argument("--optimize", action="store_true", help="optimize media (needs --approve)")
    sp.add_argument("--approve", action="store_true", help="approve repair/optimize stages")
    sp.add_argument("--width", type=int, help="preview canvas width")

    # help command
    sp = add("help", "show this help text", "Show the main help text.")
    _ = sp
    return parser


# ---------------------------------------------------------------------------
# output and error handling
# ---------------------------------------------------------------------------

def emit_result(command: str, result: Dict[str, Any], args) -> None:
    exit_code = int(result.get("exit_code", 0) or 0)
    if getattr(args, "json", False):
        envelope = {
            "ok": True,
            "tool": "powerpoint-agent",
            "version": TOOL_VERSION,
            "command": command,
            "data": result.get("data", {}),
            "warnings": result.get("warnings", []),
            "exit_code": exit_code,
        }
        print(json.dumps(envelope, ensure_ascii=False, indent=2, default=str))
        return
    if not getattr(args, "quiet", False):
        summary = result.get("summary") or ""
        if summary:
            print(summary)


def emit_error(command: str, exc: "CORE.AgentError", args) -> None:
    if getattr(args, "json", False):
        payload = {
            "ok": False,
            "tool": "powerpoint-agent",
            "version": TOOL_VERSION,
            "command": command,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "hint": exc.hint,
                "details": exc.details,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    sys.stderr.write("ERROR [%s] %s\n" % (exc.code, exc.message))
    if exc.hint:
        sys.stderr.write("HINT: %s\n" % exc.hint)
    details = exc.details
    if isinstance(details, dict):
        problems = details.get("errors") or details.get("problems") or details.get("issues")
        if isinstance(problems, list):
            for problem in problems[:40]:
                sys.stderr.write("  - %s\n" % problem)
        for key in ("path", "expected_path", "import_error"):
            if details.get(key):
                sys.stderr.write("  %s: %s\n" % (key, details[key]))


def main(argv: Optional[Sequence[str]] = None) -> int:
    CORE.install_sigint_guard()
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    command = getattr(args, "command", None)
    if not command:
        parser.print_help()
        return CORE.EXIT_OK
    if command == "help":
        parser.print_help()
        return CORE.EXIT_OK
    spec = COMMANDS.get(command)
    if spec is None:
        sys.stderr.write("ERROR [E_USAGE] Unknown command '%s'.\n" % command)
        return CORE.EXIT_USAGE
    started = time.monotonic()
    try:
        config = CORE.load_config(getattr(args, "config", None))
        log = CORE.RunLog(getattr(args, "log_file", None))
        timeout = getattr(args, "timeout", None)
        if timeout is None:
            timeout = CORE.cfg(config, "timeouts.default_seconds", 300)
        context = Context(
            config=config,
            log=log,
            deadline=CORE.Deadline(timeout) if timeout else CORE.Deadline(None),
            quiet=bool(getattr(args, "quiet", False)),
        )
        log.info("command started", command=command, argv=sys.argv[1:])
        result = spec.handler(args, context)
        result.setdefault("data", {})
        if isinstance(result.get("data"), dict):
            result["data"].setdefault("duration_seconds", round(time.monotonic() - started, 3))
            if getattr(args, "log_file", None):
                result["data"].setdefault("log_file", getattr(args, "log_file", None))
        emit_result(command, result, args)
        exit_code = int(result.get("exit_code", 0) or 0)
        log.info("command finished", command=command, exit_code=exit_code)
        return exit_code
    except CORE.AgentError as exc:
        emit_error(command, exc, args)
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("ERROR [E_INTERRUPTED] Interrupted before completion; temporary files were cleaned up.\n")
        return CORE.EXIT_INTERRUPTED
    except Exception as exc:  # pragma: no cover - unexpected
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "tool": "powerpoint-agent",
                        "command": command,
                        "error": {
                            "code": "E_UNEXPECTED",
                            "message": "%s: %s" % (type(exc).__name__, exc),
                            "hint": "Re-run with --log-file to capture the failure, then report it with the command line.",
                            "details": {"traceback": traceback.format_exc().splitlines()[-6:]},
                        },
                    },
                    indent=2,
                )
            )
        else:
            sys.stderr.write("ERROR [E_UNEXPECTED] %s: %s\n" % (type(exc).__name__, exc))
            sys.stderr.write("Re-run with --log-file to capture the failure.\n")
            if os.environ.get("PPTX_AGENT_DEBUG"):
                traceback.print_exc()
        return CORE.EXIT_RUNTIME


if __name__ == "__main__":
    sys.exit(main())
