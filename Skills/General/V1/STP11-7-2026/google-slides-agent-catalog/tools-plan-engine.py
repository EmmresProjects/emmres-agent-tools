#!/usr/bin/env python3
"""Planning engine for the google-slides-agent-catalog Skill Tool.

Responsibilities
----------------
* Tokenise and canonicalise request text (stopwords + configurable synonyms).
* Match a request against catalog skills with a deterministic scoring rule.
* Classify each matched skill against the risk policy (tier, approval requirement).
* Assemble an ordered execution plan: phases, steps, checkpoints, gates, approvals.
* Render implementation specifications from the packaged specification template.
* Compute coverage / gap reports from an implemented-skill manifest.
* Validate documents against the local schema subset stored in the plan schema asset.

This module is loaded by ``tools-gslides-catalog.py``; it is not an entry point and holds
no CLI of its own. Standard library only.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_VERSION = "1.0.0"

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "so", "as", "at", "by",
    "for", "from", "in", "into", "of", "on", "onto", "to", "with", "without", "within",
    "is", "are", "was", "were", "be", "been", "being", "it", "its", "this", "that",
    "these", "those", "we", "you", "they", "he", "she", "i", "our", "their", "your",
    "do", "does", "did", "can", "could", "should", "would", "will", "may", "might",
    "must", "not", "no", "nor", "any", "all", "each", "every", "some", "more", "most",
    "other", "such", "only", "own", "same", "too", "very", "just", "also", "up", "down",
    "out", "over", "under", "again", "further", "once", "how", "what", "when", "where",
    "which", "who", "whom", "why", "there", "here", "about", "after", "before", "while",
}

_core_module = None


def core():
    """Load the shared core module once, from the file beside this one.

    If the entry point already loaded it under this module name, that instance is reused so
    that ``except core.SkillToolError`` matches errors raised from either side.
    """
    global _core_module
    if _core_module is None:
        existing = sys.modules.get("gslides_catalog_core")
        if existing is not None and hasattr(existing, "SkillToolError"):
            _core_module = existing
            return _core_module
    if _core_module is None:
        path = os.path.join(HERE, "tools-catalog-core.py")
        spec = importlib.util.spec_from_file_location("gslides_catalog_core", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load shared core module at {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["gslides_catalog_core"] = module
        spec.loader.exec_module(module)
        _core_module = module
    return _core_module


# --------------------------------------------------------------------------------------
# Tokenisation and matching
# --------------------------------------------------------------------------------------

def stem(token):
    """Very small deterministic stemmer: enough to align plurals and -ing forms."""
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "shes", "ches", "xes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    return token


def _match_config(config):
    block = dict(config.get("match") or {})
    stop = set(_STOPWORDS)
    stop.update(str(word).lower() for word in block.get("extra_stopwords") or [])
    block["_stopwords"] = stop
    block.setdefault("min_score", 3.0)
    block.setdefault("name_weight", 3)
    block.setdefault("description_weight", 1)
    block.setdefault("category_weight", 1)
    block.setdefault("max_query_tokens", 60)
    block.setdefault("synonyms", {})
    return block


def tokenize(text, config, apply_synonyms=True):
    """Return the ordered, de-duplicated, stemmed token list for a piece of text."""
    match_cfg = _match_config(config)
    synonyms = match_cfg["synonyms"] if apply_synonyms else {}
    stop = match_cfg["_stopwords"]
    raw_tokens = re.findall(r"[a-z0-9]+", (text or "").lower().replace("'s", ""))
    tokens = []
    seen = set()
    for token in raw_tokens:
        if token in stop or len(token) < 3 and not token.isdigit():
            continue
        canonical = str(synonyms.get(token, token)).lower()
        if canonical in stop:
            continue
        canonical = stem(canonical)
        if canonical in seen:
            continue
        seen.add(canonical)
        tokens.append(canonical)
        if len(tokens) >= int(match_cfg["max_query_tokens"]):
            break
    return tokens


def surface_terms(text, config, limit=10):
    """Human-readable keywords (not stems) for draft trigger sections."""
    stop = _match_config(config)["_stopwords"]
    words = [
        word for word in re.findall(r"[a-z][a-z0-9]+", (text or "").lower())
        if word not in stop and len(word) > 2
    ]
    counts = {}
    order = []
    for word in words:
        if word not in counts:
            counts[word] = 0
            order.append(word)
        counts[word] += 1
    rank = {word: index for index, word in enumerate(order)}
    order.sort(key=lambda word: (-counts[word], rank[word]))
    return order[:limit]


def _skill_token_sets(skill, category, config):
    return {
        "name": set(tokenize(skill.get("name", ""), config)),
        "description": set(tokenize(skill.get("description", ""), config)),
        "category": set(tokenize(category.get("name", ""), config))
        | set(tokenize(category.get("purpose", ""), config)),
    }


def score_skill(query_tokens, skill, category, config):
    """Return (score, matched_terms) for one skill against the query tokens."""
    match_cfg = _match_config(config)
    sets = _skill_token_sets(skill, category, config)
    score = 0.0
    matched = []
    for token in query_tokens:
        weight = 0
        if token in sets["name"]:
            weight += int(match_cfg["name_weight"])
        if token in sets["description"]:
            weight += int(match_cfg["description_weight"])
        if token in sets["category"]:
            weight += int(match_cfg["category_weight"])
        if weight:
            score += weight
            matched.append(token)
    return round(score, 3), matched


def match_skills(catalog, request_text, config, category_ids=None, limit=None, min_score=None):
    """Rank catalog skills for a request. Deterministic ordering."""
    match_cfg = _match_config(config)
    cfg_limits = core().limits(config)
    limit = int(limit or cfg_limits["max_matches"])
    min_score = float(min_score if min_score is not None else match_cfg["min_score"])
    query_tokens = tokenize(request_text, config)
    if not query_tokens:
        raise core().SkillToolError(
            "empty-request",
            "The request contains no usable words after stopword removal.",
            hint="Describe the Slides outcome you want, for example: 'crop images and set alt text'.",
        )
    wanted = set(category_ids or [])
    results = []
    for category in catalog.get("categories", []):
        if wanted and category["id"] not in wanted:
            continue
        for skill in category.get("skills", []):
            score, matched = score_skill(query_tokens, skill, category, config)
            if score < min_score or not matched:
                continue
            results.append(
                {
                    "score": score,
                    "matched_terms": matched,
                    "skill": skill,
                    "category": category,
                }
            )
    results.sort(
        key=lambda item: (
            -item["score"],
            len(item["matched_terms"]) * -1,
            item["category"]["number"],
            item["skill"]["number"],
            item["skill"]["id"],
        )
    )
    return results[:limit], query_tokens


# --------------------------------------------------------------------------------------
# Risk classification
# --------------------------------------------------------------------------------------

def _keyword_present(keyword, haystack):
    pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def load_policy(config, path=None):
    """Load and index the risk policy."""
    policy_path = (
        core().resolve_path(path) if path
        else core().config_path(config, "risk_policy", "asset-risk-policy.json")
    )
    policy = core().load_json(policy_path, "Risk policy")
    if not isinstance(policy, dict) or not isinstance(policy.get("tiers"), list):
        raise core().SkillToolError(
            "policy-invalid",
            "Risk policy must be an object with a 'tiers' array.",
            hint="Restore asset-risk-policy.json or point --policy at a valid policy file.",
        )
    tiers = {}
    for tier in policy["tiers"]:
        if not isinstance(tier, dict) or "id" not in tier or "rank" not in tier:
            raise core().SkillToolError(
                "policy-invalid",
                "Each risk tier needs 'id' and 'rank' fields.",
            )
        tiers[tier["id"]] = tier
    if policy.get("default_tier") not in tiers:
        raise core().SkillToolError(
            "policy-invalid",
            f"default_tier {policy.get('default_tier')!r} is not defined in tiers.",
        )
    policy["_path"] = policy_path
    policy["_tiers"] = tiers
    return policy


def classify_risk(text, policy):
    """Classify text into a risk tier. Returns a dict with tier, rank and evidence."""
    haystack = " " + re.sub(r"\s+", " ", str(text or "").lower()) + " "
    tiers = policy["_tiers"]
    best = None
    for rule in policy.get("rules", []):
        tier_id = rule.get("tier")
        if tier_id not in tiers:
            continue
        hits = []
        for keyword in rule.get("keywords", []):
            if _keyword_present(keyword, haystack):
                hits.append(keyword)
        if not hits:
            continue
        candidate = {
            "tier": tier_id,
            "rank": int(tiers[tier_id].get("rank", 0)),
            "hits": hits,
        }
        if best is None:
            best = candidate
            continue
        if len(hits) > len(best["hits"]):
            best = candidate
        elif len(hits) == len(best["hits"]) and candidate["rank"] > best["rank"]:
            best = candidate
    if best is None:
        tier_id = policy["default_tier"]
        best = {"tier": tier_id, "rank": int(tiers[tier_id].get("rank", 0)), "hits": []}
    tier = tiers[best["tier"]]
    return {
        "tier": best["tier"],
        "rank": best["rank"],
        "approval_required": bool(tier.get("approval_required", False)),
        "matched_terms": best["hits"][:12],
        "description": tier.get("description", ""),
        "execution_note": tier.get("execution_note", ""),
    }


def find_hard_gates(text, policy):
    """Return hard gate terms present in the text (these always force approval)."""
    haystack = " " + re.sub(r"\s+", " ", str(text or "").lower()) + " "
    return [term for term in policy.get("hard_gate_terms", []) if _keyword_present(term, haystack)]


# --------------------------------------------------------------------------------------
# Plan assembly
# --------------------------------------------------------------------------------------

def _phases(config):
    phases = []
    for index, phase in enumerate(config.get("phases") or []):
        entry = dict(phase)
        entry["_order"] = index
        phases.append(entry)
    if not phases:
        raise core().SkillToolError(
            "config-invalid",
            "Configuration defines no phases; a plan cannot be ordered.",
            hint="Restore the 'phases' array in the configuration file.",
        )
    return phases


def phase_for_category(config, category_id):
    for phase in config.get("phases") or []:
        if category_id in (phase.get("categories") or []):
            return phase
    return {
        "id": "unclassified",
        "label": "Unclassified work",
        "purpose": "Capability not mapped to a configured phase; review the configuration.",
        "checkpoint": "Take a named version before starting unclassified work.",
        "surfaces": [],
        "categories": [],
    }


def resolve_category_ids(config, tokens):
    """Map user-supplied category tokens (number, id, or name fragment) to category ids."""
    catalog = core().load_catalog_asset(config)
    index = core().index_catalog(catalog)
    resolved = []
    unknown = []
    for token in tokens:
        needle = str(token).strip().lower()
        hit = None
        if needle.isdigit():
            for category in catalog["categories"]:
                if category["number"] == int(needle):
                    hit = category["id"]
                    break
        if hit is None and needle in index["categories"]:
            hit = needle
        if hit is None:
            for category in catalog["categories"]:
                if needle and (needle in category["id"] or needle in category["name"].lower()):
                    hit = category["id"]
                    break
        if hit:
            resolved.append(hit)
        else:
            unknown.append(token)
    if unknown:
        raise core().SkillToolError(
            "unknown-category",
            "Unknown category selector(s): " + ", ".join(sorted({str(item) for item in unknown})),
            hint="Run 'categories' to list valid ids, or pass a category number such as --category 1.",
        )
    return catalog, resolved


def build_plan(catalog, request_text, config, policy, category_ids=None, limit=None, mode="request"):
    """Assemble a complete, validated plan document."""
    limits = core().limits(config)
    phase_order = {
        str(phase.get("id")): index for index, phase in enumerate(config.get("phases") or [])
    }
    matches_raw, query_tokens = match_skills(
        catalog, request_text, config, category_ids=category_ids, limit=limit
    )
    request_gates = find_hard_gates(request_text, policy)

    matches = []
    phases_seen = {}
    gates = {}
    approvals = []
    issues = []

    for rank, item in enumerate(matches_raw, start=1):
        skill = item["skill"]
        category = item["category"]
        phase = phase_for_category(config, category["id"])
        risk = classify_risk(f"{skill['name']} {skill['description']}", policy)
        skill_gates = find_hard_gates(f"{skill['name']} {skill['description']}", policy)
        forced_by = sorted(set(skill_gates) | set(request_gates))
        approval_required = bool(risk["approval_required"] or forced_by)
        entry = {
            "rank": rank,
            "skill_id": skill["id"],
            "skill_name": skill["name"],
            "description": skill["description"],
            "category_id": category["id"],
            "category_name": category["name"],
            "phase": phase["id"],
            "score": item["score"],
            "matched_terms": item["matched_terms"],
            "risk": {
                "tier": risk["tier"],
                "rank": risk["rank"],
                "approval_required": approval_required,
                "matched_terms": risk["matched_terms"],
                "execution_note": risk["execution_note"],
            },
        }
        if forced_by:
            entry["risk"]["hard_gate_terms"] = forced_by
        matches.append(entry)

        phase_steps = phases_seen.setdefault(
            phase["id"],
            {
                "id": phase["id"],
                "order_index": phase_order.get(phase["id"], 999),
                "label": phase.get("label", phase["id"]),
                "purpose": phase.get("purpose", ""),
                "checkpoint": phase.get("checkpoint", ""),
                "steps": [],
            },
        )
        phase_steps["steps"].append(
            {
                "order": 0,
                "skill_id": skill["id"],
                "skill_name": skill["name"],
                "risk_tier": risk["tier"],
                "approval_required": approval_required,
                "surfaces": list(phase.get("surfaces") or []),
            }
        )

        if approval_required:
            reasons = []
            if risk["matched_terms"]:
                reasons.append("risk terms: " + ", ".join(risk["matched_terms"][:6]))
            if forced_by:
                reasons.append("hard gate terms: " + ", ".join(forced_by))
            approvals.append(
                {
                    "skill_id": skill["id"],
                    "skill_name": skill["name"],
                    "tier": risk["tier"],
                    "required_before": "any state change on the target presentation",
                    "reasons": reasons,
                }
            )

        for gate in config.get("gates") or []:
            if category["id"] in (gate.get("applies_to_categories") or []):
                slot = gates.setdefault(
                    gate["id"],
                    {"id": gate["id"], "requirement": gate["requirement"], "categories": []},
                )
                if category["id"] not in slot["categories"]:
                    slot["categories"].append(category["id"])

    phases = sorted(phases_seen.values(), key=lambda item: item["order_index"])
    for phase in phases:
        phase.pop("order_index", None)
        for order, step in enumerate(phase["steps"], start=1):
            step["order"] = order

    if not matches:
        issues.append(
            "No catalog capability matched the request. Rephrase with concrete Slides actions, "
            "or select a category (--category) or skill (--skill)."
        )
    mapped_categories = set()
    for phase in config.get("phases") or []:
        mapped_categories.update(phase.get("categories") or [])
    uncovered = [
        category["id"]
        for category in catalog.get("categories", [])
        if category["id"] not in mapped_categories
    ]
    for category_id in uncovered:
        issues.append(
            f"Category {category_id!r} is not mapped to any configured phase; "
            "its steps would be reported as unclassified."
        )

    review = sorted(
        {
            match["category_id"]
            for match in matches
            if match["category_id"] in (policy.get("always_review_categories") or [])
        }
    )
    notes = [
        "Plans describe intended Slides work; no Google Slides API call is made by this command.",
        "Detect feature availability before acting: account edition, admin policy, browser and rollout differ.",
        "Take a named version checkpoint before the first structural edit of any phase.",
    ]
    if review:
        notes.append("Categories in the always-review set: " + ", ".join(review))
    if approvals:
        notes.append(
            f"{len(approvals)} step(s) require explicit human approval before execution; "
            "use 'approve-check' to record the decision."
        )

    status = "ok"
    if issues:
        status = "warning"
    plan = {
        "plan_version": "1.0",
        "generated_by": {"tool": "tools-gslides-catalog.py", "version": core().CORE_VERSION},
        "request": {
            "text": request_text,
            "category_filter": ",".join(category_ids) if category_ids else None,
            "max_matches": int(limit or limits["max_matches"]),
            "mode": mode,
            "tokens": query_tokens,
        },
        "catalog": {
            "source": catalog.get("source_name") or catalog.get("title") or "catalog",
            "categories": catalog.get("counts", {}).get("categories", len(catalog.get("categories", []))),
            "skills": catalog.get("counts", {}).get("skills", 0),
        },
        "matches": matches,
        "phases": phases,
        "approvals": approvals,
        "gates": sorted(gates.values(), key=lambda gate: gate["id"]),
        "validation": {"status": status, "issues": issues},
        "notes": notes,
    }
    return plan


def render_plan_text(plan, max_steps=40):
    """Render a plan document as human-readable lines."""
    lines = []
    lines.append(f"Plan {plan['plan_version']}  |  catalog {plan['catalog']['skills']} skills across "
                 f"{plan['catalog']['categories']} categories")
    lines.append(f"Request: {plan['request']['text'][:160]}")
    if plan["request"].get("category_filter"):
        lines.append(f"Category filter: {plan['request']['category_filter']}")
    lines.append("")
    lines.append(f"Matched capabilities ({len(plan['matches'])}):")
    for match in plan["matches"]:
        flag = "APPROVAL" if match["risk"]["approval_required"] else "ok"
        lines.append(
            f"  {match['rank']:>2}. [{match['score']:>5}] {match['skill_name']}  "
            f"({match['category_id']} / {match['phase']})  risk={match['risk']['tier']} {flag}"
        )
    if not plan["matches"]:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Execution phases ({len(plan['phases'])}):")
    shown = 0
    for phase in plan["phases"]:
        lines.append(f"  {phase['id']} — {phase['label']}")
        if phase.get("purpose"):
            lines.append(f"      purpose: {phase['purpose']}")
        lines.append(f"      checkpoint: {phase['checkpoint']}")
        for step in phase["steps"]:
            if shown >= max_steps:
                lines.append("      ... step list truncated (use --json for the full plan)")
                break
            marker = "approval required" if step["approval_required"] else step["risk_tier"]
            lines.append(f"      {step['order']}. {step['skill_name']} [{marker}]")
            shown += 1
    if plan["approvals"]:
        lines.append("")
        lines.append(f"Approval gates ({len(plan['approvals'])}):")
        for approval in plan["approvals"]:
            lines.append(
                f"  - {approval['skill_name']} ({approval['tier']}) before {approval['required_before']}"
            )
            for reason in approval["reasons"]:
                lines.append(f"      reason: {reason}")
    if plan["gates"]:
        lines.append("")
        lines.append(f"Availability and platform gates ({len(plan['gates'])}):")
        for gate in plan["gates"]:
            lines.append(f"  - {gate['id']}: {gate['requirement']}")
    if plan["validation"]["issues"]:
        lines.append("")
        lines.append("Validation issues:")
        for issue in plan["validation"]["issues"]:
            lines.append(f"  - {issue}")
    lines.append("")
    lines.append("Notes:")
    for note in plan["notes"]:
        lines.append(f"  - {note}")
    return lines


# --------------------------------------------------------------------------------------
# Specification rendering
# --------------------------------------------------------------------------------------

_SPEC_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)


def _spec_values(skill, category, phase, risk, request_text):
    approvals = "yes" if risk["approval_required"] else "no"
    return {
        "{{SKILL_NAME}}": skill["name"],
        "{{SKILL_ID}}": skill["id"],
        "{{CATEGORY_NAME}}": category["name"],
        "{{CATEGORY_ID}}": category["id"],
        "{{PHASE_LABEL}}": phase.get("label", phase["id"]),
        "{{PHASE_ID}}": phase["id"],
        "{{RISK_TIER}}": risk["tier"],
        "{{APPROVAL_REQUIRED}}": approvals,
        "{{REQUEST}}": request_text or "not supplied",
        "{{DESCRIPTION}}": skill["description"],
    }


def _spec_sections(skill, category, phase, risk, config, catalog, neighbors):
    defaults = config.get("spec_defaults") or {}
    surfaces = phase.get("surfaces") or []
    gates = [
        gate for gate in (config.get("gates") or [])
        if category["id"] in (gate.get("applies_to_categories") or [])
    ]
    top_terms = surface_terms(f"{skill['name']} {skill['description']}", config)
    owner = defaults.get("owner", "Unassigned skill owner")
    version = defaults.get("version", "0.1.0")
    success = defaults.get(
        "success_condition_template",
        "The requested Slides outcome is produced as native editable objects and verified.",
    )
    deprecation = defaults.get("deprecation_policy", "Review on every Slides feature rollout.")
    sibling_text = ", ".join(f"`{name}`" for name in neighbors) if neighbors else "none in this category"
    sections = {}

    sections[1] = [
        f"- Stable id: `{skill['id']}` (lowercase kebab, unique across all "
        f"{catalog.get('counts', {}).get('skills', 0)} catalog skills).",
        f"- Registration: add a `tools-{skill['id']}.py` entry point only if this capability needs "
        "its own runtime; otherwise register it in the parent skill's capability list.",
        f"- Catalog number: category {category['number']}, skill {skill['number']}.",
        "- Decide: final command, handler or class name exposed to the orchestrator.",
    ]
    sections[2] = [
        f"- Purpose: {skill['description']}",
        f"- Category purpose: {category.get('purpose') or 'not stated'}",
        f"- Success condition: {success}",
        "- Decide: the measurable acceptance threshold for this skill (for example zero text "
        "overflow on representative slides, or a verified recipient access result).",
    ]
    sections[3] = [
        "- Triggers: requests that mention "
        + (", ".join(f"`{term}`" for term in top_terms) if top_terms else "the capability by name")
        + ".",
        "- Do not trigger when the request belongs to an adjacent capability: " + sibling_text + ".",
        "- Do not trigger for read-only inspection when the user asked only for a report.",
        "- Decide: account, edition or language conditions that should suppress the trigger.",
    ]
    sections[4] = [
        "- Inputs: presentation file id or URL, target slide ids or ranges, and the requested "
        "audience and deadline.",
        "- Structured options: an optional JSON options document supplied with `--input`.",
        "- Account prerequisites: an account where Google Slides is enabled, plus edit rights on "
        "the target file.",
    ]
    sections[4] += [f"- Platform gate `{gate['id']}`: {gate['requirement']}" for gate in gates]
    sections[4].append("- Decide: whether a preview slide, duplicate or copy is an accepted input form.")
    sections[5] = [
        "- Outputs: edited presentation state, plus a structured JSON result written to `--out`.",
        "- Side effects: a version-history entry per phase, and any permission change only after approval.",
        "- Never output: credentials, unredacted personal data, or intermediate logs in the deliverable.",
    ]
    sections[6] = [
        "- Slides surfaces: " + (", ".join(surfaces) if surfaces else "native editor controls") + ".",
        "- Automation route: Google Slides API v1 and Drive API v3 are available only through "
        "`tools-slides-api.py`, which enforces an operation allowlist and an approval token.",
        "- Sheet-linked charts, linked slides and embedded media stay in their source system; this "
        "skill records the relationship instead of flattening it.",
    ]
    sections[7] = [
        f"- Risk tier `{risk['tier']}`: {risk.get('description', '')}",
        f"- Execution note: {risk.get('execution_note', '')}",
        "- Least privilege: request only the file role needed for the task; never escalate to owner.",
        "- Privacy boundary: speaker notes, comments and version history are treated as sensitive by default.",
    ]
    sections[8] = [
        f"- Phase: {phase.get('label', phase['id'])} — {phase.get('purpose', '')}",
        f"- Checkpoint: {phase.get('checkpoint', 'Take a named version before editing.')}",
        "- Sequence: (1) capture a named version, (2) detect availability, (3) perform the smallest "
        "sufficient edit, (4) verify the result, (5) record the outcome and the version name.",
        "- Rollback: restore the named version; preserve the failed state for diagnosis first.",
    ]
    if risk["approval_required"]:
        sections[8].append(
            "- Approval: obtain an explicit human approval token before the first state change; "
            "no approval, no execution."
        )
    sections[9] = [
        "- Detect before acting: account edition, administrator policy, browser and OS, language, "
        "and staged rollout of the specific feature.",
        "- Fallback ladder: native control → Apps Script → approved add-on → documented manual route "
        "→ static alternative with a stated limitation.",
        "- Record the detected capability state in the result document.",
    ]
    sections[9] += [f"- Gate `{gate['id']}`: {gate['requirement']}" for gate in gates]
    if not gates:
        sections[9].append("- Gate: no category-specific gate is configured; verify availability manually.")
    sections[10] = [
        "- Access check: open the file and any linked asset as the intended recipient, not as the owner.",
        "- Export check: render PDF and PPTX and compare typography, tables, charts and connectors.",
        "- Playback check: presenter view, animation order, media start behaviour and skipped slides.",
        "- Accessibility check: alt text, reading order, contrast, captions and language setting.",
    ]
    sections[11] = [
        f"- Normal: {skill['name']} applied to a representative deck with the theme and layouts in place.",
        "- Boundary: empty slide, single object, maximum-length text, and the largest permitted file.",
        "- Failure: missing permission, unavailable feature, offline state, or a stale linked object.",
        "- Repair: apply the smallest responsible fix and re-run the checks in section 10.",
        "- Regression: re-run after any Slides update, theme change or import of foreign files.",
    ]
    sections[12] = [
        f"- Owner: {owner}. Version `{version}`. Catalog edition: {catalog.get('schema_version', '1.0')}.",
        f"- Deprecation: {deprecation}",
        "- Compatibility: keep the plan document schema at `plan_version` 1.0; bump on breaking change.",
    ]
    return sections


def render_spec(skill, category, config, policy, template_text, catalog, request_text=""):
    """Render a full implementation specification document from the packaged template."""
    phase = phase_for_category(config, category["id"])
    risk = classify_risk(f"{skill['name']} {skill['description']}", policy)
    neighbors = [
        other["id"] for other in category.get("skills", []) if other["id"] != skill["id"]
    ][:3]
    values = _spec_values(skill, category, phase, risk, request_text)
    sections = _spec_sections(skill, category, phase, risk, config, catalog, neighbors)

    head, _, _ = template_text, None, None
    matches = list(_SPEC_SECTION_RE.finditer(template_text))
    if not matches:
        raise core().SkillToolError(
            "template-invalid",
            "Specification template contains no '## N. Title' section headings.",
            hint="Restore asset-spec-template.md or supply a template that uses '## 1.' style headings.",
        )
    intro = template_text[: matches[0].start()]
    for token, value in values.items():
        intro = intro.replace(token, str(value))

    out_lines = [intro.rstrip(), ""]
    rendered = 0
    seen_numbers = set()
    for index, match in enumerate(matches):
        number = int(match.group(1))
        title = match.group(2)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(template_text)
        body = template_text[start:end].strip()
        out_lines.append(f"## {number}. {title}")
        out_lines.append("")
        if number in sections and number not in seen_numbers:
            seen_numbers.add(number)
            out_lines.extend(sections[number])
            rendered += 1
        elif body:
            out_lines.append(body)
        else:
            out_lines.append("- Decide: fill this section during implementation review.")
        out_lines.append("")
    if rendered == 0:
        raise core().SkillToolError(
            "template-invalid",
            "None of the template sections matched the specification generator (expected 1-12).",
        )
    document = "\n".join(out_lines).rstrip() + "\n"
    return document, {
        "sections_rendered": rendered,
        "sections_total": len(matches),
        "skill_id": skill["id"],
        "risk_tier": risk["tier"],
        "phase": phase["id"],
    }


# --------------------------------------------------------------------------------------
# Coverage and gap reporting
# --------------------------------------------------------------------------------------

VALID_STATUSES = ("implemented", "partial", "planned", "absent")


def _normalise_manifest(manifest):
    """Accept {'skills': {...}} or {'skills': [...]} and return {skill_id: status}."""
    if not isinstance(manifest, dict):
        raise core().SkillToolError(
            "manifest-invalid",
            "Coverage manifest must be a JSON object.",
            hint='Use {"skills": {"<skill-id>": "implemented"}} or {"skills": ["<skill-id>"]}.',
        )
    skills = manifest.get("skills", manifest)
    mapping = {}
    if isinstance(skills, list):
        for entry in skills:
            if isinstance(entry, str):
                mapping[entry] = "implemented"
            elif isinstance(entry, dict) and entry.get("id"):
                mapping[str(entry["id"])] = str(entry.get("status", "implemented"))
            else:
                raise core().SkillToolError(
                    "manifest-invalid",
                    "List entries must be skill id strings or objects with an 'id'.",
                )
    elif isinstance(skills, dict):
        for key, value in skills.items():
            if isinstance(value, dict):
                mapping[str(key)] = str(value.get("status", "implemented"))
            else:
                mapping[str(key)] = str(value)
    else:
        raise core().SkillToolError("manifest-invalid", "'skills' must be an object or array.")
    for key, status in mapping.items():
        if status not in VALID_STATUSES:
            raise core().SkillToolError(
                "manifest-invalid",
                f"Unknown status {status!r} for skill {key!r}.",
                hint="Allowed statuses: " + ", ".join(VALID_STATUSES) + ".",
            )
    return mapping


def coverage_report(catalog, manifest, config=None, suggest=None):
    """Compute per-category and overall coverage from an implemented-skill manifest."""
    mapping = _normalise_manifest(manifest)
    index = core().index_catalog(catalog)
    import difflib
    unknown = sorted(key for key in mapping if key not in index["skills"])
    if unknown:
        suggestions = []
        for key in unknown[:10]:
            close = difflib.get_close_matches(key, list(index["skills"]), n=1, cutoff=0.6)
            suggestions.append({"input": key, "suggestion": close[0] if close else None})
        raise core().SkillToolError(
            "unknown-skill",
            f"{len(unknown)} manifest entr(y/ies) do not match any catalog skill id.",
            hint="Run 'list --json' to read exact skill ids, then fix the manifest.",
            details={"unknown": unknown[:20], "suggestions": suggestions},
        )

    rows = []
    totals = {"skills": 0, "implemented": 0, "partial": 0, "planned": 0, "absent": 0, "unmapped": 0}
    gaps = []
    for category in catalog.get("categories", []):
        row = {
            "category_id": category["id"],
            "category_name": category["name"],
            "skills": category["skill_count"],
            "implemented": 0,
            "partial": 0,
            "planned": 0,
            "absent": 0,
            "unmapped": 0,
        }
        for skill in category.get("skills", []):
            status = mapping.get(skill["id"], "unmapped")
            row[status if status != "unmapped" else "unmapped"] += 1
            totals["skills"] += 1
            if status == "unmapped":
                totals["unmapped"] += 1
                gaps.append({"skill_id": skill["id"], "skill_name": skill["name"],
                             "category_id": category["id"], "status": "unmapped"})
            else:
                totals[status] += 1
                if status in ("planned", "absent"):
                    gaps.append({"skill_id": skill["id"], "skill_name": skill["name"],
                                 "category_id": category["id"], "status": status})
        denominator = row["skills"] or 1
        row["coverage"] = round((row["implemented"] + 0.5 * row["partial"]) / denominator, 3)
        rows.append(row)

    overall = round((totals["implemented"] + 0.5 * totals["partial"]) / (totals["skills"] or 1), 3)
    return {
        "report_version": "1.0",
        "catalog": {
            "source": catalog.get("source_name") or catalog.get("title") or "catalog",
            "categories": len(catalog.get("categories", [])),
            "skills": totals["skills"],
        },
        "overall_coverage": overall,
        "totals": totals,
        "categories": rows,
        "gaps": gaps[: (core().limits(config or {"limits": {}})["max_report_items"] if config else 500)],
        "gap_count": len(gaps),
    }


def render_coverage_text(report):
    lines = [
        f"Coverage report  |  {report['catalog']['skills']} skills across "
        f"{report['catalog']['categories']} categories",
        f"Overall coverage: {report['overall_coverage'] * 100:.1f}% "
        f"(implemented {report['totals']['implemented']}, partial {report['totals']['partial']}, "
        f"planned {report['totals']['planned']}, absent {report['totals']['absent']}, "
        f"unmapped {report['totals']['unmapped']})",
        "",
        "Per category:",
    ]
    for row in report["categories"]:
        lines.append(
            f"  {row['coverage'] * 100:5.1f}%  {row['category_id']:<48} "
            f"impl {row['implemented']:>2} partial {row['partial']:>2} "
            f"planned {row['planned']:>2} absent {row['absent']:>2} unmapped {row['unmapped']:>2}"
        )
    lines.append("")
    lines.append(f"Gaps ({report['gap_count']}):")
    for gap in report["gaps"][:40]:
        lines.append(f"  - [{gap['status']}] {gap['skill_id']} ({gap['category_id']})")
    if report["gap_count"] > 40:
        lines.append(f"  ... {report['gap_count'] - 40} more (use --json for the full list)")
    return lines


# --------------------------------------------------------------------------------------
# Local schema-subset validator
# --------------------------------------------------------------------------------------

def validate_against_schema(value, schema, path="plan"):
    """Validate a value against the supported JSON Schema subset. Returns issue strings."""
    issues = []

    def fail(message):
        issues.append(f"{path}: {message}")

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        checks = {
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "null": lambda v: v is None,
        }
        if not any(checks.get(kind, lambda v: True)(value) for kind in types):
            fail(f"expected type {'/'.join(types)}, got {type(value).__name__}")
            return issues

    if "enum" in schema and value not in schema["enum"]:
        fail(f"value {value!r} is not one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"value {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"value {value} is above maximum {schema['maximum']}")
    if isinstance(value, str) and "pattern" in schema:
        if not re.search(schema["pattern"], value):
            fail(f"string {value!r} does not match pattern {schema['pattern']!r}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                fail(f"missing required property {key!r}")
        properties = schema.get("properties") or {}
        for key, sub_schema in properties.items():
            if key in value:
                issues.extend(validate_against_schema(value[key], sub_schema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    fail(f"unexpected property {key!r}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            fail(f"array has {len(value)} item(s), minimum is {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"array has {len(value)} item(s), maximum is {schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                issues.extend(validate_against_schema(item, item_schema, f"{path}[{index}]"))
    return issues
