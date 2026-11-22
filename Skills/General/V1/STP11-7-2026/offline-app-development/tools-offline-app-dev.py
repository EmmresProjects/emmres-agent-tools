#!/usr/bin/env python3
"""
tools-offline-app-dev.py — Unified engine for the Full Offline App Development Skill Catalog.

Catalog Edition 1.0 (November 2026): 27 categories, 1095 skill concepts, 7 description
archetypes (implement / evaluate / adapter / build / operate / plan / select-configure).

This tool implements the catalog as a working, reusable Skill Tool component:

  capabilities   List this tool's capabilities and catalog coverage (built-in capability list)
  list           List catalog categories and skills
  search         Full-text search across all 1095 skills
  show           Show one skill by ref (e.g. 6.18) or exact name
  brief          Generate a complete capability brief (work order) for any catalog skill
  matrix         Target platform decision matrix (weighted, configurable scoring)
  profile        Emit a structured target platform/framework/arch profile
  plan           Generate an owned, verifiable plan from a built-in template
  adr            Architecture Decision Record manager (new/list/show/supersede)
  risk           Risk / offline-constraint register (add/list/update/close/export)
  accept         Acceptance evaluation with pass/warn/fail verdict from recorded results
  verify         Offline verification: tool presence, --probe versions, SHA-256 bundle manifests

Runtime: Python 3.8+ standard library only. No network access is used or required.
Assets are resolved relative to this file's directory, or OAD_ASSETS_DIR if set.
Exit codes: 0 OK; 1 runtime failure / verification failed; 2 input or usage error;
  accept only: 3 = verdict WARN, 4 = verdict FAIL. See knowledge-readme.md.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "1.0.0"
ASSET_DIR = Path(os.environ.get("OAD_ASSETS_DIR") or Path(__file__).resolve().parent)

CATALOG_ASSET = "asset-skill-catalog.json"
PROFILES_ASSET = "asset-platform-profiles.json"
MATRIX_ASSET = "asset-decision-matrix.json"
PLANS_ASSET = "asset-plan-templates.json"
ACCEPT_ASSET = "asset-acceptance-checklist.json"
BRIEF_ASSET = "asset-brief-templates.json"
ADR_TEMPLATE_ASSET = "asset-adr-template.md"

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_INPUT = 2
EXIT_ACCEPT_WARN = 3
EXIT_ACCEPT_FAIL = 4

_FORMATS = ("text", "json", "md")


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def eprint(msg):
    print(msg, file=sys.stderr)


def emit_error(message, hint=None, fmt="text", code=EXIT_INPUT):
    payload = {"ok": False, "error": {"message": message, "hint": hint or "", "code": code}}
    if fmt == "json":
        eprint(json.dumps(payload, ensure_ascii=False))
    else:
        eprint("error: %s" % message)
        if hint:
            eprint("hint: %s" % hint)
    return code


def load_asset(name):
    path = ASSET_DIR / name
    if not path.is_file():
        raise AssetError("missing asset: %s (looked in %s)" % (name, ASSET_DIR))
    try:
        if name.endswith(".json"):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except json.JSONDecodeError as exc:
        raise AssetError("asset %s is not valid JSON: %s" % (name, exc))
    except OSError as exc:
        raise AssetError("cannot read asset %s: %s" % (name, exc))


class AssetError(Exception):
    """Operational error. `code` is EXIT_INPUT (2) for user-input problems and
    EXIT_RUNTIME (1) for internal/IO failures; emit_error/main honor it."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = EXIT_RUNTIME if code is None else code


def load_json_file(path, what):
    p = Path(path)
    if not p.is_file():
        raise AssetError("%s not found: %s" % (what, path), EXIT_INPUT)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise AssetError("%s is not valid JSON (%s): %s" % (what, path, exc), EXIT_INPUT)
    except OSError as exc:
        raise AssetError("cannot read %s (%s): %s" % (what, path, exc), EXIT_INPUT)


def atomic_write(path, content, force=False):
    p = Path(path)
    if p.exists() and not force:
        raise AssetError("refusing to overwrite existing file: %s (use --force)" % p, EXIT_INPUT)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent or "."))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_or_print(content, output, force=False):
    if output:
        atomic_write(output, content, force=force)
        return "written: %s" % output
    print(content)
    return None


def render(payload, fmt, text_renderer):
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return text_renderer()


def validate_format(fmt, allowed=_FORMATS):
    if fmt not in allowed:
        raise AssetError("unsupported format %r (choose from: %s)" % (fmt, ", ".join(allowed)), EXIT_INPUT)


def iter_skills(catalog):
    for cat in catalog["categories"]:
        for skill in cat["skills"]:
            yield cat, skill


