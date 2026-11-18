#!/usr/bin/env python3
"""Request validation, engine routing, plan construction, and document rendering
for the ultimate-game-maker Skill Tool.

Loaded by tools-game-maker.py through tools-common.py. No third-party packages:
the JSON Schema subset validator below is self-contained so the tool runs on a
bare Python installation.

Determinism contract: identical normalized requests always produce an identical
plan id, identical work item order, and identical rendered documents.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

SCHEMA_VERSION = "1.0"

# --------------------------------------------------------------------------- #
# Engine routing table (editable data, scored deterministically)
# --------------------------------------------------------------------------- #
ROUTES = [
    {
        "engine": "godot",
        "label": "Godot 4.x",
        "languages": ["gdscript", "csharp"],
        "dimensions": ["2d", "2d-hd", "3d"],
        "platforms": ["windows", "linux", "macos", "web", "android"],
        "license": "MIT, no royalty and no per-seat fee",
        "strengths": ["fast iteration", "strong 2D pipeline", "open source", "small teams", "web export"],
        "limits": ["console export needs a licensed third-party SDK",
                   "large-scale 3D tooling is thinner than Unreal"],
        "team_range": [1, 25],
        "scope_fit": ["prototype", "vertical-slice", "full-game"],
        "iteration_hours": 24,
        "solo_weight": 3
    },
    {
        "engine": "unity",
        "label": "Unity 6 / LTS line",
        "languages": ["csharp"],
        "dimensions": ["2d", "2d-hd", "3d", "mixed"],
        "platforms": ["windows", "linux", "macos", "android", "ios", "web",
                      "xbox", "playstation", "nintendo"],
        "license": "proprietary licence with revenue thresholds",
        "strengths": ["broadest platform coverage", "large asset ecosystem",
                      "mature 2D and 3D in one editor", "console support via partners"],
        "limits": ["package and editor version drift", "licence changes can affect shipped projects"],
        "team_range": [1, 200],
        "scope_fit": ["prototype", "vertical-slice", "full-game", "live-service"],
        "iteration_hours": 20,
        "solo_weight": 2
    },
    {
        "engine": "unreal",
        "label": "Unreal Engine 5.x",
        "languages": ["cpp", "blueprint"],
        "dimensions": ["3d", "mixed"],
        "platforms": ["windows", "macos", "linux", "android", "ios",
                      "xbox", "playstation", "nintendo"],
        "license": "royalty-based licence above a revenue threshold",
        "strengths": ["high-end rendering", "large-team workflows",
                      "cinematics and world partitioning", "console ready"],
        "limits": ["heavy build and cook times", "limited 2D workflow", "steep C++ surface"],
        "team_range": [3, 500],
        "scope_fit": ["vertical-slice", "full-game", "live-service"],
        "iteration_hours": 45,
        "solo_weight": -3
    },
    {
        "engine": "web",
        "label": "Browser HTML5 stack (Canvas/WebGL, TypeScript)",
        "languages": ["typescript", "javascript"],
        "dimensions": ["2d", "2d-hd", "3d", "mixed"],
        "platforms": ["web", "windows", "linux", "macos", "android", "ios"],
        "license": "permissive open-source libraries",
        "strengths": ["no install step", "instant share links", "tiny distribution cost"],
        "limits": ["browser memory and audio restrictions", "no direct console path"],
        "team_range": [1, 60],
        "scope_fit": ["prototype", "vertical-slice", "full-game"],
        "iteration_hours": 12,
        "solo_weight": 3
    },
    {
        "engine": "python",
        "label": "Python line (pygame / Ren'Py / Panda3D)",
        "languages": ["python"],
        "dimensions": ["2d", "3d"],
        "platforms": ["windows", "linux", "macos", "web"],
        "license": "permissive open-source libraries",
        "strengths": ["fastest path to a playable prototype", "readable source",
                      "strong visual-novel tooling in Ren'Py"],
        "limits": ["weaker runtime performance", "packaging to stores is manual", "no console path"],
        "team_range": [1, 15],
        "scope_fit": ["prototype", "vertical-slice"],
        "iteration_hours": 8,
        "solo_weight": 3
    },
    {
        "engine": "native",
        "label": "Native C/C++/C#/Java frameworks (SDL, SFML, MonoGame, LibGDX, Vulkan/OpenGL)",
        "languages": ["cpp", "c", "csharp", "java"],
        "dimensions": ["2d", "3d", "mixed"],
        "platforms": ["windows", "linux", "macos", "android", "ios"],
        "license": "permissive open-source libraries with per-library obligations",
        "strengths": ["total control", "predictable runtime cost", "no engine licence"],
        "limits": ["you build the engine layer yourself", "longer path to a playable slice"],
        "team_range": [1, 40],
        "scope_fit": ["prototype", "vertical-slice", "full-game"],
        "iteration_hours": 40,
        "solo_weight": -2
    },
    {
        "engine": "custom",
        "label": "Custom engine, tooling, or compiler",
        "languages": ["cpp", "rust", "csharp", "python"],
        "dimensions": ["2d", "3d", "mixed"],
        "platforms": ["windows", "linux", "macos"],
        "license": "your own",
        "strengths": ["exact fit to unusual mechanics", "learning value"],
        "limits": ["tooling cost dominates production", "no third-party asset pipeline"],
        "team_range": [1, 30],
        "scope_fit": ["prototype"],
        "iteration_hours": 80,
        "solo_weight": -4
    },
    {
        "engine": "gamemaker",
        "label": "GameMaker",
        "languages": ["gml"],
        "dimensions": ["2d"],
        "platforms": ["windows", "macos", "web", "android", "ios",
                      "xbox", "playstation", "nintendo"],
        "license": "subscription licence with console add-ons",
        "strengths": ["fast 2D iteration", "console exporting", "simple runtime model"],
        "limits": ["2D focus only", "subscription cost", "GML ecosystem is smaller"],
        "team_range": [1, 20],
        "scope_fit": ["prototype", "vertical-slice", "full-game"],
        "iteration_hours": 16,
        "solo_weight": 2
    },
    {
        "engine": "construct",
        "label": "Construct",
        "languages": ["javascript"],
        "dimensions": ["2d"],
        "platforms": ["web", "windows", "android", "ios"],
        "license": "subscription licence",
        "strengths": ["event-sheet authoring without code", "fast 2D prototypes", "web first"],
        "limits": ["limited code-first workflows", "2D only", "smaller ecosystem"],
        "team_range": [1, 10],
        "scope_fit": ["prototype", "vertical-slice"],
        "iteration_hours": 14,
        "solo_weight": 2
    },
    {
        "engine": "defold",
        "label": "Defold",
        "languages": ["lua"],
        "dimensions": ["2d", "3d"],
        "platforms": ["windows", "macos", "linux", "web", "android", "ios",
                      "xbox", "playstation", "nintendo"],
        "license": "free, source-available with a permissive licence",
        "strengths": ["small runtime", "mobile focus", "Lua scripting", "console support"],
        "limits": ["small ecosystem", "fewer built-in 3D features"],
        "team_range": [1, 20],
        "scope_fit": ["prototype", "vertical-slice", "full-game"],
        "iteration_hours": 18,
        "solo_weight": 1
    },
    {
        "engine": "rpg-maker",
        "label": "RPG Maker",
        "languages": ["javascript"],
        "dimensions": ["2d"],
        "platforms": ["windows", "macos", "web"],
        "license": "per-title commercial licence",
        "strengths": ["turnkey RPG systems", "fast content authoring", "visual-novel friendly"],
        "limits": ["genre box", "engine limits are hard to escape", "no console path without add-ons"],
        "team_range": [1, 8],
        "scope_fit": ["prototype", "vertical-slice"],
        "iteration_hours": 10,
        "solo_weight": 2
    },
    {
        "engine": "bevy",
        "label": "Bevy (Rust)",
        "languages": ["rust"],
        "dimensions": ["2d", "3d"],
        "platforms": ["windows", "linux", "macos", "web"],
        "license": "MIT/Apache-2.0",
        "strengths": ["data-oriented architecture", "compile-time safety", "fast runtime"],
        "limits": ["fast-moving API between releases", "smaller gamedev ecosystem"],
        "team_range": [1, 20],
        "scope_fit": ["prototype", "vertical-slice", "full-game"],
        "iteration_hours": 30,
        "solo_weight": 1
    }
]

ROUTE_BY_ENGINE = {route["engine"]: route for route in ROUTES}

CONSOLE_PLATFORMS = {"xbox", "playstation", "nintendo", "switch", "ps5", "ps4", "xbox-series"}


# --------------------------------------------------------------------------- #
# JSON Schema subset validator
# --------------------------------------------------------------------------- #
class SchemaError(Exception):
    pass


SUPPORTED_KEYWORDS = {
    "type", "required", "properties", "additionalProperties", "enum", "items",
    "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum",
    "maximum", "minProperties", "$schema", "schema_id", "title", "description",
}


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _type_ok(value, expected):
    actual = _type_name(value)
    if expected == "number":
        return actual in ("number", "integer")
    if expected == "integer":
        return actual == "integer"
    return actual == expected


def validate_instance(instance, schema, path="$"):
    """Validate against the JSON Schema subset used by this Skill Tool.

    Returns a list of {path, keyword, message}. An empty list means valid.
    """
    errors = []
    if not isinstance(schema, dict):
        return errors

    if "type" in schema:
        expected = schema["type"]
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, option) for option in options):
            errors.append({
                "path": path,
                "keyword": "type",
                "message": "expected %s but found %s" % (" or ".join(options), _type_name(instance)),
            })
            return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append({
            "path": path,
            "keyword": "enum",
            "message": "value %r is not one of %s" % (instance, schema["enum"]),
        })

    if _type_name(instance) == "string":
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append({"path": path, "keyword": "minLength",
                           "message": "shorter than the minimum length %s" % schema["minLength"]})
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append({"path": path, "keyword": "maxLength",
                           "message": "longer than the maximum length %s" % schema["maxLength"]})
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append({"path": path, "keyword": "pattern",
                           "message": "does not match required pattern %s" % schema["pattern"]})

    if _type_name(instance) in ("number", "integer"):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append({"path": path, "keyword": "minimum",
                           "message": "below the minimum %s" % schema["minimum"]})
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append({"path": path, "keyword": "maximum",
                           "message": "above the maximum %s" % schema["maximum"]})

    if _type_name(instance) == "array":
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append({"path": path, "keyword": "minItems",
                           "message": "needs at least %s item(s)" % schema["minItems"]})
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append({"path": path, "keyword": "maxItems",
                           "message": "allows at most %s item(s)" % schema["maxItems"]})
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate_instance(item, schema["items"], "%s[%d]" % (path, index)))

    if _type_name(instance) == "object":
        properties = schema.get("properties", {})
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append({"path": path, "keyword": "minProperties",
                           "message": "needs at least %s propert(ies)" % schema["minProperties"]})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append({"path": path, "keyword": "required",
                               "message": "missing required property '%s'" % key})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_instance(value, properties[key], "%s.%s" % (path, key)))
            elif additional is False:
                allowed = ", ".join(sorted(properties)) or "none"
                errors.append({"path": "%s.%s" % (path, key), "keyword": "additionalProperties",
                               "message": "unexpected property; allowed properties: %s" % allowed})
            elif isinstance(additional, dict):
                errors.extend(validate_instance(value, additional, "%s.%s" % (path, key)))
    return errors


def unsupported_keywords(schema, path="$"):
    """Report schema keywords this validator does not enforce (integrity check)."""
    found = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key not in SUPPORTED_KEYWORDS:
                found.append("%s.%s" % (path, key))
            if key in ("properties", "items", "additionalProperties") and isinstance(value, dict):
                if key == "properties":
                    for sub_key, sub in value.items():
                        found.extend(unsupported_keywords(sub, "%s.%s" % (path, sub_key)))
                else:
                    found.extend(unsupported_keywords(value, "%s.%s" % (path, key)))
    return found


# --------------------------------------------------------------------------- #
# Request handling
# --------------------------------------------------------------------------- #
DEFAULT_PROJECT = {
    "genre": "",
    "goal": "",
    "engine": "auto",
    "engine_version": "unspecified",
    "language": "",
    "platforms": ["windows"],
    "dimension": "2d",
    "scope": "vertical-slice",
    "team_size": 1,
    "weeks": 12,
    "target_fps": 60,
    "constraints": [],
    "acceptance_criteria": [],
}


def normalize_request(raw, cli_overrides=None):
    """Fill documented defaults and record which fields were assumed."""
    request = copy.deepcopy(raw or {})
    assumed = []
    project = dict(request.get("project") or {})
    for key, default in DEFAULT_PROJECT.items():
        if key not in project or project[key] in ("", None):
            project[key] = copy.deepcopy(default)
            assumed.append("project.%s" % key)
    request["project"] = project
    request.setdefault("request_version", SCHEMA_VERSION)
    capabilities = dict(request.get("capabilities") or {})
    for key in ("skills", "categories", "phases", "tags", "exclude"):
        if key not in capabilities:
            capabilities[key] = []
    capabilities.setdefault("limit", None)
    request["capabilities"] = capabilities
    request.setdefault("overrides", {})
    request.setdefault("output", {"format": "both", "include_specs": True, "overwrite": False})
    for key, value in (cli_overrides or {}).items():
        if value is None:
            continue
        if key in ("skills", "categories", "phases", "tags"):
            capabilities[key] = value
        elif key == "engine":
            project["engine"] = value
        elif key in ("platforms",):
            project["platforms"] = value
        elif key in ("engine_version", "language", "dimension", "scope", "genre", "goal"):
            project[key] = value
        elif key in ("team_size", "weeks", "target_fps"):
            project[key] = value
        elif key == "out_dir":
            request["output"]["dir"] = value
    request["assumed_defaults"] = sorted(set(assumed))
    return request


def validate_request(raw, schema):
    """Validate a raw request document. Returns (ok, errors, warnings)."""
    errors = validate_instance(raw, schema)
    warnings = []
    capabilities = raw.get("capabilities") if isinstance(raw, dict) else None
    if isinstance(capabilities, dict):
        selectors = [k for k in ("skills", "categories", "phases", "tags")
                     if capabilities.get(k)]
        if not selectors:
            errors.append({"path": "$.capabilities", "keyword": "minProperties",
                           "message": "provide at least one of skills, categories, phases, or tags"})
        if capabilities.get("limit") and selectors == ["skills"]:
            limit = capabilities["limit"]
            if isinstance(limit, int) and limit < len(capabilities.get("skills") or []):
                warnings.append({"path": "$.capabilities.limit", "message":
                                 "limit %d truncates the %d explicitly listed skills"
                                 % (limit, len(capabilities["skills"]))})
    project = raw.get("project") if isinstance(raw, dict) else None
    if isinstance(project, dict):
        engine = project.get("engine")
        if engine and engine not in ("auto", "engine-agnostic") and engine not in ROUTE_BY_ENGINE:
            warnings.append({"path": "$.project.engine", "message":
                             "engine '%s' has no routing profile; routing stays advisory" % engine})
    return (not errors), errors, warnings


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def route(request):
    """Score every routing profile against the project and pick one."""
    project = request["project"]
    dimension = project.get("dimension", "2d")
    platforms = [p.strip().lower() for p in project.get("platforms", [])]
    wants_console = bool(CONSOLE_PLATFORMS.intersection(platforms))
    team = int(project.get("team_size", 1))
    scope = project.get("scope", "vertical-slice")
    language = (project.get("language") or "").strip().lower()
    constraints = " ".join(project.get("constraints", [])).lower()
    requested = (project.get("engine") or "auto").strip().lower()

    scored = []
    for route_def in ROUTES:
        score = 0
        reasons = []
        if dimension in route_def["dimensions"]:
            score += 35
            reasons.append("supports %s presentation" % dimension)
        else:
            reasons.append("does not support %s presentation" % dimension)
        covered = [p for p in platforms if p in route_def["platforms"]]
        if platforms and len(covered) == len(platforms):
            score += 25
            reasons.append("covers every requested platform (%s)" % ", ".join(platforms))
        else:
            score += 3 * len(covered)
            missing = [p for p in platforms if p not in route_def["platforms"]]
            if missing:
                reasons.append("cannot target %s without extra work" % ", ".join(missing))
        low, high = route_def["team_range"]
        if low <= team <= high:
            score += 12
        else:
            score -= 6
            reasons.append("team size %d is outside its comfortable range %d-%d" % (team, low, high))
        if scope in route_def["scope_fit"]:
            score += 10
        else:
            reasons.append("less proven for %s scope" % scope)
        if language and language in route_def["languages"]:
            score += 10
            reasons.append("uses %s, the language you named" % language)
        if team <= 2:
            score += route_def["solo_weight"]
        if wants_console:
            if CONSOLE_PLATFORMS.intersection(route_def["platforms"]):
                score += 8
                reasons.append("declared console platform coverage")
            else:
                score -= 10
                reasons.append("no first-party console export path")
        if "royalty" in constraints and "royalty" in route_def["license"]:
            score -= 8
            reasons.append("royalty-bearing licence conflicts with a stated constraint")
        if ("open source" in constraints or "no license fee" in constraints) and \
                "free" in route_def["license"].lower():
            score += 6
            reasons.append("licence matches the no-fee constraint")
        scored.append({
            "engine": route_def["engine"],
            "label": route_def["label"],
            "score": score,
            "languages": route_def["languages"],
            "license": route_def["license"],
            "strengths": route_def["strengths"],
            "limits": route_def["limits"],
            "iteration_hours": route_def["iteration_hours"],
            "reasons": reasons,
        })

    scored.sort(key=lambda item: (-item["score"], item["engine"]))
    explicit = requested not in ("", "auto", "engine-agnostic")
    if explicit:
        selected = next((item for item in scored if item["engine"] == requested), None)
        if selected is None:
            selected = {
                "engine": requested, "label": requested, "score": 0,
                "languages": [], "license": "unspecified",
                "strengths": [], "limits": ["no routing profile for this engine"],
                "iteration_hours": 40,
                "reasons": ["explicitly requested"],
            }
            scored.insert(0, selected)
        else:
            selected = dict(selected)
            selected["reasons"] = ["explicitly requested, so routing is advisory only"] + selected["reasons"]
    else:
        selected = dict(scored[0])
        selected["reasons"] = list(selected["reasons"])

    alternatives = []
    for item in scored:
        if item["engine"] == selected["engine"]:
            continue
        alternatives.append({
            "engine": item["engine"],
            "label": item["label"],
            "score": item["score"],
            "why_not": "; ".join(item["reasons"][:3]) or "lower fit score",
        })
    return {
        "decision": "explicit" if explicit else "recommended",
        "selected": selected,
        "alternatives": alternatives[:4],
        "confidence": "high" if selected["score"] >= 60 else ("medium" if selected["score"] >= 35 else "low"),
        "note": ("Engine was requested explicitly; the score below is advisory and does not override the request."
                 if explicit else
                 "Selected by deterministic scoring of dimension, platforms, team size, scope, language, and constraints."),
    }


# --------------------------------------------------------------------------- #
# Plan construction
# --------------------------------------------------------------------------- #
def _merge_verification(verification, overrides, slug):
    merged = []
    for item in verification:
        override = overrides.get(slug) or overrides.get(slugify_intent(item["intent"]))
        merged.append({
            "intent": item["intent"],
            "command": override or item.get("command_hint"),
            "source": "request override" if override else ("category default" if item.get("command_hint") else "manual step"),
        })
    if slug in overrides and not any(v["command"] == overrides[slug] for v in merged):
        merged.append({"intent": "Project-specific verification supplied in the request",
                       "command": overrides[slug], "source": "request override"})
    return merged


def slugify_intent(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def build_plan(registry, request, source=None):
    """Build a deterministic production plan from a normalized request."""
    routing = route(request)
    project = request["project"]
    overrides = (request.get("overrides") or {}).get("verification_commands") or {}
    budget_ms = (request.get("overrides") or {}).get("performance_budget_ms") or \
        round(1000.0 / float(project.get("target_fps", 60)), 2)

    caps = request["capabilities"]
    skills = registry.select(
        skills=caps.get("skills"),
        categories=caps.get("categories"),
        phases=caps.get("phases"),
        tags=caps.get("tags"),
        exclude=caps.get("exclude"),
        limit=caps.get("limit"),
    )

    work_items = []
    for index, skill in enumerate(skills, start=1):
        contract = registry.contract(skill)
        item_id = "WI-%02d" % index
        spec_path = "specs/%s.md" % contract["slug"]
        work_items.append({
            "id": item_id,
            "capability_id": contract["id"],
            "capability": contract["name"],
            "category": contract["category"],
            "category_slug": contract["category_slug"],
            "phase": contract["phase"],
            "objective": _objective(contract, project, routing),
            "specializations": contract["specializations"],
            "tags": contract["tags"],
            "engine_family": contract["engine_family"],
            "languages": contract["languages"],
            "spec_path": spec_path,
            "artifacts": contract["artifacts"],
            "checks": contract["checks"],
            "risks": contract["risks"],
            "parameters": dict(contract["parameters"], performance_budget_ms=budget_ms),
            "verification": _merge_verification(contract["verification"], overrides, contract["slug"]),
            "approval_actions": contract["approval_actions"],
            "depends_on": contract["depends_on"],
            "effort_points": contract["effort_points"],
        })

    conflicts = _conflicts(work_items, routing)
    milestones = _milestones(registry, work_items, project)
    risk_register = []
    for item in work_items:
        for risk in item["risks"]:
            risk_register.append({
                "id": "R-%03d" % (len(risk_register) + 1),
                "risk": risk["risk"],
                "mitigation": risk["mitigation"],
                "severity": risk["severity"],
                "capability": item["capability"],
                "check_refs": [c["id"] for c in item["checks"] if c["required"]][:2],
            })
    approvals = _approvals(work_items, routing)
    total_points = sum(item["effort_points"] for item in work_items)
    capacity = _feasibility(project, total_points)

    plan = {
        "schema_version": SCHEMA_VERSION,
        "tool": registry.capabilities.get("dataset", "ultimate-game-maker-capability-catalog"),
        "tool_version": getattr(registry, "tool_version", "1.0.0"),
        "generator": "ultimate-game-maker/tools-game-maker.py",
        "request": request,
        "routing": routing,
        "summary": {
            "capabilities": len(work_items),
            "categories": len({item["category_slug"] for item in work_items}),
            "milestones": len(milestones),
            "required_checks": sum(1 for item in work_items for c in item["checks"] if c["required"]),
            "total_checks": sum(len(item["checks"]) for item in work_items),
            "risks": len(risk_register),
            "high_severity_risks": sum(1 for r in risk_register if r["severity"] == "high"),
            "effort_points": total_points,
            "approval_actions": sorted({a["action"] for a in approvals}),
            "engine_conflicts": len(conflicts),
            "performance_budget_ms": budget_ms,
        },
        "feasibility": capacity,
        "engine_conflicts": conflicts,
        "milestones": milestones,
        "work_items": work_items,
        "risk_register": risk_register,
        "approvals": approvals,
        "source": source or {"kind": "generated"},
    }
    plan["plan_id"] = canonical_hash(canonical_plan_payload(plan))
    plan["next_commands"] = _next_commands(plan, request)
    return plan


def canonical_hash(obj):
    """SHA-256 over a canonical JSON encoding, matching tools-production-registry.py."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _conflicts(work_items, routing):
    """Flag capabilities written for a different engine than the plan routes to."""
    selected = routing["selected"]["engine"]
    generic = {"2d", "3d", "cloud", "ci", "mobile", "console", "desktop", "xr", "native", "tooling"}
    conflicts = []
    for item in work_items:
        family = [entry.lower() for entry in item["engine_family"]]
        if not family or "engine-agnostic" in family or selected in family:
            continue
        if set(family) <= generic:
            continue
        conflicts.append({
            "capability": item["capability"],
            "capability_id": item["capability_id"],
            "work_item": item["id"],
            "capability_engine_family": family,
            "planned_engine": selected,
            "severity": "blocking",
            "note": ("This capability is scoped to %s while the plan routes to %s. Drop it, add the "
                     "matching engine work, or accept the rework explicitly."
                     % (", ".join(family), selected)),
        })
    return conflicts


