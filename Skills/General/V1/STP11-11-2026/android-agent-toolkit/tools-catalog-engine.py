#!/usr/bin/env python3
"""android-agent-toolkit :: catalog engine (module 6 of 7).

Serves the packaged skill catalog (asset-catalog-skills.json, 324 entries across 24
categories) and answers capability questions:

  capabilities [--category N] [--requires X]   list every skill with its toolkit command
  catalog list [--search T] [--category N]     filtered listing
  catalog search TERM                          alias for list --search
  catalog show <skill-id>                      full record for one skill (e.g. 03.02)
  catalog contract <skill-id>                  inputs, outputs, requirements, approval needs
  catalog stats                                counts by category, tier and requirement
  catalog validate                             structural and routing checks over every entry
"""

import re

import aatk_core as core

_CACHE = {}

APPROVAL_GROUPS = {"workspace", "preserve", "integrity"}
APPROVAL_HINTS = {
    ("integrity", "pins-init"): "may overwrite the pin store",
    ("integrity", "pins-add"): "writes the pin store",
    ("preserve", "package"): "writes a release archive",
    ("preserve", "snapshot"): "writes into a directory (overwrite needs --yes)",
    ("workspace", "clean"): "deletes generated files only when --yes is given",
    ("tool", "run"): "non-read-only tool invocations need --yes",
    ("package", "normalize"): "writes extracted components",
}


def catalog():
    if "data" not in _CACHE:
        data = core.load_json(core.resolve_asset("asset-catalog-skills.json"), "skill catalog")
        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            raise core.ToolkitError("E_CONFIG", "asset-catalog-skills.json is malformed")
        _CACHE["data"] = data
    return _CACHE["data"]


def _skills():
    return catalog()["skills"]


def find_skill(skill_id):
    for s in _skills():
        if s["id"] == skill_id:
            return s
    raise core.ToolkitError("E_NOT_FOUND", "No skill with id '%s'" % skill_id,
                            hint="Use: catalog list --search <term>  (ids look like 03.02)")


def _row(s):
    return {"id": s["id"], "category": s["category"], "name": s["name"], "tier": s["tier"],
            "requires": s.get("requires") or [], "toolkit_command": s["toolkit_command"]}


def _validate_filters(skills, category=None, requires=None):
    """Reject out-of-range categories and unknown requirement tokens instead of returning an empty list."""
    if category is not None:
        valid = sorted({s["category"] for s in skills})
        if category not in valid:
            raise core.ToolkitError("E_USAGE", "category must be one of %d-%d; got %s" % (valid[0], valid[-1], category),
                                    hint="Run: capabilities   (lists all 24 categories)")
    if requires:
        vocab = sorted({r for s in skills for r in (s.get("requires") or [])})
        if not any(requires.lower() in r.lower() for r in vocab):
            raise core.ToolkitError("E_USAGE", "no skill has requirement '%s'" % requires,
                                    hint="Valid requirements: " + ", ".join(vocab))


def _filter(skills, category=None, requires=None, search=None):
    out = skills
    if category is not None:
        out = [s for s in out if s["category"] == category]
    if requires:
        out = [s for s in out if any(requires.lower() in r.lower() for r in (s.get("requires") or []))]
    if search:
        t = search.lower()
        out = [s for s in out if t in (s["name"] + " " + s["description"] + " " + s["id"] + " " +
                                       s["toolkit_command"]).lower()]
    return out


def _text_rows(rows):
    return ["%-6s %-38s %-10s %s" % (r["id"], r["name"][:38], r["tier"], r["toolkit_command"]) for r in rows]


def cmd_capabilities(args, cfg):
    _validate_filters(_skills(), category=args.category, requires=args.requires)
    rows = [_row(s) for s in _filter(_skills(), category=args.category, requires=args.requires)]
    data = {"count": len(rows), "skills": rows, "_text": _text_rows(rows)}
    return core.ok("capabilities", data)


def cmd_catalog_list(args, cfg):
    _validate_filters(_skills(), category=args.category)
    rows = [_row(s) for s in _filter(_skills(), category=args.category, search=args.search)]
    return core.ok("catalog list", {"count": len(rows), "skills": rows, "_text": _text_rows(rows)})


def cmd_catalog_search(args, cfg):
    rows = [_row(s) for s in _filter(_skills(), search=args.term)]
    return core.ok("catalog search", {"count": len(rows), "skills": rows, "_text": _text_rows(rows)})


def cmd_catalog_show(args, cfg):
    return core.ok("catalog show", find_skill(args.skill_id))


