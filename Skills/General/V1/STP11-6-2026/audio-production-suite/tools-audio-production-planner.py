#!/usr/bin/env python3
"""tools-audio-production-planner.py - turns a brief into an executable production plan.

Produces, for any of the 280 catalogued capabilities or any whole category:
  * a routing table (capability -> owner operation -> expected artifact)
  * a stage list, control list, and objective check list from the category playbook
  * deliverables and loudness/format targets from the chosen delivery preset
  * assumptions, risks, approval gates, and copy-ready next commands
  * a per-capability contract sheet (inputs, outputs, controls, checks, recovery)

Run directly for its own help: python3 tools-audio-production-planner.py --help
"""

from __future__ import annotations

import datetime
import os
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
REGISTRY_FILENAME = "tools-capability-registry.py"
PLAN_TEMPLATE = "asset-plan-template.md"
PRESETS_ASSET = "asset-delivery-presets.json"
QA_POLICY_ASSET = "asset-qa-policy.json"
APPROVAL_POLICY_ASSET = "asset-approval-policy.json"

MEDIUM_TO_PRESET = {
    "streaming-music": "streaming-music",
    "music": "streaming-music",
    "album": "streaming-music",
    "club": "club-playback",
    "dj": "club-playback",
    "podcast": "podcast-speech",
    "audiobook": "audiobook",
    "broadcast": "broadcast-r128",
    "radio": "broadcast-r128",
    "cinema": "cinema-feature",
    "film": "film-score-stems",
    "tv": "broadcast-r128",
    "game": "game-interactive",
    "sfx": "sfx-library",
    "sound-design": "sfx-library",
    "field-recording": "archival-master",
    "archive": "archival-master",
    "archival": "archival-master",
}


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

    common = load("aps_audio_common", COMMON_FILENAME)
    registry = load("aps_capability_registry", REGISTRY_FILENAME)
    return common, registry


common, registry = _load()


def normalize_brief(data=None, **overrides):
    """Validate and fill a production brief. Unknown keys are rejected, not ignored."""
    brief = {
        "title": "Untitled production",
        "goal": "",
        "audience": "",
        "medium": "music",
        "preset": None,
        "duration_seconds": None,
        "deadline": "",
        "rights_status": "unknown",
        "constraints": [],
        "capabilities": [],
        "notes": "",
    }
    if data:
        if not isinstance(data, dict):
            raise common.ToolError("invalid_brief", "The brief file must contain a JSON object")
        unknown = [key for key in data if key not in brief]
        if unknown:
            raise common.ToolError("invalid_brief", "Unknown brief fields: %s" % ", ".join(sorted(unknown)),
                                   {"allowed": sorted(brief.keys())})
        brief.update({k: v for k, v in data.items() if v is not None})
    for key, value in overrides.items():
        if value is not None:
            if key not in brief:
                raise common.ToolError("invalid_brief", "Unknown brief field %r" % key)
            brief[key] = value
    if isinstance(brief["constraints"], str):
        brief["constraints"] = [c.strip() for c in brief["constraints"].split(";") if c.strip()]
    if isinstance(brief["capabilities"], str):
        brief["capabilities"] = [c.strip() for c in brief["capabilities"].split(",") if c.strip()]
    if not brief["goal"]:
        raise common.ToolError("invalid_brief", "The brief needs a goal",
                               hint="Pass --goal \"deliver a 45 second trailer cue\" or set goal in the JSON file.")
    presets = common.load_asset(PRESETS_ASSET)
    if brief["preset"] is None:
        medium = str(brief["medium"]).strip().lower()
        preset_id = MEDIUM_TO_PRESET.get(medium, medium)
        if preset_id not in presets["presets"]:
            raise common.ToolError("unknown_preset",
                                   "No delivery preset for medium %r" % brief["medium"],
                                   {"available_presets": sorted(presets["presets"].keys())})
        brief["preset"] = preset_id
    elif brief["preset"] not in presets["presets"]:
        raise common.ToolError("unknown_preset", "Unknown preset %r" % brief["preset"],
                               {"available_presets": sorted(presets["presets"].keys())})
    if brief["duration_seconds"] is not None:
        try:
            brief["duration_seconds"] = float(brief["duration_seconds"])
        except (TypeError, ValueError):
            raise common.ToolError("invalid_brief", "duration_seconds must be a number")
        if brief["duration_seconds"] <= 0 or brief["duration_seconds"] > common.LIMITS["hard_max_seconds"]:
            raise common.ToolError("invalid_brief",
                                   "duration_seconds must be between 0 and %s" % common.LIMITS["hard_max_seconds"])
    if str(brief["rights_status"]).lower() not in ("unknown", "cleared", "pending", "restricted", "public-domain",
                                                   "licensed", "generated"):
        raise common.ToolError("invalid_brief",
                               "rights_status must be one of unknown, cleared, pending, restricted, "
                               "public-domain, licensed, generated")
    return brief