def _objective(contract, project, routing=None):
    goal = (project.get("goal") or "").strip()
    family = ", ".join(contract["engine_family"]) or "engine-agnostic"
    if routing:
        engine_label = "%s %s" % (routing["selected"]["label"], project.get("engine_version", ""))
    else:
        engine_label = project.get("engine_version", "unspecified version")
    text = ("%s for '%s'. Plan engine context: %s. Capability engine family: %s. Phase gate: %s."
            % (contract["name"], project.get("name", "unnamed project"),
               engine_label.strip(), family, contract["phase"]))
    if goal:
        text += " Project goal: %s." % goal
    return text


def _milestones(registry, work_items, project):
    groups = {}
    for item in work_items:
        groups.setdefault(item["phase"], []).append(item)
    weeks = int(project.get("weeks", 12))
    ordered_phases = [p for p in registry.profiles_doc.get("phase_order", []) if p in groups]
    total_points = sum(item["effort_points"] for item in work_items) or 1
    milestones = []
    allocated = 0
    for index, phase in enumerate(ordered_phases):
        meta = registry.phase_meta(phase)
        items = groups[phase]
        points = sum(item["effort_points"] for item in items)
        if index == len(ordered_phases) - 1:
            phase_weeks = max(1, weeks - allocated)
        else:
            phase_weeks = max(1, int(round(weeks * points / float(total_points))))
        allocated += phase_weeks
        milestones.append({
            "id": "M%d" % (index + 1),
            "phase": phase,
            "label": meta.get("label", phase),
            "goal": meta.get("goal", ""),
            "exit_criteria": meta.get("exit_criteria", ""),
            "work_item_ids": [item["id"] for item in items],
            "capability_count": len(items),
            "effort_points": points,
            "suggested_weeks": phase_weeks,
            "depends_on": ["M%d" % index] if index else [],
            "required_check_ids": [c["id"] for item in items for c in item["checks"] if c["required"]],
            "approval_actions": sorted({a for item in items for a in item["approval_actions"]}),
        })
    return milestones


