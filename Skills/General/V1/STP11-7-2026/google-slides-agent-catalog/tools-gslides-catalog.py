#!/usr/bin/env python3
"""Primary entry point for the google-slides-agent-catalog Skill Tool.

Turns the Google Slides agent capability catalog into runnable work: ingest and normalise
catalog sources, list and inspect capabilities, plan a Slides task with risk gates,
generate implementation specifications, score coverage, and verify operator approvals.

Run ``python3 tools-gslides-catalog.py help`` for the full command reference.

Standard library only. Exit codes:
    0  success
    2  usage, input or configuration error
    3  plan produced but an approval gate is open and --fail-on-approval was used
    4  no catalog capability matched the request
    5  a produced document failed schema validation
    6  approval gate denied
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_NAME = "tools-gslides-catalog.py"
TOOL_VERSION = "1.0.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_APPROVAL_OPEN = 3
EXIT_NO_MATCH = 4
EXIT_SCHEMA = 5
EXIT_DENIED = 6


def _load(name, filename):
    """Load a sibling prefixed module by file path (names contain hyphens by design)."""
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        raise SystemExit(f"Required module missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("gslides_catalog_core", "tools-catalog-core.py")
engine = _load("gslides_plan_engine", "tools-plan-engine.py")

CAPABILITIES = [
    {
        "command": "help",
        "summary": "Show command reference, inputs, exit codes and safety rules.",
        "needs_approval": False,
    },
    {
        "command": "capabilities",
        "summary": "List every command this tool implements, with arguments (capability list).",
        "needs_approval": False,
    },
    {
        "command": "categories",
        "summary": "List catalog categories with purpose, skill counts and configured phase.",
        "needs_approval": False,
    },
    {
        "command": "list",
        "summary": "List catalog skills, optionally filtered by category or search text.",
        "needs_approval": False,
    },
    {
        "command": "describe",
        "summary": "Describe one category or one skill: risk tier, phase, gates, neighbours.",
        "needs_approval": False,
    },
    {
        "command": "plan",
        "summary": "Match a Slides request to capabilities and produce an ordered, gated execution plan.",
        "needs_approval": False,
    },
    {
        "command": "validate",
        "summary": "Validate a plan document against the packaged plan schema and the risk policy.",
        "needs_approval": False,
    },
    {
        "command": "spec",
        "summary": "Render a 12-section implementation specification for one capability.",
        "needs_approval": False,
    },
    {
        "command": "coverage",
        "summary": "Score implemented-vs-catalog coverage and list gaps from a manifest.",
        "needs_approval": False,
    },
    {
        "command": "ingest",
        "summary": "Parse a plain-text, Markdown, HTML or PDF catalog source into a catalog asset.",
        "needs_approval": False,
    },
    {
        "command": "approve-request",
        "summary": "Emit an unsigned approval request bound to a plan fingerprint for an operator to sign.",
        "needs_approval": False,
    },
    {
        "command": "approve-check",
        "summary": "Verify a signed approval record before any gated step may execute.",
        "needs_approval": False,
    },
    {
        "command": "doctor",
        "summary": "Check configuration, packaged assets, template and catalog integrity.",
        "needs_approval": False,
    },
]

HELP_TEXT = """google-slides-agent-catalog — Skill Tool

Turns a Google Slides agent capability catalog into runnable plans, specifications,
coverage reports and approval gates. It never calls Google services itself; live API
calls belong to tools-slides-api.py, which requires its own approval token.

USAGE
  python3 tools-gslides-catalog.py <command> [options]

COMMANDS
  help                 Show this reference.
  capabilities         Machine-readable capability list of this tool.
  categories           List catalog categories (id, purpose, skills, phase).
  list                 List skills: --category <sel> --search <text> --limit <n>
  describe             Inspect one entry: --skill <id> | --category <sel>
  plan                 Plan work: --request <text|-> | --request-file <path>
                       options: --category <sel> (repeatable) --max-matches <n>
                                --out <path> --json --fail-on-approval
  validate             Validate a plan: --plan <path> [--json]
  spec                 Spec for one skill: --skill <id> [--request <text>]
                       [--template <path>] [--out <path>]
  coverage             Coverage report: --manifest <path> [--min-coverage <0-1>]
                       [--out <path>]
  ingest               Parse a source: --source <path> [--out <path>] [--validate]
  approve-request      Unsigned record: --plan <path> [--out <path>]
  approve-check        Verify record: --plan <path> --record <path> [--json]
  doctor               Environment and asset integrity check.

GLOBAL OPTIONS
  --config <path>      Configuration file (default: asset-default-config.json beside this file)
  --json               Emit a structured JSON envelope instead of text
  --version            Print version and exit

EXIT CODES
  0 success   3 plan produced with open approval gates (--fail-on-approval)
  2 usage/input/configuration error   4 no capability matched the request
  5 document failed schema validation  6 approval gate denied

