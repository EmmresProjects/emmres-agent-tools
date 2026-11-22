#!/usr/bin/env python3
"""tools-presentation-makers.py - primary entry point of the presentation-makers Skill Tool.

One unified Skill Tool covering all 32 categories / 320 catalog capabilities:
requirements and platform selection, narrative planning, drafting, rendering to
PPTX/HTML/PDF/Markdown/captions, accessibility and quality-gate auditing,
regression diffing, benchmarking, approved external conversion, and delivery
packaging with clean-state verification.

Run `python3 tools-presentation-makers.py --help` for the command list and
`python3 tools-presentation-makers.py capabilities` for the capability index.

Python 3.9+, standard library only, no network access.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("pm_core", "tools-pm-core.py")
intake = _load("pm_intake", "tools-pm-intake.py")
plan = _load("pm_plan", "tools-pm-plan.py")
render = _load("pm_render", "tools-pm-render.py")
audit_mod = _load("pm_audit", "tools-pm-audit.py")
package_mod = _load("pm_package", "tools-pm-package.py")
pm_core = core  # convenience alias used by the output and error helpers

PROG = "tools-presentation-makers.py"
DEFAULT_FORMATS = "pptx,html,pdf,md,notes,outline"
ALL_FORMATS = ["pptx", "html", "pdf", "md", "notes", "outline", "srt"]


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------
def emit(result, args, human=None):
    """Print a JSON envelope or a human summary, and return the exit code."""
    state = result.get("status", "ok")
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif human:
        print(human)
        for record in result.get("diagnostics", []):
            marker = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}.get(record["level"], "INFO ")
            where = " (%s)" % record["where"] if record.get("where") else ""
            print("  %s %-32s %s%s" % (marker, record["code"], record["message"], where))
            if record.get("hint"):
                print("       fix: %s" % record["hint"])
    if state == "error":
        return pm_core.EXIT_GATE if result.get("counts", {}).get("error") else pm_core.EXIT_USAGE
    return pm_core.EXIT_OK


def fail(exc, args):
    payload = exc.as_dict() if isinstance(exc, pm_core.PMError) else {
        "status": "error", "code": "internal", "message": str(exc),
        "exit_code": pm_core.EXIT_INTERNAL,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print("ERROR [%s] %s" % (payload.get("code"), payload.get("message")), file=sys.stderr)
        if payload.get("hint"):
            print("       fix: %s" % payload["hint"], file=sys.stderr)
    return payload.get("exit_code", pm_core.EXIT_INTERNAL)


def outdir(args):
    config = core.load_config(getattr(args, "config", None))
    base = getattr(args, "outdir", None) or config["paths"]["output_dir"]
    return core.resolve_path(base)


def load_spec(path, config):
    spec = core.read_json(path)
    errors = core.validate_against_asset(spec, config, "deck_spec_schema")
    hard = [e for e in errors if e["level"] == "error"]
    if hard:
        detail = "; ".join("%s %s" % (e.get("where", "$"), e["message"]) for e in hard[:6])
        raise core.PMError("Deck spec does not match asset-deck-spec-schema.json: %s" % detail,
                           code="spec-schema",
                           hint="Run 'validate --kind deck-spec --input <file>' for the full list.")
    return spec, errors


def guard_overwrite(paths, args, config, what="output"):
    """Honour policy.require_overwrite_approval before replacing existing files."""
    if not config.get("policy", {}).get("require_overwrite_approval", True):
        return
    if getattr(args, "force", False):
        return
    existing = [p for p in paths if os.path.exists(p)]
    if existing:
        raise core.PMError(
            "Refusing to overwrite existing %s: %s" % (what, ", ".join(os.path.basename(p) for p in existing[:4])),
            code="output-exists",
            exit_code=core.EXIT_APPROVAL,
            hint="Re-run with --force to replace existing outputs, or point --outdir somewhere else.",
        )


def require(path, label):
    if not path:
        raise core.PMError("Missing required argument: %s" % label, code="argument-missing",
                           hint="See --help for the required arguments of this command.")
    return path


def _load_catalog(config):
    name = os.path.basename(config["paths"].get("catalog", "asset-skill-catalog.json"))
    catalog = core.load_asset_json(name)
    if not isinstance(catalog, dict) or "categories" not in catalog:
        raise core.PMError("Catalog asset is not in the expected shape", code="catalog-invalid")
    return catalog


# --------------------------------------------------------------------------
# capabilities
# --------------------------------------------------------------------------
def cmd_capabilities(args):
    config = core.load_config(args.config)
    catalog = _load_catalog(config)
    categories = catalog.get("categories", [])
    query = (args.search or "").lower()
    category_filter = (args.category or "").lower()

    def cat_matches(category):
        if not category_filter:
            return True
        return (category_filter == str(category.get("id"))
                or category_filter in category.get("slug", "")
                or category_filter in category.get("name", "").lower())

    selected = [c for c in categories if cat_matches(c)]
    if not selected:
        raise core.PMError("No category matches '%s'" % args.category, code="category-not-found",
                           hint="Run 'capabilities' without --category to list all 32 categories.")

    skills = []
    for category in selected:
        for skill in category.get("skills", []):
            if query and query not in skill["name"].lower() and query not in skill.get("description", "").lower():
                continue
            skills.append(dict(skill, category=category["name"], category_id=category["id"],
                               coverage=category.get("coverage")))

    if args.skill:
        needle = args.skill.lower()
        exact = [s for s in skills if s["name"].lower() == needle]
        skills = exact or [s for s in skills if needle in s["name"].lower() or needle in s["slug"]]
        if not skills:
            raise core.PMError("No skill matches '%s'" % args.skill, code="skill-not-found",
                               hint="Try a shorter fragment, or run 'capabilities --search <term>'.")

    result = core.new_result("capabilities")
    result.update({
        "catalog": catalog.get("catalog"),
        "catalog_version": catalog.get("catalog_version"),
        "category_count": catalog.get("category_count"),
        "skill_count": catalog.get("skill_count"),
        "matched_categories": len(selected),
        "matched_skills": len(skills),
        "categories": [{
            "id": c.get("id"), "name": c.get("name"), "skill_count": c.get("skill_count"),
            "coverage": c.get("coverage"),
        } for c in selected],
        "skills": skills,
        "commands": ["capabilities", "intake", "validate", "brief", "plan", "draft", "build", "audit",
                     "diff", "benchmark", "package", "convert", "pipeline", "config", "selftest"],
    })
    if not skills:
        result["diagnostics"].append(core.diag("warning", "no-match",
                                               "No skill matched the filters", hint="Remove --search or --skill."))
    core.finish(result)

    if args.format == "json" or args.json:
        text = json.dumps(result, indent=2, ensure_ascii=False)
    elif args.format == "md":
        lines = ["# presentation-makers capabilities", "",
                 "%s categories, %s skills." % (result["category_count"], result["skill_count"]), ""]
        for category in result["categories"]:
            coverage = category.get("coverage") or {}
            lines.append("## %s. %s (%s skills)" % (category["id"], category["name"], category["skill_count"]))
            if coverage:
                lines.append("_Handled by:_ `%s` (%s)" % (", ".join(coverage.get("commands", [])),
                                                          coverage.get("mode", "")))
            lines.append("")
            for skill in [s for s in skills if s["category_id"] == category["id"]]:
                lines.append("- **%s** - %s" % (skill["name"], skill["description"]))
            lines.append("")
        text = "\n".join(lines)
    else:
        lines = ["presentation-makers - %s categories, %s skills (catalog v%s)"
                 % (result["category_count"], result["skill_count"], result["catalog_version"]), ""]
        for category in result["categories"]:
            coverage = category.get("coverage") or {}
            tag = ""
            if coverage.get("commands"):
                tag = "  -> %s [%s]" % (", ".join(coverage["commands"]), coverage.get("mode", ""))
            lines.append("%2s. %-58s %2d skills%s"
                         % (category["id"], category["name"], category["skill_count"], tag))
        if args.category or args.search or args.skill:
            lines.append("")
            for skill in skills:
                lines.append("- %s [%s]: %s" % (skill["name"], skill["category"], skill["description"]))
        lines += ["", "Commands: " + ", ".join(result["commands"]), ""]
        text = "\n".join(lines)

    if args.out:
        path = core.write_text(args.out, text + "\n", force=args.force)
        print("Wrote %s" % core.relpath_for_display(path))
        return pm_core.EXIT_OK
    print(text)
    return pm_core.EXIT_OK


# --------------------------------------------------------------------------
# intake
# --------------------------------------------------------------------------
def cmd_intake(args):
    config = core.load_config(args.config)
    if args.input == "-":
        raw = sys.stdin.read()
        source_name = "stdin"
    else:
        raw = core.read_text(require(args.input, "--input"))
        source_name = os.path.basename(args.input)
    if len(raw.encode("utf-8")) > config["limits"].get("max_input_bytes", 2097152):
        raise core.PMError("Input exceeds the configured size limit", code="input-too-large")

    parsed = intake.parse_catalog(raw, source_format=args.format, single_name=args.single_name,
                                  default_category=args.default_category)
    coverage = None
    if args.coverage_map:
        coverage = core.read_json(args.coverage_map)
        if not isinstance(coverage, dict) or "by_name" not in coverage:
            raise core.PMError("Coverage map must contain a 'by_name' object", code="coverage-invalid")
    catalog = intake.build_catalog(parsed, coverage_map=coverage, source_name=source_name,
                                   source_label=args.source_label)
    problems = intake.validate_catalog(catalog)
    errors = [p for p in problems if p["level"] == "error"]
    if errors:
        raise core.PMError("Parsed catalog failed validation: %s" % errors[0]["message"],
                           code="catalog-invalid",
                           hint="Check that the source has category headers and 'Skill Name:'/Description pairs.")

    target = args.out or os.path.basename(config["paths"].get("catalog", "asset-skill-catalog.json"))
    path = core.write_json(target, catalog, force=args.force)
    result = core.new_result("intake")
    result["diagnostics"] = problems
    result.update({
        "source": source_name,
        "source_format": parsed["source_format"],
        "category_count": catalog["category_count"],
        "skill_count": catalog["skill_count"],
        "outputs": [path],
    })
    core.finish(result)
    human = ("Parsed %s (%s): %d categories, %d skills -> %s"
             % (source_name, parsed["source_format"], catalog["category_count"],
                catalog["skill_count"], core.relpath_for_display(path)))
    return emit(result, args, human)


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------
def cmd_validate(args):
    config = core.load_config(args.config)
    payload = core.read_json(require(args.input, "--input"))
    keys = {"brief": "brief_schema", "deck-spec": "deck_spec_schema", "spec": "deck_spec_schema"}
    if args.kind not in keys:
        raise core.PMError("Unknown --kind '%s'" % args.kind, code="kind-unknown",
                           hint="Use --kind brief or --kind deck-spec")
    errors = core.validate_against_asset(payload, config, keys[args.kind])
    result = core.new_result("validate")
    result["diagnostics"] = errors
    result.update({"kind": args.kind, "input": args.input, "valid": not any(e["level"] == "error" for e in errors)})
    core.finish(result)
    human = "%s is %s against the %s schema (%d diagnostic(s))" % (
        os.path.basename(args.input), "valid" if result["valid"] else "INVALID", args.kind, len(errors))
    code = emit(result, args, human)
    return pm_core.EXIT_OK if result["valid"] else pm_core.EXIT_USAGE if code == 0 else code


# --------------------------------------------------------------------------
# brief
# --------------------------------------------------------------------------
def cmd_brief(args):
    config = core.load_config(args.config)
    brief, schema_diags = plan.load_brief(require(args.brief, "--brief"), config)
    framework, framework_diags = plan.choose_framework(brief, config)
    ranked = plan.score_platforms(brief, config)
    top = ranked[0]
    risks = plan.risk_register(brief, top)
    sequence = plan.production_plan(brief, top)
    target_dir = os.path.join(outdir(args), "brief")
    core.ensure_dir(target_dir)

    brief_out = core.write_json(os.path.join(target_dir, "brief.json"), brief, force=args.force)
    analysis = {
        "tool": core.TOOL_NAME,
        "command": "brief",
        "generated_at": core.now_iso(),
        "brief": brief,
        "assumptions": plan.brief_assumptions(brief),
        "framework": {"id": framework["id"], "name": framework["name"],
                      "description": framework.get("description", ""),
                      "sections": [s["title"] for s in framework.get("sections", [])]},
        "platform_ranking": ranked,
        "risk_register": risks,
        "production_sequence": sequence,
        "review_checkpoints": brief.get("review_checkpoints", []),
        "success_criteria": brief.get("success_criteria", []),
    }
    analysis_path = core.write_json(os.path.join(target_dir, "brief-analysis.json"), analysis, force=args.force)

    md = ["# Presentation brief analysis",
          "",
          "**%s**" % brief["title"],
          "",
          "- Purpose: %s%s" % (brief["purpose"],
                              " - %s" % brief["purpose_detail"] if brief.get("purpose_detail") else ""),
          "- Audience: %s (%s, %s expertise)" % (brief["audience"]["primary"], brief["audience"]["size"],
                                                 brief["audience"]["expertise"]),
          "- Delivery: %s, %s minutes, network %s"
          % (brief["delivery"]["mode"], brief["delivery"]["duration_minutes"], brief["delivery"]["network"]),
          "- Slide target: %d" % brief["constraints"]["slides_target"],
          "",
          "## Platform recommendation", "",
          "| Rank | Platform | Score | Why | Cautions |", "|---|---|---|---|---|"]
    for index, row in enumerate(ranked[:5], start=1):
        md.append("| %d | %s | %d | %s | %s |" % (
            index, row["name"], row["score"], "; ".join(row["reasons"]) or "-", "; ".join(row["cautions"]) or "-"))
    md += ["", "Chosen: **%s** (score %d). %s" % (top["name"], top["score"], top["requires"]), "",
           "## Narrative framework", "",
           "**%s** - %s" % (framework["name"], framework.get("description", "")), "",
           "Sections: " + " -> ".join(s["title"] for s in framework.get("sections", [])), "",
           "## Risk register", "", "| Risk | Level | Trigger | Mitigation | Test |", "|---|---|---|---|---|"]
    for risk in risks:
        md.append("| %s | %s | %s | %s | %s |" % (risk["risk"], risk["level"], risk["trigger"],
                                                  risk["mitigation"], risk["test"]))
    md += ["", "## Production sequence", ""]
    for step in sequence:
        md.append("%d. **%s** - %s _Exit:_ %s" % (step["step"], step["name"], step["detail"], step["exit"]))
    md += ["", "## Assumptions applied", ""] + ["- %s" % a for a in analysis["assumptions"]]
    if brief.get("success_criteria"):
        md += ["", "## Success criteria", ""] + ["- %s" % c for c in brief["success_criteria"]]
    md += ["", "## Review checkpoints", ""] + ["- [ ] %s (%s)" % (c.get("stage"), c.get("approver"))
                                               for c in brief.get("review_checkpoints", [])]
    md_path = core.write_text(os.path.join(target_dir, "brief-analysis.md"), "\n".join(md) + "\n", force=args.force)

    result = core.new_result("brief")
    result["diagnostics"] = schema_diags + framework_diags
    result.update({
        "outputs": [brief_out, analysis_path, md_path],
        "framework": analysis["framework"]["id"],
        "platform": {"chosen": top["name"], "score": top["score"], "ranking": ranked[:5]},
        "risk_count": len(risks),
        "assumptions": analysis["assumptions"],
    })
    core.finish(result)
    human = ("Brief analysed: framework '%s', platform '%s' (%d/100), %d risk(s).\n"
             "Wrote %s, %s, %s" % (analysis["framework"]["id"], top["name"], top["score"], len(risks),
                                   os.path.basename(brief_out), os.path.basename(analysis_path), os.path.basename(md_path)))
    return emit(result, args, human)


# --------------------------------------------------------------------------
# plan / draft
# --------------------------------------------------------------------------
def _content_docs(paths):
    docs = []
    sources = []
    for path in paths:
        text = core.read_text(path)
        doc = plan.parse_content(text, source_name=os.path.basename(path))
        docs.append(doc)
        sources.append(os.path.basename(path))
    if len(docs) == 1:
        return docs[0], sources
    merged = {"title": docs[0]["title"], "sections": [], "sources": [],
              "preamble": " ".join(d.get("preamble", "") for d in docs)}
    for doc in docs:
        for section in doc["sections"]:
            section = dict(section)
            section["id"] = "c%d" % (len(merged["sections"]) + 1)
            merged["sections"].append(section)
        merged["sources"].extend(doc.get("sources", []))
    merged["sources"] = list(dict.fromkeys(merged["sources"]))
    return merged, sources


def _plan_command(args, require_content):
    config = core.load_config(args.config)
    brief, brief_diags = plan.load_brief(require(args.brief, "--brief"), config)
    content, content_sources = (None, [])
    if args.content:
        content, content_sources = _content_docs(args.content)
    elif require_content:
        raise core.PMError("This command needs at least one --content file",
                           code="content-required",
                           hint="Supply Markdown or JSON source material, or use 'plan' for a skeleton deck.")

    framework, fw_diags = plan.choose_framework(brief, config)
    spec, spec_diags = plan.build_deck_spec(brief, config, content=content,
                                            content_sources=content_sources, framework=framework)
    schema_errors = core.validate_against_asset(spec, config, "deck_spec_schema")
    hard = [e for e in schema_errors if e["level"] == "error"]
    if hard:
        raise core.PMError("Generated deck spec failed schema validation: %s" % hard[0]["message"],
                           code="spec-generation-failed",
                           hint="This is a tool defect; report the brief that produced it.")

    target_dir = outdir(args)
    guard_overwrite([os.path.join(target_dir, "deck-spec.json")], args, config, what="deck spec")
    spec_path = core.write_json(os.path.join(target_dir, "deck-spec.json"), spec, force=args.force)
    outputs = [spec_path]
    diagnostics = brief_diags + fw_diags + spec_diags + schema_errors

    content_map = None
    if content:
        content_map = []
        covered = set()
        for section in spec.get("sections", []):
            slide_ids = section.get("slide_ids", [])
            titles = [s.get("title") for s in spec.get("slides", []) if s.get("id") in slide_ids]
            matched = [c for c in content["sections"]
                       if any(tok in " ".join(titles).lower() for tok in [c["title"].lower()[:18]])]
            covered.update(c["id"] for c in matched)
            content_map.append({"section": section["title"], "slides": titles,
                                "content_sections": [c["title"] for c in matched]})
        unmapped = [c["title"] for c in content["sections"] if c["id"] not in covered]
        if unmapped:
            diagnostics.append(core.diag(
                "warning", "draft-content-unmapped",
                "%d content section(s) did not map to a narrative section: %s"
                % (len(unmapped), ", ".join(unmapped[:5])),
                hint="Rename the content heading to match a framework section, or extend the framework."))
        path = core.write_json(os.path.join(target_dir, "content-map.json"),
                               {"mapped": content_map, "unmapped": unmapped}, force=args.force)
        outputs.append(path)

    pacing = plan.pacing_plan(spec)
    pacing_path = core.write_json(os.path.join(target_dir, "pacing-plan.json"), pacing, force=args.force)
    outputs.append(pacing_path)

    md = ["# Deck plan - %s" % spec["deck"]["title"], "",
          "- Framework: %s" % spec["deck"]["framework"],
          "- Slides: %d (target %d), sections: %d" % (len(spec["slides"]), brief["constraints"]["slides_target"],
                                                      len(spec["sections"])),
          "- Planned time: %s minutes of %s available"
          % (spec["stats"]["planned_minutes"], brief["delivery"]["duration_minutes"]),
          "- Theme: %s" % spec["deck"]["theme_id"], "",
          "## Slide plan", "", "| # | Role | Title | Time | Sources |", "|---|---|---|---|---|"]
    for index, slide in enumerate(spec["slides"], start=1):
        md.append("| %d | %s | %s | %ds | %s |" % (index, slide["role"], slide["title"],
                                                   slide.get("pacing_seconds", 0),
                                                   len(slide.get("sources", [])) or "-"))
    if spec.get("assumptions"):
        md += ["", "## Assumptions", ""] + ["- %s" % a for a in spec["assumptions"]]
    md_path = core.write_text(os.path.join(target_dir, "deck-plan.md"), "\n".join(md) + "\n", force=args.force)
    outputs.append(md_path)

    result = core.new_result("draft" if require_content else "plan")
    result["diagnostics"] = diagnostics
    result.update({
        "outputs": outputs,
        "spec": spec_path,
        "stats": spec["stats"],
        "pacing": pacing,
        "framework": framework["id"],
    })
    core.finish(result)
    human = ("Deck spec written: %d slides in %d sections, %s planned minutes, framework '%s'.\n"
             "Wrote %s" % (len(spec["slides"]), len(spec["sections"]), spec["stats"]["planned_minutes"],
                           framework["id"], ", ".join(os.path.basename(p) for p in outputs)))
    return emit(result, args, human)


def cmd_plan(args):
    return _plan_command(args, require_content=False)


def cmd_draft(args):
    return _plan_command(args, require_content=True)


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def cmd_build(args):
    config = core.load_config(args.config)
    spec, schema_diags = load_spec(require(args.spec, "--spec"), config)
    formats = [f.strip().lower() for f in (args.formats or DEFAULT_FORMATS).split(",") if f.strip()]
    unknown = [f for f in formats if f not in ALL_FORMATS]
    if unknown:
        raise core.PMError("Unknown format(s): %s" % ", ".join(unknown), code="format-unknown",
                           hint="Supported formats: %s" % ", ".join(ALL_FORMATS))
    target_dir = outdir(args)
    expected_paths = [os.path.join(target_dir, name) for name in
                      ("deck.pptx", "deck.html", "handout.pdf", "handout.md", "speaker-notes.md",
                       "outline.md", "captions.srt")]
    guard_overwrite(expected_paths, args, config, what="rendered output(s)")
    produced = render.render_all(spec, target_dir, formats, config)

    diagnostics = list(schema_diags)
    verified = []
    for fmt, (path, size) in produced.items():
        ok, detail = package_mod._verify_file(path)
        verified.append({"format": fmt, "path": path, "bytes": size, "verified": ok, "check": detail})
        if not ok:
            diagnostics.append(core.diag("error", "build-verify-failed",
                                         "%s failed verification: %s" % (os.path.basename(path), detail)))
        if size > config["limits"].get("max_file_bytes", 26214400):
            diagnostics.append(core.diag("warning", "build-file-large",
                                         "%s is %.1f MB" % (os.path.basename(path), size / 1048576.0)))

    result = core.new_result("build")
    result["diagnostics"] = diagnostics
    result.update({"outputs": [p for p, _ in produced.values()], "verified": verified,
                   "formats": formats, "stats": spec.get("stats")})
    core.finish(result)
    human = "Built %d format(s):\n%s" % (len(produced), "\n".join(
        "  %-9s %-42s %7d bytes  %s" % (v["format"], os.path.basename(v["path"]), v["bytes"], v["check"])
        for v in verified))
    return emit(result, args, human)


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
def cmd_audit(args):
    config = core.load_config(args.config)
    spec, schema_diags = load_spec(require(args.spec, "--spec"), config)
    brief = None
    if args.brief:
        brief, _ = plan.load_brief(args.brief, config)
    if args.build_dir:
        # audit the formats that actually exist, and keep any the spec declared
        declared = list((spec.get("provenance") or {}).get("rendered_formats") or [])
        for fmt, filename in (("pptx", "deck.pptx"), ("pdf", "handout.pdf"), ("html", "deck.html")):
            if os.path.isfile(os.path.join(core.resolve_path(args.build_dir), filename)) and fmt not in declared:
                declared.append(fmt)
        if not declared:
            declared = ["pptx", "html", "pdf"]  # nothing rendered: report it
        spec.setdefault("provenance", {})["rendered_formats"] = declared
    report = audit_mod.audit(spec, config, build_dir=args.build_dir, brief=brief)
    if args.gate:
        wanted = {g.lower() for g in args.gate}
        report["gates"] = [g for g in report["gates"] if g["id"] in wanted or g["name"].lower() in wanted]
        report["findings"] = [f for f in report["findings"] if f.get("gate") in
                              {g["id"] for g in report["gates"]}]

    target_dir = outdir(args)
    core.ensure_dir(target_dir)
    json_path = core.write_json(os.path.join(target_dir, "audit-report.json"), report, force=args.force)
    md_path = core.write_text(os.path.join(target_dir, "audit-report.md"),
                              audit_mod.render_report(report), force=args.force)
    result = core.new_result("audit")
    result["diagnostics"] = schema_diags + report["findings"]
    result.update({
        "outputs": [json_path, md_path],
        "status": report["status"],
        "score": report["score"],
        "gates": report["gates"],
        "blocking_gate_failures": report["blocking_gate_failures"],
    })
    core.finish(result)
    if args.strict and report["counts"]["warning"]:
        result["status"] = "error"

    human = ["Audit: %s (score %s/100)" % (report["status"], report["score"])]
    for gate in report["gates"]:
        human.append("  %-14s %-7s score %5s  errors %d  warnings %d%s"
                     % (gate["id"], gate["status"], gate["score"], gate["errors"], gate["warnings"],
                        "  [blocking]" if gate["blocking"] and gate["status"] == "fail" else ""))
    human.append("  report: %s" % core.relpath_for_display(md_path))
    code = 0
    if report["status"] == "blocked":
        code = pm_core.EXIT_GATE
    elif args.strict and report["counts"]["warning"]:
        code = pm_core.EXIT_GATE
    emit(result, args, "\n".join(human))
    return code


# --------------------------------------------------------------------------
# diff / benchmark
# --------------------------------------------------------------------------
def cmd_diff(args):
    config = core.load_config(args.config)
    before, _ = load_spec(require(args.before, "--before"), config)
    after, _ = load_spec(require(args.after, "--after"), config)
    diff = audit_mod.compare_specs(before, after)
    target_dir = outdir(args)
    json_path = core.write_json(os.path.join(target_dir, "spec-diff.json"), diff, force=args.force)
    md = ["# Deck diff", "", "- Before: %s" % before.get("deck", {}).get("title"),
          "- After: %s" % after.get("deck", {}).get("title"), "",
          "- Added slides: %d" % len(diff["added"]),
          "- Removed slides: %d" % len(diff["removed"]),
          "- Changed slides: %d" % len(diff["changed"]),
          "- Reordered only: %s" % diff["reordered"], ""]
    for group in ("added", "removed"):
        if diff[group]:
            md += ["## %s" % group.capitalize(), ""] + ["- %s: %s" % (i["slide_id"], i["title"]) for i in diff[group]]
            md.append("")
    if diff["changed"]:
        md += ["## Changed", ""] + ["- %s: %s (%s)" % (c["slide_id"], c["title"], ", ".join(c["fields"]))
                                    for c in diff["changed"]]
    md_path = core.write_text(os.path.join(target_dir, "spec-diff.md"), "\n".join(md) + "\n", force=args.force)

    result = core.new_result("diff")
    result.update({"outputs": [json_path, md_path], "diff": diff})
    core.finish(result)
    human = ("Diff: %d added, %d removed, %d changed%s"
             % (len(diff["added"]), len(diff["removed"]), len(diff["changed"]),
                ", reordered" if diff["reordered"] else ""))
    return emit(result, args, human)


def cmd_benchmark(args):
    config = core.load_config(args.config)
    brief = None
    if args.brief:
        brief, _ = plan.load_brief(args.brief, config)
    candidates = []
    for path in args.spec:
        spec, _ = load_spec(path, config)
        report = audit_mod.audit(spec, config, build_dir=args.build_dir, brief=brief)
        candidates.append({
            "spec": path,
            "title": spec.get("deck", {}).get("title"),
            "score": report["score"],
            "status": report["status"],
            "errors": report["counts"]["error"],
            "warnings": report["counts"]["warning"],
            "slides": len(spec.get("slides", [])),
            "planned_minutes": spec.get("stats", {}).get("planned_minutes"),
            "source_coverage": round(100.0 * sum(1 for s in spec.get("slides", []) if s.get("sources"))
                                     / max(1, len(spec.get("slides", []))), 1),
            "gates": {g["id"]: g["status"] for g in report["gates"]},
        })
    candidates.sort(key=lambda c: (-c["score"], c["errors"], c["warnings"], c["slides"]))
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
    result = core.new_result("benchmark")
    result.update({"outputs": [], "candidates": candidates, "criteria": [
        "audit score", "blocking errors", "warnings", "source coverage per slide", "slide count"]})
    core.finish(result)
    human = "Benchmark (%d candidate(s)):\n%s" % (len(candidates), "\n".join(
        "  %d. %-42s score %5s  err %d  warn %d  slides %d  sourced %s%%"
        % (c["rank"], (c["title"] or c["spec"])[:42], c["score"], c["errors"], c["warnings"],
           c["slides"], c["source_coverage"]) for c in candidates))
    return emit(result, args, human)


# --------------------------------------------------------------------------
# package
# --------------------------------------------------------------------------
def cmd_package(args):
    config = core.load_config(args.config)
    spec, schema_diags = load_spec(require(args.spec, "--spec"), config)
    build_dir = require(args.build_dir, "--build-dir")
    report = None
    if args.audit_report:
        report = core.read_json(args.audit_report)
    else:
        report = audit_mod.audit(spec, config, build_dir=build_dir)
    result = package_mod.build_package(spec, build_dir, outdir(args), config, report=report,
                                       approve=getattr(args, "force", False),
                                       extra_files=args.extra or [])
    verification = package_mod.verify_package(result["zip"]) if args.verify else None
    diagnostics = schema_diags + result.get("diagnostics", [])
    if verification and verification["status"] != "pass":
        diagnostics.extend(verification["findings"])
    payload = core.new_result("package")
    payload["diagnostics"] = diagnostics
    payload.update({
        "outputs": [result["folder"], result["zip"]],
        "package": {k: v for k, v in result.items() if k != "diagnostics"},
        "verification": verification,
    })
    core.finish(payload)
    if payload["status"] == "error":
        return emit(payload, args, "Packaging failed")
    human = ("Package built: %s (%d files, %.1f KB)\n  folder: %s\n  zip: %s"
             % (result["name"], len(result["files"]), result["zip_bytes"] / 1024.0,
                core.relpath_for_display(result["folder"]), core.relpath_for_display(result["zip"])))
    if verification:
        human += "\n  verification: %s (%d files reopened from a fresh extraction)" % (
            verification["status"], len(verification["verified"]))
    code = emit(payload, args, human)
    if verification and verification["status"] != "pass":
        return pm_core.EXIT_GATE
    return code


# --------------------------------------------------------------------------
# convert (external tool, gated)
# --------------------------------------------------------------------------
def cmd_convert(args):
    config = core.load_config(args.config)
    target_dir = outdir(args)
    core.ensure_dir(target_dir)
    if not args.approve_external_tool:
        raise core.PMError(
            "Running an external converter needs explicit approval",
            code="approval-required",
            exit_code=pm_core.EXIT_APPROVAL,
            hint="Re-run with --approve-external-tool after confirming the binary and the input file.",
        )
    binary = shutil.which(args.tool)
    if not binary:
        raise core.PMError(
            "External converter '%s' was not found on PATH" % args.tool,
            code="external-tool-missing",
            exit_code=pm_core.EXIT_EXTERNAL,
            hint="Install the converter, or use the bundled native outputs "
                 "(pptx, html, pdf, md, notes, outline, srt) which need no external tool.",
        )
    source = core.resolve_path(require(args.input, "--input"))
    if not os.path.isfile(source):
        raise core.PMError("Input file not found: %s" % args.input, code="input-missing")
    if os.path.getsize(source) > config["limits"].get("max_file_bytes", 26214400):
        raise core.PMError("Input file exceeds the configured size limit", code="input-too-large")

    command = {
        "soffice": [binary, "--headless", "--convert-to", args.to, "--outdir", target_dir, source],
        "libreoffice": [binary, "--headless", "--convert-to", args.to, "--outdir", target_dir, source],
        "unoconv": [binary, "-f", args.to, "-o", target_dir, source],
    }.get(os.path.basename(binary).lower().split(".")[0], [binary])
    timeout = int(args.timeout or config["limits"].get("external_tool_timeout_seconds", 180))
    attempts = 0
    last_error = ""
    while attempts <= config["limits"].get("max_retries", 2):
        attempts += 1
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            if completed.returncode == 0:
                produced = sorted(
                    os.path.join(target_dir, name) for name in os.listdir(target_dir)
                    if os.path.splitext(name)[1].lower().lstrip(".") == args.to.split(":")[0].lower()
                    or os.path.splitext(name)[0] == os.path.splitext(os.path.basename(source))[0])
                result = core.new_result("convert")
                result.update({"command": command, "attempts": attempts, "outputs": produced,
                               "external_tool": binary, "note":
                               "External conversion is outside the bundled renderers; "
                               "verify the output before delivery."})
                core.finish(result)
                return emit(result, args, "Converted with %s after %d attempt(s): %s"
                            % (os.path.basename(binary), attempts, ", ".join(os.path.basename(p) for p in produced)))
            last_error = (completed.stderr or completed.stdout or "").strip()[:400]
        except subprocess.TimeoutExpired:
            last_error = "timed out after %ds" % timeout
        if attempts > config["limits"].get("max_retries", 2):
            break
    raise core.PMError("Conversion failed after %d attempt(s): %s" % (attempts, last_error or "unknown error"),
                       code="conversion-failed", exit_code=pm_core.EXIT_EXTERNAL)


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def cmd_pipeline(args):
    config = core.load_config(args.config)
    steps = []
    target = outdir(args)
    core.ensure_dir(target)

    brief, _ = plan.load_brief(require(args.brief, "--brief"), config)
    framework, _ = plan.choose_framework(brief, config)
    ranked = plan.score_platforms(brief, config)
    steps.append({"step": "brief", "status": "ok", "platform": ranked[0]["name"], "score": ranked[0]["score"],
                  "framework": framework["id"]})

    content, content_sources = (None, [])
    if args.content:
        content, content_sources = _content_docs(args.content)
    spec, spec_diags = plan.build_deck_spec(brief, config, content=content,
                                            content_sources=content_sources, framework=framework)
    spec_path = core.write_json(os.path.join(target, "deck-spec.json"), spec, force=args.force)
    steps.append({"step": "plan", "status": "ok", "slides": len(spec["slides"]),
                  "sections": len(spec["sections"]), "output": spec_path})

    formats = [f.strip().lower() for f in (args.formats or DEFAULT_FORMATS).split(",") if f.strip()]
    produced = render.render_all(spec, target, formats, config)
    verified = {fmt: package_mod._verify_file(path)[0] for fmt, (path, _size) in produced.items()}
    steps.append({"step": "build", "status": "ok" if all(verified.values()) else "error",
                  "formats": formats, "verified": verified})

    spec.setdefault("provenance", {})["rendered_formats"] = [f for f in formats if f in ("pptx", "html", "pdf")]
    report = audit_mod.audit(spec, config, build_dir=target, brief=brief)
    core.write_json(os.path.join(target, "audit-report.json"), report, force=args.force)
    core.write_text(os.path.join(target, "audit-report.md"), audit_mod.render_report(report), force=args.force)
    core.ensure_dir(os.path.join(target, "audit"))
    steps.append({"step": "audit", "status": report["status"], "score": report["score"],
                  "errors": report["counts"]["error"], "warnings": report["counts"]["warning"],
                  "blocking": report["blocking_gate_failures"]})

    diagnostics = list(spec_diags) + list(report["findings"])
    packaged = None
    if report["status"] == "blocked" and not args.allow_blocked:
        diagnostics.append(core.diag(
            "error", "pipeline-blocked",
            "Audit blocked delivery; packaging was skipped",
            hint="Fix the blocking findings, or pass --allow-blocked to package a draft anyway."))
    else:
        packaged = package_mod.build_package(spec, target, target, config, report=report,
                                             approve=args.force, extra_files=[])
        verification = package_mod.verify_package(packaged["zip"])
        steps.append({"step": "package", "status": verification["status"], "zip": packaged["zip"],
                      "files": len(packaged["files"]), "verified_files": len(verification["verified"])})
        if verification["status"] != "pass":
            diagnostics.extend(verification["findings"])

    result = core.new_result("pipeline")
    result["diagnostics"] = diagnostics
    result.update({
        "outputs": [spec_path] + [p for p, _ in produced.values()] + ([packaged["zip"]] if packaged else []),
        "steps": steps,
        "spec": spec_path,
        "audit": {"status": report["status"], "score": report["score"],
                  "blocking": report["blocking_gate_failures"]},
        "stats": spec.get("stats"),
    })
    core.finish(result)
    human = ["Pipeline complete", ]
    for step in steps:
        detail = ", ".join("%s=%s" % (k, v) for k, v in step.items() if k not in ("step", "status"))
        human.append("  %-8s %-6s %s" % (step["step"], step["status"], detail))
    code = emit(result, args, "\n".join(human))
    if report["status"] == "blocked" and not args.allow_blocked:
        return pm_core.EXIT_GATE
    return code


# --------------------------------------------------------------------------
# config / selftest
# --------------------------------------------------------------------------
def cmd_config(args):
    config = core.load_config(args.config)
    if args.show:
        print(json.dumps(config, indent=2))
        return pm_core.EXIT_OK
    print("Effective configuration (%s)" % (args.config or "built-in defaults"))
    print(json.dumps({k: config[k] for k in ("limits", "policy", "audit", "render", "ai")}, indent=2))
    return pm_core.EXIT_OK


def cmd_selftest(args):
    module = _load("pm_selftest", "tools-selftest.py")
    return module.run(verbose=args.verbose)


# --------------------------------------------------------------------------
# Argument parser
# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="presentation-makers: one Skill Tool for the 32-category, 320-capability "
                    "presentation catalog (requirements, narrative, drafting, rendering, "
                    "accessibility audit, packaging). Offline, standard library only.",
        epilog="""examples (POSIX shell):
  python3 tools-presentation-makers.py capabilities
  python3 tools-presentation-makers.py capabilities --category 13 --format md
  python3 tools-presentation-makers.py intake --input catalog.txt --coverage-map asset-coverage-map.json --out asset-skill-catalog.json --force
  python3 tools-presentation-makers.py validate --kind brief --input asset-example-brief.json
  python3 tools-presentation-makers.py brief --brief asset-example-brief.json --outdir out
  python3 tools-presentation-makers.py draft --brief asset-example-brief.json --content asset-example-content.md --outdir out
  python3 tools-presentation-makers.py build --spec out/deck-spec.json --outdir out/deck --formats pptx,html,pdf,md,notes
  python3 tools-presentation-makers.py audit --spec out/deck-spec.json --build-dir out/deck --outdir out/audit
  python3 tools-presentation-makers.py package --spec out/deck-spec.json --build-dir out/deck --outdir out --verify
  python3 tools-presentation-makers.py pipeline --brief asset-example-brief.json --content asset-example-content.md --outdir out
  python3 tools-presentation-makers.py selftest