def _inputs_from_command(cmd):
    inputs = []
    for m in re.finditer(r"<([^>]+)>", cmd):
        inputs.append(m.group(1))
    for m in re.finditer(r"--([a-z][a-z0-9-]*)", cmd):
        inputs.append("--" + m.group(1))
    return sorted(set(inputs))


def cmd_catalog_contract(args, cfg):
    s = find_skill(args.skill_id)
    group, sub = (s["toolkit_command"].split() + [""])[:2]
    needs = []
    if (group, sub) in APPROVAL_HINTS:
        needs.append(APPROVAL_HINTS[(group, sub)])
    if group == "tool" and sub == "run":
        needs.append("only read-only argument prefixes run without --yes")
    if s["tier"] in ("adapter", "composite") and s.get("requires"):
        needs.append("requires: " + ", ".join(s["requires"]))
    data = {"id": s["id"], "name": s["name"], "tier": s["tier"], "command": s["toolkit_command"],
            "inputs": _inputs_from_command(s["toolkit_command"]), "requires": s.get("requires") or [],
            "approval_and_requirements": needs,
            "output": "JSON envelope {ok, command, status, data, warnings} (use --json)",
            "exit_codes": {"0": "ok", "1": "error", "2": "usage/input", "3": "approval required", "4": "requirement missing"},
            "description": s["description"]}
    return core.ok("catalog contract", data)


def cmd_catalog_stats(args, cfg):
    skills = _skills()
    by_cat, by_tier, req = {}, {}, {}
    for s in skills:
        by_cat[str(s["category"])] = by_cat.get(str(s["category"]), 0) + 1
        by_tier[s["tier"]] = by_tier.get(s["tier"], 0) + 1
        for r in s.get("requires") or []:
            req[r] = req.get(r, 0) + 1
    data = {"skills": len(skills), "categories": catalog().get("category_count"), "by_category": by_cat,
            "by_tier": by_tier, "by_requirement": req}
    data["_text"] = ["skills: %d in %d categories" % (len(skills), len(by_cat))] + \
                    ["tier %-10s %d" % kv for kv in sorted(by_tier.items())] + \
                    ["requires %-20s %d" % kv for kv in sorted(req.items())]
    return core.ok("catalog stats", data)


def cmd_catalog_validate(args, cfg):
    cat = catalog()
    skills = _skills()
    errors, warnings = [], []
    ids = [s["id"] for s in skills]
    if len(ids) != len(set(ids)):
        errors.append("duplicate skill ids")
    if len(skills) != cat.get("skill_count") or len(skills) != 324:
        errors.append("expected 324 skills, found %d" % len(skills))
    cats = {c["category"] for c in cat.get("categories", [])}
    if len(cats) != 24:
        errors.append("expected 24 categories, found %d" % len(cats))
    tiers = set(cat.get("tiers", {}).keys())
    routes = set(getattr(args, "_routes", set()))
    unrouted = []
    for s in skills:
        if s["tier"] not in tiers:
            errors.append("%s: unknown tier %s" % (s["id"], s["tier"]))
        parts = s["toolkit_command"].split()
        if parts and parts[0] == "capabilities":
            continue  # top-level command with flags, routed separately
        if len(parts) < 2:
            errors.append("%s: command has no subcommand" % s["id"])
            continue
        if routes and (parts[0], parts[1]) not in routes:
            unrouted.append({"id": s["id"], "command": s["toolkit_command"]})
    if unrouted:
        errors.append("%d skill commands have no registered route" % len(unrouted))
    data = {"skills": len(skills), "categories": len(cats), "tiers": sorted(tiers),
            "unrouted": unrouted[:50], "errors": errors, "warnings": warnings,
            "_text": ["skills %d, categories %d" % (len(skills), len(cats))] +
                     (["ERROR: " + e for e in errors] or ["all skill commands resolve to registered routes"])}
    if errors:
        raise core.ToolkitError("E_FAILED", "Catalog validation failed (%d issue(s))" % len(errors), data=data)
    return core.ok("catalog validate", data)


COMMANDS = {
    ("catalog", "list"): cmd_catalog_list,
    ("catalog", "search"): cmd_catalog_search,
    ("catalog", "show"): cmd_catalog_show,
    ("catalog", "contract"): cmd_catalog_contract,
    ("catalog", "stats"): cmd_catalog_stats,
    ("catalog", "validate"): cmd_catalog_validate,
    ("capabilities", None): cmd_capabilities,
}