SAFETY
  * Plans are inert: no Slides, Drive or network call is performed.
  * Publishing, sharing, ownership, credential, deletion, export and distribution
    capabilities are classified as gated tiers and need a signed approval record.
  * Approval records are bound to a SHA-256 fingerprint of the exact plan; editing the
    plan after approval invalidates the record.
  * No secrets are stored in this package; tokens are read from the environment only.
"""


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

class Session:
    """Carries configuration and output preferences through a command run."""

    def __init__(self, args):
        self.args = args
        self.json = bool(getattr(args, "json", False))
        self.config = core.load_config(getattr(args, "config", None))
        self.limits = core.limits(self.config)

    def output_path(self, requested, default_name=None):
        """Resolve an output path from --out, the configured output_dir, or None."""
        if requested:
            return core.resolve_path(requested)
        configured = (self.config.get("paths") or {}).get("output_dir")
        if configured and default_name:
            base = core.resolve_path(configured, base=self.config["_config_dir"])
            return os.path.join(base, default_name)
        return None


def finish(payload, lines, exit_code=EXIT_OK):
    payload = dict(payload)
    payload.setdefault("ok", exit_code == EXIT_OK)
    payload.setdefault("exit_code", exit_code)
    payload["lines"] = lines
    return payload, exit_code


def print_result(payload, as_json, stream=None):
    stream = stream or sys.stdout
    if as_json:
        body = {key: value for key, value in payload.items() if key != "lines"}
        json.dump(body, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        return
    for line in payload.get("lines", []):
        stream.write(str(line) + "\n")


def print_error(error, as_json):
    payload = error.as_dict()
    payload["exit_code"] = EXIT_USAGE
    if as_json:
        json.dump(payload, sys.stderr, indent=2, ensure_ascii=False)
        sys.stderr.write("\n")
    else:
        sys.stderr.write(f"ERROR [{error.code}] {error.message}\n")
        if error.hint:
            sys.stderr.write(f"  hint: {error.hint}\n")
        for issue in (error.details or {}).get("issues", [])[:5]:
            sys.stderr.write(f"  issue: {issue}\n")


def _catalog(session, path=None):
    return core.load_catalog_asset(session.config, path)


def _policy(session, path=None):
    return engine.load_policy(session.config, path)


def _read_request(session, args):
    text = getattr(args, "request", None)
    request_file = getattr(args, "request_file", None)
    if text and request_file:
        raise core.SkillToolError(
            "conflicting-input",
            "Use either --request or --request-file, not both.",
        )
    if request_file:
        content, _meta = core.read_source_text(request_file, session.limits["max_source_bytes"])
        return content.strip()
    if text == "-":
        if sys.stdin is None or sys.stdin.isatty():
            raise core.SkillToolError(
                "stdin-empty",
                "--request - was used but no piped input is available.",
                hint="Pipe text in, for example: printf 'crop images' | python3 tools-gslides-catalog.py plan --request -",
            )
        data = sys.stdin.read(session.limits["max_source_bytes"])
        return data.strip()
    return (text or "").strip()


def _plan_fingerprint(plan):
    """Stable SHA-256 over the gated decision surface of a plan."""
    surface = {
        "plan_version": plan.get("plan_version"),
        "request": {key: plan.get("request", {}).get(key) for key in ("text", "category_filter", "mode")},
        "matches": [
            {
                "skill_id": match.get("skill_id"),
                "category_id": match.get("category_id"),
                "risk": {
                    "tier": (match.get("risk") or {}).get("tier"),
                    "approval_required": (match.get("risk") or {}).get("approval_required"),
                },
            }
            for match in plan.get("matches", [])
        ],
        "approvals": [
            {"skill_id": approval.get("skill_id"), "tier": approval.get("tier")}
            for approval in plan.get("approvals", [])
        ],
    }
    blob = json.dumps(surface, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------

def cmd_help(session, args):
    return finish({"help": HELP_TEXT}, HELP_TEXT.rstrip("\n").split("\n"))


def cmd_capabilities(session, args):
    lines = [f"{TOOL_NAME} {TOOL_VERSION} — implemented capabilities", ""]
    for entry in CAPABILITIES:
        note = " (read-only)" if not entry["needs_approval"] else " (gated)"
        lines.append(f"  {entry['command']:<17} {entry['summary']}{note}")
    lines.append("")
    lines.append(f"Commands: {len(CAPABILITIES)}. Any command not listed is not implemented.")
    return finish({"tool": TOOL_NAME, "version": TOOL_VERSION, "capabilities": CAPABILITIES}, lines)


def cmd_categories(session, args):
    catalog = _catalog(session, getattr(args, "catalog", None))
    rows = []
    lines = [
        f"Catalog: {catalog.get('title', 'catalog')} — "
        f"{catalog['counts']['categories']} categories / {catalog['counts']['skills']} skills",
        "",
    ]
    for category in catalog["categories"]:
        phase = engine.phase_for_category(session.config, category["id"])
        rows.append(
            {
                "number": category["number"],
                "id": category["id"],
                "name": category["name"],
                "purpose": category["purpose"],
                "phase": phase["id"],
                "skills": category["skill_count"],
            }
        )
        lines.append(
            f"{category['number']:>2}. {category['id']:<48} phase={phase['id']:<14} "
            f"skills={category['skill_count']:<3} {category['name']}"
        )
        lines.append(f"    purpose: {category['purpose']}")
    return finish(
        {
            "catalog": catalog.get("source_name"),
            "counts": catalog["counts"],
            "categories": rows,
        },
        lines,
    )


def cmd_list(session, args):
    catalog = _catalog(session, getattr(args, "catalog", None))
    category_ids = None
    if getattr(args, "category", None):
        catalog, category_ids = engine.resolve_category_ids(session.config, args.category)
    search = (getattr(args, "search", None) or "").strip().lower()
    limit = int(getattr(args, "limit", None) or session.limits["max_matches"])
    rows = []
    for category in catalog["categories"]:
        if category_ids and category["id"] not in category_ids:
            continue
        for skill in category["skills"]:
            haystack = f"{skill['id']} {skill['name']} {skill['description']}".lower()
            if search and search not in haystack:
                continue
            phase = engine.phase_for_category(session.config, category["id"])
            rows.append(
                {
                    "skill_id": skill["id"],
                    "skill_name": skill["name"],
                    "description": skill["description"],
                    "category_id": category["id"],
                    "phase": phase["id"],
                }
            )
    total = len(rows)
    rows = rows[:limit]
    lines = [f"Skills ({total} match{'' if total == 1 else 'es'}; showing {len(rows)}):"]
    for row in rows:
        lines.append(f"  {row['skill_id']:<52} {row['category_id']:<44} {row['skill_name']}")
    if total > len(rows):
        lines.append(f"  ... {total - len(rows)} more (raise --limit or use --json)")
    if not rows:
        lines.append("  (none)")
        lines.append("  hint: run 'categories' to see valid category ids, or clear --search.")
    return finish(
        {"count": len(rows), "total_matching": total, "skills": rows},
        lines,
        EXIT_OK if rows else EXIT_NO_MATCH,
    )


def cmd_describe(session, args):
    catalog = _catalog(session, getattr(args, "catalog", None))
    policy = _policy(session, getattr(args, "policy", None))
    index = core.index_catalog(catalog)
    skill_id = getattr(args, "skill", None)
    category_sel = getattr(args, "category", None)
    if not skill_id and not category_sel:
        raise core.SkillToolError(
            "missing-selector",
            "describe needs --skill <id> or --category <selector>.",
            hint="Run 'list' to find skill ids.",
        )
    if skill_id:
        skill = index["skills"].get(skill_id)
        if skill is None:
            import difflib

            close = difflib.get_close_matches(skill_id, list(index["skills"]), n=3, cutoff=0.5)
            raise core.SkillToolError(
                "unknown-skill",
                f"No catalog skill with id {skill_id!r}.",
                hint=("Did you mean: " + ", ".join(close)) if close else "Run 'list' to read exact ids.",
            )
        category = index["categories"][skill["category_id"]]
        phase = engine.phase_for_category(session.config, category["id"])
        risk = engine.classify_risk(f"{skill['name']} {skill['description']}", policy)
        gates = [
            gate for gate in (session.config.get("gates") or [])
            if category["id"] in (gate.get("applies_to_categories") or [])
        ]
        neighbours = [other["id"] for other in category["skills"] if other["id"] != skill["id"]][:4]
        payload = {
            "skill_id": skill["id"],
            "skill_name": skill["name"],
            "description": skill["description"],
            "category": {"id": category["id"], "name": category["name"], "purpose": category["purpose"]},
            "phase": phase,
            "risk": risk,
            "gates": gates,
            "neighbours": neighbours,
        }
        lines = [
            f"{skill['name']}  [{skill['id']}]",
            f"  category : {category['name']} ({category['id']})",
            f"  purpose  : {skill['description']}",
            f"  phase    : {phase['id']} — {phase.get('label', '')}",
            f"  risk     : {risk['tier']} (approval required: {risk['approval_required']})",
            f"  risk why : {', '.join(risk['matched_terms']) or 'no risk keywords matched'}",
            f"  fix note : {risk['execution_note']}",
        ]
        for gate in gates:
            lines.append(f"  gate     : {gate['id']} — {gate['requirement']}")
        if neighbours:
            lines.append("  neighbours (do not confuse): " + ", ".join(neighbours))
        return finish(payload, lines)

    catalog, category_ids = engine.resolve_category_ids(session.config, [category_sel])
    category = index["categories"][category_ids[0]]
    phase = engine.phase_for_category(session.config, category["id"])
    payload = {
        "category_id": category["id"],
        "category_name": category["name"],
        "purpose": category["purpose"],
        "phase": phase,
        "skills": [
            {"skill_id": skill["id"], "skill_name": skill["name"], "description": skill["description"]}
            for skill in category["skills"]
        ],
    }
    lines = [
        f"{category['name']}  [{category['id']}] — {category['skill_count']} skills",
        f"  purpose: {category['purpose']}",
        f"  phase  : {phase['id']} — {phase.get('label', '')}",
    ]
    for number, skill in enumerate(category["skills"], start=1):
        lines.append(f"  {number:>2}. {skill['id']:<52} {skill['name']}")
    return finish(payload, lines)


def cmd_plan(session, args):
    catalog = _catalog(session, getattr(args, "catalog", None))
    policy = _policy(session, getattr(args, "policy", None))
    request_text = _read_request(session, args)
    category_ids = None
    mode = "request"
    if getattr(args, "category", None):
        catalog, category_ids = engine.resolve_category_ids(session.config, args.category)
        mode = "category"
    if not request_text:
        if category_ids:
            # No free-text request: derive it from the selected category names and purposes.
            by_id = {category["id"]: category for category in catalog["categories"]}
            request_text = " ".join(
                f"{by_id[cid]['name']} {by_id[cid]['purpose']}".strip() for cid in category_ids
            ).strip()
        else:
            raise core.SkillToolError(
                "missing-request",
                "plan needs --request <text> (or '-' for stdin) or --request-file <path>.",
                hint="Example: --request \"prepare slides for a client review and share read-only\"",
            )
    plan = engine.build_plan(
        catalog,
        request_text,
        session.config,
        policy,
        category_ids=category_ids,
        limit=getattr(args, "max_matches", None),
        mode=mode,
    )

    schema_path = core.config_path(session.config, "plan_schema", "asset-plan-schema.json")
    schema = core.load_json(schema_path, "Plan schema")
    schema_issues = engine.validate_against_schema(plan, schema)
    if schema_issues:
        plan["validation"]["status"] = "error"
        plan["validation"]["issues"].extend(schema_issues[:20])

    payload = {"plan": plan, "fingerprint": _plan_fingerprint(plan)}
    lines = engine.render_plan_text(plan)
    lines.append("")
    lines.append(f"Plan fingerprint: {payload['fingerprint']}")
    lines.append("Next: python3 tools-gslides-catalog.py approve-request --plan <plan.json>")

    out = session.output_path(getattr(args, "out", None), "plan.json")
    exit_code = EXIT_OK
    if schema_issues:
        exit_code = EXIT_SCHEMA
        lines.append(f"SCHEMA: plan failed validation with {len(schema_issues)} issue(s).")
    elif not plan["matches"]:
        exit_code = EXIT_NO_MATCH
        lines.append("NO-MATCH: no capability matched; see the validation issues above.")
    elif plan["approvals"] and getattr(args, "fail_on_approval", False):
        exit_code = EXIT_APPROVAL_OPEN
        lines.append(f"APPROVAL-OPEN: {len(plan['approvals'])} gated step(s) need a signed approval record.")
    if out:
        core.save_json(out, plan)
        lines.append(f"Wrote plan: {out}")
        payload["plan_path"] = out
    return finish(payload, lines, exit_code)


def cmd_validate(session, args):
    plan_path = getattr(args, "plan", None)
    if not plan_path:
        raise core.SkillToolError("missing-path", "validate needs --plan <path>.")
    plan = core.load_json(plan_path, "Plan document")
    schema_path = core.config_path(session.config, "plan_schema", "asset-plan-schema.json")
    schema = core.load_json(schema_path, "Plan schema")
    issues = engine.validate_against_schema(plan, schema)
    policy = _policy(session, getattr(args, "policy", None))
    semantic = []
    if isinstance(plan, dict):
        approvals = plan.get("approvals") or []
        gated_matches = [
            match for match in (plan.get("matches") or [])
            if (match.get("risk") or {}).get("approval_required")
        ]
        if len(approvals) < len(gated_matches):
            semantic.append(
                f"{len(gated_matches)} match(es) require approval but only {len(approvals)} "
                "approval entr(y/ies) are present."
            )
        for match in plan.get("matches") or []:
            if match.get("skill_id") is None:
                semantic.append("A match entry has no skill_id.")
        if (plan.get("request") or {}).get("mode") not in ("request", "category", "skill"):
            semantic.append("request.mode must be request, category or skill.")
        policy_terms = {
            keyword for rule in policy.get("rules", []) for keyword in rule.get("keywords", [])
        }
        for approval in approvals:
            if approval.get("tier") not in policy["_tiers"]:
                semantic.append(f"Unknown policy tier in approval: {approval.get('tier')!r}")
            for reason in approval.get("reasons", []):
                if not any(term in reason for term in policy_terms) and not reason.startswith("hard gate"):
                    semantic.append(f"Approval reason is not traceable to policy terms: {reason!r}")
    issues.extend(semantic)
    status = "ok" if not issues else "error"
    lines = [f"Plan validation: {status}"]
    if issues:
        lines.extend(f"  - {issue}" for issue in issues[:40])
        if len(issues) > 40:
            lines.append(f"  ... {len(issues) - 40} more")
    else:
        lines.append(
            f"  {len(plan.get('matches', []))} match(es), {len(plan.get('phases', []))} phase(s), "
            f"{len(plan.get('approvals', []))} approval gate(s) — schema and policy checks passed."
        )
    payload = {"status": status, "issues": issues, "plan": str(core.resolve_path(plan_path))}
    return finish(payload, lines, EXIT_OK if not issues else EXIT_SCHEMA)


def cmd_spec(session, args):
    catalog = _catalog(session, getattr(args, "catalog", None))
    policy = _policy(session, getattr(args, "policy", None))
    index = core.index_catalog(catalog)
    skill_id = getattr(args, "skill", None)
    if not skill_id:
        raise core.SkillToolError("missing-selector", "spec needs --skill <id>.")
    skill = index["skills"].get(skill_id)
    if skill is None:
        import difflib

        close = difflib.get_close_matches(skill_id, list(index["skills"]), n=3, cutoff=0.5)
        raise core.SkillToolError(
            "unknown-skill",
            f"No catalog skill with id {skill_id!r}.",
            hint=("Did you mean: " + ", ".join(close)) if close else "Run 'list' to read exact ids.",
        )
    category = index["categories"][skill["category_id"]]
    template_path = getattr(args, "template", None)
    template_path = (
        core.resolve_path(template_path) if template_path
        else core.config_path(session.config, "spec_template", "asset-spec-template.md")
    )
    if not os.path.isfile(template_path):
        raise core.SkillToolError(
            "template-missing",
            f"Specification template not found: {template_path}",
            hint="Restore asset-spec-template.md or pass --template <path>.",
        )
    with open(template_path, "r", encoding="utf-8") as handle:
        template_text = handle.read()
    request_text = (getattr(args, "request", None) or "").strip()
    document, meta = engine.render_spec(
        skill, category, session.config, policy, template_text, catalog, request_text
    )
    out = session.output_path(getattr(args, "out", None), f"spec-{skill['id']}.md")
    lines = [
        f"Specification for {skill['name']} [{skill['id']}]",
        f"  category : {category['name']} ({category['id']})",
        f"  phase    : {meta['phase']}",
        f"  risk     : {meta['risk_tier']}",
        f"  sections : {meta['sections_rendered']}/{meta['sections_total']} filled from the template",
    ]
    if out:
        core.save_text(out, document)
        lines.append(f"Wrote specification: {out}")
        meta["path"] = out
    else:
        lines.append("")
        lines.append(document)
    payload = {"specification": meta}
    return finish(payload, lines)


def cmd_coverage(session, args):
    catalog = _catalog(session, getattr(args, "catalog", None))
    manifest_path = getattr(args, "manifest", None)
    if not manifest_path:
        raise core.SkillToolError(
            "missing-path",
            "coverage needs --manifest <path>.",
            hint='Manifest example: {"skills": {"presentation-creation-router": "implemented"}}',
        )
    manifest = core.load_json(manifest_path, "Coverage manifest")
    report = engine.coverage_report(catalog, manifest, session.config)
    threshold = getattr(args, "min_coverage", None)
    lines = engine.render_coverage_text(report)
    exit_code = EXIT_OK
    if threshold is not None:
        passed = report["overall_coverage"] >= float(threshold)
        lines.append("")
        lines.append(
            f"Threshold {float(threshold):.2f}: {'PASS' if passed else 'FAIL'} "
            f"(coverage {report['overall_coverage']:.3f})"
        )
        exit_code = EXIT_OK if passed else EXIT_SCHEMA
    out = session.output_path(getattr(args, "out", None), "coverage-report.json")
    payload = {"report": report}
    if out:
        core.save_json(out, report)
        lines.append(f"Wrote report: {out}")
        payload["report_path"] = out
    return finish(payload, lines, exit_code)


def cmd_ingest(session, args):
    source = getattr(args, "source", None)
    if not source:
        raise core.SkillToolError(
            "missing-path",
            "ingest needs --source <path> (plain text, Markdown, HTML or PDF).",
        )
    text, meta = core.read_source_text(source, session.limits["max_source_bytes"])
    catalog = core.parse_catalog_text(
        text,
        source_name=os.path.basename(core.resolve_path(source)),
        max_skills=session.limits["max_skills"],
    )
    issues = core.validate_catalog(catalog)
    errors = [issue for issue in issues if issue["severity"] == "error"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    lines = [
        f"Ingested {meta['path']} ({meta['format']}, {meta['bytes']} bytes)",
        f"  categories : {catalog['counts']['categories']}",
        f"  skills     : {catalog['counts']['skills']}",
        f"  errors     : {len(errors)}",
        f"  warnings   : {len(warnings)}",
    ]
    for issue in (errors + warnings)[:20]:
        lines.append(f"  {issue['severity']}: [{issue['code']}] {issue['message']}")
    exit_code = EXIT_OK
    if getattr(args, "validate", False) and errors:
        exit_code = EXIT_SCHEMA
        lines.append("VALIDATION: catalog failed validation; fix the source or the parser mapping.")
    if errors and not getattr(args, "validate", False):
        exit_code = EXIT_SCHEMA
    out = session.output_path(getattr(args, "out", None), "catalog.json")
    payload = {"catalog": {k: v for k, v in catalog.items() if k != "categories"},
               "issues": issues, "source": meta}
    if out:
        document = dict(catalog)
        document["schema_version"] = catalog.get("schema_version", "1.0")
        core.save_json(out, document)
        lines.append(f"Wrote catalog: {out}")
        payload["catalog_path"] = out
    else:
        payload["categories"] = catalog["categories"]
    return finish(payload, lines, exit_code)


def cmd_approve_request(session, args):
    plan_path = getattr(args, "plan", None)
    if not plan_path:
        raise core.SkillToolError("missing-path", "approve-request needs --plan <path>.")
    plan = core.load_json(plan_path, "Plan document")
    if not isinstance(plan, dict) or "matches" not in plan:
        raise core.SkillToolError("plan-invalid", "The file is not a plan document produced by this tool.")
    fingerprint = _plan_fingerprint(plan)
    approvals = plan.get("approvals") or []
    record = {
        "record_version": "1.0",
        "status": "pending-approval",
        "plan_fingerprint": fingerprint,
        "plan_path": os.path.basename(core.resolve_path(plan_path)),
        "request": (plan.get("request") or {}).get("text", ""),
        "gated_skills": sorted({approval["skill_id"] for approval in approvals}),
        "approval_requirements": [
            {
                "skill_id": approval["skill_id"],
                "skill_name": approval["skill_name"],
                "tier": approval["tier"],
                "required_before": approval["required_before"],
                "reasons": approval.get("reasons", []),
            }
            for approval in approvals
        ],
        "instructions": (
            "An authorised operator must set status to 'approved', add 'approver', add an ISO-8601 "
            "'approved_at' timestamp, and list every acknowledged skill id in "
            "'acknowledged_skills'. Then run approve-check."
        ),
        "approver": "",
        "approved_at": "",
        "acknowledged_skills": [],
    }
    out = session.output_path(getattr(args, "out", None), "approval-request.json")
    lines = [
        f"Approval request for {len(approvals)} gated step(s)",
        f"  fingerprint: {fingerprint}",
    ]
    for approval in approvals:
        lines.append(f"  - {approval['skill_name']} ({approval['tier']})")
    if not approvals:
        lines.append("  (this plan has no gated steps; approval is not required)")
    if out:
        core.save_json(out, record)
        lines.append(f"Wrote unsigned record: {out}")
        lines.append("Operator action required: sign the record, then run approve-check.")
        payload = {"record": record, "record_path": out, "fingerprint": fingerprint}
    else:
        lines.append("")
        lines.append(json.dumps(record, indent=2, ensure_ascii=False))
        payload = {"record": record, "fingerprint": fingerprint}
    return finish(payload, lines)


def cmd_approve_check(session, args):
    plan_path = getattr(args, "plan", None)
    record_path = getattr(args, "record", None)
    if not plan_path or not record_path:
        raise core.SkillToolError(
            "missing-path",
            "approve-check needs --plan <path> and --record <signed record path>.",
        )
    plan = core.load_json(plan_path, "Plan document")
    record = core.load_json(record_path, "Approval record")
    fingerprint = _plan_fingerprint(plan)
    approvals = plan.get("approvals") or []
    max_age = float((session.config.get("approval") or {}).get("max_age_hours", 72))

    problems = []
    if record.get("plan_fingerprint") != fingerprint:
        problems.append(
            "Record fingerprint does not match this plan; the plan changed after approval."
        )
    if record.get("status") != "approved":
        problems.append(f"Record status is {record.get('status')!r}, not 'approved'.")
    if not str(record.get("approver") or "").strip():
        problems.append("Record has no approver; approval must name a responsible person.")
    approved_at = str(record.get("approved_at") or "").strip()
    age_hours = None
    if not approved_at:
        problems.append("Record has no approved_at timestamp.")
    else:
        try:
            from datetime import datetime, timezone

            stamp = approved_at.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0
            if age_hours < -0.05:
                problems.append("approved_at is in the future; check the operator clock or timezone.")
            elif age_hours > max_age:
                problems.append(
                    f"Approval is {age_hours:.1f} hours old, above the {max_age:g} hour limit."
                )
        except ValueError:
            problems.append("approved_at is not a valid ISO-8601 timestamp.")
    missing = sorted(
        {approval["skill_id"] for approval in approvals}
        - set(record.get("acknowledged_skills") or [])
    )
    if missing:
        problems.append("Unacknowledged gated skills: " + ", ".join(missing))

    decision = "approved" if not problems else "denied"
    lines = [
        f"Approval decision: {decision}",
        f"  fingerprint: {fingerprint}",
        f"  gated steps: {len(approvals)}",
    ]
    if approvals:
        lines.append(f"  approver   : {record.get('approver') or '(none)'}")
        lines.append(f"  approved_at: {approved_at or '(none)'}"
                     + (f"  age={age_hours:.1f}h" if age_hours is not None else ""))
    for problem in problems:
        lines.append(f"  problem: {problem}")
    if decision == "approved":
        lines.append("Execution may proceed for the gated steps listed above.")
    else:
        lines.append("Execution blocked. Re-run approve-request and have an operator sign the record.")
    payload = {
        "decision": decision,
        "fingerprint": fingerprint,
        "gated_steps": [
            {
                "skill_id": approval["skill_id"],
                "skill_name": approval["skill_name"],
                "tier": approval["tier"],
            }
            for approval in approvals
        ],
        "problems": problems,
        "max_age_hours": max_age,
        "age_hours": None if age_hours is None else round(age_hours, 3),
    }
    exit_code = EXIT_OK if decision == "approved" else EXIT_DENIED
    return finish(payload, lines, exit_code)


def cmd_doctor(session, args):
    checks = []

    def add(check_id, ok_flag, detail):
        checks.append({"check": check_id, "ok": bool(ok_flag), "detail": detail})

    config = session.config
    add("config", True, f"loaded {config['_config_path']} (version {config.get('config_version')})")
    catalog_path = core.config_path(config, "catalog", "asset-slides-catalog.json")
    try:
        catalog = core.load_catalog_asset(config)
        issues = [issue for issue in core.validate_catalog(catalog) if issue["severity"] == "error"]
        add("catalog", not issues,
            f"{catalog['counts']['categories']} categories / {catalog['counts']['skills']} skills, "
            f"{len(issues)} error(s)")
    except core.SkillToolError as error:
        add("catalog", False, error.message)
    try:
        policy = engine.load_policy(config)
        unranked = [
            rule.get("tier") for rule in policy.get("rules", []) if rule.get("tier") not in policy["_tiers"]
        ]
        add("risk-policy", not unranked,
            f"{len(policy['_tiers'])} tiers, {len(policy.get('rules', []))} rules, "
            f"{len(unranked)} rule(s) reference an undefined tier")
    except core.SkillToolError as error:
        add("risk-policy", False, error.message)
    try:
        schema = core.load_json(core.config_path(config, "plan_schema", "asset-plan-schema.json"),
                                "Plan schema")
        add("plan-schema", bool(schema.get("properties")), "schema loaded with "
            f"{len(schema.get('properties', {}))} top-level properties")
    except core.SkillToolError as error:
        add("plan-schema", False, error.message)
    try:
        template_path = core.config_path(config, "spec_template", "asset-spec-template.md")
        with open(template_path, "r", encoding="utf-8") as handle:
            template = handle.read()
        sections = len(engine._SPEC_SECTION_RE.findall(template)) if hasattr(engine, "_SPEC_SECTION_RE") else 0
        add("spec-template", sections >= 1, f"{sections} numbered section(s) in {os.path.basename(template_path)}")
    except OSError as error:
        add("spec-template", False, str(error))
    mapped = set()
    for phase in config.get("phases") or []:
        mapped.update(phase.get("categories") or [])
    try:
        unmapped = [
            category["id"] for category in catalog["categories"] if category["id"] not in mapped
        ]
    except (NameError, KeyError):
        unmapped = ["catalog unavailable"]
    add("phase-mapping", not unmapped,
        "all categories mapped to a phase" if not unmapped else "unmapped: " + ", ".join(unmapped[:5]))
    add("python", sys.version_info >= (3, 8), f"Python {sys.version.split()[0]}")
    api_tool = os.path.join(HERE, "tools-slides-api.py")
    add("api-adapter", os.path.isfile(api_tool), "tools-slides-api.py present" if os.path.isfile(api_tool)
        else "tools-slides-api.py missing (live API calls unavailable)")
    failed = [check for check in checks if not check["ok"]]
    lines = ["Environment doctor:"]
    for check in checks:
        lines.append(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['check']}: {check['detail']}")
    lines.append("")
    lines.append("Doctor result: " + ("healthy" if not failed else f"{len(failed)} check(s) failed"))
    exit_code = EXIT_OK if not failed else EXIT_SCHEMA
    return finish({"checks": checks, "healthy": not failed}, lines, exit_code)


COMMANDS = {
    "help": cmd_help,
    "capabilities": cmd_capabilities,
    "categories": cmd_categories,
    "list": cmd_list,
    "describe": cmd_describe,
    "plan": cmd_plan,
    "validate": cmd_validate,
    "spec": cmd_spec,
    "coverage": cmd_coverage,
    "ingest": cmd_ingest,
    "approve-request": cmd_approve_request,
    "approve-check": cmd_approve_check,
    "doctor": cmd_doctor,
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Google Slides agent catalog Skill Tool: plan, gate, spec and score Slides capabilities.",
        add_help=True,
    )
    parser.add_argument("--config", help="Configuration file path")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON envelope")
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command")

    def add(name, help_text):
        return sub.add_parser(name, help=help_text)

    add("help", "Show command reference")
    add("capabilities", "List implemented capabilities")
    p = add("categories", "List catalog categories")
    p.add_argument("--catalog", help="Catalog asset path override")
    p = add("list", "List catalog skills")
    p.add_argument("--category", action="append", help="Category number, id or name fragment")
    p.add_argument("--search", help="Substring filter over id, name and description")
    p.add_argument("--limit", type=int, help="Maximum rows to show")
    p.add_argument("--catalog", help="Catalog asset path override")
    p = add("describe", "Describe a category or skill")
    p.add_argument("--skill", help="Skill id")
    p.add_argument("--category", help="Category number, id or name fragment")
    p.add_argument("--catalog", help="Catalog asset path override")
    p.add_argument("--policy", help="Risk policy path override")
    p = add("plan", "Plan a Slides request")
    p.add_argument("--request", help="Request text, or '-' to read stdin")
    p.add_argument("--request-file", help="Read the request from a file")
    p.add_argument("--category", action="append", help="Restrict to categories (repeatable)")
    p.add_argument("--max-matches", type=int, help="Maximum matched capabilities")
    p.add_argument("--out", help="Write the plan JSON to this path")
    p.add_argument("--catalog", help="Catalog asset path override")
    p.add_argument("--policy", help="Risk policy path override")
    p.add_argument("--fail-on-approval", action="store_true",
                   help="Exit 3 when approval gates are open")
    p = add("validate", "Validate a plan document")
    p.add_argument("--plan", required=False, help="Plan JSON path")
    p.add_argument("--policy", help="Risk policy path override")
    p = add("spec", "Render an implementation specification")
    p.add_argument("--skill", help="Skill id")
    p.add_argument("--request", help="Optional request context recorded in the spec")
    p.add_argument("--template", help="Specification template path override")
    p.add_argument("--out", help="Write the Markdown specification to this path")
    p.add_argument("--catalog", help="Catalog asset path override")
    p.add_argument("--policy", help="Risk policy path override")
    p = add("coverage", "Coverage and gap report")
    p.add_argument("--manifest", help="Coverage manifest JSON path")
    p.add_argument("--min-coverage", type=float, help="Fail when overall coverage is below this ratio")
    p.add_argument("--out", help="Write the report JSON to this path")
    p.add_argument("--catalog", help="Catalog asset path override")
    p = add("ingest", "Parse a catalog source")
    p.add_argument("--source", help="Plain text, Markdown, HTML or PDF catalog source")
    p.add_argument("--out", help="Write the parsed catalog JSON to this path")
    p.add_argument("--validate", action="store_true", help="Fail when validation errors are found")
    p = add("approve-request", "Emit an unsigned approval record")
    p.add_argument("--plan", help="Plan JSON path")
    p.add_argument("--out", help="Write the unsigned record to this path")
    p = add("approve-check", "Verify a signed approval record")
    p.add_argument("--plan", help="Plan JSON path")
    p.add_argument("--record", help="Signed approval record path")
    add("doctor", "Environment and asset integrity check")
    return parser


def _hoist_globals(argv):
    """Allow --json/--config before or after the subcommand (argparse only accepts them before)."""
    hoisted = []
    rest = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--json":
            hoisted.append(token)
            index += 1
        elif token == "--config" and index + 1 < len(argv):
            hoisted.extend([token, argv[index + 1]])
            index += 2
        elif token.startswith("--config="):
            hoisted.append(token)
            index += 1
        else:
            rest.append(token)
            index += 1
    return hoisted + rest


def main(argv=None):
    argv = _hoist_globals(list(sys.argv[1:] if argv is None else argv))
    if not argv:
        sys.stdout.write(HELP_TEXT)
        return EXIT_OK
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors, 0 on --help/--version
        return int(exc.code or 0)
    command = args.command or "help"
    handler = COMMANDS.get(command)
    if handler is None:
        sys.stderr.write(f"ERROR [unknown-command] {command!r} is not implemented.\n")
        sys.stderr.write("Run: python3 tools-gslides-catalog.py capabilities\n")
        return EXIT_USAGE
    try:
        session = Session(args)
        result = handler(session, args)
        if isinstance(result, tuple):
            payload, exit_code = result
            print_result(payload, session.json)
            return int(exit_code)
        return int(result or EXIT_OK)
    except core.SkillToolError as error:
        print_error(error, getattr(args, "json", False))
        return EXIT_USAGE
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted: no partial output was written by this command.\n")
        return 130
    except BrokenPipeError:
        return EXIT_OK
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR [io-failure] {error}\n")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
