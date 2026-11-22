#!/usr/bin/env python3
"""tools-catalog-engine.py

Shared, dependency-free catalog engine for the HumanSphere Holistic Support
Skill Tool: loading, normalisation, tag/risk derivation, keyword search, and
smallest-set skill routing.

This module is a library, not the user-facing entry point. The primary entry
point is tools-humansphere.py, which loads this file by relative path.

Relative path from the future skill root:
    humansphere-holistic-support/tools-catalog-engine.py
Default catalog asset:
    humansphere-holistic-support/asset-skill-catalog.json
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Hard limits (bound every loop and every input; no network, no subprocess)
# ---------------------------------------------------------------------------
LIMITS = {
    "max_query_chars": 4000,
    "max_source_bytes": 2_000_000,
    "max_catalog_skills": 2000,
    "max_skills": 8,
    "default_max_skills": 5,
    "default_timeout_seconds": 10.0,
    "max_tags_per_skill": 24,
    "max_steps_per_skill": 8,
    "max_search_results": 50,
}

CATALOG_SCHEMA = "humansphere.skill-catalog/1"

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can cannot could did do does doing done down
    during each either else etc few for from further had has have having he her here hers
    herself him himself his how i if in into is it its itself just like me more most my
    myself no nor not now of off on once only or other ought our ours ourselves out over
    own same she should so some such than that the their theirs them themselves then there
    these they this those through to too under until up upon us very was we were what when
    where which while who whom why will with within without would you your yours yourself
    yourselves helps help user users supports support support's part focused skill skills
    name description make makes made using use uses such avoid avoids avoids
    """.split()
)

# Category-level keyword boosts: words that suggest a whole category.
CATEGORY_KEYWORDS = {
    1: "feel feelings feeling emotional emotion upset sad lonely grief loss comfort listen "
       "overwhelmed vent hurt crying check-in motivation celebrate boundary compassion",
    2: "stress anxious anxiety worry panic mood depression burnout resilience mindfulness "
       "rumination therapy coping psychological mental regulation reframe",
    3: "crisis danger safety emergency self-harm suicide abuse violence overdose unsafe "
       "hurt myself harassment threaten panic attack urgent",
    4: "sleep health body pain fatigue movement exercise nutrition meal food hydration "
       "medication symptom chronic condition energy ergonomic checkup",
    5: "money budget cost costs debt savings bill bills expense expenses income financial "
       "scam fraud tuition rent savings overspend",
    6: "computer laptop phone device technology tech app apps account password internet "
       "wifi backup software digital online file files troubleshoot",
    7: "faith spiritual meaning purpose values prayer belief religion ritual gratitude "
       "existential mortality nature observance",
    8: "persona tone style character voice personality identity pronouns expression humor "
       "fiction roleplay presentation",
    9: "relationship relationships friend friends family partner couple parent parenting "
       "conflict apology breakup social connection invitation caregiver",
    10: "work job career study school exam goal goals procrastination focus habit decision "
        "leadership interview resume deadline transition",
    11: "culture cultural language migration immigrant belonging tradition celebrate "
        "translation interfaith diaspora heritage",
    12: "adhd autism dyslexia neurodiverse accessibility executive function sensory "
        "accommodation disability assistive screen reader captions",
    13: "creative creativity hobby journaling story writing play confidence challenge "
        "learning project growth practice",
    14: "home routine groceries meal errand appointment travel emergency preparedness "
        "records maintenance organization house chores",
    15: "student employee caregiver older adult entrepreneur remote worker creative "
        "professional international reintegration",
    16: "preference memory continuity consent personalization profile handoff session "
        "tone directness explanation",
    17: "referral navigator provider clinician therapist doctor services clinic benefits "
        "shelter legal hotline appointment healthcare",
    18: "review safety ethics governance scope dependency stereotype privacy evidence "
        "approval escalation quality",
}

HIGH_RISK_TERMS = (
    "self-harm", "suicide", "overdose", "poison", "abuse", "crisis", "emergency",
    "danger", "red-flag", "red flag", "withdrawal", "harassment", "violence",
)
ELEVATED_RISK_TERMS = (
    "medical", "medication", "diagnos", "clinician", "prescriber", "pharmacist",
    "legal", "financial", "fraud", "scam", "debt", "disability", "minors",
    "licensed", "authorit", "emergency fund", "hardship", "guardian",
)
HIGH_RISK_CATEGORIES = (3,)
ELEVATED_RISK_CATEGORIES = (5, 12, 17, 18)


