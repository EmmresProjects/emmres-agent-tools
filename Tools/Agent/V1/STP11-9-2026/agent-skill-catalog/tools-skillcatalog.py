#!/usr/bin/env python3
"""agent-skill-catalog — primary entry point.

A reusable Skill Tool for the Agent Skill Catalog: it parses any pasted catalog
(plain text, Markdown, copied HTML) into a structured registry, validates it,
routes task intents to catalog skills, generates implementation briefs, builds
and audits a saved reference library, scaffolds and checks Skill Tool packages,
and runs its own test suite.

Layout (all files sit flat beside this one):

    tools-skillcatalog.py      this entry point
    tools-catalog-parser.py    parsing / normalisation / rendering
    tools-catalog-planner.py   search, routing, briefs, validation, coverage, evidence
    tools-reference-library.py reference library build / audit / dedupe / package
    tools-skill-packager.py    package validation and scaffold generation
    tools-selftest.py          test suite
    knowledge-readme.md        full documentation
    asset-catalog-240-skills.md  canonical catalog data (240 skills, 24 categories)
    asset-reference-schema.json  reference entry schema
    asset-reference-seed.json    seed sources, one per reference domain pack
    asset-intent-keywords.json   routing keyword map
    asset-plan-template.md       implementation brief template

Standard library only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import signal
import sys

sys.dont_write_bytecode = True  # keep the package folder free of __pycache__

VERSION = "1.0.0"
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = "asset-catalog-240-skills.md"
DEFAULT_SCHEMA = "asset-reference-schema.json"
DEFAULT_SEED = "asset-reference-seed.json"
DEFAULT_KEYWORDS = "asset-intent-keywords.json"
DEFAULT_TEMPLATE = "asset-plan-template.md"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INPUT = 2
EXIT_BLOCKED = 3


# --------------------------------------------------------------------------- #
# Module loading (filenames use hyphens, so modules load through importlib)
# --------------------------------------------------------------------------- #
def _load(name: str, filename: str):
    path = os.path.join(TOOL_DIR, filename)
    if not os.path.isfile(path):
        raise RuntimeError("required module missing: %s (expected beside the entry point)" % filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parser_mod = _load("tools_catalog_parser", "tools-catalog-parser.py")
planner_mod = _load("tools_catalog_planner", "tools-catalog-planner.py")
library_mod = _load("tools_reference_library", "tools-reference-library.py")
packager_mod = _load("tools_skill_packager", "tools-skill-packager.py")

CatalogError = parser_mod.CatalogError


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
class _Interrupted(Exception):
    pass


def _on_sigint(signum, frame):  # pragma: no cover - signal path
    raise _Interrupted()


def asset_path(value: str | None, default_name: str) -> str:
    """Resolve an asset reference: 'asset', None or a relative file name."""
    if value in (None, "", "asset", "default", "bundled"):
        return os.path.join(TOOL_DIR, default_name)
    if os.path.isabs(value):
        return value
    if os.path.exists(value):
        return value
    candidate = os.path.join(TOOL_DIR, value)
    return candidate if os.path.exists(candidate) else value


def resolve_catalog(value: str | None) -> str:
    return asset_path(value, DEFAULT_CATALOG)


def load_catalog(path: str | None, fmt: str = "auto", max_bytes: int = parser_mod.MAX_TEXT_BYTES) -> dict:
    target = resolve_catalog(path)
    text = parser_mod.read_text(target, max_bytes=max_bytes)
    catalog = parser_mod.parse_catalog(text, fmt=fmt)
    catalog["source"] = os.path.basename(target)
    return catalog


def emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def write_text(path: str, text: str, force: bool = False, approve: bool = False) -> None:
    """Write through a temporary file so an interrupted run leaves no partial output."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    if os.path.exists(path) and not (force or approve):
        raise PermissionError("output already exists: %s (pass --force to replace it)" % path)
    tmp = path + ".tmp-partial"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _fail(message: str, code: int = EXIT_INPUT, as_json: bool = False) -> int:
    if as_json:
        emit_json({"ok": False, "error": message})
    print("error: %s" % message, file=sys.stderr)
    return code


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_capabilities(args) -> int:
    payload = {
        "tool": "agent-skill-catalog",
        "version": VERSION,
        "entry_point": "tools-skillcatalog.py",
        "requires": "Python 3.9+ standard library only",
        "capabilities": [
            {"id": "parse", "command": "parse --input FILE [--format auto|text|markdown|list|plain-list|html]",
             "summary": "Extract categories, skill names and descriptions from plain text, Markdown or copied HTML; render JSON, Markdown, text or HTML.",
             "catalog_skills": ["Reference Catalog Builder", "Reference Ingestion Assistant", "Document Structure Extractor"]},
            {"id": "validate", "command": "validate --input FILE [--strict] [--json]",
             "summary": "Structural validation with stable error and warning codes and a non-zero exit code on failure.",
             "catalog_skills": ["Template Compliance Checker", "Requirement Coverage Auditor"]},
            {"id": "search", "command": "search --query TEXT [--category N] [--limit N] [--json]",
             "summary": "Rank catalog skills by explainable lexical relevance, optionally within one category.",
             "catalog_skills": ["Reference Library Navigator", "Skill Discovery Router"]},
            {"id": "route", "command": "route --intent TEXT [--top N] [--json]",
             "summary": "Route a task intent to the best catalog skills using an editable signal map plus lexical scoring.",
             "catalog_skills": ["Skill Discovery Router", "Source Priority Router", "Multi-Agent Task Router"]},
            {"id": "plan", "command": "plan --skill NAME | --category N [--out PATH]",
             "summary": "Generate an implementation brief: triggers, inputs, steps, outputs, approval gates, verification checklist, limits.",
             "catalog_skills": ["Agent Capability Architect", "Instruction Set Optimizer", "Agent Safety Boundary Reviewer"]},
            {"id": "coverage", "command": "coverage [--goal TEXT] [--json]",
             "summary": "Coverage counts per category plus goal-versus-library gap analysis with nearest catalog terms.",
             "catalog_skills": ["Reference Gap Finder", "Knowledge Frontier Mapper", "Requirement Coverage Auditor"]},
            {"id": "evidence", "command": "evidence --claims FILE --library DIR [--out FILE] [--csv FILE]",
             "summary": "Build a claim-by-source pre-screen matrix with explicit human judgement columns (never an automatic verdict).",
             "catalog_skills": ["Evidence Matrix Builder", "Claim Verification Analyst", "Citation Context Checker"]},
            {"id": "library build", "command": "library build --entries FILE --out DIR",
             "summary": "Create category-named Markdown reference files, an archive index and a generated schema page.",
             "catalog_skills": ["Reference Catalog Builder", "Reference Library Navigator", "Source Provenance Recorder"]},
            {"id": "library audit", "command": "library audit --library DIR [--online] [--max-age-days N]",
             "summary": "Staleness, structure and optional bounded live link-health audit with a repair queue; never edits records.",
             "catalog_skills": ["Link Health Auditor", "Knowledge Decay Auditor", "Web Archive Locator"]},
            {"id": "library dedupe", "command": "library dedupe --library DIR [--apply --approve]",
             "summary": "Canonicalise URLs, group duplicates, merge annotations, tombstone history instead of deleting it.",
             "catalog_skills": ["Reference Deduplicator", "Note Consolidation Assistant"]},
            {"id": "library package", "command": "library package --library DIR --name PACK --out DIR",
             "summary": "Bundle a priority-ordered topic pack with progressive-loading instructions.",
             "catalog_skills": ["Topic Reference Packager", "Context Pack Builder"]},
            {"id": "scaffold", "command": "scaffold --name NAME --capability TEXT [--from-skill NAME] [--zip]",
             "summary": "Generate a working starter Skill Tool folder (runnable CLI, config asset, knowledge file) and optionally zip it.",
             "catalog_skills": ["Agent Capability Architect", "Agent Handoff Protocol Designer"]},
            {"id": "check", "command": "check --path DIR | --zip FILE [--json]",
             "summary": "Verify the Skill Tool packaging contract: no SKILL.md, no subfolders, prefixes, one knowledge file, depth, file count, references.",
             "catalog_skills": ["Artifact Inspection Agent", "Quality Scorecard Assessor", "Clean-State Reproducer"]},
            {"id": "selftest", "command": "selftest [--verbose]",
             "summary": "Run the shipped test suite against this folder through the real CLI surface.",
             "catalog_skills": ["Golden Reference Maintainer", "Regression Test Curator", "Repair Loop Coordinator"]},
        ],
        "catalog": {"default_file": DEFAULT_CATALOG, "skills": 240, "categories": 24},
        "assets": [DEFAULT_CATALOG, DEFAULT_SCHEMA, DEFAULT_SEED, DEFAULT_KEYWORDS, DEFAULT_TEMPLATE],
        "approval_required_for": ["library dedupe --apply", "overwriting existing output files",
                                  "replacing an existing scaffold folder", "creating a ZIP over an existing one"],
        "network": "off by default; only `library audit --online` contacts URLs, bounded by --max-urls/--timeout/--retries",
    }
    if args.json:
        emit_json(payload)
    else:
        print("agent-skill-catalog %s — %d capabilities\n" % (VERSION, len(payload["capabilities"])))
        for item in payload["capabilities"]:
            print("  %-16s %s" % (item["id"], item["summary"]))
            print("  %-16s $ python3 agent-skill-catalog/tools-skillcatalog.py %s" % ("", item["command"]))
        print("\nCatalog: %d skills in %d categories (%s)" % (240, 24, DEFAULT_CATALOG))
        print("Run '%s --help' for full usage." % os.path.basename(__file__))
    return EXIT_OK


