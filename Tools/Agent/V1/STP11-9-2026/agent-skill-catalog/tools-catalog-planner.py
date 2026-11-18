#!/usr/bin/env python3
"""Catalog planning module: search, routing, implementation briefs, validation,
coverage/gap analysis and a heuristic evidence pre-screen.

Loaded by ``agent-skill-catalog/tools-skillcatalog.py`` via ``importlib``.
Consumes only JSON-native dicts, so it can also be imported by other tools that
already hold a parsed catalog.

Standard library only.
"""
from __future__ import annotations

import csv
import difflib
import io
import json
import re

from tools_catalog_parser import normalize_ws, slugify, tokenize

__all__ = [
    "APPROVAL_LEXICON",
    "INPUT_LEXICON",
    "build_evidence_matrix",
    "build_plan",
    "coverage_report",
    "load_keywords",
    "route_intent",
    "search",
    "validate_catalog",
]

# Lexicons drive derivation. They are data, not magic: extend them freely.
INPUT_LEXICON = {
    "link": "links and URLs", "url": "links and URLs", "http": "links and URLs",
    "file": "files and documents", "document": "files and documents",
    "note": "notes or transcripts", "transcript": "notes or transcripts",
    "repository": "repository or codebase", "code": "code or configuration",
    "dataset": "datasets or tables", "table": "datasets or tables", "metric": "metric definitions",
    "schema": "schemas or specifications", "specification": "schemas or specifications",
    "policy": "policies or procedures", "procedure": "policies or procedures",
    "log": "logs or telemetry", "telemetry": "logs or telemetry", "trace": "traces",
    "evidence": "evidence records", "source": "source references", "citation": "source references",
    "requirement": "requirements or acceptance criteria", "deadline": "dates and deadlines",
    "budget": "budget or cost figures", "cost": "budget or cost figures",
    "risk": "risk records", "decision": "decision records", "action": "action items",
    "meeting": "meeting notes or agenda", "customer": "customer records",
    "contract": "contracts or clauses", "invoice": "invoices or purchase orders",
    "image": "image assets", "video": "video assets", "audio": "audio assets",
    "game": "game design artifacts", "test": "test cases or results",
    "model": "models or parameters", "backup": "backup or restore records",
    "access": "access records or role lists", "user": "user research artifacts",
}

APPROVAL_LEXICON = {
    "destructive": (
        "delete", "remove", "purge", "destroy", "overwrite", "wipe", "truncate",
        "drop", "erase", "dispose", "deduplicate", "merge",
    ),
    "publishing": (
        "publish", "release", "deploy", "send", "post", "broadcast", "distribute",
        "go live", "announce",
    ),
    "financial": (
        "payment", "pay", "invoice", "purchase", "budget", "spend", "refund",
        "charge", "procure",
    ),
    "credential-or-security": (
        "credential", "secret", "password", "token", "key rotation", "rotate",
        "revoke", "revocation", "privilege", "privileged", "access review",
        "penetration", "exploit",
    ),
    "personal-data": (
        "personal data", "sensitive", "redact", "mask", "anonym", "privacy",
        "consent", "re-identification",
    ),
    "high-stakes": (
        "clinical", "medical", "health", "legal", "regulatory", "safety",
        "dosage", "hazard", "emergency",
    ),
}

APPROVAL_ACTIONS = {
    "destructive": "Removing, merging or overwriting stored records",
    "publishing": "Shipping to an external audience or production environment",
    "financial": "Committing money, invoices or purchase orders",
    "credential-or-security": "Rotating, revoking or exposing credentials and privileges",
    "personal-data": "Handling or exporting personal or otherwise sensitive data",
    "high-stakes": "Acting where wrong output affects health, safety or legal position",
}

BASE_VERIFICATION = [
    "Help and capability list run: `bash: python3 agent-skill-catalog/tools-skillcatalog.py --help`.",
    "Canonical catalog path given explicitly and resolved from the future skill root.",
    "Valid input produces the documented output and exit code 0.",
    "Missing required input fails fast with a non-zero exit code and an actionable message.",
    "Malformed input is rejected before any output file is written.",
    "Structured output (JSON) parses and carries `ok`, counts and any warnings.",
    "The produced artifact is opened and inspected, not assumed correct.",
    "Recovery path tested: correct the input, rerun, confirm the output changes as intended.",
]


