#!/usr/bin/env python3
"""Catalog registry for the ultimate-game-maker Skill Tool.

Loads the unified catalog, category-profile, and specialization-lexicon assets, then resolves catalog
capabilities (skills, categories, phases, tags) into concrete production
contracts: artifacts, acceptance checks, risks, parameters, verification
intents, approval-sensitive actions, and prerequisite capability ids.

This module is a library. It is loaded by tools-game-maker.py through
tools-common.py and never needs to be executed directly. It has no third-party
dependencies.

Design rules enforced here:
  * every capability contract is deterministic (same inputs, same output);
  * every check id is stable and derived from the catalog skill slug;
  * all data comes from the JSON assets, so behaviour is editable without code.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

TOOL_NAME = "ultimate-game-maker"
TOOL_VERSION = "2.0.0"
SCHEMA_VERSION = "2.0"

ASSET_FILES = {
    "capabilities": "asset-catalog.json",
    "profiles": "asset-category-profiles.json",
    "lexicon": "asset-skill-lexicon.json",
    "request_schema": "asset-request-schema.json",
    "gate_policy": "asset-gate-policy.json",
}

PHASE_ORDER = ["phase-1", "phase-2", "phase-3", "phase-4", "phase-5"]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ToolError(Exception):
    """Structured error carrying a stable machine-readable code."""

    def __init__(self, code, message, hint=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def as_dict(self):
        payload = {"ok": False, "error": {"code": self.code, "message": self.message}}
        if self.hint:
            payload["error"]["hint"] = self.hint
        if self.details:
            payload["error"]["details"] = self.details
        return payload

    def __str__(self):  # pragma: no cover - convenience for debugging
        return "%s: %s" % (self.code, self.message)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def slugify(text):
    """Lowercase ASCII slug used for ids, filenames, and stable references."""
    text = text.replace("C++", "cpp").replace("C#", "csharp")
    text = text.replace("CI/CD", "ci-cd").replace("&", " and ")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-").lower()
    return text or "unnamed"


def canonical_hash(obj):
    """SHA-256 of a canonical JSON encoding - the determinism primitive."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


MAX_ASSET_BYTES = 8 * 1024 * 1024


def read_json(path, label):
    path = Path(path)
    if not path.is_file():
        raise ToolError(
            "asset_missing",
            "Required asset '%s' was not found at %s" % (label, path.name),
            hint="Keep all tools-* and asset-* files in the same Skill Tool folder.",
        )
    if path.stat().st_size > MAX_ASSET_BYTES:
        raise ToolError("asset_too_large", "Asset '%s' exceeds %d bytes" % (label, MAX_ASSET_BYTES),
                       hint="Restore a bounded asset or raise MAX_ASSET_BYTES deliberately.")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "asset_invalid_json",
            "Asset '%s' is not valid JSON (line %s, column %s): %s"
            % (label, exc.lineno, exc.colno, exc.msg),
            hint="Restore the asset from the delivered archive or fix the JSON syntax.",
            details={"file": path.name},
        )


def token_in(text, token):
    """Word-boundary, case-insensitive token match inside a lowercase string."""
    pattern = r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(token.lower())
    return re.search(pattern, text) is not None


