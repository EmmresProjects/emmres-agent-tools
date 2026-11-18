#!/usr/bin/env python3
"""tools-capability-registry.py - loads and queries the packaged capability catalog.

The catalog asset holds 28 categories and 280 named capabilities. This module is
the single source of truth for lookup, validation, search, and routing, so the
planner and the command line never hard-code capability names.

Run directly for its own help: python3 tools-capability-registry.py --help
"""

from __future__ import annotations

import difflib
import os
import re
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
CATALOG_ASSET = "asset-capability-catalog.json"
PLAYBOOK_ASSET = "asset-category-playbooks.json"


def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import importlib.util

    def load(name, filename):
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, os.path.join(here, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return load("aps_audio_common", COMMON_FILENAME)


common = _load()

_CACHE = {}

SEARCH_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "are", "was",
    "our", "your", "its", "their", "them", "than", "then", "also", "very", "any",
    "all", "can", "will", "would", "should", "could", "how", "what", "when",
    "where", "which", "who", "why", "not", "but", "use", "using", "make", "made",
    "get", "got", "need", "needs", "want", "wants", "please", "some", "more",
}


def load(catalog_path=None, playbook_path=None):
    """Load and validate the catalog and playbooks (cached by resolved path)."""
    key = (catalog_path or CATALOG_ASSET, playbook_path or PLAYBOOK_ASSET)
    if key in _CACHE:
        return _CACHE[key]
    catalog = (common.read_json(catalog_path, "capability catalog")
               if catalog_path else common.load_asset(CATALOG_ASSET))
    playbooks = (common.read_json(playbook_path, "category playbooks")
                 if playbook_path else common.load_asset(PLAYBOOK_ASSET))
    problems = validate(catalog, playbooks)
    if problems:
        raise common.ToolError("invalid_catalog",
                               "The capability catalog failed validation",
                               {"problems": problems[:20], "problem_count": len(problems)},
                               hint="Restore the packaged asset unchanged or fix the reported fields.")
    for category in catalog["categories"]:
        for skill in category["skills"]:
            skill["category_slug"] = category["slug"]
            skill["category_name"] = category["name"]
            skill["category_index"] = category["index"]
    _CACHE[key] = (catalog, playbooks)
    return catalog, playbooks


def validate(catalog, playbooks=None):
    problems = []
    if not isinstance(catalog, dict):
        return ["catalog root must be an object"]
    categories = catalog.get("categories")
    if not isinstance(categories, list) or not categories:
        return ["catalog must contain a non-empty categories list"]
    seen_ids = set()
    seen_slugs = set()
    skill_total = 0
    for category in categories:
        for field in ("index", "name", "slug", "skills"):
            if field not in category:
                problems.append("category %r missing field %r" % (category.get("name"), field))
        slug = category.get("slug", "")
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug or ""):
            problems.append("category slug is not valid kebab-case: %r" % slug)
        if slug in seen_slugs:
            problems.append("duplicate category slug: %s" % slug)
        seen_slugs.add(slug)
        skills = category.get("skills") or []
        skill_total += len(skills)
        for skill in skills:
            sid = skill.get("id")
            if not sid:
                problems.append("skill without id in %s" % slug)
                continue
            if sid in seen_ids:
                problems.append("duplicate skill id: %s" % sid)
            seen_ids.add(sid)
            for field in ("name", "description", "operations", "slug"):
                if not skill.get(field):
                    problems.append("skill %s missing %r" % (sid, field))
            if not isinstance(skill.get("operations"), list):
                problems.append("skill %s operations must be a list" % sid)
    if catalog.get("total_skills") and catalog["total_skills"] != skill_total:
        problems.append("declared total_skills %s does not match %s entries"
                        % (catalog["total_skills"], skill_total))
    if catalog.get("total_categories") and catalog["total_categories"] != len(categories):
        problems.append("declared total_categories %s does not match %s entries"
                        % (catalog["total_categories"], len(categories)))
    if playbooks is not None:
        entries = playbooks.get("playbooks") if isinstance(playbooks, dict) else None
        if not isinstance(entries, dict):
            problems.append("playbooks file must contain a playbooks object")
        else:
            for category in categories:
                if category.get("slug") not in entries:
                    problems.append("no playbook for category %s" % category.get("slug"))
            for slug in entries:
                if slug not in seen_slugs:
                    problems.append("playbook %s has no matching category" % slug)
    return problems


def categories():
    catalog, _ = load()
    return [{"index": c["index"], "name": c["name"], "slug": c["slug"],
             "skill_count": len(c["skills"]), "family": c.get("family", "")}
            for c in catalog["categories"]]


def skills(category=None):
    catalog, _ = load()
    out = []
    for entry in catalog["categories"]:
        if category and category not in (entry["slug"], str(entry["index"]), entry["name"]):
            continue
        out.extend(entry["skills"])
    if not out:
        raise common.ToolError("unknown_category", "No capabilities match category %r" % category,
                               {"available": [c["slug"] for c in catalog["categories"]]})
    return out