def cmd_parse(args) -> int:
    max_bytes = args.max_bytes
    path = args.input
    text = parser_mod.read_text(path, max_bytes=max_bytes)
    catalog = parser_mod.parse_catalog(text, fmt=args.format)
    catalog["title"] = args.title
    renderers = {"json": parser_mod.to_json, "md": parser_mod.to_markdown,
                 "text": parser_mod.to_text, "html": parser_mod.to_html}
    rendered = renderers[args.emit](catalog)
    if args.out:
        write_text(args.out, rendered, force=args.force, approve=args.approve)
    else:
        sys.stdout.write(rendered)
    if args.stats:
        stats = parser_mod.catalog_stats(catalog)
        if args.json:
            emit_json({"ok": True, "stats": stats, "categories": catalog["categories"], "warnings": catalog.get("warnings", [])})
        else:
            print("\n-- stats --", file=sys.stderr)
            print("format=%s categories=%d skills=%d vocabulary=%d avg_words=%.1f warnings=%d" % (
                stats["format"], stats["categories"], stats["skills"], stats["vocabulary_size"],
                stats["average_description_words"], len(stats["warnings"])), file=sys.stderr)
    return EXIT_OK


def cmd_validate(args) -> int:
    try:
        catalog = load_catalog(args.catalog, fmt=args.format, max_bytes=args.max_bytes)
    except CatalogError as exc:
        return _fail(str(exc), EXIT_INPUT, args.json)
    result = planner_mod.validate_catalog(catalog, strict=args.strict)
    result["source"] = catalog.get("source")
    result["counts"]["format"] = catalog.get("format")
    if args.json:
        emit_json(result)
    else:
        print("%s: %d categories, %d skills (format: %s)" % (
            "VALID" if result["ok"] else "INVALID", result["counts"]["categories"],
            result["counts"]["skills"], catalog.get("format")))
        for item in result["errors"]:
            print("  ERROR %-28s %s [%s]" % (item["code"], item["message"], item["path"]))
        for item in result["warnings"]:
            print("  WARN  %-28s %s [%s]" % (item["code"], item["message"], item["path"]))
        if catalog.get("warnings"):
            print("  parser warnings: %d (rerun with --json to inspect)" % len(catalog["warnings"]))
    return EXIT_OK if result["ok"] else EXIT_FINDINGS