class CatalogError(Exception):
    """Structured, recoverable error raised by the catalog engine."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict:
        out = {"status": "error", "code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out


class EngineTimeout(CatalogError):
    def __init__(self, seconds: float):
        super().__init__(
            "timeout",
            f"Operation exceeded the {seconds:g}s deadline.",
            "Re-run with a shorter query, a smaller --max-skills value, or a larger --timeout.",
        )


class Deadline:
    """Cooperative deadline used to bound catalog scans and routing loops."""

    def __init__(self, seconds: float):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = LIMITS["default_timeout_seconds"]
        self.seconds = max(0.05, min(seconds, 300.0))
        self.expires_at = time.monotonic() + self.seconds

    def check(self) -> None:
        if time.monotonic() > self.expires_at:
            raise EngineTimeout(self.seconds)


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------
def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def normalize_text(text: str) -> str:
    text = strip_accents(str(text or "")).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    out = []
    for token in normalize_text(text).split():
        if len(token) < 2:
            continue
        if not keep_stopwords and token in STOPWORDS:
            continue
        out.append(stem(token))
    return out


def stem(token: str) -> str:
    """Deliberately small suffix stripper; keeps tags stable without a stemmer dep."""
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            if suffix == "ies":
                return token[: -len(suffix)] + "y"
            return token[: -len(suffix)]
    return token


def slugify(value: str) -> str:
    slug = normalize_text(value).replace(" ", "-")
    return re.sub(r"-{2,}", "-", slug).strip("-") or "item"


# ---------------------------------------------------------------------------
# Skill normalisation, tag and risk derivation
# ---------------------------------------------------------------------------
def derive_tags(name: str, description: str, category: str = "") -> list[str]:
    seen: list[str] = []
    for token in tokenize(f"{name} {name} {description} {category}"):
        if token not in seen:
            seen.append(token)
        if len(seen) >= LIMITS["max_tags_per_skill"]:
            break
    return seen


def derive_risk(skill: dict) -> tuple[str, bool, list[str]]:
    """Return (risk_level, needs_human_referral, risk_reasons)."""
    haystack = f"{skill.get('name', '')} {skill.get('description', '')}".lower()
    category_id = int(skill.get("category_id") or 0)
    reasons: list[str] = []

    level = "standard"
    if category_id in HIGH_RISK_CATEGORIES:
        level = "high"
        reasons.append(f"category {category_id} is crisis/safety")
    elif category_id in ELEVATED_RISK_CATEGORIES:
        level = "elevated"
        reasons.append(f"category {category_id} touches regulated guidance")

    for term in HIGH_RISK_TERMS:
        if term in haystack:
            level = "high"
            reasons.append(f"mentions '{term}'")
            break
    if level != "high":
        for term in ELEVATED_RISK_TERMS:
            if term in haystack:
                level = "elevated"
                reasons.append(f"mentions '{term}'")
                break

    referral_markers = (
        "licensed", "professional", "clinician", "pharmacist", "prescriber",
        "authorities", "navigator", "referral", "human review", "qualified",
    )
    needs_referral = level in ("high", "elevated") or any(
        marker in haystack for marker in referral_markers
    )
    if needs_referral and not reasons:
        reasons.append("description requires qualified human help")
    return level, needs_referral, reasons


def normalize_skill(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise CatalogError(
            "invalid_skill",
            "A skill entry is missing its name.",
            "Re-run parse-source on a catalog that contains 'Skill Name:' entries.",
        )
    description = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    category = str(raw.get("category") or "Uncategorised").strip() or "Uncategorised"
    category_id = int(raw.get("category_id") or 0)
    skill = {
        "id": str(raw.get("id") or "").strip(),
        "name": name,
        "slug": str(raw.get("slug") or slugify(name)),
        "category": category.replace("|", "").strip(),
        "category_id": category_id,
        "description": description,
    }
    if not skill["id"]:
        raise CatalogError("invalid_skill", f"Skill '{name}' is missing its numeric id.")
    tags = raw.get("tags")
    skill["tags"] = (
        [str(t) for t in tags][: LIMITS["max_tags_per_skill"]]
        if isinstance(tags, list) and tags
        else derive_tags(name, description, category)
    )
    if raw.get("risk"):
        skill["risk"] = str(raw["risk"])
        skill["needs_human_referral"] = bool(raw.get("needs_human_referral", False))
        skill["risk_reasons"] = [str(r) for r in (raw.get("risk_reasons") or [])]
    else:
        level, referral, reasons = derive_risk(skill)
        skill["risk"] = level
        skill["needs_human_referral"] = referral
        skill["risk_reasons"] = reasons
    return skill


# Third-person description verbs mapped to their imperative form. A fixed table
# keeps step generation deterministic and reviewable (no statistical guesswork).
VERB_MAP = {
    "responds": "respond", "reflects": "reflect", "asks": "ask", "avoids": "avoid",
    "helps": "help", "uses": "use", "provides": "provide", "offers": "offer",
    "supports": "support", "creates": "create", "builds": "build", "plans": "plan",
    "combines": "combine", "organizes": "organize", "organises": "organise",
    "explains": "explain", "suggests": "suggest", "gives": "give", "tracks": "track",
    "reviews": "review", "compares": "compare", "drafts": "draft", "leads": "lead",
    "teaches": "teach", "develops": "develop", "maintains": "maintain", "adjusts": "adjust",
    "stores": "store", "chooses": "choose", "handles": "handle", "checks": "check",
    "groups": "group", "reduces": "reduce", "matches": "match", "finds": "find",
    "sorts": "sort", "sets": "set", "follows": "follow", "prepares": "prepare",
    "turns": "turn", "designs": "design", "practices": "practice", "preserves": "preserve",
    "invites": "invite", "adapts": "adapt", "balances": "balance", "prompts": "prompt",
    "recognises": "recognise", "recognizes": "recognize", "requires": "require",
    "separates": "separate", "minimises": "minimise", "minimizes": "minimize",
    "maps": "map", "names": "name", "lists": "list", "records": "record",
    "writes": "write", "keeps": "keep", "explores": "explore", "guides": "guide",
    "shares": "share", "includes": "include", "respects": "respect",
    "structures": "structure", "models": "model", "works": "work",
    "describes": "describe", "identifies": "identify", "clarifies": "clarify",
    "proposes": "propose", "encourages": "encourage", "confirms": "confirm",
    "simplifies": "simplify", "summarises": "summarise", "summarizes": "summarize",
    "discusses": "discuss", "labels": "label", "connects": "connect",
    "removes": "remove", "adds": "add", "calculates": "calculate",
    "estimates": "estimate", "enables": "enable", "improves": "improve",
    "covers": "cover", "collects": "collect", "monitors": "monitor", "tests": "test",
    "applies": "apply", "returns": "return", "reaches": "reach", "stops": "stop",
    "starts": "start", "notices": "notice", "thanks": "thank", "celebrates": "celebrate",
    "organizes": "organize", "notes": "note", "questions": "question",
    "selects": "select", "directs": "direct", "defines": "define",
    "protects": "protect", "demonstrates": "demonstrate", "focuses": "focus",
    "moves": "move", "shortens": "shorten", "splits": "split",
    "distinguishes": "distinguish", "allocates": "allocate", "revises": "revise",
    "redirects": "redirect", "replaces": "replace", "restates": "restate",
    "flags": "flag", "drops": "drop", "scales": "scale", "pairs": "pair",
    "prioritises": "prioritise", "prioritizes": "prioritize", "tracks": "track",
}
ACTION_VERBS = frozenset(VERB_MAP.values())
_SPLIT_RE = re.compile(r";\s*|,\s*(?:and\s+)?|\s+and\s+(?=[a-z])|\s+while\s+")


_OBJECT_STARTERS = frozenset(
    "the a an your their his her our this that these those it them him every each "
    "one two three user users someone people what who".split()
)


def _is_action(word: str, following: str = "") -> bool:
    """True when `word` starts a genuinely new action clause."""
    lowered = re.sub(r"[^a-z']", "", word.lower())
    if not lowered:
        return False
    if lowered in VERB_MAP:
        return True
    if lowered not in ACTION_VERBS:
        return False
    # A bare imperative only starts a new clause when it takes an object here;
    # otherwise it is probably an enumerated noun ("practice", "respect for").
    tail = re.match(r"[A-Za-z']+\s+([A-Za-z']+)", following)
    return bool(tail and tail.group(1).lower() in _OBJECT_STARTERS)


def _imperative(clause: str) -> str:
    match = re.match(r"([A-Za-z']+)(\s+)(.*)$", clause)
    if not match:
        return clause[0].upper() + clause[1:] if clause else clause
    word = match.group(1)
    base = VERB_MAP.get(word.lower())
    if base:
        rest = match.group(2) + match.group(3)
        return base[0].upper() + base[1:] + rest
    lowered = word.lower()
    if lowered in ACTION_VERBS:
        return lowered[0].upper() + lowered[1:] + match.group(2) + match.group(3)
    bare = stem(lowered)
    if bare in ACTION_VERBS:
        return bare[0].upper() + bare[1:] + match.group(2) + match.group(3)
    return clause[0].upper() + clause[1:]


def skill_steps(skill: dict, max_steps: int = LIMITS["max_steps_per_skill"]) -> list[str]:
    """Turn a skill description into short, checkable operating prompts.

    The description is the authoring contract for the capability, so its action
    clauses become the prompts the plan surfaces to the responder. Clauses split
    only at real action boundaries, so list objects stay attached to their verb.
    """
    text = re.sub(r"\s+", " ", str(skill.get("description") or "")).strip().rstrip(".")
    if not text:
        return []
    steps: list[str] = []
    buffer = ""
    remainder = text
    pieces: list[str] = []
    position = 0
    for match in _SPLIT_RE.finditer(text):
        head = text[position:match.start()]
        following = text[match.end():]
        next_word = re.match(r"[A-Za-z']+", following)
        boundary = match.group(0).strip().startswith(";") or (
            next_word is not None and _is_action(next_word.group(0), following)
        )
        if boundary and head.strip():
            pieces.append(head.strip())
            position = match.end()
    tail = text[position:]
    if tail.strip():
        pieces.append(tail.strip())
    del buffer, remainder
    for piece in pieces:
        piece = re.sub(r"^(?:and|but|or|then|plus)\s+", "", piece.strip(), flags=re.I)
        piece = re.sub(r"\s+", " ", piece).strip(" .,;:")
        if len(piece) < 3:
            continue
        steps.append(_imperative(piece))
        if len(steps) >= max_steps:
            break
    return steps


# ---------------------------------------------------------------------------
# Catalog load / save
# ---------------------------------------------------------------------------
def load_catalog(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise CatalogError(
            "catalog_missing",
            f"Catalog file not found: {path}",
            "Pass --catalog with a valid path, or use the shipped "
            "humansphere-holistic-support/asset-skill-catalog.json.",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(
            "catalog_invalid_json",
            f"Catalog is not valid JSON: {path} ({exc.msg} at line {exc.lineno}).",
            "Re-generate it with the parse-source command.",
        ) from exc
    return catalog_from_dict(raw)


def catalog_from_dict(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise CatalogError("catalog_invalid", "Catalog root must be a JSON object.")
    raw_skills = raw.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise CatalogError(
            "catalog_empty",
            "Catalog contains no skills.",
            "Provide a catalog with at least one skill entry.",
        )
    if len(raw_skills) > LIMITS["max_catalog_skills"]:
        raise CatalogError(
            "catalog_too_large",
            f"Catalog has {len(raw_skills)} skills; limit is {LIMITS['max_catalog_skills']}.",
            "Split the catalog or raise LIMITS['max_catalog_skills'] in tools-catalog-engine.py.",
        )

    skills: list[dict] = []
    seen_ids: set[str] = set()
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            raise CatalogError("invalid_skill", "Every skill entry must be a JSON object.")
        skill = normalize_skill(raw_skill)
        if skill["id"] in seen_ids:
            raise CatalogError(
                "duplicate_skill_id",
                f"Duplicate skill id '{skill['id']}'.",
                "Renumber the catalog so every skill id is unique.",
            )
        seen_ids.add(skill["id"])
        skills.append(skill)

    categories: dict[int, dict] = {}
    for skill in skills:
        categories.setdefault(
            skill["category_id"],
            {"id": skill["category_id"], "name": skill["category"], "skill_count": 0},
        )
        categories[skill["category_id"]]["skill_count"] += 1

    return {
        "schema": raw.get("schema") or CATALOG_SCHEMA,
        "title": raw.get("title") or "Skill catalog",
        "source": raw.get("source") or "",
        "categories": [categories[key] for key in sorted(categories)],
        "skills": skills,
        "counts": {"skills": len(skills), "categories": len(categories)},
    }


def save_catalog(catalog: dict, path: str | Path, approve: bool = False) -> dict:
    path = Path(path)
    payload = {
        "schema": catalog.get("schema", CATALOG_SCHEMA),
        "title": catalog.get("title", "Skill catalog"),
        "source": catalog.get("source", ""),
        "counts": {
            "skills": len(catalog["skills"]),
            "categories": len(catalog["categories"]),
        },
        "categories": catalog["categories"],
        "skills": catalog["skills"],
    }
    existed = path.exists()
    if existed and not approve:
        raise CatalogError(
            "approval_required",
            f"Refusing to overwrite existing file without approval: {path}",
            "Re-run with --approve to confirm overwriting, or choose a new --out path.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    return {"path": str(path), "overwrote": existed, "bytes": path.stat().st_size}


def atomic_write_json(path: Path, payload: dict) -> None:
    """Crash-safe write: temp file in the same directory, then atomic replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Search and routing