def _approvals(work_items, routing):
    policy_actions = []
    for item in work_items:
        for action in item["approval_actions"]:
            policy_actions.append({"action": action, "capability": item["capability"],
                                   "work_item": item["id"]})
    grouped = {}
    for entry in policy_actions:
        grouped.setdefault(entry["action"], []).append(entry)
    out = []
    for action in sorted(grouped):
        entries = grouped[action]
        out.append({
            "action": action,
            "capabilities": sorted({e["capability"] for e in entries}),
            "work_items": sorted({e["work_item"] for e in entries}),
            "requires_human_approval": True,
            "gate_profile": "release",
            "engine_context": routing["selected"]["engine"],
        })
    return out


def _feasibility(project, total_points):
    team = max(1, int(project.get("team_size", 1)))
    weeks = max(1, int(project.get("weeks", 12)))
    capacity = team * weeks * 2  # 2 effort points per person-week
    needed_weeks = int(round(total_points / float(team * 2))) if total_points else 0
    if total_points > capacity:
        verdict = "over_capacity"
        note = ("Selected scope needs about %d person-weeks at 2 points per person-week; the plan allows %d. "
                "Cut scope, extend the schedule, or add people." % (needed_weeks, weeks))
    elif total_points > capacity * 0.75:
        verdict = "tight"
        note = "Scope fits but leaves under 25% slack; keep the cut list ready."
    else:
        verdict = "comfortable"
        note = "Scope fits inside the declared capacity with slack for rework."
    return {
        "team_size": team,
        "weeks": weeks,
        "capacity_points": capacity,
        "required_points": total_points,
        "estimated_person_weeks": needed_weeks,
        "verdict": verdict,
        "note": note,
    }