def _select_capabilities(brief):
    if brief["capabilities"]:
        chosen = []
        for entry in brief["capabilities"]:
            designator = registry.resolve(entry)
            if designator["type"] == "category":
                chosen.extend(designator["skills"])
            else:
                chosen.append(designator["skill"])
        deduped = []
        seen = set()
        for skill in chosen:
            if skill["id"] not in seen:
                seen.add(skill["id"])
                deduped.append(skill)
        return deduped, "explicit"
    text = " ".join([brief["goal"], brief["audience"], brief["notes"], brief["medium"]])
    matches = registry.search(text, limit=10, min_score=1.0)
    if matches:
        best = matches[0]["score"]
        matches = [match for match in matches if match["score"] >= max(1.0, best * 0.45)][:8]
    skills = [registry.get(match["id"])["skill"] for match in matches]
    return skills, "routed from the brief text"


def _unique(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_plan(brief, catalog_path=None, playbook_path=None):
    catalog, playbooks = registry.load(catalog_path, playbook_path)
    presets = common.load_asset(PRESETS_ASSET)
    policy = common.load_asset(QA_POLICY_ASSET)
    approvals = common.load_asset(APPROVAL_POLICY_ASSET)
    preset = presets["presets"][brief["preset"]]
    skills, selection_mode = _select_capabilities(brief)
    if not skills:
        raise common.ToolError("no_capability",
                               "No capability in the catalog matches this brief",
                               hint="Pass --capability with a name or id such as C18-S01.")
    categories = []
    for skill in skills:
        if skill["category_slug"] not in categories:
            categories.append(skill["category_slug"])
    stages = []
    controls = []
    checks = []
    deliverables = []
    approval_gates = []
    limits = []
    metrics = []
    for slug in categories:
        playbook = playbooks["playbooks"][slug]
        stages.extend(playbook["stages"])
        controls.extend(playbook["controls"])
        checks.extend(playbook["checks"])
        deliverables.extend(playbook["deliverables"])
        approval_gates.extend(playbook["approval_gates"])
        limits.extend(playbook["limits"])
        metrics.extend(playbook["metrics"])
    deliverables = _unique(deliverables + preset["deliverables"])
    approval_gates = _unique(approval_gates + ["Publishing or distributing the approved master"])
    limits = _unique(limits + ["Measurement-based checks only; creative judgement needs a listening pass"])

    assumptions = []
    if not brief["audience"]:
        assumptions.append("Audience was not stated; plan assumes a general listening audience.")
    if brief["duration_seconds"] is None:
        assumptions.append("Duration was not stated; plan assumes section lengths may flex to the brief.")
    assumptions.append("Delivery targets follow the %s preset (%s)." % (preset["label"], brief["preset"]))
    if str(brief["rights_status"]).lower() in ("unknown", "pending"):
        assumptions.append("Rights status is %s: no publishing step may run until clearance is recorded."
                           % brief["rights_status"])
    assumptions.append("Source material is available locally as PCM WAV; other containers need conversion first.")
    assumptions.append("Selection of capabilities was %s." % selection_mode)

    approval_required = []
    for action in approvals["actions"]:
        if action["id"] in ("destructive-overwrite", "publish-deliverable", "voice-likeness",
                            "rights-signoff", "bulk-batch"):
            approval_required.append({
                "action": action["id"],
                "label": action["label"],
                "risk": action["risk"],
                "when": action["when"],
                "approver": action["approver"],
            })

    command_prefix = "python3 tools-audio-suite.py"
    next_commands = [
        "%s capabilities --query \"%s\" --limit 8" % (command_prefix, brief["goal"].split(".")[0][:40] or "audio"),
        "%s spec --capability %s --preset %s" % (command_prefix, skills[0]["id"], brief["preset"]),
        "%s plan --brief brief.json --out plan.md --format markdown" % command_prefix,
        "%s process --input source.wav --output staged.wav --recipe %s"
        % (command_prefix, preset.get("default_recipe", "streaming-safe-normalize")),
        "%s analyze --input staged.wav --preset %s --out qa.json" % (command_prefix, brief["preset"]),
        "%s manifest --input delivery --preset %s --out delivery/manifest.json"
        % (command_prefix, brief["preset"]),
        "%s verify-manifest --manifest delivery/manifest.json" % command_prefix,
    ]

    plan = {
        "plan_version": "1.0.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "title": brief["title"],
        "brief": brief,
        "preset": {"id": brief["preset"], "label": preset["label"], "targets": preset["targets"],
                   "format": preset["format"], "deliverables": preset["deliverables"],
                   "default_recipe": preset.get("default_recipe")},
        "capabilities": [{
            "id": skill["id"], "name": skill["name"], "category": skill["category_name"],
            "category_slug": skill["category_slug"], "description": skill["description"],
            "operations": skill["operations"],
        } for skill in skills],
        "capability_count": len(skills),
        "category_slugs": categories,
        "stages": stages,
        "controls": _unique(controls),
        "checks": _unique(checks),
        "deliverables": deliverables,
        "metrics": _unique(metrics),
        "limits": limits,
        "approval_gates": approval_gates,
        "approvals_required": approval_required,
        "qa_policy": {"policy_version": policy.get("policy_version"),
                      "gates": [g["id"] for g in policy.get("gates", [])]},
        "assumptions": assumptions,
        "risks": _risks(brief, skills),
        "next_commands": next_commands,
    }
    plan["markdown"] = render_plan_markdown(plan)
    return plan


def _risks(brief, skills):
    risks = []
    rights = str(brief["rights_status"]).lower()
    if rights in ("unknown", "pending"):
        risks.append({"risk": "Rights not cleared", "impact": "release blocked", "mitigation":
                      "Record clearance in the rights register before any publishing step."})
    if rights == "restricted":
        risks.append({"risk": "Restricted source rights", "impact": "limited permitted uses",
                      "mitigation": "Confirm permitted territories and edits with the rights holder."})
    if any(skill["category_slug"].startswith("voice-speech") for skill in skills) or \
            any("voice" in skill["name"].lower() for skill in skills):
        risks.append({"risk": "Voice likeness or consent gap", "impact": "legal and ethical exposure",
                      "mitigation": "Obtain written consent and record permitted uses before generation."})
    if brief["duration_seconds"] and brief["duration_seconds"] > 3600:
        risks.append({"risk": "Long-form measurement cost", "impact": "slow analysis passes",
                      "mitigation": "Analyse in segments or raise --max-seconds deliberately."})
    if not brief["deadline"]:
        risks.append({"risk": "No deadline recorded", "impact": "review gates may slip",
                      "mitigation": "Set dates for each stage before starting production."})
    if not risks:
        risks.append({"risk": "None material identified", "impact": "monitor", "mitigation":
                      "Re-run the plan when the brief changes."})
    return risks


def render_plan_markdown(plan):
    template_path = os.path.join(common.SKILL_ROOT, PLAN_TEMPLATE)
    if not os.path.isfile(template_path):
        raise common.ToolError("missing_asset", "Plan template not found: %s" % PLAN_TEMPLATE)
    with open(template_path, "r", encoding="utf-8") as handle:
        template = handle.read()
    brief = plan["brief"]
    preset = plan["preset"]

    def bullets(items):
        return "\n".join("- %s" % item for item in items) if items else "- None"

    capability_rows = "\n".join("| %s | %s | %s | %s |"
                                % (skill["id"], skill["name"], skill["category"],
                                   ", ".join(skill["operations"]))
                                for skill in plan["capabilities"])
    stage_rows = "\n".join("%d. %s" % (index, stage) for index, stage in enumerate(plan["stages"], 1))
    risk_rows = "\n".join("| %s | %s | %s |" % (r["risk"], r["impact"], r["mitigation"]) for r in plan["risks"])
    approval_rows = "\n".join("| %s | %s | %s | %s |"
                              % (a["action"], a["label"], a["when"], a["approver"])
                              for a in plan["approvals_required"])
    target_rows = "\n".join("- %s: %s" % (key, value) for key, value in preset["targets"].items())
    format_rows = "\n".join("- %s: %s" % (key, value) for key, value in preset["format"].items())
    command_block = "\n".join(plan["next_commands"])

    replacements = {
        "{{TITLE}}": plan["title"],
        "{{GENERATED_AT}}": plan["generated_at"],
        "{{GOAL}}": brief["goal"],
        "{{AUDIENCE}}": brief["audience"] or "not stated",
        "{{MEDIUM}}": str(brief["medium"]),
        "{{PRESET_LABEL}}": preset["label"],
        "{{PRESET_ID}}": plan["preset"]["id"],
        "{{DURATION}}": ("%s seconds" % brief["duration_seconds"]) if brief["duration_seconds"] else "not stated",
        "{{DEADLINE}}": brief["deadline"] or "not stated",
        "{{RIGHTS}}": str(brief["rights_status"]),
        "{{CAPABILITY_COUNT}}": str(plan["capability_count"]),
        "{{CAPABILITY_ROWS}}": capability_rows,
        "{{STAGES}}": stage_rows,
        "{{CONTROLS}}": bullets(plan["controls"]),
        "{{CHECKS}}": bullets(plan["checks"]),
        "{{DELIVERABLES}}": bullets(plan["deliverables"]),
        "{{METRICS}}": bullets(plan["metrics"]),
        "{{TARGETS}}": target_rows,
        "{{FORMAT}}": format_rows,
        "{{ASSUMPTIONS}}": bullets(plan["assumptions"]),
        "{{RISKS}}": risk_rows,
        "{{APPROVALS}}": approval_rows,
        "{{LIMITS}}": bullets(plan["limits"]),
        "{{CONSTRAINTS}}": bullets(brief["constraints"]) if brief["constraints"] else "- None stated",
        "{{COMMANDS}}": command_block,
    }
    text = template
    for key, value in replacements.items():
        text = text.replace(key, value)
    leftover = [key for key in replacements if key in text]
    if leftover:
        raise common.ToolError("template_error", "Unreplaced placeholders remain: %s" % ", ".join(leftover))
    return text


def build_spec(identifier, preset_id="streaming-music", brief=None, catalog_path=None,
               playbook_path=None):
    """Contract sheet for one capability (or a whole category)."""
    catalog, playbooks = registry.load(catalog_path, playbook_path)
    presets = common.load_asset(PRESETS_ASSET)
    policy = common.load_asset(QA_POLICY_ASSET)
    approvals = common.load_asset(APPROVAL_POLICY_ASSET)
    if preset_id not in presets["presets"]:
        raise common.ToolError("unknown_preset", "Unknown preset %r" % preset_id,
                               {"available_presets": sorted(presets["presets"].keys())})
    preset = presets["presets"][preset_id]
    designator = registry.resolve(identifier)
    skills = [designator["skill"]] if designator["type"] == "skill" else designator["skills"]
    category_slugs = _unique([s["category_slug"] for s in skills])
    playbook_data = [playbooks["playbooks"][slug] for slug in category_slugs]
    spec = {
        "spec_version": "1.0.0",
        "capability": skills[0]["name"] if designator["type"] == "skill" else designator["category"]["name"],
        "capability_ids": [s["id"] for s in skills],
        "category": skills[0]["category_name"],
        "preset": {"id": preset_id, "label": preset["label"], "targets": preset["targets"],
                   "format": preset["format"]},
        "contract": {
            "inputs": _unique(["Goal, audience, and intended listening context",
                               "Source audio or session assets",
                               "Duration, tempo, key, or timing requirements as applicable",
                               "Rights and consent status",
                               "Delivery specification and deadline"]),
            "outputs": _unique(preset["deliverables"] +
                               [item for p in playbook_data for item in p["deliverables"]]),
            "controls": _unique([item for p in playbook_data for item in p["controls"]]),
            "checks": _unique([item for p in playbook_data for item in p["checks"]] +
                              [g.get("label", g.get("id", "gate")) for g in policy.get("gates", [])]),
            "recovery": ["Identify the earliest failing layer and preserve approved work",
                         "Repair only the responsible component, then re-run the linked checks",
                         "Re-approve any deliverable whose measurements changed",
                         "Record the recovery action in the delivery manifest notes"],
        },
        "stages": _unique([item for p in playbook_data for item in p["stages"]]),
        "limits": _unique([item for p in playbook_data for item in p["limits"]]),
        "approval_gates": _unique([item for p in playbook_data for item in p["approval_gates"]] +
                                  [a["when"] for a in approvals["actions"]
                                   if a["id"] in ("destructive-overwrite", "publish-deliverable")]),
        "operations": registry.operations(skills[0]["id"] if designator["type"] == "skill"
                                          else category_slugs[0]),
        "commands": [
            "python3 tools-audio-suite.py plan --goal \"%s\" --preset %s --out plan.md --format markdown"
            % (_safe(("Produce: " + skills[0]["name"]), 60), preset_id),
            "python3 tools-audio-suite.py process --input source.wav --output staged.wav --recipe %s"
            % preset.get("default_recipe", "streaming-safe-normalize"),
            "python3 tools-audio-suite.py analyze --input staged.wav --preset %s --out qa.json" % preset_id,
            "python3 tools-audio-suite.py manifest --input delivery --preset %s --out delivery/manifest.json"
            % preset_id,
        ],
    }
    if brief:
        spec["brief"] = brief
    spec["markdown"] = render_spec_markdown(spec)
    return spec


def _safe(text, limit):
    cleaned = " ".join(str(text).replace('"', "'").split())
    return cleaned[:limit]


def render_spec_markdown(spec):
    lines = ["# Capability contract: %s" % spec["capability"], ""]
    lines.append("- Category: %s" % spec["category"])
    lines.append("- Capability ids: %s" % ", ".join(spec["capability_ids"]))
    lines.append("- Delivery preset: %s (`%s`)" % (spec["preset"]["label"], spec["preset"]["id"]))
    lines.append("- Toolkit operations: %s" % ", ".join(spec["operations"]))
    lines.append("")
    lines.append("## Contract")
    for section in ("inputs", "outputs", "controls", "checks", "recovery"):
        lines.append("")
        lines.append("### %s" % section.capitalize())
        for item in spec["contract"][section]:
            lines.append("- %s" % item)
    lines.append("")
    lines.append("## Stages")
    for index, stage in enumerate(spec["stages"], 1):
        lines.append("%d. %s" % (index, stage))
    lines.append("")
    lines.append("## Targets")
    for key, value in spec["preset"]["targets"].items():
        lines.append("- %s: %s" % (key, value))
    lines.append("")
    lines.append("## Limits and approval gates")
    for item in spec["limits"]:
        lines.append("- Limit: %s" % item)
    for item in spec["approval_gates"]:
        lines.append("- Approval gate: %s" % item)
    lines.append("")
    lines.append("## Commands")
    for command in spec["commands"]:
        lines.append("```\n%s\n```" % command)
    return "\n".join(lines) + "\n"


def route(requirements, limit=3):
    """Map free-text requirements to capabilities, flagging anything unmatched."""
    if isinstance(requirements, str):
        requirements = [r.strip() for r in requirements.split("\n") if r.strip()]
    if not requirements:
        raise common.ToolError("invalid_argument", "At least one requirement line is required")
    assignments = []
    unmatched = []
    for requirement in requirements:
        matches = registry.search(requirement, limit=limit, min_score=1.2)
        if not matches:
            unmatched.append(requirement)
            continue
        assignments.append({
            "requirement": requirement,
            "primary": {"id": matches[0]["id"], "name": matches[0]["name"], "category": matches[0]["category"]},
            "alternatives": [{"id": m["id"], "name": m["name"]} for m in matches[1:]],
            "score": matches[0]["score"],
        })
    return {"assignments": assignments, "unmatched": unmatched,
            "coverage": common.r2(1.0 - (len(unmatched) / float(len(requirements))), 3)}


def main(argv=None):
    print("""tools-audio-production-planner.py - brief to plan, contract sheet, and routing (library + help).

Functions: normalize_brief(), build_plan(), build_spec(), render_plan_markdown(),
render_spec_markdown(), route()

Command line use goes through the suite entry point:
  python3 tools-audio-suite.py plan --goal "podcast episode master" --medium podcast --out plan.md --format markdown
  python3 tools-audio-suite.py spec --capability C18-S03 --preset podcast-speech
  python3 tools-audio-suite.py route --requirements requirements.txt
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
