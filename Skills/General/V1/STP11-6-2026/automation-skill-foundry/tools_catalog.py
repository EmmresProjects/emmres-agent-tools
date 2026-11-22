#!/usr/bin/env python3
"""tools_catalog.py - PRIMARY ENTRY POINT for the Automation Skill Foundry.

Turns catalog text (plain text, Markdown, or copied HTML), a category list, or
free-form automation intent into a validated, machine-readable skill bundle.

Commands
  list     list categories, or the skills inside one category
  search   rank skills for a free-text need
  parse    extract Skill Tool names + descriptions from supplied text/file
  route    select the smallest safe skill set for an automation intent
  info     show full metadata for one skill
  check    validate the catalog asset and report integrity

Run `python3 tools_catalog.py --help` or `... list --help` for details.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_common as C

PROG = "tools_catalog.py"

MY_EXTENSION = (
    "list --category 3",
    "search --query \"nightly invoice processing\"",
    "route --goal \"process new invoices every night\"",
)


# --------------------------------------------------------------------------
# text extraction (plain text, Markdown, HTML)
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)</(?:p|div|li|h[1-6]|tr|td|th|section|article|table|ul|ol|br)\s*>|<br\s*/?>")
_NUMBER_RE = re.compile(r"^\s*(?:#\s*)?(\d{1,3})\s*[.)\]:-]?\s*")
_MARKER_RE = re.compile(
    r"(?i)^\s*(?:skill\s*name|tool\s*name|skill|tool|name|title|capability)"
    r"(?:\s*[:=]\s*|\s+[-\u2013]\s+)"
)
_DESC_RE = re.compile(r"(?i)^\s*(?:description|purpose|summary|what\s*it\s*does)"
                      r"(?:\s*[:=]\s*|\s+[-\u2013]\s+)")
_CATEGORY_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[Cc]ategory\s*[:\-]?\s*)?"
    r"(?:\d{1,2}\s*[.)]\s*)?([A-Za-z][A-Za-z0-9 &/'+,\-]{2,80}?)\s*"
    r"(?:\|\s*\d+\s*skills?)?\s*$"
)
_CATEGORY_HINT_RE = re.compile(r"(?i)\b(automation|bots?|builders?|operations?|governance|"
                              r"quality|monitoring|security|privacy|media|creative|content|"
                              r"productivity|integration|infrastructure|devops|mining|extraction|"
                              r"work|worker|planning|architecture|file|files|folder|storage|"
                              r"document|office|knowledge|email|messaging|communication|browser|"
                              r"web|form|data|processing|reporting|api|connector|cloud|server|"
                              r"development|testing|business|workplace|personal|information|"
                              r"alerts?|reliability|safe|frameworks?|conversational)\b")
_CATEGORY_COUNT_RE = re.compile(r"(?i)\|\s*\d+\s+skills?\s*$")
_UMBRELLA_TAIL_RE = re.compile(
    r"(?i)\b(automation|work|worker|bots?|reliability|governance|content|mining|extraction|"
    r"management|operations|systems|architecture|builders|services)\s*$")
_SKILL_NOUN_RE = re.compile(
    r"(?i)\b(bot|builder|architect|scheduler|monitor|checker|designer|manager|router|finder|"
    r"harness|planner|reviewer|packager|runner|automator|organizer|writer|analyzer|gate|mapper|"
    r"estimator|assessor|tracker|supervisor|coordinator|pipeline|extension|cataloger|publisher|"
    r"declutter|repurposer|fingerprinter|handoff|scorer|reporter|auditor|enforcer|standardizer|"
    r"guard|validator|orchestrator|processor|extractor|classifier|tester|miner|collector|"
    r"distributor|provisioner|aggregator|synchronizer|connector|adapter|draft)\b")
_TRAILER_RE = re.compile(
    r"(?i)^(suggested catalog use|recommended skill bundle pattern|build note|"
    r"category map|purpose|core design rules)\b")
_EMPH_RE = re.compile(r"(\*\*|__|`|~~)")
_BULLET_RE = re.compile(r"^\s*[\u2022\u00b7*+\-]\s+")
_FILLER = re.compile(r"(?i)^(focused skills for this part.*|catalog only.*|core design rules.*|"
                     r"purpose|build note|suggested catalog use|recommended skill bundle pattern.*)$")


def normalize_text(raw):
    """Strip HTML tags/entities, unify line breaks, and drop page furniture."""
    if raw is None:
        return ""
    text = str(raw)
    if "<" in text and ">" in text and _TAG_RE.search(text):
        text = _BLOCK_RE.sub("\n", text)
        text = _TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"(?i)^(autoforge\s*\||.*\|\s*page\s*\d+$|page\s*\d+\s*(of|\|)\s*\d*)", stripped):
            continue
        if _FILLER.match(stripped):
            continue
        stripped = _EMPH_RE.sub("", stripped)
        stripped = _BULLET_RE.sub("", stripped)
        lines.append(re.sub(r"[ \t]+", " ", stripped))
    return "\n".join(lines)


def _clean_name(value):
    name = re.sub(r"\s+", " ", value).strip(" -:\u2013|")
    name = _MARKER_RE.sub("", name).strip(" -:\u2013|")
    return name


def _is_category_line(line):
    """True only for category headings - never for skill rows or descriptions."""
    if _CATEGORY_COUNT_RE.search(line):
        return True
    if line.startswith("|") or _DESC_RE.match(line) or _MARKER_RE.match(line):
        return False
    match = _CATEGORY_RE.match(line)
    if not match:
        return False
    candidate = match.group(1).strip()
    words = [w for w in re.split(r"\s+", candidate) if w]
    if not 2 <= len(words) <= 9:
        return False
    if len(words) >= 3 and sum(1 for w in words if w.isupper()) >= len(words) - 1:
        return False
    if not _CATEGORY_HINT_RE.search(candidate):
        return False
    umbrella = bool(_UMBRELLA_TAIL_RE.search(candidate)) and (
        "," in candidate or " and " in candidate or "&" in candidate)
    if _SKILL_NOUN_RE.search(candidate) and not umbrella:
        return False
    numbered = _NUMBER_RE.match(line)
    rest = line[numbered.end():] if numbered else line
    if numbered and _SKILL_NOUN_RE.search(rest) and not umbrella:
        return False
    return True


def _can_append(cur_text):
    """Append wrapped lines only until the description reads as finished."""
    return not cur_text.rstrip().endswith((".", "!", "?"))


def _marker_at(line):
    """Return (number_or_None, rest) when a line carries a name marker."""
    number = None
    offset = 0
    numbered = _NUMBER_RE.match(line)
    if numbered:
        number = int(numbered.group(1))
        offset = numbered.end()
    match = _MARKER_RE.match(line[offset:])
    if not match:
        return None
    rest = line[offset + match.end():].strip()
    if not rest or len(rest) > 120:
        return None
    return number, rest


def _accept_name(candidate, rest_after_number):
    """Reject prose and category headings that look like numbered rows."""
    if not candidate or len(candidate) > 90:
        return False
    if not _SKILL_NOUN_RE.search(candidate):
        return False
    if "." in candidate or _UMBRELLA_TAIL_RE.search(candidate):
        return False
    return True


def extract_skills(raw, max_skills=500):
    """Extract (number, name, description) rows from arbitrary catalog text.

    Handles the common shapes: `1. Skill Name: X` + `Description: Y`, Markdown
    `**Skill Name:** X` + `**Description:** Y`, HTML lists, numbered plain names,
    and pipe tables. Prose, banners, and category headings are ignored.
    """
    text = normalize_text(raw)
    if not text.strip():
        raise C.FoundryError("no usable text found in the supplied input",
                            code="empty_input")
    lines = text.split("\n")
    found = []
    current = None
    last_number = 0

    for line in lines:
        if _TRAILER_RE.match(line):
            current = None
            continue
        desc = _DESC_RE.match(line)
        if desc and current is not None:
            if _can_append(current["description"]):
                current["description"] = (current["description"] + " "
                                          + line[desc.end():].strip()).strip()
            continue
        if desc:
            continue
        if _is_category_line(line):
            current = None
            continue
        marked = _marker_at(line)
        if marked:
            number, rest = marked
            candidate = _clean_name(rest)
            if candidate and (_SKILL_NOUN_RE.search(candidate) or len(candidate) <= 60):
                current = {"number": number, "name": candidate, "description": ""}
                found.append(current)
                last_number = number or last_number
                continue
        else:
            numbered = _NUMBER_RE.match(line)
            if numbered and not _SKILL_NOUN_RE.search(line[numbered.end():].split("|")[0]) is False:
                number = int(numbered.group(1))
                raw_rest = line[numbered.end():]
                candidate = _clean_name(raw_rest.split("|")[0])
                forward = number == last_number + 1 or number >= last_number + 1
                if _accept_name(candidate, raw_rest) or (forward and candidate
                                                         and _SKILL_NOUN_RE.search(candidate)):
                    current = {"number": number, "name": candidate, "description": ""}
                    found.append(current)
                    last_number = number
                    continue
        if current is not None and _can_append(current["description"]):
            current["description"] = (current["description"] + " " + line).strip()

    # Pipe table fallback: | 12 | Bulk Rename Bot | Renames ... |
    if not found:
        for line in lines:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 3 and re.fullmatch(r"#?\d{1,3}", cells[0] or ""):
                found.append({"number": int(cells[0].lstrip("#")),
                              "name": _clean_name(cells[1]),
                              "description": " ".join(cells[2:]).strip()})

    results = []
    seen = set()
    for item in found:
        name = _clean_name(item["name"])
        if not name or len(name) > 120:
            continue
        if not _SKILL_NOUN_RE.search(name) and not item["description"]:
            continue
        key = C._slug(name)
        if not key or key in seen:
            continue
        seen.add(key)
        if len(results) >= max_skills:
            raise C.FoundryError(
                "input contains more than max_skills=%d entries; split the source"
                % max_skills, code="too_many_skills")
        results.append({
            "number": item["number"],
            "id": key,
            "name": name,
            "description": re.sub(r"\s+", " ", item["description"]).strip(),
        })
    if not results:
        raise C.FoundryError(
            "could not identify any 'Skill Name: ...' + 'Description: ...' entries",
            code="no_skills_found",
        )
    return results


def extract_categories(raw):
    text = normalize_text(raw)
    cats = []
    seen = set()
    for line in text.split("\n"):
        if line.startswith("|"):
            continue
        match = _CATEGORY_RE.match(line)
        if not match or not _is_category_line(line):
            continue
        name = re.sub(r"\s+", " ", match.group(1)).strip(" -:\u2013")
        key = C._slug(name)
        if key in seen:
            continue
        seen.add(key)
        numbered = _NUMBER_RE.match(line)
        cats.append({
            "number": int(numbered.group(1)) if numbered else None,
            "name": name,
            "id": key,
        })
    return cats


def load_source(args):
    """Return (raw_text, mime, origin) from --input file or a positional path."""
    path = getattr(args, "input", None) or getattr(args, "path", None)
    if not path:
        raise C.FoundryError("provide --input FILE - or a text argument",
                            code="missing_input")
    if not os.path.exists(path):
        raise C.FoundryError("input file not found: %s" % path, code="missing_file")
    if os.path.isdir(path):
        raise C.FoundryError("input path is a directory, expected a file: %s" % path,
                            code="bad_input")
    size = os.path.getsize(path)
    limit = 8 * 1024 * 1024
    if size > limit:
        raise C.FoundryError(
            "input file is too large (%d bytes > %d)" % (size, limit),
            code="input_too_large")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    mime = "html" if re.search(r"(?i)<html|<div|<\w+ [^>]*class=", raw[:4000]) else (
        "markdown" if re.search(r"(?m)^\s*(#{1,6}\s|\|.*\|)", raw[:4000]) else "text")
    return raw, mime, os.path.abspath(path)


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

def _recipe_patterns():
    return C.load_json(C.asset(C.RECIPE_FILE), "recipe patterns")


_stem = C._stem


def match_recipes(goal, min_score=2):
    """Score packaged multi-skill recipe patterns against a goal statement.

    Keyword hits are weighted x2 vs summary/name hits; min_score is compared
    against the weighted score so two keyword hits always qualify.
    """
    patterns = _recipe_patterns()
    terms = [t for t in C._slug(goal).split("-") if len(t) > 3]
    if not terms:
        raise C.FoundryError("goal text is too short to match recipes",
                            code="bad_goal")
    rows = []
    for pattern in patterns.get("patterns", []):
        keywords = {_stem(k) for k in
                    C._slug(" ".join(pattern.get("match_keywords", []))).split("-")}
        blob = C._slug(" ".join([pattern.get("name", ""), pattern.get("summary", "")]))
        words = {_stem(w) for w in blob.split("-")}
        score = 0
        matched = []
        for term in sorted(set(_stem(t) for t in terms)):
            if term in keywords:
                score += 2
                matched.append(term)
            elif term in words:
                score += 1
                matched.append(term)
        if score >= min_score:
            rows.append((score, len(matched), pattern, matched))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [{"score": s, "matched_terms": m, "pattern": p} for s, _, p, m in rows]


def build_bundle(catalog, selected, goal, cfg, rules, risk=None):
    """Compose a bundle: stages, roles, gate, tests, and safety notes."""
    stages = rules["bundle_stages"]
    stage_order = [s["id"] for s in stages]
    risk = risk or {"level": "unspecified", "score": 0, "drivers": [], "notes": "not scored"}
    entries = []
    for skill in selected:
        entries.append({
            "number": skill["number"],
            "id": skill["id"],
            "name": skill["name"],
            "category": skill["category"],
            "description": skill["description"],
            "stage": _role_stage(skill, rules),
            "why": _why(skill, goal),
        })
    unknown = [e for e in entries if e["stage"] not in stage_order]
    for entry in unknown:
        entry["stage"] = "build"
    entries.sort(key=lambda e: (stage_order.index(e["stage"]), int(e["number"])))
    trimmed = []
    cap = int(cfg.get("max_items") or 0)
    if cap and len(entries) > cap:
        # keep safety-relevant stages first, then the remaining build steps
        keep_rank = {"guard": 0, "verify": 1, "plan": 2, "trigger": 3,
                     "build": 4, "operate": 5, "handoff": 6}
        ranked = sorted(entries, key=lambda e: (keep_rank.get(e["stage"], 9),
                                                int(e["number"])))
        kept = {e["number"] for e in ranked[:cap]}
        trimmed = [{"number": e["number"], "name": e["name"], "stage": e["stage"]}
                   for e in ranked[cap:]]
        entries = [e for e in entries if e["number"] in kept]
    gate = [
        "Dry run + diff preview before any write, send, delete, publish, or spend",
        "Explicit human approval for destructive, financial, public, or credential actions",
        "Bounded retries (max 3) with exponential backoff and a hard timeout",
        "Structured audit log of actor, input, action, result, approval, failure",
        "Rollback or restore checkpoint created before mutating state",
    ]
    if risk["level"] in ("high", "critical"):
        gate.insert(0, "Blocking review required: risk level %s" % risk["level"])
    return {
        "ok": True,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "goal": goal,
        "risk": risk,
        "approved_by": None,
        "approval_required": bool(risk["level"] in ("high", "critical")),
        "stages": stage_order,
        "skills": entries,
        "skill_count": len(entries),
        "per_category": _per_category(entries),
        "gates": gate,
        "next_commands": [
            "python3 tools_plan.py workflow --input bundle.json",
            "python3 tools_schedule.py plan --input bundle.json --profile nightly-batch",
            "python3 tools_scaffold.py build --input bundle.json --out-dir "
            "automation-workspace --apply",
            "python3 tools_governance.py gate --input bundle.json --project "
            "automation-workspace",
            "python3 tools_governance.py test-plan --input bundle.json --project "
            "automation-workspace",
        ],
        "limits": {
            "max_items": cap or None,
            "bundle_size": len(entries),
            "truncated": bool(trimmed),
            "trimmed_skills": trimmed,
            "trim_rule": "safety stages (guard, verify) are kept before build/operate "
                         "extras when a bundle exceeds config.max_items",
        },
    }


def _role_stage(skill, rules):
    return C.stage_of(skill, rules)


def _why(skill, goal):
    return "Covers %s for: %s" % (skill["category"].lower(), (goal or "").strip() or "stated need")


def _per_category(entries):
    counts = {}
    for entry in entries:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    return [{"category": k, "count": v} for k, v in sorted(counts.items())]


def score_risk(goal, rules, extra_flags=None):
    """Delegate to the shared deterministic scorer in tools_common.py."""
    return C.score_risk(goal, rules, extra_flags=extra_flags)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_list(args, cfg):
    catalog = C.Catalog(C.resource_path(cfg["catalog_file"]))
    if args.category is None:
        rows = []
        for cat in catalog.categories:
            rows.append({
                "number": cat["number"],
                "id": cat["id"],
                "name": cat["name"],
                "skill_count": cat["skill_count"],
                "number_range": [
                    catalog.by_cat[int(cat["number"])][0]["number"],
                    catalog.by_cat[int(cat["number"])][-1]["number"],
                ],
                "skills": [
                    {"number": s["number"], "id": s["id"], "name": s["name"]}
                    for s in catalog.by_cat[int(cat["number"])]
                ] if args.expand else None,
            })
        return C.guard_output_size({
            "ok": True,
            "tool": PROG,
            "action": "list",
            "catalog": catalog.summary(),
            "category_total": len(rows),
            "categories": rows,
        }, cfg)

    cat = catalog.category(args.category)
    skills = catalog.by_cat[int(cat["number"])]
    parent = None
    match = re.match(r"([a-z0-9-]+)-skills$", cat["id"])
    if match:
        parent = match.group(1)
    return {
        "ok": True,
        "tool": PROG,
        "action": "list",
        "category": {"number": cat["number"], "id": cat["id"], "name": cat["name"],
                     "skill_count": cat["skill_count"], "parent_name": parent},
        "skills": skills,
    }


def cmd_search(args, cfg):
    catalog = C.Catalog(C.resource_path(cfg["catalog_file"]))
    results = catalog.search(args.query, category=args.category,
                             limit=args.limit or cfg["max_items"])
    return {
        "ok": True,
        "tool": PROG,
        "action": "search",
        "query": args.query,
        "category": args.category,
        "result_count": len(results),
        "results": results,
        "note": "No match is a valid answer; widen the query or use `route`." if not results else None,
    }


def cmd_parse(args, cfg):
    raw, mime, origin = load_source(args)
    skills = extract_skills(raw, max_skills=args.max_skills)
    categories = extract_categories(raw)
    numbers = [s["number"] for s in skills if s["number"]]
    warnings = []
    if numbers and numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        warnings.append("skill numbering is not sequential; order preserved as written")
    if not categories:
        warnings.append("no category headings detected; pass --category-name to label the bundle")
    missing_desc = [s["name"] for s in skills if not s["description"]]
    if missing_desc:
        warnings.append("%d entr(ies) have no description: %s"
                        % (len(missing_desc), ", ".join(missing_desc[:5])))
    umbrella = None
    if categories:
        names = [c["name"] for c in categories]
        if len(names) == 1:
            umbrella = names[0]
        else:
            common = _common_prefix(names)
            umbrella = common or None
    if args.category_name:
        umbrella = args.category_name
    umbrella_id = C._slug(umbrella) if umbrella else None
    if umbrella_id:
        umbrella_id = re.sub(r"-skills$", "", umbrella_id) + "-skills"
    return {
        "ok": True,
        "tool": PROG,
        "action": "parse",
        "source": {"path": origin, "mime": mime, "bytes": len(raw.encode("utf-8"))},
        "category_count": len(categories),
        "categories": categories,
        "umbrella": {"name": umbrella, "id": umbrella_id},
        "skill_count": len(skills),
        "skills": skills,
        "warnings": warnings,
    }


def _common_prefix(names):
    if not names:
        return None
    split = [n.replace(" and ", " & ").split(" & ")[0].strip() for n in names]
    shared = os.path.commonprefix(split).strip(" ,-")
    return shared if len(shared.split()) >= 2 else None


def cmd_info(args, cfg):
    catalog = C.Catalog(C.resource_path(cfg["catalog_file"]))
    skill = catalog.resolve(args.skill)
    rules = C.load_rules(root=C.tool_root())
    related = [
        {"number": s["number"], "name": s["name"]}
        for s in catalog.by_cat[int(skill["category_number"])]
        if s["number"] != skill["number"]
    ][:5]
    return {
        "ok": True,
        "tool": PROG,
        "action": "info",
        "skill": skill,
        "stage": _role_stage(skill, rules),
        "same_category": related,
    }


def cmd_route(args, cfg):
    catalog = C.Catalog(C.resource_path(cfg["catalog_file"]))
    rules = C.load_rules(root=C.tool_root())
    goal = " ".join(args.goal).strip() if isinstance(args.goal, list) else str(args.goal).strip()
    if not goal:
        raise C.FoundryError("goal is empty", code="bad_goal")

    risk = score_risk(goal, rules, extra_flags=args.risk_flag)
    selected = []
    source = None
    if args.skills:
        selected = catalog.resolve_many(args.skills, max_items=cfg["max_items"])
        source = "explicit"
    else:
        recipes = [] if args.no_recipes else match_recipes(goal)
        if recipes:
            best = recipes[0]["pattern"]
            selected = [catalog.resolve(n) for n in best["skill_numbers"]]
            source = "recipe:%s" % best["id"]
        else:
            limit = args.limit or cfg["ask_skill_limit"]
            hits = catalog.search(goal, limit=max(limit, 3))
            admissible = [
                h for h in hits
                if score_risk(h["description"] + " " + h["name"], rules)["level"]
                not in cfg.get("deny_risk_levels", [])
            ]
            if len(admissible) < 3:
                raise C.FoundryError(
                    "could not assemble a safe bundle for this goal "
                    "(found %d candidate skills, need >= 3)" % len(admissible),
                    code="no_bundle",
                    detail={"hint": "pass --skills 2,41,96 or refine the goal"},
                )
            selected = [catalog.resolve(h["number"]) for h in admissible[: max(3, limit)]]
            source = "search"

    dedup = {s["number"]: s for s in selected}
    selected = [dedup[k] for k in sorted(dedup)]
    level = risk["level"]
    if level == "critical" and not args.approve:
        raise C.FoundryError(
            "goal is scored critical risk (%s); re-run with --approve after a human "
            "reviews the plan" % ", ".join(d["dimension"] for d in risk["drivers"]),
            code="approval_required",
            detail={"risk": risk, "hint": "python3 tools_catalog.py route ... --approve"},
        )
    if level in cfg.get("deny_risk_levels", []):
        raise C.FoundryError("risk level %s is denied by config.deny_risk_levels" % level,
                             code="risk_denied", detail={"risk": risk})

    bundle = build_bundle(catalog, selected, goal, cfg, rules, risk=risk)
    bundle["selection_source"] = source
    if args.approve:
        bundle["approved_by"] = args.approve
    return C.guard_output_size(bundle, cfg)


def cmd_check(args, cfg):
    root = C.tool_root()
    problems = []
    catalog_path = C.resource_path(cfg["catalog_file"])
    try:
        catalog = C.Catalog(catalog_path)
    except C.FoundryError as exc:
        problems.append({"check": "catalog_load", "ok": False, "message": exc.message})
        catalog = None
    checks = [{"check": "catalog_load", "ok": catalog is not None}]
    if catalog:
        numbers = [int(s["number"]) for s in catalog.skills]
        checks.append({"check": "skill_numbers_sequential",
                       "ok": numbers == list(range(1, len(numbers) + 1)),
                       "detail": "1..%d" % len(numbers)})
        ids = [s["id"] for s in catalog.skills]
        checks.append({"check": "skill_ids_unique", "ok": len(ids) == len(set(ids))})
        bad_counts = []
        for cat in catalog.categories:
            actual = len(catalog.by_cat.get(int(cat["number"]), []))
            if actual != int(cat["skill_count"]):
                bad_counts.append({"category": cat["name"], "declared": cat["skill_count"],
                                   "actual": actual})
        checks.append({"check": "category_counts_match", "ok": not bad_counts,
                       "detail": bad_counts or "20/20 match"})
        checks.append({"check": "descriptions_present",
                       "ok": all(s.get("description") for s in catalog.skills)})
    for name in C.MANAGED_RESOURCES:
        path = os.path.join(root, name)
        checks.append({"check": "asset_present:%s" % name, "ok": os.path.isfile(path)})
    for name in ("tools_common.py", "tools_plan.py", "tools_schedule.py",
                 "tools_scaffold.py", "tools_governance.py", "tools_selftest.py"):
        checks.append({"check": "tool_present:%s" % name,
                       "ok": os.path.isfile(os.path.join(root, name))})
    forbidden = [n for n in os.listdir(root) if n.upper() == "SKILL.MD"]
    checks.append({"check": "no_skill_md_in_tool_folder", "ok": not forbidden})
    subdirs = [n for n in os.listdir(root)
               if os.path.isdir(os.path.join(root, n)) and n != "__pycache__"]
    checks.append({"check": "no_internal_subfolders", "ok": not subdirs,
                   "detail": subdirs or "flat layout"})
    prefixed = [n for n in os.listdir(root)
                if not n.startswith(("tools", "knowledge", "asset"))
                and n != "__pycache__"]
    checks.append({"check": "all_files_prefixed", "ok": not prefixed,
                   "detail": prefixed or "ok"})
    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "tool": PROG,
        "action": "check",
        "tool_name": C.TOOL_NAME,
        "version": C.TOOL_VERSION,
        "checks": checks,
        "failed_count": len(failed),
        "catalog": catalog.summary() if catalog else None,
    }


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Automation Skill Foundry - catalog reader, text parser and "
                    "bundle router (primary entry point).",
        epilog="Examples:\n  " + "\n  ".join(
            "python3 tools_catalog.py " + e for e in MY_EXTENSION),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (C.TOOL_NAME, C.TOOL_VERSION))
    C.add_common_arguments(parser)
    common = argparse.ArgumentParser(add_help=False)
    C.add_common_arguments(common, suppress=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("list", parents=[common], help="list categories, or skills in one category",
                       description="List catalog categories. Add --category N to list its skills.")
    p.add_argument("--category", type=int, metavar="N", help="category number (1-20)")
    p.add_argument("--expand", action="store_true", help="include every skill per category")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", parents=[common], help="rank skills for a free-text need")
    p.add_argument("--query", required=True, metavar="TEXT", help="need, e.g. 'nightly invoices'")
    p.add_argument("--category", type=int, metavar="N", help="restrict to one category")
    p.add_argument("--limit", type=int, metavar="N", help="max results (default from config)")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("parse", parents=[common], help="extract skill names + descriptions from supplied text")
    p.add_argument("--input", metavar="FILE", help="plain text, Markdown, or HTML file")
    p.add_argument("path", nargs="?", help="positional alternative to --input")
    p.add_argument("--category-name", metavar="NAME", help="umbrella category name override")
    p.add_argument("--max-skills", type=int, default=500, metavar="N",
                   help="refuse inputs with more than N entries (default 500)")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("route", parents=[common], help="choose the smallest safe skill set for a goal")
    p.add_argument("--goal", required=True, nargs="+", metavar="TEXT", help="automation intent")
    p.add_argument("--skills", metavar="LIST",
                   help="explicit selection: numbers or ids, comma separated")
    p.add_argument("--limit", type=int, metavar="N", help="max skills from search")
    p.add_argument("--no-recipes", action="store_true", help="skip packaged recipe patterns")
    p.add_argument("--risk-flag", action="append", metavar="TEXT", default=[],
                   help="operator-declared risk note (repeatable)")
    p.add_argument("--approve", metavar="NAME",
                   help="record the approver for a high/critical risk bundle")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("info", parents=[common], help="show full metadata for one skill")
    p.add_argument("skill", metavar="SKILL", help="number (#132), id, or name")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("check", parents=[common], help="validate catalog + folder integrity")
    p.set_defaults(func=cmd_check)
    return parser


def main(argv=None):
    return C.run_cli(PROG, build_parser, lambda a, c: a.func(a, c), argv)


if __name__ == "__main__":
    sys.exit(main())