# ---------------------------------------------------------------------------
def find_skill(catalog: dict, needle: str) -> dict:
    needle = str(needle or "").strip()
    if not needle:
        raise CatalogError("missing_argument", "A skill id, slug, or name is required.")
    if needle.isdigit():
        for skill in catalog["skills"]:
            if skill["id"] == needle:
                return skill
    key = normalize_text(needle).replace(" ", "-")
    for skill in catalog["skills"]:
        if key in (skill["slug"], normalize_text(skill["name"]).replace(" ", "-")):
            return skill
    normalised = normalize_text(needle)
    matches = [s for s in catalog["skills"] if normalised and normalised in normalize_text(s["name"])]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise CatalogError(
            "ambiguous_skill",
            f"'{needle}' matches {len(matches)} skills.",
            "Use the numeric skill id instead, for example --skill 51.",
        )
    raise CatalogError(
        "skill_not_found",
        f"No skill matches '{needle}'.",
        "Run the list command to see ids, or the search command to find a slug.",
    )


def list_skills(catalog: dict, category: str | None = None) -> list[dict]:
    if not category:
        return list(catalog["skills"])
    wanted = normalize_text(category)
    selected = [
        skill
        for skill in catalog["skills"]
        if wanted in normalize_text(skill["category"]) or wanted == str(skill["category_id"])
    ]
    if not selected:
        raise CatalogError(
            "category_not_found",
            f"No category matches '{category}'.",
            "Run the capabilities command to list the available categories.",
        )
    return selected