def cmd_search(args) -> int:
    catalog = load_catalog(args.catalog, max_bytes=args.max_bytes)
    results = planner_mod.search(catalog, args.query, category=args.category,
                                 limit=args.limit, min_score=args.min_score)
    if args.json:
        emit_json({"ok": True, "query": args.query, "category": args.category,
                   "matches": results, "total_scanned": len(catalog["skills"])})
    else:
        if not results:
            print("no matches for %r in %d skills (try shorter terms or --min-score 0)" % (args.query, len(catalog["skills"])))
            return EXIT_FINDINGS
        print("query: %r — %d match(es) from %d skills\n" % (args.query, len(results), len(catalog["skills"])))
        for rank, item in enumerate(results, start=1):
            print("%2d. %-42s %5.2f  [%s]" % (rank, item["name"], item["score"], item["category"]))
            print("    %s" % item["description"])
    return EXIT_OK


def cmd_route(args) -> int:
    catalog = load_catalog(args.catalog, max_bytes=args.max_bytes)
    keywords = planner_mod.load_keywords(asset_path(args.keywords, DEFAULT_KEYWORDS))
    result = planner_mod.route_intent(catalog, args.intent, keywords, top=args.top,
                                      min_score=args.min_score)
    if args.json:
        result["ok"] = True
        emit_json(result)
    else:
        print("intent: %s" % result["intent"])
        for reason in result["reasons"]:
            print("  signal: %s" % reason)
        if not result["matches"]:
            print("  no skill scored at or above the %.2f confidence floor; rephrase with concrete nouns "
                  "or lower --min-score" % args.min_score)
            return EXIT_FINDINGS
        print("")
        for rank, item in enumerate(result["matches"], start=1):
            print("%d. %-42s %5.2f  [%s]" % (rank, item["name"], item["score"], item["category"]))
            print("   %s" % item["description"])
            print("   next: %s" % item["command"])
    return EXIT_OK


