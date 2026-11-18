#!/usr/bin/env python3
"""tools-plan-builder.py

Support-plan rendering, handoff/referral packets, consent-based preference
memory, and JSON Schema (subset) validation for the HumanSphere Holistic
Support Skill Tool.

Relative paths from the future skill root:
    humansphere-holistic-support/tools-plan-builder.py
    humansphere-holistic-support/tools-catalog-engine.py
    humansphere-holistic-support/tools-safety-engine.py
    humansphere-holistic-support/asset-plan-template.md
    humansphere-holistic-support/asset-preference-profile.schema.json
    humansphere-holistic-support/asset-support-resources.json
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(filename: str, module_name: str):
    if module_name in sys.modules:
        return sys.modules[module_name]
    target = _HERE / filename
    if not target.is_file():
        raise SystemExit(f"Missing required sibling module: {filename} (expected in {_HERE.name}/).")
    spec = importlib.util.spec_from_file_location(module_name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


engine = _load("tools-catalog-engine.py", "hs_catalog_engine")
safety = _load("tools-safety-engine.py", "hs_safety_engine")

CatalogError = engine.CatalogError
SafetyError = safety.SafetyError

PROFILE_SCHEMA = "humansphere.preference-profile/1"
PROFILE_TOP_LEVEL_KEYS = frozenset(
    {"schema", "consent", "identity", "preferences", "accessibility", "context",
     "boundaries", "persona", "memory"}
)
REQUIRED_PLACEHOLDERS = (
    "TITLE",
    "QUERY",
    "SUPPORT_MODE",
    "URGENCY_LEVEL",
    "URGENCY_LABEL",
    "PERSONALIZATION",
    "GENERATED_AT",
    "SAFETY_SECTION",
    "SKILLS_TABLE",
    "SKILL_DETAIL",
    "BUNDLE_SECTION",
    "RESOURCES",
    "PROFILE_SECTION",
    "REVIEW_SECTION",
    "LIMITS",
)

MODES = (
    "listening",
    "planning",
    "research",
    "teaching",
    "drafting",
    "practice",
    "escalation",
)

MODE_CUES = {
    "escalation": ("kill myself", "self-harm", "overdose", "emergency", "chest pain",
                   "cannot breathe", "can't breathe", "danger", "abuse", "threat"),
    "drafting": ("draft", "write", "rewrite", "email", "message", "letter", "apolog",
                 "resume", "cv", "cover letter", "post"),
    "planning": ("plan", "schedule", "budget", "steps", "organise", "organize", "routine",
                 "timeline", "checklist", "goal", "decide", "compare", "troubleshoot",
                 "not working", "failing", "fix", "repair", "sort out", "cost", "costs",
                 "what should i do", "how do i"),
    "research": ("research", "find", "look up", "compare", "options", "which", "sources",
                 "current", "latest", "requirements"),
    "teaching": ("explain", "what is", "how does", "teach", "understand", "meaning of",
                 "difference between"),
    "practice": ("practice", "rehearse", "roleplay", "role play", "role-play", "try out",
                 "simulate", "drill"),
}

TONE_DIRECTIVES = {
    "gentle": "Warm, unhurried wording; short sentences; permission-seeking questions.",
    "neutral": "Plain, even wording; no filler; clear statements.",
    "direct": "Lead with the point, then the reason; no hedging; keep respect.",
    "energetic": "Upbeat and momentum-building, without pressure or hype.",
    "formal": "Complete sentences, professional register, no slang or emoji.",
    "playful": "Light and warm humour allowed on low-stakes topics only.",
}

DIRECTNESS_WORDS = {
    1: "very soft - cushion every ask, invite rather than direct",
    2: "soft - suggest options, name the recommendation second",
    3: "balanced - state the recommendation plainly with one alternative",
    4: "firm - recommendation first, brief rationale, one alternative",
    5: "very firm - single recommendation, rationale, no menu unless asked",
}

FORMAT_WORDS = {

    "brief": "keep the whole output under ~200 words unless the user asks for more",
    "structured": "use short headed sections and checklists",
    "detailed": "full sections with examples and rationale",
}


# ---------------------------------------------------------------------------
# Template handling
# ---------------------------------------------------------------------------
def load_template(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise CatalogError(
            "template_missing",
            f"Plan template not found: {path}",
            "Pass --template with a valid Markdown template, or use the shipped "
            "humansphere-holistic-support/asset-plan-template.md.",
        )
    return path.read_text(encoding="utf-8")


def validate_template(text: str) -> dict:
    found = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", text))
    missing = [key for key in REQUIRED_PLACEHOLDERS if key not in found]
    unknown = sorted(found - set(REQUIRED_PLACEHOLDERS))
    return {
        "valid": not missing,
        "missing_placeholders": missing,
        "unknown_placeholders": unknown,
        "placeholders_present": sorted(found),
    }


def render_template(template: str, values: dict) -> str:
    check = validate_template(template)
    if not check["valid"]:
        raise CatalogError(
            "template_incomplete",
            "Plan template is missing placeholders: " + ", ".join(check["missing_placeholders"]),
            "Add the missing {{PLACEHOLDERS}} or restore asset-plan-template.md.",
        )
    out = template
    for key in REQUIRED_PLACEHOLDERS:
        out = out.replace("{{" + key + "}}", str(values.get(key, "")))
    return out


# ---------------------------------------------------------------------------
# Preference profile: defaults, schema validation, consent-based memory
# ---------------------------------------------------------------------------
def default_profile() -> dict:
    return {
        "schema": PROFILE_SCHEMA,
        "consent": {"granted": False, "granted_at": "", "scope": [], "note": ""},
        "identity": {
            "name_used": "",
            "pronouns": "",
            "age_band": "not-stated",
            "languages": [],
            "roles": [],
        },
        "preferences": {
            "tone": "neutral",
            "directness": 3,
            "format": "structured",
            "reading_level": "plain",
            "humor": "none",
            "examples": True,
            "max_list_items": 7,
        },
        "accessibility": {"needs": [], "sensory": [], "reading": []},
        "context": {"region": "", "timezone": "", "goals": [], "limits": [], "values": []},
        "boundaries": {"never_do": [], "topics_to_avoid": [], "require_approval_for": []},
        "persona": {
            "enabled": False,
            "label": "",
            "voice": "",
            "values": [],
            "fiction_disclosure": "when-roleplay-starts",
        },
        "memory": {"entries": []},
    }


def load_profile(path: str | Path, required: bool = False) -> dict:
    path = Path(path)
    if not path.is_file():
        if required:
            raise CatalogError(
                "profile_missing",
                f"Preference profile not found: {path}",
                "Create one first: profile init --out <path> --consent.",
            )
        return default_profile()
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(
            "profile_invalid_json",
            f"Preference profile is not valid JSON: {path} ({exc.msg} at line {exc.lineno}).",
            "Restore a backup or re-create it with: profile init --out <path> --consent --approve.",
        ) from exc
    merged = default_profile()
    _deep_update(merged, profile)
    return merged


def _deep_update(base: dict, patch: dict) -> dict:
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def save_profile(profile: dict, path: str | Path, consent: bool, approve: bool = False) -> dict:
    path = configurability_check(path)
    gate = validate_profile(profile)
    if not gate["valid"]:
        raise CatalogError(
            "profile_invalid",
            "Profile failed schema validation: " + "; ".join(gate["errors"][:4]),
            "Run the profile validate command for the full list.",
        )
    if not consent:
        raise SafetyError(
            "consent_required",
            "Writing preference memory requires explicit consent (approval gate A-MEMORY-WRITE).",
            "Re-run with --consent, and record the consent scope in consent.scope.",
        )
    if not profile.get("consent", {}).get("granted"):
        raise SafetyError(
            "consent_not_granted",
            "The profile's consent.granted flag is false, so nothing may be stored.",
            "Set consent with: profile set --key consent.granted --value true --consent.",
        )
    existed = path.exists()
    if existed and not approve:
        raise SafetyError(
            "approval_required",
            f"Refusing to overwrite the existing profile without approval: {path}",
            "Re-run with --approve to confirm the overwrite (approval gate A-OVERWRITE).",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    engine.atomic_write_json(path, profile)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "overwrote": existed,
        "memory_entries": len(profile.get("memory", {}).get("entries", [])),
    }


def configurability_check(path: str | Path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise CatalogError(
            "invalid_output_path",
            f"Preference profile must be written as .json, got: {path.name}",
            "Use --out path/to/preferences.json.",
        )
    return path


def validate_profile(profile: dict, schema: dict | None = None) -> dict:
    errors: list[str] = []
    if schema is None:
        errors = _inline_profile_checks(profile)
    else:
        errors = validate_instance(schema, profile)
    return {"valid": not errors, "errors": errors}


def _inline_profile_checks(profile: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]
    if not isinstance(profile.get("consent"), dict):
        errors.append("consent must be an object")
    elif not isinstance(profile["consent"].get("granted"), bool):
        errors.append("consent.granted must be a boolean")
    prefs = profile.get("preferences")
    if not isinstance(prefs, dict):
        errors.append("preferences must be an object")
    else:
        if prefs.get("tone") not in ("gentle", "neutral", "direct", "energetic", "formal", "playful"):
            errors.append(f"preferences.tone '{prefs.get('tone')}' is not an allowed tone")
        if not isinstance(prefs.get("directness"), int) or not 1 <= prefs["directness"] <= 5:
            errors.append("preferences.directness must be an integer from 1 to 5")
        if prefs.get("format") not in ("brief", "structured", "detailed"):
            errors.append("preferences.format must be brief, structured, or detailed")
    memory = profile.get("memory")
    if not isinstance(memory, dict) or not isinstance(memory.get("entries"), list):
        errors.append("memory.entries must be a list")
    else:
        for index, entry in enumerate(memory["entries"]):
            if not isinstance(entry, dict) or not entry.get("key") or not isinstance(
                entry.get("value"), str
            ):
                errors.append(f"memory.entries[{index}] needs a key and a string value")
    return errors


def validate_instance(schema: dict, instance, path: str = "$") -> list[str]:
    """Validate with a documented JSON Schema subset (no third-party dependency)."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: schema fragment is not an object"]
    expected = schema.get("type")
    if expected:
        ok = {
            "object": isinstance(instance, dict),
            "array": isinstance(instance, list),
            "string": isinstance(instance, str),
            "integer": isinstance(instance, int) and not isinstance(instance, bool),
            "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
            "boolean": isinstance(instance, bool),
            "null": instance is None,
        }.get(expected, True)
        if not ok:
            return [f"{path}: expected {expected}, got {type(instance).__name__}"]

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if not re.search(schema["pattern"], instance):
                    errors.append(f"{path}: does not match pattern {schema['pattern']}")
            except re.error:
                errors.append(f"{path}: schema pattern is not a valid regex")
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: '{instance}' is not one of {schema['enum']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required") or []:
            if key not in instance:
                errors.append(f"{path}: missing required key '{key}'")
        props = schema.get("properties") or {}
        for key, value in instance.items():
            if key in props:
                errors.extend(validate_instance(props[key], value, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key '{key}'")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate_instance(item_schema, item, f"{path}[{index}]"))
    return errors


def get_path(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise CatalogError(
                "key_not_found",
                f"Preference key not found: {dotted}",
                "Run 'profile show' to see stored keys, then use a key that exists.",
            )
        node = node[part]
    return node


def set_path(data: dict, dotted: str, value) -> None:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise CatalogError("invalid_key", "Preference key cannot be empty.")
    if parts[0] == "memory" and parts[1:2] == ["entries"]:
        raise CatalogError(
            "invalid_key",
            "Use 'profile remember --key ... --value ...' to add memory entries.",
        )
    if parts[0] not in PROFILE_TOP_LEVEL_KEYS:
        raise CatalogError(
            "unknown_key",
            f"Unknown profile section: '{parts[0]}'",
            "Top-level keys are: " + ", ".join(sorted(PROFILE_TOP_LEVEL_KEYS)),
        )
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return None


def coerce_value(value: str):
    text = str(value).strip()
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if re.fullmatch(r"-?\d{1,6}", text):
        return int(text)
    if text.startswith("[") or text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def remember(profile: dict, key: str, value: str, source: str = "user-stated") -> dict:
    entries = profile.setdefault("memory", {}).setdefault("entries", [])
    if len(entries) >= 200:
        raise CatalogError(
            "memory_full",
            "Preference memory already holds 200 entries.",
            "Forget an older entry first: profile forget --key <key> --approve.",
        )
    entries[:] = [e for e in entries if e.get("key") != key]
    entry = {"key": key, "value": str(value)[:1000], "added_at": now_utc(), "source": source}
    entries.append(entry)
    return entry


def forget(profile: dict, key: str | None, all_entries: bool) -> dict:
    entries = profile.setdefault("memory", {}).setdefault("entries", [])
    if all_entries:
        removed = len(entries)
        entries.clear()
        return {"removed": removed, "scope": "all"}
    kept = [e for e in entries if e.get("key") != key]
    removed = len(entries) - len(kept)
    if removed == 0:
        raise CatalogError(
            "key_not_found",
            f"No memory entry stored under key '{key}'.",
            "Run 'profile show' to list stored memory keys.",
        )
    profile["memory"]["entries"] = kept
    return {"removed": removed, "scope": key}


# ---------------------------------------------------------------------------
# Routing helpers: support mode, tone, personalization
# ---------------------------------------------------------------------------
def classify_mode(query: str, urgency: dict | None = None) -> dict:
    if urgency and urgency.get("human_handoff_required"):
        return {
            "mode": "escalation",
            "reason": f"urgency level '{urgency['level']}' requires qualified human help first",
            "scores": {},
        }
    lowered = str(query or "").lower()
    scores = {}
    for mode, cues in MODE_CUES.items():
        hits = [cue for cue in cues if cue in lowered]
        if hits:
            scores[mode] = len(hits)
    if not scores:
        return {"mode": "listening", "reason": "no task cue found; listen and clarify first", "scores": {}}
    best = max(scores.items(), key=lambda kv: (kv[1], -list(MODES).index(kv[0])))
    return {
        "mode": best[0],
        "reason": f"cue words matched: {best[1]}",
        "scores": scores,
    }


def tone_directive(profile: dict) -> str:
    prefs = (profile or {}).get("preferences") or {}
    tone = prefs.get("tone", "neutral")
    directness = prefs.get("directness", 3)
    try:
        directness = int(directness)
    except (TypeError, ValueError):
        directness = 3
    directness = max(1, min(5, directness))
    parts = [
        TONE_DIRECTIVES.get(tone, TONE_DIRECTIVES["neutral"]),
        f"Directness {directness}/5: {DIRECTNESS_WORDS[directness]}.",
        f"Format: {FORMAT_WORDS.get(prefs.get('format', 'structured'), FORMAT_WORDS['structured'])}.",
    ]
    humor = prefs.get("humor", "none")
    if humor != "none":
        parts.append(f"Humour: {humor}, low-stakes topics only, never about the user's pain.")
    else:
        parts.append("Humour: none unless the user asks.")
    reading = prefs.get("reading_level", "plain")
    parts.append(f"Reading level: {reading}.")
    return " ".join(parts)


def personalization_note(profile: dict) -> str:
    identity = (profile or {}).get("identity") or {}
    bits = []
    if identity.get("name_used"):
        bits.append(f"use name '{identity['name_used']}'")
    if identity.get("pronouns"):
        bits.append(f"pronouns '{identity['pronouns']}'")
    if identity.get("languages"):
        bits.append("language(s): " + ", ".join(identity["languages"][:5]))
    access = (profile or {}).get("accessibility") or {}
    for key in ("needs", "sensory", "reading"):
        if access.get(key):
            bits.append(f"{key}: " + ", ".join(str(x) for x in access[key][:4]))
    persona = (profile or {}).get("persona") or {}
    if persona.get("enabled"):
        bits.append(
            f"fictional style '{persona.get('label', 'persona')}' "
            f"(disclosure: {persona.get('fiction_disclosure', 'when-roleplay-starts')})"
        )
    prefs = (profile or {}).get("preferences") or {}
    bits.append(
        f"tone {prefs.get('tone', 'neutral')}, directness {prefs.get('directness', 3)}/5, "
        f"format {prefs.get('format', 'structured')}, reading {prefs.get('reading_level', 'plain')}"
    )
    ctx = (profile or {}).get("context") or {}
    if ctx.get("goals"):
        bits.append("goals: " + "; ".join(str(g) for g in ctx["goals"][:3]))
    if ctx.get("limits"):
        bits.append("limits: " + "; ".join(str(g) for g in ctx["limits"][:3]))
    if ctx.get("region"):
        bits.append(f"region {ctx['region']}")
    consent = (profile or {}).get("consent") or {}
    if not consent.get("granted"):
        return (
            "Consent not granted: no preference memory is stored or applied. Using portable "
            "defaults (neutral tone, directness 3/5, structured sections, plain language)."
        )
    if not bits:
        return "Profile stored but empty; using portable defaults for tone, format, and language."
    return "; ".join(bits) + "."


# ---------------------------------------------------------------------------
# Resource registry
# ---------------------------------------------------------------------------
def load_resources(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise CatalogError(
            "resources_missing",
            f"Resource registry not found: {path}",
            "Pass --resources with a valid registry, or use the shipped "
            "humansphere-holistic-support/asset-support-resources.json.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(
            "resources_invalid_json",
            f"Resource registry is not valid JSON: {path} ({exc.msg} at line {exc.lineno}).",
        ) from exc
    if not isinstance(data.get("resources"), list):
        raise CatalogError(
            "resources_invalid",
            "Resource registry needs a top-level 'resources' array.",
            "Restore the shipped registry or add a 'resources' array.",
        )
    return data


def select_resources(registry: dict, region: str = "", kinds: list[str] | None = None,
                     include_global: bool = True, limit: int = 8) -> list[dict]:
    wanted_kinds = {k.lower() for k in (kinds or [])}
    region_key = engine.normalize_text(region).replace(" ", "-") if region else ""
    out: list[dict] = []
    for entry in registry.get("resources", []):
        entry_region = str(entry.get("region", "")).lower()
        if region_key and entry_region not in (region_key, "global"):
            continue
        if region_key and entry_region == "global" and not include_global:
            continue
        if wanted_kinds:
            entry_kinds = {str(k).lower() for k in entry.get("kinds", [])}
            if not (wanted_kinds & entry_kinds):
                continue
        out.append(entry)
        if len(out) >= max(1, min(limit, 20)):
            break
    return out


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------
def build_plan(
    catalog: dict,
    policy: dict,
    query: str,
    max_skills: int = engine.LIMITS["default_max_skills"],
    timeout: float = engine.LIMITS["default_timeout_seconds"],
    profile: dict | None = None,
    registry: dict | None = None,
    region: str = "",
    force_governance: bool = True,
) -> dict:
    profile = profile or default_profile()
    urgency = safety.assess_urgency(query, policy)
    mode = classify_mode(query, urgency)
    routing = engine.route(
        catalog, query, max_skills=max_skills, timeout=timeout, force_governance=force_governance
    )
    registry = registry or {"resources": []}
    effective_region = region or (profile.get("context") or {}).get("region", "")
    kinds = ["emergency", "crisis"] if urgency["rank"] >= 2 else ["crisis", "health", "community", "helpline"]
    resources = select_resources(registry, region=effective_region, kinds=kinds)

    success_checks = _success_checks(urgency, routing)
    plan = {
        "schema": "humansphere.support-plan/1",
        "title": f"Holistic support plan - {mode['mode']}",
        "query": query,
        "mode": mode,
        "urgency": urgency,
        "routing": routing,
        "resources": resources,
        "profile": {
            "used": bool((profile.get("consent") or {}).get("granted")),
            "personalization": personalization_note(profile),
            "tone_directive": tone_directive(profile),
            "boundaries": profile.get("boundaries") or {},
        },
        "success_checks": success_checks,
        "generated_at": now_utc(),
    }
    return plan


def _success_checks(urgency: dict, routing: dict) -> list[str]:
    checks = [
        "The stated need is restated in the user's own words and confirmed.",
        "Only the smallest useful set of skills is active, with clear role separation.",
        "Every action item is something the user chose, not something imposed.",
    ]
    if urgency["rank"] >= 1:
        checks.insert(
            0,
            f"Urgency level '{urgency['level']}' is named, and a qualified human route is offered "
            "before any planning content.",
        )
    if any(item.get("needs_human_referral") for item in routing["selected"]):
        checks.append("Professional limits are stated wherever medicine, law, or money is discussed.")
    checks.append("The user is told they can correct the record and that memory is opt-in.")
    return checks


def skill_section_lines(plan: dict, max_items: int = 7) -> str:
    rows = ["| # | Skill | Category | Risk | Why selected |", "| --- | --- | --- | --- | --- |"]
    for index, item in enumerate(plan["routing"]["selected"], start=1):
        rows.append(
            f"| {index} | {item['id']}. {item['name']} | {item['category']} | "
            f"{item['risk']} | {item['reason']} |"
        )
    return "\n".join(rows)


def skill_detail_block(plan: dict, max_items: int = 7) -> str:
    blocks = []
    for item in plan["routing"]["selected"]:
        steps = (item.get("steps") or [])[:max_items]
        lines = [f"### {item['id']}. {item['name']}", "", f"_{item['description']}_", ""]
        if steps:
            lines.append("Operating prompts:")
            lines.extend(f"- [ ] {step}" for step in steps)
        if item.get("needs_human_referral"):
            lines.append(
                "- [ ] Hand off to a qualified human where this touches medicine, law, money, "
                "or crisis risk."
            )
        if item.get("pinned"):
            lines.append("- [ ] Keep this governance check attached for the whole session.")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def bundle_block(plan: dict) -> str:
    selected = plan["routing"]["selected"]
    shown = ", ".join(item["name"] for item in selected[:4])
    extra = len(selected) - 4
    names = shown + (f", plus {extra} more from the table above" if extra > 0 else "")
    urgency = plan["urgency"]
    lines = [
        "1. **Listen** - restate the need, the preferred support mode, context, and limits.",
        f"2. **Clarify** - separate emotion, facts, decisions, practical tasks, and risks "
        f"(detected mode: {plan['mode']['mode']}).",
        f"3. **Support** - use this smallest set: {names}.",
        "4. **Protect** - apply privacy, non-inference, professional scope, and escalation rules.",
        "5. **Act** - produce one usable artifact: a plan, checklist, draft, practice run, or referral path.",
        "6. **Review** - confirm the user's choice, corrections, next step, and any human handoff.",
    ]
    if urgency["rank"] >= 1:
        lines.insert(
            0,
            f"0. **Escalate first** - urgency level '{urgency['level']}'. "
            f"Required actions: {'; '.join(urgency['required_actions']) or 'none listed'}.",
        )
    return "\n".join(lines)


def safety_block(plan: dict) -> str:
    urgency = plan["urgency"]
    lines = [f"- Urgency level: **{urgency['level']}** ({urgency['label']})."]
    if urgency["signals"]:
        for signal in urgency["signals"]:
            lines.append(
                f"- Signal {signal['signal_id']} ({signal['level']}): "
                + "; ".join(signal["evidence"][:3])
            )
    if urgency["required_actions"]:
        lines.append("- Required actions:")
        lines.extend(f"  - [ ] {action}" for action in urgency["required_actions"])
    if urgency.get("escalation_text"):
        lines.extend(["", "Suggested escalation wording:", "", f"> {urgency['escalation_text']}"])
    if urgency["rank"] == 0:
        lines.append(
            "- No acute risk signal detected. Still name the human option once, without alarm."
        )
    lines.append(
        "- This tool does not diagnose, treat, advise legally or financially, or contact anyone "
        "on the user's behalf."
    )
    return "\n".join(lines)


def resources_block(plan: dict, region: str = "") -> str:
    if not plan["resources"]:
        return (
            "No bundled resource entries matched this region or category. Do not invent contact "
            "details: ask the user for their region, then look up the current official emergency, "
            "crisis, or service page together, or use the user's existing trusted service."
        )
    lines = [
        "Official source pointers only. Contact details are deliberately not bundled - verify the "
        "current number or address on the official page before sharing it.",
        "",
        "| Service | Region | Kinds | Official source | Verify |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in plan["resources"]:
        lines.append(
            f"| {entry.get('name', '')} | {entry.get('region', '')} | "
            f"{', '.join(entry.get('kinds', []))} | {entry.get('official_url', '')} | "
            f"{entry.get('verify_note', 'confirm on the official page')} |"
        )
    return "\n".join(lines)


def profile_block(plan: dict) -> str:
    profile = plan["profile"]
    lines = [
        condition_line
        for condition_line in [
            f"- Personalization: {profile['personalization']}",
            f"- Tone directive: {profile['tone_directive']}",
        ]
    ]
    boundaries = profile.get("boundaries") or {}
    for key, label in (("never_do", "Never do"), ("topics_to_avoid", "Avoid unless the user reopens it"),
                       ("require_approval_for", "Require explicit approval for")):
        if boundaries.get(key):
            raw = boundaries[key]
            items = raw if isinstance(raw, (list, tuple)) else [raw]
            lines.append(f"- {label}: " + "; ".join(str(x) for x in items))
    lines.append(
        "- Memory stayed on this device: no preferences are written unless consent is granted and "
        "the command includes --consent."
    )
    return "\n".join(lines)


def review_block(plan: dict, policy: dict) -> str:
    lines = ["- [ ] " + check for check in plan["success_checks"]]
    lines.append("")
    lines.append("Policy checklist:")
    lines.extend("- [ ] " + check for check in safety.governance_summary(policy)["review_checklist"])
    return "\n".join(lines)


def limits_block(plan: dict) -> str:
    return "\n".join(
        [
            "- Not a clinician, lawyer, financial adviser, spiritual authority, or real companion.",
            "- No network access: no live source lookups, no sending, no publishing, no account access.",
            "- Skill selection is keyword-based and explainable, not a semantic model; review the "
            "selected set before acting on it.",
            "- Bundled resources are source pointers, not verified contact details.",
            f"- Routing considered {plan['routing']['candidates_considered']} candidate skills with a "
            f"{plan['routing']['max_skills']}-skill cap.",
        ]
    )


def render_plan(plan: dict, template: str, policy: dict) -> str:
    values = {
        "TITLE": plan["title"],
        "QUERY": plan["query"],
        "SUPPORT_MODE": f"{plan['mode']['mode']} ({plan['mode']['reason']})",
        "URGENCY_LEVEL": plan["urgency"]["level"],
        "URGENCY_LABEL": plan["urgency"]["label"],
        "PERSONALIZATION": plan["profile"]["personalization"],
        "GENERATED_AT": plan["generated_at"],
        "SAFETY_SECTION": safety_block(plan),
        "SKILLS_TABLE": skill_section_lines(plan),
        "SKILL_DETAIL": skill_detail_block(plan),
        "BUNDLE_SECTION": bundle_block(plan),
        "RESOURCES": resources_block(plan),
        "PROFILE_SECTION": profile_block(plan),
        "REVIEW_SECTION": review_block(plan, policy),
        "LIMITS": limits_block(plan),
    }
    return render_template(template, values)


# ---------------------------------------------------------------------------
# Handoff / referral packets
# ---------------------------------------------------------------------------
def build_handoff(
    facts: str,
    policy: dict,
    profile: dict | None = None,
    questions: list[str] | None = None,
    documents: list[str] | None = None,
    urgency_level: str | None = None,
    recipient: str = "clinician, hotline, or responder",
) -> dict:
    profile = profile or default_profile()
    if not str(facts or "").strip():
        raise CatalogError(
            "empty_facts",
            "No facts were supplied for the handoff summary.",
            "Pass --facts 'what happened' or --input-file path/to/facts.md.",
        )
    urgency = safety.assess_urgency(facts, policy)
    if urgency_level:
        levels = policy.get("_levels") or {}
        if engine.normalize_text(urgency_level).replace(" ", "-") not in levels:
            raise CatalogError(
                "invalid_urgency_level",
                f"Unknown urgency level: {urgency_level}",
                "Use one of: " + ", ".join(levels.keys()),
            )
        chosen = engine.normalize_text(urgency_level).replace(" ", "-")
        urgency = dict(urgency)
        urgency["level"] = chosen
        urgency["rank"] = int((levels[chosen] or {}).get("rank", 0))
        urgency["label"] = (levels[chosen] or {}).get("label", "")
        urgency["required_actions"] = list((levels[chosen] or {}).get("required_actions") or [])

    redacted = safety.redact_pii(facts, policy)
    bullets = _fact_bullets(redacted["text"])
    identity = (profile.get("identity") or {})
    packet = {
        "schema": "humansphere.handoff/1",
        "kind": "handoff",
        "heading": "Facts-only summary - prepared with the user's approval",
        "for": recipient,
        "generated_at": now_utc(),
        "urgent": urgency["rank"] >= 2,
        "urgency_level": urgency["level"],
        "urgency_label": urgency["label"],
        "presenting_facts": bullets,
        "who": {
            "name_used": identity.get("name_used", "") if (profile.get("consent") or {}).get("granted") else "",
            "pronouns": identity.get("pronouns", "") if (profile.get("consent") or {}).get("granted") else "",
            "age_band": identity.get("age_band", "not-stated"),
            "region": (profile.get("context") or {}).get("region", ""),
            "languages": identity.get("languages", []),
        },
        "questions_for_recipient": [q for q in (questions or []) if str(q).strip()][:15]
        or ["What should I watch for next?", "What would make this an emergency?"],
        "documents": [d for d in (documents or []) if str(d).strip()][:15],
        "access_needs": (profile.get("accessibility") or {}).get("needs", []),
        "redactions": redacted["redactions"],
        "limits": [
            "Facts are user-reported and unverified.",
            "No diagnosis, assessment, prediction, or recommendation is made by this tool.",
        ],
        "consent_line": "The user approved sharing this summary. Nothing was sent automatically.",
    }
    return packet


def _fact_bullets(text: str, limit: int = 15) -> list[str]:
    lines = [re.sub(r"^[\s\-*+•·\d.)]+", "", line).strip() for line in str(text).splitlines()]
    bullets = [line for line in lines if line]
    if len(bullets) <= 1:
        single = re.sub(r"\s+", " ", str(text)).strip()
        parts = re.split(r"(?<=[.!?])\s+", single)
        bullets = [part.strip() for part in parts if part.strip()]
    return bullets[:limit]


def render_handoff(packet: dict) -> str:
    who = packet["who"]
    identity = ", ".join(
        f"{label}: {value}"
        for label, value in (
            ("Name used", who["name_used"]),
            ("Pronouns", who["pronouns"]),
            ("Age band", who["age_band"]),
            ("Region", who["region"]),
            ("Languages", ", ".join(who["languages"]) if who["languages"] else ""),
        )
        if value
    ) or "Not shared (no consent or no details supplied)"
    lines = [
        f"# {packet['heading']}",
        "",
        f"- Prepared for: {packet['for']}",
        f"- Prepared (UTC): {packet['generated_at']}",
        f"- Urgency level: {packet['urgency_level']} ({packet['urgency_label']})",
        f"- Urgent: {'yes' if packet['urgent'] else 'no'}",
        "",
        "## Who",
        f"- {identity}",
        "",
        "## What happened (user-reported facts)",
    ]
    lines.extend(f"- {bullet}" for bullet in packet["presenting_facts"])
    lines.extend(["", "## Questions for you"])
    lines.extend(f"- {question}" for question in packet["questions_for_recipient"])
    if packet["documents"]:
        lines.extend(["", "## Documents or records brought"])
        lines.extend(f"- {doc}" for doc in packet["documents"])
    if packet["access_needs"]:
        lines.extend(["", "## Access needs"])
        lines.extend(f"- {need}" for need in packet["access_needs"])
    if packet["redactions"]:
        lines.extend(["", "## Privacy"])
        lines.append(
            "- Identifiers masked before export: "
            + ", ".join(f"{label} x{count}" for label, count in packet["redactions"].items())
        )
    lines.extend(["", "## Limits"])
    lines.extend(f"- {limit}" for limit in packet["limits"])
    lines.extend(["", f"_{packet['consent_line']}_"])
    return "\n".join(lines)


def write_text(path: str | Path, text: str, approve: bool = False) -> dict:
    path = Path(path)
    existed = path.exists()
    if existed and not approve:
        raise SafetyError(
            "approval_required",
            f"Refusing to overwrite existing file without approval: {path}",
            "Re-run with --approve to confirm overwriting, or choose a new --out path.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    engine.atomic_write_text(path, text)
    return {"path": str(path), "bytes": path.stat().st_size, "overwrote": existed}