def _next_commands(plan, request):
    out_dir = (request.get("output") or {}).get("dir") or "build/game-plan"
    profile = "milestone"
    return [
        "sh tools-verify.sh",
        "python3 ultimate-game-maker/tools-game-maker.py capabilities --category %s --format markdown"
        % plan["work_items"][0]["category_slug"],
        "python3 ultimate-game-maker/tools-game-maker.py export --plan %s/plan.json --out %s --approve --confirm APPROVE"
        % (out_dir, out_dir),
        "python3 ultimate-game-maker/tools-game-maker.py evidence-gate --plan %s/plan.json --evidence %s/evidence.json --profile %s"
        % (out_dir, out_dir, profile),
    ]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _compact_ids(ids, preview=8):
    """Render long id lists as ranges, e.g. WI-01..WI-08, WI-101..WI-103."""
    if not ids:
        return "none"
    groups = []
    start = previous = None
    previous_number = None
    for item in ids:
        prefix, _, tail = item.rpartition("-")
        try:
            number = int(tail)
        except ValueError:
            groups.append(item)
            continue
        if start is None:
            start = previous = item
            previous_number = number
            continue
        if prefix == start.rpartition("-")[0] and number == previous_number + 1:
            previous = item
            previous_number = number
        else:
            groups.append(start if start == previous else "%s..%s" % (start, previous))
            start = previous = item
            previous_number = number
    if start is not None:
        groups.append(start if start == previous else "%s..%s" % (start, previous))
    if len(groups) > preview:
        return "%s (+%d more ranges)" % (", ".join(groups[:preview]), len(groups) - preview)
    return ", ".join(groups)


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_plan_md(plan):
    project = plan["request"]["project"]
    routing = plan["routing"]
    lines = []
    add = lines.append
    add("# Production Plan: %s" % project.get("name", "unnamed project"))
    add("")
    add("- Plan id: `%s`" % plan["plan_id"])
    add("- Engine: **%s** (%s) - %s decision" % (
        routing["selected"]["label"], routing["selected"]["engine"], routing["decision"]))
    add("- Scope: %s, %s, %d week(s), team of %d" % (
        project.get("scope"), project.get("dimension"), project.get("weeks"), project.get("team_size")))
    add("- Capabilities: %d across %d categories; required checks: %d; risks: %d" % (
        plan["summary"]["capabilities"], plan["summary"]["categories"],
        plan["summary"]["required_checks"], plan["summary"]["risks"]))
    add("- Feasibility: **%s** - %s" % (plan["feasibility"]["verdict"], plan["feasibility"]["note"]))
    if project.get("goal"):
        add("- Goal: %s" % project["goal"])
    if plan["request"].get("assumed_defaults"):
        add("- Assumed defaults: %s" % ", ".join(plan["request"]["assumed_defaults"]))
    add("")
    add("## Routing")
    add("")
    add("%s" % routing["note"])
    add("")
    add("| Engine | Score | Confidence | Licence | Iteration cost |")
    add("| --- | --- | --- | --- | --- |")
    selected = routing["selected"]
    add("| %s | %d | %s | %s | ~%dh per change |" % (
        _cell(selected["label"]), selected["score"], routing["confidence"],
        _cell(selected["license"]), selected["iteration_hours"]))
    add("")
    add("Selection reasons:")
    for reason in selected["reasons"]:
        add("- %s" % reason)
    if routing["alternatives"]:
        add("")
        add("Rejected alternatives:")
        for alt in routing["alternatives"]:
            add("- **%s** (score %d) - %s" % (alt["label"], alt["score"], alt["why_not"]))
    add("")
    if plan.get("engine_conflicts"):
        add("## Engine conflicts")
        add("")
        add("These capabilities are scoped to a different engine than the plan routes to. Resolve each one "
            "before scheduling the work.")
        add("")
        add("| Work item | Capability | Capability engine family | Planned engine |")
        add("| --- | --- | --- | --- |")
        for conflict in plan["engine_conflicts"]:
            add("| %s | %s | %s | %s |" % (conflict["work_item"], _cell(conflict["capability"]),
                                           ", ".join(conflict["capability_engine_family"]),
                                           conflict["planned_engine"]))
        add("")
    add("## Milestones")
    add("")
    add("| Milestone | Phase | Capabilities | Points | Weeks | Approvals |")
    add("| --- | --- | --- | --- | --- | --- |")
    for milestone in plan["milestones"]:
        add("| %s %s | %s | %d | %d | %d | %s |" % (
            milestone["id"], _cell(milestone["label"]), milestone["phase"],
            milestone["capability_count"], milestone["effort_points"],
            milestone["suggested_weeks"],
            _cell(", ".join(milestone["approval_actions"]) or "none")))
    for milestone in plan["milestones"]:
        add("")
        add("### %s - %s" % (milestone["id"], milestone["label"]))
        add("")
        add("- Goal: %s" % milestone["goal"])
        add("- Exit criteria: %s" % milestone["exit_criteria"])
        add("- Work items (%d): %s" % (len(milestone["work_item_ids"]),
                                        _compact_ids(milestone["work_item_ids"])))
    add("")
    add("## Work items")
    for item in plan["work_items"]:
        add("")
        add("### %s - %s" % (item["id"], item["capability"]))
        add("")
        add("- Capability id: `%s`" % item["capability_id"])
        add("- Category: %s (%s)" % (item["category"], item["phase"]))
        add("- Objective: %s" % item["objective"])
        if item["specializations"]:
            add("- Specializations: %s" % ", ".join(item["specializations"]))
        add("- Effort: %d points; spec: `%s`" % (item["effort_points"], item["spec_path"]))
        if item["depends_on"]:
            add("- Depends on: %s" % ", ".join("`%s`" % d for d in item["depends_on"]))
        add("")
        add("Deliverables:")
        for artifact in item["artifacts"]:
            add("- **%s** (%s) - %s" % (artifact["title"], artifact.get("format", "markdown"),
                                         artifact.get("purpose", "")))
        add("")
        add("Acceptance checks:")
        add("")
        add("| Check id | Required | Class | Statement | Evidence |")
        add("| --- | --- | --- | --- | --- |")
        for check in item["checks"]:
            add("| `%s` | %s | %s | %s | %s |" % (
                check["id"], "yes" if check["required"] else "no",
                check["class"], _cell(check["statement"]), _cell(check["evidence"])))
        add("")
        add("Risks:")
        for risk in item["risks"]:
            add("- **%s** (%s) - mitigation: %s" % (risk["risk"], risk["severity"], risk["mitigation"]))
        if item["approval_actions"]:
            add("")
            add("Approval required before: %s" % ", ".join(item["approval_actions"]))
    add("")
    add("## Approval ledger requirements")
    add("")
    if plan["approvals"]:
        add("| Action | Capabilities | Gate |")
        add("| --- | --- | --- |")
        for approval in plan["approvals"]:
            add("| %s | %s | %s |" % (_cell(approval["action"]),
                                       _cell(", ".join(approval["capabilities"])),
                                       approval["gate_profile"]))
    else:
        add("No approval-sensitive actions were selected for this plan.")
    add("")
    add("## Risk register")
    add("")
    add("| Id | Severity | Risk | Mitigation | Capability |")
    add("| --- | --- | --- | --- | --- |")
    for risk in plan["risk_register"]:
        add("| %s | %s | %s | %s | %s |" % (
            risk["id"], risk["severity"], _cell(risk["risk"]),
            _cell(risk["mitigation"]), _cell(risk["capability"])))
    add("")
    add("## Next commands")
    add("")
    for command in plan["next_commands"]:
        add("- `%s`" % command)
    add("")
    return "\n".join(lines)


