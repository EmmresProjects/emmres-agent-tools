#!/usr/bin/env python3
"""Catalog resolution and plan generation for the Media Intelligence Workbench.

Given matched catalog entries, this module derives an executable, evidence-first
work plan: analysis units, concrete measurement steps, the local tool that
performs each step, evidence requirements, gates, confidence policy and the
artifact contract produced downstream.

Loaded by tools-miw.py. Also usable directly for inspection:

    python3 tools-plan.py --catalog asset-capability-catalog.json --list-categories
    python3 tools-plan.py --catalog asset-capability-catalog.json --search "splice audio"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# Category profiles: what each capability family actually needs in practice.
# Each profile is functional guidance consumed by the planner, not prose.
# --------------------------------------------------------------------------

CATEGORY_PROFILES = {
    "cat-01": {
        "inputs": ["image", "video", "audio", "archive", "subtitle", "document"],
        "steps": [
            "detect container from magic bytes and record the decoder requirement",
            "screen ingestion safety (pixel bomb, decompression ratio, embedded payload, trailing archive)",
            "hash originals (sha256) and mark them read-only",
            "route each file to an analysis-ready bucket and record unsupported formats explicitly",
        ],
        "evidence": ["file_path", "byte_offset", "sha256", "technical_probe"],
        "outputs": ["ingestion_manifest", "technical_probe", "safety_report"],
    },
    "cat-02": {
        "inputs": ["image"],
        "steps": [
            "describe only what is visible; label every inference",
            "ground each object claim to a bounding box in absolute and relative coordinates",
            "report counts with ranges when occlusion prevents exact counts",
            "attach a confidence value per claim and abstain when the category is ambiguous",
        ],
        "evidence": ["region", "confidence", "raw_pixels_reference"],
        "outputs": ["scene_observations", "region_grounding", "object_table"],
    },
    "cat-03": {
        "inputs": ["video"],
        "steps": [
            "probe duration, streams and frame cadence before sampling",
            "sample frames on a fixed cadence and measure frame-to-frame difference",
            "cut the timeline at detected scene boundaries and label chapters",
            "build a chronological event list with start/end timecodes and uncertainty",
        ],
        "evidence": ["timecode", "frame_index", "scene_scores"],
        "outputs": ["scene_segments", "event_timeline", "chapter_plan"],
    },
    "cat-04": {
        "inputs": ["audio", "video"],
        "steps": [
            "decode PCM and measure peak, RMS, clipping, DC offset and crest factor",
            "map silence, speech-like activity and noise floor regions with timecodes",
            "detect narrowband hum and channel balance/phase faults",
            "emit transcript-ready segments with confidence placeholders for a speech model",
        ],
        "evidence": ["timecode", "sample_offset", "level_measurement"],
        "outputs": ["level_report", "silence_map", "segment_manifest"],
    },
    "cat-05": {
        "inputs": ["audio"],
        "steps": [
            "build the onset envelope and estimate tempo with autocorrelation confidence",
            "measure band energy shares and spectral centroid to describe texture",
            "segment the track on energy and spectral change and label sections",
            "state explicitly when rhythm or harmony evidence is insufficient",
        ],
        "evidence": ["timecode", "tempo_confidence", "spectral_profile"],
        "outputs": ["structure_map", "tempo_report", "texture_profile"],
    },
    "cat-06": {
        "inputs": ["image", "video", "subtitle"],
        "steps": [
            "extract text only from regions that pass a legibility check",
            "preserve reading order, position and confidence per text run",
            "keep original spelling and mark uncertain characters instead of guessing",
            "align extracted text to the frame or caption timecode it belongs to",
        ],
        "evidence": ["region", "timecode", "confidence"],
        "outputs": ["text_runs", "caption_cues", "text_timeline"],
    },
    "cat-07": {
        "inputs": ["video", "audio"],
        "steps": [
            "record observable cues only (gesture, pause, overlap, volume shift)",
            "separate observation from interpretation in every row of the output",
            "map interaction patterns to timecodes with participant turn indexes",
            "refuse identity, intent, honesty, emotion-state or trait conclusions",
        ],
        "evidence": ["timecode", "turn_index", "observation_vs_inference"],
        "outputs": ["interaction_timeline", "cue_table", "review_questions"],
    },
    "cat-08": {
        "inputs": ["image", "video"],
        "steps": [
            "estimate camera motion and subject motion separately",
            "track paths and quantify direction, speed trend and stops in pixel space",
            "state monocular depth and scale assumptions explicitly",
            "measure dimensions only against a supplied scale reference, with tolerance",
        ],
        "evidence": ["timecode", "region", "calibration_reference"],
        "outputs": ["motion_report", "trajectory_table", "measurement_notes"],
    },
    "cat-09": {
        "inputs": ["image", "video", "audio"],
        "steps": [
            "measure sharpness, noise, exposure, contrast, cast, banding and blocking",
            "measure audio level, clipping, hum, channel balance and spectral balance",
            "compare against the delivery specification supplied by the caller",
            "recommend the smallest fix that resolves the measured defect",
        ],
        "evidence": ["metric_value", "threshold_source", "timecode"],
        "outputs": ["quality_report", "defect_list", "conformance_diff"],
    },
    "cat-10": {
        "inputs": ["video", "audio", "image"],
        "steps": [
            "measure reading speed, cue duration and line length against published limits",
            "measure luminance-transition flash risk over the real frame cadence",
            "check contrast of text and overlays against WCAG ratios",
            "list remediation items with priority and human-review needs",
        ],
        "evidence": ["timecode", "measured_value", "criterion_reference"],
        "outputs": ["accessibility_report", "remediation_plan"],
    },
    "cat-11": {
        "inputs": ["video", "image", "audio"],
        "steps": [
            "write a non-destructive recipe (operation, parameters, target path)",
            "verify the source is hashed and preserved before any transform",
            "require explicit approval before writing derived media",
            "compare source and derived output before declaring success",
        ],
        "evidence": ["recipe_id", "parameter_record", "source_sha256"],
        "outputs": ["transformation_recipe", "comparison_report", "rollback_notes"],
    },
    "cat-12": {
        "inputs": ["image", "video", "audio", "subtitle"],
        "steps": [
            "index each asset with content-derived features plus available metadata",
            "rank candidates by combined similarity and metadata relevance",
            "return the exact timestamp, frame or region that supports each hit",
            "report recall limitations and the index build state with every answer",
        ],
        "evidence": ["timecode", "region", "similarity_score", "index_version"],
        "outputs": ["search_index_manifest", "ranked_results", "answer_citations"],
    },
    "cat-13": {
        "inputs": ["video", "audio", "image", "subtitle"],
        "steps": [
            "collect measured observations and grounded spans from upstream units",
            "separate observation, interpretation and recommendation sections",
            "attach an evidence table row to every claim",
            "state scope, limits and unanswered questions in the report itself",
        ],
        "evidence": ["claim", "source_file", "timecode_or_region", "confidence"],
        "outputs": ["summary", "evidence_table", "analyst_report"],
    },
    "cat-14": {
        "inputs": ["video", "audio", "subtitle", "log"],
        "steps": [
            "estimate per-source offset and drift, then apply piecewise correction",
            "preserve the original timestamps for audit next to corrected ones",
            "report residual alignment error after correction",
            "surface modality disagreements instead of silently averaging them",
        ],
        "evidence": ["source_file", "original_timestamp", "corrected_timestamp", "residual_error"],
        "outputs": ["sync_report", "fused_timeline", "conflict_list"],
    },
    "cat-15": {
        "inputs": ["image", "video", "audio", "subtitle"],
        "steps": [
            "inventory identifiers (faces, voices, plates, screens, names, locations)",
            "define redaction scope and persistence across motion and occlusion",
            "verify removal on re-decoded output rather than trusting the edit",
            "record consent, retention and distribution constraints with the deliverable",
        ],
        "evidence": ["region", "timecode", "redaction_state", "consent_status"],
        "outputs": ["redaction_plan", "privacy_report", "release_decision"],
    },
    "cat-16": {
        "inputs": ["image", "video", "audio"],
        "steps": [
            "hash originals and work only on read-only copies",
            "inventory metadata, container anomalies and edit-history indicators",
            "measure compression-aware signals and report them as indicators, not verdicts",
            "argue alternative explanations and state the tool's detection limits",
        ],
        "evidence": ["sha256", "byte_offset", "indicator", "alternative_explanation"],
        "outputs": ["provenance_report", "indicator_table", "review_memo"],
    },
    "cat-17": {
        "inputs": ["video", "image", "audio", "log"],
        "steps": [
            "detect configured events with explicit thresholds and timecodes",
            "require corroborating cues before any alert is treated as confirmed",
            "escalate to the responsible human channel; never act autonomously",
            "track false-alarm rate so the detector's reliability is visible",
        ],
        "evidence": ["timecode", "region", "threshold", "corroboration"],
        "outputs": ["event_clips", "alert_log", "reliability_notes"],
    },
    "cat-18": {
        "inputs": ["image", "video", "audio"],
        "steps": [
            "verify calibration, orientation labels and acquisition quality before measuring",
            "report only observable content within the validated measurement range",
            "distinguish acquisition artefacts from real structure",
            "route every clinical or regulated observation to qualified review",
        ],
        "evidence": ["calibration_reference", "region", "measurement_tolerance"],
        "outputs": ["acquisition_report", "observation_log", "review_handoff"],
    },
    "cat-19": {
        "inputs": ["video", "audio", "image"],
        "steps": [
            "map the recording against the supplied rubric or lesson goals",
            "locate completed, missed, out-of-order or unsafe steps with timecodes",
            "provide practice tasks linked to the exact moments",
            "leave grading and accommodation decisions to the human instructor",
        ],
        "evidence": ["timecode", "rubric_row", "observed_step"],
        "outputs": ["rubric_evidence", "feedback_notes", "practice_plan"],
    },
    "cat-20": {
        "inputs": ["video", "audio", "image"],
        "steps": [
            "measure edit rhythm (shot lengths, movement, dialogue density) rather than assert taste",
            "describe craft choices and their observable effect on pacing and clarity",
            "compare intended plan against captured material where both exist",
            "produce timecoded production notes for downstream teams",
        ],
        "evidence": ["timecode", "shot_boundary", "measurement"],
        "outputs": ["craft_analysis", "production_notes", "selection_table"],
    },
    "cat-21": {
        "inputs": ["video", "image", "audio"],
        "steps": [
            "extract the visible product claims, features and demonstrations with timecodes",
            "separate claimed benefits from demonstrated evidence",
            "flag missing proof, usability friction and unanswered questions",
            "check listing or creative coverage against the channel requirements supplied",
        ],
        "evidence": ["timecode", "region", "claim_vs_evidence"],
        "outputs": ["creative_analysis", "coverage_map", "gap_list"],
    },
    "cat-22": {
        "inputs": ["video", "audio", "subtitle"],
        "steps": [
            "preserve speaker turns, names, numbers and approved terminology",
            "check reading speed, line breaks and on-screen text fit per locale",
            "flag cultural references that change meaning across markets",
            "compare every language track against the source for drift and omissions",
        ],
        "evidence": ["timecode", "source_span", "target_span", "terminology_id"],
        "outputs": ["localization_plan", "terminology_table", "qa_checklist"],
    },
    "cat-23": {
        "inputs": ["image", "video", "audio", "subtitle", "archive"],
        "steps": [
            "resolve the requested capabilities into ordered, restartable stages",
            "estimate cost and time from measured container properties",
            "persist checkpoints and classify failures for bounded retry",
            "validate every intermediate contract before the next stage consumes it",
        ],
        "evidence": ["stage_id", "input_hash", "parameters", "status"],
        "outputs": ["pipeline_plan", "checkpoint_state", "cost_estimate"],
    },
    "cat-24": {
        "inputs": ["image", "video", "audio", "annotation"],
        "steps": [
            "define labels, instructions, edge cases and abstention rules",
            "generate provisional labels that a human validates, never ground truth",
            "measure agreement and localisation error with explicit tolerance windows",
            "record versions, seeds and metric definitions for reproducibility",
        ],
        "evidence": ["label", "annotator", "metric_definition", "benchmark_item"],
        "outputs": ["annotation_guide", "agreement_report", "evaluation_result"],
    },
    "cat-25": {
        "inputs": ["image", "video", "audio", "metadata", "consent_record"],
        "steps": [
            "record rights, consent, territory, duration and restrictions per asset",
            "apply project policy for retention, access and allowed transformations",
            "block release while a mandatory gate is unsatisfied",
            "append every decision to a tamper-evident audit trail",
        ],
        "evidence": ["asset_id", "rights_status", "policy_rule", "decision_log"],
        "outputs": ["rights_register", "policy_decision", "audit_trail"],
    },
}

# Local commands of this Skill Tool that partially implement a capability.
COMMAND_HINTS = {
    "cat-01": ["probe", "inspect", "ingest"],
    "cat-02": ["analyze-image"],
    "cat-03": ["inspect", "analyze-video"],
    "cat-04": ["inspect", "analyze-audio"],
    "cat-05": ["analyze-audio"],
    "cat-06": ["subtitle-audit"],
    "cat-07": ["analyze-video"],
    "cat-08": ["analyze-video"],
    "cat-09": ["analyze-image", "analyze-audio", "analyze-video", "conformance"],
    "cat-10": ["accessibility"],
    "cat-11": ["plan"],
    "cat-12": ["analyze-image", "index"],
    "cat-13": ["report", "plan"],
    "cat-14": ["inspect"],
    "cat-15": ["inspect"],
    "cat-16": ["inspect"],
    "cat-17": ["analyze-video"],
    "cat-18": ["analyze-image"],
    "cat-19": ["analyze-video"],
    "cat-20": ["analyze-video"],
    "cat-21": ["analyze-image"],
    "cat-22": ["subtitle-audit"],
    "cat-23": ["plan", "ingest"],
    "cat-24": ["evaluate"],
    "cat-25": ["inspect", "report"],
}

STOPWORDS = set("""a an and are as at be by for from has have how in into is it its
of on or that the their then there these this to using was will with your you we
while without within across per via""".split())


class CatalogError(Exception):
    code = "catalog-error"


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def load_json(path, kind="json"):
    if not os.path.isfile(path):
        raise CatalogError("file not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise CatalogError("%s is not valid JSON: %s" % (kind, exc))


def load_catalog(path):
    cat = load_json(path, "catalog")
    for key in ("categories", "skill_count", "category_count"):
        if key not in cat:
            raise CatalogError("catalog missing required key %r" % key)
    ids = set()
    for c in cat["categories"]:
        for s in c["skills"]:
            if s["id"] in ids:
                raise CatalogError("duplicate skill id %s" % s["id"])
            ids.add(s["id"])
            if not s.get("description"):
                raise CatalogError("skill %s has no description" % s["id"])
    return cat


def flatten(cat):
    rows = []
    for c in cat["categories"]:
        for s in c["skills"]:
            rows.append({
                "skill_id": s["id"],
                "skill_name": s["name"],
                "description": s["description"],
                "category_id": c["id"],
                "category_number": c["number"],
                "category_name": c["name"],
                "policy_class": s.get("policy_class") or c.get("policy_class"),
                "gates": s.get("gates") or c.get("gates", []),
            })
    return rows


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def _tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in STOPWORDS]


def _category_matches(category, needle):
    needle = str(needle).strip().lower()
    if not needle:
        return False
    if needle in (category["id"].lower(), category["slug"].lower(),
                  category["name"].lower()):
        return True
    if needle.isdigit() and int(needle) == category["number"]:
        return True
    return needle in category["name"].lower()


def search(catalog, query=None, category=None, limit=10):
    """Score catalog entries against a free-text query and/or category filter."""
    rows = flatten(catalog)
    if category:
        keep = {c["id"] for c in catalog["categories"]
                if _category_matches(c, category)}
        if not keep:
            raise CatalogError("no catalog category matches %r" % category)
        rows = [r for r in rows if r["category_id"] in keep]
    if not query:
        return rows[:limit] if limit else rows

    qtokens = _tokens(query)
    qphrase = query.lower().strip()
    scored = []
    for r in rows:
        hay_name = r["skill_name"].lower()
        hay_desc = r["description"].lower()
        hay_cat = r["category_name"].lower()
        score = 0.0
        if qphrase and qphrase in hay_name:
            score += 6.0
        if qphrase and qphrase in hay_desc:
            score += 3.0
        for t in qtokens:
            if t in hay_name.split() or (" " + t) in (" " + hay_name):
                score += 2.0
            elif t in hay_name:
                score += 1.2
            if t in hay_desc:
                score += 0.8
            if t in hay_cat:
                score += 0.5
            if t == r["skill_id"] or t == r["category_id"]:
                score += 4.0
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["category_number"], pair[1]["skill_id"]))
    matched = []
    for score, r in scored[:limit]:
        item = dict(r)
        item["match_score"] = round(score, 2)
        matched.append(item)
    return matched


def resolve_categories(catalog, selector):
    """Accept '3', 'cat-03', or a name fragment; return the matching category."""
    needle = str(selector).strip().lower()
    for c in catalog["categories"]:
        if needle in (c["id"].lower(), c["slug"].lower(),
                      c["name"].lower(), c["name"].lower().replace(" ", "-")):
            return c
    if needle.isdigit():
        n = int(needle)
        for c in catalog["categories"]:
            if c["number"] == n:
                return c
    for c in catalog["categories"]:
        if needle and needle in c["name"].lower():
            return c
    return None


# --------------------------------------------------------------------------
# plan generation
# --------------------------------------------------------------------------


def _unit_from_row(row, index, config, policy_preset, policies):
    profile = CATEGORY_PROFILES.get(row["category_id"], {
        "inputs": ["image", "video", "audio"],
        "steps": ["define accepted inputs", "measure observable properties",
                  "attach evidence to each claim", "state limits and abstain when unsupported"],
        "evidence": ["source_file", "timecode_or_region"],
        "outputs": ["observations", "evidence_table"],
    })
    preset = policies["presets"].get(policy_preset, policies["presets"]["standard"])
    gates = list(dict.fromkeys(row["gates"]))
    needs_review = row["category_id"] in preset.get("require_human_review_categories", [])
    if needs_review and "gate-human-review-on-low-confidence" not in gates:
        gates.append("gate-human-review-on-low-confidence")
    approval = any(policies["gates"].get(g, {}).get("approval_required") for g in gates)
    return {
        "unit_id": "U%02d" % index,
        "skill_id": row["skill_id"],
        "skill_name": row["skill_name"],
        "category": row["category_name"],
        "responsibility": row["description"],
        "policy_class": row["policy_class"],
        "accepted_inputs": profile["inputs"],
        "analysis_steps": profile["steps"],
        "required_evidence": profile["evidence"],
        "produces": profile["outputs"],
        "local_commands": COMMAND_HINTS.get(row["category_id"], []),
        "external_dependency": _external_dependency(row["category_id"]),
        "confidence_policy": {
            "review_below": preset["confidence_review_threshold"],
            "abstain_below": preset["confidence_escalate_threshold"],
            "must_label_inference": True,
        },
        "gates": [{"id": g, "title": policies["gates"][g]["title"],
                   "required_action": policies["gates"][g]["required_action"],
                   "approval_required": policies["gates"][g]["approval_required"]}
                  for g in gates if g in policies["gates"]],
        "requires_human_approval": approval,
        "runtime": "local-measurement" if COMMAND_HINTS.get(row["category_id"]) else "plan-only",
    }


def _external_dependency(cat_id):
    if cat_id in ("cat-02", "cat-06", "cat-18", "cat-21"):
        return "Vision model or qualified human for semantic labels (not shipped)."
    if cat_id == "cat-04":
        return "Speech-to-text model for verbatim transcription (not shipped)."
    if cat_id == "cat-22":
        return "Translation model or human translator (not shipped)."
    if cat_id in ("cat-11",):
        return "Encoder (ffmpeg or an editing host) to execute the recipe (not shipped)."
    if cat_id in ("cat-12", "cat-24"):
        return "Embedding model or benchmark labels (not shipped)."
    return None


def build_plan(matches, config, catalog, policies, request_text=None,
               policy_preset=None):
    preset_name = policy_preset or config.get("policy", {}).get("preset", "standard")
    if preset_name not in policies["presets"]:
        raise CatalogError("unknown policy preset %r (available: %s)"
                           % (preset_name, ", ".join(sorted(policies["presets"]))))
    max_units = int(config.get("limits", {}).get("max_plan_units", 250))
    if len(matches) > max_units:
        raise CatalogError("plan would contain %d units, over limits.max_plan_units=%d"
                           % (len(matches), max_units))
    if not matches:
        raise CatalogError("no catalog entries matched the request",
                          )
    units = [_unit_from_row(m, i + 1, config, preset_name, policies)
             for i, m in enumerate(matches)]
    categories = sorted({u["category"] for u in units})
    if len(categories) == 1:
        umbrella = categories[0].lower()
    else:
        umbrella = "unified media intelligence workflow"

    stage_order = ["ingest", "probe", "measure", "ground", "interpret", "review", "report"]
    pipeline = [
        {"stage": "ingest", "description": "safety scan, hash, route, preserve originals",
         "units": [u["unit_id"] for u in units] if True else []},
        {"stage": "probe", "description": "container/stream technical probe",
         "units": [u["unit_id"] for u in units]},
        {"stage": "measure", "description": "deterministic signal measurement per unit",
         "units": [u["unit_id"] for u in units]},
        {"stage": "ground", "description": "attach timecodes, regions, byte offsets and confidences",
         "units": [u["unit_id"] for u in units]},
        {"stage": "interpret", "description": "observations separated from interpretation, limits stated",
         "units": [u["unit_id"] for u in units]},
        {"stage": "review", "description": "route low-confidence or gated units to a human",
         "units": sorted({u["unit_id"] for u in units if u["gates"]})},
        {"stage": "report", "description": "emit evidence table plus analyst report",
         "units": [u["unit_id"] for u in units]},
    ]
    gates = sorted({g["id"] for u in units for g in u["gates"]})
    approval_required = any(u["requires_human_approval"] for u in units)
    preset = policies["presets"][preset_name]
    return {
        "plan_version": "1.0.0",
        "generated_by": "media-intelligence-workbench",
        "request": (request_text or "").strip()[:4000],
        "umbrella": umbrella,
        "policy_preset": preset_name,
        "policy_summary": preset["summary"],
        "catalog": {"category_count": catalog["category_count"],
                    "skill_count": catalog["skill_count"],
                    "matched_units": len(units),
                    "categories_touched": categories},
        "units": units,
        "pipeline": [dict(s) for s in pipeline],
        "stage_order": stage_order,
        "gates": [{"id": g, "title": policies["gates"][g]["title"],
                   "required_action": policies["gates"][g]["required_action"],
                   "approval_required": policies["gates"][g]["approval_required"],
                   "blocks_release": policies["gates"][g]["blocks_release"]}
                  for g in gates],
        "requires_human_approval": approval_required,
        "abstention_rules": policies["abstention_rules"],
        "contract": {
            "evidence_row_schema": "asset-evidence-schema.json#/$defs/evidenceRow",
            "report_schema": "asset-evidence-schema.json#/$defs/report",
            "required_report_sections": ["scope", "sources", "observations",
                                         "interpretation", "evidence_table",
                                         "limits", "next_steps"],
        },
        "next_commands": _next_commands(units, config),
        "limits": config.get("limits", {}),
    }


def _next_commands(units, config):
    cmds = []
    seen = set()
    for u in units:
        for c in u["local_commands"]:
            if c in seen:
                continue
            seen.add(c)
            cmds.append("python3 media-intelligence-workbench/tools-miw.py %s --help" % c)
    return cmds[:8]


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

REQUIRED_REPORT_SECTIONS = ["scope", "sources", "observations", "interpretation",
                            "evidence_table", "limits", "next_steps"]


def validate_report(report, strict_evidence=True):
    """Structural validation of a report document. Returns list of error strings."""
    errors = []

    def need(cond, msg):
        if not cond:
            errors.append(msg)

    need(isinstance(report, dict), "report must be a JSON object")
    if not isinstance(report, dict):
        return errors
    need(report.get("report_version") == "1.0.0", "report_version must be '1.0.0'")
    need(isinstance(report.get("title"), str) and report["title"].strip(),
         "title must be a non-empty string")
    for section in REQUIRED_REPORT_SECTIONS:
        need(section in report, "missing required section %r" % section)
    need(isinstance(report.get("sources", []), list), "sources must be a list")
    need(isinstance(report.get("evidence_table", []), list),
         "evidence_table must be a list")
    need(isinstance(report.get("observations", []), list), "observations must be a list")
    need(isinstance(report.get("limits"), (list, str)), "limits must be a list or string")

    for i, row in enumerate(report.get("evidence_table", []) or []):
        prefix = "evidence_table[%d]" % i
        if not isinstance(row, dict):
            errors.append(prefix + " must be an object")
            continue
        for key in ("claim", "source", "confidence", "kind"):
            if key not in row:
                errors.append(prefix + " missing %r" % key)
        conf = row.get("confidence")
        if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
            errors.append(prefix + ".confidence must be a number in [0,1]")
        if row.get("kind") not in ("observation", "interpretation"):
            errors.append(prefix + ".kind must be 'observation' or 'interpretation'")
        has_anchor = any(row.get(k) for k in ("timecode", "start_s", "region", "offset"))
        if strict_evidence and row.get("kind") == "observation" and not has_anchor:
            errors.append(prefix + " observation has no timecode/region/offset anchor")
        if row.get("kind") == "interpretation" and not row.get("basis"):
            errors.append(prefix + " interpretation must name its basis")
    return errors


def validate_plan(plan, catalog, policies):
    errors = []
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    if not plan.get("units"):
        errors.append("plan.units must not be empty")
    known = {s["id"] for c in catalog["categories"] for s in c["skills"]}
    seen = set()
    for u in plan.get("units", []) or []:
        if u.get("skill_id") not in known:
            errors.append("unit %s references unknown skill %s"
                          % (u.get("unit_id"), u.get("skill_id")))
        if u.get("unit_id") in seen:
            errors.append("duplicate unit id %s" % u.get("unit_id"))
        seen.add(u.get("unit_id"))
        if not u.get("analysis_steps"):
            errors.append("unit %s has no analysis steps" % u.get("unit_id"))
        for g in u.get("gates", []) or []:
            if g.get("id") not in policies.get("gates", {}):
                errors.append("unit %s references undefined gate %s"
                              % (u.get("unit_id"), g.get("id")))
    for stage in plan.get("pipeline", []) or []:
        if stage.get("stage") not in plan.get("stage_order", []):
            errors.append("pipeline stage %r not in stage_order" % stage.get("stage"))
    if not plan.get("gates"):
        errors.append("plan must carry at least one gate")
    return errors


# --------------------------------------------------------------------------
# CLI (direct module use for inspection)
# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inspect and search the media "
                                             "intelligence capability catalog.")
    ap.add_argument("--catalog", default="asset-capability-catalog.json")
    ap.add_argument("--list-categories", action="store_true")
    ap.add_argument("--search")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    try:
        cat = load_catalog(args.catalog)
    except CatalogError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    if args.list_categories:
        for c in cat["categories"]:
            print("%-7s %-58s %2d skills  policy=%s"
                  % (c["id"], c["name"], len(c["skills"]), c.get("policy_class")))
        print("\n%d categories, %d skills" % (cat["category_count"], cat["skill_count"]))
        return 0
    if args.search:
        for r in search(cat, args.search, limit=args.limit):
            print("%-6s %-40s %s" % (r["skill_id"], r["skill_name"],
                                     ("score %.2f" % r["match_score"]) if "match_score" in r else ""))
            print("        " + r["description"])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