def search(catalog: dict, query: str, limit: int = 10) -> list[dict]:
    query = _guard_query(query)
    limit = max(1, min(int(limit), LIMITS["max_search_results"]))
    q_tokens = set(tokenize(query))
    if not q_tokens:
        raise CatalogError(
            "empty_query",
            "The search query has no usable words.",
            "Describe the need in a few plain words, for example: 'I cannot sleep'.",
        )
    results = []
    for skill in catalog["skills"]:
        name_tokens = set(tokenize(skill["name"]))
        tag_tokens = set(skill["tags"])
        desc_tokens = set(tokenize(skill["description"]))
        score = 0.0
        matched: list[str] = []
        for token in q_tokens:
            if token in name_tokens:
                score += 3.0
                matched.append(token)
            elif token in tag_tokens:
                score += 1.5
                matched.append(token)
            elif token in desc_tokens:
                score += 0.5
                matched.append(token)
        if score > 0:
            results.append(
                {
                    "id": skill["id"],
                    "name": skill["name"],
                    "slug": skill["slug"],
                    "category": skill["category"],
                    "category_id": skill["category_id"],
                    "risk": skill["risk"],
                    "score": round(score, 2),
                    "matched_terms": sorted(set(matched)),
                }
            )
    results.sort(key=lambda item: (-item["score"], int(item["id"])))
    return results[:limit]