def _find_skill(catalog: dict, wanted: str) -> tuple[dict | None, str]:
    lowered = wanted.strip().lower()
    for skill in catalog["skills"]:
        if skill["name"].lower() == lowered:
            return skill, ""
    for skill in catalog["skills"]:
        if lowered in skill["name"].lower():
            return skill, "matched by substring"
    names = [skill["name"] for skill in catalog["skills"]]
    close = __import__("difflib").get_close_matches(wanted, names, n=1, cutoff=0.6)
    if close:
        skill = next(item for item in catalog["skills"] if item["name"] == close[0])
        return skill, "matched by similarity to %r" % close[0]
    return None, ""


def cmd_plan(args) -> int:
    catalog = load_catalog(args.catalog, max_bytes=args.max_bytes)
    template_path = asset_path(args.template, DEFAULT_TEMPLATE)
    try:
        template = parser_mod.read_text(template_path)
    except CatalogError as exc:
        return _fail(str(exc), EXIT_INPUT, args.json)
    generated = args.generated or _dt.date.today().isoformat()

    if args.category is not None:
        category = next((cat for cat in catalog["categories"]
                         if str(cat["index"]) == str(args.category)
                         or parser_mod.slugify(cat["name"]) == parser_mod.slugify(str(args.category))), None)
        if category is None:
            return _fail("no category matching %r (use 'coverage' to list categories)" % args.category, EXIT_INPUT, args.json)
        out_dir = args.out or "plans"
        os.makedirs(out_dir, exist_ok=True)
        written = []
        for skill in category["skills"]:
            text = planner_mod.build_plan(skill, template, catalog.get("source", DEFAULT_CATALOG), generated)
            path = os.path.join(out_dir, "plan-%s.md" % skill["slug"])
            write_text(path, text, force=args.force, approve=args.approve)
            written.append(path)
        if args.json:
            emit_json({"ok": True, "category": category["name"], "written": written, "count": len(written)})
        else:
            print("wrote %d plan(s) for category %r into %s" % (len(written), category["name"], out_dir))
            for path in written:
                print("  %s" % path)
        return EXIT_OK

    if not args.skill:
        return _fail("pass --skill NAME or --category N", EXIT_INPUT, args.json)
    skill, note = _find_skill(catalog, args.skill)
    if skill is None:
        return _fail("skill %r not found in %s (try 'search --query ...')" % (args.skill, catalog.get("source")), EXIT_INPUT, args.json)
    text = planner_mod.build_plan(skill, template, catalog.get("source", DEFAULT_CATALOG), generated)
    if args.out:
        write_text(args.out, text, force=args.force, approve=args.approve)
        if args.json:
            emit_json({"ok": True, "skill": skill["name"], "category": skill["category"],
                       "out": args.out, "note": note})
        else:
            print("wrote plan for %r to %s%s" % (skill["name"], args.out, (" (%s)" % note) if note else ""))
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_coverage(args) -> int:
    catalog = load_catalog(args.catalog, max_bytes=args.max_bytes)
    report = planner_mod.coverage_report(catalog, goal=args.goal, top_gaps=args.top_gaps)
    if args.json:
        report["ok"] = True
        emit_json(report)
        return EXIT_OK
    print("%d categories · %d skills · %d distinct terms" % (
        report["categories"], report["skills"], report["vocabulary_size"]))
    print("")
    width = max(len(item["category"]) for item in report["per_category"])
    for item in report["per_category"]:
        print("  %2d. %-*s %3d" % (item["category_index"], width, item["category"], item["skills"]))
    if report["goal"]:
        print("\ngoal: %s" % report["goal"])
        print("coverage ratio: %.2f (%d of %d goal terms present in the catalog)" % (
            report.get("coverage_ratio", 0.0), len(report["covered_terms"]),
            len(report["covered_terms"]) + len(report["gaps"]) + report.get("gaps_truncated", 0)))
        if report["gaps"]:
            print("gaps (term -> nearest catalog terms):")
            for gap in report["gaps"]:
                print("  %-22s %s" % (gap["term"], ", ".join(gap["nearest_catalog_terms"]) or "(no close term)"))
        if report.get("gaps_truncated"):
            print("  ... %d more (use --top-gaps)" % report["gaps_truncated"])
    return EXIT_OK