def render_checklist_md(plan):
    lines = ["# Acceptance Checklist: %s" % plan["request"]["project"].get("name", "unnamed project"), "",
             "Plan id: `%s`" % plan["plan_id"], "",
             "Mark each check in the evidence file, then run the gate command shown at the end.", ""]
    for milestone in plan["milestones"]:
        lines.append("## %s - %s" % (milestone["id"], milestone["label"]))
        lines.append("")
        for item in plan["work_items"]:
            if item["id"] not in milestone["work_item_ids"]:
                continue
            lines.append("### %s - %s" % (item["id"], item["capability"]))
            lines.append("")
            for check in item["checks"]:
                box = "x" if False else " "
                required = "required" if check["required"] else "optional"
                lines.append("- [%s] `%s` (%s, %s) %s" % (
                    box, check["id"], required, check["class"], check["statement"]))
                lines.append("  - evidence: %s" % check["evidence"])
            lines.append("")
            if item["approval_actions"]:
                lines.append("  - approval required before: %s" % ", ".join(item["approval_actions"]))
                lines.append("")
    lines.append("## Gate")
    lines.append("")
    lines.append("`python3 ultimate-game-maker/tools-game-maker.py evidence-gate --plan plan.json --evidence evidence.json --profile milestone`")
    lines.append("")
    return "\n".join(lines)