def find_skill(catalog, ref):
    """Resolve a skill ref 'N.M' or an exact skill name (case-insensitive)."""
    ref = ref.strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", ref)
    if m:
        cat_id, num = int(m.group(1)), int(m.group(2))
        for cat in catalog["categories"]:
            if cat["id"] == cat_id:
                for skill in cat["skills"]:
                    if skill["num"] == num:
                        return cat, skill
                break
        raise AssetError("no skill at ref %s" % ref, EXIT_INPUT)
    lowered = ref.lower()
    matches = [(c, s) for c, s in iter_skills(catalog) if s["name"].lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        # substring fallback to help the user
        loose = [(c, s) for c, s in iter_skills(catalog) if lowered in s["name"].lower()]
        if len(loose) == 1:
            return loose[0]
        if loose:
            names = ", ".join("%s %s" % (s["ref"], s["name"]) for _, s in loose[:8])
            raise AssetError("ambiguous skill name %r; close matches: %s" % (ref, names), EXIT_INPUT)
        raise AssetError("no skill matching %r; use `list` or `search` to discover refs" % ref, EXIT_INPUT)
    names = ", ".join("%s %s" % (s["ref"], s["name"]) for _, s in matches)
    raise AssetError("ambiguous skill name %r: %s" % (ref, names), EXIT_INPUT)


def archetype_names(catalog):
    return sorted(catalog.get("archetypes", {}).keys())


# ---------------------------------------------------------------------------
# capabilities / catalog browsing
# ---------------------------------------------------------------------------

TOOL_CAPABILITIES = [
    ("capabilities", "List this tool's capabilities and catalog coverage"),
    ("list", "List catalog categories and skills (--category, --archetype, --count-only)"),
    ("search", "Full-text search across all catalog skills (AND semantics)"),
    ("show", "Show one skill by ref (N.M) or exact name"),
    ("brief", "Generate a complete capability brief (work order) for any catalog skill"),
    ("matrix", "Target platform decision matrix (weighted, configurable scoring)"),
    ("profile", "Emit a structured target platform/framework/architecture profile"),
    ("plan", "Generate an owned, verifiable plan from a built-in template (--list to see them)"),
    ("adr", "Architecture Decision Record manager (new / list / show / supersede)"),
    ("risk", "Risk and offline-constraint register (add / list / update / close / export)"),
    ("accept", "Acceptance evaluation with pass/warn/fail verdict from recorded results"),
    ("verify", "Offline verification: tool presence, optional --probe versions, SHA-256 bundle manifests"),
]


def cmd_capabilities(args):
    catalog = load_asset(CATALOG_ASSET)
    per_arch = {}
    for _, skill in iter_skills(catalog):
        per_arch[skill["archetype"]] = per_arch.get(skill["archetype"], 0) + 1
    payload = {
        "ok": True,
        "tool": "tools-offline-app-dev.py",
        "version": TOOL_VERSION,
        "catalog": {
            "source": catalog.get("source"),
            "edition": catalog.get("edition"),
            "categories": catalog.get("total_categories"),
            "skills": catalog.get("total_skills"),
            "archetypes": per_arch,
        },
        "capabilities": [{"command": c, "description": d} for c, d in TOOL_CAPABILITIES],
        "asset_dir": str(ASSET_DIR),
        "runtime": "python3 >= 3.8, standard library only, no network access",
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    lines = [
        "tools-offline-app-dev.py v%s — Full Offline App Development Skill Catalog engine" % TOOL_VERSION,
        "Catalog: %s (edition %s)" % (catalog.get("source"), catalog.get("edition")),
        "Archetypes: %s" % ", ".join("%s=%d" % kv for kv in sorted(per_arch.items())),
        "",
        "Capabilities:",
    ]
    for c, d in TOOL_CAPABILITIES:
        lines.append("  %-12s  %s" % (c, d))
    lines += ["", "Run `%s <command> --help` for per-command options." % Path(sys.argv[0]).name]
    print("\n".join(lines))
    return EXIT_OK


def _filter_skills(catalog, category=None, archetype=None):
    cats = catalog["categories"]
    if category:
        cl = str(category).strip().lower()
        cats = [c for c in cats if str(c["id"]) == cl or cl in c["name"].lower()]
        if not cats:
            raise AssetError("no category matching %r" % category, EXIT_INPUT)
    out = []
    for c in cats:
        for s in c["skills"]:
            if archetype and s["archetype"] != archetype:
                continue
            out.append((c, s))
    return out


def cmd_list(args):
    catalog = load_asset(CATALOG_ASSET)
    if archetype_names(catalog) and args.archetype and args.archetype not in catalog["archetypes"]:
        return emit_error("unknown archetype %r (choose from: %s)" % (
            args.archetype, ", ".join(archetype_names(catalog))), fmt=args.format)
    if args.count_only:
        if args.category or args.archetype:
            n = len(_filter_skills(catalog, args.category, args.archetype))
        else:
            n = catalog["total_skills"]
        print(n)
        return EXIT_OK
    if args.format == "json":
        rows = _filter_skills(catalog, args.category, args.archetype)
        print(json.dumps({"ok": True, "count": len(rows),
                          "skills": [dict(s, category=c["name"], category_id=c["id"]) for c, s in rows]},
                         ensure_ascii=False, indent=2))
        return EXIT_OK
    if not args.category and not args.archetype:
        print("Catalog categories (%d):\n" % len(catalog["categories"]))
        for c in catalog["categories"]:
            print("  %2d  %-70s %d skills" % (c["id"], c["name"], c["count"]))
        print("\nTotal: %d skills. Use --category N or --archetype NAME to list skills." % catalog["total_skills"])
        return EXIT_OK
    rows = _filter_skills(catalog, args.category, args.archetype)
    print("Skills (%d):\n" % len(rows))
    for c, s in rows:
        print("  %-6s [%s] %s" % (s["ref"], s["archetype"], s["name"]))
    return EXIT_OK


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def cmd_search(args):
    catalog = load_asset(CATALOG_ASSET)
    terms = _TOKEN_RE.findall(args.query.lower())
    if not terms:
        return emit_error("query %r contains no searchable terms" % args.query, fmt=args.format)
    if args.archetype and args.archetype not in catalog["archetypes"]:
        return emit_error("unknown archetype %r (choose from: %s)" % (
            args.archetype, ", ".join(archetype_names(catalog))), fmt=args.format)
    matches = []
    for cat, skill in _filter_skills(catalog, args.category, args.archetype):
        hay = ("%s %s %s %s" % (skill["name"], skill["description"], cat["name"], skill["archetype"])).lower()
        if all(t in hay for t in terms):
            score = sum(hay.count(t) for t in terms) + (10 if all(t in skill["name"].lower() for t in terms) else 0)
            matches.append((score, cat, skill))
    matches.sort(key=lambda m: (-m[0], m[1]["id"], m[2]["num"]))
    matches = matches[: args.limit]
    if args.format == "json":
        print(json.dumps({"ok": True, "query": args.query, "count": len(matches),
                          "matches": [dict(s, category=c["name"], category_id=c["id"]) for _, c, s in matches]},
                         ensure_ascii=False, indent=2))
        return EXIT_OK
    if not matches:
        print("No skills match %r." % args.query)
        return EXIT_OK
    print("Matches for %r (%d shown, limit %d):\n" % (args.query, len(matches), args.limit))
    for _, cat, skill in matches:
        print("  %-6s [%s] %s  —  %s" % (skill["ref"], skill["archetype"], skill["name"], cat["name"]))
    return EXIT_OK


def cmd_show(args):
    catalog = load_asset(CATALOG_ASSET)
    cat, skill = find_skill(catalog, args.ref)
    payload = dict(skill, category=cat["name"], category_id=cat["id"],
                   archetype_contract=catalog["archetypes"][skill["archetype"]]["contract"])
    if args.format == "json":
        print(json.dumps({"ok": True, "skill": payload}, ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.format == "md":
        print("## [%s] %s\n\n- **Category:** %s (%d)\n- **Archetype:** %s\n- **Contract:** %s\n\n%s\n" % (
            skill["ref"], skill["name"], cat["name"], cat["id"],
            skill["archetype"], payload["archetype_contract"], skill["description"]))
        return EXIT_OK
    print("ref:         %s" % skill["ref"])
    print("name:        %s" % skill["name"])
    print("category:    %s (%d)" % (cat["name"], cat["id"]))
    print("archetype:   %s" % skill["archetype"])
    print("contract:    %s" % payload["archetype_contract"])
    print("description: %s" % skill["description"])
    return EXIT_OK


# ---------------------------------------------------------------------------
# brief generation
# ---------------------------------------------------------------------------

def cmd_brief(args):
    catalog = load_asset(CATALOG_ASSET)
    templates = load_asset(BRIEF_ASSET)
    cat, skill = find_skill(catalog, args.ref)
    arch = skill["archetype"]
    tmpl = templates["archetypes"].get(arch)
    if tmpl is None:
        return emit_error("no brief template for archetype %r" % arch, fmt=args.format)

    def fill(text):
        return (text.replace("{ref}", skill["ref"]).replace("{name}", skill["name"])
                    .replace("{category}", cat["name"]).replace("{description}", skill["description"]))

    sections = [{"heading": s["heading"], "body": fill(s["body"])} for s in tmpl["sections"]]
    payload = {
        "ok": True,
        "skill": dict(skill, category=cat["name"], category_id=cat["id"]),
        "archetype": arch,
        "archetype_label": tmpl["label"],
        "contract": tmpl["contract"],
        "title": "Capability Brief — %s (%s)" % (skill["name"], skill["ref"]),
        "sections": sections,
        "generated": utc_now(),
        "note": "Deterministic work order generated by tools-offline-app-dev.py; fill in project specifics, keep the contract sections intact.",
    }
    if args.format == "json":
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        lines = ["# %s" % payload["title"], "",
                 "- **Catalog ref:** %s" % skill["ref"],
                 "- **Category:** %s" % cat["name"],
                 "- **Archetype:** %s — %s" % (tmpl["label"], arch),
                 "- **Contract:** %s" % tmpl["contract"],
                 "- **Generated:** %s" % payload["generated"], ""]
        for s in sections:
            lines += ["## %s" % s["heading"], "", s["body"], ""]
        lines.append("*Catalog description: %s*" % skill["description"])
        content = "\n".join(lines)
    try:
        msg = write_or_print(content + ("\n" if not content.endswith("\n") else ""), args.output, args.force)
        if msg:
            print(msg)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    return EXIT_OK


# ---------------------------------------------------------------------------
# decision matrix
# ---------------------------------------------------------------------------

def _load_matrix(args):
    matrix = load_asset(MATRIX_ASSET)
    criteria = {c["id"]: dict(c) for c in matrix["criteria"]}
    scores = {t: dict(s) for t, s in matrix["scores"].items()}
    if args.criteria:
        override = load_json_file(args.criteria, "criteria override file")
        if "criteria" in override:
            for c in override["criteria"]:
                if "id" not in c:
                    raise AssetError("each override criterion needs an 'id'", EXIT_INPUT)
                if c["id"] in criteria:
                    criteria[c["id"]].update({k: v for k, v in c.items() if k in ("weight", "label", "guidance")})
                else:
                    criteria[c["id"]] = {"id": c["id"], "label": c.get("label", c["id"]),
                                         "weight": float(c.get("weight", 0)), "guidance": c.get("guidance", "")}
        if "scores" in override:
            for target, sc in override["scores"].items():
                scores.setdefault(target, {}).update({k: int(v) for k, v in sc.items()})
        if "drop_criteria" in override:
            for cid in override["drop_criteria"]:
                criteria.pop(cid, None)
    total_weight = sum(float(c["weight"]) for c in criteria.values())
    if total_weight <= 0:
        raise AssetError("total criteria weight must be > 0", EXIT_INPUT)
    return criteria, scores, matrix


def cmd_matrix(args):
    try:
        criteria, scores, matrix = _load_matrix(args)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    targets = [t.strip() for t in args.targets.split(",")] if args.targets else sorted(scores)
    missing = [t for t in targets if t not in scores]
    if missing:
        return emit_error("no scores for target(s): %s (known: %s)" % (
            ", ".join(missing), ", ".join(sorted(scores))), fmt=args.format)
    rows = []
    for t in targets:
        sc = scores[t]
        missing_crit = [cid for cid in criteria if cid not in sc]
        if missing_crit:
            return emit_error("scores for %r missing criteria: %s" % (t, ", ".join(missing_crit)), fmt=args.format)
        total_weight = sum(float(c["weight"]) for c in criteria.values())
        weighted = sum(float(c["weight"]) * float(sc[cid]) for cid, c in criteria.items()) / total_weight
        rows.append({"target": t, "weighted_score": round(weighted, 3),
                     "scores": {cid: sc[cid] for cid in criteria}})
    rows.sort(key=lambda r: (-r["weighted_score"], r["target"]))
    payload = {
        "ok": True,
        "criteria": [{"id": c["id"], "label": c["label"], "weight": c["weight"]} for c in criteria.values()],
        "ranking": rows,
        "scale": matrix.get("score_scale"),
        "decision_note": matrix.get("decision_note"),
        "generated": utc_now(),
    }
    if args.format == "json":
        out = json.dumps(payload, ensure_ascii=False, indent=2)
    elif args.format == "md":
        lines = ["# Target Platform Decision Matrix", "",
                 "| Rank | Target | Weighted score (0-5) |", "|---|---|---|"]
        for i, r in enumerate(rows, 1):
            lines.append("| %d | %s | %.3f |" % (i, r["target"], r["weighted_score"]))
        lines += ["", "**Criteria weights (raw):** " + ", ".join("%s %s" % (c["id"], c["weight"]) for c in criteria.values()),
                  "", payload["decision_note"]]
        out = "\n".join(lines)
    else:
        lines = ["Target platform decision matrix (weights configurable via --criteria)\n"]
        lines.append("  %-24s %s" % ("TARGET", "WEIGHTED (0-5)"))
        for i, r in enumerate(rows, 1):
            marker = "  <- smallest maintainable candidate" if i == 1 else ""
            lines.append("  %2d. %-22s %.3f%s" % (i, r["target"], r["weighted_score"], marker))
        lines.append("\nCriteria: " + ", ".join("%s=%.0f" % (c["id"], c["weight"]) for c in criteria.values()))
        lines.append("\n" + payload["decision_note"])
        out = "\n".join(lines)
    try:
        msg = write_or_print(out + "\n", args.output, args.force)
        if msg:
            print(msg)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    return EXIT_OK


# ---------------------------------------------------------------------------
# platform profiles
# ---------------------------------------------------------------------------

def cmd_profile(args):
    data = load_asset(PROFILES_ASSET)
    profiles = {p["id"]: p for p in data["profiles"]}
    if args.list or not args.target:
        if args.format == "json":
            print(json.dumps({"ok": True, "profiles": sorted(profiles)}, ensure_ascii=False, indent=2))
        else:
            print("Available profiles (%d):\n" % len(profiles))
            for pid, p in sorted(profiles.items()):
                print("  %-22s %s  [%s]" % (pid, p["name"], p["kind"]))
        return EXIT_OK
    p = profiles.get(args.target.strip().lower())
    if p is None:
        return emit_error("unknown profile %r (choose from: %s; or --list)" % (
            args.target, ", ".join(sorted(profiles))), fmt=args.format)
    payload = {"ok": True, "profile": p, "generated": utc_now(),
               "guidance_note": data.get("description")}
    if args.format == "json":
        out = json.dumps(payload, ensure_ascii=False, indent=2)
    elif args.format == "md":
        lines = ["# %s" % p["name"], "",
                 "- **Kind:** %s" % p["kind"],
                 "- **Minimum OS:** %s" % p["min_os"],
                 "- **CPU architectures:** %s" % ", ".join(p["cpu_architectures"]),
                 "- **Dependency weight:** %s" % p["dependency_weight"],
                 "", "## Compatibility\n\n%s" % p["compatibility"],
                 "", "## Performance\n\n%s" % p["performance"],
                 "", "## Accessibility\n\n%s" % p["accessibility"],
                 "", "## Distribution constraints\n\n%s" % p["distribution_constraints"],
                 "", "## Escape paths\n\n%s" % p["escape_paths"],
                 "", "## Offline notes\n\n%s" % p["offline_notes"]]
        out = "\n".join(lines)
    else:
        lines = ["%s  [%s]" % (p["name"], p["kind"]),
                 "  min OS:            %s" % p["min_os"],
                 "  architectures:      %s" % ", ".join(p["cpu_architectures"]),
                 "  dependency weight:  %s" % p["dependency_weight"],
                 "  compatibility:      %s" % p["compatibility"],
                 "  performance:        %s" % p["performance"],
                 "  accessibility:      %s" % p["accessibility"],
                 "  distribution:       %s" % p["distribution_constraints"],
                 "  escape paths:       %s" % p["escape_paths"],
                 "  offline notes:      %s" % p["offline_notes"]]
        out = "\n".join(lines)
    try:
        msg = write_or_print(out + "\n", args.output, args.force)
        if msg:
            print(msg)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    return EXIT_OK


# ---------------------------------------------------------------------------
# plan templates
# ---------------------------------------------------------------------------

def cmd_plan(args):
    data = load_asset(PLANS_ASSET)
    templates = {t["id"]: t for t in data["templates"]}
    if args.list or not args.template:
        if args.format == "json":
            print(json.dumps({"ok": True, "templates": [
                {"id": t["id"], "name": t["name"], "purpose": t["purpose"]} for t in data["templates"]]},
                ensure_ascii=False, indent=2))
        else:
            print("Plan templates (%d):\n" % len(templates))
            for t in data["templates"]:
                print("  %-20s %s" % (t["id"], t["name"]))
                print("  %-20s %s" % ("", t["purpose"]))
        return EXIT_OK
    tmpl = templates.get(args.template.strip().lower())
    if tmpl is None:
        return emit_error("unknown plan template %r (choose from: %s; or --list)" % (
            args.template, ", ".join(sorted(templates))), fmt=args.format)
    substitutions = {"date": today()}
    for item in args.set or []:
        if "=" not in item:
            return emit_error("--set expects key=value, got %r" % item, fmt=args.format)
        k, v = item.split("=", 1)
        substitutions[k.strip().lower()] = v

    def fill(text):
        def repl(match):
            key = match.group(1).lower()
            return substitutions.get(key, match.group(0))
        return re.sub(r"\{([a-z0-9_-]+)\}", repl, text)

    t = {k: (fill(v) if isinstance(v, str) else [fill(x) for x in v]) for k, v in tmpl.items()}
    unresolved = sorted(set(re.findall(r"\{[a-z0-9_-]+\}", json.dumps(t))))
    user_context = {k: v for k, v in substitutions.items() if k != "date"}
    payload = {"ok": True, "template": t, "generated": utc_now(),
               "user_context": user_context,
               "unresolved_placeholders": unresolved}
    if args.format == "json":
        out = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        context_line = (" Context: " + ", ".join("%s=%s" % kv for kv in sorted(user_context.items())) + ".") if user_context else ""
        lines = ["# %s" % t["name"], "", t["purpose"], "",
                 "*Generated %s by tools-offline-app-dev.py.%s Unresolved placeholders: %s*" % (
                     payload["generated"], context_line, ", ".join(unresolved) or "none"), ""]
        lines.append("## Owners\n" + "\n".join("- %s" % o for o in t["owners"]) + "\n")
        lines.append("## Prerequisites\n" + "\n".join("- %s" % p for p in t["prerequisites"]) + "\n")
        lines.append("## Ordered steps\n" + "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(t["steps"])) + "\n")
        lines.append("## Decision points\n" + "\n".join("- %s" % d for d in t["decision_points"]) + "\n")
        lines.append("## Failure recovery\n" + "\n".join("- %s" % f for f in t["failure_recovery"]) + "\n")
        lines.append("## Required evidence\n" + "\n".join("- %s" % e for e in t["evidence"]) + "\n")
        lines.append("## Completion condition\n\n%s\n" % t["completion_condition"])
        out = "\n".join(lines)
    try:
        msg = write_or_print(out + "\n", args.output, args.force)
        if msg:
            print(msg)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    return EXIT_OK


# ---------------------------------------------------------------------------
# ADR manager
# ---------------------------------------------------------------------------

def _adr_dir(store):
    return Path(store)


def _load_adr_index(store):
    idx = _adr_dir(store) / "adr-index.json"
    if idx.is_file():
        return load_json_file(str(idx), "ADR index")
    return {"schema_version": 1, "records": []}


def _save_adr_index(store, index):
    atomic_write(_adr_dir(store) / "adr-index.json",
                 json.dumps(index, ensure_ascii=False, indent=2) + "\n", force=True)


def _next_adr_number(index):
    return (max([r["number"] for r in index["records"]], default=0)) + 1


def cmd_adr(args):
    try:
        if args.adr_command == "new":
            if not args.title:
                return emit_error("adr new requires --title", fmt=args.format)
            index = _load_adr_index(args.store)
            number = _next_adr_number(index)
            template = load_asset(ADR_TEMPLATE_ASSET)
            date = args.date or today()
            body = (template.replace("{{number}}", "%04d" % number)
                            .replace("{{title}}", args.title)
                            .replace("{{date}}", date)
                            .replace("{{status}}", args.status)
                            .replace("{{decision_makers}}", args.decision_makers or "unspecified")
                            .replace("{{supersedes}}", str(args.supersedes or "none"))
                            .replace("{{context}}", args.context or "TODO: record the forces, constraints, and catalog refs that motivated this decision.")
                            .replace("{{decision}}", args.decision or "TODO: record the chosen option, rejected alternatives, and the documented escape path.")
                            .replace("{{consequences}}", args.consequences or "TODO: record what becomes easier/harder, reversibility, follow-ups, and offline impact.")
                            .replace("{{evidence}}", args.evidence or "TODO: attach matrix output, benchmarks, acceptance reports (immutable references; never secrets)."))
            slug = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")[:60] or "untitled"
            fname = "adr-%04d-%s.md" % (number, slug)
            if not body.startswith("# ADR-%04d:" % number):
                return emit_error("internal error: rendered ADR heading is missing its '# ADR-%04d:' marker; "
                                  "check asset-adr-template.md" % number, fmt=args.format)
            record = {"number": number, "title": args.title, "status": args.status, "date": date,
                      "decision_makers": args.decision_makers or "unspecified",
                      "supersedes": args.supersedes, "file": fname, "created": utc_now()}
            store = _adr_dir(args.store)
            store.mkdir(parents=True, exist_ok=True)
            atomic_write(store / fname, body, force=args.force)
            index["records"].append(record)
            _save_adr_index(str(store), index)
            out = {"ok": True, "adr": record, "store": str(store)}
            print(json.dumps(out, ensure_ascii=False, indent=2) if args.format == "json" else
                  "written: %s (status: %s)" % (store / fname, args.status))
            return EXIT_OK
        if args.adr_command == "list":
            index = _load_adr_index(args.store)
            records = index["records"]
            if args.format == "json":
                print(json.dumps({"ok": True, "count": len(records), "records": records},
                                 ensure_ascii=False, indent=2))
            else:
                if not records:
                    print("No ADRs in %s" % _adr_dir(args.store))
                for r in records:
                    print("  ADR-%04d  [%s]  %s  (%s)" % (r["number"], r["status"], r["title"], r["file"]))
            return EXIT_OK
        if args.adr_command == "show":
            index = _load_adr_index(args.store)
            match = [r for r in index["records"] if r["number"] == args.number]
            if not match:
                return emit_error("no ADR-%04d in %s" % (args.number, args.store), fmt=args.format)
            r = match[0]
            p = _adr_dir(args.store) / r["file"]
            print(p.read_text(encoding="utf-8") if p.is_file() else json.dumps(r, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.adr_command == "supersede":
            index = _load_adr_index(args.store)
            by_num, old_num = args.by, args.number
            nums = {r["number"] for r in index["records"]}
            if by_num not in nums:
                return emit_error("no ADR-%04d to supersede with" % by_num, fmt=args.format)
            if old_num not in nums:
                return emit_error("no ADR-%04d to mark superseded" % old_num, fmt=args.format)
            for r in index["records"]:
                if r["number"] == old_num:
                    r["status"] = "superseded by ADR-%04d" % by_num
                    r["updated"] = utc_now()
            _save_adr_index(args.store, index)
            # update the record file's Status line in place, atomically
            for r in index["records"]:
                if r["number"] == old_num:
                    p = _adr_dir(args.store) / r["file"]
                    if p.is_file():
                        text = p.read_text(encoding="utf-8")
                        text = re.sub(r"(?m)^- \*\*Status:\*\*.*$", "- **Status:** superseded by ADR-%04d" % by_num, text, count=1)
                        atomic_write(p, text, force=True)
            out = {"ok": True, "superseded": old_num, "by": by_num}
            print(json.dumps(out, ensure_ascii=False, indent=2) if args.format == "json" else
                  "ADR-%04d marked superseded by ADR-%04d" % (old_num, by_num))
            return EXIT_OK
        return emit_error("unknown adr command %r" % args.adr_command, fmt=args.format)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)



# ---------------------------------------------------------------------------
# risk / offline-constraint register
# ---------------------------------------------------------------------------

RISK_CATEGORIES = ["technical", "schedule", "security", "compliance", "privacy",
                   "performance", "accessibility", "dependency", "distribution",
                   "offline-constraint", "toolchain", "data", "vendor", "other"]
RISK_STATUSES = ["open", "mitigating", "accepted", "closed", "transferred"]


def _register_path(store, register):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", register):
        raise AssetError("register name %r must match ^[a-z0-9][a-z0-9-]*$" % register, EXIT_INPUT)
    return Path(store) / ("%s.json" % register)


def _load_register(path, register):
    if path.is_file():
        data = load_json_file(str(path), "register")
        if data.get("schema_version") != 1:
            raise AssetError("register %s has unsupported schema_version %r" % (path, data.get("schema_version")), EXIT_INPUT)
        data.setdefault("register", register)
        data.setdefault("records", [])
        return data
    return {"schema_version": 1, "register": register, "created": utc_now(), "records": []}


def _save_register(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", force=True)


def _validate_risk_fields(a):
    errors = []
    if a.category not in RISK_CATEGORIES:
        errors.append("category %r not in %s" % (a.category, ", ".join(RISK_CATEGORIES)))
    for name, val in (("severity", a.severity), ("likelihood", a.likelihood)):
        if val is None or not (1 <= int(val) <= 5):
            errors.append("%s must be an integer 1-5, got %r" % (name, val))
    return errors


def _exposure(rec):
    return int(rec["severity"]) * int(rec["likelihood"])


def cmd_risk(args):
    try:
        path = _register_path(args.store, args.register)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    try:
        data = _load_register(path, args.register)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    records = data["records"]
    try:
        if args.risk_command == "add":
            errors = _validate_risk_fields(args)
            if not args.title:
                errors.append("--title is required")
            if not args.owner:
                errors.append("--owner is required")
            if errors:
                return emit_error("; ".join(errors), fmt=args.format)
            next_id = (max((int(r["id"].split("-")[1]) for r in records), default=0)) + 1
            rec = {"id": "R-%04d" % next_id, "title": args.title, "category": args.category,
                   "severity": int(args.severity), "likelihood": int(args.likelihood),
                   "exposure": int(args.severity) * int(args.likelihood),
                   "status": "open", "owner": args.owner,
                   "mitigation": args.mitigation or "", "contingency": args.contingency or "",
                   "evidence": args.evidence or "",
                   "created": utc_now(), "updated": utc_now(), "closed": None}
            records.append(rec)
            _save_register(path, data)
            print(json.dumps({"ok": True, "record": rec, "store": str(path)}, ensure_ascii=False, indent=2)
                  if args.format == "json" else "added %s (exposure %d) -> %s" % (rec["id"], rec["exposure"], path))
            return EXIT_OK
        if args.risk_command == "list":
            rows = records
            if args.status:
                rows = [r for r in rows if r["status"] == args.status]
            if args.min_exposure is not None:
                rows = [r for r in rows if int(r.get("exposure", _exposure(r))) >= args.min_exposure]
            rows = sorted(rows, key=lambda r: (-int(r.get("exposure", 0)), r["id"]))
            if args.format == "json":
                print(json.dumps({"ok": True, "register": args.register, "count": len(rows),
                                  "records": rows}, ensure_ascii=False, indent=2))
            else:
                if not rows:
                    print("No matching records in %s" % path)
                for r in rows:
                    print("  %s  E=%-2d S=%d L=%d [%s] %-12s %s (owner: %s)" % (
                        r["id"], r.get("exposure", _exposure(r)), r["severity"], r["likelihood"],
                        r["status"], r["category"], r["title"], r["owner"]))
            return EXIT_OK
        if args.risk_command == "update":
            match = [r for r in records if r["id"] == args.id]
            if not match:
                return emit_error("no record %s in %s" % (args.id, path), fmt=args.format)
            r = match[0]
            changed = []
            if args.status:
                if args.status not in RISK_STATUSES:
                    return emit_error("status %r not in %s" % (args.status, ", ".join(RISK_STATUSES)), fmt=args.format)
                r["status"] = args.status
                r["closed"] = utc_now() if args.status == "closed" else None
                changed.append("status")
            for field in ("mitigation", "contingency", "evidence", "owner", "title"):
                val = getattr(args, field, None)
                if val is not None:
                    r[field] = val
                    changed.append(field)
            if args.severity is not None:
                if not (1 <= int(args.severity) <= 5):
                    return emit_error("severity must be 1-5", fmt=args.format)
                r["severity"] = int(args.severity)
                changed.append("severity")
            if args.likelihood is not None:
                if not (1 <= int(args.likelihood) <= 5):
                    return emit_error("likelihood must be 1-5", fmt=args.format)
                r["likelihood"] = int(args.likelihood)
                changed.append("likelihood")
            if not changed:
                return emit_error("nothing to update; pass at least one of --status/--severity/--likelihood/--mitigation/--contingency/--evidence/--owner/--title", fmt=args.format)
            r["exposure"] = _exposure(r)
            r["updated"] = utc_now()
            _save_register(path, data)
            print(json.dumps({"ok": True, "record": r}, ensure_ascii=False, indent=2) if args.format == "json"
                  else "updated %s (%s)" % (r["id"], ", ".join(changed)))
            return EXIT_OK
        if args.risk_command == "close":
            return cmd_risk_update_shortcut(args, data, path, records)
        if args.risk_command == "export":
            rows = sorted(records, key=lambda r: r["id"])
            if args.format == "csv":
                buf = []
                writer = csv.DictWriter(_ListWriter(buf), fieldnames=[
                    "id", "title", "category", "severity", "likelihood", "exposure",
                    "status", "owner", "mitigation", "contingency", "evidence", "created", "updated"])
                writer.writeheader()
                for r in rows:
                    writer.writerow({k: r.get(k, "") for k in writer.fieldnames})
                out = "".join(buf)
            elif args.format == "md":
                lines = ["# Risk Register: %s" % args.register, "",
                         "| Id | Exp | S | L | Status | Category | Title | Owner |",
                         "|---|---|---|---|---|---|---|---|"]
                for r in rows:
                    lines.append("| %s | %d | %d | %d | %s | %s | %s | %s |" % (
                        r["id"], r.get("exposure", _exposure(r)), r["severity"], r["likelihood"],
                        r["status"], r["category"], r["title"].replace("|", "\\|"), r["owner"]))
                lines.append("")
                out = "\n".join(lines)
            else:
                out = json.dumps({"ok": True, "register": args.register, "exported": utc_now(),
                                  "records": rows}, ensure_ascii=False, indent=2)
            try:
                msg = write_or_print(out + ("\n" if not out.endswith("\n") else ""), args.output, args.force)
                if msg:
                    print(msg)
            except AssetError as exc:
                return emit_error(str(exc), fmt=args.format)
            return EXIT_OK
        return emit_error("unknown risk command %r" % args.risk_command, fmt=args.format)
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)


def cmd_risk_update_shortcut(args, data, path, records):
    match = [r for r in records if r["id"] == args.id]
    if not match:
        return emit_error("no record %s in %s" % (args.id, path), fmt=args.format)
    r = match[0]
    r["status"] = "closed"
    r["closed"] = utc_now()
    r["updated"] = utc_now()
    if args.evidence:
        r["evidence"] = args.evidence
    _save_register(path, data)
    print(json.dumps({"ok": True, "record": r}, ensure_ascii=False, indent=2) if args.format == "json"
          else "closed %s" % r["id"])
    return EXIT_OK


class _ListWriter:
    """Minimal file-like object so csv can write into a list of strings."""
    def __init__(self, buf):
        self.buf = buf

    def write(self, s):
        self.buf.append(s)
        return len(s)


# ---------------------------------------------------------------------------
# acceptance evaluation
# ---------------------------------------------------------------------------

VALID_ACCEPT_RESULTS = ("pass", "warn", "fail", "untested")


def cmd_accept(args):
    try:
        checklist = load_asset(ACCEPT_ASSET) if args.checklist == "default" else load_json_file(args.checklist, "checklist")
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    checks = checklist.get("checks", [])
    if not checks:
        return emit_error("checklist has no checks", fmt=args.format)
    check_ids = [c["id"] for c in checks]
    if len(set(check_ids)) != len(check_ids):
        return emit_error("checklist contains duplicate check ids", fmt=args.format)

    if args.init_results:
        template = {"platform": "", "toolchain": "", "device_or_simulator": "",
                    "tester": "", "build": "", "date": today(),
                    "results": {c["id"]: {"result": "untested", "observation": "", "evidence": ""}
                                for c in checks}}
        try:
            atomic_write(args.init_results, json.dumps(template, ensure_ascii=False, indent=2) + "\n", force=args.force)
        except AssetError as exc:
            return emit_error(str(exc), fmt=args.format)
        print("written: %s (fill in platform/toolchain/device and per-check results, then run accept --results)" % args.init_results)
        return EXIT_OK

    if not args.results:
        return emit_error("accept requires --results <file> (or --init-results <file> to create one)",
                          hint="the tool records results you observed on a device/simulator; it never drives devices itself",
                          fmt=args.format)
    try:
        results_doc = load_json_file(args.results, "results file")
    except AssetError as exc:
        return emit_error(str(exc), fmt=args.format)
    results = results_doc.get("results")
    if not isinstance(results, dict):
        return emit_error("results file must contain an object key 'results' mapping check id -> {result, observation, evidence}",
                          fmt=args.format)
    unknown = sorted(set(results) - set(check_ids))
    if unknown:
        return emit_error("results reference unknown check ids: %s (known ids: %s)" % (
            ", ".join(unknown), ", ".join(check_ids)), fmt=args.format)

    rows, counts = [], {"pass": 0, "warn": 0, "fail": 0, "untested": 0}
    for c in checks:
        entry = results.get(c["id"], {})
        if isinstance(entry, str):  # tolerate bare "pass"/"warn"/... strings
            entry = {"result": entry, "observation": "", "evidence": ""}
        result = str(entry.get("result", "untested")).strip().lower()
        if result not in VALID_ACCEPT_RESULTS:
            return emit_error("check %r has invalid result %r (choose from %s)" % (
                c["id"], result, ", ".join(VALID_ACCEPT_RESULTS)), fmt=args.format)
        counts[result] += 1
        rows.append({"id": c["id"], "description": c["description"], "result": result,
                     "fail_severity": c.get("fail_severity", "fail"),
                     "observation": entry.get("observation", ""), "evidence": entry.get("evidence", ""),
                     "evidence_hint": c.get("evidence_hint", "")})
    fails = [r for r in rows if r["result"] == "fail"]
    warns = [r for r in rows if r["result"] == "warn"]
    untested = [r for r in rows if r["result"] == "untested"]
    if fails:
        verdict = "FAIL"
        exit_code = EXIT_ACCEPT_FAIL
    elif warns or untested:
        verdict = "WARN"
        exit_code = EXIT_ACCEPT_WARN
    else:
        verdict = "PASS"
        exit_code = EXIT_OK

    remediation = []
    for r in fails + warns:
        remediation.append({"check": r["id"], "severity": r["fail_severity"] if r["result"] == "fail" else "warn",
                            "observation": r["observation"], "required_evidence": r["evidence_hint"],
                            "action": "Fix, attach evidence, re-run this check, then re-run the full acceptance evaluation (retest the affected cells plus a regression sweep)."})
    for r in untested:
        remediation.append({"check": r["id"], "severity": "untested", "observation": "",
                            "required_evidence": r["evidence_hint"],
                            "action": "Execute the check on a clean environment and record the real result. Untested is never counted as pass."})

    payload = {"ok": exit_code == EXIT_OK, "verdict": verdict,
               "context": {"platform": results_doc.get("platform", ""),
                           "toolchain": results_doc.get("toolchain", ""),
                           "device_or_simulator": results_doc.get("device_or_simulator", ""),
                           "tester": results_doc.get("tester", ""),
                           "build": results_doc.get("build", ""),
                           "date": results_doc.get("date", today())},
               "summary": counts, "total_checks": len(rows),
               "checks": rows, "remediation": remediation, "generated": utc_now()}
    if args.format == "json":
        out = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        ctx = payload["context"]
        lines = ["# Acceptance Evaluation Report", "",
                 "- **Verdict:** %s" % verdict,
                 "- **Platform:** %s" % (ctx["platform"] or "unspecified"),
                 "- **Toolchain:** %s" % (ctx["toolchain"] or "unspecified"),
                 "- **Device/simulator:** %s" % (ctx["device_or_simulator"] or "unspecified"),
                 "- **Build:** %s" % (ctx["build"] or "unspecified"),
                 "- **Tester:** %s" % (ctx["tester"] or "unspecified"),
                 "- **Date:** %s" % ctx["date"],
                 "- **Checks:** %d pass, %d warn, %d fail, %d untested (of %d)" % (
                     counts["pass"], counts["warn"], counts["fail"], counts["untested"], len(rows)),
                 "", "## Results", ""]
        for r in rows:
            mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "untested": "UNTESTED"}[r["result"]]
            lines.append("- [%s] `%s` — %s" % (mark, r["id"], r["description"]))
            if r["observation"]:
                lines.append("      observation: %s" % r["observation"])
            if r["evidence"]:
                lines.append("      evidence: %s" % r["evidence"])
        if remediation:
            lines += ["", "## Remediation and retest", ""]
            for item in remediation:
                lines.append("- `%s` (%s): %s" % (item["check"], item["severity"], item["action"]))
                lines.append("      required evidence: %s" % item["required_evidence"])
        out = "\n".join(lines)
    if args.output:
        try:
            atomic_write(args.output, out + "\n", force=args.force)
        except AssetError as exc:
            return emit_error(str(exc), fmt=args.format)
        print("written: %s (verdict %s)" % (args.output, verdict))
    else:
        print(out)
    return exit_code


# ---------------------------------------------------------------------------
# offline verification
# ---------------------------------------------------------------------------

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_verify(args):
    did_work = False
    report = {"ok": True, "generated": utc_now(), "tools": [], "manifest": None}
    fmt = args.format
    failures = 0

    if not (args.tools or args.manifest or args.init_manifest):
        return emit_error("verify needs at least one of --tools, --manifest (+ --root), or --init-manifest (+ --root)",
                          fmt=fmt)

    # ---- tool presence / versions
    if args.tools:
        did_work = True
        for name in [t.strip() for t in args.tools.split(",") if t.strip()]:
            resolved = shutil.which(name)
            entry = {"name": name, "found": bool(resolved), "path": resolved or ""}
            if resolved is None:
                failures += 1
                entry["status"] = "MISSING"
            else:
                entry["status"] = "OK"
                if args.probe:
                    try:
                        proc = subprocess.run([resolved, "--version"], capture_output=True, text=True,
                                              timeout=10.0, shell=False)
                        first = (proc.stdout or proc.stderr or "").strip().splitlines()
                        entry["version"] = first[0][:200] if first else "(no output)"
                        entry["probe_exit"] = proc.returncode
                    except subprocess.TimeoutExpired:
                        entry["version"] = "(probe timed out after 10s)"
                        entry["probe_exit"] = None
                    except OSError as exc:
                        entry["version"] = "(probe failed: %s)" % exc
                        entry["probe_exit"] = None
            report["tools"].append(entry)

    # ---- manifest verification (read-only)
    if args.manifest:
        did_work = True
        if not args.root:
            return emit_error("--manifest requires --root <bundle-dir>", fmt=fmt)
        root = Path(args.root)
        if not root.is_dir():
            return emit_error("root directory not found: %s" % root, fmt=fmt)
        try:
            manifest = load_json_file(args.manifest, "manifest")
        except AssetError as exc:
            return emit_error(str(exc), fmt=fmt)
        files = manifest.get("files")
        if not isinstance(files, list):
            return emit_error("manifest must contain a list key 'files'", fmt=fmt)
        failures_local = []
        checked = 0
        for item in files:
            rel = item.get("path")
            expected = item.get("sha256")
            if not rel or not expected:
                failures_local.append({"path": str(rel), "problem": "manifest entry missing path or sha256"})
                continue
            candidate = (root / rel)
            if any(part == ".." for part in Path(rel).parts):
                failures_local.append({"path": rel, "problem": "path traversal outside root rejected"})
                continue
            if not candidate.is_file():
                failures_local.append({"path": rel, "problem": "missing file"})
                continue
            actual = _sha256_file(candidate)
            checked += 1
            if actual.lower() != str(expected).lower():
                failures_local.append({"path": rel, "problem": "sha256 mismatch",
                                       "expected": str(expected).lower(), "actual": actual})
        # untracked files
        listed = {item.get("path") for item in files if isinstance(item, dict)}
        untracked = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if rel not in listed:
                    untracked.append(rel)
        manifest_ok = not failures_local and not (args.strict and untracked)
        failures += 0 if manifest_ok else 1
        report["manifest"] = {"path": args.manifest, "root": str(root), "checked": checked,
                              "failures": failures_local, "untracked": untracked,
                              "strict": args.strict, "ok": manifest_ok}

    # ---- manifest creation (writes only the manifest file)
    if args.init_manifest:
        if not args.root:
            return emit_error("--init-manifest requires --root <bundle-dir>", fmt=fmt)
        root = Path(args.root)
        if not root.is_dir():
            return emit_error("root directory not found: %s" % root, fmt=fmt)
        entries = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                entries.append({"path": rel, "sha256": _sha256_file(p), "bytes": p.stat().st_size})
        manifest = {"schema_version": 1, "bundle": root.name, "algorithm": "sha256",
                    "created": utc_now(), "files": entries}
        try:
            atomic_write(args.init_manifest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", force=args.force)
        except AssetError as exc:
            return emit_error(str(exc), fmt=fmt)
        report["init_manifest"] = {"path": args.init_manifest, "root": str(root), "files": len(entries)}
        did_work = True

    report["ok"] = failures == 0
    if fmt == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if report["tools"]:
            print("Tool presence:")
            for t in report["tools"]:
                line = "  [%s] %s" % (t["status"], t["name"])
                if t.get("path"):
                    line += "  -> %s" % t["path"]
                if t.get("version"):
                    line += "  (%s)" % t["version"]
                print(line)
        if report["manifest"]:
            m = report["manifest"]
            print("Manifest verification (%s vs %s): %d files checked" % (m["path"], m["root"], m["checked"]))
            for f in m["failures"]:
                print("  FAIL  %s: %s" % (f["path"], f["problem"]))
            for u in m["untracked"]:
                print("  WARN  untracked file (present but not pinned): %s" % u)
            print("  result: %s" % ("OK" if m["ok"] else "FAILED"))
        if report.get("init_manifest"):
            m = report["init_manifest"]
            print("Manifest written: %s (%d files)" % (m["path"], m["files"]))
        if failures:
            print("verify: FAILED (%d failing area(s))" % failures)
        else:
            print("verify: OK")
    return EXIT_OK if failures == 0 else EXIT_RUNTIME


# ---------------------------------------------------------------------------
# argument parsing / dispatch
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="tools-offline-app-dev.py",
        description="Unified engine for the Full Offline App Development Skill Catalog (27 categories, 1095 concepts). "
                    "Standard library only; no network access; see knowledge-readme.md.")
    p.add_argument("--version", action="version", version="tools-offline-app-dev.py %s" % TOOL_VERSION)
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def fmt_arg(sp, default="text", choices=_FORMATS):
        sp.add_argument("--format", default=default, choices=choices)

    sp = sub.add_parser("capabilities", help="list this tool's capabilities and catalog coverage")
    fmt_arg(sp, choices=("text", "json"))
    sp.set_defaults(func=cmd_capabilities)

    sp = sub.add_parser("list", help="list catalog categories and skills")
    sp.add_argument("--category", help="category number or name substring (e.g. 6 or 'ios')")
    sp.add_argument("--archetype", help="one of: implement, evaluate, adapter, build, operate, plan, select-configure")
    sp.add_argument("--count-only", action="store_true", help="print only the number of matching skills")
    fmt_arg(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="full-text search across the catalog (AND semantics)")
    sp.add_argument("query", help="search terms, e.g. 'keystore adapter'")
    sp.add_argument("--category", help="restrict to category number or name substring")
    sp.add_argument("--archetype", help="restrict to archetype")
    sp.add_argument("--limit", type=int, default=20)
    fmt_arg(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", help="show one skill by ref (N.M) or exact name")
    sp.add_argument("ref")
    fmt_arg(sp)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("brief", help="generate a capability brief (work order) for a catalog skill")
    sp.add_argument("ref", help="skill ref N.M or exact name")
    sp.add_argument("--output", help="write brief to a file instead of stdout")
    sp.add_argument("--force", action="store_true", help="allow overwriting --output if it exists")
    fmt_arg(sp, choices=("md", "json"))
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("matrix", help="target platform decision matrix (weighted scoring)")
    sp.add_argument("--targets", help="comma-separated target ids (default: all scored targets)")
    sp.add_argument("--criteria", help="JSON override file: {\"criteria\": [...], \"scores\": {...}, \"drop_criteria\": [...]}")
    sp.add_argument("--output")
    sp.add_argument("--force", action="store_true")
    fmt_arg(sp)
    sp.set_defaults(func=cmd_matrix)

    sp = sub.add_parser("profile", help="structured target platform/framework/arch profile")
    sp.add_argument("target", nargs="?", help="profile id (see --list)")
    sp.add_argument("--list", action="store_true", help="list available profile ids")
    sp.add_argument("--output")
    sp.add_argument("--force", action="store_true")
    fmt_arg(sp)
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("plan", help="generate an owned, verifiable plan from a built-in template")
    sp.add_argument("template", nargs="?", help="template id (see --list)")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--set", action="append", metavar="key=value",
                    help="substitute {key} placeholders (repeatable); {date} defaults to today")
    sp.add_argument("--output")
    sp.add_argument("--force", action="store_true")
    fmt_arg(sp, choices=("md", "json"))
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("adr", help="architecture decision record manager")
    adr_sub = sp.add_subparsers(dest="adr_command", metavar="<adr-command>")
    a = adr_sub.add_parser("new", help="create a new ADR (deterministic numbering)")
    a.add_argument("--title", required=True)
    a.add_argument("--status", default="proposed", choices=("proposed", "accepted", "rejected", "deprecated"))
    a.add_argument("--decision-makers")
    a.add_argument("--supersedes", type=int)
    a.add_argument("--context")
    a.add_argument("--decision")
    a.add_argument("--consequences")
    a.add_argument("--evidence")
    a.add_argument("--date")
    a.add_argument("--store", default="./adr-records", help="ADR directory (default ./adr-records)")
    a.add_argument("--force", action="store_true")
    fmt_arg(a, choices=("text", "json"))
    a.set_defaults(func=cmd_adr)
    a = adr_sub.add_parser("list", help="list ADRs")
    a.add_argument("--store", default="./adr-records")
    fmt_arg(a, choices=("text", "json"))
    a.set_defaults(func=cmd_adr)
    a = adr_sub.add_parser("show", help="print one ADR by number")
    a.add_argument("number", type=int)
    a.add_argument("--store", default="./adr-records")
    fmt_arg(a, choices=("text", "json"))
    a.set_defaults(func=cmd_adr)
    a = adr_sub.add_parser("supersede", help="mark an ADR superseded by another")
    a.add_argument("number", type=int, help="ADR to mark superseded")
    a.add_argument("--by", type=int, required=True, help="superseding ADR number")
    a.add_argument("--store", default="./adr-records")
    fmt_arg(a, choices=("text", "json"))
    a.set_defaults(func=cmd_adr)

    sp = sub.add_parser("risk", help="risk / offline-constraint register")
    risk_sub = sp.add_subparsers(dest="risk_command", metavar="<risk-command>")

    def risk_common(a, with_id=False):
        a.add_argument("--register", default="technical-risk",
                       help="register name (default technical-risk; use e.g. offline-constraint for category-1.37 use)")
        a.add_argument("--store", default="./registers", help="register directory (default ./registers)")
        fmt_arg(a, choices=("text", "json"))
        if with_id:
            a.add_argument("id", help="record id, e.g. R-0001")

    a = risk_sub.add_parser("add", help="add a record")
    a.add_argument("--title", required=False)
    a.add_argument("--category", default="technical", choices=RISK_CATEGORIES)
    a.add_argument("--severity", type=int)
    a.add_argument("--likelihood", type=int)
    a.add_argument("--owner")
    a.add_argument("--mitigation")
    a.add_argument("--contingency")
    a.add_argument("--evidence")
    risk_common(a)
    a.set_defaults(func=cmd_risk)

    a = risk_sub.add_parser("list", help="list records, highest exposure first")
    a.add_argument("--status", choices=RISK_STATUSES)
    a.add_argument("--min-exposure", type=int, dest="min_exposure")
    risk_common(a)
    a.set_defaults(func=cmd_risk)

    a = risk_sub.add_parser("update", help="update fields of a record")
    a.add_argument("--status", choices=RISK_STATUSES)
    a.add_argument("--severity", type=int)
    a.add_argument("--likelihood", type=int)
    a.add_argument("--title")
    a.add_argument("--owner")
    a.add_argument("--mitigation")
    a.add_argument("--contingency")
    a.add_argument("--evidence")
    risk_common(a, with_id=True)
    a.set_defaults(func=cmd_risk)

    a = risk_sub.add_parser("close", help="close a record (optional --evidence)")
    a.add_argument("--evidence")
    risk_common(a, with_id=True)
    a.set_defaults(func=cmd_risk)

    a = risk_sub.add_parser("export", help="export the register")
    a.add_argument("--output")
    a.add_argument("--force", action="store_true")
    a.add_argument("--register", default="technical-risk")
    a.add_argument("--store", default="./registers")
    a.add_argument("--format", default="json", choices=("json", "csv", "md"))
    a.set_defaults(func=cmd_risk)

    sp = sub.add_parser("accept", help="acceptance evaluation with pass/warn/fail verdict from recorded results")
    sp.add_argument("--checklist", default="default",
                    help="path to a checklist JSON, or 'default' for asset-acceptance-checklist.json")
    sp.add_argument("--results", help="results JSON: {platform, toolchain, device_or_simulator, tester, build, date, results:{id:{result,observation,evidence}}}")
    sp.add_argument("--init-results", metavar="PATH", help="write a blank results template to PATH and exit")
    sp.add_argument("--output", help="write report to a file instead of stdout")
    sp.add_argument("--force", action="store_true")
    fmt_arg(sp, choices=("md", "json"))
    sp.set_defaults(func=cmd_accept)

    sp = sub.add_parser("verify", help="offline verification: tool presence, SHA-256 bundle manifests")
    sp.add_argument("--tools", help="comma-separated tool names to check on PATH")
    sp.add_argument("--probe", action="store_true",
                    help="also run '<tool> --version' (10s timeout, no shell); off by default because it executes the binary")
    sp.add_argument("--manifest", help="integrity manifest to verify (see asset-verify-manifest.example.json)")
    sp.add_argument("--root", help="bundle directory the manifest paths are relative to")
    sp.add_argument("--init-manifest", metavar="PATH", help="create a manifest for --root and write it to PATH")
    sp.add_argument("--strict", action="store_true", help="treat untracked files in the bundle as failures")
    sp.add_argument("--force", action="store_true", help="allow overwriting --init-manifest output")
    fmt_arg(sp, choices=("text", "json"))
    sp.set_defaults(func=cmd_verify)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_INPUT
    if args.command == "adr" and not getattr(args, "adr_command", None):
        parser.parse_args(["adr", "--help"])
        return EXIT_INPUT
    if args.command == "risk" and not getattr(args, "risk_command", None):
        parser.parse_args(["risk", "--help"])
        return EXIT_INPUT
    try:
        return args.func(args)
    except AssetError as exc:
        return emit_error(str(exc), fmt=getattr(args, "format", "text"), code=getattr(exc, "code", EXIT_RUNTIME))
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            return EXIT_OK
    except KeyboardInterrupt:
        eprint("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