def cmd_evidence(args) -> int:
    try:
        claims = json.loads(parser_mod.read_text(args.claims))
    except (ValueError, CatalogError) as exc:
        return _fail("cannot read claims file: %s" % exc, EXIT_INPUT, args.json)
    if isinstance(claims, dict):
        claims = claims.get("claims", claims.get("entries", []))
    if not isinstance(claims, list):
        return _fail("claims file must hold a JSON array (strings or objects with 'claim')", EXIT_INPUT, args.json)
    try:
        if args.entries:
            sources = library_mod.load_entries_file(args.entries)
        else:
            sources = library_mod.load_library(args.library or ".")
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _fail(str(exc), EXIT_INPUT, args.json)
    try:
        matrix = planner_mod.build_evidence_matrix(claims, sources, min_score=args.min_score)
    except ValueError as exc:
        return _fail(str(exc), EXIT_INPUT, args.json)
    markdown = planner_mod.matrix_to_markdown(matrix)
    if args.out:
        write_text(args.out, markdown, force=args.force, approve=args.approve)
    if args.csv:
        write_text(args.csv, planner_mod.matrix_to_csv(matrix), force=args.force, approve=args.approve)
    if args.json:
        emit_json({key: matrix[key] for key in ("ok", "claims", "sources", "min_score", "disclaimer")}
                  | {"rows": matrix["rows"][:args.top], "row_count": len(matrix["rows"])})
    else:
        sys.stdout.write(markdown)
        print("rows: %d (claims %d × sources %d, min-score %.2f)" % (
            len(matrix["rows"]), matrix["claims"], matrix["sources"], matrix["min_score"]))
        if args.out:
            print("written: %s" % args.out)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# library subcommands
# --------------------------------------------------------------------------- #
def cmd_library(args) -> int:
    action = args.library_action
    if action == "build":
        try:
            entries = library_mod.load_entries_file(asset_path(args.entries, DEFAULT_SEED))
        except (OSError, ValueError) as exc:
            return _fail("cannot read entries: %s" % exc, EXIT_INPUT, args.json)
        schema = None
        try:
            schema = json.loads(parser_mod.read_text(asset_path(None, DEFAULT_SCHEMA)))
        except (CatalogError, ValueError):
            schema = None
        validation = library_mod.validate_entries(entries, today=args.today)
        if not validation["ok"]:
            if args.json:
                emit_json(validation)
            for item in validation["errors"]:
                print("ERROR %-22s %s [%s]" % (item["code"], item["message"], item["entry"]), file=sys.stderr)
            return _fail("entries failed validation; library not written", EXIT_FINDINGS, False)
        try:
            result = library_mod.write_library(args.out, entries, schema=schema,
                                               force=args.force, approve=args.approve)
        except PermissionError as exc:
            return _fail(str(exc), EXIT_BLOCKED, args.json)
        result["validation"] = validation["counts"]
        if args.json:
            emit_json(result)
        else:
            print("library written: %s (%d entries, %d categories)" % (
                result["directory"], result["entries"], result["categories"]))
            for name in result["files"]:
                print("  %s" % name)
            print("validation: %d error(s), %d warning(s)" % (
                validation["counts"]["errors"], validation["counts"]["warnings"]))
        return EXIT_OK

    if action == "audit":
        try:
            entries = library_mod.load_library(args.library)
        except (FileNotFoundError, OSError) as exc:
            return _fail(str(exc), EXIT_INPUT, args.json)
        result = library_mod.audit_library(
            entries, today=args.today, max_age_days=args.max_age_days, online=args.online,
            timeout=args.timeout, retries=args.retries, max_urls=args.max_urls)
        report = library_mod.library_to_markdown_report(result)
        if args.out:
            write_text(args.out, report, force=args.force, approve=args.approve)
        if args.json:
            emit_json(result)
        else:
            sys.stdout.write(report)
            if args.out:
                print("written: %s" % args.out)
        return EXIT_OK if result["ok"] else EXIT_FINDINGS

    if action == "dedupe":
        try:
            entries = library_mod.load_library(args.library)
        except (FileNotFoundError, OSError) as exc:
            return _fail(str(exc), EXIT_INPUT, args.json)
        try:
            result = library_mod.dedupe_entries(entries, apply=args.apply, approve=args.approve)
        except PermissionError as exc:
            return _fail(str(exc), EXIT_BLOCKED, args.json)
        if result["applied"]:
            schema = None
            try:
                schema = json.loads(parser_mod.read_text(asset_path(None, DEFAULT_SCHEMA)))
            except (CatalogError, ValueError):
                schema = None
            library_mod.write_library(args.library, entries, schema=schema, force=True, approve=True)
            result["rewritten"] = args.library
        if args.json:
            emit_json(result)
        else:
            verb = "applied" if result["applied"] else "dry run"
            print("dedupe %s: %d entries, %d duplicate group(s), %d near-duplicate pair(s)" % (
                verb, result["entries"], result["duplicate_groups"], len(result["near_duplicates"])))
            for merge in result["merges"]:
                print("  keep %r, tombstone %s" % (merge["keep"], merge["tombstone"]))
                print("    %s" % merge["action"])
            for near in result["near_duplicates"]:
                print("  near-duplicate (%s): %s" % (near["reason"], near["entries"]))
            if not result["applied"]:
                print("\nnothing written. Re-run with --apply --approve to record the merges.")
        return EXIT_OK

    if action == "package":
        try:
            entries = library_mod.load_library(args.library)
        except (FileNotFoundError, OSError) as exc:
            return _fail(str(exc), EXIT_INPUT, args.json)
        try:
            result = library_mod.package_library(entries, args.name, categories=args.category,
                                                limit=args.limit, out_dir=args.out)
        except ValueError as exc:
            return _fail(str(exc), EXIT_INPUT, args.json)
        if args.json:
            emit_json(result)
        else:
            print("pack %r written to %s (%d entries, categories: %s)" % (
                result["pack"], result["out_dir"], result["selected"], ", ".join(result["categories"])))
            for name in result["files"]:
                print("  %s" % name)
        return EXIT_OK

    return _fail("unknown library action %r" % action, EXIT_INPUT, args.json)