def render_handoff_md(plan):
    project = plan["request"]["project"]
    selected = plan["routing"]["selected"]
    lines = ["# Handoff: %s" % project.get("name", "unnamed project"), "",
             "This handoff is editable. It states the environment, the commands, the ownership, and the limits.",
             "",
             "## Environment to reproduce",
             "",
             "| Item | Value |",
             "| --- | --- |",
             "| Engine | %s (`%s`) |" % (_cell(selected["label"]), selected["engine"]),
             "| Engine version | %s |" % _cell(project.get("engine_version")),
             "| Language | %s |" % _cell(project.get("language") or ", ".join(selected["languages"])),
             "| Platforms | %s |" % _cell(", ".join(project.get("platforms", []))),
             "| Dimension | %s |" % _cell(project.get("dimension")),
             "| Scope | %s |" % _cell(project.get("scope")),
             "| Performance budget | %s ms per frame |" % plan["summary"]["performance_budget_ms"],
             "| Licence note | %s |" % _cell(selected["license"]),
             "",
             "## Commands",
             "",
             "Run from the skill root, in a POSIX shell:",
             ""]
    for command in plan["next_commands"]:
        lines.append("- `%s`" % command)
    lines += ["", "## Ownership by milestone", "",
              "| Milestone | Capabilities | Suggested weeks | Next owner action |",
              "| --- | --- | --- | --- |"]
    for milestone in plan["milestones"]:
        lines.append("| %s %s | %d | %d | collect evidence for %d required checks |" % (
            milestone["id"], _cell(milestone["label"]), milestone["capability_count"],
            milestone["suggested_weeks"], len(milestone["required_check_ids"])))
    lines += ["", "## Known limitations of this plan", "",
              "- This Skill Tool produces specifications, checks, gates, and handoffs. It does not compile or run Unity, Unreal, Godot, console, or handheld builds; those steps need the real engine and SDK on a machine that has them.",
              "- Command hints come from the category profiles and must be confirmed against the exact engine/editor version in use.",
              "- Checks marked required are only satisfied by real evidence; the gate engine rejects empty or placeholder evidence text.",
              "- Items whose verification needs hardware (XR devices, consoles, handhelds) stay blocked until a human runs them.",
              ""]
    return "\n".join(lines)


