#!/usr/bin/env python3
"""Planning intelligence for the video-production-orchestrator Skill Tool.

Module role: everything that reasons *about* a production without touching media
bytes. Imported by the CLI and by the self-test so both exercise identical logic.

Responsibilities
  * catalog          - load / filter / search the 250-capability dataset
  * resolve          - map a plain-text request onto real catalog capabilities
  * plan             - build a validated production plan (deliverables, phases,
                       gates, risks, per-capability task briefs)
  * coverage         - report requirement coverage of a plan
  * digest           - expand a master config into a delivery matrix
  * variant          - generate meaningfully different creative variants
  * manifest         - build/verify a delivery manifest with checksums
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

from importlib.machinery import SourceFileLoader

_HERE = Path(__file__).resolve().parent
_src = SourceFileLoader("video_core", str(_HERE / "tools-video-core.py")).load_module()
core = _src
_input = SourceFileLoader("video_input", str(_HERE / "tools-video-input.py")).load_module()

CATALOG_FILE = "asset-catalog-data.json"
PLAN_SCHEMA = "asset-production-plan.schema.json"
MASTER_SCHEMA = "asset-master-config.schema.json"
POLICY_FILE = "asset-approval-policy.json"

PHASE_NAMES = {
    1: "Reliable foundation",
    2: "Professional finishing",
    3: "Advanced creation",
    4: "Scale and safety",
    5: "Autonomous production",
}

PLATFORM_ALIASES = {
    "tiktok": "tiktok-vertical", "reels": "reels-vertical", "shorts": "shorts-vertical",
    "youtube shorts": "shorts-vertical", "instagram": "instagram-square",
    "youtube": "youtube-1080p", "linkedin": "linkedin-1080p", "broadcast": "broadcast-1080i",
    "tv": "broadcast-1080i", "cinema": "cinema-2k", "web": "youtube-1080p",
}


# ------------------------------------------------------------------- catalog
def catalog_path(path=None):
    if path:
        return core.ensure_file(path, "catalog data")
    local = _HERE / CATALOG_FILE
    if not local.exists():
        raise core.not_found(f"catalog data not found beside the tool: {local}",
                             "Pass --catalog with the path to asset-catalog-data.json.")
    return local


def load_catalog(path=None):
    data = core.read_json(catalog_path(path), "catalog data")
    for key in ("catalog", "categories", "skills"):
        if key not in data:
            raise core.validation_error(f"catalog data is missing '{key}'")
    index = {s["id"]: s for s in data["skills"]}
    return data, index


def search_capabilities(term, catalog=None, index=None, limit=20):
    """Token-overlap search over name + description + keywords."""
    data = catalog or load_catalog()[0]
    tokens = [t for t in re.split(r"[^a-z0-9]+", term.lower()) if len(t) > 2]
    if not tokens:
        raise core.usage_error("search term must contain at least one word of 3+ characters")
    scored = []
    for skill in data["skills"]:
        blob = (skill["name"] + " " + skill["description"] + " " + " ".join(skill.get("keywords", []))).lower()
        name_low = skill["name"].lower()
        score = 0.0
        for token in tokens:
            if token in name_low:
                score += 3.0
            elif token in blob:
                score += 1.0
        if score:
            scored.append((score, skill))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    results = [{"id": s["id"], "name": s["name"], "category": _category_of(data, s)["category"],
                "family": s["family"], "phase": s["phase"], "score": round(sc, 2),
                "description": s["description"],
                "requires_approval": s["requires_approval"]} for sc, s in scored[:limit]]
    return {"query": term, "match_count": len(scored), "returned": len(results), "results": results}


def _category_of(data, skill):
    cid = skill["category_id"]
    for cat in data["categories"]:
        if cat["id"] == cid:
            return cat
    return {"id": cid, "category": "Uncategorised", "phase": skill.get("phase", 1)}


# ------------------------------------------------------------------- resolve
DEFAULT_REQUIREMENTS = [
    ("brief", r"\b(brief|concept|creative|direction|treatment|pitch|promo|advert\w*|campaign)\b"),
    ("script", r"\b(script\w*|narrat\w*|voiceover|vo|copy|explain\w*|story\w*|dialogu\w*\s+scene)\b"),
    ("plan", r"\b(storyboard\w*|shot\s*list|board\w*|coverage|schedul\w*|previs\w*|shots?)\b"),
    ("edit", r"\b(edit\w*|cut\w*|assembl\w*|timeline|sequenc\w*|montage)\b"),
    ("captions", r"\b(caption\w*|subtitle\w*|srt|accessib\w*|transcri\w*)\b"),
    ("audio", r"\b(audio|music\w*|mix\w*|loudness|sound\w*|sfx|foley|dialogu\w*|voice\w*|narration\s+mix)\b"),
    ("grade", r"\b(colou?r\w*|grade\w*|grading|lut|look\s*dev)\b"),
    ("master", r"\b(export\w*|master\w*|encod\w*|deliver\w*|render\w*|publish\w*|upload)\b"),
    ("qa", r"\b(qa|review\w*|check\w*|verif\w*|quality)\b"),
    ("vfx", r"\b(remove\w*|removal|clean\s*-?up|wire\w*|boom|rig|vfx|composit\w*|rotoscop\w*|key\w*|matte\w*|artifact\w*)\b"),
    ("motion", r"\b(transition\w*|animat\w*|motion|stabil\w*|speed\s*ramp|slow\s*motion|loop)\b"),
    ("graphics", r"\b(graphic\w*|lower\s*third\w*|kinetic|infographic\w*|chart\w*|logo|icon\w*|title\s*card)\b"),
    ("localize", r"\b(translat\w*|localis\w*|localiz\w*|dub\w*|audio\s*description|sign\s*language)\b"),
    ("version", r"\b(version\w*|refram\w*|cutdown|cut\s*down|thumbnail|9:16|1:1|16:9)\b"),
]


# Every production needs direction and verification, so these are baseline
# requirements even when the request never says the words.
BASELINE_REQUIREMENTS = ("brief", "qa")


def infer_requirements(request_text):
    blob = (request_text or "").lower()
    found = [name for name, pattern in DEFAULT_REQUIREMENTS if re.search(pattern, blob)]
    for base in BASELINE_REQUIREMENTS:
        if base not in found:
            found.insert(0 if base == "brief" else len(found), base)
    order = [name for name, _ in DEFAULT_REQUIREMENTS]
    return [r for r in order if r in found]


def resolve(request_text, catalog=None, max_capabilities=60, requested=None):
    """Map a request onto real catalog capabilities.

    Returns the parsed request plus selected capabilities with a rationale so the
    plan is auditable and no capability appears without a reason.
    """
    data, index = load_catalog() if catalog is None else (catalog[0], catalog[1])
    parsed = _input.normalise_request(request_text)
    requirements = infer_requirements(request_text)
    explicit = [c.strip() for c in (requested or []) if c and c.strip()]

    selected = []
    seen = set()

    def add(skill, reason):
        if skill["id"] in seen:
            return False
        seen.add(skill["id"])
        selected.append({**skill, "reason": reason})
        return True

    # 1. explicit ids / slugs / names
    for token in explicit:
        skill = _lookup(token, data, index)
        if skill is None:
            raise core.not_found(
                f"capability not found: {token}",
                hint="Use 'search' to find a capability id, or pass a catalog id such as C05-E01.",
            )
        add(skill, f"explicitly requested ({token})")

    # 2. capability names discovered in the request text
    for parsed_skill in parsed["skills"]:
        skill = _lookup(parsed_skill["name"], data, index, fuzzy=True)
        if skill:
            add(skill, f"named in request input ('{parsed_skill['name']}')")

    # 3. requirement-driven baseline
    req_family = {"brief": "planning", "script": "writing", "plan": "board", "edit": "edit",
                  "captions": "text", "audio": "audio", "grade": "color",
                  "master": "master", "qa": "qa", "vfx": "vfx", "motion": "motion",
                  "graphics": "graphics", "localize": "localize", "version": "version"}
    buckets = []
    for req in requirements:
        family = req_family.get(req)
        if not family:
            continue
        buckets.append((req, family, [s for s in data["skills"] if s["family"] == family]))
    # Round-robin across requirement families: coverage beats depth when the
    # capability budget is tight.
    depth = 0
    while len(selected) < max_capabilities and any(depth < len(b[2]) for b in buckets):
        progressed = False
        for req, family, candidates in buckets:
            if depth >= len(candidates) or len(selected) >= max_capabilities:
                continue
            add(candidates[depth], f"covers requirement '{req}'")
            progressed = True
        if not progressed:
            break
        depth += 1

    if not selected:
        raise core.validation_error(
            "no catalog capability matched the request",
            hint="Describe the work (edit, captions, grade, VFX...) or pass --capability C05-E01.",
        )

    if len(selected) > max_capabilities:
        dropped = selected[max_capabilities:]
        selected = selected[:max_capabilities]
        core_hint = f"{len(dropped)} capability(ies) omitted by --max-capabilities"
    else:
        core_hint = None

    # Ordering: catalog order keeps phases coherent.
    selected.sort(key=lambda s: s["id"])
    umbrella = parsed["umbrella_name"]
    if parsed["category_count"] == 0 or not any(c["name"] for c in parsed["categories"]):
        umbrella = _family_umbrella(selected)
    return {
        "request": request_text,
        "umbrella_name": umbrella,
        "parsed": parsed,
        "requirements": requirements,
        "capabilities": selected,
        "truncation_note": core_hint,
    }


def _family_umbrella(selected):
    """Short hyphenated umbrella name when the request supplied no category.

    Broad productions (4+ distinct families) are named for the whole workflow;
    narrower ones are named for their two dominant families.
    """
    counts = {}
    for skill in selected:
        counts[skill["family"]] = counts.get(skill["family"], 0) + 1
    ranked = [fam for fam, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    if not ranked:
        return "video-production"
    if len(ranked) >= 4:
        return "video-production"
    slug = "-".join(ranked[:2]) if len(ranked) > 1 else ranked[0]
    return re.sub(r"-{2,}", "-", slug).strip("-") or "video-production"


def _lookup(token, data, index, fuzzy=False):
    token = str(token).strip()
    upper = token.upper()
    if upper in index:
        return index[upper]
    slug = token.lower().replace(" ", "-")
    for skill in data["skills"]:
        if skill["slug"] == slug:
            return skill
    lowered = token.lower()
    exact = [s for s in data["skills"] if s["name"].lower() == lowered]
    if exact:
        return exact[0]
    if fuzzy:
        partial = [s for s in data["skills"]
                   if lowered in s["name"].lower() or s["name"].lower() in lowered]
        if partial:
            return sorted(partial, key=lambda s: len(s["name"]))[0]
    return None


# ---------------------------------------------------------------------- plan
def build_plan(resolved, catalog_data, goal=None, audience=None, platform=None,
               duration_seconds=None, outputs=None, source_label=None, plan_id=None):
    """Turn a resolved request into a schema-valid production plan."""
    caps = resolved["capabilities"]
    umbrella = resolved["umbrella_name"]
    plan_id = plan_id or core.safe_stem(umbrella + "-plan")
    phases = sorted({c["phase"] for c in caps})
    goal_text = goal or _derive_goal(resolved["request"], caps)

    deliverables = []
    if outputs:
        for i, item in enumerate(outputs, 1):
            path = item.get("path") if isinstance(item, dict) else str(item)
            kind = item.get("kind") if isinstance(item, dict) else _guess_kind(path)
            if kind not in {"editable-source", "review-preview", "final-master", "package", "documentation"}:
                raise core.validation_error(
                    f"unknown deliverable kind {kind!r} for {path}",
                    hint="Use editable-source, review-preview, final-master, package or documentation.")
            deliverables.append({"id": f"D{i:02d}", "kind": kind,
                                 "status": "planned", "path": path})
    if not deliverables:
        deliverables = [
            {"id": "D01", "kind": "editable-source", "status": "planned", "path": "edit/"},
            {"id": "D02", "kind": "review-preview", "status": "planned", "path": "review/preview.mp4"},
            {"id": "D03", "kind": "final-master", "status": "planned", "path": "masters/master.mp4"},
            {"id": "D04", "kind": "package", "status": "planned", "path": "package/"},
            {"id": "D05", "kind": "documentation", "status": "planned", "path": "delivery-manifest.json"},
        ]

    gates = [
        {"id": "G01", "check": "schema", "blocking": True, "status": "open",
         "evidence": "tools-video-orchestrator.py validate"},
        {"id": "G02", "check": "technical", "blocking": True, "status": "open",
         "evidence": "probe every master for container/codec/fps/audio layout"},
        {"id": "G03", "check": "qa", "blocking": True, "status": "open",
         "evidence": "black/freeze/silence/loudness scan"},
        {"id": "G04", "check": "rights", "blocking": True, "status": "open",
         "evidence": "rights checklist per asset before picture lock"},
        {"id": "G05", "check": "approval", "blocking": True, "status": "open",
         "evidence": "human sign-off recorded in approval-record.json"},
    ]
    if any(c["family"] in ("text", "localize") for c in caps):
        gates.append({"id": "G06", "check": "captions", "blocking": False, "status": "open",
                      "evidence": "caption readability + overlap scan"})
    if any(c["family"] in ("audio", "master") for c in caps):
        gates.append({"id": "G07", "check": "loudness", "blocking": False, "status": "open",
                      "evidence": "integrated LUFS and true-peak targets"})
    if any(c["family"] in ("edit", "board", "workvideo") for c in caps):
        gates.append({"id": "G08", "check": "sync", "blocking": False, "status": "open",
                      "evidence": "audio/video drift check at head, middle and tail"})

    risks = _risks(caps, resolved, platform)

    plan = {
        "plan_id": plan_id,
        "umbrella_name": umbrella,
        "goal": goal_text,
        "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "input": source_label or "inline-request",
            "format": resolved["parsed"]["source_format"] if resolved["parsed"]["skill_count"] else "catalog",
            "category_count": resolved["parsed"]["category_count"],
            "skill_count": resolved["parsed"]["skill_count"],
        },
        "deliverables": deliverables,
        "capabilities": [
            {
                "id": c["id"], "name": c["name"],
                "category": _category_of(catalog_data, c)["category"],
                "family": c["family"], "phase": c["phase"], "status": "pending",
                "requires_approval": c["requires_approval"],
                "outputs": _outputs_for(c, catalog_data),
                "notes": c["reason"],
            }
            for c in caps
        ],
        "gates": gates,
        "risks": risks,
    }
    if audience:
        plan["audience"] = audience
    if platform:
        plan["platform"] = platform
    if duration_seconds:
        plan["duration_seconds"] = float(duration_seconds)

    errors = core.validate_schema(plan, core.read_json(_HERE / PLAN_SCHEMA, "plan schema"))
    if errors:
        raise core.validation_error("generated plan is invalid (internal error)",
                                    details={"errors": errors[:40]})
    return plan


def _derive_goal(request, caps):
    clean = re.sub(r"\s+", " ", request or "").strip()
    if len(clean) > 400:
        clean = clean[:397] + "..."
    families = sorted({c["family"] for c in caps})
    if clean:
        return clean
    return "Deliver a video production using " + ", ".join(families)


def _guess_kind(path):
    low = str(path).lower()
    if low.endswith((".mp4", ".mov", ".mxf")) or "master" in low:
        return "final-master"
    if "preview" in low or "review" in low:
        return "review-preview"
    if "package" in low or low.endswith("/"):
        return "package"
    return "documentation"


def _outputs_for(skill, catalog_data):
    cat = _category_of(catalog_data, skill)
    defaults = cat.get("default_artifacts") or []
    base = skill["slug"]
    outputs = [f"{base}-{item}" for item in defaults[:3]]
    return outputs or [f"{base}-notes.md"]


def _risks(caps, resolved, platform):
    risks = []
    families = {c["family"] for c in caps}
    if "generative" in families:
        risks.append({"code": "generative_disclosure", "severity": "warn",
                      "message": "generated media needs disclosure and provenance records",
                      "mitigation": "run provenance capability and label synthetic shots"})
    if "vfx" in families or "composite" in families:
        risks.append({"code": "vfx_integration", "severity": "warn",
                      "message": "VFX shots carry schedule and integration risk",
                      "mitigation": "previz first, then integration review before picture lock"})
    if "audio" in families:
        risks.append({"code": "loudness_target", "severity": "info",
                      "message": "delivery loudness target must be declared per platform",
                      "mitigation": "set loudness_target_lufs in the master config"})
    if not any(c["family"] == "qa" for c in caps):
        risks.append({"code": "no_qa_capability", "severity": "warn",
                      "message": "plan contains no quality-assurance capability",
                      "mitigation": "add C23 QA capabilities or run 'qa' on every master"})
    if any(c["requires_approval"] for c in caps):
        names = [c["name"] for c in caps if c["requires_approval"]]
        risks.append({"code": "approval_required", "severity": "warn",
                      "message": f"{len(names)} capability(ies) require human approval before execution",
                      "mitigation": "use --approve-plan then --approve <token>"})
    if platform and platform not in ("", "unspecified") and not re.search(r"[a-z]", platform):
        risks.append({"code": "platform_unclear", "severity": "warn",
                      "message": f"platform '{platform}' has no delivery preset",
                      "mitigation": "use a preset name such as youtube-1080p or reels-vertical"})
    if resolved.get("truncation_note"):
        risks.append({"code": "capability_truncated", "severity": "warn",
                      "message": resolved["truncation_note"],
                      "mitigation": "raise --max-capabilities if more work is genuinely needed"})
    return risks


# ------------------------------------------------------------------- coverage
def coverage_report(plan, catalog_data=None, request_text=None):
    """Report which requirements the plan actually covers and what is missing."""
    catalog_data = catalog_data or load_catalog()[0]
    families = {c["family"] for c in plan["capabilities"]}
    req_map = {"brief": {"planning"}, "script": {"writing"}, "plan": {"board"},
               "edit": {"edit"}, "captions": {"text", "localize"}, "audio": {"audio"},
               "grade": {"color"}, "master": {"master"}, "qa": {"qa"},
               "vfx": {"vfx", "composite"}, "motion": {"motion"}, "graphics": {"graphics"},
               "localize": {"localize"}, "version": {"version"}}
    # Infer from the explicit request when given, otherwise from the plan goal
    # (which stores the original request text).
    reqs = infer_requirements(request_text if request_text is not None else plan.get("goal", ""))
    rows = []
    for req in reqs:
        needed = req_map.get(req, set())
        covered = bool(needed & families)
        rows.append({"requirement": req, "covered": covered,
                     "covered_by": sorted(needed & families) or [],
                     "capabilities": [c["id"] for c in plan["capabilities"] if c["family"] in needed]})
    blocking_risks = [r for r in plan.get("risks", []) if r["severity"] == "fail"]
    gaps = [r["requirement"] for r in rows if not r["covered"]]
    status = "fail" if blocking_risks else ("warn" if gaps else "pass")
    return {
        "plan_id": plan["plan_id"],
        "status": status,
        "requirements": rows,
        "uncovered": gaps,
        "phase_spread": sorted({c["phase"] for c in plan["capabilities"]}),
        "approval_required": [c["name"] for c in plan["capabilities"] if c.get("requires_approval")],
        "blocking_risks": blocking_risks,
    }


# --------------------------------------------------------------------- briefs
FAMILY_PLAYBOOK = {
    "planning": ["State goal, audience, platform, runtime and emotional target.",
                 "Name the single controlling idea and the visual system that carries it.",
                 "List approvals needed before any irreversible work."],
    "writing": ["Separate verified facts from interpretation; flag claims needing attribution.",
                "Write to the runtime budget at ~2.4 words/second for narration.",
                "Map each line to a planned visual so no line is unsupported."],
    "board": ["Give every shot an id, duration, framing, movement and audio note.",
              "Check screen direction and eyelines across adjacent shots.",
              "Confirm coverage supports the edit before locking the shot list."],
    "ingest": ["Copy, never move, originals; verify checksums twice on separate targets.",
               "Record timecode, reel, camera and rights metadata at ingest.",
               "Generate proxies with relink metadata preserving source timecode."],
    "edit": ["Assemble for story clarity first, then timing, then polish.",
             "Maintain an edit decision list so every cut is traceable to a source range.",
             "Hold picture lock before colour, VFX turnovers and final mix."],
    "motion": ["Motivate every transition by a story or motion cue.",
               "Keep motion energy inside the accessible flash and speed envelope.",
               "Verify continuity of direction, attention and audio across the cut."],
    "text": ["Limit captions to 2 lines and <= 21 characters per second.",
             "Respect platform safe areas and keep contrast >= 4.5:1 for body text.",
             "Check names, numbers, capitalisation and localisation overflow."],
    "graphics": ["Animate data without changing its meaning; label axes and sources.",
                 "Keep one visual system: type scale, palette, motion curve.",
                 "Export templates with named, editable controls."],
    "composite": ["Match black level, grain, blur, lens and light direction to the plate.",
                  "Track and stabilise before attaching graphics.",
                  "Check edge quality at 100% and in motion."],
    "vfx": ["Break each effect into plates, elements, tracking, simulation and cleanup.",
            "Integrate shadow, contact, occlusion and light wrap.",
            "Review at delivery resolution, not proxy resolution."],
    "generative": ["Write shot-level prompts with subject, action, camera, light and style.",
                   "Keep identity and scene continuity references per shot.",
                   "Record model, prompt, seed and permissions for every generated clip."],
    "animation": ["Block timing and weight before detail.",
                  "Match frame rate, grain and blur when integrating with live action.",
                  "Check lip sync and secondary motion frame by frame."],
    "color": ["Balance exposure and white balance before any look.",
              "Match shots to neighbours and to the hero reference.",
              "Declare working space, display transform and legal ranges."],
    "audio": ["Repair before you process; process with restraint.",
              "Keep dialogue intelligible above music and effects.",
              "Hit the platform loudness and true-peak target on the full mix."],
    "image": ["Prepare stills at 1.5x the largest on-screen size.",
              "Keep transparent cutouts edge-clean and unpremultiplied as required.",
              "Track rights and attribution per image."],
    "repair": ["Preserve the original; work on verified copies.",
               "Repair only the responsible layer and re-run linked checks.",
               "Declare what was repaired versus invented."],
    "genre": ["Match the genre contract: hook timing, structure, length, ending.",
              "Respect platform and legal requirements for the format.",
              "Deliver the version set the format implies."],
    "workvideo": ["Keep UI legible: magnify regions instead of scaling whole frames.",
                  "Remove dead time and mistakes without losing context.",
                  "Publish chapters, transcript and captions with the master."],
    "localize": ["Lock picture before localisation to avoid re-timing churn.",
                 "Validate subtitle files for timing, reading speed and overlap.",
                 "Check accessibility for flashes, motion and cognitive load."],
    "version": ["Generate versions from one approved master configuration.",
                "Protect faces, captions and CTAs inside per-platform safe areas.",
                "Confirm message, colour, audio and disclosures survive every version."],
    "master": ["Choose codec and container from the delivery target, not habit.",
               "Verify resolution, fps, colour, audio layout, timecode and metadata.",
               "Test playback on representative players and devices."],
    "qa": ["Scan for black frames, freezes, silence, clipping and loudness drift.",
           "Check continuity, facts, labels, names and numbers against sources.",
           "Record sign-off on the exact version that will be delivered."],
    "pipeline": ["Declare project structure, naming and versioning before work starts.",
                 "Resolve missing media, fonts and effects before render queues.",
                 "Keep rollback points and verified archives."],
    "autonomous": ["Expose every assumption the automation makes.",
                   "Route each requirement to the cheapest capable method.",
                   "Preserve links from generated output back to source segments."],
}


def task_brief(plan, capability_id, catalog_data=None):
    """Emit an execution-ready brief for one capability in the plan."""
    catalog_data = catalog_data or load_catalog()[0]
    entry = next((c for c in plan["capabilities"] if c["id"].lower() == capability_id.lower()
                  or c["name"].lower() == capability_id.lower()), None)
    if entry is None:
        raise core.not_found(f"capability {capability_id!r} is not part of plan {plan['plan_id']}",
                             hint="Run 'coverage' to list the capabilities in this plan.")
    skill = next((s for s in catalog_data["skills"] if s["id"] == entry["id"]), None)
    gates = [g for g in plan["gates"] if g["blocking"]]
    return {
        "plan_id": plan["plan_id"],
        "capability": {"id": entry["id"], "name": entry["name"], "category": entry["category"],
                       "description": skill["description"] if skill else "",
                       "phase": entry["phase"], "phase_name": PHASE_NAMES.get(entry["phase"], "")},
        "why": entry.get("notes", ""),
        "inputs": _brief_inputs(entry, plan),
        "procedure": FAMILY_PLAYBOOK.get(entry["family"], ["Execute the capability description faithfully."]),
        "outputs": entry.get("outputs", []),
        "checks": [g["check"] for g in gates] + _family_checks(entry["family"]),
        "approval_required": entry.get("requires_approval", False),
        "done_when": _done_when(entry, plan),
        "limits": {"timeout_seconds": 300, "retries": 1,
                   "on_failure": "record the failing layer, keep passing work, repair, rerun checks"},
    }


def _brief_inputs(entry, plan):
    inputs = {"goal": plan["goal"], "deliverables": [d["path"] for d in plan["deliverables"]]}
    if plan.get("audience"):
        inputs["audience"] = plan["audience"]
    if plan.get("platform"):
        inputs["platform"] = plan["platform"]
    if plan.get("duration_seconds"):
        inputs["duration_seconds"] = plan["duration_seconds"]
    return inputs


def _family_checks(family):
    return {
        "planning": ["audience_platform_fit", "approval_path"],
        "writing": ["claim_verification", "runtime_budget"],
        "board": ["screen_direction", "coverage_completeness"],
        "ingest": ["checksum_match", "originals_untouched"],
        "edit": ["continuity", "pacing", "edl_traceability"],
        "motion": ["motion_continuity", "photosensitivity"],
        "text": ["caption_readability", "safe_area", "contrast"],
        "graphics": ["data_fidelity", "visual_system_consistency"],
        "composite": ["edge_quality", "colour_match"],
        "vfx": ["integration_review", "scale_perspective"],
        "generative": ["identity_continuity", "provenance_recorded", "disclosure"],
        "animation": ["weight_contact", "lip_sync"],
        "color": ["shot_match", "colour_pipeline_declared"],
        "audio": ["loudness_target", "true_peak", "dialogue_intelligibility"],
        "image": ["resolution_adequate", "rights_tracked"],
        "repair": ["original_preserved", "authenticity_log"],
        "genre": ["format_contract", "legal_requirements"],
        "workvideo": ["ui_legibility", "privacy_redaction"],
        "localize": ["subtitle_validation", "accessibility_review"],
        "version": ["safe_area", "cross_version_consistency"],
        "master": ["technical_validity", "playback_compatibility"],
        "qa": ["black_freeze_silence", "continuity_accuracy", "signoff_recorded"],
        "pipeline": ["dependency_resolution", "rollback_available"],
        "autonomous": ["assumptions_exposed", "source_links_preserved"],
    }.get(family, ["capability_output_present"])


def _done_when(entry, plan):
    return [
        f"outputs exist and are listed under deliverable paths: {', '.join(d.get('path','') for d in plan['deliverables'][:3])}",
        f"checks pass: {', '.join(_family_checks(entry['family']))}",
        "notes recorded in the plan entry so downstream capabilities can continue",
    ]


# ------------------------------------------------------------------- variant
VARIANT_LENSES = [
    ("retention-hook", "Lead with the single most surprising concrete fact; cut setup entirely.",
     "fast", "high contrast, 1 idea per 2 seconds"),
    ("proof-led", "Open on the strongest evidence or demonstration, then explain it.",
     "medium", "screen/evidence first, talking head second"),
    ("story-led", "Open on a person in a specific moment with a stake, then widen.",
     "medium", "handheld-adjacent framing, natural sound up"),
    ("contrarian", "Open by naming the common assumption and immediately complicating it.",
     "fast", "typographic disruption, bold cut rhythm"),
    ("demonstration-first", "Show the finished result in the first 3 seconds, then rewind to method.",
     "medium", "before/after split, annotated close-ups"),
    ("quiet-authority", "Low-music, restrained, evidence-forward; trust over novelty.",
     "slow", "long lens, locked frame, generous negative space"),
]


def explore_variants(count=3, base=None, seed=None):
    """Deterministically select distinct creative variants (no duplicate lenses)."""
    count = max(1, min(int(count), len(VARIANT_LENSES)))
    order = list(VARIANT_LENSES)
    if seed is not None:
        # Deterministic Fisher-Yates: the same seed always yields the same
        # selection, and different seeds usually yield a different one.
        for i in range(len(order) - 1, 0, -1):
            digest = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
            j = int(digest[:12], 16) % (i + 1)
            order[i], order[j] = order[j], order[i]
    picks = order[:count]
    variants = []
    for i, (name, hook, pacing, visual) in enumerate(picks, 1):
        variants.append({
            "variant_id": f"V{i:02d}",
            "name": name,
            "hook": hook,
            "pacing": pacing,
            "visual_direction": visual,
            "base": base or "unnamed-production",
            "deltas": {
                "structure": "hook -> proof -> method" if pacing == "fast" else "hook -> context -> proof",
                "sound": "no music under first 5s" if name == "quiet-authority" else "music drives cut rhythm",
                "length_delta_seconds": -10 if pacing == "fast" else (10 if pacing == "slow" else 0),
            },
        })
    return {"base": base or "unnamed-production", "count": len(variants), "variants": variants}


# ------------------------------------------------------------------ manifest
def build_manifest(entries, root=None, title=None, include_checksums=True):
    """Create a delivery manifest for real files with sha256 checksums."""
    rows = []
    problems = []
    for entry in entries:
        path = Path(entry["path"])
        if root and not path.is_absolute():
            path = Path(root) / path
        if not path.exists():
            problems.append(core.finding("fail", "missing_file", f"missing deliverable: {entry['path']}"))
            continue
        row = {
            "path": str(path).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "role": entry.get("role", "asset"),
            "version": entry.get("version", "v1"),
        }
        if include_checksums and path.is_file():
            row["sha256"] = core.sha256_file(path)
        if entry.get("caption_for"):
            row["caption_for"] = entry["caption_for"]
        rows.append(row)
    manifest = {
        "title": title or "delivery-package",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "file_count": len(rows),
        "total_bytes": sum(r["bytes"] for r in rows),
        "files": rows,
        "problems": problems,
        "summary": core.summarise_findings(problems),
    }
    return manifest


def verify_manifest(manifest, root=None):
    """Re-verify a manifest against the filesystem."""
    findings = []
    for row in manifest.get("files", []):
        path = Path(row["path"])
        if root and not path.is_absolute():
            path = Path(root) / path
        if not path.exists():
            findings.append(core.finding("fail", "missing_file", f"missing from package: {row['path']}"))
            continue
        size = path.stat().st_size
        if size != row.get("bytes"):
            findings.append(core.finding("fail", "size_mismatch",
                                         f"{row['path']}: {size} bytes, manifest says {row.get('bytes')}"))
        if row.get("sha256") and core.sha256_file(path) != row["sha256"]:
            findings.append(core.finding("fail", "checksum_mismatch",
                                         f"{row['path']}: sha256 differs from manifest"))
    return {"file_count": len(manifest.get("files", [])), "findings": findings,
            "summary": core.summarise_findings(findings)}


# ------------------------------------------------------------- plan mutation
def update_status(plan, capability_id, status, note=None):
    if status not in {"pending", "in-progress", "done", "blocked", "skipped"}:
        raise core.validation_error(f"unknown status {status!r}",
                                    hint="Use pending, in-progress, done, blocked or skipped.")
    target = None
    for entry in plan["capabilities"]:
        if entry["id"].lower() == capability_id.lower() or entry["name"].lower() == capability_id.lower():
            target = entry
            break
    if target is None:
        raise core.not_found(f"capability {capability_id!r} is not in plan {plan['plan_id']}")
    if status == "done" and target.get("requires_approval"):
        raise core.gate_error(
            f"'{target['name']}' requires approval before it can be marked done",
            hint="Record approval with 'approve' first, then set the status.")
    previous = target["status"]
    target["status"] = status
    if note:
        target["notes"] = (target.get("notes", "") + f" | {note}").strip(" |")
    return {"plan_id": plan["plan_id"], "capability_id": target["id"], "previous": previous,
            "status": status, "remaining_pending": sum(1 for c in plan["capabilities"] if c["status"] == "pending")}
