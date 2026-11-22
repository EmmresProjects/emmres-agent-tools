#!/usr/bin/env python3
"""
tools-production-deployment.py — PRIMARY ENTRY POINT

Cloud Production Deployment Skill Tool: catalog-driven production readiness for
cloud / server / deployment work, built from the 30-category, 1212-skill
"Cloud, Server, and Production Deployment — Skill Catalog" (Edition 1.0, Nov 2026).

What it does (offline, no cloud calls, read-only against your infrastructure):
  capabilities       Show this tool's capability list and catalog statistics.
  categories         List the 30 catalog categories with skill counts.
  list               List skills inside one category (filter: --pattern).
  search             Full-text / regex search across all 1212 skill concepts.
  show               Show one skill concept in full (by id, name, or unique substring).
  plan               Generate a production deployment plan (Markdown or JSON) for a
                     single skill or for a whole category, with approval gates,
                     evidence checklist, rollback procedure and escalation path.
  assess             Score a deployment config JSON against the production acceptance
                     rubric (44 checks across governance/security/reliability/
                     operations/cost/privacy) — pass/warn/fail with remediation.
  decision-matrix    Weighted decision matrix (9 catalog criteria) for choosing a
                     production design; ranks options and records rejected ones.
  validate           Validate a config or options file without assessing/scoring.
  selftest           Offline integrity check of every bundled asset and engine path.
  version            Print tool and engine versions.

Dependencies: Python 3.8+ standard library only. (pypdf is needed solely by
tools-build-catalog.py when rebuilding asset-catalog.json from the PDF.)

Exit codes: 0 = success (or assess verdict pass/warn) · 1 = assess verdict FAIL,
or selftest failure · 2 = handled error (bad input, missing file, ...) · 130 = Ctrl+C.

All reference paths in output are relative to this Skill Tool folder.
Run with -h / --help on any subcommand for its exact flags.
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # never leave __pycache__ in the packaged Skill Tool folder

TOOL_DIR = Path(__file__).resolve().parent
TOOL_VERSION = "1.0.0"

# --- load the sibling engine module (hyphenated filename ⇒ importlib) --------
_engine_path = TOOL_DIR / "tools-deployment-engine.py"
_spec = importlib.util.spec_from_file_location("tools_deployment_engine", _engine_path)
if _spec is None or _spec.loader is None:
    print("error: cannot load engine: tools-deployment-engine.py missing or unreadable", file=sys.stderr)
    sys.exit(2)
engine = importlib.util.module_from_spec(_spec)
sys.modules["tools_deployment_engine"] = engine
_spec.loader.exec_module(engine)

EngineError = engine.EngineError

PROG = "tools-production-deployment.py"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def emit_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def write_or_print(text, output, force):
    if not output or output == "-":
        print(text)
        return None
    if force:
        engine.atomic_write_text_force(output, text)
    else:
        engine.atomic_write_text(output, text)
    return output


def fail(exc, fmt):
    err = exc.to_dict() if isinstance(exc, EngineError) else {"kind": "unexpected", "message": str(exc), "details": {}}
    if fmt == "json":
        emit_json({"status": "error", "error": err})
    else:
        print(f"error[{err['kind']}]: {err['message']}", file=sys.stderr)
        details = err.get("details") or {}
        if details:
            if "candidates" in details:
                print("candidates:", file=sys.stderr)
                for c in details["candidates"]:
                    if isinstance(c, dict):
                        cat = c.get("category", "")
                        nm = c.get("name", "")
                        print(f"  - {c.get('id', c)}" + (f"  ({cat} — {nm})" if cat or nm else ""), file=sys.stderr)
                    else:
                        print(f"  - {c}", file=sys.stderr)
            for k, v in details.items():
                if k != "candidates":
                    print(f"{k}: {json.dumps(v, ensure_ascii=False)}", file=sys.stderr)
    return 2


def fmt_of(args):
    return getattr(args, "format", "text") or "text"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_capabilities(args, catalog):
    stats = catalog.stats()
    commands = [
        ("capabilities", "show this capability list and catalog statistics"),
        ("categories", "list the catalog categories with skill counts"),
        ("list --category X [--pattern P] [--format text|json]", "enumerate skills in a category"),
        ("search QUERY [--category X] [--pattern P] [--regex] [--limit N]", "search all skill concepts"),
        ("show --skill NAME|ID [--format text|json]", "full detail for one skill concept"),
        ("plan (--skill NAME|ID | --category X) [--provider P] [--environment E]", "generate a production deployment plan (md|json)"),
        ("assess --config FILE [--rubric FILE] [--strict] [--format text|json|md]", "score a deployment config vs the 44-check acceptance rubric"),
        ("decision-matrix --options FILE [--criteria FILE] [--format text|json|md]", "weighted decision matrix; records rejected options"),
        ("validate --kind config|options --file FILE", "validate input files without scoring"),
        ("selftest", "offline integrity check of assets and engine"),
        ("version", "print tool/engine versions"),
    ]
    if args.format == "json":
        emit_json({"tool": "Cloud Production Deployment", "version": TOOL_VERSION,
                   "engineVersion": engine.ENGINE_VERSION, "statistics": stats,
                   "commands": [{"command": c, "description": d} for c, d in commands],
                   "patterns": list(stats["patternCounts"])})
        return 0
    print("Cloud Production Deployment — Skill Tool")
    print(f"Version {TOOL_VERSION} (engine {engine.ENGINE_VERSION})")
    print(f"Catalog: {stats['catalog']} — Edition {stats['edition']} ({stats['published']})")
    print(f"Scope: {stats['categoryCount']} categories · {stats['skillCount']} skill concepts\n")
    print("Capability list:")
    for c, d in commands:
        print(f"  {c:<72} {d}")
    print("\nSkill patterns (archetypes):")
    for p, n in sorted(stats["patternCounts"].items(), key=lambda kv: -kv[1]):
        print(f"  {p:<20} {n:>4} skills")
    print("\nDrill in with: list --category 1   ·   search 'rollback'   ·   show --skill 'Cloud Region Selection'")
    print("Then:          plan --skill 'Cloud Region Selection' --provider aws --environment production")
    print("Verify with:   assess --config asset-example-deployment.json")
    return 0


def cmd_version(args, catalog):
    if args.format == "json":
        emit_json({"tool": TOOL_VERSION, "engine": engine.ENGINE_VERSION})
    else:
        print(f"{PROG} {TOOL_VERSION} (engine {engine.ENGINE_VERSION})")
    return 0


def cmd_categories(args, catalog):
    cats = catalog.list_categories()
    if args.format == "json":
        emit_json({"status": "ok", "categories": cats, "count": len(cats)})
        return 0
    print(f"{'#':>2}  {'skills':>6}  category")
    for c in cats:
        print(f"{c['number']:>2}  {c['skillCount']:>6}  {c['name']}")
    print(f"\n30 categories · {sum(c['skillCount'] for c in cats)} skill concepts")
    return 0


def cmd_list(args, catalog):
    cat, skills = catalog.list_skills(args.category, pattern=args.pattern)
    if args.format == "json":
        emit_json({"status": "ok", "category": {"number": cat["number"], "name": cat["name"]},
                   "pattern": args.pattern, "count": len(skills), "skills": skills})
        return 0
    print(f"Category {cat['number']} — {cat['name']}  ({len(skills)} skill concepts)")
    for s in skills:
        print(f"  {s['number']:>2}. {s['name']}")
        print(f"      id: {s['id']}  ·  pattern: {s['pattern']}")
    return 0


def cmd_search(args, catalog):
    result = catalog.search(args.query, category_query=args.category, pattern=args.pattern,
                            limit=args.limit, use_regex=args.regex)
    if args.format == "json":
        emit_json({"status": "ok", **result})
        return 0
    print(f"search '{result['query']}'{' (regex)' if result['regex'] else ''}: "
          f"{result['total']} hit(s), showing {result['returned']}")
    for s in result["results"]:
        print(f"  [{s['categoryNumber']:>2}] {s['name']}")
        print(f"      {s['id']}  ·  pattern: {s['pattern']}")
        print(f"      {s['description']}")
    return 0


def cmd_show(args, catalog):
    s = catalog.find_skill(args.skill)
    legend = catalog.data.get("patternLegend", {})
    if args.format == "json":
        emit_json({"status": "ok", "skill": s, "patternSummary": legend.get(s["pattern"], "")})
        return 0
    print(f"{s['name']}")
    print(f"  id:          {s['id']}")
    print(f"  category:    {s['categoryNumber']}. {s['category']}  ({s['categoryId']})")
    print(f"  number:      {s['number']}")
    print(f"  pattern:     {s['pattern']} — {legend.get(s['pattern'], '')}")
    print(f"\n  Catalog description:\n    {s['description']}")
    print("\n  Next steps:")
    print(f"    plan --skill '{s['name']}' --provider aws --environment production")
    print("    assess --config <deployment-config.json>   # verify against the acceptance rubric")
    return 0


def cmd_plan(args, catalog):
    plan = engine.build_plan(catalog, skill_query=args.skill, category_query=args.category,
                             provider=args.provider, environment=args.environment,
                             rpo_minutes=args.rpo_minutes, rto_minutes=args.rto_minutes)
    if args.format == "json":
        text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    else:
        if args.template:
            template = engine.read_text_capped(args.template, 256 * 1024, "plan template")
        else:
            template = engine.read_text_capped(engine.PLAN_TEMPLATE_ASSET, engine.MAX_ASSET_BYTES, "plan template asset")
        text = engine.render_plan_markdown(plan, template)
    written = write_or_print(text, args.output, args.force)
    if written and args.format != "json":
        print(f"plan written to {written}", file=sys.stderr)
    if args.format == "json" and written:
        # keep stdout parseable when writing to a file in json mode
        emit_json({"status": "ok", "output": written, "planType": plan["planType"],
                   "title": plan["title"]})
    return 0


def _verdict_marker(verdict):
    return {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(verdict, verdict.upper())


def cmd_assess(args, catalog):
    rubric = engine.load_rubric(args.rubric or engine.RUBRIC_ASSET)
    cfg = engine.load_config(args.config, rubric)
    report = engine.assess_config(cfg, rubric, strict=args.strict)
    out_fmt = args.format
    if out_fmt == "json":
        text = json.dumps({"status": "ok", **report}, ensure_ascii=False, indent=2) + "\n"
    elif out_fmt == "md":
        text = _render_assessment_md(report)
    else:
        text = _render_assessment_text(report)
    written = write_or_print(text, args.output, args.force)
    if out_fmt == "json" and written:
        emit_json({"status": "ok", "output": written, "verdict": report["verdict"], "score": report["score"]})
    return 1 if report["verdict"] == "fail" else 0


def _render_assessment_text(report):
    ident = report["service"]
    lines = [
        "Production Acceptance Assessment",
        f"  service:      {ident.get('service', '?')}   environment: {ident.get('environment', '?')}",
        f"  provider:     {ident.get('provider', '?')}   region: {ident.get('region', '?')}   exposure: {ident.get('exposure', '?')}",
        f"  rubric:       {report['rubricId']} v{report['rubricVersion']}",
        "",
        f"  SCORE: {report['score']:.1f}/100   VERDICT: {_verdict_marker(report['verdict'])}   RISK: {report['risk'].upper()}",
        f"  checks: {report['checkedChecks']} run · {report['skippedChecks']} skipped (not applicable) · "
        f"{report['failures']} fail · {report['warnings']} warn",
        "",
        "  Domains:",
    ]
    for d in report["domains"]:
        score = " n/a" if d["score"] is None else f"{d['score']:>5.1f}"
        lines.append(f"    {d['domain']:<12} {score}   fails={d['fails']} warns={d['warns']}")
    fails = [c for c in report["checks"] if c["result"] == "fail"]
    warns = [c for c in report["checks"] if c["result"] == "warn"]
    if fails:
        lines.append("\n  FAILURES (fix before release):")
        for c in fails:
            lines.append(f"    [{c['severity']:>8}] {c['id']} (w={c['weight']}) — {c['title']}")
            lines.append(f"             reason: {c['reason']}")
            if c["remediation"]:
                lines.append(f"             fix:    {c['remediation']}")
    if warns:
        lines.append("\n  WARNINGS (sign off or fix):")
        for c in warns:
            lines.append(f"    [{c['severity']:>8}] {c['id']} (w={c['weight']}) — {c['title']}")
            lines.append(f"             reason: {c['reason']}")
            if c["remediation"]:
                lines.append(f"             fix:    {c['remediation']}")
    if not fails and not warns:
        lines.append("\n  All applicable checks pass.")
    lines.append("\n  Retest procedure:")
    for step in report["retestProcedure"]:
        lines.append(f"    {step}")
    return "\n".join(lines)


def _render_assessment_md(report):
    ident = report["service"]
    lines = [
        f"# Production Acceptance Assessment — {ident.get('service', 'service')}",
        "",
        f"- **Environment:** {ident.get('environment', '?')}  ·  **Provider:** {ident.get('provider', '?')}  ·  "
        f"**Region:** {ident.get('region', '?')}  ·  **Exposure:** {ident.get('exposure', '?')}",
        f"- **Rubric:** `{report['rubricId']}` v{report['rubricVersion']}",
        "",
        f"## Result: {_verdict_marker(report['verdict'])} — score {report['score']:.1f}/100 — risk {report['risk'].upper()}",
        "",
        f"Checks run: {report['checkedChecks']} · skipped (n/a): {report['skippedChecks']} · "
        f"failures: {report['failures']} · warnings: {report['warnings']}",
        "",
        "## Domains",
        "",
        "| Domain | Score | Fails | Warns |",
        "|---|---|---|---|",
    ]
    for d in report["domains"]:
        score = "n/a" if d["score"] is None else f"{d['score']:.1f}"
        lines.append(f"| {d['domain']} | {score} | {d['fails']} | {d['warns']} |")
    lines += ["", "## Findings", ""]
    bad = [c for c in report["checks"] if c["result"] in ("fail", "warn")]
    if not bad:
        lines.append("All applicable checks pass.")
    for c in bad:
        lines.append(f"### [{c['result'].upper()}] {c['id']} (severity {c['severity']}, weight {c['weight']})")
        lines.append(f"- {c['title']}")
        lines.append(f"- Reason: {c['reason']}")
        if c["remediation"]:
            lines.append(f"- Remediation: {c['remediation']}")
        if c["evidence"]:
            lines.append(f"- Evidence to attach: {c['evidence']}")
        lines.append("")
    lines += ["## Retest Procedure", ""]
    lines += [f"{i + 1}. {step}" for i, step in enumerate(report["retestProcedure"])]
    return "\n".join(lines) + "\n"


def cmd_decision_matrix(args, catalog):
    criteria = engine.load_criteria(args.criteria or engine.CRITERIA_ASSET)
    options = engine.load_options(args.options)
    result = engine.evaluate_decision(options, criteria, memo=args.memo)
    out_fmt = args.format
    if out_fmt == "json":
        text = json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2) + "\n"
    elif out_fmt == "md":
        text = _render_decision_md(result, criteria)
    else:
        text = _render_decision_text(result, criteria)
    written = write_or_print(text, args.output, args.force)
    if out_fmt == "json" and written:
        emit_json({"status": "ok", "output": written, "winner": result["winner"]["name"],
                   "score": result["winner"]["totalScore"]})
    return 0


def _render_decision_text(result, criteria):
    lines = ["Weighted Decision Matrix — production design selection"]
    if result["context"]:
        lines.append(f"context: {result['context']}")
    lines.append(f"criteria: {result['criteriaId']} v{result['criteriaVersion']} (weights sum {result['weightSum']:.0f})\n")
    lines.append(f"{'rank':<5}{'score/100':<10}option")
    for i, row in enumerate(result["ranking"], 1):
        marker = "  <- winner" if i == 1 else ""
        lines.append(f"{i:<5}{row['totalScore']:<10.2f}{row['name']}{marker}")
    lines.append(f"\nWinner: {result['winner']['name']} ({result['winner']['totalScore']:.2f}/100)")
    if result["rejected"]:
        lines.append("\nRejected options (recorded):")
        for r in result["rejected"]:
            lines.append(f"  - {r['name']}: {r['totalScore']:.2f}/100 (gap {r['gapToWinner']:.2f})")
            weakest = ", ".join(f"{w['name']}={w['rawScore']:.0f} (-{w['pointsLost']:.1f} pts)" for w in r["weakestCriteria"])
            lines.append(f"      weakest criteria: {weakest}")
            if r["notes"]:
                lines.append(f"      notes: {r['notes']}")
    if result["memo"]:
        lines.append(f"\nmemo: {result['memo']}")
    return "\n".join(lines)


def _render_decision_md(result, criteria):
    lines = [
        "# Weighted Decision Matrix — Production Design Selection", "",
    ]
    if result["context"]:
        lines += [f"**Context:** {result['context']}", ""]
    lines += [
        f"Criteria: `{result['criteriaId']}` v{result['criteriaVersion']} (weights sum {result['weightSum']:.0f})",
        "",
        "## Ranking", "",
        "| Rank | Option | Score /100 |", "|---|---|---|",
    ]
    for i, row in enumerate(result["ranking"], 1):
        medal = " (winner)" if i == 1 else ""
        lines.append(f"| {i} | {row['name']}{medal} | {row['totalScore']:.2f} |")
    lines += ["", f"**Winner: {result['winner']['name']}** — {result['winner']['totalScore']:.2f}/100", "",
              "## Score Breakdown (winner)", "",
              "| Criterion | Weight | Direction | Raw (1-5) | Weighted pts |", "|---|---|---|---|---|"]
    for b in result["ranking"][0]["breakdown"]:
        lines.append(f"| {b['name']} | {b['weight']} | {b['direction']} | {b['rawScore']:.0f} | {b['weighted']:.2f} |")
    if result["rejected"]:
        lines += ["", "## Rejected Options (recorded)", ""]
        for r in result["rejected"]:
            lines.append(f"### {r['name']} — {r['totalScore']:.2f}/100 (gap {r['gapToWinner']:.2f})")
            for w in r["weakestCriteria"]:
                lines.append(f"- Weakest: {w['name']} — raw score {w['rawScore']:.0f} (lost {w['pointsLost']:.2f} of {w['weight']} pts)")
            if r["notes"]:
                lines.append(f"- Notes: {r['notes']}")
            lines.append("")
    if result["memo"]:
        lines += ["## Decision Memo", "", result["memo"], ""]
    return "\n".join(lines) + "\n"


def cmd_validate(args, catalog):
    if args.kind == "config":
        rubric = engine.load_rubric(args.rubric or engine.RUBRIC_ASSET)
        cfg = engine.load_config(args.file, rubric)
        ident = {k: cfg.get(k) for k in ("service", "environment", "provider", "region")}
        missing = [k for k in ("service", "environment", "provider") if not cfg.get(k)]
        summary = {"kind": "config", "file": str(args.file), "topLevelKeys": sorted(cfg),
                   "identifiers": ident, "missingIdentifiers": missing,
                   "checksInRubric": len(rubric["checks"])}
        ok = not missing
        if args.format == "json":
            emit_json({"status": "ok" if ok else "warn", "validation": summary})
        else:
            print(f"config OK: {args.file}")
            print(f"  identifiers: {json.dumps(ident, ensure_ascii=False)}")
            print(f"  top-level keys ({len(cfg)}): {', '.join(sorted(cfg))}")
            print(f"  rubric checks available: {len(rubric['checks'])}")
            if missing:
                print(f"  WARN missing identifiers (not fatal): {', '.join(missing)}")
        return 0 if ok else 2
    # options
    criteria = engine.load_criteria(args.criteria or engine.CRITERIA_ASSET)
    options = engine.load_options(args.file)
    result = engine.evaluate_decision(options, criteria)
    if args.format == "json":
        emit_json({"status": "ok", "validation": {
            "kind": "options", "file": str(args.file),
            "options": [o["name"] for o in result["ranking"]],
            "criteria": [c["id"] for c in criteria["criteria"]],
            "weightsSum": criteria["weightSum"]}})
    else:
        print(f"options OK: {args.file}")
        print(f"  options ({len(result['ranking'])}): {', '.join(o['name'] for o in result['ranking'])}")
        print(f"  criteria ({len(criteria['criteria'])}): {', '.join(c['id'] for c in criteria['criteria'])}")
        print(f"  weights sum: {criteria['weightSum']:.0f}")
    return 0


def cmd_selftest(args, catalog):
    checks = []

    def record(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # 1. catalog integrity
    try:
        stats = catalog.stats()
        ok = stats["categoryCount"] == len(catalog.list_categories()) and stats["skillCount"] > 0
        record("catalog loads and is consistent", ok,
               f"{stats['categoryCount']} categories, {stats['skillCount']} skills")
        record("every skill has a known pattern",
               all(s["pattern"] in catalog.data.get("patternLegend", {}) for s in catalog._skills))
        hits = catalog.search("rollback", limit=5)
        record("search returns results", hits["total"] >= 1, f"{hits['total']} hits for 'rollback'")
        record("find_skill resolves by name", catalog.find_skill("Cloud Region Selection")["name"] == "Cloud Region Selection")
    except EngineError as exc:
        record("catalog loads and is consistent", False, exc.message)

    # 2. rubric integrity
    try:
        rubric = engine.load_rubric(engine.RUBRIC_ASSET)
        domains = sorted({c["domain"] for c in rubric["checks"]})
        record("rubric loads (44 checks)", len(rubric["checks"]) >= 40,
               f"{len(rubric['checks'])} checks across domains: {', '.join(domains)}")
        total_w = sum(c["weight"] for c in rubric["checks"])
        record("rubric weights positive", total_w > 0, f"total weight {total_w}")
    except EngineError as exc:
        record("rubric loads", False, exc.message)

    # 3. criteria integrity
    try:
        criteria = engine.load_criteria(engine.CRITERIA_ASSET)
        record("decision criteria load, weights sum 100", abs(criteria["weightSum"] - 100.0) < 0.5,
               f"{len(criteria['criteria'])} criteria")
    except EngineError as exc:
        record("decision criteria load", False, exc.message)

    # 4. example config passes the rubric
    try:
        example = TOOL_DIR / "asset-example-deployment.json"
        cfg = engine.load_config(example, rubric)
        report = engine.assess_config(cfg, rubric)
        record("example config assesses to verdict pass", report["verdict"] == "pass",
               f"score {report['score']}, risk {report['risk']}")
    except Exception as exc:  # noqa: BLE001 - selftest must report, not crash
        record("example config assesses to verdict pass", False, str(exc))

    # 5. plan rendering end-to-end
    try:
        plan = engine.build_plan(catalog, skill_query="Cloud Region Selection",
                                 provider="aws", environment="production")
        template = engine.read_text_capped(engine.PLAN_TEMPLATE_ASSET, engine.MAX_ASSET_BYTES, "plan template")
        rendered = engine.render_plan_markdown(plan, template)
        record("plan renders without unfilled placeholders", "{{" not in rendered and len(rendered) > 500,
               f"{len(rendered)} bytes of markdown")
    except Exception as exc:  # noqa: BLE001
        record("plan renders without unfilled placeholders", False, str(exc))

    # 6. decision matrix end-to-end (deterministic, in-memory)
    try:
        criteria = engine.load_criteria(engine.CRITERIA_ASSET)
        ids = [c["id"] for c in criteria["criteria"]]
        strong = {cid: 5 for cid in ids}
        weak = {cid: 2 for cid in ids}
        result = engine.evaluate_decision(
            {"context": "selftest", "options": [{"name": "strong", "scores": strong}, {"name": "weak", "scores": weak}]},
            criteria)
        record("decision matrix ranks deterministically",
               result["winner"]["name"] == "strong" and len(result["rejected"]) == 1,
               f"winner {result['winner']['name']} at {result['winner']['totalScore']}")
    except Exception as exc:  # noqa: BLE001
        record("decision matrix ranks deterministically", False, str(exc))

    failed = [c for c in checks if not c[1]]
    if args.format == "json":
        emit_json({"status": "ok" if not failed else "fail",
                   "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]})
    else:
        for name, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        print(f"\nselftest: {len(checks) - len(failed)}/{len(checks)} checks passed")
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Catalog-driven production readiness for cloud/server/deployment work. "
                    "Offline; no cloud API calls; read-only against infrastructure.",
        epilog="Examples:\n"
               "  python3 tools-production-deployment.py capabilities\n"
               "  python3 tools-production-deployment.py search 'rollback' --limit 10\n"
               "  python3 tools-production-deployment.py show --skill 'Cloud Region Selection'\n"
               "  python3 tools-production-deployment.py plan --skill 'Cloud Region Selection' --provider aws\n"
               "  python3 tools-production-deployment.py assess --config asset-example-deployment.json\n"
               "  python3 tools-production-deployment.py decision-matrix --options options.json\n"
               "\nAll paths may be absolute or relative to your working directory. Output paths are\n"
               "written atomically and existing files are never overwritten unless --force is given.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--catalog", default=None, metavar="PATH",
                   help="override the bundled asset-catalog.json (advanced; default: bundled asset)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, fn, help_text, fmt_choices=("text", "json")):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.add_argument("--format", choices=list(fmt_choices), default=fmt_choices[0])
        sp.set_defaults(func=fn)
        return sp

    add("capabilities", cmd_capabilities, "show this capability list and catalog statistics")
    add("version", cmd_version, "print tool/engine versions")
    add("categories", cmd_categories, "list the 30 catalog categories with skill counts")

    sp = add("list", cmd_list, "enumerate skills in a category")
    sp.add_argument("--category", required=True, help="category number (1-30), id, slug, or name substring")
    sp.add_argument("--pattern", choices=["focused-capability", "provision-from-code", "runbook-definition",
                                          "decision-matrix", "evaluation-gate", "operate-service"],
                    default=None, help="filter by skill archetype")

    sp = add("search", cmd_search, "search all skill concepts by substring or regex")
    sp.add_argument("query", help="search string (or regex with --regex)")
    sp.add_argument("--category", default=None, help="restrict to one category")
    sp.add_argument("--pattern", default=None,
                    choices=["focused-capability", "provision-from-code", "runbook-definition",
                             "decision-matrix", "evaluation-gate", "operate-service"])
    sp.add_argument("--regex", action="store_true", help="treat QUERY as a regular expression")
    sp.add_argument("--limit", type=int, default=25, help="max results (1-200, default 25)")

    sp = add("show", cmd_show, "full detail for one skill concept")
    sp.add_argument("--skill", required=True, help="skill id, exact name, or unique substring")

    sp = add("plan", cmd_plan, "generate a production deployment plan (markdown or json)", ("md", "json"))
    sp.set_defaults(format="md")
    sp.add_argument("--skill", default=None, help="skill id, exact name, or unique substring")
    sp.add_argument("--category", default=None, help="category number/id/slug/name — plans all skills in it")
    sp.add_argument("--provider", default="aws", choices=engine.PROVIDERS)
    sp.add_argument("--environment", default="production", choices=engine.ENVIRONMENTS)
    sp.add_argument("--rpo-minutes", type=int, default=15, help="recovery point objective (default 15)")
    sp.add_argument("--rto-minutes", type=int, default=60, help="recovery time objective (default 60)")
    sp.add_argument("--template", default=None, metavar="PATH", help="override asset-plan-template.md")
    sp.add_argument("--output", default=None, metavar="PATH", help="write to PATH (default: stdout); '-' = stdout")
    sp.add_argument("--force", action="store_true", help="allow overwriting an existing --output file")

    sp = add("assess", cmd_assess, "score a deployment config against the acceptance rubric", ("text", "json", "md"))
    sp.add_argument("--config", required=True, metavar="PATH", help="deployment config JSON (see knowledge-readme.md)")
    sp.add_argument("--rubric", default=None, metavar="PATH", help="override asset-assessment-rubric.json")
    sp.add_argument("--strict", action="store_true", help="treat any warn as a release-blocking fail")
    sp.add_argument("--output", default=None, metavar="PATH", help="write report to PATH (default: stdout)")
    sp.add_argument("--force", action="store_true", help="allow overwriting an existing --output file")

    sp = add("decision-matrix", cmd_decision_matrix,
             "weighted decision matrix for production design selection", ("text", "json", "md"))
    sp.add_argument("--options", required=True, metavar="PATH", help="options JSON (see knowledge-readme.md)")
    sp.add_argument("--criteria", default=None, metavar="PATH", help="override asset-decision-criteria.json")
    sp.add_argument("--memo", default="", help="free-text decision memo appended to the output")
    sp.add_argument("--output", default=None, metavar="PATH", help="write report to PATH (default: stdout)")
    sp.add_argument("--force", action="store_true", help="allow overwriting an existing --output file")

    sp = add("validate", cmd_validate, "validate a config or options file without scoring")
    sp.add_argument("--kind", required=True, choices=["config", "options"])
    sp.add_argument("--file", required=True, metavar="PATH")
    sp.add_argument("--rubric", default=None, metavar="PATH")
    sp.add_argument("--criteria", default=None, metavar="PATH")

    add("selftest", cmd_selftest, "offline integrity check of assets and engine")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        catalog = engine.Catalog.load(args.catalog or engine.CATALOG_ASSET)
    except EngineError as exc:
        return fail(exc, fmt_of(args))
    try:
        return args.func(args, catalog)
    except EngineError as exc:
        return fail(exc, fmt_of(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # stdout closed by a downstream pipe (e.g. `| head`); exit quietly
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