def _tokens_with_weights(skill: dict) -> dict:
    """Return token -> weight for a skill record (name tokens weighted higher)."""
    weights: dict[str, float] = {}
    for token in tokenize(skill.get("name", "")):
        weights[token] = max(weights.get(token, 0.0), 3.0)
    for token in tokenize(skill.get("description", "")):
        weights[token] = max(weights.get(token, 0.0), 1.5)
    for token in tokenize(skill.get("category", "")):
        weights[token] = max(weights.get(token, 0.0), 0.75)
    return weights


def score_skill(skill: dict, query: str) -> tuple[float, list[str]]:
    """Score one skill against a free-text query (deterministic and explainable)."""
    query = normalize_ws(query)
    if not query:
        return 0.0, []
    q_tokens = tokenize(query)
    if not q_tokens:
        return 0.0, []
    weights = _tokens_with_weights(skill)
    total = 0.0
    matched: list[str] = []
    for token in q_tokens:
        if token in weights:
            total += weights[token]
            matched.append(token)
            continue
        partial = 0.0
        for candidate, weight in weights.items():
            if candidate.startswith(token) or token.startswith(candidate):
                partial = max(partial, weight * 0.5)
        if partial:
            total += partial
            matched.append(token + "~")
    lowered_query = query.lower()
    name = (skill.get("name") or "").lower()
    description = (skill.get("description") or "").lower()
    if len(lowered_query) > 3 and lowered_query in name:
        total += 3.0
    elif len(lowered_query) > 3 and lowered_query in description:
        total += 1.0
    coverage = len([m for m in matched if not m.endswith("~")]) / float(len(q_tokens))
    score = (total / max(len(q_tokens), 1)) + coverage
    return round(score, 3), matched


def search(catalog: dict, query: str, category: int | str | None = None,
           limit: int = 10, min_score: float = 0.0) -> list[dict]:
    """Rank skills by lexical relevance, optionally restricted to one category."""
    results = []
    for skill in catalog.get("skills", []):
        if category not in (None, "", 0):
            if not _category_matches(skill, category):
                continue
        score, matched = score_skill(skill, query)
        if score < min_score or score <= 0.0:
            continue
        results.append({
            "name": skill["name"],
            "category": skill.get("category"),
            "category_index": skill.get("category_index"),
            "description": skill.get("description"),
            "score": score,
            "matched_terms": matched,
        })
    results.sort(key=lambda item: (-item["score"], item["category_index"] or 0, item["name"]))
    return results[: max(1, limit)]


def _category_matches(skill: dict, category: int | str) -> bool:
    try:
        return int(category) == int(skill.get("category_index", -1))
    except (TypeError, ValueError):
        wanted = slugify(str(category))
        return wanted in {
            slugify(str(skill.get("category", ""))),
            slugify(str(skill.get("category_slug", ""))),
        }