def dedupe(items, key):
    """Order-preserving de-duplication."""
    seen = set()
    out = []
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class Registry(object):
    """Catalog + profiles + lexicon, indexed for routing, planning, and gating."""

    def __init__(self, root, deadline=None):
        self.root = Path(root)
        self.deadline = deadline
        self.loaded = False
        self.tool_version = TOOL_VERSION
        self.schema_version = SCHEMA_VERSION
        self.capabilities = {}
        self.profiles_doc = {}
        self.lexicon_doc = {}
        self.categories = []
        self._category_by_slug = {}
        self._skill_by_id = {}
        self._skills = []
        self._contract_cache = {}

    # -- infrastructure ----------------------------------------------------- #
    def _deadline_check(self):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ToolError(
                "timeout",
                "Operation exceeded the configured time budget",
                hint="Raise --timeout, narrow the capability selection, or reduce --limit.",
            )

    def asset_path(self, key):
        return self.root / ASSET_FILES[key]

    def load(self):
        self.capabilities = read_json(self.asset_path("capabilities"), "capabilities")
        self.profiles_doc = read_json(self.asset_path("profiles"), "category profiles")
        self.lexicon_doc = read_json(self.asset_path("lexicon"), "skill lexicon")
        self._index()
        self.loaded = True
        return self

    def _index(self):
        handlers = self.capabilities.get("handlers") or []
        if not handlers:
            raise ToolError("asset_invalid", "Unified capability catalog contains no handlers")
        problems = []
        self.categories = []
        self._skills = []
        self._category_by_slug = {}
        self._skill_by_id = {}
        self._contract_cache = {}
        for number, handler in enumerate(handlers, start=1):
            category_slug = handler["category_slug"]
            entry = {
                "number": number,
                "name": handler["category"],
                "slug": category_slug,
                "summary": handler.get("outcome", ""),
                "skill_count": len(handler.get("skills", [])),
            }
            self.categories.append(entry)
            self._category_by_slug[entry["slug"]] = entry
            if entry["slug"] not in self.profiles_doc.get("profiles", {}):
                problems.append("missing profile for category '%s'" % entry["slug"])
            for order, skill in enumerate(handler.get("skills", []), start=1):
                record = {
                    "id": "%s/%s" % (category_slug, skill["slug"]),
                    "name": skill["name"],
                    "slug": skill["slug"],
                    "order": order,
                    "description": skill.get("description", ""),
                    "category": handler["category"],
                    "category_slug": category_slug,
                    "category_number": number,
                }
                self._skill_by_id[record["id"]] = record
                self._skills.append(record)
        if problems:
            raise ToolError(
                "asset_invalid",
                "Catalog and profile assets disagree: %s" % "; ".join(problems[:5]),
                hint="Each catalog category needs one entry in asset-category-profiles.json.",
            )
        seen_rules = set()
        all_rules = list(self.lexicon_doc.get("rules", [])) + list(self.lexicon_doc.get("role_rules", []))
        for rule in all_rules:
            if rule.get("id") in seen_rules:
                raise ToolError(
                    "asset_invalid",
                    "Duplicate lexicon rule id '%s'" % rule.get("id"),
                    hint="Lexicon rule ids must be unique.",
                )
            seen_rules.add(rule.get("id"))
        self.categories.sort(key=lambda c: c["number"])
        self._skills.sort(key=lambda s: (s["category_number"], s["order"]))

    # -- catalogue access --------------------------------------------------- #
    def skills(self):
        return list(self._skills)

    def category_list(self):
        return list(self.categories)

    def skill_by_id(self, skill_id):
        try:
            return self._skill_by_id[skill_id]
        except KeyError:
            raise ToolError(
                "unknown_capability",
                "No catalog capability with id '%s'" % skill_id,
                hint="Run 'capabilities' to list valid ids.",
            )

    def profile(self, skill):
        return self.profiles_doc["profiles"][skill["category_slug"]]

    def universal_checks(self):
        return list(self.profiles_doc.get("universal_checks", []))

    # -- resolution --------------------------------------------------------- #
    def resolve_category(self, query):
        q = (query or "").strip()
        if not q:
            raise ToolError("invalid_argument", "Category selector is empty")
        if q.isdigit():
            for cat in self.categories:
                if cat["number"] == int(q):
                    return cat
        lowered = q.lower()
        for cat in self.categories:
            if lowered in (cat["name"].lower(), cat["slug"]):
                return cat
        matches = [c for c in self.categories
                   if lowered in c["name"].lower() or lowered in c["slug"]]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ToolError(
                "unknown_category",
                "No catalog category matches '%s'" % q,
                hint="Run 'capabilities --format markdown' to see category names.",
            )
        raise ToolError(
            "ambiguous_category",
            "Category selector '%s' matches %d categories" % (q, len(matches)),
            hint="Use the exact category name, its number, or its slug.",
            details={"candidates": [{"number": c["number"], "name": c["name"]} for c in matches[:10]]},
        )

    def resolve_skill(self, query):
        q = (query or "").strip()
        if not q:
            raise ToolError("invalid_argument", "Capability selector is empty")
        lowered = q.lower()
        for skill in self._skills:
            if lowered in (skill["id"].lower(), skill["name"].lower(), skill["slug"]):
                return skill
        matches = [s for s in self._skills
                   if lowered == s["slug"] or lowered in s["name"].lower()
                   or lowered == s["id"].split("/")[-1]]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            words = [w for w in re.split(r"[^a-z0-9]+", lowered) if len(w) > 2]
            fuzzy = [s for s in self._skills
                     if all(w in s["name"].lower() for w in words)] if words else []
            if len(fuzzy) == 1:
                return fuzzy[0]
            raise ToolError(
                "unknown_capability",
                "No catalog capability matches '%s'" % q,
                hint="Run 'capabilities --search \"<words>\"' to find the exact skill name.",
            )
        raise ToolError(
            "ambiguous_capability",
            "Capability selector '%s' matches %d capabilities" % (q, len(matches)),
            hint="Use the full skill name or the capability id shown in 'capabilities'.",
            details={"candidates": [{"id": s["id"], "name": s["name"]} for s in matches[:10]]},
        )

    # -- selection ---------------------------------------------------------- #
    def select(self, skills=None, categories=None, phases=None, tags=None,
               exclude=None, limit=None):
        """Select catalog capabilities by any combination of selectors."""
        chosen = {}
        if skills:
            for query in skills:
                skill = self.resolve_skill(query)
                chosen[skill["id"]] = skill
        if categories:
            for query in categories:
                cat = self.resolve_category(query)
                for skill in self._skills:
                    if skill["category_slug"] == cat["slug"]:
                        chosen[skill["id"]] = skill
        if phases:
            wanted = {self.normalize_phase(p) for p in phases}
            for skill in self._skills:
                if self.profile(skill)["phase"] in wanted:
                    chosen[skill["id"]] = skill
        if tags:
            wanted = [t.lower() for t in tags]
            for skill in self._skills:
                contract_tags = [t.lower() for t in self.contract(skill)["tags"]]
                if any(w in contract_tags for w in wanted):
                    chosen[skill["id"]] = skill
        if exclude:
            for query in exclude:
                try:
                    drop = self.resolve_skill(query)
                except ToolError:
                    continue
                chosen.pop(drop["id"], None)
        result = sorted(chosen.values(),
                        key=lambda s: (s["category_number"], s["order"]))
        if not result:
            raise ToolError(
                "empty_selection",
                "The selectors matched no catalog capabilities",
                hint="Try 'capabilities --category 1' or 'capabilities --tag graphics'.",
            )
        if limit is not None:
            if limit < 1:
                raise ToolError("invalid_argument", "--limit must be >= 1")
            result = result[:limit]
        return result

    def normalize_phase(self, value):
        raw = str(value).strip().lower()
        aliases = {"1": "phase-1", "2": "phase-2", "3": "phase-3", "4": "phase-4", "5": "phase-5",
                   "phase1": "phase-1", "phase2": "phase-2", "phase3": "phase-3",
                   "phase4": "phase-4", "phase5": "phase-5"}
        phase = aliases.get(raw, raw)
        if phase not in self.profiles_doc.get("phases", {}):
            raise ToolError(
                "invalid_phase",
                "Unknown phase '%s'" % value,
                hint="Valid phases: %s" % ", ".join(self.profiles_doc.get("phases", {})),
            )
        return phase

    def phase_meta(self, phase):
        return self.profiles_doc.get("phases", {}).get(phase, {"label": phase, "goal": "", "exit_criteria": ""})

    # -- search ------------------------------------------------------------- #
    def search(self, text, limit=None):
        needle = (text or "").strip().lower()
        if not needle:
            raise ToolError("invalid_argument", "Search text is empty")
        words = [w for w in re.split(r"[^a-z0-9+#]+", needle) if w]
        hits = []
        for skill in self._skills:
            haystack = " ".join([skill["name"], skill["category"], skill["description"]]).lower()
            if all(word in haystack for word in words):
                hits.append(skill)
        if limit:
            hits = hits[:limit]
        return hits

    # -- specialisation ----------------------------------------------------- #
    def _matching_rules(self, skill, key):
        name = skill["name"].lower()
        slug = skill["slug"]
        matched = []
        for rule in self.lexicon_doc.get(key, []):
            for token in rule.get("match", []):
                if token_in(name, token) or token_in(slug, token):
                    matched.append(rule)
                    break
        return matched

    def specializations(self, skill):
        """Domain rules first, then responsibility-role rules (both additive)."""
        return self._matching_rules(skill, "rules") + self._matching_rules(skill, "role_rules")

    def prerequisites(self, skill):
        """Documented prerequisite capabilities (ids) for a capability."""
        profile = self.profile(skill)
        family = [f.lower() for f in profile.get("engine_family", [])]
        deps = []
        design_id = "game-vision-design-and-production-planning/game-creative-director"
        router_id = ("engine-discovery-documentation-and-version-intelligence/"
                     "game-engine-capability-router")
        engine_families = {"unity", "unreal", "godot", "web", "html5", "python", "pygame",
                           "renpy", "native", "sdl", "sfml", "monogame", "libgdx", "opengl",
                           "vulkan", "gamemaker", "construct", "rpg-maker", "love2d", "bevy",
                           "raylib", "custom", "cloud", "ci"}
        if skill["category_slug"] != "game-vision-design-and-production-planning":
            deps.append(design_id)
        if engine_families.intersection(family) and "engine-agnostic" not in family:
            deps.append(router_id)
        return [d for d in dedupe(deps, lambda x: x) if d != skill["id"]]

    # -- contract ----------------------------------------------------------- #
    def contract(self, skill):
        """Full production contract for one catalog capability."""
        cached = self._contract_cache.get(skill["id"])
        if cached is not None:
            return json.loads(json.dumps(cached))
        self._deadline_check()
        profile = self.profile(skill)
        rules = self.specializations(skill)

        artifacts = list(profile.get("artifacts", []))
        checks = list(self.universal_checks()) + list(profile.get("checks", []))
        risks = list(profile.get("risks", []))
        verification = list(profile.get("verification", []))
        params = {}
        tags = [skill["category_slug"]]
        for rule in rules:
            artifacts.extend(rule.get("artifacts", []))
            checks.extend(rule.get("checks", []))
            risks.extend(rule.get("risks", []))
            tags.extend(rule.get("tags", []))
            params.update(rule.get("params", {}))

        artifacts = dedupe(artifacts, lambda a: a["title"].lower())
        checks = dedupe(checks, lambda c: c["statement"].lower())
        risks = dedupe(risks, lambda r: r["risk"].lower())
        verification = dedupe(verification, lambda v: v["intent"].lower())
        tags = dedupe(tags, lambda t: t)

        numbered = []
        for index, check in enumerate(checks, start=1):
            numbered.append({
                "id": "AC-%s-%02d" % (skill["slug"][:40], index),
                "statement": check["statement"],
                "evidence": check["evidence"],
                "required": bool(check.get("required", True)),
                "class": check.get("class", "functional"),
            })

        contract = {
            "id": skill["id"],
            "name": skill["name"],
            "slug": skill["slug"],
            "category": skill["category"],
            "category_slug": skill["category_slug"],
            "category_number": skill["category_number"],
            "phase": profile["phase"],
            "description": skill["description"],
            "specializations": [rule["id"] for rule in rules],
            "tags": tags,
            "engine_family": list(profile.get("engine_family", [])),
            "languages": list(profile.get("languages", [])),
            "artifacts": artifacts,
            "checks": numbered,
            "risks": risks,
            "parameters": params,
            "verification": verification,
            "approval_actions": list(profile.get("approval_actions", [])),
            "depends_on": self.prerequisites(skill),
            "effort_points": self._effort_points(artifacts, numbered, risks, profile),
        }
        self._contract_cache[skill["id"]] = contract
        return json.loads(json.dumps(contract))

    @staticmethod
    def _effort_points(artifacts, checks, risks, profile):
        """Deterministic, documented effort heuristic (1 point = half a day)."""
        base = 3
        points = base + len(artifacts) + len(checks) + 2 * len(risks)
        if "engine-agnostic" not in [f.lower() for f in profile.get("engine_family", [])]:
            points += 2
        return min(points, 40)

    # -- aggregate view ----------------------------------------------------- #
    def domain_signature(self, skill):
        """Contract fingerprint excluding the shared universal checks."""
        contract = self.contract(skill)
        universal = {c["statement"].lower() for c in self.universal_checks()}
        checks = [c["statement"] for c in contract["checks"]
                  if c["statement"].lower() not in universal]
        return canonical_hash({
            "artifacts": [a["title"] for a in contract["artifacts"]],
            "checks": checks,
            "risks": [r["risk"] for r in contract["risks"]],
            "params": contract["parameters"],
        })

    def stats(self):
        signatures = {}
        tag_counts = {}
        phase_counts = {}
        approval_counts = {}
        approval_capabilities = 0
        check_total = 0
        for skill in self._skills:
            contract = self.contract(skill)
            signatures[self.domain_signature(skill)] = True
            for tag in contract["tags"]:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            phase_counts[contract["phase"]] = phase_counts.get(contract["phase"], 0) + 1
            if contract["approval_actions"]:
                approval_capabilities += 1
            for action in contract["approval_actions"]:
                approval_counts[action] = approval_counts.get(action, 0) + 1
            check_total += len(contract["checks"])
        return {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "categories": len(self.categories),
            "capabilities": len(self._skills),
            "lexicon_rules": len(self.lexicon_doc.get("rules", [])),
            "role_rules": len(self.lexicon_doc.get("role_rules", [])),
            "universal_checks": len(self.universal_checks()),
            "distinct_domain_signatures": len(signatures),
            "distinct_signature_ratio": round(len(signatures) / float(len(self._skills)), 4),
            "total_checks": check_total,
            "phases": dict(sorted(phase_counts.items())),
            "tags": dict(sorted(tag_counts.items())),
            "capabilities_requiring_approval": approval_capabilities,
            "approval_action_assignments": sum(approval_counts.values()),
            "approval_action_kinds": len(approval_counts),
        }