""")
    parser.add_argument("--version", action="version",
                        version="%s %s" % (core.TOOL_NAME, core.TOOL_VERSION))
    parser.add_argument("--config", help="optional JSON configuration overriding the built-in defaults")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON envelope")
    parser.add_argument("--force", action="store_true",
                        help="approve overwriting existing outputs (required by policy)")
    parser.add_argument("--quiet", action="store_true", help="suppress informational output")

    # Options that must be accepted after the subcommand as well as before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    common.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    common.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")

    cap = sub.add_parser("capabilities", parents=[common], help="list categories and skills, or inspect one skill")
    cap.add_argument("--category", help="category id, slug, or name fragment")
    cap.add_argument("--search", help="filter skills by name or description text")
    cap.add_argument("--skill", help="show one skill by name or slug fragment")
    cap.add_argument("--format", choices=["text", "json", "md"], default="text")
    cap.add_argument("--out", help="write the listing to a file instead of stdout")
    cap.set_defaults(func=cmd_capabilities)

    intk = sub.add_parser("intake", parents=[common], help="parse pasted text, Markdown, or HTML into a catalog")
    intk.add_argument("--input", help="file path, or '-' for stdin")
    intk.add_argument("--format", choices=["auto", "text", "markdown", "html"], default="auto")
    intk.add_argument("--single-name", help="name to use when the input is one capability")
    intk.add_argument("--default-category", help="category to use when the input has no headers")
    intk.add_argument("--coverage-map", help="asset-coverage-map.json to attach command coverage")
    intk.add_argument("--source-label", help="human-readable source description stored in the catalog")
    intk.add_argument("--out", help="output catalog path")
    intk.set_defaults(func=cmd_intake)

    val = sub.add_parser("validate", parents=[common], help="validate a brief or deck spec against its schema")
    val.add_argument("--kind", required=True, choices=["brief", "deck-spec", "spec"])
    val.add_argument("--input", required=True)
    val.set_defaults(func=cmd_validate)

    brf = sub.add_parser("brief", parents=[common], help="interpret requirements, choose a platform, plan production")
    brf.add_argument("--brief", required=True)
    brf.add_argument("--outdir")
    brf.set_defaults(func=cmd_brief)

    pln = sub.add_parser("plan", parents=[common], help="build the narrative and slide skeleton from a brief")
    pln.add_argument("--brief", required=True)
    pln.add_argument("--content", nargs="*", help="optional content files to map into the plan")
    pln.add_argument("--outdir")
    pln.set_defaults(func=cmd_plan)

    drf = sub.add_parser("draft", parents=[common], help="fill the plan from supplied content into a full deck spec")
    drf.add_argument("--brief", required=True)
    drf.add_argument("--content", nargs="+", required=True)
    drf.add_argument("--outdir")
    drf.set_defaults(func=cmd_draft)

    bld = sub.add_parser("build", parents=[common], help="render a deck spec to pptx, html, pdf, markdown, notes, captions")
    bld.add_argument("--spec", required=True)
    bld.add_argument("--outdir")
    bld.add_argument("--formats", default=DEFAULT_FORMATS,
                     help="comma separated: %s" % ", ".join(ALL_FORMATS))
    bld.set_defaults(func=cmd_build)

    aud = sub.add_parser("audit", parents=[common], help="run accessibility, integrity, privacy, and delivery gates")
    aud.add_argument("--spec", required=True)
    aud.add_argument("--build-dir", help="directory holding rendered outputs to inspect")
    aud.add_argument("--brief", help="optional brief for brand constraints")
    aud.add_argument("--gate", action="append", help="limit the report to one gate (repeatable)")
    aud.add_argument("--strict", action="store_true", help="treat warnings as blocking")
    aud.add_argument("--outdir")
    aud.set_defaults(func=cmd_audit)

    dif = sub.add_parser("diff", parents=[common], help="compare two deck specs for regressions")
    dif.add_argument("--before", required=True)
    dif.add_argument("--after", required=True)
    dif.add_argument("--outdir")
    dif.set_defaults(func=cmd_diff)

    ben = sub.add_parser("benchmark", parents=[common], help="score several deck specs against one brief")
    ben.add_argument("--spec", nargs="+", required=True)
    ben.add_argument("--brief")
    ben.add_argument("--build-dir")
    ben.set_defaults(func=cmd_benchmark)

    pkg = sub.add_parser("package", parents=[common], help="assemble and verify the delivery package")
    pkg.add_argument("--spec", required=True)
    pkg.add_argument("--build-dir", required=True)
    pkg.add_argument("--audit-report", help="reuse an existing audit-report.json")
    pkg.add_argument("--extra", nargs="*", help="extra files to include (no archives)")
    pkg.add_argument("--verify", action="store_true", default=True,
                     help="re-open every packaged file from a fresh extraction (default on)")
    pkg.add_argument("--no-verify", dest="verify", action="store_false")
    pkg.add_argument("--outdir")
    pkg.set_defaults(func=cmd_package)

    cnv = sub.add_parser("convert", parents=[common], help="delegate conversion to an approved local converter")
    cnv.add_argument("--input", required=True)
    cnv.add_argument("--to", required=True, help="target format understood by the converter, e.g. pdf")
    cnv.add_argument("--tool", default="soffice", help="converter binary on PATH (default soffice)")
    cnv.add_argument("--timeout", type=int, help="per-attempt timeout in seconds")
    cnv.add_argument("--approve-external-tool", action="store_true",
                     help="required: confirms you approve running the external binary")
    cnv.add_argument("--outdir")
    cnv.set_defaults(func=cmd_convert)

    pipe = sub.add_parser("pipeline", parents=[common], help="run brief -> plan -> build -> audit -> package end to end")
    pipe.add_argument("--brief", required=True)
    pipe.add_argument("--content", nargs="*")
    pipe.add_argument("--formats", default=DEFAULT_FORMATS)
    pipe.add_argument("--allow-blocked", action="store_true",
                      help="package even when the audit blocks delivery")
    pipe.add_argument("--outdir")
    pipe.set_defaults(func=cmd_pipeline)

    cfg = sub.add_parser("config", parents=[common], help="show the effective configuration")
    cfg.add_argument("--show", action="store_true", help="print the full merged configuration")
    cfg.set_defaults(func=cmd_config)

    st = sub.add_parser("selftest", parents=[common], help="run the bundled test suite")
    st.add_argument("--verbose", action="store_true")
    st.set_defaults(func=cmd_selftest)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return pm_core.EXIT_OK
    try:
        return args.func(args)
    except pm_core.PMError as exc:
        return fail(exc, args)
    except KeyboardInterrupt:
        print("Cancelled by user", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - last-resort guard
        import traceback
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "code": "internal", "message": str(exc),
                              "traceback": traceback.format_exc()}, indent=2))
        else:
            print("INTERNAL ERROR: %s" % exc, file=sys.stderr)
            if os.environ.get("PM_DEBUG"):
                traceback.print_exc()
            else:
                print("Set PM_DEBUG=1 for the traceback.", file=sys.stderr)
        return pm_core.EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