def load_keywords(path: str) -> dict:
    """Load the routing keyword asset. Missing file yields an empty router."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"intents": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cannot load routing keywords %s: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("intents", []), list):
        raise ValueError("routing keywords must be an object with an 'intents' list")
    return data


def _intent_scores(text: str, keywords: dict) -> list[tuple[str, float, list[str]]]:
    lowered = " " + normalize_ws(text).lower() + " "
    text_tokens = set(tokenize(text))
    scored = []
    for intent in keywords.get("intents", []):
        signals = [normalize_ws(str(sig)).lower() for sig in intent.get("signals", [])]
        hits = []
        for sig in signals:
            if not sig:
                continue
            if sig in lowered:
                hits.append(sig)
                continue
            sig_tokens = tokenize(sig)  # e.g. "vulnerability triage" -> both words present
            if sig_tokens and all(token in text_tokens for token in sig_tokens):
                hits.append(sig)
        if not hits:
            continue
        weight = sum(1.0 + min(len(hit), 24) / 24.0 for hit in hits)
        scored.append((intent.get("id", "intent"), round(weight, 3), hits))
    scored.sort(key=lambda item: -item[1])
    return scored


def route_intent(catalog: dict, intent: str, keywords: dict, top: int = 5,
                 min_score: float = 0.0) -> dict:
    """Route a task intent to the best catalog skills, with reasons."""
    if not normalize_ws(intent):
        raise ValueError("intent text is empty")
    intent_hits = _intent_scores(intent, keywords)
    boost_categories: dict[int, float] = {}
    boost_skills: dict[str, float] = {}
    reasons: list[str] = []
    for intent_id, weight, hits in intent_hits[:3]:
        config = next((i for i in keywords.get("intents", []) if i.get("id") == intent_id), {})
        for cat in config.get("boost_categories", []) or []:
            try:
                boost_categories[int(cat)] = boost_categories.get(int(cat), 0.0) + min(weight, 4.0) * 0.5
            except (TypeError, ValueError):
                continue
        for name in config.get("boost_skills", []) or []:
            boost_skills[normalize_ws(str(name)).lower()] = boost_skills.get(
                normalize_ws(str(name)).lower(), 0.0) + min(weight, 4.0) * 0.75
        reasons.append("intent '%s' matched signals: %s" % (intent_id, ", ".join(hits[:6])))

    ranked = []
    for skill in catalog.get("skills", []):
        lexical, matched = score_skill(skill, intent)
        bonus = boost_categories.get(skill.get("category_index", -1), 0.0)
        bonus += boost_skills.get((skill.get("name") or "").lower(), 0.0)
        score = round(lexical + bonus, 3)
        if score <= 0.0 or score < min_score:
            continue
        ranked.append({
            "name": skill["name"],
            "category": skill.get("category"),
            "category_index": skill.get("category_index"),
            "description": skill.get("description"),
            "score": score,
            "lexical_score": lexical,
            "category_boost": round(bonus, 3),
            "matched_terms": matched,
            "command": "python3 agent-skill-catalog/tools-skillcatalog.py plan --skill \"%s\"" % skill["name"],
        })
    ranked.sort(key=lambda item: (-item["score"], item["category_index"] or 0, item["name"]))
    return {
        "intent": normalize_ws(intent),
        "intent_signals": [{"id": i, "weight": w, "hits": h} for i, w, h in intent_hits],
        "reasons": reasons,
        "matches": ranked[: max(1, top)],
    }


# --------------------------------------------------------------------------- #
# Implementation brief
# --------------------------------------------------------------------------- #
def _clauses(description: str) -> list[str]:
    parts = re.split(r";\s*|,\s*(?:and\s+)?|\.\s+(?=[A-Z])", description or "")
    clauses = []
    for part in parts:
        part = normalize_ws(part).strip(" .;,")
        if len(part.split()) >= 3:
            clauses.append(part[0].upper() + part[1:] if part else part)
    return clauses


def _derived_inputs(description: str) -> list[str]:
    tokens = set(tokenize(description))
    found: list[str] = []
    for token in sorted(tokens):
        label = INPUT_LEXICON.get(token)
        if label and label not in found:
            found.append(label)
    base = ["A stated objective or task request", "The canonical catalog path (`asset-catalog-240-skills.md`) or a custom catalog file"]
    return base + found


def _derived_approvals(description: str, name: str) -> list[dict]:
    haystack = (" %s %s " % (name, description)).lower()
    gates = []
    for kind, words in APPROVAL_LEXICON.items():
        hits = [word for word in words if word in haystack]
        if hits:
            gates.append({
                "kind": kind,
                "why": "Description mentions: %s" % ", ".join(sorted(set(hits))),
                "action": APPROVAL_ACTIONS[kind],
                "requirement": "Require explicit human approval with the proposed change and a rollback plan before executing.",
            })
    return gates


def _trigger_phrases(skill: dict) -> list[str]:
    name = normalize_ws(skill.get("name", ""))
    lowered = name.lower()
    phrases = [lowered, "help me with %s" % lowered, "run %s" % lowered]
    clauses = _clauses(skill.get("description", ""))
    if clauses:
        head = clauses[0]
        head = re.sub(r"^(?:turn|create|build|write|review|check|map|record|compare|design|guide|prepare|convert|generate|maintain)\s+", "", head, flags=re.I)
        phrase = normalize_ws(head).lower()
        if 3 <= len(phrase.split()) <= 9:
            phrases.append(phrase)
    seen, ordered = set(), []
    for phrase in phrases:
        if phrase and phrase not in seen:
            seen.add(phrase)
            ordered.append(phrase)
    return ordered


def build_plan(skill: dict, template: str, catalog_name: str = "asset-catalog-240-skills.md",
               generated: str | None = None) -> str:
    """Render an implementation brief for one catalog skill using a template."""
    clauses = _clauses(skill.get("description", ""))
    gates = _derived_approvals(skill.get("description", ""), skill.get("name", ""))
    steps = [
        "Confirm the trigger and restate the objective in one sentence.",
        "Validate required inputs; refuse to continue when a required item is missing.",
    ]
    if clauses:
        steps.extend("Produce: %s." % (clause[0].lower() + clause[1:]) for clause in clauses)
    steps.append("Emit structured output (JSON or Markdown) that names every produced section.")
    steps.append("Run the verification checklist and record the evidence.")

    outputs = ["- Structured result (JSON) with `ok`, `warnings` and item counts",
               "- Human-readable artifact (Markdown) for review and handoff"]
    outputs.extend("- %s" % (clause[0].upper() + clause[1:]) for clause in clauses)

    verification = list(BASE_VERIFICATION)
    verification.extend("Confirmed recorded: %s." % (clause[0].lower() + clause[1:]) for clause in clauses)
    for gate in gates:
        verification.append("Approval captured for %s before the action ran." % gate["kind"])

    values = {
        "skill_name": skill.get("name", ""),
        "skill_slug": slugify(skill.get("name", "")),
        "category": skill.get("category", ""),
        "category_index": str(skill.get("category_index", "")),
        "description": skill.get("description", ""),
        "catalog": catalog_name,
        "generated": generated or "not recorded",
        "trigger_phrases": "\n".join("- `%s`" % phrase for phrase in _trigger_phrases(skill)),
        "non_triggers": "\n".join([
            "- Requests that belong to a different catalog category with a closer name match.",
            "- Pure conversation or explanation requests that need no produced artifact.",
            "- Any request that would require credentials, paid services or network access that has not been approved.",
        ]),
        "inputs": "\n".join("- %s" % item for item in _derived_inputs(skill.get("description", ""))),
        "steps": "\n".join("%d. %s" % (position, step) for position, step in enumerate(steps, start=1)),
        "outputs": "\n".join(outputs),
        "approval_gates": "\n".join(
            "- **%s** — %s. %s. %s" % (gate["kind"], gate["action"], gate["why"], gate["requirement"])
            for gate in gates
        ) or "- None derived from the description. Still require approval before any destructive, publishing, financial, credential or personal-data action.",
        "verification": "\n".join("- [ ] %s" % item for item in verification),
        "limits": "\n".join([
            "- Input text ceiling: 8 MiB per catalog file (`--max-bytes`).",
            "- Network use is off by default; online link checks require `--online`, `--timeout`, `--retries` and `--max-urls`.",
            "- No secrets, paid services or privileged endpoints are used by this Skill Tool.",
            "- Long jobs must be cancellable: Ctrl-C aborts and partial outputs are removed.",
        ]),
        "next_commands": "\n".join([
            "```bash",
            "python3 agent-skill-catalog/tools-skillcatalog.py scaffold --name %s --from-skill \"%s\" --out ./%s --catalog agent-skill-catalog/%s"
            % (slugify(skill.get("name", "")), skill.get("name", ""), "build", catalog_name),
            "python3 agent-skill-catalog/tools-skillcatalog.py check --path ./build/%s" % slugify(skill.get("name", "")),
            "```",
        ]),
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{%s}}" % key, value)
    unresolved = sorted(set(re.findall(r"\{\{([a-z_]+)\}\}", rendered)))
    if unresolved:
        rendered += "\n\n<!-- unresolved template placeholders: %s -->\n" % ", ".join(unresolved)
    return rendered


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_catalog(catalog: dict, strict: bool = False) -> dict:
    """Validate a parsed catalog and return structured findings."""
    errors: list[dict] = []
    warnings: list[dict] = []

    def add(bucket: list, code: str, message: str, path: str = "") -> None:
        bucket.append({"code": code, "message": message, "path": path})

    categories = catalog.get("categories", [])
    skills = catalog.get("skills", [])
    if not categories:
        add(errors, "CATALOG_EMPTY", "catalog contains no categories", "categories")
    if not skills:
        add(errors, "NO_SKILLS", "catalog contains no skills", "skills")

    seen_categories: dict[str, int] = {}
    seen_skills: dict[str, str] = {}
    seen_descriptions: dict[str, str] = {}

    for position, cat in enumerate(categories, start=1):
        name = normalize_ws(cat.get("name", ""))
        path = "categories[%d]" % position
        if not name:
            add(errors, "CATEGORY_NAME_MISSING", "category name is empty", path)
        key = name.lower()
        if key in seen_categories:
            add(errors, "CATEGORY_DUPLICATE", "duplicate category name %r" % name, path)
        seen_categories[key] = position
        if not cat.get("skills"):
            add(errors, "CATEGORY_EMPTY", "category %r has no skills" % name, path)
        elif len(cat["skills"]) < 3:
            add(warnings, "CATEGORY_THIN", "category %r has only %d skills" % (name, len(cat["skills"])), path)
        for index, skill in enumerate(cat.get("skills", []), start=1):
            if skill.get("index") != index:
                add(warnings, "SKILL_INDEX_NONSEQUENTIAL", "skill index %r is not sequential at position %d"
                    % (skill.get("index"), index), path)
            skill_name = normalize_ws(skill.get("name", ""))
            skill_path = "%s.skills[%d]" % (path, index)
            if not skill_name:
                add(errors, "SKILL_NAME_MISSING", "skill name is empty", skill_path)
                continue
            if len(skill_name) > 90:
                add(errors, "SKILL_NAME_TOO_LONG", "skill name longer than 90 characters: %r" % skill_name, skill_path)
            if re.search(r"[<>]|skill\s*name\s*:", skill_name, re.I):
                add(errors, "SKILL_NAME_MARKUP", "skill name contains markup: %r" % skill_name, skill_path)
            lowered = skill_name.lower()
            if lowered in seen_skills:
                add(errors, "SKILL_DUPLICATE", "duplicate skill name %r (also at %s)" % (skill_name, seen_skills[lowered]), skill_path)
            else:
                seen_skills[lowered] = skill_path
            description = normalize_ws(skill.get("description", ""))
            if not description:
                add(errors, "SKILL_DESCRIPTION_MISSING", "skill %r has no description" % skill_name, skill_path)
            elif len(description) < 20:
                add(warnings, "SKILL_DESCRIPTION_SHORT", "skill %r description is shorter than 20 characters" % skill_name, skill_path)
            else:
                if description.lower() in seen_descriptions:
                    add(warnings, "SKILL_DESCRIPTION_DUPLICATE",
                        "skill %r repeats the description of %r" % (skill_name, seen_descriptions[description.lower()]), skill_path)
                else:
                    seen_descriptions[description.lower()] = skill_name
                if not description[0].isupper():
                    add(warnings, "SKILL_DESCRIPTION_CASE", "skill %r description does not start with a capital letter" % skill_name, skill_path)

    if strict:
        errors.extend(warnings)
        warnings = []

    return {
        "ok": not errors,
        "strict": strict,
        "errors": errors,
        "warnings": warnings,
        "counts": {"categories": len(categories), "skills": len(skills),
                   "errors": len(errors), "warnings": len(warnings)},
    }


# --------------------------------------------------------------------------- #
# Coverage and gap analysis
# --------------------------------------------------------------------------- #
def coverage_report(catalog: dict, goal: str | None = None, top_gaps: int = 15) -> dict:
    """Report catalog coverage and, given a goal, the capability gaps."""
    skills = catalog.get("skills", [])
    vocabulary: dict[str, int] = {}
    for skill in skills:
        for token in set(tokenize(skill.get("name", "") + " " + skill.get("description", ""))):
            vocabulary[token] = vocabulary.get(token, 0) + 1
    report = {
        "categories": len(catalog.get("categories", [])),
        "skills": len(skills),
        "vocabulary_size": len(vocabulary),
        "per_category": [
            {"category_index": cat["index"], "category": cat["name"], "skills": len(cat["skills"])}
            for cat in catalog.get("categories", [])
        ],
        "top_terms": sorted(vocabulary.items(), key=lambda item: (-item[1], item[0]))[:20],
        "goal": None,
        "covered_terms": [],
        "gaps": [],
    }
    if goal and normalize_ws(goal):
        goal_tokens = tokenize(goal)
        known = sorted(vocabulary)
        covered = [token for token in goal_tokens if token in vocabulary]
        missing = [token for token in goal_tokens if token not in vocabulary]
        gaps = []
        for token in missing:
            nearest = difflib.get_close_matches(token, known, n=3, cutoff=0.75)
            gaps.append({"term": token, "nearest_catalog_terms": nearest})
        report["goal"] = normalize_ws(goal)
        report["covered_terms"] = covered
        report["coverage_ratio"] = round(len(covered) / float(len(goal_tokens)), 3) if goal_tokens else 0.0
        report["gaps"] = gaps[: max(1, top_gaps)]
        report["gaps_truncated"] = max(0, len(gaps) - len(report["gaps"]))
    return report


# --------------------------------------------------------------------------- #
# Evidence pre-screen (heuristic, never a verdict)
# --------------------------------------------------------------------------- #
def build_evidence_matrix(claims: list, sources: list, min_score: float = 0.0) -> dict:
    """Build a claim-by-source pre-screen matrix.

    Overlap is lexical only. The output deliberately leaves the judgement
    columns for a human reviewer instead of pretending to decide support.
    """
    normalised_claims = []
    for position, claim in enumerate(claims, start=1):
        if isinstance(claim, str):
            normalised_claims.append({"id": "C%d" % position, "claim": normalize_ws(claim)})
        elif isinstance(claim, dict):
            text = normalize_ws(str(claim.get("claim") or claim.get("text") or ""))
            if text:
                normalised_claims.append({
                    "id": str(claim.get("id") or "C%d" % position),
                    "claim": text,
                    "source_refs": claim.get("source_refs") or [],
                })
    if not normalised_claims:
        raise ValueError("no usable claims supplied (expect strings or objects with 'claim')")
    if not sources:
        raise ValueError("no sources supplied (build a reference library first or pass an entries file)")

    rows = []
    for claim in normalised_claims:
        claim_tokens = set(tokenize(claim["claim"]))
        for source in sources:
            haystack = " ".join(str(source.get(key, "")) for key in ("title", "url", "tags", "summary", "annotation"))
            source_tokens = set(tokenize(haystack))
            overlap = sorted(claim_tokens & source_tokens)
            ratio = round(len(overlap) / float(len(claim_tokens)), 3) if claim_tokens else 0.0
            if ratio < min_score:
                continue
            if ratio >= 0.5:
                screen = "strong-lexical-overlap"
            elif ratio >= 0.25:
                screen = "partial-lexical-overlap"
            else:
                screen = "weak-lexical-overlap"
            rows.append({
                "claim_id": claim["id"],
                "claim": claim["claim"],
                "source_title": source.get("title", ""),
                "source_url": source.get("url", ""),
                "overlap_ratio": ratio,
                "screen": screen,
                "overlap_terms": overlap,
                "reviewer_support": "",
                "reviewer_confidence": "",
                "evidence_note": "Lexical pre-screen only. A human must confirm support or contradiction.",
            })
    rows.sort(key=lambda row: (row["claim_id"], -row["overlap_ratio"], row["source_title"]))
    return {
        "ok": True,
        "claims": len(normalised_claims),
        "sources": len(sources),
        "rows": rows,
        "min_score": min_score,
        "disclaimer": ("Heuristic lexical overlap only. A human must confirm support, contradiction and "
                       "source authority; this matrix never decides them."),
    }


def matrix_to_markdown(matrix: dict) -> str:
    out = ["# Evidence Pre-screen Matrix", ""]
    out.append("Claims: %d · Sources: %d · Method: %s" % (matrix["claims"], matrix["sources"], matrix["disclaimer"]))
    out.append("")
    out.append("| Claim | Source | Overlap | Screen | Overlap terms | Reviewer support | Confidence |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in matrix["rows"]:
        out.append("| %s: %s | %s | %.2f | %s | %s | _to fill_ | _to fill_ |" % (
            row["claim_id"],
            row["claim"].replace("|", "\\|"),
            (row["source_title"] or row["source_url"]).replace("|", "\\|"),
            row["overlap_ratio"],
            row["screen"],
            ", ".join(row["overlap_terms"][:8]),
        ))
    return "\n".join(out) + "\n"


def matrix_to_csv(matrix: dict) -> str:
    buffer = io.StringIO()
    fields = ["claim_id", "claim", "source_title", "source_url", "overlap_ratio", "screen",
              "overlap_terms", "reviewer_support", "reviewer_confidence", "evidence_note"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in matrix["rows"]:
        record = dict(row)
        record["overlap_terms"] = "; ".join(row["overlap_terms"])
        writer.writerow(record)
    return buffer.getvalue()