def get(identifier):
    """Exact lookup by skill id, skill slug, or category slug (returns a group)."""
    catalog, _ = load()
    needle = (identifier or "").strip()
    if not needle:
        raise common.ToolError("invalid_argument", "A capability id, slug, or name is required")
    lowered = needle.lower()
    index_match = re.match(r"^(?:c|cat|category)[\s\-_]*(\d{1,2})$", lowered)
    wanted_index = int(index_match.group(1)) if index_match else None
    for category in catalog["categories"]:
        if wanted_index is not None and category["index"] == wanted_index:
            return {"type": "category", "category": {"index": category["index"], "name": category["name"],
                                                     "slug": category["slug"]},
                    "skills": category["skills"]}
        if lowered in (category["slug"].lower(), "category %d" % category["index"], str(category["index"])):
            return {"type": "category", "category": {"index": category["index"], "name": category["name"],
                                                     "slug": category["slug"]},
                    "skills": category["skills"]}
        for skill in category["skills"]:
            if lowered == skill["id"].lower() or lowered == skill["slug"].lower():
                return {"type": "skill", "skill": skill}
    raise common.ToolError("not_found", "No capability with id or slug %r" % needle,
                           hint="Use a capability id (C18-S03), a category (C18 or "
                                "'mastering-loudness-sequencing-and-release-preparation'), "
                                "or run 'capabilities --query <text>'.")


def _score(query_tokens, haystack_text, name, keywords, weights=None):
    """Score a capability against query tokens; 0 means no defensible match."""
    text = haystack_text.lower()
    lowered_name = name.lower()
    lowered_keywords = [k.lower() for k in keywords]
    name_words = set(re.findall(r"[a-z0-9\-']+", lowered_name))
    text_words = set(re.findall(r"[a-z0-9\-']+", text))

    def hits(token, name_set, text_set):
        if token in name_set or token in text_set:
            return True
        if len(token) >= 4:  # tolerate plurals and stems, never arbitrary substrings
            return any(word.startswith(token) for word in name_set) or \
                any(word.startswith(token) for word in text_set)
        return False

    score = 0.0
    matched = 0
    weights = weights or {}
    for token in query_tokens:
        weight = weights.get(token, 1.0)
        if token in name_words or hits(token, name_words, name_words):
            score += 4.0 * weight
            matched += 1
        elif token in lowered_keywords:
            score += 1.5 * weight
            matched += 1
        elif hits(token, name_words, text_words):
            score += 1.0 * weight
            matched += 1
    ratio = difflib.SequenceMatcher(None, " ".join(query_tokens), lowered_name).ratio()
    if matched == 0 and ratio < 0.45:
        return 0.0
    return score + ratio * 0.3


def search(query, category=None, limit=8, min_score=0.5):
    query = (query or "").strip()
    if not query:
        raise common.ToolError("invalid_argument", "Search text is required")
    tokens = [t for t in re.findall(r"[a-z0-9\-']+", query.lower())
              if len(t) > 1 and t not in SEARCH_STOPWORDS]
    if not tokens:
        raise common.ToolError("invalid_argument",
                               "Search text needs at least one meaningful word",
                               {"received": query})
    pool = skills(category)
    word_cache = {}

    def words_of(skill):
        key = skill["id"]
        if key not in word_cache:
            word_cache[key] = set(re.findall(
                r"[a-z0-9\-']+",
                " ".join([skill["name"], skill["description"]]).lower()))
        return word_cache[key]

    frequency = {token: sum(1 for skill in pool if token in words_of(skill)) for token in tokens}
    weights = {token: (0.3 if frequency[token] > 0.25 * len(pool) else 1.0) for token in tokens}
    scored = []
    for skill in pool:
        text = " ".join([skill["name"], skill["description"], skill.get("category_name", "")])
        score = _score(tokens, text, skill["name"], skill.get("keywords", []), weights)
        if score >= min_score:
            scored.append((score, skill))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    results = []
    for score, skill in scored[:limit]:
        results.append({
            "id": skill["id"],
            "name": skill["name"],
            "category": skill["category_name"],
            "category_slug": skill["category_slug"],
            "description": skill["description"],
            "operations": skill["operations"],
            "score": common.r2(score, 2),
        })
    return results


def resolve(identifier, allow_search=True):
    """Resolve user text to a designator: exact id/slug first, then best search hit."""
    identifier = (identifier or "").strip()
    if not identifier:
        raise common.ToolError("invalid_argument", "A capability name is required")
    try:
        return get(identifier)
    except common.ToolError:
        if not allow_search:
            raise
    matches = search(identifier, limit=3)
    if not matches:
        raise common.ToolError("not_found", "No capability matches %r" % identifier,
                               hint="Try 'capabilities --query <text>' or pass a capability id like C18-S03.")
    best = matches[0]
    result = get(best["id"])
    result["match"] = {"resolved_from": identifier, "score": best["score"],
                       "alternatives": [m["name"] for m in matches[1:]]}
    return result


def operations(identifier):
    designator = resolve(identifier)
    if designator["type"] == "skill":
        return designator["skill"]["operations"]
    combined = []
    for skill in designator["skills"]:
        for op in skill["operations"]:
            if op not in combined:
                combined.append(op)
    return combined


def stats():
    catalog, playbooks = load()
    operations_seen = {}
    for category in catalog["categories"]:
        for skill in category["skills"]:
            for op in skill["operations"]:
                operations_seen[op] = operations_seen.get(op, 0) + 1
    return {
        "catalog_title": catalog.get("catalog_title"),
        "categories": len(catalog["categories"]),
        "capabilities": sum(len(c["skills"]) for c in catalog["categories"]),
        "playbooks": len(playbooks.get("playbooks", {})),
        "operations": dict(sorted(operations_seen.items(), key=lambda item: -item[1])),
    }


def main(argv=None):
    print("""tools-capability-registry.py - capability catalog access layer (library + help).

Functions: load(), validate(), categories(), skills(), get(), search(), resolve(),
operations(), stats()

Command line use goes through the suite entry point:
  python3 tools-audio-suite.py capabilities --query "mastering loudness"
  python3 tools-audio-suite.py capabilities --category 18
  python3 tools-audio-suite.py describe --capability C18-S03
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