def _guard_query(query: str) -> str:
    query = str(query or "").strip()
    if not query:
        raise CatalogError(
            "missing_argument",
            "A --query value is required.",
            "Example: --query 'I feel overwhelmed and my laptop is failing'.",
        )
    if len(query) > LIMITS["max_query_chars"]:
        raise CatalogError(
            "query_too_long",
            f"Query is {len(query)} characters; limit is {LIMITS['max_query_chars']}.",
            "Summarise the need in a few sentences.",
        )
    return query


def route(
    catalog: dict,
    query: str,
    max_skills: int = LIMITS["default_max_skills"],
    timeout: float = LIMITS["default_timeout_seconds"],
    force_governance: bool = True,
) -> dict:
    """Pick the smallest useful set of skills for a stated need.

    Scoring is deterministic and explainable: name hits weigh more than tag hits,
    tag hits weigh more than description hits, whole-query phrases weigh most,
    and a category-level keyword boost rewards topical coherence.
    """
    query = _guard_query(query)
    deadline = Deadline(timeout)
    max_skills = max(1, min(int(max_skills), LIMITS["max_skills"]))
    q_tokens = set(tokenize(query))
    if not q_tokens:
        raise CatalogError(
            "empty_query",
            "The query has no usable words.",
            "Describe the need in a few plain words.",
        )
    q_norm = normalize_text(query)
    category_matches = {
        cid: sorted(q_tokens & set(tokenize(words))) for cid, words in CATEGORY_KEYWORDS.items()
    }
    category_boost = {cid: len(matched) for cid, matched in category_matches.items()}

    scored = []
    for skill in catalog["skills"]:
        deadline.check()
        name_tokens = set(tokenize(skill["name"]))
        tag_tokens = set(skill["tags"])
        desc_tokens = set(tokenize(skill["description"]))
        score = 0.0
        matched: list[str] = []
        for token in q_tokens:
            if token in name_tokens:
                score += 3.0
                matched.append(token)
            elif token in tag_tokens:
                score += 1.5
                matched.append(token)
            elif token in desc_tokens:
                score += 0.5
                matched.append(token)
        if normalize_text(skill["name"]) and normalize_text(skill["name"]) in q_norm:
            score += 4.0
            matched.append(skill["name"].lower())
        if score > 0:
            # Category boost only amplifies a real lexical match; a totally
            # unmatched skill never enters the candidate pool this way.
            score += 0.6 * category_boost.get(skill["category_id"], 0)
        if score <= 0:
            continue
        scored.append({"skill": skill, "score": score, "matched": sorted(set(matched))})

    # A whole category can be relevant even when no single skill's wording matches
    # the query. Add one representative so the plan does not silently drop a domain.
    covered = {item["skill"]["category_id"] for item in scored}
    for cid, matched in category_matches.items():
        if cid in covered or not matched:
            continue
        deadline.check()
        members = [s for s in catalog["skills"] if s["category_id"] == cid]
        if not members:
            continue
        scored.append(
            {
                "skill": members[0],
                "score": round(0.6 * len(matched), 2),
                "matched": matched,
                "category_only": True,
            }
        )

    scored.sort(key=lambda item: (-item["score"], int(item["skill"]["id"])))

    per_category_cap = 2
    selected: list[dict] = []
    per_category: dict[int, int] = {}
    chosen_ids: set[str] = set()

    def _add(item: dict) -> None:
        skill = item["skill"]
        reason = (
            f"No skill wording matched directly; category keywords matched: "
            f"{', '.join(item['matched'])}."
            if item.get("category_only")
            else _route_reason(skill, item["matched"], item["score"])
        )
        selected.append(
            {
                "id": skill["id"],
                "name": skill["name"],
                "slug": skill["slug"],
                "category": skill["category"],
                "category_id": skill["category_id"],
                "description": skill["description"],
                "risk": skill["risk"],
                "needs_human_referral": skill["needs_human_referral"],
                "score": round(item["score"], 2),
                "matched_terms": item["matched"],
                "reason": reason,
                "steps": skill_steps(skill),
            }
        )
        per_category[skill["category_id"]] = per_category.get(skill["category_id"], 0) + 1
        chosen_ids.add(skill["id"])

    # Pass 1: at most two skills per category, so roles stay distinct.
    for item in scored:
        if len(selected) >= max_skills:
            break
        deadline.check()
        cid = item["skill"]["category_id"]
        if per_category.get(cid, 0) >= per_category_cap:
            continue
        _add(item)

    # Pass 2: fill any remaining slots when the need is concentrated in one domain.
    if len(selected) < max_skills:
        for item in scored:
            if len(selected) >= max_skills:
                break
            deadline.check()
            if item["skill"]["id"] in chosen_ids:
                continue
            _add(item)

    used_categories = set(per_category)

    if not selected:
        selected.append(_fallback_skill(catalog, deadline))

    if force_governance:
        govern = _governance_skill(catalog)
        if govern and all(item["id"] != govern["id"] for item in selected):
            selected.append(
                {
                    "id": govern["id"],
                    "name": govern["name"],
                    "slug": govern["slug"],
                    "category": govern["category"],
                    "category_id": govern["category_id"],
                    "description": govern["description"],
                    "risk": govern["risk"],
                    "needs_human_referral": govern["needs_human_referral"],
                    "score": 0.0,
                    "matched_terms": [],
                    "reason": "Always attached: keeps scope, consent, and escalation visible.",
                    "steps": skill_steps(govern, max_steps=3),
                    "pinned": True,
                }
            )

    return {
        "query": query,
        "max_skills": max_skills,
        "selected": selected,
        "categories_touched": sorted(used_categories),
        "candidates_considered": len(scored),
        "elapsed_seconds": round(deadline.seconds - max(0.0, deadline.expires_at - time.monotonic()), 4),
    }