def render_spec_md(item, plan):
    project = plan["request"]["project"]
    lines = ["# %s" % item["capability"], "",
             "| Field | Value |", "| --- | --- |",
             "| Capability id | `%s` |" % item["capability_id"],
             "| Work item | %s |" % item["id"],
             "| Category | %s |" % _cell(item["category"]),
             "| Phase | %s |" % item["phase"],
             "| Project | %s |" % _cell(project.get("name")),
             "| Engine context | %s |" % _cell(plan["routing"]["selected"]["label"]),
             "",
             "## Objective",
             "",
             item["objective"],
             "",
             "## Deliverables",
             ""]
    for artifact in item["artifacts"]:
        lines.append("- **%s** (%s): %s" % (artifact["title"], artifact.get("format", "markdown"),
                                            artifact.get("purpose", "")))
    lines += ["", "## Acceptance checks", "",
              "| Check id | Required | Class | Statement | Evidence to collect |",
              "| --- | --- | --- | --- | --- |"]
    for check in item["checks"]:
        lines.append("| `%s` | %s | %s | %s | %s |" % (
            check["id"], "yes" if check["required"] else "no", check["class"],
            _cell(check["statement"]), _cell(check["evidence"])))
    lines += ["", "## Risks and mitigations", ""]
    for risk in item["risks"]:
        lines.append("- **%s** (%s): %s" % (risk["risk"], risk["severity"], risk["mitigation"]))
    lines += ["", "## Parameters to configure", ""]
    if item["parameters"]:
        for key in sorted(item["parameters"]):
            value = item["parameters"][key]
            if isinstance(value, list):
                value = ", ".join(str(entry) for entry in value)
            elif isinstance(value, dict):
                value = json.dumps(value, sort_keys=True)
            lines.append("- `%s`: %s" % (key, value))
    else:
        lines.append("- No capability-specific parameters; use the project-level settings.")
    lines += ["", "## Verification steps", "",
              "| Step | Command | Source |", "| --- | --- | --- |"]
    for step in item["verification"]:
        lines.append("| %s | %s | %s |" % (
            _cell(step["intent"]),
            "`%s`" % _cell(step["command"]) if step.get("command") else "manual",
            step["source"]))
    lines += ["", "## Dependencies", ""]
    if item["depends_on"]:
        for dep in item["depends_on"]:
            lines.append("- `%s`" % dep)
    else:
        lines.append("- None declared; this capability can start immediately.")
    if item["approval_actions"]:
        lines += ["", "## Approval required", ""]
        for action in item["approval_actions"]:
            lines.append("- `%s` must be approved by a human before execution." % action)
    lines.append("")
    return "\n".join(lines)


def canonical_plan_payload(plan):
    """The subset of a plan that defines its identity (used for plan_id)."""
    return {
        "request": plan["request"],
        "capability_ids": [item["capability_id"] for item in plan["work_items"]],
        "check_ids": [c["id"] for item in plan["work_items"] for c in item["checks"]],
        "schema_version": plan["schema_version"],
    }
