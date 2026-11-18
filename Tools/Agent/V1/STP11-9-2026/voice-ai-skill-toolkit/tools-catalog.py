#!/usr/bin/env python3
"""Catalog data access for the voice-ai-skill-toolkit.

Loads asset-voice-skill-catalog.json (500 skills across 50 categories,
extracted verbatim from the Voice AI Skill Catalog document) and exposes
deterministic lookup helpers used by the CLI:

  * load_catalog()          -> parsed catalog dict (cached)
  * resolve_category(query) -> category dict or None (id or name substring)
  * resolve_skill(query)    -> (skill dict, matches list)
  * skills_for(category, tier) -> filtered list
  * capability_index()      -> machine-readable capability list for the CLI

Skill lookup accepts: exact slug id ("number-verbalization-engine"),
catalog number ("2.3"), case-insensitive exact name, or unique substring.
Ambiguous queries return the candidate list and a non-fatal error so the
caller can show choices instead of guessing.
"""

import importlib.util
import os
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    # Share one module instance per name via sys.modules so that classes
    # (e.g. tools_common.ToolError) keep a single identity across siblings.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_common = _load_sibling("tools_common", "tools-common.py")
load_asset_json = _common.load_asset_json
ToolError = _common.ToolError

_CATALOG = None


def load_catalog():
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = load_asset_json("asset-voice-skill-catalog.json")
    return _CATALOG


def all_skills():
    return load_catalog()["skills"]


def all_categories():
    return load_catalog()["categories"]


def resolve_category(query):
    """Category by numeric id or (unique) case-insensitive name substring."""
    if query is None:
        return None
    q = str(query).strip()
    cats = all_categories()
    if q.isdigit():
        for c in cats:
            if c["id"] == int(q):
                return c
        return None
    ql = q.lower()
    exact = [c for c in cats if c["name"].lower() == ql]
    if exact:
        return exact[0]
    hits = [c for c in cats if ql in c["name"].lower()]
    return hits[0] if len(hits) == 1 else None


def resolve_skill(query):
    """Resolve a skill by slug, catalog number, exact name, or substring.

    Returns (skill_or_None, candidate_list). Exactly one of them carries the
    usable result: on an unambiguous hit skill is set; otherwise candidates is
    non-empty when the query was merely ambiguous/not found.
    """
    if not query or not str(query).strip():
        raise ToolError("Skill query is empty. Use --skill <slug|number|name>.")
    q = str(query).strip()
    ql = q.lower()
    skills = all_skills()

    # 1) slug id
    for s in skills:
        if s["id"] == ql:
            return s, []
    # 2) catalog number "C.N" or "C.N " with flexible padding
    if "." in ql:
        parts = ql.split(".")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            num = "%d.%d" % (int(parts[0]), int(parts[1]))
            for s in skills:
                if s["number"] == num:
                    return s, []
    # 3) exact name (case-insensitive)
    for s in skills:
        if s["name"].lower() == ql:
            return s, []
    # 4) unique substring over name/id/number
    hits = [
        s for s in skills
        if ql in s["name"].lower() or ql in s["id"] or ql in s["number"]
    ]
    if len(hits) == 1:
        return hits[0], []
    return None, hits


def filter_skills(category=None, tier=None):
    skills = all_skills()
    if category is not None:
        cat = resolve_category(category)
        if cat is None:
            raise ToolError("Unknown category: %s" % category)
        skills = [s for s in skills if s["category_id"] == cat["id"]]
    if tier:
        t = tier.lower()
        if t not in ("standard", "review", "elevated"):
            raise ToolError("Unknown tier: %s (expected standard|review|elevated)" % tier)
        skills = [s for s in skills if s["tier"] == t]
    return skills


def search_skills(query, category=None, tier=None, limit=25):
    ql = str(query).strip().lower()
    if not ql:
        raise ToolError("Search query is empty.")
    pool = filter_skills(category=category, tier=tier)
    hits = []
    for s in pool:
        hay = " ".join(
            [s["name"], s["id"], s["number"], s["focus"], s["category_name"], s["description"]]
        ).lower()
        if ql in hay:
            hits.append(s)
    return hits[:limit], len(hits)


def tier_gate_required(skill):
    """True when the skill's category mandates a consent/approval gate."""
    return skill and skill.get("tier") == "elevated"


def capability_index():
    """Machine-readable list of toolkit capabilities (used by `capabilities`)."""
    return [
        {"command": "categories", "summary": "List the 50 catalog categories with coverage text and tier."},
        {"command": "list", "summary": "List skills, optionally filtered by --category and --tier."},
        {"command": "search", "summary": "Keyword search across all 500 catalog skills."},
        {"command": "show", "summary": "Show one skill's full catalog entry (slug, number, focus, description, tier)."},
        {"command": "brief", "summary": "Generate a production brief/work order for any of the 500 skills from asset-brief-template.md."},
        {"command": "init-package", "summary": "Scaffold a complete voice work-package folder (manifest, transcript template, consent record when required)."},
        {"command": "audio-check", "summary": "Measure a WAV file: duration, sample rate, channels, peak/RMS dBFS, clipping, DC offset, silence, delivery-profile conformance. Optional ffmpeg for true LUFS."},
        {"command": "transcript-check", "summary": "Validate SRT/VTT/JSON transcripts: timestamp syntax, ordering, overlaps, cue length, reading speed (CPS/WPM)."},
        {"command": "consent-check", "summary": "Validate a voice-consent record against the required fields, scopes, disclosures and revocation rules in asset-consent-record-template.json."},
        {"command": "validate-package", "summary": "Validate a whole work package: manifest schema, required artifacts, consent gate, transcripts and audio, verdict pass/warn/fail."},
        {"command": "qa-report", "summary": "Render a human-review QA / handoff report (Markdown) from a validated work package."},
        {"command": "selftest", "summary": "Run the built-in end-to-end self test in a temporary directory (no network, no external services)."},
        {"command": "capabilities", "summary": "Print this capability list (add --json for machine-readable output)."},
        {"command": "help", "summary": "Show help and the full command list."},
        {"command": "version", "summary": "Print toolkit and catalog versions."},
    ]
