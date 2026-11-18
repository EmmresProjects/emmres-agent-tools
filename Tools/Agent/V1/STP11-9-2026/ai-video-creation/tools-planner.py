"""ai-video-creation / tools-planner.py

Real local planning operations: intent routing, dependency-ordered production
plan construction, deterministic prompt compilation, and shot list / EDL
generation from a script or brief.

Pure Python standard library; loaded by the primary entry point. All functions
take explicit arguments so they can be unit-tested from tools-selftest.py.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict

_VAGUE = re.compile(r"\b(nice|cool|good|better|great|awesome|some|maybe|stuff|things|"
                    r" kinda|sort of|whatever|appropriate|interesting|modern|high quality)\b", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return [t for t in _TOKEN.findall(str(text or "").lower()) if len(t) > 2]


def _seed(*parts):
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:8], 16)


# --------------------------------------------------------------------------
# Capability routing
# --------------------------------------------------------------------------

def route(reg, brief, config, explicit_caps=None):
    """Score every catalog capability against the brief and return the best matches.

    Scoring is transparent: name-token overlaps (weight 3), category/purpose
    overlaps (weight 1), family hints (weight 2), and explicit requests (weight 20).
    """
    caps = reg.get("capabilities", [])
    text = " ".join(str(brief.get(k, "")) for k in
                    ("title", "objective", "audience", "style", "keywords", "script", "notes"))
    tokens = set(_tokens(text))
    style = str(brief.get("style", "")).lower()
    explicit = set(str(x).lower() for x in (explicit_caps or []))
    family_hints = {
        "caption": "captioning", "subtitle": "captioning", "translate": "captioning",
        "localize": "captioning", "accessibility": "captioning", "script": "timeline",
        "storyboard": "timeline", "shot": "timeline", "edit": "timeline", "timeline": "timeline",
        "prompt": "prompting", "brief": "prompting", "quality": "assurance", "qa": "assurance",
        "evaluate": "assurance", "audit": "assurance", "deliver": "delivery", "encode": "delivery",
        "publish": "safety", "rights": "safety", "consent": "safety", "plan": "planning",
        "workflow": "planning", "pipeline": "planning", "generate": "generation",
    }
    hinted = {family_hints[t] for t in tokens if t in family_hints}
    if style:
        hinted.update({family_hints[t] for t in _tokens(style) if t in family_hints})
    rows = []
    for cap in caps:
        name_tokens = set(_tokens(cap["name"]))
        cat_tokens = set(_tokens(cap["category"]) + _tokens(cap.get("category_purpose", "")))
        overlap = tokens & name_tokens
        cat_overlap = tokens & cat_tokens
        score = 3 * len(overlap) + len(cat_overlap)
        if cap["family"] in hinted:
            score += 2
        key = cap["name"].lower()
        if key in explicit or cap["slug"] in explicit or cap["id"] in explicit:
            score += 20
        if cap["local_operation"] in ("plan", "compile-prompt") and tokens:
            score += 1  # always-useful entry capabilities
        if score <= 0:
            continue
        reasons = []
        if overlap:
            reasons.append("name matches: %s" % ", ".join(sorted(overlap)[:5]))
        if cat_overlap:
            reasons.append("category matches: %s" % ", ".join(sorted(cat_overlap)[:4]))
        if cap["family"] in hinted:
            reasons.append("intent family '%s'" % cap["family"])
        if key in explicit or cap["slug"] in explicit or cap["id"] in explicit:
            reasons.append("explicitly requested")
        rows.append({"id": cap["id"], "name": cap["name"], "slug": cap["slug"],
                     "category": cap["category"], "family": cap["family"],
                     "local_operation": cap["local_operation"], "score": score,
                     "reasons": reasons, "requires_approval": cap.get("requires_approval", False)})
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    limit = int(config.get("limits", {}).get("max_route_results", 20))
    return rows[:limit], {"scored": len(rows), "intent_tokens": sorted(tokens)[:30], "families_hinted": sorted(hinted)}


# --------------------------------------------------------------------------
# Production plan
# --------------------------------------------------------------------------

def _topological(stages):
    """Return stage ids in dependency order; raise on cycles/unknown deps."""
    by_id = OrderedDict((s["id"], s) for s in stages)
    done, out = set(), []
    while len(out) < len(by_id):
        progressed = False
        for sid, stage in by_id.items():
            if sid in done:
                continue
            deps = stage.get("depends_on", [])
            unknown = [d for d in deps if d not in by_id]
            if unknown:
                raise ValueError("stage %s has unknown dependencies: %s" % (sid, unknown))
            if all(d in done for d in deps):
                out.append(sid)
                done.add(sid)
                progressed = True
        if not progressed:
            raise ValueError("workflow contains a dependency cycle: %s" %
                             [s for s in by_id if s not in done])
    return out


def build_plan(reg, brief, config, policy, route_rows, contracts=None, gates=None):
    """Combine routed capabilities into one unified, dependency-ordered plan."""
    workflow = config.get("workflow", [])
    max_stages = int(config.get("limits", {}).get("max_stages", 64))
    if len(workflow) > max_stages:
        workflow = workflow[:max_stages]
    caps_by_id = {c["id"]: c for c in reg.get("capabilities", [])}
    selected_ids = [r["id"] for r in route_rows if r["id"] in caps_by_id]
    explicit = []
    for name in brief.get("capabilities") or []:
        cap = next((c for c in reg.get("capabilities", [])
                    if str(name).lower() in (c["id"].lower(), c["slug"].lower(), c["name"].lower())), None)
        if cap and cap["id"] not in selected_ids:
            selected_ids.insert(0, cap["id"])
            explicit.append(cap["id"])
    selected = [caps_by_id[i] for i in selected_ids]
    per_stage_cap = max(1, min(8, (len(selected) // max(1, len(workflow))) + 1))
    used = set()
    stages = []
    for wf in workflow:
        ops = set(wf.get("operations", []))
        fams = set(wf.get("families", []))
        pool = [r for r in route_rows if r["id"] in caps_by_id and r["id"] not in used and
                (r["local_operation"] in ops or r["family"] in fams)]
        if not pool:
            # Every workflow stage still performs a real operation, even without a
            # routed capability: it uses its default local operation.
            pool = []
        chosen = pool[:per_stage_cap]
        # Guarantee at least one capability per stage by borrowing from the selected list.
        if not chosen:
            for cap in selected:
                if cap["id"] not in used and (cap["local_operation"] in ops or cap["family"] in fams):
                    chosen = [{"id": cap["id"], "name": cap["name"], "slug": cap["slug"],
                               "category": cap["category"], "family": cap["family"],
                               "local_operation": cap["local_operation"], "score": 0,
                               "reasons": ["selected for workflow stage"], "requires_approval": cap["requires_approval"]}]
                    break
        if not chosen:
            # Explicit fallback capability for the stage's default operation.
            matches = [c for c in reg.get("capabilities", []) if c["local_operation"] == wf.get("default_operation")]
            if matches:
                c = matches[0]
                chosen = [{"id": c["id"], "name": c["name"], "slug": c["slug"],
                           "category": c["category"], "family": c["family"],
                           "local_operation": c["local_operation"], "score": 0,
                           "reasons": ["default capability for stage"], "requires_approval": c["requires_approval"],
                           "fallback": True}]
        for c in chosen:
            used.add(c["id"])
        stage_sensitive = (wf.get("default_operation") in ("contract", "approval", "delivery") or
                           any(c.get("requires_approval") for c in chosen))
        reasons = []
        for c in chosen:
            reasons.extend({"capability": c["name"], "reason": r} for r in
                           (plan_approval_reasons(c, brief, policy, fallback=c.get("fallback", False),
                                                  apply_brief_flags=stage_sensitive)))
        stage = {
            "id": wf["id"],
            "name": wf["name"],
            "depends_on": list(wf.get("depends_on", [])),
            "operation": wf.get("default_operation", "contract"),
            "capabilities": [{k: c[k] for k in ("id", "name", "slug", "category", "family", "local_operation")} for c in chosen],
            "quality_gates": list(wf.get("gates", [])),
            "approval_required": bool(reasons),
            "approval_reasons": reasons,
            "limits": {
                "timeout_seconds": config.get("timeouts", {}).get("stage_seconds"),
                "max_attempts": config.get("retries", {}).get("max_attempts"),
                "retry_on": config.get("retries", {}).get("retry_on", []),
            },
            "fallback": "Preserve earlier stages; write an editable spec and mark provider_required.",
            "status": "planned",
        }
        stages.append(stage)
    order = _topological(stages)
    job_id = hashlib.sha256(("|".join([config.get("config_version", "1.0"),
                                      str(brief.get("title", "")), str(brief.get("objective", "")),
                                      ",".join(selected_ids[:20])])).encode("utf-8")).hexdigest()[:16]
    plan = {
        "schema_version": "1.0",
        "kind": "production-plan",
        "job_id": job_id,
        "umbrella": reg.get("umbrella"),
        "brief": {k: v for k, v in brief.items() if k not in ("script", "captions_text")},
        "provider": brief.get("provider", config.get("providers", {}).get("default", "local")),
        "config_version": config.get("config_version", "1.0"),
        "stage_order": order,
        "stages": stages,
        "selected_capabilities": [c["id"] for c in selected],
        "explicit_capabilities": explicit,
        "routing": {"top": route_rows[:10], "total_matched": len(selected_ids)},
        "quality_gate_ids": (gates or {}).get("default_gate_ids", []),
        "policy": {"approval_required": any(s["approval_required"] for s in stages),
                   "shell": False, "network": bool(config.get("providers", {}).get("allow_external", False)),
                   "allowed_providers": config.get("providers", {}).get("enabled", [])},
        "limits": {"job_seconds": config.get("timeouts", {}).get("job_seconds"),
                   "max_output_bytes": config.get("limits", {}).get("max_output_bytes"),
                   "cancel_file": config.get("output", {}).get("cancel_file_name", "CANCEL")},
        "unresolved_issues": [],
        "warnings": [],
    }
    if plan["provider"] not in plan["policy"]["allowed_providers"]:
        plan["warnings"].append("Provider '%s' is not enabled; execution will require a configured adapter." % plan["provider"])
        plan["unresolved_issues"].append("Configure an adapter for provider '%s' before generative execution." % plan["provider"])
    if not selected:
        plan["warnings"].append("No capabilities matched the brief; the plan uses workflow defaults only.")
    return plan


def build_single_capability_plan(reg, brief, config, policy, capability, gates, provider=None):
    """Build a valid one-stage plan so ANY catalog capability can be run directly."""
    import hashlib
    cap = capability
    reasons = plan_approval_reasons(cap, brief, policy, fallback=False, apply_brief_flags=True)
    job_id = hashlib.sha256(("single|%s|%s|%s" % (cap["id"], brief.get("title", ""),
                                                  config.get("config_version", "1.0"))).encode("utf-8")).hexdigest()[:16]
    stage = {
        "id": "capability",
        "name": cap["name"],
        "depends_on": [],
        "operation": cap["local_operation"],
        "capabilities": [{k: cap.get(k) for k in ("id", "name", "slug", "category", "family", "local_operation")}],
        "quality_gates": list(cap.get("quality_gates", [])),
        "approval_required": bool(reasons),
        "approval_reasons": [{"capability": cap["name"], "reason": r} for r in reasons],
        "limits": {"timeout_seconds": config.get("timeouts", {}).get("stage_seconds"),
                   "max_attempts": config.get("retries", {}).get("max_attempts"),
                   "retry_on": config.get("retries", {}).get("retry_on", [])},
        "fallback": "Write an editable spec and mark provider_required.",
        "status": "planned",
    }
    return {
        "schema_version": "1.0", "kind": "production-plan", "job_id": job_id,
        "umbrella": reg.get("umbrella"), "brief": brief,
        "provider": provider or brief.get("provider", config.get("providers", {}).get("default", "local")),
        "config_version": config.get("config_version", "1.0"),
        "stage_order": ["capability"], "stages": [stage],
        "selected_capabilities": [cap["id"]], "explicit_capabilities": [cap["id"]],
        "routing": {"top": [], "total_matched": 1, "mode": "single-capability"},
        "quality_gate_ids": (gates or {}).get("default_gate_ids", []),
        "policy": {"approval_required": bool(reasons), "shell": False,
                   "network": bool(config.get("providers", {}).get("allow_external", False)),
                   "allowed_providers": config.get("providers", {}).get("enabled", [])},
        "limits": {"job_seconds": config.get("timeouts", {}).get("job_seconds"),
                   "max_output_bytes": config.get("limits", {}).get("max_output_bytes"),
                   "cancel_file": config.get("output", {}).get("cancel_file_name", "CANCEL")},
        "warnings": [], "unresolved_issues": [],
    }


def plan_approval_reasons(capability, brief, policy, fallback=False, apply_brief_flags=True):
    """Plan-time approval reasons (the authoritative check lives in tools-contracts.py).

    Workflow defaults are not real requests from the operator, so they never
    trigger approval on their own; brief flags still do.
    """
    out = []
    if not fallback:
        for kw in policy.get("approval_required", {}).get("capability_keywords", []):
            if kw.lower() in ("%s %s" % (capability.get("name", ""), capability.get("category", ""))).lower():
                out.append("capability keyword '%s'" % kw)
                break
        if capability.get("requires_approval") and not out:
            out.append("catalog-marked sensitive capability")
    if apply_brief_flags:
        for flag in policy.get("approval_required", {}).get("brief_flags", []):
            if brief.get(flag):
                out.append("brief flag '%s'" % flag)
    return out


# --------------------------------------------------------------------------
# Prompt compilation
# --------------------------------------------------------------------------

def compile_prompt(brief, style_presets, config, shot=None, variants=1):
    """Deterministically build editable positive/negative prompts with constraints."""
    preset_name = brief.get("style_preset") or "cinematic"
    preset = style_presets.get("presets", {}).get(preset_name) or style_presets.get("presets", {}).get("cinematic", {})
    max_variants = int(config.get("limits", {}).get("max_prompt_variants", 12))
    variants = max(1, min(int(variants), max_variants))
    subject = shot.get("description") if shot else brief.get("objective", "")
    title = brief.get("title", "Untitled brief")
    bits = [
        str(subject).strip(),
        "Subject context: %s" % title,
        "Style: %s" % (brief.get("style") or preset.get("look", "clean, coherent imagery")),
        "Camera: %s" % (brief.get("camera") or preset.get("camera", "locked-off medium shot")),
        "Motion: %s" % (brief.get("motion") or preset.get("motion", "subtle, physically plausible motion")),
        "Lighting: %s" % preset.get("lighting", "soft motivated light"),
        "Palette: %s" % preset.get("palette", "natural balanced color"),
    ]
    if brief.get("audience"):
        bits.append("Audience: %s" % brief["audience"])
    if brief.get("locale"):
        bits.append("Locale: %s" % brief["locale"])
    constraints = list(brief.get("constraints") or [])
    constraints += [
        "aspect ratio %s" % brief.get("aspect_ratio", "16:9"),
        "resolution %s" % brief.get("resolution", "1920x1080"),
        "%s fps" % brief.get("fps", 24),
        "%s seconds" % brief.get("duration_seconds", 8),
    ]
    for i, c in enumerate(constraints):
        bits.append("Constraint %d: %s" % (i + 1, c))
    negative = list(preset.get("negative", []))
    for c in brief.get("constraints") or []:
        if str(c).lower().startswith(("avoid", "no ", "without", "exclude")):
            negative.append(str(c))
    if brief.get("negative_prompt"):
        negative.extend(brief["negative_prompt"] if isinstance(brief["negative_prompt"], list)
                        else [str(brief["negative_prompt"])])
    seed = brief.get("seed") if isinstance(brief.get("seed"), int) else _seed(title, subject, preset_name)
    ambiguous = sorted(set(m.group(0).lower() for m in _VAGUE.finditer(" ".join(bits))))
    out = []
    for v in range(variants):
        camera = preset.get("camera_variants", [preset.get("camera", "locked-off shot")])[v % max(1, len(preset.get("camera_variants", [1])))]
        vb = list(bits)
        if variants > 1:
            vb[3] = "Camera: %s" % camera
        positive = ". ".join(x.rstrip(".") for x in vb if x) + "."
        out.append({
            "variant": v + 1,
            "positive_prompt": positive,
            "negative_prompt": "; ".join(dict.fromkeys(negative)),
            "seed": seed + v,
            "style_preset": preset_name,
            "shot_id": shot.get("id") if shot else None,
            "constraints": constraints,
            "assumptions": [
                "No generative model is bundled; this prompt is an editable input for a configured provider.",
                "Unspecified physical details are left to the provider and must be reviewed.",
            ],
        })
    return {
        "kind": "prompt-set",
        "title": title,
        "variants": out,
        "ambiguity_audit": {
            "vague_terms": ambiguous,
            "status": "needs-human-review" if ambiguous else "clear",
            "suggestion": "Replace vague terms with measurable visual details." if ambiguous else None,
        },
        "quality_gates": ["prompt-source-fidelity", "technical-validity", "reproducibility"],
        "dependencies": ["style presets", "brief", "optional shot list"],
        "unresolved_issues": ["Generative rendering requires a configured provider."],
    }


def prompt_markdown(prompt_set):
    lines = ["# Prompt set: %s" % prompt_set["title"], ""]
    for v in prompt_set["variants"]:
        lines += ["## Variant %d (seed %s)" % (v["variant"], v["seed"]), "",
                  "**Positive**", "", v["positive_prompt"], "",
                  "**Negative**", "", v["negative_prompt"] or "(none)", "",
                  "**Constraints**", ""]
        lines += ["- %s" % c for c in v["constraints"]]
        lines += ["", "**Assumptions**", ""] + ["- %s" % a for a in v["assumptions"]] + [""]
    a = prompt_set["ambiguity_audit"]
    lines += ["## Ambiguity audit", "", "- Status: %s" % a["status"],
              "- Vague terms: %s" % (", ".join(a["vague_terms"]) or "none"), ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Shot list / EDL
# --------------------------------------------------------------------------

def _timecode(seconds, fps):
    total_frames = int(round(seconds * fps))
    h, rem = divmod(total_frames, int(round(fps)) * 3600)
    m, rem = divmod(rem, int(round(fps)) * 60)
    s, f = divmod(rem, int(round(fps)))
    return "%02d:%02d:%02d:%02d" % (h, m, s, f)


def shotlist(brief, style_presets, config, script_text=None):
    """Turn a script/brief into a bounded shot list with timecodes and an EDL."""
    preset_name = brief.get("style_preset") or "cinematic"
    preset = style_presets.get("presets", {}).get(preset_name, style_presets.get("presets", {}).get("cinematic", {}))
    fps = float(brief.get("fps", 24))
    total = float(brief.get("duration_seconds", 24))
    max_shots = int(config.get("limits", {}).get("max_shots", 240))
    raw = script_text if script_text is not None else brief.get("script", "")
    lines = [re.sub(r"^\s*(?:\d+[.)]\s*|[•\-*]\s*)", "", l).strip()
             for l in str(raw or "").splitlines()]
    lines = [l for l in lines if l and not re.fullmatch(r"Page\s+\d+", l, re.I)]
    if not lines:
        lines = ["%s: establishing shot" % brief.get("title", "Opening"),
                 "%s: main subject action" % brief.get("objective", "Main action"),
                 "%s: closing detail" % brief.get("title", "Closing")]
    scene = "Scene 1"
    scenes = []
    for line in lines:
        m = re.match(r"^(scene\s+\d+|int\.|ext\.|act\s+\d+)\s*[:\-]?\s*(.*)$", line, re.I)
        if m:
            scene = m.group(1)[:80]
            if not m.group(2).strip():
                continue  # pure scene header; the following lines are its shots
            scenes.append((scene, m.group(2).strip()))
            continue
        scenes.append((scene, line))
    scenes = scenes[:max_shots]
    n = max(1, len(scenes))
    per = total / n
    cameras = preset.get("camera_variants", [preset.get("camera", "medium shot")])
    motions = preset.get("motion_variants", [preset.get("motion", "subtle motion")])
    shots, cursor = [], 0.0
    for i, (sc, desc) in enumerate(scenes, 1):
        start = cursor
        end = total if i == n else round(cursor + per, 3)
        shots.append({
            "id": "S%03d" % i,
            "scene": sc,
            "description": desc[:400],
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(end - start, 3),
            "in_timecode": _timecode(start, fps),
            "out_timecode": _timecode(end, fps),
            "camera": cameras[(i - 1) % len(cameras)],
            "motion": motions[(i - 1) % len(motions)],
            "transition": "cut" if i < n else "fade-out",
            "source": "to-generate",
        })
        cursor = end
    edl = ["TITLE: %s" % brief.get("title", "Untitled"), "FCM: NON-DROP FRAME", ""]
    for i, s in enumerate(shots, 1):
        edl.append("%03d  GEN       V     C        %s %s %s %s" %
                   (i, s["in_timecode"], s["out_timecode"], s["in_timecode"], s["out_timecode"]))
        edl.append("* FROM CLIP NAME: %s" % s["id"])
        edl.append("")
    return {
        "kind": "shot-list",
        "title": brief.get("title", "Untitled"),
        "fps": fps,
        "total_duration_seconds": total,
        "shot_count": len(shots),
        "shots": shots,
        "edl": "\n".join(edl),
        "dependencies": ["brief", "style preset"],
        "quality_gates": ["temporal-coherence", "prompt-source-fidelity", "technical-validity", "handoff-readiness"],
        "unresolved_issues": ["Shots are planning artifacts; media generation needs a configured provider."],
    }


def shotlist_markdown(shot_list):
    lines = ["# Shot list: %s" % shot_list["title"], "",
             "Duration: %s s | Frames per second: %s | Shots: %d" %
             (shot_list["total_duration_seconds"], shot_list["fps"], shot_list["shot_count"]), "",
             "| Shot | Scene | In | Out | Camera | Motion | Description |",
             "|---|---|---|---|---|---|---|"]
    for s in shot_list["shots"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" %
                     (s["id"], s["scene"], s["in_timecode"], s["out_timecode"],
                      s["camera"], s["motion"], s["description"].replace("|", "\\|")[:100]))
    lines += ["", "## Edit decision list", "", "```", shot_list["edl"], "```", ""]
    return "\n".join(lines)
