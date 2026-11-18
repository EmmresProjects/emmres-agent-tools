#!/usr/bin/env python3
"""Core catalog, validation, planning, captions, and HTML animatic support."""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CAPABILITIES = [
    "video-prompt-architect", "shot-list-generator", "storyboard-planner",
    "multi-shot-orchestrator", "timeline-assembly-skill", "caption-qa-auditor",
    "synthetic-media-disclosure-skill", "final-release-gatekeeper"
]
INTEGRATION_CATEGORIES = {1, 3, 4, 5, 6, 7, 13, 14, 15, 16, 21, 22, 24, 37, 42, 43, 45, 46}
APPROVAL_TERMS = {
    "likeness": ("likeness", "avatar", "identity", "deepfake"),
    "rights": ("copyright", "license", "trademark", "rights"),
    "security": ("credential", "security", "webhook"),
    "training": ("training", "fine-tun", "dataset"),
    "publishing": ("publish", "distribution", "upload")
}

class ToolError(Exception):
    """Expected input or operational error with a stable machine-readable code."""
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def read_json(path: str | Path, max_bytes: int = 1_048_576) -> dict[str, Any]:
    p = Path(path)
    try:
        if not p.is_file():
            raise ToolError("INPUT_NOT_FOUND", f"Input file does not exist: {p}")
        if p.stat().st_size > max_bytes:
            raise ToolError("INPUT_TOO_LARGE", f"Input exceeds {max_bytes} bytes: {p}")
        value = json.loads(p.read_text(encoding="utf-8"))
    except ToolError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ToolError("INPUT_UNREADABLE", f"Cannot read {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolError("INVALID_JSON", f"Invalid JSON in {p} at line {exc.lineno}, column {exc.colno}", {"line": exc.lineno, "column": exc.colno}) from exc
    if not isinstance(value, dict):
        raise ToolError("INVALID_INPUT_TYPE", "Top-level JSON value must be an object")
    return value


def write_json(path: str | Path, value: Any, overwrite: bool = False) -> None:
    p = Path(path)
    if p.exists() and not overwrite:
        raise ToolError("OUTPUT_EXISTS", f"Refusing to overwrite existing output: {p}", {"recovery": "Choose another path or pass --overwrite after reviewing the existing file."})
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_name(p.name + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(p)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ToolError("OUTPUT_WRITE_FAILED", f"Cannot write {p}: {exc}") from exc


def load_catalog(path: str | Path | None = None) -> dict[str, Any]:
    catalog = read_json(path or ROOT / "asset-skill-catalog.json")
    categories = catalog.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ToolError("INVALID_CATALOG", "Catalog must contain a non-empty categories array")
    seen: set[str] = set()
    count = 0
    for category in categories:
        if not all(k in category for k in ("id", "name", "purpose", "skills")):
            raise ToolError("INVALID_CATALOG", "Each category requires id, name, purpose, and skills")
        for skill in category["skills"]:
            if skill["id"] in seen:
                raise ToolError("INVALID_CATALOG", f"Duplicate capability id: {skill['id']}")
            seen.add(skill["id"])
            count += 1
    if count != catalog.get("capability_count"):
        raise ToolError("INVALID_CATALOG", "capability_count does not match catalog entries")
    return catalog


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config = read_json(path or ROOT / "asset-default-config.json")
    for key in ("limits", "runtime", "defaults", "policy"):
        if key not in config:
            raise ToolError("INVALID_CONFIG", f"Missing configuration section: {key}")
    return config


def normalize_brief(brief: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    errors = []
    for field in ("project_title", "prompt"):
        if not isinstance(brief.get(field), str) or not brief.get(field, "").strip():
            errors.append({"field": field, "message": "A non-empty string is required"})
    allowed = {"project_title", "prompt", "objective", "audience", "duration_seconds", "aspect_ratio", "resolution", "fps", "language", "seed", "categories", "capabilities", "shots", "assets", "safety", "delivery", "provider"}
    for field in brief.keys() - allowed:
        errors.append({"field": field, "message": "Unknown field; see asset-brief.schema.json"})
    if errors:
        raise ToolError("INVALID_BRIEF", "Brief validation failed", errors)
    result = dict(brief)
    defaults = config["defaults"]
    for field in ("duration_seconds", "aspect_ratio", "resolution", "fps", "language"):
        result.setdefault(field, defaults[field])
    for field in ("categories", "capabilities", "shots", "assets"):
        result.setdefault(field, [])
    result.setdefault("safety", {})
    result.setdefault("delivery", {})
    result.setdefault("seed", 0)
    duration = result["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= config["limits"]["max_duration_seconds"]:
        errors.append({"field": "duration_seconds", "message": f"Must be a number greater than 0 and at most {config['limits']['max_duration_seconds']}"})
    if isinstance(result["fps"], bool) or not isinstance(result["fps"], (int, float)) or not 0 < result["fps"] <= 120:
        errors.append({"field": "fps", "message": "Must be greater than 0 and at most 120"})
    if not isinstance(result["aspect_ratio"], str) or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", result["aspect_ratio"]):
        errors.append({"field": "aspect_ratio", "message": "Must use positive integer dimensions, for example 16:9"})
    resolution = result["resolution"]
    if not isinstance(resolution, list) or len(resolution) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 16 <= v <= 16384 for v in resolution):
        errors.append({"field": "resolution", "message": "Must contain two integers between 16 and 16384"})
    for field in ("categories", "capabilities", "shots", "assets"):
        if not isinstance(result[field], list):
            errors.append({"field": field, "message": "Must be an array"})
    if len(result["shots"]) > config["limits"]["max_shots"]:
        errors.append({"field": "shots", "message": "Too many shots"})
    for index, shot in enumerate(result["shots"]):
        if not isinstance(shot, dict) or not isinstance(shot.get("title"), str) or not shot["title"].strip() or not isinstance(shot.get("visual"), str) or not shot["visual"].strip():
            errors.append({"field": f"shots[{index}]", "message": "Each shot requires non-empty title and visual strings"})
        elif "duration_seconds" in shot and (isinstance(shot["duration_seconds"], bool) or not isinstance(shot["duration_seconds"], (int, float)) or shot["duration_seconds"] <= 0):
            errors.append({"field": f"shots[{index}].duration_seconds", "message": "Must be greater than 0"})
    for index, asset in enumerate(result["assets"]):
        if not isinstance(asset, dict) or not isinstance(asset.get("path"), str) or asset.get("kind") not in {"image", "video", "audio", "font", "data", "other"}:
            errors.append({"field": f"assets[{index}]", "message": "Each asset requires path and a supported kind"})
    if errors:
        raise ToolError("INVALID_BRIEF", "Brief validation failed", errors)
    return result


def catalog_indexes(catalog: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    categories, skills = {}, {}
    for category in catalog["categories"]:
        categories[category["id"]] = category
        categories[category["name"].lower()] = category
        categories[str(category["number"])] = category
        for skill in category["skills"]:
            record = dict(skill)
            record["category_id"] = category["id"]
            record["category_number"] = category["number"]
            record["category_name"] = category["name"]
            skills[skill["id"]] = record
            skills[skill["name"].lower()] = record
    return categories, skills


def select_capabilities(brief: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    categories, skills = catalog_indexes(catalog)
    selected: dict[str, Any] = {}
    unknown = []
    for value in brief["categories"]:
        key = str(value).strip().lower()
        if key not in categories:
            unknown.append(value)
            continue
        for skill in categories[key]["skills"]:
            selected[skill["id"]] = skills[skill["id"]]
    for value in brief["capabilities"]:
        key = str(value).strip().lower()
        if key not in skills:
            unknown.append(value)
        else:
            selected[skills[key]["id"]] = skills[key]
    if unknown:
        raise ToolError("UNKNOWN_CAPABILITY", "One or more categories or capabilities were not found", {"unknown": unknown, "recovery": "Run the capabilities command to inspect exact ids."})
    if not selected:
        for capability_id in DEFAULT_CAPABILITIES:
            selected[capability_id] = skills[capability_id]
    return sorted(selected.values(), key=lambda item: (item["category_number"], item["number"]))


def approvals_for(brief: dict[str, Any], skill: dict[str, Any]) -> list[str]:
    approvals = set()
    safety = brief["safety"]
    for field, approval in (("contains_real_person", "likeness"), ("voice_cloning", "voice_cloning"), ("sensitive_content", "sensitive_content"), ("factual_reconstruction", "factual_reconstruction"), ("political_content", "sensitive_content")):
        if safety.get(field) is True:
            approvals.add(approval)
    text = (skill["name"] + " " + skill["description"]).lower()
    for approval, terms in APPROVAL_TERMS.items():
        if any(term in text for term in terms):
            approvals.add(approval)
    if any("license" not in asset or not asset.get("license") for asset in brief["assets"]):
        approvals.add("rights")
    return sorted(approvals)


def output_artifacts(skill: dict[str, Any]) -> list[str]:
    text = skill["name"].lower()
    rules = [
        (("caption", "subtitle", "translation", "localiz"), ["captions.srt", "captions.vtt", "localization-notes.json"]),
        (("audio", "music", "voice", "speech", "dub", "lip"), ["audio-production-sheet.json", "sync-cues.json"]),
        (("color", "look", "finish"), ["look-decision-list.json", "finishing-checklist.json"]),
        (("encode", "compression", "delivery", "codec"), ["delivery-profile.json", "validation-report.json"]),
        (("safe", "rights", "consent", "provenance", "privacy"), ["approval-register.json", "provenance-record.json"]),
        (("test", "quality", "audit", "evaluat", "inspect"), ["evaluation-results.json", "repair-queue.json"]),
        (("train", "dataset", "model"), ["dataset-or-model-card.json", "reproducibility-record.json"]),
        (("3d", "nerf", "gaussian", "virtual"), ["scene-specification.json", "capture-requirements.json"]),
        (("prompt",), ["prompt-pack.json", "constraint-report.json"]),
        (("shot", "story", "scene", "screenplay", "storyboard"), ["shot-plan.json", "storyboard.html"])
    ]
    for terms, artifacts in rules:
        if any(term in text for term in terms):
            return artifacts
    return ["production-worksheet.json", "handoff-notes.json"]


def integration_for(skill: dict[str, Any]) -> dict[str, Any]:
    if skill["category_number"] in INTEGRATION_CATEGORIES:
        return {"mode": "provider-adapter-required", "status": "planned", "reason": "The catalog entry needs a configured generation, media, sensor, training, or compute provider. No provider result is fabricated."}
    return {"mode": "deterministic-local-workflow", "status": "planned", "reason": "Planning and validation are available locally; media edits require supplied source media or the optional renderer."}


def make_shots(brief: dict[str, Any]) -> list[dict[str, Any]]:
    if brief["shots"]:
        raw = [dict(shot) for shot in brief["shots"]]
    else:
        pieces = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", brief["prompt"]) if part.strip()]
        if not pieces:
            pieces = [brief["prompt"]]
        raw = [{"title": f"Shot {i + 1}", "visual": text, "caption": text} for i, text in enumerate(pieces)]
    specified = sum(float(shot.get("duration_seconds", 0)) for shot in raw)
    unspecified = sum("duration_seconds" not in shot for shot in raw)
    remaining = max(0.1, float(brief["duration_seconds"]) - specified)
    result, cursor = [], 0.0
    for index, shot in enumerate(raw):
        duration = float(shot.get("duration_seconds", remaining / max(1, unspecified)))
        if not math.isfinite(duration) or duration <= 0:
            raise ToolError("INVALID_SHOT_DURATION", f"Shot {index + 1} has an invalid duration")
        item = {
            "id": shot.get("id") or f"shot-{index + 1:02d}", "title": shot["title"].strip(),
            "visual": shot["visual"].strip(), "caption": shot.get("caption", ""),
            "duration_seconds": round(duration, 3), "start_seconds": round(cursor, 3),
            "end_seconds": round(cursor + duration, 3), "camera": shot.get("camera", "not specified"),
            "motion": shot.get("motion", "not specified"), "source": shot.get("source")
        }
        cursor += duration
        result.append(item)
    return result


def create_plan(brief: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    selected = select_capabilities(brief, catalog)
    shots = make_shots(brief)
    tasks = []
    for skill in selected:
        approvals = approvals_for(brief, skill)
        tasks.append({
            "capability_id": skill["id"], "name": skill["name"], "category_id": skill["category_id"],
            "description": skill["description"], "responsibility": f"Produce and review the {skill['name']} work package using the brief, shot plan, supplied assets, and policy gates.",
            "inputs": ["brief", "shots", "assets", "delivery", "safety"], "expected_artifacts": output_artifacts(skill),
            "quality_gates": ["source fidelity", "technical validity", "accessibility where relevant", "rights and consent", "reproducibility", "handoff readiness"],
            "required_approvals": approvals, "execution": integration_for(skill),
            "recovery": "Preserve completed shots and prior artifacts; correct the earliest failed input or integration and rerun only affected tasks."
        })
    required = sorted({item for task in tasks for item in task["required_approvals"]})
    warnings = []
    if not brief["assets"]:
        warnings.append("No source assets were supplied; preview uses text and color panels, not generated footage.")
    if required:
        warnings.append("Human approval is required before the indicated sensitive or downstream actions.")
    if abs(shots[-1]["end_seconds"] - brief["duration_seconds"]) > 0.01:
        warnings.append(f"Shot durations total {shots[-1]['end_seconds']} seconds, which differs from the requested {brief['duration_seconds']} seconds; explicit shot durations take precedence.")
    return {
        "schema_version": "1.0", "tool": "ai-video-production", "project_title": brief["project_title"],
        "status": "planned", "brief": brief, "shots": shots,
        "duration_seconds": shots[-1]["end_seconds"], "capabilities": tasks,
        "capability_count": len(tasks), "approval_register": [{"approval": item, "status": "pending"} for item in required],
        "provenance": {"catalog": "ai-video-production/asset-skill-catalog.json", "generator": "deterministic local planner", "seed": brief["seed"], "synthetic_media_disclosure": brief["delivery"].get("disclosure", "Review and disclose synthetic or modified media as appropriate.")},
        "warnings": warnings, "unresolved_issues": ["Configure and approve external providers before neural generation, model training, sensor processing, or publishing."] if any(t["execution"]["mode"] == "provider-adapter-required" for t in tasks) else []
    }


def timestamp(seconds: float, decimal: str = ",") -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ToolError("INVALID_CAPTION_TIME", f"Invalid caption time: {seconds}")
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal}{millis:03d}"


def captions_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan.get("shots"), list) or not plan["shots"]:
        raise ToolError("INVALID_PLAN", "Plan must contain at least one shot")
    captions = []
    for i, shot in enumerate(plan["shots"]):
        text = str(shot.get("caption", "")).strip()
        if text:
            captions.append({"index": len(captions) + 1, "start": float(shot["start_seconds"]), "end": float(shot["end_seconds"]), "text": text, "speaker": shot.get("speaker")})
    for caption in captions:
        if caption["end"] <= caption["start"]:
            raise ToolError("INVALID_CAPTION_TIME", f"Caption {caption['index']} must end after it starts")
    return captions


def format_srt(captions: list[dict[str, Any]]) -> str:
    return "\n".join(f"{c['index']}\n{timestamp(c['start'])} --> {timestamp(c['end'])}\n{c['text']}\n" for c in captions)


def format_vtt(captions: list[dict[str, Any]]) -> str:
    body = "\n".join(f"{timestamp(c['start'], '.')} --> {timestamp(c['end'], '.')}\n{c['text']}\n" for c in captions)
    return "WEBVTT\n\n" + body


def evaluate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    checks = []
    shots = plan.get("shots", [])
    checks.append({"check": "shots_present", "passed": bool(shots)})
    continuous = True
    valid_times = True
    previous = 0.0
    for shot in shots:
        try:
            start, end = float(shot["start_seconds"]), float(shot["end_seconds"])
            valid_times &= end > start
            continuous &= abs(start - previous) < 0.002
            previous = end
        except (KeyError, TypeError, ValueError):
            valid_times = False
    checks.extend([{"check": "valid_shot_times", "passed": valid_times}, {"check": "continuous_timeline", "passed": continuous}])
    captions = [s for s in shots if str(s.get("caption", "")).strip()]
    checks.append({"check": "caption_coverage", "passed": len(captions) == len(shots), "detail": f"{len(captions)}/{len(shots)} shots have captions"})
    pending = [a for a in plan.get("approval_register", []) if a.get("status") != "approved"]
    checks.append({"check": "approvals_resolved", "passed": not pending, "detail": f"{len(pending)} pending"})
    external = [c for c in plan.get("capabilities", []) if c.get("execution", {}).get("mode") == "provider-adapter-required"]
    checks.append({"check": "external_work_disclosed", "passed": all(c["execution"]["status"] == "planned" for c in external), "detail": f"{len(external)} provider-dependent tasks remain planned"})
    return {"status": "pass" if all(c["passed"] for c in checks) else "review-required", "checks": checks, "repair_route": "Resolve the earliest failed check, update the brief or plan, and rerun plan and evaluate. Pending approvals do not invalidate the editable plan but block release."}


def render_html(plan: dict[str, Any]) -> str:
    """Create a self-contained, accessible animated storyboard; no network resources."""
    title = html.escape(plan["project_title"])
    shots = plan["shots"]
    duration = float(plan["duration_seconds"])
    colors = ["#111827", "#164e63", "#3f2d5b", "#134e4a", "#4a2c20"]
    panels = []
    for i, shot in enumerate(shots):
        bg = colors[i % len(colors)]
        panels.append(f'<section class="panel" data-start="{shot["start_seconds"]}" data-end="{shot["end_seconds"]}" aria-label="{html.escape(shot["title"])}"><div class="eyebrow">SHOT {i+1:02d} · {html.escape(shot["camera"])}</div><h2>{html.escape(shot["title"])}</h2><p>{html.escape(shot["visual"])}</p><div class="motif" aria-hidden="true"><i></i><i></i><i></i></div></section>')
    data = json.dumps([{"start": s["start_seconds"], "end": s["end_seconds"], "caption": s.get("caption", "")} for s in shots], ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — Animatic</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#080d18;color:#f8fafc;font:16px/1.5 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:24px}}header{{display:flex;gap:16px;align-items:end;justify-content:space-between;flex-wrap:wrap}}h1{{margin:0;font-size:clamp(24px,4vw,44px)}}.meta{{color:#a5b4fc}}.stage{{position:relative;aspect-ratio:16/9;overflow:hidden;border:1px solid #334155;border-radius:18px;background:#000;margin:20px 0}}.panel{{position:absolute;inset:0;padding:7%;display:none;flex-direction:column;justify-content:center;overflow:hidden}}.panel.active{{display:flex;animation:enter .45s ease-out}}@keyframes enter{{from{{opacity:.2;transform:scale(1.025)}}to{{opacity:1;transform:scale(1)}}}}h2{{font-size:clamp(28px,5vw,64px);line-height:1.05;margin:10px 0;max-width:80%}}.panel p{{font-size:clamp(15px,2vw,25px);max-width:72%;color:#e2e8f0}}.eyebrow{{font-size:12px;letter-spacing:.14em;color:#67e8f9;text-transform:uppercase}}.motif{{position:absolute;right:-5%;bottom:-15%;width:43%;aspect-ratio:1;opacity:.35}}.motif i{{position:absolute;inset:0;border:2px solid #a5f3fc;border-radius:50%}}.motif i:nth-child(2){{inset:15%}}.motif i:nth-child(3){{inset:30%}}.caption{{position:absolute;bottom:5%;left:10%;right:10%;text-align:center;font-weight:650;font-size:clamp(16px,2.4vw,28px);text-shadow:0 2px 5px #000;z-index:3}}.controls{{display:flex;gap:12px;align-items:center;flex-wrap:wrap}}button{{background:#67e8f9;border:0;border-radius:8px;padding:10px 20px;font-weight:700;cursor:pointer}}button:focus-visible,input:focus-visible{{outline:3px solid #fbbf24;outline-offset:3px}}input[type=range]{{flex:1;min-width:180px;accent-color:#67e8f9}}.notes{{margin-top:24px;color:#cbd5e1}}.notes summary{{cursor:pointer;color:#67e8f9}}@media(prefers-reduced-motion:reduce){{.panel.active{{animation:none}}}}</style></head>
<body><main><header><div><div class="eyebrow">AI VIDEO PRODUCTION · EDITABLE PREVISUALIZATION</div><h1>{title}</h1></div><div class="meta">{duration:.3f} seconds · {len(shots)} shots</div></header>
<div class="stage" role="region" aria-label="Animated storyboard">{''.join(panels)}<div class="caption" aria-live="polite"></div></div>
<div class="controls"><button type="button" id="play">Play</button><button type="button" id="restart">Restart</button><label for="seek">Timeline</label><input id="seek" type="range" min="0" max="{duration}" value="0" step="0.01"><output id="time">0.00 / {duration:.2f}</output></div>
<details class="notes"><summary>Production and accessibility notes</summary><p>This is a deterministic text-and-color animatic, not neural video footage. Edit the source brief and regenerate the plan to revise it. Captions are available as separate SRT and WebVTT outputs. Motion is disabled when the viewer requests reduced motion.</p><pre>{html.escape(json.dumps(plan.get('provenance', {}), indent=2))}</pre></details></main>
<script>const shots={data},duration={duration};let current=0,playing=false,last=0;const panels=[...document.querySelectorAll('.panel')],caption=document.querySelector('.caption'),seek=document.querySelector('#seek'),time=document.querySelector('#time'),play=document.querySelector('#play');function draw(){{let i=shots.findIndex(s=>current>=s.start&&current<s.end);if(i<0)i=current>=duration?shots.length-1:0;panels.forEach((p,n)=>p.classList.toggle('active',n===i));caption.textContent=shots[i].caption;seek.value=current;time.textContent=current.toFixed(2)+' / '+duration.toFixed(2)}}function frame(t){{if(!playing)return;if(!last)last=t;current+=Math.min((t-last)/1000,.1);last=t;if(current>=duration){{current=duration;playing=false;play.textContent='Play'}}draw();if(playing)requestAnimationFrame(frame)}}play.onclick=()=>{{if(current>=duration)current=0;playing=!playing;play.textContent=playing?'Pause':'Play';last=0;if(playing)requestAnimationFrame(frame)}};document.querySelector('#restart').onclick=()=>{{current=0;last=0;draw()}};seek.oninput=()=>{{current=Number(seek.value);last=0;draw()}};document.addEventListener('keydown',e=>{{if(e.code==='Space'&&e.target===document.body){{e.preventDefault();play.click()}}}});draw();</script></body></html>'''