def cmd_scaffold(args) -> int:
    capability = args.capability or ""
    brief = ""
    if args.from_skill:
        catalog = load_catalog(args.catalog, max_bytes=args.max_bytes)
        skill, note = _find_skill(catalog, args.from_skill)
        if skill is None:
            return _fail("skill %r not found in the catalog" % args.from_skill, EXIT_INPUT, args.json)
        template = parser_mod.read_text(asset_path(args.template, DEFAULT_TEMPLATE))
        brief = planner_mod.build_plan(skill, template, catalog.get("source", DEFAULT_CATALOG),
                                       args.generated or _dt.date.today().isoformat())
        capability = capability or skill["description"]
        if note and not args.json:
            print("note: %s" % note)
    elif not capability:
        return _fail("pass --capability TEXT or --from-skill NAME", EXIT_INPUT, args.json)
    try:
        result = packager_mod.scaffold(
            args.name, args.out, capability, brief=brief, catalog_ref=args.catalog_ref,
            with_tests=not args.no_tests, force=args.force, approve=args.approve,
            created=args.generated or _dt.date.today().isoformat())
    except (ValueError, PermissionError) as exc:
        return _fail(str(exc), EXIT_BLOCKED if isinstance(exc, PermissionError) else EXIT_INPUT, args.json)
    payload = {"ok": True, "name": result["name"], "folder": result["folder"], "files": result["files"],
               "structure_ok": result["structure"]["ok"]}
    if args.zip:
        try:
            archive = packager_mod.archive_folder(result["folder"], args.zip, approve=args.approve or args.force)
        except (PermissionError, OSError) as exc:
            return _fail(str(exc), EXIT_BLOCKED, args.json)
        payload["zip"] = args.zip
        payload["zip_ok"] = archive["ok"]
        payload["zip_members"] = archive["members"]
    if args.json:
        emit_json(payload)
    else:
        print("scaffolded %r in %s" % (payload["name"], payload["folder"]))
        for name in payload["files"]:
            print("  %s" % name)
        print("structure check: %s" % ("OK" if payload["structure_ok"] else "see 'check --path'"))
        if args.zip:
            print("zip: %s (%d members, %s)" % (args.zip, payload["zip_members"],
                                                "OK" if payload["zip_ok"] else "FAILED"))
    return EXIT_OK if payload["structure_ok"] and payload.get("zip_ok", True) else EXIT_FINDINGS


def cmd_check(args) -> int:
    try:
        if args.zip:
            result = packager_mod.validate_zip(args.zip, max_files=args.max_files)
            result["knowledge_readme"] = None
        else:
            if not args.path:
                return _fail("pass --path DIR or --zip FILE", EXIT_INPUT, args.json)
            result = packager_mod.validate_package(args.path, max_files=args.max_files,
                                                  check_references=not args.no_references)
    except FileNotFoundError as exc:
        return _fail(str(exc), EXIT_INPUT, args.json)
    if args.json:
        emit_json(result)
    else:
        target = args.zip or args.path
        print("%s: %s" % ("VALID" if result["ok"] else "INVALID", target))
        if "counts" in result:
            print("  counts: %s" % json.dumps(result["counts"]))
        for item in result["errors"]:
            print("  ERROR %-24s %s [%s]" % (item["code"], item["message"], item.get("path", "")))
        for item in result["warnings"]:
            print("  WARN  %-24s %s [%s]" % (item["code"], item["message"], item.get("path", "")))
    return EXIT_OK if result["ok"] else EXIT_FINDINGS


