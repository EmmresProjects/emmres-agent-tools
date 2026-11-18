#!/usr/bin/env python3
"""tools-humansphere.py - PRIMARY ENTRY POINT

HumanSphere Holistic Support Skill Tool: routes a stated human need to the
smallest useful set of support skills from a 178-skill / 18-category catalog,
builds a safety-first support plan, reviews drafted responses against a
governance policy, produces facts-only handoff summaries, and keeps
consent-based preference memory.

Run from the future skill root:
    python3 humansphere-holistic-support/tools-humansphere.py --help
    python3 humansphere-holistic-support/tools-humansphere.py capabilities
    python3 humansphere-holistic-support/tools-humansphere.py plan --query "..."

Requires Python 3.9+ and the standard library only.
Every path below is relative to this file's folder (the Skill Tool folder), so
the tool works from any working directory and from any parent skill root.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import unittest
from pathlib import Path

TOOL_VERSION = "1.0.0"
_HERE = Path(__file__).resolve().parent

DEFAULT_CATALOG = _HERE / "asset-skill-catalog.json"
DEFAULT_POLICY = _HERE / "asset-safety-policy.json"
DEFAULT_TEMPLATE = _HERE / "asset-plan-template.md"
DEFAULT_RESOURCES = _HERE / "asset-support-resources.json"
DEFAULT_SCHEMA = _HERE / "asset-preference-profile.schema.json"
DEFAULT_PROFILE = _HERE / "asset-preference-profile.json"

EXIT_OK = 0
EXIT_WARN = 1
EXIT_BLOCK = 2
EXIT_APPROVAL = 3
EXIT_INPUT = 4
EXIT_TIMEOUT = 5


def _load(filename: str, module_name: str):
    if module_name in sys.modules:
        return sys.modules[module_name]
    target = _HERE / filename
    if not target.is_file():
        print(
            f"FATAL: required Skill Tool file is missing: {filename}\n"
            f"       Expected next to the entry point in: {_HERE.name}/",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_INPUT)
    spec = importlib.util.spec_from_file_location(module_name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


engine = _load("tools-catalog-engine.py", "hs_catalog_engine")
safety = _load("tools-safety-engine.py", "hs_safety_engine")
planner = _load("tools-plan-builder.py", "hs_plan_builder")
parser_mod = _load("tools-input-parser.py", "hs_input_parser")

CatalogError = engine.CatalogError
SafetyError = safety.SafetyError


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
class Context:
    def __init__(self, args):
        self.args = args
        self._catalog = None
        self._policy = None
        self._registry = None
        self._profile = None
        self._template = None

    @property
    def catalog_path(self) -> Path:
        return Path(self.args.catalog)

    @property
    def catalog(self) -> dict:
        if self._catalog is None:
            self._catalog = engine.load_catalog(self.catalog_path)
        return self._catalog

    @property
    def policy(self) -> dict:
        if self._policy is None:
            self._policy = safety.load_policy(self.args.policy)
        return self._policy

    @property
    def registry(self) -> dict:
        if self._registry is None:
            self._registry = planner.load_resources(self.args.resources)
        return self._registry

    @property
    def profile(self) -> dict:
        if self._profile is None:
            self._profile = planner.load_profile(self.args.profile)
        return self._profile

    @property
    def template(self) -> str:
        if self._template is None:
            self._template = planner.load_template(self.args.template)
        return self._template


def emit(payload: dict, args, text_renderer=None) -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if text_renderer is not None:
            print(text_renderer(payload))
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
    code = payload.get("exit_code")
    return int(code) if isinstance(code, int) else EXIT_OK


def read_text_arg(args, direct_attr: str = "text", file_attr: str = "input_file") -> str:
    direct = getattr(args, direct_attr, None)
    path_value = getattr(args, file_attr, None)
    if direct and path_value:
        raise CatalogError(
            "conflicting_input",
            f"Pass either --{direct_attr.replace('_', '-')} or --{file_attr.replace('_', '-')}, not both.",
        )
    if path_value:
        path = Path(path_value)
        if not path.is_file():
            raise CatalogError(
                "input_missing",
                f"Input file not found: {path}",
                "Check the path, or pass the text directly instead.",
            )
        if path.stat().st_size > engine.LIMITS["max_source_bytes"]:
            raise CatalogError(
                "input_too_large",
                f"Input file exceeds {engine.LIMITS['max_source_bytes']} bytes.",
            )
        return path.read_text(encoding="utf-8", errors="replace")
    if direct:
        return direct
    raise CatalogError(
        "missing_argument",
        f"An input value is required: --{direct_attr.replace('_', '-')} or --{file_attr.replace('_', '-')}.",
    )


# ---------------------------------------------------------------------------
# Text renderers
# ---------------------------------------------------------------------------
def render_capabilities(payload: dict) -> str:
    lines = [
        payload["title"],
        f"Skills: {payload['counts']['skills']}  |  Categories: {payload['counts']['categories']}",
        "",
    ]
    for category in payload["categories"]:
        lines.append(f"{category['category_id']}. {category['category']}  ({category['skill_count']} skills)")
        for skill in category["skills"]:
            flag = " [human referral]" if skill["needs_human_referral"] else ""
            risk = "" if skill["risk"] == "standard" else f" ({skill['risk']} risk)"
            lines.append(f"   {skill['id']:>3}. {skill['name']}{risk}{flag}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_list(payload: dict) -> str:
    lines = [f"{'ID':>4}  {'Risk':<8} {'Category':<34} Skill"]
    for skill in payload["skills"]:
        lines.append(
            f"{skill['id']:>4}  {skill['risk']:<8} {skill['category'][:34]:<34} {skill['name']}"
        )
    lines.append("")
    lines.append(f"{payload['count']} skill(s) listed.")
    return "\n".join(lines)


def render_show(payload: dict) -> str:
    lines = [
        f"{payload['id']}. {payload['name']}  [{payload['slug']}]",
        f"Category: {payload['category']} (id {payload['category_id']})",
        f"Risk: {payload['risk']}  |  Human referral: {'yes' if payload['needs_human_referral'] else 'no'}",
        f"Description: {payload['description']}",
    ]
    if payload.get("risk_reasons"):
        lines.append("Risk reasons: " + "; ".join(payload["risk_reasons"]))
    if payload.get("steps"):
        lines.append("Operating prompts:")
        lines.extend(f"  - {step}" for step in payload["steps"])
    return "\n".join(lines)


def render_search(payload: dict) -> str:
    if not payload["results"]:
        return f"No skill matched '{payload['query']}'. Try fewer or more general words."
    lines = [f"Top matches for '{payload['query']}':"]
    for item in payload["results"]:
        lines.append(
            f"  {item['id']:>3}. {item['name']}  [{item['category']}]  score {item['score']}  "
            f"matched: {', '.join(item['matched_terms'])}"
        )
    return "\n".join(lines)


def render_route(payload: dict) -> str:
    active = [item for item in payload["selected"] if not item.get("pinned")]
    pinned = [item for item in payload["selected"] if item.get("pinned")]
    lines = [
        f"Stated need: {payload['query']}",
        f"Selected {len(active)} skill(s) from {payload['candidates_considered']} candidates "
        f"(cap {payload['max_skills']}"
        + (f", plus {len(pinned)} pinned governance check" if pinned else "")
        + ").",
        "",
    ]
    for index, item in enumerate(payload["selected"], start=1):
        lines.append(f"{index}. {item['id']}. {item['name']}  [{item['category']}] risk={item['risk']}")
        lines.append(f"   why: {item['reason']}")
        for step in (item.get("steps") or [])[:4]:
            lines.append(f"   - {step}")
    return "\n".join(lines)


def render_review(payload: dict) -> str:
    lines = [
        f"Verdict: {payload['verdict'].upper()}  "
        f"(block {payload['counts']['block']}, warn {payload['counts']['warn']})",
        f"Urgency of the user's text: {payload['urgency']['level']} - {payload['urgency']['label']}",
    ]
    if payload["findings"]:
        lines.append("")
        lines.append("Findings:")
        for finding in payload["findings"]:
            lines.append(f"  [{finding['severity'].upper()}] {finding['rule_id']} - {finding['title']}")
            for evidence in finding["evidence"]:
                lines.append(f"      evidence: {evidence}")
            lines.append(f"      fix: {finding['remediation']}")
    else:
        lines.append("")
        lines.append("No policy findings. Still confirm the human option is named once.")
    return "\n".join(lines)


def render_urgency(payload: dict) -> str:
    lines = [
        f"Urgency level: {payload['level']} ({payload['label']})",
        f"Escalation required: {'yes' if payload['escalation_required'] else 'no'}  |  "
        f"Human handoff required: {'yes' if payload['human_handoff_required'] else 'no'}",
    ]
    if payload["signals"]:
        lines.append("Signals:")
        for signal in payload["signals"]:
            lines.append(f"  {signal['signal_id']} ({signal['level']}): {'; '.join(signal['evidence'])}")
    if payload["required_actions"]:
        lines.append("Required actions:")
        lines.extend(f"  - {action}" for action in payload["required_actions"])
    if payload.get("escalation_text"):
        lines.extend(["", "Suggested escalation wording:", payload["escalation_text"]])
    return "\n".join(lines)


def render_resources(payload: dict) -> str:
    if not payload["resources"]:
        return "No registry entry matched. Do not invent one: look the service up with the user."
    lines = ["Official source pointers - verify on the official page before sharing.", ""]
    for entry in payload["resources"]:
        contact = f"  hint: {entry['contact_hint']}" if entry.get("contact_hint") else ""
        lines.append(f"- {entry['name']}  [{entry['region']}/{', '.join(entry['kinds'])}]")
        if entry.get("official_url"):
            lines.append(f"    source: {entry['official_url']}")
        if contact:
            lines.append(f"    {contact.strip()}")
        lines.append(f"    verify: {entry['verify_note']}")
    return "\n".join(lines)


def render_governance(payload: dict) -> str:
    lines = [
        f"{payload['summary']['title']} (policy {payload['summary']['policy_version']})",
        "",
        "Urgency levels:",
    ]
    for level in payload["summary"]["urgency_levels"]:
        lines.append(f"  {level['rank']}. {level['level']}: {level['label']}")
        for action in level["required_actions"]:
            lines.append(f"     - {action}")
    lines.append("")
    lines.append("Response rules:")
    for rule in payload["summary"]["response_rules"]:
        lines.append(f"  [{rule['severity'].upper()}] {rule['rule_id']}: {rule['title']}")
    lines.append("Conditional rules:")
    for rule in payload["summary"]["conditional_rules"]:
        lines.append(f"  [{rule['severity'].upper()}] {rule['rule_id']}: {rule['title']}")
    lines.append("")
    lines.append("Professional scope boundaries:")
    for domain in payload["summary"]["scope_boundaries"]:
        lines.append(f"  - {domain}")
    lines.append("")
    lines.append("Approval gates:")
    for gate in payload["summary"]["approval_gates"]:
        lines.append(f"  - {gate}")
    lines.append("")
    lines.append("Privacy patterns: " + ", ".join(payload["summary"]["pii_patterns"]))
    lines.append("")
    lines.append("Self check: " + ("PASS" if payload["self_check"]["passed"] else "FAIL"))
    for name, ok in payload["self_check"]["cases"].items():
        lines.append(f"  {'ok  ' if ok else 'FAIL'} {name}")
    return "\n".join(lines)


def render_profile(payload: dict) -> str:
    profile = payload["profile"]
    lines = [
        f"Profile: {payload['path']}",
        f"Consent granted: {'yes' if profile['consent']['granted'] else 'no'}"
        + (f" (scope: {', '.join(profile['consent']['scope'])})" if profile["consent"]["scope"] else ""),
        f"Identity: name={profile['identity']['name_used'] or '-'} pronouns={profile['identity']['pronouns'] or '-'} "
        f"age_band={profile['identity']['age_band']} languages={','.join(profile['identity']['languages']) or '-'}",
        f"Preferences: tone={profile['preferences']['tone']} directness={profile['preferences']['directness']}/5 "
        f"format={profile['preferences']['format']} reading={profile['preferences']['reading_level']} "
        f"humor={profile['preferences']['humor']}",
        f"Tone directive: {payload['tone_directive']}",
        f"Personalization: {payload['personalization']}",
        f"Memory entries: {len(profile['memory']['entries'])}",
    ]
    for entry in profile["memory"]["entries"]:
        lines.append(f"  - {entry['key']} = {entry['value']}  ({entry.get('source', 'user-stated')}, {entry.get('added_at', '')})")
    if payload.get("validation"):
        lines.append("Validation: " + ("valid" if payload["validation"]["valid"] else "INVALID"))
        for error in payload["validation"]["errors"][:10]:
            lines.append(f"  ! {error}")
    return "\n".join(lines)


def render_parse_summary(payload: dict) -> str:
    extraction = payload["extraction"]
    lines = [
        f"Parsed {extraction['skills']} skill(s) in {extraction['categories']} categories "
        f"from {extraction['format']} input ({extraction['characters']} characters, mode {extraction['mode']}).",
        f"Title: {payload['title']}",
    ]
    for category in payload["categories"]:
        lines.append(f"  {category['id']}. {category['name']} ({category['skill_count']})")
    if payload.get("output"):
        lines.append(f"Written: {payload['output']['path']} ({payload['output']['bytes']} bytes)")
    return "\n".join(lines)


def render_validate(payload: dict) -> str:
    lines = [f"Asset validation: {'PASS' if payload['valid'] else 'FAIL'}", ""]
    for check in payload["checks"]:
        status = "ok  " if check["valid"] else "FAIL"
        lines.append(f"  {status} {check['asset']} - {check['detail']}")
    if payload.get("errors"):
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"  ! {error}" for error in payload["errors"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_capabilities(ctx: Context) -> int:
    args = ctx.args
    catalog = ctx.catalog
    payload = engine.capabilities(catalog)
    if args.category:
        wanted = engine.normalize_text(args.category)
        filtered = [
            category
            for category in payload["categories"]
            if wanted in engine.normalize_text(category["category"]) or wanted == str(category["category_id"])
        ]
        if not filtered:
            raise CatalogError(
                "category_not_found",
                f"No category matches '{args.category}'.",
                "Run the capabilities command without --category to list all 18 categories.",
            )
        payload["categories"] = filtered
        payload["counts"] = {
            "skills": sum(c["skill_count"] for c in filtered),
            "categories": len(filtered),
        }
    payload["exit_code"] = EXIT_OK
    return emit(payload, args, render_capabilities)


def cmd_list(ctx: Context) -> int:
    args = ctx.args
    skills = engine.list_skills(ctx.catalog, args.category)
    if args.risk:
        skills = [s for s in skills if s["risk"] == args.risk]
    payload = {
        "count": len(skills),
        "skills": [
            {
                "id": s["id"],
                "name": s["name"],
                "slug": s["slug"],
                "category": s["category"],
                "risk": s["risk"],
                "needs_human_referral": s["needs_human_referral"],
            }
            for s in skills
        ],
        "exit_code": EXIT_OK,
    }
    return emit(payload, args, render_list)


def cmd_show(ctx: Context) -> int:
    skill = engine.find_skill(ctx.catalog, ctx.args.skill)
    payload = engine.describe(ctx.catalog, skill)
    payload["exit_code"] = EXIT_OK
    return emit(payload, ctx.args, render_show)


def cmd_search(ctx: Context) -> int:
    results = engine.search(ctx.catalog, ctx.args.query, limit=ctx.args.limit)
    payload = {"query": ctx.args.query, "count": len(results), "results": results, "exit_code": EXIT_OK}
    return emit(payload, ctx.args, render_search)


def cmd_route(ctx: Context) -> int:
    result = engine.route(
        ctx.catalog,
        ctx.args.query,
        max_skills=ctx.args.max_skills,
        timeout=ctx.args.timeout,
        force_governance=not ctx.args.no_governance,
    )
    urgency = safety.assess_urgency(ctx.args.query, ctx.policy)
    result["urgency"] = urgency
    result["exit_code"] = EXIT_WARN if safety.requires_human_review(urgency) else EXIT_OK
    return emit(result, ctx.args, render_route)


def cmd_plan(ctx: Context) -> int:
    args = ctx.args
    plan = planner.build_plan(
        ctx.catalog,
        ctx.policy,
        args.query,
        max_skills=args.max_skills,
        timeout=args.timeout,
        profile=ctx.profile,
        registry=ctx.registry,
        region=args.region or "",
        force_governance=not args.no_governance,
    )
    markdown = planner.render_plan(plan, ctx.template, ctx.policy)
    review = safety.review_response(markdown, ctx.policy, user_text=args.query)
    plan["plan_review"] = {
        "verdict": review["verdict"],
        "counts": review["counts"],
        "findings": review["findings"],
    }
    written = None
    if args.out:
        written = planner.write_text(args.out, markdown, approve=args.approve)
    payload = {
        "plan": plan,
        "markdown": markdown,
        "output": written,
        "exit_code": review["exit_code"],
    }
    code = emit(payload, args, lambda p: p["markdown"])
    if written and not args.json:
        print(f"\n[plan written] {written['path']} ({written['bytes']} bytes)", file=sys.stderr)
    if review["findings"] and not args.json:
        print(
            f"[plan review] verdict {review['verdict'].upper()} - "
            + "; ".join(f"{f['rule_id']} ({f['severity']})" for f in review["findings"]),
            file=sys.stderr,
        )
    return code


def cmd_review(ctx: Context) -> int:
    args = ctx.args
    text = read_text_arg(args, "text", "input_file")
    user_text = ""
    if args.user_text or args.user_file:
        user_text = read_text_arg(args, "user_text", "user_file")
    result = safety.review_response(text, ctx.policy, user_text=user_text)
    if args.out:
        planner.write_text(args.out, json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                           approve=args.approve)
    return emit(result, args, render_review)


def cmd_urgency(ctx: Context) -> int:
    args = ctx.args
    text = read_text_arg(args, "text", "input_file")
    result = safety.assess_urgency(text, ctx.policy)
    result["exit_code"] = EXIT_WARN if safety.requires_human_review(result) else EXIT_OK
    return emit(result, args, render_urgency)


def cmd_redact(ctx: Context) -> int:
    args = ctx.args
    text = read_text_arg(args, "text", "input_file")
    result = safety.redact_pii(text, ctx.policy)
    if args.out:
        result["output"] = planner.write_text(args.out, result["text"] + "\n", approve=args.approve)
    result["exit_code"] = EXIT_OK
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result["text"])
        print(f"\n[redactions] {result['total']}: "
              f"{', '.join(f'{k} x{v}' for k, v in result['redactions'].items()) or 'none'}",
              file=sys.stderr)
    return EXIT_OK


def cmd_handoff(ctx: Context) -> int:
    args = ctx.args
    facts = read_text_arg(args, "facts", "input_file")
    packet = planner.build_handoff(
        facts,
        ctx.policy,
        profile=ctx.profile,
        questions=args.question,
        documents=args.document,
        urgency_level=args.urgency_level,
        recipient=args.recipient,
    )
    markdown = planner.render_handoff(packet)
    written = None
    if args.out:
        if not args.approve:
            raise SafetyError(
                "approval_required",
                "Writing a handoff summary requires --approve (approval gate A-EXPORT).",
                "Review the rendered summary, then re-run with --approve to write it.",
            )
        written = planner.write_text(args.out, markdown, approve=True)
    payload = {"packet": packet, "markdown": markdown, "output": written, "exit_code": EXIT_OK}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(markdown)
        if written:
            print(f"\n[handoff written] {written['path']} ({written['bytes']} bytes)", file=sys.stderr)
    return EXIT_OK


def cmd_resources(ctx: Context) -> int:
    args = ctx.args
    resources = planner.select_resources(
        ctx.registry,
        region=args.region or "",
        kinds=args.kind or None,
        include_global=not args.no_global,
        limit=args.limit,
    )
    payload = {
        "region": args.region or "",
        "kinds": args.kind or [],
        "count": len(resources),
        "resources": resources,
        "registry_note": ctx.registry.get("adopter_rules", []),
        "exit_code": EXIT_OK,
    }
    return emit(payload, args, render_resources)


def cmd_governance(ctx: Context) -> int:
    payload = {
        "summary": safety.governance_summary(ctx.policy),
        "self_check": safety.self_check(ctx.policy),
        "exit_code": EXIT_OK,
    }
    if not payload["self_check"]["passed"]:
        payload["exit_code"] = EXIT_BLOCK
    return emit(payload, ctx.args, render_governance)


def cmd_parse_source(ctx: Context) -> int:
    args = ctx.args
    if bool(args.text) == bool(args.input_file):
        raise CatalogError(
            "conflicting_input",
            "Pass exactly one of --text or --input-file for parse-source.",
            "Use --input-file for a saved .txt/.md/.html file, or --text for pasted content.",
        )
    if args.input_file:
        catalog = parser_mod.parse_file(args.input_file, fmt=args.format, category_hint=args.category or "")
    else:
        catalog = parser_mod.parse_source(
            args.text, fmt=args.format, source_name="pasted input", category_hint=args.category or ""
        )
    catalog = parser_mod.filter_categories(catalog, args.keep_category)
    if args.title:
        catalog["title"] = args.title
    payload = {
        "title": catalog["title"],
        "extraction": catalog["extraction"],
        "counts": catalog["counts"],
        "categories": [
            {"id": c["id"], "name": c["name"], "skill_count": c["skill_count"]} for c in catalog["categories"]
        ],
        "skills": catalog["skills"],
        "exit_code": EXIT_OK,
    }
    if args.out:
        written = engine.save_catalog(catalog, args.out, approve=args.approve)
        payload["output"] = written
    return emit(payload, args, render_parse_summary)


def cmd_validate(ctx: Context) -> int:
    args = ctx.args
    checks: list[dict] = []
    errors: list[str] = []

    def add(asset: str, valid: bool, detail: str) -> None:
        checks.append({"asset": asset, "valid": valid, "detail": detail})
        if not valid:
            errors.append(f"{asset}: {detail}")

    try:
        catalog = engine.load_catalog(ctx.catalog_path)
        counts = catalog["counts"]
        add(
            ctx.catalog_path.name,
            counts["skills"] > 0 and counts["categories"] > 0,
            f"{counts['skills']} skills in {counts['categories']} categories",
        )
    except (CatalogError, KeyError) as exc:
        add(ctx.catalog_path.name, False, getattr(exc, "message", str(exc)))

    try:
        policy = ctx.policy
        check = safety.self_check(policy)
        add(Path(ctx.args.policy).name, check["passed"], "policy valid; engine self-check " +
            ("passed" if check["passed"] else "failed"))
    except (SafetyError, KeyError) as exc:
        add(ctx.args.policy, False, getattr(exc, "message", str(exc)))

    try:
        template = planner.load_template(ctx.args.template)
        result = planner.validate_template(template)
        add(
            Path(ctx.args.template).name,
            result["valid"],
            "all placeholders present" if result["valid"]
            else "missing: " + ", ".join(result["missing_placeholders"]),
        )
    except CatalogError as exc:
        add(Path(ctx.args.template).name, False, exc.message)

    try:
        registry = ctx.registry
        entries = registry.get("resources", [])
        bad = [
            e.get("id")
            for e in entries
            if not e.get("id") or not e.get("name") or not isinstance(e.get("kinds"), list)
        ]
        add(
            Path(ctx.args.resources).name,
            bool(entries) and not bad,
            f"{len(entries)} entries" + (f"; malformed: {', '.join(map(str, bad))}" if bad else ""),
        )
    except CatalogError as exc:
        add(Path(ctx.args.resources).name, False, exc.message)

    try:
        schema_path = Path(args.schema)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        probe = planner.validate_instance(schema, planner.default_profile())
        add(schema_path.name, not probe, "schema accepts the default profile"
            + ("" if not probe else " (unexpected errors)"))
        if probe:
            errors.extend(probe)
    except (OSError, json.JSONDecodeError) as exc:
        add(Path(args.schema).name, False, f"unreadable schema: {exc}")

    profile_path = Path(args.profile)
    if profile_path.is_file():
        try:
            profile = planner.load_profile(profile_path, required=True)
            result = planner.validate_profile(profile)
            add(profile_path.name, result["valid"],
                "profile valid" if result["valid"] else "; ".join(result["errors"][:3]))
        except CatalogError as exc:
            add(profile_path.name, False, exc.message)
    else:
        add(profile_path.name, True, "not present (optional; created by profile init)")

    payload = {
        "valid": all(c["valid"] for c in checks),
        "checks": checks,
        "errors": errors,
        "exit_code": EXIT_OK if all(c["valid"] for c in checks) else EXIT_BLOCK,
    }
    return emit(payload, args, render_validate)


def cmd_self_test(ctx: Context) -> int:
    test_file = _HERE / "tools-self-test.py"
    if not test_file.is_file():
        print("self-test file missing: tools-self-test.py", file=sys.stderr)
        return EXIT_INPUT
    spec = importlib.util.spec_from_file_location("hs_self_test", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    verbosity = 1 if ctx.args.quiet else 2
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return EXIT_OK if result.wasSuccessful() else EXIT_BLOCK


# ---------------------------------------------------------------------------
# Profile command family
# ---------------------------------------------------------------------------
def cmd_profile(ctx: Context) -> int:
    args = ctx.args
    action = args.profile_action
    path = Path(args.profile)

    if action == "init":
        profile = planner.default_profile()
        profile["consent"]["granted"] = bool(args.consent)
        profile["consent"]["granted_at"] = planner.now_utc() if args.consent else ""
        if args.region:
            profile["context"]["region"] = args.region
        saved = planner.save_profile(profile, path, consent=bool(args.consent), approve=args.approve)
        payload = {
            "path": saved["path"],
            "profile": profile,
            "tone_directive": planner.tone_directive(profile),
            "personalization": planner.personalization_note(profile),
            "validation": planner.validate_profile(profile),
            "exit_code": EXIT_OK,
        }
        return emit(payload, args, render_profile)

    if action == "show":
        profile = planner.load_profile(path, required=bool(args.require))
        payload = {
            "path": str(path),
            "profile": profile,
            "tone_directive": planner.tone_directive(profile),
            "personalization": planner.personalization_note(profile),
            "validation": planner.validate_profile(profile),
            "exists": path.is_file(),
            "exit_code": EXIT_OK,
        }
        return emit(payload, args, render_profile)

    if action == "set":
        profile = planner.load_profile(path, required=True)
        if not args.consent:
            raise SafetyError(
                "consent_required",
                "Changing stored preferences requires --consent (approval gate A-MEMORY-WRITE).",
                "Re-run with --consent after the user agrees to store the preference.",
            )
        if not profile["consent"]["granted"]:
            raise SafetyError(
                "consent_not_granted",
                "consent.granted is false, so the profile cannot be changed.",
                "Run: profile set --key consent.granted --value true --consent",
            )
        for key, value in args.set_pair or []:
            planner.set_path(profile, key, planner.coerce_value(value))
        if args.key:
            planner.set_path(profile, args.key, planner.coerce_value(args.value))
        saved = planner.save_profile(profile, path, consent=True, approve=True)
        payload = {
            "path": saved["path"],
            "profile": profile,
            "tone_directive": planner.tone_directive(profile),
            "personalization": planner.personalization_note(profile),
            "validation": planner.validate_profile(profile),
            "exit_code": EXIT_OK,
        }
        return emit(payload, args, render_profile)

    if action == "remember":
        profile = planner.load_profile(path, required=True)
        if not args.consent:
            raise SafetyError(
                "consent_required",
                "Storing a memory entry requires --consent (approval gate A-MEMORY-WRITE).",
                "Re-run with --consent after the user agrees.",
            )
        entry = planner.remember(profile, args.key, args.value, source=args.source)
        saved = planner.save_profile(profile, path, consent=True, approve=True)
        payload = {
            "path": saved["path"],
            "stored": entry,
            "profile": profile,
            "tone_directive": planner.tone_directive(profile),
            "personalization": planner.personalization_note(profile),
            "exit_code": EXIT_OK,
        }
        return emit(payload, args, render_profile)

    if action == "forget":
        if not args.approve:
            raise SafetyError(
                "approval_required",
                "Deleting memory requires --approve (approval gate A-MEMORY-DELETE).",
                "Re-run with --approve to confirm the deletion.",
            )
        profile = planner.load_profile(path, required=True)
        result = planner.forget(profile, args.key, all_entries=args.all)
        saved = planner.save_profile(profile, path, consent=True, approve=True)
        payload = {
            "path": saved["path"],
            "forgotten": result,
            "profile": profile,
            "tone_directive": planner.tone_directive(profile),
            "personalization": planner.personalization_note(profile),
            "exit_code": EXIT_OK,
        }
        return emit(payload, args, render_profile)

    if action == "validate":
        profile = planner.load_profile(path, required=True)
        schema_path = Path(args.schema)
        schema = None
        if schema_path.is_file():
            try:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise CatalogError(
                    "schema_invalid_json",
                    f"Preference schema is not valid JSON: {schema_path} ({exc.msg})",
                ) from exc
        result = planner.validate_profile(profile, schema)
        payload = {
            "path": str(path),
            "validation": result,
            "profile": profile,
            "tone_directive": planner.tone_directive(profile),
            "personalization": planner.personalization_note(profile),
            "exit_code": EXIT_OK if result["valid"] else EXIT_BLOCK,
        }
        return emit(payload, args, render_profile)

    raise CatalogError("invalid_action", f"Unknown profile action: {action}")


def cmd_template_check(ctx: Context) -> int:
    template = planner.load_template(ctx.args.template)
    result = planner.validate_template(template)
    payload = {
        "template": ctx.args.template,
        "valid": result["valid"],
        "missing_placeholders": result["missing_placeholders"],
        "unknown_placeholders": result["unknown_placeholders"],
        "placeholders_present": result["placeholders_present"],
        "exit_code": EXIT_OK if result["valid"] else EXIT_BLOCK,
    }
    if ctx.args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Template: {payload['template']}")
        print("Valid: " + ("yes" if payload["valid"] else "no"))
        print("Placeholders present: " + ", ".join(payload["placeholders_present"]))
        if payload["missing_placeholders"]:
            print("Missing: " + ", ".join(payload["missing_placeholders"]))
        if payload["unknown_placeholders"]:
            print("Unknown: " + ", ".join(payload["unknown_placeholders"]))
    return payload["exit_code"]


def cmd_version(ctx: Context) -> int:
    info = {
        "tool": "humansphere-holistic-support",
        "version": TOOL_VERSION,
        "python": sys.version.split()[0],
        "entry_point": Path(__file__).name,
        "exit_code": EXIT_OK,
    }
    if ctx.args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"{info['tool']} {info['version']} (Python {info['python']}, entry {info['entry_point']})")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-humansphere.py",
        description=(
            "HumanSphere Holistic Support Skill Tool - route a stated human need to the smallest "
            "useful set of support skills, build a safety-first plan, review a draft response, and "
            "produce facts-only handoffs. No network access, standard library only."
        ),
        epilog=(
            "Exit codes: 0 ok | 1 warnings (review) | 2 blocked/invalid | 3 approval required | "
            "4 input error | 5 timeout. Start with the capabilities command."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit structured JSON instead of text")
    parser.add_argument("--quiet", action="store_true", help="reduced verbosity (self-test)")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG),
                        help="catalog JSON path (default: asset-skill-catalog.json beside this file)")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY),
                        help="safety policy JSON path (default: asset-safety-policy.json)")
    parser.add_argument("--resources", default=str(DEFAULT_RESOURCES),
                        help="resource registry JSON path (default: asset-support-resources.json)")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE),
                        help="plan template path (default: asset-plan-template.md)")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA),
                        help="preference profile schema path (default: asset-preference-profile.schema.json)")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE),
                        help="preference profile path (created by the profile init command)")
    parser.add_argument("--timeout", type=float, default=engine.LIMITS["default_timeout_seconds"],
                        help="per-operation deadline in seconds (default 10)")
    parser.add_argument("--max-skills", type=int, default=engine.LIMITS["default_max_skills"],
                        help="maximum skills to select (default 5, hard cap 8)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, help_text: str, func) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text, description=help_text,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
        sp.set_defaults(func=func)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit structured JSON (same as the global --json)")
        sp.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="reduced verbosity")
        return sp

    sp = add("capabilities", "Capability list: every category and skill, with risk flags", cmd_capabilities)
    sp.add_argument("--category", help="limit to one category name or id")

    sp = add("list", "List skills with ids, categories, and risk levels", cmd_list)
    sp.add_argument("--category", help="limit to one category name or id")
    sp.add_argument("--risk", choices=["standard", "elevated", "high"], help="limit to one risk level")

    sp = add("show", "Show one skill by id, slug, or name", cmd_show)
    sp.add_argument("--skill", required=True, help="numeric id, slug, or name")

    sp = add("search", "Keyword search across the catalog", cmd_search)
    sp.add_argument("--query", required=True)
    sp.add_argument("--limit", type=int, default=10)

    sp = add("route", "Select the smallest useful set of skills for a stated need", cmd_route)
    sp.add_argument("--query", required=True)
    sp.add_argument("--max-skills", type=int, default=argparse.SUPPRESS,
                    help="override the global --max-skills for this call")
    sp.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                    help="override the global --timeout for this call")
    sp.add_argument("--no-governance", action="store_true",
                    help="do not pin the safety-reviewer skill into the selection")

    sp = add("plan", "Build and render a full safety-first support plan", cmd_plan)
    sp.add_argument("--query", required=True)
    sp.add_argument("--max-skills", type=int, default=argparse.SUPPRESS,
                    help="override the global --max-skills for this call")
    sp.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                    help="override the global --timeout for this call")
    sp.add_argument("--out", help="write the Markdown plan to this path")
    sp.add_argument("--approve", action="store_true", help="allow overwriting an existing --out file")
    sp.add_argument("--region", help="region code for resource filtering (e.g. ph, us, uk, eu, global)")
    sp.add_argument("--no-governance", action="store_true")

    sp = add("review", "Review a drafted response against the governance policy", cmd_review)
    sp.add_argument("--text", help="draft response text")
    sp.add_argument("--input-file", help="file containing the draft response")
    sp.add_argument("--user-text", help="what the user said (enables missed-urgency checks)")
    sp.add_argument("--user-file", help="file containing the user's message")
    sp.add_argument("--out", help="write the JSON review report to this path")
    sp.add_argument("--approve", action="store_true", help="allow overwriting --out")

    sp = add("urgency", "Assess urgency signals in user text", cmd_urgency)
    sp.add_argument("--text")
    sp.add_argument("--input-file")

    sp = add("redact", "Mask personal identifiers before sharing text", cmd_redact)
    sp.add_argument("--text")
    sp.add_argument("--input-file")
    sp.add_argument("--out", help="write the redacted text to this path")
    sp.add_argument("--approve", action="store_true")

    sp = add("handoff", "Build a facts-only handoff or referral summary", cmd_handoff)
    sp.add_argument("--facts", help="the facts to summarise")
    sp.add_argument("--input-file", help="file containing the facts")
    sp.add_argument("--question", action="append", default=[], help="question for the recipient (repeatable)")
    sp.add_argument("--document", action="append", default=[], help="document to bring (repeatable)")
    sp.add_argument("--urgency-level", help="override the detected urgency level")
    sp.add_argument("--recipient", default="clinician, hotline, or responder",
                    help="who receives the summary")
    sp.add_argument("--out", help="write the Markdown summary (requires --approve)")
    sp.add_argument("--approve", action="store_true", help="confirm the export")

    sp = add("resources", "Query the official-source resource registry", cmd_resources)
    sp.add_argument("--region", help="region code (ph, us, uk, eu, au, ca, in, global)")
    sp.add_argument("--kind", action="append", default=[], help="resource kind filter (repeatable)")
    sp.add_argument("--no-global", action="store_true", help="exclude global entries")
    sp.add_argument("--limit", type=int, default=8)

    add("governance", "Show safety policy, scope boundaries, and approval gates", cmd_governance)

    sp = add("parse-source", "Extract skills from pasted text, Markdown, or copied HTML", cmd_parse_source)
    sp.add_argument("--text", help="pasted catalog text")
    sp.add_argument("--input-file", help="path to a .txt, .md, or .html catalog")
    sp.add_argument("--format", default="auto", choices=["auto", "text", "markdown", "md", "html"])
    sp.add_argument("--category", help="category name to apply to every skill in this input")
    sp.add_argument("--keep-category", action="append", default=[],
                    help="keep only this category (repeatable)")
    sp.add_argument("--title", help="override the derived catalog title")
    sp.add_argument("--out", help="write a catalog JSON asset (requires --approve if it exists)")
    sp.add_argument("--approve", action="store_true", help="allow overwriting --out")

    add("validate", "Validate every asset and policy before use", cmd_validate)

    sp = add("template-check", "Check the plan template placeholders", cmd_template_check)

    add("self-test", "Run the bundled test suite", cmd_self_test)
    add("version", "Print version information", cmd_version)

    sp = add("profile", "Consent-based preference profile: init/show/set/remember/forget/validate",
             cmd_profile)
    profile_sub = sp.add_subparsers(dest="profile_action", metavar="<action>", required=True)
    p_init = profile_sub.add_parser("init", help="create a profile (writes only with --consent)")
    p_init.add_argument("--consent", action="store_true")
    p_init.add_argument("--approve", action="store_true", help="overwrite an existing profile")
    p_init.add_argument("--region", help="region code for resource filtering")
    p_show = profile_sub.add_parser("show", help="print the stored profile")
    p_show.add_argument("--require", action="store_true", help="fail if the profile does not exist")
    p_set = profile_sub.add_parser("set", help="set one or more preference keys")
    p_set.add_argument("--key", help="dotted key, e.g. preferences.tone")
    p_set.add_argument("--value", help="value; JSON, comma lists, and true/false are coerced")
    p_set.add_argument("--set", dest="set_pair", nargs=2, action="append", metavar=("KEY", "VALUE"),
                       help="set a key/value pair (repeatable)")
    p_set.add_argument("--consent", action="store_true", required=False)
    p_remember = profile_sub.add_parser("remember", help="store one consent-based memory entry")
    p_remember.add_argument("--key", required=True)
    p_remember.add_argument("--value", required=True)
    p_remember.add_argument("--source", default="user-stated",
                            choices=["user-stated", "user-corrected", "user-imported"])
    p_remember.add_argument("--consent", action="store_true")
    p_forget = profile_sub.add_parser("forget", help="delete one or all memory entries")
    p_forget.add_argument("--key", help="memory key to delete")
    p_forget.add_argument("--all", action="store_true", help="delete every memory entry")
    p_forget.add_argument("--approve", action="store_true", help="required confirmation")
    p_validate = profile_sub.add_parser("validate", help="validate the profile against the schema")
    for action_parser in (p_init, p_show, p_set, p_remember, p_forget, p_validate):
        action_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                                   help="emit structured JSON (same as the global --json)")
        action_parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                                   help="reduced verbosity")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        print("\nStart with: python3 humansphere-holistic-support/tools-humansphere.py capabilities")
        return EXIT_OK
    ctx = Context(args)
    try:
        return int(args.func(ctx))
    except CatalogError as exc:
        payload = exc.as_dict()
        payload["exit_code"] = EXIT_TIMEOUT if exc.code == "timeout" else EXIT_INPUT
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(f"ERROR [{exc.code}] {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"HINT: {exc.hint}", file=sys.stderr)
        return payload["exit_code"]
    except SafetyError as exc:
        payload = exc.as_dict()
        payload["exit_code"] = EXIT_APPROVAL if exc.code in (
            "approval_required", "consent_required", "consent_not_granted"
        ) else EXIT_INPUT
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(f"BLOCKED [{exc.code}] {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"HINT: {exc.hint}", file=sys.stderr)
        return payload["exit_code"]
    except KeyboardInterrupt:
        print("Cancelled by user. No partial files are left behind (atomic writes).", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