def _route_reason(skill: dict, matched: list[str], score: float) -> str:
    if matched:
        head = ", ".join(matched[:5])
        return f"Matched the stated need on: {head} (score {score:.1f})."
    return f"Selected from category '{skill['category']}' (score {score:.1f})."


def _fallback_skill(catalog: dict, deadline: Deadline) -> dict:
    """Deterministic fallback so routing never returns an empty pick list."""
    preferred = ("emotional-check-in-guide", "support-mode-router", "holistic-support-catalog-router")
    by_slug = {skill["slug"]: skill for skill in catalog["skills"]}
    for slug in preferred:
        if slug in by_slug:
            skill = by_slug[slug]
            return _as_selection(skill, "Fallback: clarifies the need before any other support.")
    deadline.check()
    skill = catalog["skills"][0]
    return _as_selection(skill, "Fallback: first catalog skill, because no keyword matched.")


def _as_selection(skill: dict, reason: str) -> dict:
    return {
        "id": skill["id"],
        "name": skill["name"],
        "slug": skill["slug"],
        "category": skill["category"],
        "category_id": skill["category_id"],
        "description": skill["description"],
        "risk": skill["risk"],
        "needs_human_referral": skill["needs_human_referral"],
        "score": 0.0,
        "matched_terms": [],
        "reason": reason,
        "steps": skill_steps(skill),
    }


def _governance_skill(catalog: dict) -> dict | None:
    for skill in catalog["skills"]:
        if skill["slug"] == "support-response-safety-reviewer":
            return skill
    for skill in catalog["skills"]:
        if skill["category_id"] == 18:
            return skill
    return None


def capabilities(catalog: dict) -> dict:
    """Return a capability listing grouped by category (the catalog-list command)."""
    grouped = []
    for category in catalog["categories"]:
        skills = [s for s in catalog["skills"] if s["category_id"] == category["id"]]
        grouped.append(
            {
                "category_id": category["id"],
                "category": category["name"],
                "skill_count": len(skills),
                "skills": [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "slug": s["slug"],
                        "risk": s["risk"],
                        "needs_human_referral": s["needs_human_referral"],
                    }
                    for s in skills
                ],
            }
        )
    return {
        "title": catalog["title"],
        "counts": catalog["counts"],
        "categories": grouped,
    }


def describe(catalog: dict, skill: dict) -> dict:
    out = dict(skill)
    out["steps"] = skill_steps(skill)
    return out