def cmd_selftest(args) -> int:
    import subprocess
    script = os.path.join(TOOL_DIR, "tools-selftest.py")
    if not os.path.isfile(script):
        return _fail("tools-selftest.py missing beside the entry point", EXIT_INPUT, False)
    command = [sys.executable, script]
    if args.verbose:
        command.append("--verbose")
    completed = subprocess.run(command, cwd=TOOL_DIR)
    return completed.returncode


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    description = (
        "agent-skill-catalog: parse, validate, route, plan and package an agent skill catalog.\n\n"
        "Every command works offline on the bundled 240-skill catalog or on your own catalog file.\n"
        "Start with: capabilities -> search -> plan -> scaffold -> check."
    )
    parser = argparse.ArgumentParser(
        prog="tools-skillcatalog.py", description=description, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 agent-skill-catalog/tools-skillcatalog.py capabilities\n"
               "  python3 agent-skill-catalog/tools-skillcatalog.py search --query \"threat model\" --limit 5\n"
               "  python3 agent-skill-catalog/tools-skillcatalog.py plan --skill \"Threat Model Facilitator\" --out ./plans/threat-model.md\n"
               "  python3 agent-skill-catalog/tools-skillcatalog.py check --path agent-skill-catalog\n")
    parser.add_argument("--version", action="version", version="agent-skill-catalog " + VERSION)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    caps = sub.add_parser("capabilities", help="list every capability and its command")
    caps.add_argument("--json", action="store_true")
    caps.set_defaults(func=cmd_capabilities)

    parse = sub.add_parser("parse", help="parse a catalog from text, Markdown or HTML")
    parse.add_argument("--input", required=True, help="catalog file or '-' for stdin")
    parse.add_argument("--format", default="auto",
                       choices=["auto", "text", "markdown", "list", "plain-list", "html"],
                       help="input shape; 'auto' detects it (default)")
    parse.add_argument("--emit", default="json", choices=["json", "md", "text", "html"])
    parse.add_argument("--out", help="output path (default: stdout)")
    parse.add_argument("--title", default="Skill Catalog", help="title used by md/html output")
    parse.add_argument("--stats", action="store_true", help="print container stats")
    parse.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    parse.add_argument("--json", action="store_true", help="emit stats as JSON (with --stats)")
    parse.add_argument("--force", action="store_true", help="replace an existing output file")
    parse.add_argument("--approve", action="store_true", help="explicit approval for replacement")
    parse.set_defaults(func=cmd_parse)

    validate = sub.add_parser("validate", help="validate a catalog's structure and content")
    validate.add_argument("--catalog", default="asset", help="'asset' (bundled) or a catalog file path")
    validate.add_argument("--format", default="auto", choices=["auto", "text", "markdown", "list", "plain-list", "html"])
    validate.add_argument("--strict", action="store_true", help="treat warnings as errors")
    validate.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    search = sub.add_parser("search", help="rank catalog skills by keyword relevance")
    search.add_argument("--query", required=True)
    search.add_argument("--catalog", default="asset")
    search.add_argument("--category", help="limit to one category (index, name or slug)")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--min-score", type=float, default=0.0)
    search.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)

    route = sub.add_parser("route", help="route a task intent to the best catalog skills")
    route.add_argument("--intent", required=True)
    route.add_argument("--catalog", default="asset")
    route.add_argument("--keywords", default=None, help="routing keyword asset (default: bundled)")
    route.add_argument("--top", type=int, default=5)
    route.add_argument("--min-score", type=float, default=0.75,
                       help="confidence floor for a match (default 0.75; 0 shows everything)")
    route.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    route.add_argument("--json", action="store_true")
    route.set_defaults(func=cmd_route)

    plan = sub.add_parser("plan", help="generate implementation brief(s) for catalog skills")
    plan.add_argument("--skill", help="exact, substring or close skill name")
    plan.add_argument("--category", help="generate one brief per skill in this category")
    plan.add_argument("--catalog", default="asset")
    plan.add_argument("--template", default=None, help="brief template (default: bundled asset)")
    plan.add_argument("--out", help="output file (with --skill) or directory (with --category)")
    plan.add_argument("--generated", default=None, help="date recorded in the brief (YYYY-MM-DD)")
    plan.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--force", action="store_true")
    plan.add_argument("--approve", action="store_true")
    plan.set_defaults(func=cmd_plan)

    coverage = sub.add_parser("coverage", help="coverage counts and goal-versus-catalog gaps")
    coverage.add_argument("--catalog", default="asset")
    coverage.add_argument("--goal", help="research or delivery goal to test against the catalog")
    coverage.add_argument("--top-gaps", type=int, default=15)
    coverage.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    coverage.add_argument("--json", action="store_true")
    coverage.set_defaults(func=cmd_coverage)

    evidence = sub.add_parser("evidence", help="claim-by-source pre-screen matrix (heuristic, human-judged)")
    evidence.add_argument("--claims", required=True, help="JSON file: array of strings or {id, claim} objects")
    evidence.add_argument("--library", help="reference library directory")
    evidence.add_argument("--entries", help="entries JSON file (alternative to --library)")
    evidence.add_argument("--out", help="Markdown matrix output path")
    evidence.add_argument("--csv", help="CSV matrix output path")
    evidence.add_argument("--min-score", type=float, default=0.05,
                          help="minimum lexical overlap to include a row (0 includes non-overlapping sources)")
    evidence.add_argument("--top", type=int, default=10, help="rows shown by --json")
    evidence.add_argument("--force", action="store_true")
    evidence.add_argument("--approve", action="store_true")
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(func=cmd_evidence)

    library = sub.add_parser("library", help="build, audit, dedupe or package a reference library")
    library_sub = library.add_subparsers(dest="library_action", metavar="ACTION")

    build = library_sub.add_parser("build", help="write category Markdown files from an entries file")
    build.add_argument("--entries", default=None, help="entries JSON (default: bundled seed)")
    build.add_argument("--out", required=True, help="library directory to create")
    build.add_argument("--today", default=None, help="date used for validation (YYYY-MM-DD)")
    build.add_argument("--force", action="store_true")
    build.add_argument("--approve", action="store_true", help="approve overwriting an existing library")
    build.add_argument("--json", action="store_true")

    audit = library_sub.add_parser("audit", help="staleness and optional live link audit")
    audit.add_argument("--library", required=True)
    audit.add_argument("--today", default=None)
    audit.add_argument("--max-age-days", type=int, default=365)
    audit.add_argument("--online", action="store_true", help="enable bounded live URL checks")
    audit.add_argument("--timeout", type=float, default=5.0)
    audit.add_argument("--retries", type=int, default=1)
    audit.add_argument("--max-urls", type=int, default=25)
    audit.add_argument("--out", help="write the Markdown report here")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--approve", action="store_true")
    audit.add_argument("--json", action="store_true")

    dedupe = library_sub.add_parser("dedupe", help="find and (with approval) record duplicates")
    dedupe.add_argument("--library", required=True)
    dedupe.add_argument("--apply", action="store_true", help="rewrite records instead of a dry run")
    dedupe.add_argument("--approve", action="store_true", help="required with --apply")
    dedupe.add_argument("--json", action="store_true")

    pack = library_sub.add_parser("package", help="build a topic reference pack")
    pack.add_argument("--library", required=True)
    pack.add_argument("--name", required=True)
    pack.add_argument("--category", action="append", help="category slug (repeatable)")
    pack.add_argument("--limit", type=int, default=40)
    pack.add_argument("--out", required=True)
    pack.add_argument("--json", action="store_true")

    library.set_defaults(func=cmd_library)

    scaffold = sub.add_parser("scaffold", help="generate a working starter Skill Tool folder")
    scaffold.add_argument("--name", required=True, help="lowercase-hyphenated tool name")
    scaffold.add_argument("--out", required=True, help="parent directory for the new folder")
    scaffold.add_argument("--capability", help="one-line capability statement")
    scaffold.add_argument("--from-skill", help="derive the brief from this catalog skill")
    scaffold.add_argument("--catalog", default="asset")
    scaffold.add_argument("--catalog-ref", default="agent-skill-catalog/" + DEFAULT_CATALOG,
                          help="relative catalog reference stored in asset-config.json")
    scaffold.add_argument("--template", default=None)
    scaffold.add_argument("--generated", default=None)
    scaffold.add_argument("--no-tests", action="store_true", help="skip tools-selftest.py")
    scaffold.add_argument("--zip", help="also write this ZIP (folder at archive root)")
    scaffold.add_argument("--max-bytes", type=int, default=parser_mod.MAX_TEXT_BYTES)
    scaffold.add_argument("--force", action="store_true")
    scaffold.add_argument("--approve", action="store_true")
    scaffold.add_argument("--json", action="store_true")
    scaffold.set_defaults(func=cmd_scaffold)

    check = sub.add_parser("check", help="validate a Skill Tool folder or delivery ZIP")
    check.add_argument("--path", help="folder to validate")
    check.add_argument("--zip", help="ZIP to validate without extracting")
    check.add_argument("--max-files", type=int, default=packager_mod.MAX_FILES)
    check.add_argument("--no-references", action="store_true", help="skip internal reference resolution")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    selftest = sub.add_parser("selftest", help="run the shipped test suite")
    selftest.add_argument("--verbose", action="store_true")
    selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv=None) -> int:
    signal.signal(signal.SIGINT, _on_sigint)
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return EXIT_OK
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except _Interrupted:
        print("\ninterrupted: no partial outputs were kept", file=sys.stderr)
        return 130
    except CatalogError as exc:
        return _fail(str(exc), EXIT_INPUT, getattr(args, "json", False))
    except PermissionError as exc:
        return _fail(str(exc), EXIT_BLOCKED, getattr(args, "json", False))
    except FileNotFoundError as exc:
        return _fail(str(exc), EXIT_INPUT, getattr(args, "json", False))
    except (ValueError, OSError) as exc:
        return _fail("%s: %s" % (type(exc).__name__, exc), EXIT_INPUT, getattr(args, "json", False))


if __name__ == "__main__":
    sys.exit(main())
