"""Production pipeline, governance, safety and automation capabilities.

Part of the AI Image Production Toolkit.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if "tools_core" not in sys.modules:  # pragma: no cover - direct-import fallback
    _spec = importlib.util.spec_from_file_location("tools_core", os.path.join(_HERE, "tools-core.py"))
    _core = importlib.util.module_from_spec(_spec)
    sys.modules["tools_core"] = _core
    _spec.loader.exec_module(_core)

import tools_core as core  # noqa: E402
from tools_core import (  # noqa: E402
    InvalidInput, deterministic_seed, hex_to_rgb, load_asset, md_table, read_json,
    read_text, rgb_to_hex, slugify, utc_now, write_json, write_text,
)

PROFILES = "asset-model-profiles.json"
TEMPLATES = "asset-workflow-templates.json"
SAFETY = "asset-safety-rules.json"
RUBRIC = "asset-quality-rubric.json"
LEXICON = "asset-style-lexicon.json"
SAMPLES = "asset-sample-prompts.json"
NEGATIVES = "asset-negative-prompts.json"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _report(ctx, slug: str, title: str, body: str, data: Any, summary: str,
            metrics: Optional[Dict[str, Any]] = None,
            extra_artifacts: Optional[List[str]] = None) -> Dict[str, Any]:
    report = f"# {title}\n\n{body}\n"
    ctx.write_text(f"{slug}.md", report)
    ctx.write_json(f"{slug}.json", {"title": title, "summary": summary, "data": data,
                                    "metrics": metrics or {}, "generated_utc": utc_now()})
    return {"summary": summary, "data": data, "report": report, "metrics": metrics or {},
            "artifacts": extra_artifacts or []}


def _model(profile_id: str) -> Dict[str, Any]:
    profiles = load_asset(PROFILES)
    for model in profiles["models"]:
        if model["id"] == profile_id:
            return model
    known = ", ".join(m["id"] for m in profiles["models"])
    raise InvalidInput(f"unknown model '{profile_id}' (known: {known})")


def _read_dataset(path: str, caption_field: str) -> List[Dict[str, Any]]:
    """Read a JSONL, JSON or CSV caption dataset into a list of records."""
    if not os.path.isfile(path):
        raise InvalidInput(f"dataset not found: {path}")
    records: List[Dict[str, Any]] = []
    lower = path.lower()
    if lower.endswith(".jsonl") or lower.endswith(".ndjson"):
        for i, line in enumerate(read_text(path).splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise InvalidInput(f"invalid JSON on line {i} of {path}: {exc}") from exc
    elif lower.endswith(".json"):
        payload = read_json(path)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
            records = payload["records"]
        else:
            raise InvalidInput("JSON dataset must be a list or an object with a 'records' list")
    elif lower.endswith(".csv"):
        with open(path, "r", encoding="utf-8", newline="") as fh:
            records = list(csv.DictReader(fh))
    else:
        raise InvalidInput("unsupported dataset format; use .jsonl, .json or .csv")
    if not records:
        raise InvalidInput(f"dataset is empty: {path}")
    return records


def _sample_dataset() -> List[Dict[str, Any]]:
    """The packaged caption corpus, used when no dataset file is supplied."""
    sample = load_asset(SAMPLES).get("dataset_sample", {})
    records = sample.get("records", [])
    return [dict(r) for r in records]


def _dataset_records(path: Optional[str], caption_field: str) -> Tuple[List[Dict[str, Any]], str, bool]:
    """Return (records, source label, is_packaged_sample)."""
    if path:
        return _read_dataset(path, caption_field), core.rel_path(path), False
    records = _sample_dataset()
    if not records:
        raise InvalidInput("no dataset supplied and the packaged sample corpus is empty")
    return records, "asset-sample-prompts.json (packaged sample corpus)", True


def _caption_of(record: Dict[str, Any], field: str) -> str:
    for key in (field, "caption", "text", "description"):
        if key in record and record[key]:
            return str(record[key])
    return ""


def _lines(items: List[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


# ---------------------------------------------------------------------------
# Category 3 - Stable Diffusion core operations
# ---------------------------------------------------------------------------


def sd_setup_guide(ctx) -> Dict[str, Any]:
    vram = float(ctx.get("gpu_vram_gb", 12))
    interface = str(ctx.get("interface", "comfyui"))
    os_name = str(ctx.get("os", "linux"))
    tiers = load_asset(PROFILES)["gpu_tiers"]
    tier = next((t for t in tiers if vram <= t["vram_gb"]), tiers[-1])
    candidates = [m for m in load_asset(PROFILES)["models"] if m["min_vram_gb"] <= vram]
    if not candidates:
        candidates = [load_asset(PROFILES)["models"][0]]
    chosen = max(candidates, key=lambda m: (m["quality_score"], m["speed_score"]))
    steps = [
        {"step": 1, "action": "Install the runtime",
         "detail": "python3 -m venv .venv && source .venv/bin/activate; pin Python 3.10-3.12 for the widest wheel support"},
        {"step": 2, "action": "Install the interface",
         "detail": f"install the {interface} build against the exact torch build you chose"},
        {"step": 3, "action": "Place the models",
         "detail": f"keep checkpoints in one directory; {tier['max_model']} is the practical ceiling at {tier['vram_gb']} GB"},
        {"step": 4, "action": "Download models",
         "detail": f"start with {chosen['id']} ({chosen['name']}) and one ControlNet"},
        {"step": 5, "action": "Verify",
         "detail": "render a 512x512 test at 20 steps and confirm the output is not black, grey or tiled"},
        {"step": 6, "action": "Update safely",
         "detail": "snapshot the environment file before upgrading and keep the last known-good checkpoint"},
    ]
    body = "\n".join([
        f"Target: {interface} on {os_name} with {vram} GB VRAM (tier {tier['id']}: {tier['typical']})",
        "",
        md_table([[s["step"], s["action"], s["detail"]] for s in steps], ["Step", "Action", "Detail"]),
        "",
        f"Recommended starting model: {chosen['name']} ({chosen['id']}).",
        f"Hardware note: {tier['notes']}",
    ])
    data = {"vram_gb": vram, "interface": interface, "os": os_name, "tier": tier,
            "recommended_model": chosen["id"], "steps": steps}
    return _report(ctx, "sd-setup-guide", "Stable Diffusion setup guide", body, data,
                   f"Setup plan for {interface} with {vram} GB VRAM.")


def checkpoint_advisor(ctx) -> Dict[str, Any]:
    goal = str(ctx.get("goal", "photorealism"))
    budget = str(ctx.get("budget", "consumer"))
    profiles = load_asset(PROFILES)
    classes = profiles["checkpoint_classes"]
    if goal not in classes:
        raise InvalidInput(f"unknown goal '{goal}' (known: {', '.join(sorted(classes))})")
    models = [_model(mid) for mid in classes[goal]]
    rows = [[m["id"], m["name"], f'{m["base_resolution"]}px', f'{m["min_vram_gb"]} GB',
             m["seconds_per_image"], m["quality_score"], m["speed_score"], ", ".join(m["best_for"][:2])]
            for m in models]
    notes = [f"- {m['id']}: {', '.join(m['strengths'])}; watch out for {', '.join(m['weaknesses'])}" for m in models]
    body = "\n".join([
        f"Goal: {goal}; budget: {budget}",
        "",
        md_table(rows, ["Id", "Name", "Base res", "Min VRAM", "s/img", "Quality", "Speed", "Best for"]),
        "",
        "Selection notes:",
        _lines(notes),
        "",
        "Community checkpoints inherit the licence of their base model and may add restrictions - check the "
        "model card before commercial use.",
    ])
    data = {"goal": goal, "budget": budget, "ranking": classes[goal],
            "models": [{"id": m["id"], "name": m["name"], "min_vram_gb": m["min_vram_gb"],
                        "seconds_per_image": m["seconds_per_image"], "best_for": m["best_for"],
                        "weaknesses": m["weaknesses"]} for m in models]}
    return _report(ctx, "checkpoint-advisor", "Checkpoint selection advice", body, data,
                   f"Recommended checkpoints for {goal}: {', '.join(classes[goal])}.")


def sampler_tuner(ctx) -> Dict[str, Any]:
    model = str(ctx.get("model", "sdxl"))
    goal = str(ctx.get("goal", "quality"))
    current = str(ctx.get("sampler", "dpmpp_2m"))
    samplers = load_asset(PROFILES)["samplers"]
    goal_map = {"quality": ["dpmpp_2m", "heun", "dpmpp_sde"],
                "speed": ["dpmpp_2m", "euler", "ddim"],
                "anatomy": ["dpmpp_2m", "dpmpp_2m_sde", "euler_a"],
                "style": ["dpmpp_sde", "dpmpp_2m_sde", "dpmpp_2m"]}
    candidates = goal_map.get(goal, goal_map["quality"])
    grid = []
    for sampler_id in candidates:
        sampler = next((s for s in samplers if s["id"] == sampler_id), None)
        if not sampler:
            continue
        variants = ((sampler["steps"], sampler["cfg"]),
                    (int(sampler["steps"] * 0.7), sampler["cfg"]),
                    (int(sampler["steps"] * 1.4), sampler["cfg"] + 1.0))
        for steps, cfg in variants:
            grid.append({"sampler": sampler_id, "scheduler": sampler["scheduler"], "steps": steps, "cfg": cfg,
                         "seed": deterministic_seed(f"{model}-{sampler_id}-{steps}-{cfg}") % (2 ** 31),
                         "note": sampler["note"]})
    csv_path = os.path.join(ctx.out_dir, "sampler-grid.csv")
    core.ensure_parent(csv_path)
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sampler", "scheduler", "steps", "cfg", "seed", "note"])
        writer.writeheader()
        for row in grid:
            writer.writerow(row)
    ctx.artifacts.append(core.rel_path(csv_path))
    body = "\n".join([
        f"Model: {model}; goal: {goal}; current sampler: {current}",
        "",
        md_table([[g["sampler"], g["scheduler"], g["steps"], g["cfg"], g["seed"]] for g in grid],
                 ["Sampler", "Scheduler", "Steps", "CFG", "Seed"]),
        "",
        "Method:",
        "1. Hold the prompt, the seed and the resolution constant.",
        "2. Change exactly one variable per row.",
        "3. Render every row, then score them with the same rubric.",
        "4. Repeat the winner across three seeds before adopting it.",
        "",
        f"Grid written to {core.rel_path(csv_path)}.",
    ])
    data = {"model": model, "goal": goal, "current_sampler": current, "grid": grid,
            "csv": core.rel_path(csv_path)}
    return _report(ctx, "sampler-grid", "Sampler and scheduler tuning", body, data,
                   f"Built a {len(grid)}-row sampler comparison grid.",
                   {"grid_rows": len(grid)}, [core.rel_path(csv_path)])


def vae_manager(ctx) -> Dict[str, Any]:
    symptom = str(ctx.get("symptom", "black_image"))
    image = ctx.get("image")
    table = [
        {"vae": "model default (recommended)", "use": "keep it unless you have a reason",
         "risk": "none", "note": "SDXL ships a tuned VAE; a mismatch usually causes colour shifts"},
        {"vae": "EMA VAE", "use": "slightly smoother colour on SD1.5 checkpoints",
         "risk": "low", "note": "reduces texture slightly"},
        {"vae": "FT VAE / mse VAE", "use": "recovering detail in flat areas of SD1.5",
         "risk": "medium", "note": "can introduce black speckles in dark regions"},
        {"vae": "TAESD preview VAE", "use": "fast previews only", "risk": "high",
         "note": "lower fidelity; never use it for a final render"},
    ]
    symptoms = {
        "black_image": ["The checkpoint and the VAE belong to different model families - swap the VAE back.",
                        "Check that the VAE file is a full checkpoint and not a LoRA.",
                        "Confirm the text encoder loaded; a missing CLIP gives noise, not black."],
        "washed_colors": ["Switch back to the model default VAE.",
                          "Lower CFG by one and re-render.",
                          "Check that the image was not decoded with a mismatched colour profile."],
        "low_contrast": ["Compare against the model default VAE on the same seed.",
                          "Some boosted VAEs trade contrast for detail."],
        "noisy_output": ["The VAE decode is fine; the noise comes from the sampler, the steps or the precision.",
                          "Try fp16 with a fixed seed and 30 steps."],
        "blocky_output": ["Raise the resolution to the model's native size; VAE artefacts appear below it.",
                           "Check the attention backend - a broken install degrades quality."],
    }
    diagnosis = symptoms.get(symptom, symptoms["black_image"])
    measured = {}
    if image:
        info = core.probe_image(str(image))
        measured = {"size": [info["width"], info["height"]], "format": info["format"],
                    "bytes": info["bytes"]}
    body = "\n".join([
        f"Symptom: {symptom}",
        "",
        md_table([[v["vae"], v["use"], v["risk"], v["note"]] for v in table],
                 ["VAE", "Use it when", "Risk", "Note"]),
        "",
        "Diagnosis order:",
        _lines([f"{i + 1}. {d}" for i, d in enumerate(diagnosis)]),
    ])
    data = {"symptom": symptom, "table": table, "diagnosis": diagnosis, "measured": measured}
    return _report(ctx, "vae-diagnosis", "VAE selection and diagnosis", body, data,
                   f"VAE diagnosis for symptom '{symptom}'.")


def sdxl_workflow(ctx) -> Dict[str, Any]:
    aspect = str(ctx.get("aspect", "1:1"))
    refiner = bool(ctx.get("refiner", False))
    table = {"1:1": (1024, 1024), "3:2": (1216, 832), "2:3": (832, 1216), "16:9": (1344, 768), "9:16": (768, 1344)}
    if aspect not in table:
        raise InvalidInput(f"unknown aspect '{aspect}' (known: {', '.join(sorted(table))})")
    width, height = table[aspect]
    workflow = {
        "base": {"model": "sdxl_base.safetensors", "steps": 30, "cfg": 7.0, "sampler": "dpmpp_2m",
                 "scheduler": "karras", "width": width, "height": height, "clip_skip": 1},
        "refiner": ({"model": "sdxl_refiner_1.0.safetensors", "steps": 20, "cfg": 5.5, "switch_at": 0.8,
                     "note": "the refiner runs on the last 20% of the base pass"} if refiner else None),
        "hires": {"enabled": True, "upscaler": "4x-UltraSharp", "scale": 1.5, "steps": 12, "cfg": 6.0,
                  "denoise": 0.35},
        "memory": {"fp16": True, "attention_slicing": "only below 12 GB of VRAM",
                   "tiled_vae": "only when decoding above 1536 px"},
        "prompt_style": "SDXL responds better to long natural sentences than to comma-tag soup",
    }
    body = "\n".join([
        f"SDXL workflow - aspect {aspect} ({width}x{height})",
        "",
        md_table([[k, json.dumps(v) if isinstance(v, (dict, list)) else v] for k, v in workflow.items()],
                 ["Setting", "Value"]),
        "",
        "Guidance:",
        _lines([
            "Keep the base resolution at 1024 on the long edge; SDXL degrades below that.",
            "Use the refiner only when the base composition is already correct.",
            "Keep hires denoising at or below 0.4 or faces turn waxy.",
        ]),
    ])
    data = {"aspect": aspect, "size": [width, height], "workflow": workflow}
    return _report(ctx, "sdxl-workflow", "SDXL workflow", body, data,
                   f"Built an SDXL workflow for {aspect}.")


def sd3_workflow(ctx) -> Dict[str, Any]:
    variant = str(ctx.get("variant", "sd3-medium"))
    table = {
        "sd3-medium": ("sd3-medium.safetensors", 5.0, 5.2, 3, 7.2, 12),
        "sd3-large": ("sd3-large.safetensors", 8.0, 7.4, 4, 12.5, 20),
        "sd3.5-medium": ("sd3.5-medium.safetensors", 5.0, 5.2, 3, 7.5, 12),
        "sd3.5-large": ("sd3.5-large.safetensors", 8.0, 7.4, 4, 13.0, 20),
    }
    if variant not in table:
        raise InvalidInput(f"unknown variant '{variant}' (known: {', '.join(sorted(table))})")
    file_name, ckpt_gb, enc_gb, encoders, seconds, min_vram = table[variant]
    workflow = {
        "checkpoint": file_name, "text_encoders": encoders, "encoder_memory_gb": enc_gb,
        "resolution": [1024, 1024], "steps": 28, "cfg": 4.5, "sampler": "dpmpp_2m", "scheduler": "normal",
        "notes": [
            "SD3-class models use a multimodal diffusion transformer; the text encoders dominate memory.",
            "Keep guidance near 4.5 - higher values wash out detail.",
            "Use one well-written paragraph rather than a tag list.",
            "Long prompts are supported, so move constraints out of the negative prompt.",
        ],
    }
    body = "\n".join([
        f"Variant: {variant}",
        "",
        md_table([[k, json.dumps(v) if isinstance(v, (dict, list)) else v] for k, v in workflow.items()],
                 ["Setting", "Value"]),
        "",
        f"Memory estimate: checkpoint {ckpt_gb} GB + encoders {enc_gb} GB + VAE 0.33 GB; plan for at least "
        f"{min_vram} GB. Reference speed {seconds} s per 1024x1024 image.",
    ])
    data = {"variant": variant, "workflow": workflow, "checkpoint_gb": ckpt_gb, "encoder_gb": enc_gb,
            "min_vram_gb": min_vram, "seconds_per_image": seconds}
    return _report(ctx, "sd3-workflow", "Stable Diffusion 3 workflow", body, data,
                   f"Built a {variant} workflow.")


def webui_workflow(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    negative = ctx.get("negative") or ", ".join(load_asset(NEGATIVES)["targets"]["general"]["tokens"][:6])
    model = str(ctx.get("model", "sdxl-base"))
    presets = load_asset(TEMPLATES)["a1111_presets"]
    preset = dict(presets["txt2img"])
    preset["params"] = dict(preset["params"])
    preset["params"]["seed"] = deterministic_seed(prompt) % (2 ** 31)
    payload = {"positive prompt": prompt, "negative prompt": negative,
               "params": preset["params"], "notes": preset["notes"]}
    params = preset["params"]
    metadata_block = "\n".join([
        prompt,
        "Negative prompt: " + negative,
        "Steps: {}, Sampler: {}, CFG scale: {}, Seed: {}, Size: {}x{}, Model: {}".format(
            params["steps"], params["sampler_name"], params["cfg_scale"], params["seed"],
            params["width"], params["height"], model),
    ])
    params_path = os.path.join(ctx.out_dir, "webui-params.json")
    write_json(params_path, payload)
    meta_path = os.path.join(ctx.out_dir, "webui-metadata.txt")
    write_text(meta_path, metadata_block + "\n")
    script_path = os.path.join(ctx.out_dir, "webui-batch.sh")
    write_text(script_path, "\n".join([
        "#!/usr/bin/env bash",
        "# Batch render with Automatic1111 / Forge.",
        "# Usage: bash webui-batch.sh <checkpoint> <output-dir>",
        "set -euo pipefail",
        'CKPT="${1:-sdxl-base.safetensors}"',
        'OUT="${2:-output}"',
        'mkdir -p "$OUT"',
        'API="http://127.0.0.1:7860"',
        'while IFS= read -r line; do',
        '  echo "rendering: $line"',
        '  # POST /sdapi/v1/txt2img with the body in webui-params.json',
        'done < prompts.txt',
        "",
    ]))
    for path in (params_path, meta_path, script_path):
        ctx.artifacts.append(core.rel_path(path))
    body = "\n".join([
        "Automatic1111 / Forge parameter set:",
        "",
        md_table([[k, v] for k, v in params.items()], ["Parameter", "Value"]),
        "",
        f"Metadata block written to {core.rel_path(meta_path)} - paste it into the PNG info field.",
        "",
        "Reproducibility: the seed, sampler, scheduler, steps, CFG, size and model must all match.",
    ])
    artifacts = [core.rel_path(params_path), core.rel_path(meta_path), core.rel_path(script_path)]
    data = {"params": payload, "artifacts": artifacts}
    return _report(ctx, "webui-workflow", "WebUI workflow", body, data,
                   "Generated WebUI parameters, a metadata block and a batch script.", {}, artifacts)


def comfyui_graph(ctx) -> Dict[str, Any]:
    template = str(ctx.get("template", "txt2img"))
    prompt = str(ctx.get("prompt", "a cinematic photograph of a quiet harbour at dawn"))
    templates = load_asset(TEMPLATES)["comfyui_templates"]
    if template not in templates:
        raise InvalidInput(f"unknown template '{template}' (known: {', '.join(sorted(templates))})")
    nodes = json.loads(json.dumps(templates[template]["nodes"]))
    for node in nodes:
        for key, value in list(node["inputs"].items()):
            if isinstance(value, str) and value.startswith("{"):
                node["inputs"][key] = value.format(prompt=prompt, negative="")
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids)), "duplicate node ids in the template"
    links = []
    for node in nodes:
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
                if value[0] not in ids:
                    raise InvalidInput(f"template {template} links to a missing node {value[0]}")
                links.append([node["id"], value[0]])
    graph = {"nodes": nodes, "links": links, "extra": {"ds": {"scale": 0.8, "offset": [0, 0]}},
             "workflow_name": f"ai-image-toolkit/{template}"}
    path = os.path.join(ctx.out_dir, f"comfyui-{template}.json")
    write_json(path, graph)
    ctx.artifacts.append(core.rel_path(path))
    body = "\n".join([
        f"ComfyUI graph template: {template} ({len(nodes)} nodes, {len(links)} links)",
        "",
        md_table([[n["id"], n["type"], ", ".join(str(k) for k in n["inputs"])] for n in nodes],
                 ["Id", "Node type", "Inputs"]),
        "",
        "Validation: every node id is unique and every link points at an existing node.",
        "To use: load the JSON in ComfyUI, or POST it to /prompt in the API format.",
    ])
    data = {"template": template, "nodes": len(nodes), "links": len(links), "graph": graph,
            "path": core.rel_path(path)}
    return _report(ctx, "comfyui-graph", "ComfyUI graph", body, data,
                   f"Built a {template} ComfyUI graph with {len(nodes)} nodes.",
                   {"nodes": len(nodes)}, [core.rel_path(path)])


# ---------------------------------------------------------------------------
# Category 4 - Control and conditioning
# ---------------------------------------------------------------------------


def controlnet_orchestrator(ctx) -> Dict[str, Any]:
    goal = str(ctx.get("goal", "structure"))
    controls = [str(c).strip().lower() for c in (ctx.get("controls") or []) if str(c).strip()]
    library = load_asset(PROFILES)["controlnets"]
    if not controls:
        controls = ["canny"] if goal == "structure" else ["depth"]
    chosen = []
    for name in controls:
        entry = next((c for c in library if c["id"] == name or c["family"] == name), None)
        if entry is None:
            known = ", ".join(c["id"] for c in library)
            raise InvalidInput(f"unknown control type '{name}' (known: {known})")
        chosen.append(entry)
    total = sum(c["recommended_strength"] for c in chosen)
    rules = [
        "Two controls at full strength over-constrain the result; drop the second to about half.",
        "Keep the end_percent of a structural control below 0.85 so the last steps can resolve detail.",
        "A depth control plus a strong edge control cancels the depth signal - pick one source of geometry.",
    ]
    body = "\n".join([
        f"Goal: {goal}; controls: {', '.join(c['id'] for c in chosen)}",
        "",
        md_table([[c["id"], c["family"], c["recommended_strength"], f'{c["start"]}-{c["end"]}',
                   ", ".join(c["use_for"]), c["risk"]] for c in chosen],
                 ["Control", "Family", "Strength", "Start-End", "Use for", "Risk"]),
        "",
        f"Combined strength: {total:.2f}" + (" - too high, reduce the weakest control" if total > 1.3 else ""),
        "",
        "Rules:",
        _lines(rules),
    ])
    data = {"goal": goal,
            "controls": [{"id": c["id"], "family": c["family"], "strength": c["recommended_strength"],
                          "start": c["start"], "end": c["end"], "risk": c["risk"]} for c in chosen],
            "combined_strength": round(total, 2), "rules": rules}
    return _report(ctx, "controlnet-plan", "ControlNet orchestration plan", body, data,
                   f"Planned {len(chosen)} ControlNet(s) for {goal}.",
                   {"combined_strength": round(total, 2)})


def pose_specialist(ctx) -> Dict[str, Any]:
    pose_path = ctx.get("pose")
    joints = [j.strip() for j in str(ctx.get("joints", "")).split(",") if j.strip()]
    if not pose_path:
        body = "No pose file supplied - returning the validation checklist only."
        data = {"checklist": ["17 keypoints in COCO order", "left and right labels are mirrored correctly",
                              "limb lengths are plausible for the body size",
                              "no joint sits outside the frame unless that is intended"]}
        return _report(ctx, "pose-validation", "Pose conditioning validation", body, data,
                       "Returned the pose validation checklist (no pose file supplied).")
    try:
        payload = read_json(str(pose_path))
    except (InvalidInput, UnicodeDecodeError, ValueError):
        payload = None
    if payload is None:
        # A pose image (or any non-JSON file) was supplied: report on the image
        # and return the validation checklist instead of failing.
        info = {}
        try:
            info = core.probe_image(str(pose_path))
        except Exception:
            info = {}
        checklist = ["17 keypoints in COCO order",
                     "left and right labels are mirrored correctly",
                     "limb lengths are plausible for the body size",
                     "no joint sits outside the frame unless that is intended",
                     "the pose image resolution matches the generation resolution"]
        body = "\n".join([
            f"Pose input: {core.rel_path(str(pose_path))}",
            "",
            (f"Image size: {info.get('width')} x {info.get('height')} ({info.get('format')})"
             if info else "The pose input is not a readable image."),
            "",
            "A pose image was supplied rather than a keypoint list, so the keypoints cannot be "
            "validated numerically. Check them by eye against this list:",
            _lines(checklist),
        ])
        data = {"pose": core.rel_path(str(pose_path)), "image": info, "checklist": checklist,
                "keypoints": [], "issues": []}
        return _report(ctx, "pose-validation", "Pose conditioning validation", body, data,
                       "Returned the pose validation checklist for the supplied pose image.")
    if isinstance(payload, dict):
        keypoints = payload.get("keypoints") or payload.get("pose") or []
    else:
        keypoints = payload
    if not isinstance(keypoints, list):
        raise InvalidInput("pose file must contain a list of keypoints or a dict with a 'keypoints' list")
    points = []
    for i, kp in enumerate(keypoints):
        name = joints[i] if i < len(joints) else f"joint_{i}"
        if isinstance(kp, dict):
            points.append({"name": kp.get("name", name), "x": float(kp.get("x", 0)),
                           "y": float(kp.get("y", 0)),
                           "score": float(kp.get("score", kp.get("confidence", 1.0)))})
        elif isinstance(kp, (list, tuple)) and len(kp) >= 2:
            points.append({"name": name, "x": float(kp[0]), "y": float(kp[1]),
                           "score": float(kp[2]) if len(kp) > 2 else 1.0})
    issues = []
    for p in points:
        if p["score"] < 0.3:
            issues.append({"severity": "medium", "issue": f'{p["name"]} has low confidence ({p["score"]:.2f})'})
    named = {p["name"]: p for p in points}
    for a, b in (("lelb", "lwri"), ("relb", "rwri"), ("lknee", "lank"), ("rknee", "rank")):
        if a in named and b in named:
            pa, pb = named[a], named[b]
            if math.hypot(pb["x"] - pa["x"], pb["y"] - pa["y"]) < 1e-6:
                issues.append({"severity": "high", "issue": f"{a} and {b} coincide"})
    body = "\n".join([
        f"Pose file: {core.rel_path(str(pose_path))} with {len(points)} keypoints",
        "",
        md_table([[p["name"], p["x"], p["y"], p["score"]] for p in points],
                 ["Joint", "x", "y", "Confidence"]),
        "",
        "Issues:",
        _lines([f"[{i['severity']}] {i['issue']}" for i in issues]) if issues else "- none detected",
        "",
        "Application guidance:",
        _lines([
            "Keep the ControlNet strength at 0.8 or below when the pose itself is uncertain.",
            "Fix the pose before generating; a locked bad pose produces a locked bad figure.",
        ]),
    ])
    data = {"pose": core.rel_path(str(pose_path)), "keypoints": points, "issues": issues}
    return _report(ctx, "pose-validation", "Pose conditioning validation", body, data,
                   f"Validated {len(points)} pose keypoints; {len(issues)} issue(s).",
                   {"issues": len(issues)})


def depth_composer(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    depth_path = str(ctx.require("depth"))
    image = core.probe_image(image_path)
    depth = core.probe_image(depth_path)
    issues = []
    if (image["width"], image["height"]) != (depth["width"], depth["height"]):
        issues.append("the depth map and the source image have different dimensions - resize the depth map")
    if depth["extension"] not in ("png", "tif", "tiff"):
        issues.append("a PNG or TIFF depth map is preferred; JPEG compresses the depth steps")
    plan = {
        "control": "depth (depth_midas or depth_zoe)",
        "strength": 0.55, "start_percent": 0.0, "end_percent": 0.7, "denoise": 0.55,
        "why": "depth preserves the layout while allowing the surface and the light to be restyled",
        "rules": ["Do not use a depth control together with a strong edge control.",
                  "Keep the denoise window so the background is restyled but the geometry is not redrawn.",
                  "Check that near objects are brighter than far ones before rendering."],
    }
    body = "\n".join([
        f"Image: {core.rel_path(image_path)} ({image['width']}x{image['height']})",
        f"Depth: {core.rel_path(depth_path)} ({depth['width']}x{depth['height']}, {depth['format']})",
        "",
        md_table([[k, v if not isinstance(v, list) else "; ".join(v)] for k, v in plan.items()],
                 ["Setting", "Value"]),
        "",
        "Issues:",
        _lines(issues) if issues else "- none detected",
    ])
    data = {"image": core.rel_path(image_path), "depth": core.rel_path(depth_path),
            "issues": issues, "plan": plan}
    return _report(ctx, "depth-composition", "Depth-guided composition plan", body, data,
                   "Prepared a depth-guided restyle plan.")


def edge_controller(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    edges_path = str(ctx.require("edges"))
    mode = str(ctx.get("mode", "canny"))
    image = core.probe_image(image_path)
    edges = core.probe_image(edges_path)
    issues = []
    if (image["width"], image["height"]) != (edges["width"], edges["height"]):
        issues.append("the edge map and the source image have different dimensions - resize the edge map")
    if edges["extension"] not in ("png", "tif", "tiff"):
        issues.append("use a PNG edge map; JPEG artefacts become visible as false edges")
    library = load_asset(PROFILES)["controlnets"]
    entry = next((c for c in library if c["id"] == mode), None)
    notes = {
        "canny": "low thresholds keep interior detail; high thresholds keep only the silhouette",
        "hed": "a softer edge map preserves more freedom in texture",
        "softedge": "good for restyling without locking the line work",
        "lineart": "locks the drawing; keep the strength high so the line style survives",
        "scribble": "quick sketch control; expect stray strokes",
    }
    plan = {
        "control": entry["id"] if entry else mode,
        "strength": entry["recommended_strength"] if entry else 0.7,
        "start_percent": entry["start"] if entry else 0.0,
        "end_percent": entry["end"] if entry else 0.8,
        "pixel_perfect": True,
        "notes": notes.get(mode, ""),
        "over_constraining_check": "compare the edge map against the source at 200%: every contour should "
                                   "follow a real edge in the photograph",
    }
    body = "\n".join([
        f"Image: {core.rel_path(image_path)}; edges: {core.rel_path(edges_path)}; mode: {mode}",
        "",
        md_table([[k, v] for k, v in plan.items()], ["Setting", "Value"]),
        "",
        "Issues:",
        _lines(issues) if issues else "- none detected",
    ])
    data = {"image": core.rel_path(image_path), "edges": core.rel_path(edges_path), "mode": mode,
            "plan": plan, "issues": issues}
    return _report(ctx, "edge-control", "Edge and line-art control plan", body, data,
                   f"Prepared a {mode} edge control plan.")


def segmentation_generator(ctx) -> Dict[str, Any]:
    seg_path = str(ctx.require("segmentation"))
    classes = str(ctx.get("classes", ""))
    info = core.probe_image(seg_path)
    labels = []
    for chunk in classes.split(","):
        if ":" in chunk:
            cid, label = chunk.split(":", 1)
            labels.append({"id": cid.strip(), "label": label.strip()})
    if not labels:
        labels = [{"id": "0", "label": "background"}, {"id": "1", "label": "subject"}]
    regions = []
    for i, label in enumerate(labels):
        regions.append({
            "class_id": label["id"], "label": label["label"],
            "prompt": f"{label['label']}, rendered with the style and the lighting of the whole scene",
            "control": "seg", "strength": 0.6 if label["id"] != "0" else 0.4, "priority": i,
            "notes": "keep the class region semantically identical; only the rendering changes",
        })
    body = "\n".join([
        f"Segmentation map: {core.rel_path(seg_path)} ({info['width']}x{info['height']})",
        "",
        md_table([[r["class_id"], r["label"], r["strength"], r["prompt"]] for r in regions],
                 ["Class", "Label", "Strength", "Regional instruction"]),
        "",
        "Workflow:",
        _lines([
            "Keep the class map as the control image; do not render from a text prompt alone.",
            "Apply the control to the base pass only, not to the hires pass.",
            "Check that class boundaries did not bleed between adjacent regions.",
        ]),
    ])
    data = {"segmentation": core.rel_path(seg_path), "classes": labels, "regions": regions}
    return _report(ctx, "segmentation-generation", "Segmentation-guided generation plan", body, data,
                   f"Built {len(regions)} regional instructions from the segmentation map.")


def ip_adapter_specialist(ctx) -> Dict[str, Any]:
    references = ctx.paths("references")
    role = str(ctx.get("role", "style"))
    prompt_weight = float(ctx.get("prompt_weight", 1.0))
    weights = {"style": 0.7, "face": 0.85, "object": 0.8, "composition": 0.55, "palette": 0.45}
    base = weights.get(role, 0.6)
    adapted = max(0.1, min(1.0, base * prompt_weight))
    body = "\n".join([
        f"Role: {role}; reference images: {len(references)}; prompt weight: {prompt_weight}",
        f"Recommended adapter weight: {adapted:.2f}",
        "",
        md_table([[k, v] for k, v in weights.items()], ["Reference role", "Typical weight"]),
        "",
        "Rules:",
        _lines([
            "The adapter weight trades reference fidelity against prompt control; above 0.9 the prompt is ignored.",
            "Use one reference per role. Two face references fight each other.",
            "For a face reference, lower the overall adapter weight and raise the face block weight.",
            "Start the prompt with the subject, because the adapter and the prompt compete for the same regions.",
        ]),
    ])
    data = {"role": role, "references": [core.rel_path(r) for r in references],
            "recommended_weight": round(adapted, 2), "prompt_weight": prompt_weight, "weights": weights}
    return _report(ctx, "ip-adapter-plan", "IP-Adapter plan", body, data,
                   f"Recommended IP-Adapter weight {adapted:.2f} for a {role} reference.")


def regional_prompting(ctx) -> Dict[str, Any]:
    spec = str(ctx.get("regions", ""))
    layout = str(ctx.get("layout", "quadrants"))
    regions = []
    for chunk in [c for c in spec.split(";") if c.strip()]:
        if ":" not in chunk:
            raise InvalidInput(f"region must be 'x,y,w,h:prompt': {chunk!r}")
        box, prompt = chunk.split(":", 1)
        coords = [float(v) for v in box.split(",")]
        if len(coords) != 4:
            raise InvalidInput(f"region box needs x,y,w,h: {chunk!r}")
        regions.append({"box": coords, "prompt": prompt.strip(),
                        "mask_prompt": f"({prompt.strip()}:1.1)"})
    if not regions:
        layout_boxes = {
            "quadrants": [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5), (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
            "thirds": [(0, 0, 0.33, 0.33), (0.67, 0, 0.33, 0.33), (0, 0.67, 0.33, 0.33), (0.67, 0.67, 0.33, 0.33)],
            "center": [(0.25, 0.25, 0.5, 0.5)],
        }.get(layout)
        if layout_boxes is None:
            raise InvalidInput(f"unknown layout '{layout}' (use quadrants, thirds or center)")
        regions = [{"box": list(b), "prompt": "region subject", "mask_prompt": "(region subject:1.1)"}
                   for b in layout_boxes]
    body = "\n".join([
        f"Layout: {layout}; regions: {len(regions)}",
        "",
        md_table([[str(r["box"]), r["prompt"], r["mask_prompt"]] for r in regions],
                 ["Box (x,y,w,h)", "Region prompt", "Weighted form"]),
        "",
        "Rules:",
        _lines([
            "Regions must not overlap, or the boundary becomes a visible seam.",
            "Give each region a distinct subject and attribute; identical prompts merge.",
            "Keep a margin of at least 5% between regions so the blur does not bleed.",
            "In ComfyUI use area conditioning nodes instead of inline weighting.",
        ]),
    ])
    data = {"layout": layout, "regions": regions}
    return _report(ctx, "regional-prompting", "Regional prompting plan", body, data,
                   f"Defined {len(regions)} regional prompt region(s).")


def control_conflict_resolver(ctx) -> Dict[str, Any]:
    spec = str(ctx.get("controls", ""))
    controls = []
    for chunk in [c for c in spec.split(";") if c.strip()]:
        parts = chunk.split(":")
        if len(parts) < 3:
            raise InvalidInput(f"control must be 'name:type:strength[:start:end]': {chunk!r}")
        controls.append({
            "name": parts[0], "type": parts[1], "strength": float(parts[2]),
            "start": float(parts[3]) if len(parts) > 3 else 0.0,
            "end": float(parts[4]) if len(parts) > 4 else 1.0,
        })
    if not controls:
        controls = [{"name": "edges", "type": "canny", "strength": 0.7, "start": 0.0, "end": 0.8},
                    {"name": "pose", "type": "openpose", "strength": 0.8, "start": 0.0, "end": 0.9}]
    total = sum(c["strength"] for c in controls)
    conflicts = []
    if total > 1.3:
        conflicts.append({"conflict": "combined strength",
                          "detail": f"total {total:.2f} over-constrains the render",
                          "fix": "drop the weakest control to about 0.5"})
    geometry = {"depth", "normal", "canny", "lineart", "openpose", "seg"}
    if len([c for c in controls if c["type"] in geometry]) > 1:
        conflicts.append({"conflict": "competing geometry",
                          "detail": "more than one structural control is active",
                          "fix": "keep depth for layout and pose for the figure, or reduce both strengths"})
    for c in controls:
        if c["end"] - c["start"] > 0.95 and c["strength"] > 0.8:
            conflicts.append({"conflict": f'{c["name"]} active for the whole pass',
                              "detail": "a strong control across the full window locks the result",
                              "fix": "end the control around 0.8 so the final steps resolve detail"})
    body = "\n".join([
        md_table([[c["name"], c["type"], c["strength"], f'{c["start"]}-{c["end"]}'] for c in controls],
                 ["Control", "Type", "Strength", "Window"]),
        "",
        f"Combined strength: {total:.2f}",
        "",
        "Conflicts:",
        _lines([f"{c['conflict']}: {c['detail']} -> {c['fix']}" for c in conflicts])
        if conflicts else "- none detected",
        "",
        "Resolution order: reduce strengths first, then shorten the windows, then drop the lowest priority control.",
    ])
    data = {"controls": controls, "total_strength": round(total, 2), "conflicts": conflicts}
    return _report(ctx, "control-conflicts", "Control conflict resolution", body, data,
                   f"Found {len(conflicts)} conditioning conflict(s).",
                   {"total_strength": round(total, 2)})


# ---------------------------------------------------------------------------
# Category 9, 10 and 12 - remaining production-side helpers
# ---------------------------------------------------------------------------


def upscaling_advisor(ctx) -> Dict[str, Any]:
    target_w = int(ctx.get("target_width", 2048))
    target_h = int(ctx.get("target_height", 2048))
    image_path = ctx.get("image")
    src_w = src_h = 1024
    if image_path:
        info = core.probe_image(str(image_path))
        src_w, src_h = info["width"], info["height"]
    factor = min(target_w / src_w, target_h / src_h)
    methods = [
        {"method": "latent upscale (diffusion)", "best_for": "textures, skin, product surfaces",
         "cost": "high", "risk": "invents logos and fine text"},
        {"method": "ESRGAN-class upscaler", "best_for": "edges, architecture, line art",
         "cost": "low", "risk": "softens organic texture"},
        {"method": "face-aware upscaler", "best_for": "portraits", "cost": "medium",
         "risk": "identity drift when the face is small"},
        {"method": "conventional resample", "best_for": "already large sources",
         "cost": "very low", "risk": "soft image, no new detail"},
    ]
    if factor <= 1.2:
        chosen = "conventional resample"
    elif factor <= 2:
        chosen = "latent upscale (diffusion)"
    else:
        chosen = "ESRGAN-class upscaler"
    body = "\n".join([
        f"Source {src_w}x{src_h} -> target {target_w}x{target_h} (scale {factor:.2f}x)",
        f"Recommended: {chosen}",
        "",
        md_table([[m["method"], m["best_for"], m["cost"], m["risk"]] for m in methods],
                 ["Method", "Best for", "Cost", "Risk"]),
        "",
        "Rules:",
        _lines([
            "Above 2x, run the upscale in two stages of at most 2x each.",
            "Never upscale a face that is already below about 64 px; the result will be invented.",
            "Keep the original pixels for the second pass so the final file is not re-encoded twice.",
        ]),
    ])
    data = {"source": [src_w, src_h], "target": [target_w, target_h], "factor": round(factor, 3),
            "recommended": chosen, "methods": methods}
    return _report(ctx, "upscaling-advice", "Upscaling method selection", body, data,
                   f"Recommended {chosen} for a {factor:.2f}x upscale.", {"factor": round(factor, 3)})


def detail_recovery(ctx) -> Dict[str, Any]:
    subject = str(ctx.get("subject", "product"))
    guidance = {
        "product": {"protect": ["logo", "label text", "serial markings", "material seams"],
                    "recover": ["brushed grain", "knurling", "surface weave", "micro-scratches"]},
        "portrait": {"protect": ["identity features", "pores", "freckles", "hairline"],
                     "recover": ["hair strand detail", "iris texture", "fabric weave"]},
        "landscape": {"protect": ["horizon", "building geometry", "signage"],
                      "recover": ["foliage detail", "rock texture", "cloud structure"]},
        "architecture": {"protect": ["window mullions", "brick courses", "signage"],
                         "recover": ["material grain", "mortar texture", "foliage"]},
    }.get(subject, {"protect": ["identity and text"], "recover": ["surface texture"]})
    body = "\n".join([
        f"Subject type: {subject}",
        "",
        md_table([["protect", ", ".join(guidance["protect"])], ["recover", ", ".join(guidance["recover"])]],
                 ["Category", "Elements"]),
        "",
        "Rules:",
        _lines([
            "Mask protected elements before the detail pass; repair them afterwards with a low-denoise pass.",
            "A detail pass that invents a logo is worse than a soft image - re-render instead.",
            "Compare the detail pass against the original at 200% before accepting it.",
        ]),
    ])
    data = {"subject": subject, "guidance": guidance}
    return _report(ctx, "detail-recovery", "Detail recovery plan", body, data,
                   f"Defined protected and recoverable detail for a {subject}.")


def face_restoration(ctx) -> Dict[str, Any]:
    subject = str(ctx.get("subject", "portrait"))
    goal = str(ctx.get("goal", "preservation"))
    settings = {
        "preservation": {"strength": 0.3, "fidelity": 0.9, "restore_face": True,
                         "warning": "keep fidelity above 0.8 so the face still matches the person"},
        "balanced": {"strength": 0.5, "fidelity": 0.7, "restore_face": True,
                     "warning": "moderate risk of a slightly different face"},
        "beautification": {"strength": 0.8, "fidelity": 0.45, "restore_face": True,
                           "warning": "identity drift is likely - disclose the edit or do not ship it"},
    }
    chosen = settings.get(goal, settings["preservation"])
    body = "\n".join([
        f"Goal: {goal}; subject: {subject}",
        "",
        md_table([[k, v] for k, v in chosen.items()], ["Setting", "Value"]),
        "",
        "Checks after the pass:",
        _lines([
            "Compare eye spacing, nose width and jawline with the source.",
            "Look for plastic skin: no pores, no natural asymmetry, uniform specular response.",
            "Confirm the catchlight still matches the scene light.",
            "If the identity moved, lower the strength and re-run rather than accepting the result.",
        ]),
    ])
    data = {"goal": goal, "subject": subject, "settings": chosen}
    return _report(ctx, "face-restoration", "Face restoration control", body, data,
                   f"Face restoration set to {goal} (strength {chosen['strength']}).")


def print_resolution_planner(ctx) -> Dict[str, Any]:
    w_px = int(ctx.require("width_px"))
    h_px = int(ctx.require("height_px"))
    dpi = int(ctx.get("dpi", 300))
    bleed_mm = float(ctx.get("bleed_mm", 3.0))
    inches_w = w_px / dpi
    inches_h = h_px / dpi
    mm_w = inches_w * 25.4
    mm_h = inches_h * 25.4
    targets = []
    for label, need_dpi in (("poster_large", 150), ("poster_small", 300), ("book", 300),
                            ("packaging", 300), ("large_format", 120)):
        max_w = round(mm_w * need_dpi / 25.4)
        max_h = round(mm_h * need_dpi / 25.4)
        targets.append({"target": label, "dpi": need_dpi, "max_pixels": [max_w, max_h],
                        "fits": max_w <= w_px and max_h <= h_px})
    bleed_px = round(bleed_mm / 25.4 * dpi)
    viewing_mm = round(min(mm_w, mm_h) / 30.0, 1)
    body = "\n".join([
        f"Source: {w_px}x{h_px} px at {dpi} dpi = {inches_w:.2f} x {inches_h:.2f} in ({mm_w:.1f} x {mm_h:.1f} mm)",
        f"Bleed: {bleed_mm} mm = {bleed_px} px per side",
        "",
        md_table([[t["target"], t["dpi"], f'{t["max_pixels"][0]}x{t["max_pixels"][1]}',
                   "fits" if t["fits"] else "too small"] for t in targets],
                 ["Print target", "Required dpi", "Max pixels", "Verdict"]),
        "",
        f"Minimum legible type height at 0.5 m viewing distance: about {viewing_mm} mm.",
    ])
    data = {"pixels": [w_px, h_px], "dpi": dpi, "inches": [round(inches_w, 2), round(inches_h, 2)],
            "mm": [round(mm_w, 1), round(mm_h, 1)], "bleed_px": bleed_px, "targets": targets,
            "viewing_mm": viewing_mm}
    return _report(ctx, "print-resolution", "Print resolution plan", body, data,
                   f"Print plan: {w_px}x{h_px} px supports {inches_w:.1f}x{inches_h:.1f} in at {dpi} dpi.",
                   {"max_inches": round(inches_w, 2)})


def output_color_manager(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    target = str(ctx.get("target", "srgb"))
    info = core.probe_image(image_path)
    spaces = {
        "srgb": {"icc": "sRGB IEC61966-2.1", "use": "web, social, email and most screens",
                 "conversion": "embed the sRGB profile; never convert the values twice"},
        "display_p3": {"icc": "Display P3", "use": "wide-gamut displays and Apple platforms",
                       "conversion": "convert from sRGB into P3 in a colour-managed pipeline"},
        "cmyk_oriented": {"icc": "no ICC for CMYK", "use": "print previews and soft proofs",
                          "conversion": "keep the RGB master and produce a separate CMYK conversion"},
        "hdr_pq": {"icc": "ITU-R BT.2100 PQ", "use": "HDR master delivery",
                   "conversion": "needs a 16-bit float pipeline; an 8-bit PNG cannot carry PQ"},
        "print_preview": {"icc": "sRGB with a soft-proof profile", "use": "internal approval proofs",
                          "conversion": "apply the printer profile to a simulation, never to the master"},
    }
    if target not in spaces:
        raise InvalidInput(f"unknown target '{target}' (known: {', '.join(sorted(spaces))})")
    space = spaces[target]
    body = "\n".join([
        f"Image: {core.rel_path(image_path)} ({info['width']}x{info['height']}, {info['format']})",
        f"ICC profile embedded: {'yes' if info.get('icc_profile') else 'no'}",
        "",
        md_table([[k, v] for k, v in space.items()], ["Aspect", "Value"]),
        "",
        "Workflow:",
        _lines([
            "Keep the original RGB master untouched.",
            "Convert in a colour-managed application, never by editing channel values by hand.",
            "Embed the target profile and verify by reopening the converted file.",
            "For print, supply a soft proof and let the printer do the final conversion.",
        ]),
    ])
    data = {"image": core.rel_path(image_path), "target": target, "space": space,
            "icc_embedded": bool(info.get("icc_profile"))}
    return _report(ctx, "output-colour", "Output colour management", body, data,
                   f"Prepared a {target} delivery plan.")


def ethical_portrait_auditor(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    records_path = ctx.get("consent_records")
    rules = load_asset(SAFETY)["consent"]
    checklist = [
        {"id": "consent", "severity": "critical",
         "question": "Is there a written consent record for this person?"},
        {"id": "scope", "severity": "critical",
         "question": "Does the consent cover this specific use and channel?"},
        {"id": "expiry", "severity": "high", "question": "Is the consent valid at publication time?"},
        {"id": "minors", "severity": "critical",
         "question": "Could the subject be under 18? Then guardian consent and a second reviewer are required."},
        {"id": "sensitive", "severity": "high",
         "question": "Is the context medical, political, religious, sexual, criminal or financial?"},
        {"id": "impersonation", "severity": "critical",
         "question": "Could the image be read as evidence of something the subject did not do?"},
        {"id": "disclosure", "severity": "medium",
         "question": "Is the synthetic nature disclosed where required?"},
        {"id": "records", "severity": "medium",
         "question": "Is the consent record stored and retrievable after publication?"},
    ]
    record_status = {}
    if records_path:
        payload = read_json(str(records_path))
        entries = payload if isinstance(payload, list) else payload.get("records", [])
        record_status = {
            "count": len(entries),
            "missing_fields": sorted({f for entry in entries
                                      for f in rules["required_records"] if f not in entry}),
        }
    risks = []
    text = subject.lower()
    if any(w in text for w in ("child", "teen", "minor", "student", "school")):
        risks.append("the subject may be a minor - apply the strict age-safe path")
    if any(w in text for w in ("medical", "hospital", "patient", "diagnosis")):
        risks.append("a medical context needs non-diagnostic framing and privacy review")
    if any(w in text for w in ("politician", "ceo", "employee", "boss")):
        risks.append("a workplace or public-figure context raises impersonation and defamation risk")
    body = "\n".join([
        f"Subject: {subject}",
        "",
        md_table([[c["id"], c["severity"], c["question"]] for c in checklist],
                 ["Check", "Severity", "Question"]),
        "",
        "Consent records:",
        (f"{record_status.get('count')} record(s); missing fields: "
         f"{', '.join(record_status.get('missing_fields', [])) or 'none'}") if record_status
        else "no consent record file supplied",
        "",
        "Flags:",
        _lines(risks) if risks else "- no automatic flags raised",
    ])
    data = {"subject": subject, "checklist": checklist, "records": record_status, "flags": risks}
    return _report(ctx, "ethical-portrait-audit", "Ethical portrait audit", body, data,
                   f"Audited portrait consent requirements ({len(risks)} flag(s)).",
                   {"flags": len(risks)})


def ecommerce_producer(ctx) -> Dict[str, Any]:
    product = str(ctx.require("product"))
    marketplace = str(ctx.get("marketplace", "generic"))
    policies = load_asset(TEMPLATES)["marketplaces"]
    if marketplace not in policies:
        raise InvalidInput(f"unknown marketplace '{marketplace}' (known: {', '.join(sorted(policies))})")
    policy = policies[marketplace]
    shots = [
        {"shot": "main image", "framing": "front three-quarter, product 85-90% of the frame",
         "background": "pure white", "shadow": "soft contact shadow only", "notes": "no props, no text"},
        {"shot": "straight front", "framing": "orthographic-feeling front view",
         "background": "pure white", "shadow": "minimal", "notes": "for catalogues that require symmetry"},
        {"shot": "angle set", "framing": "left, right and top-down at the same scale",
         "background": "pure white", "shadow": "consistent", "notes": "keep the product the same size in every frame"},
        {"shot": "detail", "framing": "tight crop on one feature",
         "background": "pure white", "shadow": "none", "notes": "material and mechanism detail"},
        {"shot": "scale", "framing": "product next to a known object",
         "background": "pure white", "shadow": "soft", "notes": "gives the buyer a size reference"},
        {"shot": "lifestyle", "framing": "in a real context",
         "background": "contextual", "shadow": "natural", "notes": "usually a secondary image"},
    ]
    body = "\n".join([
        f"Product: {product}; marketplace: {policy['label']}",
        f"Image rules: {policy['min_width']}-{policy['max_width']} px wide, {policy['formats']}, "
        f"max {policy['max_bytes']} bytes, aspect {policy['min_aspect']}-{policy['max_aspect']}",
        "",
        md_table([[s["shot"], s["framing"], s["background"], s["shadow"], s["notes"]] for s in shots],
                 ["Shot", "Framing", "Background", "Shadow", "Notes"]),
        "",
        "Consistency rules:",
        _lines([
            "One white balance and one shadow direction across the whole set.",
            "The product occupies the same share of the frame in every angle.",
            "Retouching must not change the product geometry or the branding.",
        ]),
    ])
    data = {"product": product, "marketplace": marketplace, "policy": policy, "shots": shots}
    return _report(ctx, "ecommerce-standards", "E-commerce image standards", body, data,
                   f"Defined {len(shots)} listing shots for {product}.")


def floor_plan_perspective(ctx) -> Dict[str, Any]:
    width_m = float(ctx.require("width_m"))
    depth_m = float(ctx.require("depth_m"))
    height_m = float(ctx.get("height_m", 2.8))
    options = []
    for fov in (50, 65, 80, 100):
        focal = (width_m / 2) / math.tan(math.radians(fov) / 2)
        options.append({
            "fov_deg": fov,
            "focal_length_mm_equiv": round(18 / math.tan(math.radians(fov) / 2), 1),
            "distance_m": round(depth_m + focal, 2),
            "fits_width": width_m <= 2 * focal * math.tan(math.radians(fov) / 2),
        })
    camera_height = round(height_m * 0.55, 2)
    prompt = (f"interior perspective of a {width_m:.1f} by {depth_m:.1f} metre room, ceiling height "
              f"{height_m:.2f} metres, camera at {camera_height} metres height in the corner of the room, "
              f"one-point perspective, level horizon, correct vertical scale, daylight from a single window")
    body = "\n".join([
        f"Room: {width_m:.1f} x {depth_m:.1f} m, ceiling {height_m:.2f} m",
        "",
        md_table([[o["fov_deg"], o["focal_length_mm_equiv"], o["distance_m"], o["fits_width"]]
                  for o in options], ["FOV deg", "35mm equivalent", "Camera distance m", "Fits width"]),
        "",
        f"Recommended camera height: {camera_height} m (about 55% of the ceiling height).",
        "",
        f"Prompt:\n{prompt}",
        "",
        "Rules:",
        _lines([
            "Name the camera position explicitly; a model cannot infer where you stand.",
            "Keep the horizon level: a tilted horizon is the most common architectural error.",
            "Do not invent doors or connections that the plan does not show.",
        ]),
    ])
    data = {"room_m": [width_m, depth_m], "height_m": height_m, "options": options,
            "camera_height_m": camera_height, "prompt": prompt}
    return _report(ctx, "floor-plan-perspective", "Floor plan to perspective plan", body, data,
                   f"Derived a {width_m:.1f}x{depth_m:.1f} m perspective setup.")


def medical_safety_reviewer(ctx) -> Dict[str, Any]:
    description = str(ctx.require("description"))
    checks = [
        {"id": "plausibility", "severity": "high",
         "question": "Is the anatomy plausible for the described structure?"},
        {"id": "non_diagnostic", "severity": "critical",
         "question": "Is the image labelled as educational rather than diagnostic?"},
        {"id": "privacy", "severity": "critical", "question": "Is any patient-identifiable detail removed?"},
        {"id": "uncertainty", "severity": "high", "question": "Is the variability of the real anatomy stated?"},
        {"id": "labels", "severity": "high", "question": "Are labels typeset outside the image and correct?"},
        {"id": "scale", "severity": "medium", "question": "Is a scale bar or size reference present where relevant?"},
        {"id": "colour", "severity": "medium", "question": "Is any false colour explained?"},
        {"id": "consent", "severity": "critical",
         "question": "If a real patient contributed, is consent recorded?"},
    ]
    flags = []
    text = description.lower()
    if any(w in text for w in ("patient", "scan", "mri", "ct", "x-ray", "clinical")):
        flags.append("clinical imagery - ensure it is labelled educational and non-diagnostic")
    if any(w in text for w in ("tumour", "tumor", "lesion", "disease", "cancer")):
        flags.append("pathology depicted - add an explicit uncertainty and variability note")
    body = "\n".join([
        f"Description: {description}",
        "",
        md_table([[c["id"], c["severity"], c["question"]] for c in checks],
                 ["Check", "Severity", "Question"]),
        "",
        "Flags:",
        _lines(flags) if flags else "- none raised automatically",
        "",
        "Disclosure line to place with the image:",
        "\"Educational illustration produced with generative AI. Not a diagnostic image and not a substitute "
        "for professional medical advice.\"",
    ])
    data = {"description": description, "checks": checks, "flags": flags}
    return _report(ctx, "medical-safety-review", "Medical visualisation safety review", body, data,
                   f"Reviewed medical imagery safety ({len(flags)} flag(s)).")


def microscopy_enhancement(ctx) -> Dict[str, Any]:
    image_path = ctx.get("image")
    modality = str(ctx.get("modality", "brightfield"))
    measured = {}
    if image_path:
        info = core.probe_image(str(image_path))
        measured = {"size": [info["width"], info["height"]], "format": info["format"], "bytes": info["bytes"]}
    plan = {
        "denoise": "median filter at radius 1-2 px; record the kernel and the seed",
        "contrast": "local histogram equalisation or CLAHE with a clip limit of 2%",
        "segmentation": "threshold on the denoised image, never on the raw data",
        "colorisation": "use a fixed lookup table so the same structure always maps to the same colour",
        "quantitative_integrity": "keep the raw pixels; write the enhanced version to a new file",
        "log": "record every operation, its parameters and the software version",
    }
    body = "\n".join([
        f"Modality: {modality}" + (f"; source {measured}" if measured else "; no source image supplied"),
        "",
        md_table([[k, v] for k, v in plan.items()], ["Step", "Specification"]),
        "",
        "Integrity rules:",
        _lines([
            "Any measurement must be taken from the raw data, never from the enhanced image.",
            "Keep the bit depth unchanged when the data is quantitative.",
            "Record the transformation log next to the output.",
        ]),
    ])
    data = {"modality": modality, "plan": plan, "measured": measured}
    return _report(ctx, "microscopy-enhancement", "Microscopy enhancement plan", body, data,
                   f"Planned a {modality} enhancement workflow.")


def technical_accuracy_auditor(ctx) -> Dict[str, Any]:
    image_path = ctx.get("image")
    spec = str(ctx.require("spec"))
    checks = [
        {"id": "dimensions", "check": "every dimension on the image matches the specification"},
        {"id": "labels", "check": "every label is correct and legible"},
        {"id": "orientation", "check": "the orientation and the handedness are right"},
        {"id": "materials", "check": "the materials shown are the specified materials"},
        {"id": "count", "check": "the number of components is correct"},
        {"id": "scale", "check": "the relative scale of the parts is consistent"},
        {"id": "arrows", "check": "arrows and callouts point at the right features"},
        {"id": "discrepancy", "check": "anything not described in the specification is flagged, not silently kept"},
    ]
    status = "pending review"
    if image_path:
        info = core.probe_image(str(image_path))
        status = f"image inspected ({info['width']}x{info['height']}); each check still needs a human pass"
    body = "\n".join([
        f"Specification: {spec}",
        "",
        md_table([[c["id"], c["check"], "pending"] for c in checks], ["Id", "Check", "Status"]),
        "",
        f"Overall: {status}",
        "",
        "This capability structures the comparison and records the outcome; a human reviewer confirms each "
        "item against the source specification.",
    ])
    data = {"spec": spec, "checks": checks, "status": status}
    return _report(ctx, "technical-accuracy", "Technical accuracy audit", body, data,
                   "Prepared a technical accuracy audit against the specification.")


# ---------------------------------------------------------------------------
# Category 16 - Training and fine-tuning
# ---------------------------------------------------------------------------


def dataset_curation(ctx) -> Dict[str, Any]:
    caption_field = str(ctx.get("caption_field", "caption"))
    records, dataset_label, packaged = _dataset_records(ctx.get("dataset"), caption_field)
    dataset_path = dataset_label
    captions = [_caption_of(r, caption_field) for r in records]
    lengths = [len(c.split()) for c in captions]
    seen = {}
    duplicates = []
    for i, caption in enumerate(captions):
        key = core.stable_hash(caption, 16)
        if key in seen:
            duplicates.append({"index": i, "first_seen": seen[key], "caption": captions[i][:80]})
        else:
            seen[key] = i
    unique = {}
    for record, caption in zip(records, captions):
        unique.setdefault(core.stable_hash(caption, 16), {"record": record, "caption": caption})
    curated = list(unique.values())
    word_counts = {}
    for caption in captions:
        for word in re.findall(r"[a-z']+", caption.lower()):
            if len(word) > 2:
                word_counts[word] = word_counts.get(word, 0) + 1
    top = sorted(word_counts.items(), key=lambda kv: -kv[1])[:15]
    empty = sum(1 for c in captions if not c.strip())
    short = sum(1 for n in lengths if n < 3)
    out_path = os.path.join(ctx.out_dir, "curated-dataset.jsonl")
    with open(out_path, "w", encoding="utf-8") as fh:
        for item in curated:
            fh.write(json.dumps(item["record"], ensure_ascii=False) + "\n")
    ctx.artifacts.append(core.rel_path(out_path))
    if packaged:
        ctx.warn("no dataset file was supplied: the packaged sample caption corpus was analysed")
    body = "\n".join([
        f"Dataset: {dataset_label}",
        f"Records: {len(records)}; unique captions: {len(curated)}; duplicates: {len(duplicates)}",
        "",
        md_table([
            ["caption length (words)", f"{min(lengths)} - {max(lengths)}",
             f"mean {sum(lengths) / len(lengths):.1f}"],
            ["empty captions", empty, "block or drop"],
            ["captions under 3 words", short, "too short to condition reliably"],
            ["duplicate captions", len(duplicates), "remove before training"],
            ["vocabulary size", len(word_counts), "a very small vocabulary suggests a narrow dataset"],
        ], ["Measure", "Value", "Action"]),
        "",
        "Most frequent words: " + ", ".join(f"{w} ({c})" for w, c in top),
        "",
        "Curation steps:",
        _lines([
            "Drop empty and near-empty captions.",
            "Drop exact duplicates and near-duplicates (Jaccard above 0.85).",
            "Check class balance across the categories you actually care about.",
            "Remove images with broken rights, or without consent where people are identifiable.",
            "Write a manifest describing every kept file and every dropped file.",
        ]),
    ])
    manifest = {
        "source": dataset_label, "packaged_sample": packaged,
        "records_in": len(records), "records_out": len(curated),
        "duplicates_removed": len(duplicates), "empty_captions": empty, "short_captions": short,
        "top_words": [{"word": w, "count": c} for w, c in top], "curated_file": core.rel_path(out_path),
    }
    return _report(ctx, "dataset-curation", "Dataset curation report", body, manifest,
                   f"Curated {len(curated)} of {len(records)} records.",
                   {"records_in": len(records), "records_out": len(curated)}, [core.rel_path(out_path)])


def captioning_pipeline(ctx) -> Dict[str, Any]:
    dataset_path = ctx.get("dataset")
    caption_field = str(ctx.get("caption_field", "caption"))
    trigger = ctx.get("trigger") or ""
    profile = {}
    if dataset_path:
        records = _read_dataset(str(dataset_path), caption_field)
        captions = [_caption_of(r, caption_field) for r in records]
        lengths = [len(c.split()) for c in captions]
        profile = {"records": len(records), "mean_words": round(sum(lengths) / max(1, len(lengths)), 2),
                   "min_words": min(lengths), "max_words": max(lengths)}
    config = {
        "template": "{trigger}{subject}, {attributes}, {scene}, {lighting}, {medium}",
        "trigger": trigger or "<trigger>",
        "attribute_order": ["subject", "action", "clothing", "material", "expression"],
        "max_words": 60,
        "dropout": {"trigger_token": 0.05, "style_descriptor": 0.10, "attribute_list": 0.10,
                    "keep_subject": True, "note": "never drop the subject - the model loses the concept"},
        "validation": ["caption must name the subject", "no contradictory attributes",
                       "no names of real people", "no copyrighted character names"],
    }
    body = "\n".join([
        "Caption pipeline configuration:",
        "",
        md_table([[k, json.dumps(v) if isinstance(v, (dict, list)) else v] for k, v in config.items()],
                 ["Setting", "Value"]),
        "",
        ("Dataset profile: " + json.dumps(profile)) if profile else "No dataset supplied - showing the default profile.",
        "",
        "Caption rules:",
        _lines([
            "One subject per caption.",
            "Put the trigger token first when training a new concept.",
            "Keep the caption style consistent; inconsistency is learned as noise.",
            "Never caption an image with content that is not visible.",
        ]),
    ])
    data = {"config": config, "profile": profile}
    return _report(ctx, "captioning-pipeline", "Captioning pipeline design", body, data,
                   "Designed a caption template and dropout policy.")


def lora_training(ctx) -> Dict[str, Any]:
    dataset_path = ctx.get("dataset")
    size = int(ctx.get("dataset_size", 40))
    target = str(ctx.get("target", "character"))
    if dataset_path:
        size = len(_read_dataset(str(dataset_path), "caption"))
    defaults = load_asset(PROFILES)["lora_defaults"]
    base = defaults.get(target, defaults["character"])
    rank = base["rank"]
    alpha = base["alpha"]
    repeats = max(4, min(20, round(60 / max(1, size)) * 2))
    epochs = max(4, min(30, round(1200 / max(1, size * repeats))))
    buckets = base["resolution_buckets"]
    total_steps = size * repeats * epochs
    config = {
        "target": target, "dataset_images": size, "network_rank": rank, "network_alpha": alpha,
        "unet_lr": base["unet_lr"], "text_encoder_lr": base["text_encoder_lr"],
        "resolution_buckets": buckets, "repeats": repeats, "epochs": epochs, "total_steps": total_steps,
        "optimizer": "AdamW", "scheduler": "cosine with warmup",
        "save_every_n_steps": max(100, total_steps // 6),
        "regularisation": "3-5 class images to limit overfitting", "validation_prompts": 12,
    }
    body = "\n".join([
        f"LoRA configuration for a {target} adapter ({size} images)",
        "",
        md_table([[k, v] for k, v in config.items()], ["Setting", "Value"]),
        "",
        "Reasoning:",
        _lines([
            f"{size} images at {repeats} repeats for {epochs} epochs is {total_steps} optimiser steps, which is "
            "enough to learn the subject without memorising the background.",
            "The rank is the main capacity control: raise it for complex subjects, lower it to reduce overfitting.",
            "Keep the text encoder learning rate at or below a quarter of the UNet rate.",
            "Save checkpoints every sixth of the run so you can pick the least overfitted one.",
        ]),
    ])
    data = {"config": config}
    return _report(ctx, "lora-training", "LoRA training plan", body, data,
                   f"Computed a LoRA plan: rank {rank}, {total_steps} steps.")


def dreambooth_training(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    subject_class = str(ctx.get("class", "person"))
    size = int(ctx.get("dataset_size", 12))
    config = {
        "subject": subject, "class_noun": subject_class, "subject_images": size, "class_images": 20,
        "prior_preservation": True, "prior_preservation_weight": 1.0, "learning_rate": 1e-5,
        "steps": size * 100, "resolution": 1024,
        "identity_tests": ["a portrait of {trigger}", "a full body shot of {trigger}",
                           "the subject in a different setting", "the subject next to a similar-looking person"],
        "overfitting_controls": ["keep the class images diverse",
                                  "stop at the checkpoint that still follows the prompt",
                                  "compare with the base model every checkpoint"],
    }
    body = "\n".join([
        f"Fine-tune {subject} as a '{subject_class}'",
        "",
        md_table([[k, v if not isinstance(v, list) else "; ".join(v)] for k, v in config.items()],
                 ["Setting", "Value"]),
        "",
        "Rules:",
        _lines([
            "Use a rare, specific trigger token rather than the class noun.",
            "Class images must cover the pose, lighting and background variety you want to keep generalising.",
            "Evaluate every checkpoint against the base model before shipping.",
        ]),
    ])
    data = {"config": config}
    return _report(ctx, "dreambooth-training", "DreamBooth training plan", body, data,
                   f"Planned a DreamBooth run for {subject}.")


def textual_inversion(ctx) -> Dict[str, Any]:
    concept = str(ctx.require("concept"))
    examples = int(ctx.get("examples", 6))
    config = {
        "concept": concept, "token": "<" + slugify(concept).replace("-", "_") + ">", "placeholder": "<new1>",
        "examples": examples, "vectors": 1, "learning_rate": 5e-4, "steps": 1500, "max_token_length": 1,
        "validation": ["a photo of <new1>", "<new1> in a different style", "two <new1> objects side by side"],
        "overfitting_limits": ["stop when the placeholder starts to appear without the prompt",
                                "keep the learning rate low and the token count at one"],
    }
    body = "\n".join([
        f"Textual inversion for '{concept}'",
        "",
        md_table([[k, v if not isinstance(v, list) else "; ".join(v)] for k, v in config.items()],
                 ["Setting", "Value"]),
        "",
        "Use it when a LoRA would be too much: the concept is a style or a simple object and you want the "
        "base model untouched.",
    ])
    data = {"config": config}
    return _report(ctx, "textual-inversion", "Textual inversion plan", body, data,
                   f"Planned a textual inversion run for {concept}.")


def finetune_evaluation(ctx) -> Dict[str, Any]:
    target = str(ctx.require("target"))
    suites = {
        "identity": [{"prompt": "a photo of <trigger>", "checks": ["identity", "anatomy"]},
                     {"prompt": "a photo of <trigger> in a park", "checks": ["identity", "flexibility"]}],
        "flexibility": [{"prompt": "<trigger> wearing a red coat", "checks": ["attribute binding"]},
                        {"prompt": "<trigger> at night", "checks": ["lighting generalisation"]}],
        "leakage": [{"prompt": "a photo of a generic person", "checks": ["no trigger leakage"]},
                    {"prompt": "a photo of a random object", "checks": ["no trigger leakage"]}],
        "adherence": [{"prompt": "<trigger> holding a cup", "checks": ["prompt adherence", "anatomy"]}],
    }
    total = sum(len(v) for v in suites.values())
    body = "\n".join([
        f"Evaluation suite for {target}",
        "",
        md_table([[k, len(v), ", ".join(sorted({c for item in v for c in item["checks"]}))]
                  for k, v in suites.items()], ["Suite", "Cases", "Checks"]),
        "",
        f"Total fixed cases: {total}. Hold the seed, the sampler and the steps constant across checkpoints.",
        "",
        "Scoring:",
        _lines([
            "identity: does the subject still read as the same person or object?",
            "flexibility: does the model still follow unrelated prompts?",
            "leakage: does the trigger appear when it should not?",
            "adherence: are the requested attributes and counts correct?",
        ]),
    ])
    data = {"target": target, "suites": suites, "total_cases": total}
    return _report(ctx, "finetune-evaluation", "Fine-tune evaluation suite", body, data,
                   f"Built a {total}-case evaluation suite.")


def overfitting_auditor(ctx) -> Dict[str, Any]:
    caption_field = str(ctx.get("caption_field", "caption"))
    records, dataset_label, packaged = _dataset_records(ctx.get("dataset"), caption_field)
    dataset_path = dataset_label
    captions = [_caption_of(r, caption_field) for r in records]
    hashes = {}
    duplicates = []
    for i, caption in enumerate(captions):
        key = core.stable_hash(caption.strip().lower(), 16)
        if key in hashes:
            duplicates.append({"index": i, "first": hashes[key], "caption": caption[:90]})
        else:
            hashes[key] = i
    token_sets = [set(re.findall(r"[a-z']+", c.lower())) for c in captions]
    near = []
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            if not token_sets[i] or not token_sets[j]:
                continue
            union = len(token_sets[i] | token_sets[j])
            jaccard = len(token_sets[i] & token_sets[j]) / union if union else 0
            if jaccard > 0.85:
                near.append({"a": i, "b": j, "jaccard": round(jaccard, 3),
                             "captions": [captions[i][:70], captions[j][:70]]})
    recommendations = []
    if duplicates:
        recommendations.append(f"remove {len(duplicates)} exact duplicate captions")
    if near:
        recommendations.append(f"collapse {len(near)} near-duplicate caption pairs")
    recommendations.append("reduce repeats or epochs if the model reproduces the training backgrounds")
    recommendations.append("add class images so the model learns the subject, not the scene")
    recommendations.append("compare every checkpoint against the base model and keep the least overfitted")
    if packaged:
        ctx.warn("no dataset file was supplied: the packaged sample caption corpus was analysed")
    body = "\n".join([
        f"Dataset: {dataset_label} with {len(records)} records",
        "",
        md_table([["exact duplicates", len(duplicates)],
                  ["near duplicates (Jaccard > 0.85)", len(near)]], ["Check", "Count"]),
        "",
        "Examples:",
        _lines([f"- {d['caption']}" for d in duplicates[:5]]) if duplicates else "- no exact duplicates",
        "",
        "Recommendations:",
        _lines(recommendations),
    ])
    data = {"dataset": dataset_label, "packaged_sample": packaged, "records": len(records),
            "duplicates": duplicates[:50], "near_duplicates": near[:50],
            "recommendations": recommendations}
    return _report(ctx, "overfitting-audit", "Overfitting and memorization audit", body, data,
                   f"Audited {len(records)} records: {len(duplicates)} duplicates, {len(near)} near duplicates.",
                   {"duplicates": len(duplicates), "near_duplicates": len(near)})


def model_merge_evaluator(ctx) -> Dict[str, Any]:
    models = [m.strip() for m in str(ctx.get("models", "")).split(",") if m.strip()]
    ratios = [float(r) for r in str(ctx.get("ratios", "0.5,0.5")).split(",") if r.strip()]
    if not models:
        models = ["sdxl", "sdxl-photoreal"]
        ctx.warn("no models were supplied: the merge plan uses the packaged SDXL base and "
                 "photoreal checkpoints as the worked example")
    if len(ratios) < len(models):
        ratios = (ratios + [1.0 / len(models)] * len(models))[:len(models)]
    total = sum(ratios) or 1.0
    normalised = [round(r / total, 3) for r in ratios]
    warnings = []
    if len(models) > 1 and len(set(models)) == 1:
        warnings.append("the same model was listed twice - check the adapter names")
    plan = {
        "inputs": [{"model": m, "ratio": r} for m, r in zip(models, normalised)],
        "merge_order": "merge from the least specialised to the most specialised",
        "test_prompts": ["a product pack shot", "a portrait", "a landscape", "a text-heavy layout",
                         "a style reference scene"],
        "evaluation": ["prompt adherence", "identity drift", "colour shift", "artefact rate",
                       "comparison against each parent model"],
    }
    body = "\n".join([
        md_table([[p["model"], p["ratio"]] for p in plan["inputs"]], ["Model or adapter", "Ratio"]),
        "",
        "Merge rules:",
        _lines([f"{k}: {v}" for k, v in plan.items() if k != "inputs"]),
        "",
        ("Warnings:" if warnings else "No automatic warnings."),
        _lines(warnings),
    ])
    data = {"models": models, "ratios": normalised, "plan": plan, "warnings": warnings}
    return _report(ctx, "model-merge-plan", "Model merge and blend plan", body, data,
                   f"Planned a merge of {len(models)} model(s).")


def _lex() -> Dict[str, Any]:
    """Load the creative vocabulary / artist-rewrite lexicon."""
    return load_asset(LEXICON)


def _lex_values(group: str) -> List[str]:
    lexicon = _lex()
    values = lexicon.get(group, [])
    return [v if isinstance(v, str) else v.get("value", "") for v in values]


# ---------------------------------------------------------------------------
# Category 17 - Data governance, rights and provenance
# ---------------------------------------------------------------------------


def rights_reviewer(ctx) -> Dict[str, Any]:
    dataset_path = ctx.get("dataset")
    rules = load_asset(SAFETY)["copyright"]
    required_fields = ["source", "license", "author_or_owner", "usage_scope", "date_obtained"]
    findings: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    if dataset_path:
        records = _read_dataset(str(dataset_path), "caption")
        missing: Dict[str, int] = {f: 0 for f in required_fields}
        for record in records:
            for field in required_fields:
                value = None
                for key in record:
                    if key.lower().replace(" ", "_") == field:
                        value = record[key]
                        break
                if value is None or str(value).strip() == "":
                    missing[field] += 1
        findings = [{"field": f, "missing": n, "share": round(n / max(1, len(records)), 3)}
                    for f, n in missing.items()]
        blocking = [f for f in findings if f["missing"] == len(records)]
        status = "blocked" if blocking else ("review" if any(f["missing"] for f in findings) else "pass")
    else:
        status = "template"
    body = "\n".join([
        "Training-data rights review",
        "",
        ("Dataset: " + core.rel_path(str(dataset_path))) if dataset_path else
        "No dataset supplied - showing the rights checklist to apply to each training image.",
        "",
        md_table([[f["field"], f["missing"], f"{f['share'] * 100:.0f}%"] for f in findings],
                 ["Required field", "Records missing it", "Share"]) if findings else
        md_table([[f, "required per image"] for f in required_fields], ["Required field", "Requirement"]),
        "",
        "Status: " + status,
        "",
        "Rules:",
        _lines([
            rules["note"],
            "Every training image needs a recorded source, licence, owner and permitted use.",
            "Images scraped from the web have no usable licence by default - treat them as blocked.",
            "Store the licence text or link with the dataset, not just a tick in a spreadsheet.",
            "Re-review before any change of use: editorial, advertising and model training have different scopes.",
        ]),
    ])
    data = {"dataset": core.rel_path(str(dataset_path)) if dataset_path else None,
            "status": status, "records": len(records), "field_findings": findings,
            "required_fields": required_fields}
    return _report(ctx, "rights-review", "Training data rights review", body, data,
                   f"Rights review status: {status}.",
                   {"records": len(records), "status": status})


def copyright_screener(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    context = str(ctx.get("context", "commercial"))
    rules = load_asset(SAFETY)["copyright"]
    lexicon = _lex()
    lowered = prompt.lower()
    hits: List[Dict[str, Any]] = []
    for character in rules["protected_characters"]:
        if character in lowered:
            hits.append({"type": "protected_character", "term": character, "level": "high"})
    for mark in rules["protected_marks"]:
        if mark in lowered:
            hits.append({"type": "protected_mark", "term": mark, "level": "high"})
    for signal in rules["trade_dress_signals"]:
        if signal in lowered:
            hits.append({"type": "trade_dress", "term": signal, "level": "medium"})
    artist_map = lexicon.get("artist_style_rewrite", {}).get("map", {})
    for artist in artist_map:
        if artist in lowered:
            hits.append({"type": "living_artist", "term": artist, "level": "high"})
    for phrase in rules["living_artist_naming"]:
        if phrase in lowered:
            hits.append({"type": "artist_naming_phrase", "term": phrase, "level": "high"})
    levels = {h["level"] for h in hits}
    if "high" in levels:
        risk = "high"
    elif levels:
        risk = "medium"
    else:
        risk = "low"
    context_weight = {"commercial": 2, "editorial": 1, "personal": 1}.get(context, 1)
    score = min(10, len(hits) * 2 * context_weight)
    suggestions = []
    for hit in hits:
        if hit["type"] == "living_artist":
            suggestions.append(f"replace '{hit['term']}' with neutral visual attributes")
        elif hit["type"] == "artist_naming_phrase":
            suggestions.append("describe medium, brush behaviour, lighting, palette and mood instead of a name")
        else:
            suggestions.append(f"remove or licence the reference to '{hit['term']}'")
    body = "\n".join([
        f"Copyright risk screen - context: {context}",
        "",
        md_table([[h["type"], h["term"], h["level"]] for h in hits], ["Signal type", "Term", "Level"])
        if hits else md_table([["none", "-", "-"]], ["Signal type", "Term", "Level"]),
        "",
        f"Overall risk: {risk} (weighted score {score}/10)",
        "",
        "Interpretation:",
        _lines([
            rules["note"],
            f"Risk levels: {json.dumps(rules['risk_levels'])}",
        ]),
        "",
        "Suggested changes:" if suggestions else "No change suggested by the automated screen.",
        _lines(suggestions) if suggestions else "",
    ])
    data = {"prompt": prompt, "context": context, "risk": risk, "score": score, "hits": hits,
            "suggestions": suggestions}
    return _report(ctx, "copyright-screen", "Copyright risk screen", body, data,
                   f"Copyright risk: {risk} ({len(hits)} signal(s)).",
                   {"risk": risk, "hits": len(hits), "score": score})


def style_rewriter(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    lexicon = _lex()
    rewrite = lexicon["artist_style_rewrite"]
    artist_map = rewrite["map"]
    lowered = prompt.lower()
    found = [artist for artist in artist_map if artist in lowered]
    rewritten = prompt
    replacements: List[Dict[str, str]] = []
    for artist in found:
        attributes = artist_map[artist]
        phrase = re.compile(r"(in the style of\s+)?(?:" + re.escape(artist) + r")", re.IGNORECASE)
        replacement = ", ".join(attributes)
        rewritten, count = phrase.subn(replacement, rewritten)
        replacements.append({"artist": artist, "attributes": attributes,
                             "occurrences": count})
    if not found:
        rewritten = prompt
    extra = []
    if "in the style of" in lowered:
        extra.append("the phrase 'in the style of' is removed; describe attributes directly")
    body = "\n".join([
        "Artist-style request rewrite",
        "",
        f"Original : {prompt}",
        f"Rewritten: {rewritten}",
        "",
        "Policy:",
        rewrite["policy"],
        "",
        md_table([[r["artist"], r["occurrences"], "; ".join(r["attributes"])]
                  for r in replacements], ["Artist named", "Replacements", "Attribute replacement"])
        if replacements else md_table([["none", 0, "-"]], ["Artist named", "Replacements", "Attribute replacement"]),
        "",
        ("Additional notes:" if extra else "No additional notes."),
        _lines(extra) if extra else "",
        "",
        "Fallback guidance:",
        _lines(rewrite["generic_fallback"]),
    ])
    data = {"original": prompt, "rewritten": rewritten, "artists": found, "replacements": replacements,
            "notes": extra}
    return _report(ctx, "style-rewrite", "Artist-style request rewrite", body, data,
                   f"Rewrote {len(replacements)} artist reference(s)."
                   if replacements else "No living-artist reference found.",
                   {"artists": len(found)})


def consent_checker(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    records_path = ctx.get("records")
    rules = load_asset(SAFETY)["consent"]
    required = rules["required_records"]
    findings: List[Dict[str, Any]] = []
    subject_records: List[Dict[str, Any]] = []
    if records_path:
        records = _read_dataset(str(records_path), "subject_identity")
        for record in records:
            identity = str(record.get("subject_identity", "")).strip()
            if identity.lower() == subject.lower() or subject.lower() in identity.lower():
                subject_records.append(record)
        if not subject_records:
            subject_records = records
    status = "no-record"
    if subject_records:
        missing: Dict[str, int] = {f: 0 for f in required}
        for record in subject_records:
            for field in required:
                value = record.get(field)
                if value is None or str(value).strip() == "":
                    missing[field] += 1
        findings = [{"field": f, "missing": n} for f, n in missing.items()]
        complete = all(n == 0 for n in missing.values())
        status = "complete" if complete else "incomplete"
    sensitive = [c for c in rules["sensitive_contexts"] if c in subject.lower()]
    body = "\n".join([
        f"Consent and likeness review for: {subject}",
        "",
        f"Record status: {status}",
        "",
        md_table([[f["field"], f["missing"]] for f in findings], ["Required consent field", "Records missing"])
        if findings else md_table([[f, "not found"] for f in required], ["Required consent field", "Status"]),
        "",
        "Sensitive context detected: " + (", ".join(sensitive) if sensitive else "none"),
        "",
        "Rules:",
        _lines([
            rules["minors_rule"],
            "A subject appearing in sensitive contexts (medical, political, religious, criminal record, financial) "
            "needs a second reviewer and a narrower scope.",
            "Consent to appear in one campaign is not consent to reuse in another.",
            "Expiry dates are enforced: an expired record is treated as no record.",
        ]),
    ])
    data = {"subject": subject, "status": status, "records": len(subject_records),
            "findings": findings, "sensitive_contexts": sensitive, "required_fields": required}
    return _report(ctx, "consent-review", "Consent and likeness check", body, data,
                   f"Consent status for '{subject}': {status}.",
                   {"status": status, "records": len(subject_records)})


def provenance_manager(ctx) -> Dict[str, Any]:
    image_path = ctx.get("image")
    sidecar_path = ctx.get("sidecar")
    prompt = ctx.get("prompt")
    model = ctx.get("model")
    seed = ctx.get("seed")
    embed = bool(ctx.get("embed", False))
    provenance: Dict[str, Any] = {
        "tool": "ai-image-production-toolkit",
        "asset_type": "synthetic_image",
        "created_utc": utc_now(),
        "prompt": prompt,
        "model": model,
        "seed": seed,
        "source_file": core.rel_path(str(image_path)) if image_path else None,
        "generator": {"name": "AI Image Production Toolkit", "version": "1.0.0"},
        "disclosure": "AI-generated image",
        "notes": "Produced by a generative pipeline; not a photograph of a real subject.",
    }
    if image_path and os.path.isfile(str(image_path)):
        provenance["sha256"] = core.sha256_file(str(image_path))
        try:
            info = core.probe_image(str(image_path))
            provenance["image"] = {k: info.get(k) for k in ("width", "height", "format", "mode", "bytes")}
        except Exception:
            provenance["image"] = None
    written: List[str] = []
    if sidecar_path:
        core.write_json(str(sidecar_path), provenance)
        written.append(core.rel_path(str(sidecar_path)))
    else:
        target = ctx.out("provenance.json")
        core.write_json(target, provenance)
        written.append(ctx.emit(target))
    if embed and image_path and os.path.isfile(str(image_path)):
        try:
            core.png_write_text_chunks(str(image_path), {
                "provenance": json.dumps(provenance, ensure_ascii=False),
                "Software": "ai-image-production-toolkit",
            })
            written.append(core.rel_path(str(image_path)))
        except Exception as exc:  # pragma: no cover - non PNG or locked file
            ctx.warn(f"could not embed metadata into {image_path}: {exc}")
    body = "\n".join([
        "Provenance record",
        "",
        "```json",
        json.dumps(provenance, indent=2, ensure_ascii=False),
        "```",
        "",
        "Storage rules:",
        _lines([
            "Keep the sidecar next to the image it describes.",
            "Embed the record as PNG text chunks when the delivery format allows it.",
            "Strip embedded metadata from public copies that contain personal data.",
            "Never ship a source file without its provenance record.",
        ]),
    ])
    data = provenance
    return _report(ctx, "provenance", "Provenance metadata record", body, data,
                   f"Wrote a provenance record for {provenance.get('source_file') or 'the current asset'}.",
                   {"embedded": bool(embed)}, written)


def disclosure_writer(ctx) -> Dict[str, Any]:
    context = str(ctx.get("context", "social_post"))
    details = ctx.get("details")
    templates = load_asset(SAFETY)["disclosure_templates"]
    if context not in templates:
        raise InvalidInput(f"unknown disclosure context: {context} "
                           f"(expected one of {', '.join(sorted(templates))})")
    base = templates[context]
    lines = [base]
    if details:
        for detail in str(details).split(";"):
            detail = detail.strip()
            if detail:
                lines.append(detail)
    text = " ".join(lines)
    body = "\n".join([
        f"Disclosure text ({context})",
        "",
        text,
        "",
        "Placement guidance:",
        _lines([
            "Social posts: place the label in the post body and in the first image alt text.",
            "Press releases: place it in the caption and in the boilerplate.",
            "Reports and papers: place it in the figure caption and the methods section.",
            "Datasets: place it in the dataset card, not only in a README.",
            "Keep the label legible at the delivery size; do not shrink it below 12 px.",
        ]),
    ])
    data = {"context": context, "text": text, "template": base}
    return _report(ctx, "disclosure", "Synthetic media disclosure", body, data,
                   f"Produced a {context.replace('_', ' ')} disclosure.")


def privacy_redactor(ctx) -> Dict[str, Any]:
    image_path = ctx.get("image")
    apply = bool(ctx.get("apply", False))
    checklist = load_asset(SAFETY)["privacy_redaction"]["checklist"]
    regions: List[Dict[str, Any]] = []
    if image_path and os.path.isfile(str(image_path)):
        info = core.probe_image(str(image_path))
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if width and height:
            regions = [
                {"target": "faces", "box": [int(width * 0.25), int(height * 0.05),
                                           int(width * 0.75), int(height * 0.45)],
                 "action": "blur or replace with a synthetic face"},
                {"target": "registration plates", "box": [int(width * 0.05), int(height * 0.70),
                                                          int(width * 0.95), int(height * 0.92)],
                 "action": "solid fill"},
                {"target": "documents and screens", "box": [int(width * 0.05), int(height * 0.05),
                                                            int(width * 0.45), int(height * 0.35)],
                 "action": "solid fill"},
            ]
    plan = {
        "mode": "apply" if apply else "plan-only",
        "checklist": checklist,
        "regions": regions,
        "metadata": ["EXIF GPS", "camera serial", "software history", "embedded thumbnails", "XMP creator fields"],
        "irreversible": True,
        "note": "Redaction is destructive. Keep the unredacted original in a restricted location.",
    }
    body = "\n".join([
        "Privacy redaction plan",
        "",
        f"Mode: {plan['mode']}",
        "",
        md_table([[r["target"], str(r["box"]), r["action"]] for r in regions],
                 ["Target", "Bounding box (x0,y0,x1,y1)", "Action"]) if regions else
        md_table([["-", "-", "no image supplied"]], ["Target", "Bounding box (x0,y0,x1,y1)", "Action"]),
        "",
        "Checklist:",
        _lines(checklist),
        "",
        "Metadata to strip:",
        _lines(plan["metadata"]),
        "",
        "Redaction is irreversible. The original must be stored separately with restricted access.",
    ])
    data = plan
    return _report(ctx, "privacy-redaction", "Privacy redaction plan", body, data,
                   f"Prepared a privacy redaction plan with {len(regions)} target region(s).",
                   {"regions": len(regions)})


def bias_auditor(ctx) -> Dict[str, Any]:
    dataset_path = ctx.get("dataset")
    caption_field = str(ctx.get("caption_field", "caption"))
    report: Dict[str, Any] = {"dataset": None, "records": 0, "attributes": [], "gaps": [], "recommendations": []}
    if dataset_path:
        records = _read_dataset(str(dataset_path), caption_field)
        captions = [_caption_of(r, caption_field) for r in records]
        tokens = [re.findall(r"[a-z']+", c.lower()) for c in captions]
        totals: Dict[str, int] = {}
        doc_total = 0
        for toks in tokens:
            doc_total += 1
            for token in set(toks):
                totals[token] = totals.get(token, 0) + 1
        present = {t: c for t, c in totals.items() if c > 0}
        skip = {"a", "an", "the", "of", "and", "in", "on", "with", "at", "to", "for", "is", "by", "from"}
        ranked = sorted(((t, c) for t, c in present.items() if t not in skip and len(t) > 2),
                        key=lambda kv: (-kv[1], kv[0]))
        report["dataset"] = core.rel_path(str(dataset_path))
        report["records"] = len(records)
        report["attributes"] = [{"term": t, "documents": c,
                                  "share": round(c / max(1, doc_total), 3)} for t, c in ranked[:40]]
        groups = {
            "gender terms": ["woman", "women", "man", "men", "girl", "boy", "person", "lady", "gentleman"],
            "age terms": ["child", "children", "teenager", "elderly", "baby", "young", "old"],
            "skin tone descriptors": ["white", "black", "asian", "african", "latino", "pale", "dark-skinned"],
            "disability descriptors": ["disabled", "wheelchair", "blind", "deaf", "prosthetic"],
            "nationality descriptors": ["american", "european", "asian", "african", "middle eastern", "latin"],
        }
        gaps = []
        for group, terms in groups.items():
            counts = {t: totals.get(t, 0) for t in terms}
            present_terms = {t: c for t, c in counts.items() if c}
            share = (sum(present_terms.values()) / max(1, doc_total))
            gaps.append({"group": group, "documents_matched": sum(present_terms.values()),
                         "share_of_records": round(share, 3), "terms": present_terms})
        report["gaps"] = gaps
        recommendations = [
            "Balance the dataset across the categories you care about, not just the one you have.",
            "Add captions that describe people in varied, specific and non-stereotyped ways.",
            "Add a counter-stereotype pass: does the model only produce one demographic for a profession?",
            "Measure outcomes, not only inputs: run a prompt sweep across the demographic axis.",
            "Record which attributes were deliberately left out, and why.",
        ]
        report["recommendations"] = recommendations
    else:
        report["recommendations"] = [
            "Provide a dataset path to compute attribute frequencies and imbalance ratios.",
            "Add captions that describe people in varied, specific and non-stereotyped ways.",
            "Measure outcomes, not only inputs: run a prompt sweep across the demographic axis.",
        ]
    body = "\n".join([
        "Dataset bias audit",
        "",
        f"Dataset: {report['dataset'] or 'not supplied'}",
        f"Records: {report['records']}",
        "",
        md_table([[g["group"], g["documents_matched"], f"{g['share_of_records'] * 100:.0f}%",
                   ", ".join(f"{t} ({c})" for t, c in list(g["terms"].items())[:6])]
                  for g in report["gaps"]], ["Attribute group", "Records matched", "Share", "Terms"])
        if report["gaps"] else md_table([["no dataset supplied", "-", "-", "-"]],
                                        ["Attribute group", "Records matched", "Share", "Terms"]),
        "",
        "Most frequent attribute terms:",
        _lines([f"- {a['term']} ({a['documents']} documents, {a['share'] * 100:.0f}%)"
                for a in report["attributes"][:15]]) if report["attributes"] else "- no dataset supplied",
        "",
        "Recommendations:",
        _lines(report["recommendations"]),
    ])
    data = report
    return _report(ctx, "bias-audit", "Dataset bias audit", body, data,
                   f"Audited {report['records']} records across {len(report['gaps'])} attribute groups."
                   if report["records"] else "Bias audit template produced (no dataset supplied).",
                   {"records": report["records"]})


# ---------------------------------------------------------------------------
# Category 18 - Safety, authenticity and misuse prevention
# ---------------------------------------------------------------------------


def deepfake_assessor(ctx) -> Dict[str, Any]:
    scenario = str(ctx.require("scenario"))
    subject = ctx.get("subject")
    rules = load_asset(SAFETY)["deepfake"]
    text = (f"{scenario} {subject or ''}").lower()
    factors = []
    score = 0
    for factor in rules["risk_factors"]:
        hit = False
        if factor["id"] == "real_person":
            hit = any(word in text for word in ("person", "someone", "actor", "host", "real", "celebrity"))
        elif factor["id"] == "political":
            hit = any(word in text for word in ("politic", "campaign", "minister", "president", "election"))
        elif factor["id"] == "journalistic":
            hit = any(word in text for word in ("news", "documentar", "report", "journalis", "broadcast"))
        elif factor["id"] == "financial":
            hit = any(word in text for word in ("advertis", "commercial", "financial", "promo", "sale"))
        elif factor["id"] == "explicit":
            hit = any(word in text for word in ("sexual", "intimate", "nude", "explicit"))
        elif factor["id"] == "defamation":
            hit = any(word in text for word in ("accus", "confess", "arrest", "scandal", "allegation"))
        elif factor["id"] == "widely_shared":
            hit = any(word in text for word in ("viral", "social", "share", "public", "campaign"))
        if hit:
            score += factor["weight"]
            factors.append({"id": factor["id"], "label": factor["label"], "weight": factor["weight"]})
    if score >= 9:
        risk = "high"
    elif score >= 4:
        risk = "medium"
    else:
        risk = "low"
    mitigations = list(rules["mitigations"])
    if risk == "low":
        mitigations = mitigations[-2:]
    body = "\n".join([
        f"Deepfake risk assessment: {scenario}",
        "",
        f"Subject: {subject or 'not specified'}",
        f"Risk: {risk} (score {score})",
        "",
        md_table([[f["label"], f["weight"]] for f in factors], ["Risk factor", "Weight"])
        if factors else md_table([["none detected", 0]], ["Risk factor", "Weight"]),
        "",
        "Mitigations:",
        _lines(mitigations),
        "",
        "Escalation:",
        _lines([
            "High risk must be reviewed by a human before any release.",
            "Never use a real identifiable person's likeness without documented consent.",
            "Keep a record of the review and the mitigations applied.",
        ]),
    ])
    data = {"scenario": scenario, "subject": subject, "risk": risk, "score": score,
            "factors": factors, "mitigations": mitigations}
    return _report(ctx, "deepfake-risk", "Deepfake risk assessment", body, data,
                   f"Deepfake risk: {risk} (score {score}).", {"risk": risk, "score": score})


def content_safety_classifier(ctx) -> Dict[str, Any]:
    text = str(ctx.require("text"))
    image_path = ctx.get("image")
    rules = load_asset(SAFETY)["content_safety"]
    lowered = text.lower()
    matches: List[Dict[str, Any]] = []
    for category in rules["categories"]:
        hits = [p for p in category["patterns"] if p in lowered]
        if hits:
            matches.append({"id": category["id"], "label": category["label"],
                            "severity": category["severity"], "patterns": hits,
                            "guidance": category["guidance"]})
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    matches.sort(key=lambda m: severity_order.get(m["severity"], 9))
    if matches:
        top = matches[0]["severity"]
    else:
        top = "none"
    if top == "critical":
        action = "block"
    elif top == "high":
        action = "escalate"
    elif top == "medium":
        action = "review"
    else:
        action = "allow"
    image_note = None
    if image_path and os.path.isfile(str(image_path)):
        image_note = f"Image {core.rel_path(str(image_path))} also needs a visual review; " \
                     "this screen only reads the supplied text."
    body = "\n".join([
        "Content safety screen",
        "",
        f"Text screened: {text}",
        f"Action: {action} (highest severity {top})",
        "",
        md_table([[m["label"], m["severity"], ", ".join(m["patterns"])] for m in matches],
                 ["Category", "Severity", "Matched patterns"]) if matches else
        md_table([["no category matched", "none", "-"]], ["Category", "Severity", "Matched patterns"]),
        "",
        "Guidance for each hit:",
        _lines([f"{m['label']}: {m['guidance']}" for m in matches]) if matches else "- nothing flagged",
        "",
        rules["note"],
        (image_note + " " if image_note else "")
        + "This classifier is a triage aid: it never certifies content as safe and never replaces human review.",
    ])
    data = {"text": text, "action": action, "severity": top, "matches": matches}
    return _report(ctx, "content-safety", "Content safety screen", body, data,
                   f"Content safety action: {action} (severity {top}).",
                   {"action": action, "matches": len(matches)})


def age_sensitive_reviewer(ctx) -> Dict[str, Any]:
    description = str(ctx.require("description"))
    rules = load_asset(SAFETY)["age_safety"]
    lowered = description.lower()
    minor_terms = ["child", "children", "kid", "toddler", "minor", "underage", "preteen",
                   "schoolgirl", "young boy", "young girl", "baby", "infant", "teen"]
    sexual_terms = ["nude", "naked", "lingerie", "seductive", "erotic", "explicit", "bare", "topless",
                     "bikini", "suggestive"]
    body_terms = ["focus on the body", "emphasise the body", "emphasize the body", "body focus",
                  "close-up of the body", "thigh", "chest", "waist", "hips"]
    risky_terms = ["undress", "disrobed", "change into", "in a swimsuit", "provocative", "flirtatious"]
    minor = [t for t in minor_terms if t in lowered]
    sexual = [t for t in sexual_terms if t in lowered]
    body = [t for t in body_terms if t in lowered]
    risky = [t for t in risky_terms if t in lowered]
    triggers = minor + sexual + body + risky
    verdict = "clear" if not triggers else ("escalate" if minor and (sexual or body or risky)
                                            else ("review" if triggers else "clear"))
    body_text = "\n".join([
        "Age-sensitive review",
        "",
        f"Description: {description}",
        f"Verdict: {verdict}",
        "",
        md_table([["minor indicators", ", ".join(minor) or "-"],
                  ["sexual indicators", ", ".join(sexual) or "-"],
                  ["body focus indicators", ", ".join(body) or "-"],
                  ["risky framing indicators", ", ".join(risky) or "-"]],
                 ["Signal group", "Matches"]),
        "",
        "Strict rules that always apply:",
        _lines(rules["strict_rules"]),
        "",
        f"Escalation: {rules['escalation']}",
    ])
    data = {"description": description, "verdict": verdict,
            "minor_indicators": minor, "sexual_indicators": sexual,
            "body_focus_indicators": body, "risky_indicators": risky, "rules": rules["strict_rules"]}
    return _report(ctx, "age-sensitive-review", "Age-sensitive image review", body_text, data,
                   f"Age-sensitive verdict: {verdict}.",
                   {"verdict": verdict, "triggers": len(triggers)})


def adversarial_prompt_defense(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    rules = load_asset(SAFETY)["adversarial"]
    signals = []
    # leetspeak
    leet = re.findall(r"[a-z]*[0134578@$][a-z0-9]*", prompt, flags=re.IGNORECASE)
    leet_words = [w for w in leet if len(w) >= 3 and any(c.isalpha() for c in w)]
    if leet_words:
        signals.append({"id": "leetspeak", "label": "Leetspeak substitution",
                        "evidence": leet_words[:8]})
    # zero width
    zero_width = [hex(ord(ch)) for ch in prompt if ord(ch) in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD)]
    if zero_width:
        signals.append({"id": "zero_width", "label": "Zero-width characters",
                        "evidence": zero_width[:8]})
    # homoglyphs
    non_ascii = sorted({ch for ch in prompt if ord(ch) > 127})
    if non_ascii:
        signals.append({"id": "homoglyph", "label": "Non-latin characters",
                        "evidence": [f"{ch} U+{ord(ch):04X}" for ch in non_ascii[:8]]})
    # split characters
    singles = re.findall(r"(?:\b\w\s){3,}", prompt)
    if singles:
        signals.append({"id": "char_spacing", "label": "Spaced or split characters",
                        "evidence": [s.strip() for s in singles[:4]]})
    # reversed text
    words = prompt.split()
    reversed_hits = [w for w in words if len(w) > 3 and w == w[::-1]]
    if reversed_hits:
        signals.append({"id": "reversed", "label": "Reversed text", "evidence": reversed_hits[:6]})
    # encoded payload
    encoded = re.findall(r"(?:[A-Za-z0-9+/]{24,}={0,2}|\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2})+)", prompt)
    if encoded:
        signals.append({"id": "encoding", "label": "Encoded payload", "evidence": encoded[:4]})
    # roleplay wrapper
    wrapper = [w for w in ("pretend", "roleplay", "role-play", "imagine you are", "system prompt",
                           "for a novel", "hypothetically", "as an ai without restrictions")
               if w in prompt.lower()]
    if wrapper:
        signals.append({"id": "roleplay_wrap", "label": "Role-play or fiction wrapper",
                        "evidence": wrapper})
    # multilingual switch
    scripts = set()
    for ch in prompt:
        code = ord(ch)
        if 0x0600 <= code <= 0x06FF:
            scripts.add("arabic")
        elif 0x3040 <= code <= 0x30FF:
            scripts.add("japanese")
        elif 0x4E00 <= code <= 0x9FFF:
            scripts.add("chinese")
        elif 0x0400 <= code <= 0x04FF:
            scripts.add("cyrillic")
    if scripts:
        signals.append({"id": "multilingual", "label": "Language switch",
                        "evidence": sorted(scripts)})
    normalised = re.sub(r"[\s\W_]+", "", prompt.lower())
    if len(normalised) > 12 and normalised == normalised[::-1]:
        signals.append({"id": "reversed", "label": "Reversed text", "evidence": ["normalised string is a palindrome"]})
    defenses = [
        "Normalise the prompt before screening: strip zero-width characters, collapse whitespace and map leetspeak.",
        "Screen the normalised text and the raw text; compare the results and treat differences as an attack.",
        "Apply content screening to the decoded form of any encoded payload, not to the encoding.",
        "Ignore framing that tries to suspend the policy, such as role-play or hypothetical wrappers.",
        "Rate-limit and log prompts that trigger several obfuscation signals at once.",
        "Never let an obfuscation signal alone decide the outcome; route it to human review.",
    ]
    body = "\n".join([
        "Adversarial prompt analysis",
        "",
        f"Signals detected: {len(signals)}",
        "",
        md_table([[s["label"], ", ".join(str(e) for e in s["evidence"])[:120]] for s in signals],
                 ["Signal", "Evidence"]) if signals else
        md_table([["none", "-"]], ["Signal", "Evidence"]),
        "",
        "Defense design:",
        _lines(defenses),
        "",
        rules["note"],
    ])
    data = {"prompt": prompt, "signals": signals, "defenses": defenses}
    return _report(ctx, "adversarial-defense", "Adversarial prompt defense", body, data,
                   f"Detected {len(signals)} adversarial signal(s).",
                   {"signals": len(signals)})


def release_checklist(ctx) -> Dict[str, Any]:
    channel = str(ctx.get("channel", "web"))
    media_type = str(ctx.get("media_type", "marketing_image"))
    categories = load_asset(SAFETY)["release_checklist"]["categories"]
    checklist = []
    for category in categories:
        for item in category["items"]:
            checklist.append({"id": category["id"], "group": category["label"], "item": item,
                              "status": "unchecked"})
    blockers = [c for c in checklist if c["id"] in ("rights", "privacy", "security")]
    body = "\n".join([
        f"Safe release checklist - {media_type} for {channel}",
        "",
        md_table([[c["group"], c["item"]] for c in checklist], ["Group", "Check"]),
        "",
        f"Total checks: {len(checklist)}. Blocking groups: {', '.join(sorted({c['group'] for c in blockers}))}.",
        "",
        "Process:",
        _lines([
            "The reviewer records a pass, fail or not-applicable against every line.",
            "A fail in a blocking group stops the release.",
            "Keep the completed checklist with the asset and its provenance record.",
            "Re-run the checklist whenever the asset is edited or re-used in another channel.",
        ]),
    ])
    data = {"channel": channel, "media_type": media_type, "checks": checklist,
            "total": len(checklist)}
    return _report(ctx, "release-checklist", "Safe release checklist", body, data,
                   f"Built a {len(checklist)}-item release checklist for {channel}.",
                   {"checks": len(checklist)})


# ---------------------------------------------------------------------------
# Category 19 - Quality assurance and evaluation (planning side)
# ---------------------------------------------------------------------------


def aesthetic_benchmark(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    candidates = int(ctx.get("candidates", 4))
    raters = int(ctx.get("raters", 3))
    if candidates < 2:
        raise InvalidInput("a benchmark needs at least 2 candidates")
    if raters < 1:
        raise InvalidInput("a benchmark needs at least 1 rater")
    rubric = load_asset(RUBRIC)["visual_quality_rubric"]
    criteria = [c["id"] for c in rubric["dimensions"]]
    weights = {c["id"]: c["weight"] for c in rubric["dimensions"]}
    design = {
        "prompt": prompt,
        "candidates": candidates,
        "raters": raters,
        "criteria": criteria,
        "criterion_weights": weights,
        "bands": rubric["bands"],
        "scale": "1-5 integer per criterion, with a free-text note",
        "pairwise": "each candidate is also compared head-to-head against candidate 1",
        "blind": "candidate order is randomised per rater and the prompt is not shown to raters",
        "aggregation": "median per criterion; mean of medians as the overall score; ties broken by the "
                       "head-to-head pairwise count",
        "sample_size_per_candidate": candidates,
        "interrater": "report the spread of the raters; a spread above 1.0 means the rubric needs rewriting",
        "calibration": "include one known-good reference image in every round",
    }
    body = "\n".join([
        "Aesthetic benchmark design",
        "",
        md_table([[k, v if not isinstance(v, list) else "; ".join(map(str, v))]
                  for k, v in design.items()], ["Setting", "Value"]),
        "",
        "Procedure:",
        _lines([
            f"Generate {candidates} candidates with fixed seed and sampler settings.",
            f"Present them to {raters} raters in randomised order.",
            "Collect a criterion score and a pairwise preference from every rater.",
            "Aggregate with the median, then publish the spread alongside the score.",
            "Re-run the benchmark whenever the model, sampler or prompt template changes.",
        ]),
    ])
    data = design
    return _report(ctx, "aesthetic-benchmark", "Aesthetic benchmark design", body, data,
                   f"Designed a {candidates}-candidate benchmark rated by {raters} raters.",
                   {"candidates": candidates, "raters": raters})


def regression_test_builder(ctx) -> Dict[str, Any]:
    suite_path = ctx.get("suite")
    image_path = ctx.get("image")
    samples = load_asset(SAMPLES)
    cases: List[Dict[str, Any]] = []
    if suite_path:
        loaded = read_json(str(suite_path)) if str(suite_path).lower().endswith(".json") else None
        if isinstance(loaded, dict) and isinstance(loaded.get("cases"), list):
            cases = loaded["cases"]
        elif isinstance(loaded, list):
            cases = loaded
        else:
            raise InvalidInput("regression suite must be a JSON list or an object with a 'cases' list")
    else:
        base = samples.get("regression_cases", [])
        for index, case in enumerate(base):
            cases.append({
                "id": f"regression-{index + 1:02d}",
                "prompt": case.get("prompt", ""),
                "expect": case.get("expect", ""),
                "assertions": case.get("assertions", []),
            })
    metrics = ["aesthetic_score", "prompt_adherence", "artifact_count", "identity_similarity",
               "contrast_ratio", "file_size_bytes"]
    suite = {
        "name": "ai-image-regression-suite",
        "cases": cases,
        "case_count": len(cases),
        "metrics": metrics,
        "thresholds": {"aesthetic_score": 4.0, "prompt_adherence": 0.8, "artifact_count": 0,
                       "identity_similarity": 0.85, "contrast_ratio": 4.5},
        "baseline": "store per-case metric values in a baseline file and diff against it",
        "fail_policy": "a case fails when any metric falls below its threshold or regresses against the baseline",
        "reference_image": core.rel_path(str(image_path)) if image_path else None,
    }
    body = "\n".join([
        "Regression test suite",
        "",
        md_table([[c["id"], str(c.get("prompt", ""))[:70], str(c.get("expect", ""))[:50]]
                  for c in cases], ["Case", "Prompt", "Expectation"]) if cases else
        md_table([["-", "-", "-"]], ["Case", "Prompt", "Expectation"]),
        "",
        "Metrics and thresholds:",
        md_table([[m, str(suite["thresholds"].get(m, "-"))] for m in metrics], ["Metric", "Threshold"]),
        "",
        "Rules:",
        _lines([
            "Pin the model checkpoint, sampler, steps, seed and resolution in every case.",
            "Keep a stored baseline so a change in output is visible rather than inferred.",
            "A regression is a signal to investigate, not a failure to hide.",
            "Store failing outputs as artefacts so the cause can be inspected.",
        ]),
    ])
    data = suite
    return _report(ctx, "regression-suite", "Regression test suite", body, data,
                   f"Built a regression suite with {len(cases)} case(s).",
                   {"cases": len(cases)})


def human_review_protocol(ctx) -> Dict[str, Any]:
    workflow = str(ctx.get("workflow", "batch_generation"))
    volume = int(ctx.get("volume", 200))
    per_item = 45  # seconds per item for a two-stage review
    total_minutes = volume * per_item / 60.0
    protocol = {
        "workflow": workflow,
        "volume": volume,
        "stages": [
            {"stage": 1, "name": "Triage", "coverage": "100% of items",
             "action": "single reviewer, pass/fail against the release checklist",
             "time_per_item_seconds": 15},
            {"stage": 2, "name": "Deep review", "coverage": "all failures plus a 10% random sample",
             "action": "second reviewer, structured notes, defect classification",
             "time_per_item_seconds": 90},
        ],
        "sampling": {"method": "stratified by model, prompt template, seed family and cost band",
                     "sample_size": max(1, round(volume * 0.1))},
        "defect_taxonomy": ["composition", "anatomy", "text rendering", "colour", "brand", "safety",
                            "factuality", "rights", "technical artefact"],
        "agreement": "measure agreement on a 20-item overlap set before each cycle",
        "feedback_loop": "weekly defect digest feeds the prompt and rubric owners",
        "capacity_note": f"about {total_minutes:.0f} reviewer-minutes per cycle at {per_item}s per item",
    }
    body = "\n".join([
        "Human review protocol",
        "",
        md_table([[k, v if not isinstance(v, list) else "; ".join(map(str, v))]
                  for k, v in protocol.items() if k not in ("stages", "sampling")],
                 ["Setting", "Value"]),
        "",
        md_table([[s["stage"], s["name"], s["coverage"], s["action"], f"{s['time_per_item_seconds']}s"]
                  for s in protocol["stages"]], ["Stage", "Name", "Coverage", "Action", "Time/item"]),
        "",
        "Defect taxonomy:",
        _lines(protocol["defect_taxonomy"]),
        "",
        "Sampling:",
        _lines([f"{k}: {v}" for k, v in protocol["sampling"].items()]),
    ])
    data = protocol
    return _report(ctx, "human-review-protocol", "Human review protocol", body, data,
                   f"Designed a two-stage review protocol for {volume} items.",
                   {"volume": volume, "minutes": round(total_minutes)})


# ---------------------------------------------------------------------------
# Category 20 - Automation, APIs and batch pipelines
# ---------------------------------------------------------------------------


def _http_available() -> bool:
    """True when the standard library HTTP client is importable."""
    return urllib.request is not None


def api_integrator(ctx) -> Dict[str, Any]:
    endpoint = ctx.get("endpoint")
    prompt = str(ctx.get("prompt", "a quiet harbour at dawn"))
    model = str(ctx.get("model", "sdxl"))
    timeout = float(ctx.get("timeout", 30))
    retries = int(ctx.get("retries", 2))
    body = {
        "mode": "live" if endpoint else "specification",
        "endpoint": endpoint,
        "method": "POST",
        "headers": {"Content-Type": "application/json",
                    "Authorization": "Bearer ${IMAGE_API_TOKEN}"},
        "request": {
            "model": model,
            "prompt": prompt,
            "negative_prompt": "",
            "width": 1024,
            "height": 1024,
            "steps": 30,
            "cfg_scale": 7.0,
            "seed": deterministic_seed(prompt),
            "response_format": "b64_json",
        },
        "timeout_seconds": timeout,
        "retries": retries,
        "backoff": "exponential, 1s base, capped at 30s",
        "retry_on": [429, 500, 502, 503, 504, "connection reset", "timeout"],
        "never_retry_on": [400, 401, 403, 404, 422],
        "response_handling": {
            "b64_json": "decode and write to <out_dir>/api-result-<seed>.png",
            "url": "download with a timeout and verify the content type",
            "error_field": "error.message",
        },
        "idempotency": "send the same seed for retries so a retry cannot double-charge",
        "rate_limit": {"requests_per_minute": 30, "backoff": "honour the Retry-After header"},
    }
    if endpoint:
        ctx.warn("live endpoint configured: the request specification is produced but no call is made by this "
                 "tool; send it with your own HTTP client so the API token stays outside this package")
    else:
        ctx.warn("no endpoint configured: producing a request specification only. "
                 "Pass --set endpoint=<url> to fill in the target.")
    client = "\n".join([
        "# Minimal reference client - the token is read from the environment, never stored here.",
        "import json, os, time, urllib.request, urllib.error",
        "",
        "def generate(endpoint, payload, timeout=30, retries=2):",
        "    token = os.environ.get('IMAGE_API_TOKEN')",
        "    if not token:",
        "        raise SystemExit('set IMAGE_API_TOKEN in the environment')",
        "    data = json.dumps(payload).encode('utf-8')",
        "    for attempt in range(retries + 1):",
        "        req = urllib.request.Request(endpoint, data=data, method='POST',",
        "            headers={'Content-Type': 'application/json',",
        "                     'Authorization': 'Bearer ' + token})",
        "        try:",
        "            with urllib.request.urlopen(req, timeout=timeout) as resp:",
        "                return json.loads(resp.read().decode('utf-8'))",
        "        except urllib.error.HTTPError as exc:",
        "            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:",
        "                time.sleep(min(30, 2 ** attempt))",
        "                continue",
        "            raise",
        "",
    ])
    text = "\n".join([
        "Image generation API integration",
        "",
        f"Mode: {body['mode']}",
        "",
        "```json",
        json.dumps(body, indent=2, ensure_ascii=False),
        "```",
        "",
        "Reference client:",
        "```python",
        client.rstrip(),
        "```",
        "",
        "Rules:",
        _lines([
            "Read the token from the environment; never embed credentials in code, config or this package.",
            "Send the same seed on a retry so a retry cannot create a duplicate billed image.",
            "Treat 4xx responses other than 429 as permanent failures and stop retrying.",
            "Log the request id and the seed, not the token.",
            "Set a timeout on every call and honour Retry-After.",
        ]),
    ])
    data = body
    return _report(ctx, "api-integration", "Image generation API integration", body and text, data,
                   "Produced an API request specification with retry and idempotency rules.",
                   {"mode": body["mode"], "retries": retries})


def batch_runner(ctx) -> Dict[str, Any]:
    prompts_path = ctx.get("prompts")
    width = int(ctx.get("width", 1024))
    height = int(ctx.get("height", 1024))
    seeds = int(ctx.get("seeds", 2))
    resume = bool(ctx.get("resume", True))
    samples = load_asset(SAMPLES)
    entries: List[Dict[str, Any]] = []
    if prompts_path:
        payload = read_json(str(prompts_path))
        if isinstance(payload, dict):
            payload = payload.get("prompts", payload.get("items", []))
        if not isinstance(payload, list):
            raise InvalidInput("prompt file must be a JSON list, or an object with a 'prompts' list")
        for index, item in enumerate(payload):
            if isinstance(item, str):
                entries.append({"id": f"item-{index + 1:03d}", "prompt": item})
            elif isinstance(item, dict):
                prompt = item.get("prompt") or item.get("text") or ""
                if not prompt:
                    continue
                entries.append({"id": str(item.get("id", f"item-{index + 1:03d}")), "prompt": prompt,
                                "negative": item.get("negative", ""),
                                "model": item.get("model", "sdxl")})
    else:
        for index, item in enumerate(samples.get("batch_prompts", [])):
            prompt = item if isinstance(item, str) else item.get("prompt", "")
            if prompt:
                entries.append({"id": f"sample-{index + 1:03d}", "prompt": prompt})
    if not entries:
        raise InvalidInput("no prompts found: supply --set prompts=<file.json> or use the packaged samples")
    jobs: List[Dict[str, Any]] = []
    state_path = os.path.join(ctx.out_dir, "batch-state.json")
    done = set()
    if resume and os.path.isfile(state_path):
        try:
            done = set(json.loads(read_text(state_path)).get("completed", []))
        except Exception:
            done = set()
    for entry in entries:
        for variant in range(max(1, seeds)):
            seed = deterministic_seed(f"{entry['id']}:{variant}")
            job_id = f"{entry['id']}-s{variant}"
            jobs.append({
                "id": job_id,
                "prompt": entry["prompt"],
                "negative": entry.get("negative", ""),
                "model": entry.get("model", "sdxl"),
                "width": width,
                "height": height,
                "seed": seed,
                "output": f"{job_id}.png",
                "status": "skipped" if job_id in done else "pending",
            })
    pending = [j for j in jobs if j["status"] == "pending"]
    manifest = {
        "total_jobs": len(jobs),
        "pending": len(pending),
        "skipped": len(jobs) - len(pending),
        "resolution": f"{width}x{height}",
        "seeds_per_prompt": seeds,
        "jobs": jobs,
    }
    manifest_path = ctx.out("batch-manifest.json")
    core.write_json(manifest_path, manifest)
    ctx.emit(manifest_path)
    state = {"completed": sorted(done | {j["id"] for j in pending if j["status"] == "done"}),
             "updated_utc": utc_now()}
    core.write_json(state_path, state)
    ctx.emit(state_path)
    body = "\n".join([
        f"Batch manifest with {len(jobs)} job(s) ({len(pending)} pending)",
        "",
        md_table([[j["id"], j["prompt"][:60], j["seed"], j["status"]] for j in jobs[:40]],
                 ["Job", "Prompt", "Seed", "Status"]),
        "",
        "Runner notes:",
        _lines([
            "Each job carries a deterministic seed so a failed job can be repeated exactly.",
            "The manifest is the resume state: completed job ids are skipped on the next run.",
            "This tool prepares the manifest; it does not render images itself.",
            "Render with your own engine, then write the produced file names back into batch-state.json.",
        ]),
    ])
    data = {"manifest": manifest, "state_file": core.rel_path(state_path)}
    return _report(ctx, "batch-manifest", "Batch prompt run manifest", body, data,
                   f"Prepared {len(jobs)} batch job(s), {len(pending)} pending.",
                   {"jobs": len(jobs), "pending": len(pending)}, [core.rel_path(manifest_path)])


def comfyui_automator(ctx) -> Dict[str, Any]:
    workflow_path = ctx.get("workflow")
    endpoint = ctx.get("endpoint")
    prompt = ctx.get("prompt")
    timeout = float(ctx.get("timeout", 120))
    workflow: Dict[str, Any] = {}
    if workflow_path:
        loaded = read_json(str(workflow_path))
        if not isinstance(loaded, dict):
            raise InvalidInput("the ComfyUI workflow must be a JSON object")
        workflow = loaded
    templates = load_asset(TEMPLATES)
    template_ids = list(templates.get("comfyui_templates", {}).keys()) if isinstance(
        templates.get("comfyui_templates"), dict) else []
    plan = {
        "mode": "live" if endpoint else "specification",
        "endpoint": endpoint or "${COMFYUI_URL}",
        "workflow_name": workflow.get("name", "text-to-image"),
        "prompt_overrides": {},
        "steps": [
            {"n": 1, "action": "POST /prompt", "body": {"prompt": "<workflow graph>",
                                                       "client_id": "<uuid>"}},
            {"n": 2, "action": "GET /history/<prompt_id>", "body": None},
            {"n": 3, "action": "GET /view?filename=<file>", "body": None},
        ],
        "timeout_seconds": timeout,
        "poll_interval_seconds": 2,
        "max_poll_seconds": timeout,
        "cleanup": "never delete node outputs from the ComfyUI server; download and archive locally",
        "error_handling": {"on_timeout": "leave the job running and record its prompt id for later retrieval",
                           "on_400": "the graph is invalid: print the node id from the response"},
        "template_ids_available": template_ids,
    }
    if prompt:
        plan["prompt_overrides"] = {"inputs": {"text": str(prompt)}}
    if workflow:
        plan["workflow_name"] = workflow.get("name", plan["workflow_name"])
        plan["nodes"] = len(workflow.get("nodes", {})) if isinstance(workflow.get("nodes"), dict) else 0
    body = "\n".join([
        "ComfyUI API automation plan",
        "",
        "```json",
        json.dumps(plan, indent=2, ensure_ascii=False),
        "```",
        "",
        "Rules:",
        _lines([
            "Set the server URL in the environment; do not hardcode a private host.",
            "Send the prompt to /prompt, then poll /history until the job leaves the queue.",
            "Download the outputs with /view and archive them locally.",
            "Keep the prompt id of every submitted job so a timed-out run can still be collected.",
            "The server may be single-tenant: confirm with the owner before scheduling heavy jobs.",
        ]),
    ])
    data = plan
    return _report(ctx, "comfyui-automation", "ComfyUI API automation plan", body, data,
                   "Produced a ComfyUI automation plan.",
                   {"mode": plan["mode"]})


def asset_naming(ctx) -> Dict[str, Any]:
    path = ctx.get("path")
    campaign = str(ctx.get("campaign", "campaign"))
    template = str(ctx.get("template", "{campaign}_{index}_{slug}"))
    copy = bool(ctx.get("copy", True))
    target_dir = ctx.path("path")
    files: List[str] = []
    if target_dir and os.path.isdir(target_dir):
        for name in sorted(os.listdir(target_dir)):
            full = os.path.join(target_dir, name)
            if os.path.isfile(full) and not name.startswith("."):
                files.append(full)
    elif target_dir and os.path.isfile(target_dir):
        files = [target_dir]
    if not files:
        sample = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset-sample-image.png")
        if not os.path.isfile(sample):
            raise InvalidInput("no files found to rename; pass --set path=<image directory>")
        files = [sample]
        ctx.warn("no folder was supplied: the packaged sample image was used to demonstrate the "
                 "naming template")
    plan: List[Dict[str, Any]] = []
    for index, source in enumerate(files, start=1):
        stem = os.path.splitext(os.path.basename(source))[0]
        slug = slugify(stem, max_len=48) or "asset"
        name = template.format(campaign=slugify(campaign, max_len=32), index=index, slug=slug,
                                stem=slug, ext=os.path.splitext(source)[1].lstrip("."),
                                width="", height="")
        if not os.path.splitext(name)[1]:
            name += os.path.splitext(source)[1]
        plan.append({"source": core.rel_path(source), "new_name": name})
    if not copy:
        if not ctx.approved:
            return {
                "status": "approval_required",
                "summary": f"Renaming {len(plan)} file(s) in place requires approval.",
                "data": {"planned": plan},
                "report": "# Approval required\n\nRenaming files in place cannot be undone by this tool.\n\n"
                          + md_table([[p["source"], p["new_name"]] for p in plan], ["Current", "New name"]),
                "artifacts": [],
                "warnings": ["Re-run with --yes to apply the rename."],
                "metrics": {"files": len(plan)},
            }
    out_dir = ctx.out("renamed")
    core.ensure_dir(out_dir)
    applied: List[Dict[str, str]] = []
    for item in plan:
        source = item["source"]
        source_abs = os.path.abspath(source)
        destination = os.path.join(out_dir, item["new_name"]) if copy else os.path.join(
            os.path.dirname(source_abs), item["new_name"])
        if os.path.exists(destination):
            item["skipped"] = "destination already exists"
            continue
        shutil.copy2(source_abs, destination) if copy else os.rename(source_abs, destination)
        applied.append({"from": core.rel_path(source_abs), "to": core.rel_path(destination)})
    body = "\n".join([
        f"Asset naming plan ({'copy' if copy else 'rename in place'})",
        "",
        md_table([[p["source"], p["new_name"], p.get("skipped", "ok")] for p in plan],
                 ["Source", "New name", "Status"]),
        "",
        f"Applied: {len(applied)} of {len(plan)}.",
        "",
        "Naming rules:",
        _lines([
            "Keep the template short and reversible; never overwrite an existing file.",
            "Include the campaign, an index and a readable slug so a file sorts sensibly.",
            "Lowercase, hyphen separated, no spaces and no punctuation that breaks URLs.",
            "Record the mapping in the delivery manifest so downstream systems can trace it.",
        ]),
    ])
    data = {"template": template, "campaign": campaign, "mode": "copy" if copy else "rename",
            "planned": plan, "applied": applied}
    return _report(ctx, "asset-naming", "Asset naming and metadata", body, data,
                   f"Named {len(applied)} of {len(plan)} file(s).",
                   {"files": len(plan), "applied": len(applied)})


def gpu_queue_orchestrator(ctx) -> Dict[str, Any]:
    jobs_path = ctx.get("jobs")
    workers = max(1, int(ctx.get("workers", 2)))
    vram_gb = float(ctx.get("vram_gb", 12))
    jobs: List[Dict[str, Any]] = []
    if jobs_path:
        payload = read_json(str(jobs_path))
        if isinstance(payload, dict):
            payload = payload.get("jobs", [])
        if not isinstance(payload, list):
            raise InvalidInput("jobs file must be a JSON list or an object with a 'jobs' list")
        for index, item in enumerate(payload):
            if isinstance(item, str):
                jobs.append({"id": f"job-{index + 1:03d}", "prompt": item, "vram_gb": 0})
            elif isinstance(item, dict):
                jobs.append({"id": str(item.get("id", f"job-{index + 1:03d}")),
                             "prompt": str(item.get("prompt", "")),
                             "vram_gb": float(item.get("vram_gb", 0) or 0),
                             "model": item.get("model", "sdxl"),
                             "priority": int(item.get("priority", 5))})
    else:
        jobs = [
            {"id": "job-001", "prompt": "a quiet harbour at dawn", "vram_gb": 0, "model": "sdxl", "priority": 5},
            {"id": "job-002", "prompt": "a macro shot of a dew drop", "vram_gb": 0, "model": "sdxl", "priority": 5},
            {"id": "job-003", "prompt": "an isometric cafe interior", "vram_gb": 0, "model": "sdxl", "priority": 3},
        ]
    models = load_asset(PROFILES)["models"]
    seconds_by_model = {m["id"]: m.get("seconds_per_image", 5.0) for m in models}
    for job in jobs:
        job["est_seconds"] = round(seconds_by_model.get(job.get("model", "sdxl"), 5.6), 2)
        job["est_vram_gb"] = job.get("vram_gb") or next(
            (m["min_vram_gb"] for m in models if m["id"] == job.get("model", "sdxl")), 8)
    order = sorted(jobs, key=lambda j: (j.get("priority", 5), -j["est_seconds"]))
    total_seconds = sum(j["est_seconds"] for j in order)
    slots: List[List[str]] = [[] for _ in range(workers)]
    slot_times = [0.0] * workers
    schedule: List[Dict[str, Any]] = []
    for job in order:
        fits = [i for i in range(workers)
                if job["est_vram_gb"] <= vram_gb and slot_times[i] + job["est_seconds"] <= max(slot_times) + 1e-9]
        if not fits:
            index = min(range(workers), key=lambda i: slot_times[i])
        else:
            index = fits[0]
        schedule.append({"id": job["id"], "worker": index, "start_seconds": round(slot_times[index], 2),
                         "duration_seconds": job["est_seconds"], "priority": job.get("priority", 5)})
        slots[index].append(job["id"])
        slot_times[index] += job["est_seconds"]
    makespan = max(slot_times) if slot_times else 0.0
    plan = {
        "workers": workers,
        "vram_gb_per_worker": vram_gb,
        "jobs": len(order),
        "order": [j["id"] for j in order],
        "slots": slots,
        "schedule": schedule,
        "total_compute_seconds": round(total_seconds, 2),
        "makespan_seconds": round(makespan, 2),
        "utilisation": round(total_seconds / max(1e-9, makespan * workers), 3),
        "policy": ["sort by priority, then longest job first",
                   "keep every job inside the per-worker VRAM budget",
                   "requeue on OOM with the same seed and a reduced resolution"],
    }
    body = "\n".join([
        "GPU queue plan",
        "",
        md_table([[s["id"], s["worker"], s["start_seconds"], s["duration_seconds"]]
                  for s in schedule], ["Job", "Worker", "Start (s)", "Duration (s)"]),
        "",
        f"Makespan {plan['makespan_seconds']}s across {workers} worker(s); utilisation {plan['utilisation']}.",
        "",
        "Policy:",
        _lines(plan["policy"]),
    ])
    data = plan
    return _report(ctx, "gpu-queue", "GPU queue schedule", body, data,
                   f"Scheduled {len(order)} job(s) across {workers} worker(s).",
                   {"jobs": len(order), "makespan": plan["makespan_seconds"]})


def cost_optimizer(ctx) -> Dict[str, Any]:
    model = str(ctx.get("model", "sdxl"))
    steps = int(ctx.get("steps", 30))
    batch_size = max(1, int(ctx.get("batch_size", 1)))
    resolution = str(ctx.get("resolution", "1024x1024"))
    hourly_cost = float(ctx.get("hourly_cost", 0.85))
    profile = _model(model)
    try:
        width, height = (int(v) for v in resolution.lower().split("x"))
    except ValueError:
        raise InvalidInput(f"resolution must look like 1024x1024, got {resolution!r}")
    base_seconds = float(profile.get("seconds_per_image", 5.6))
    pixel_factor = (width * height) / (1024 * 1024)
    step_factor = steps / 30.0
    seconds = base_seconds * pixel_factor * step_factor
    per_image_cost = seconds / 3600.0 * hourly_cost
    batch_seconds = seconds * batch_size
    options = []
    for alt_steps in (10, 20, 30, 40, 50):
        alt_seconds = base_seconds * pixel_factor * (alt_steps / 30.0)
        options.append({"steps": alt_steps, "seconds_per_image": round(alt_seconds, 2),
                        "cost_per_image": round(alt_seconds / 3600.0 * hourly_cost, 6),
                        "throughput_per_hour": round(3600 / alt_seconds, 1)})
    best = min(options, key=lambda o: o["cost_per_image"])
    fastest = max(options, key=lambda o: o["throughput_per_hour"])
    plan = {
        "model": model,
        "model_name": profile.get("name"),
        "steps": steps,
        "batch_size": batch_size,
        "resolution": resolution,
        "hourly_cost_usd": hourly_cost,
        "estimated_seconds_per_image": round(seconds, 2),
        "estimated_cost_per_image_usd": round(per_image_cost, 6),
        "estimated_cost_per_1000_usd": round(per_image_cost * 1000, 2),
        "throughput_images_per_hour": round(3600 / seconds, 1),
        "batch_seconds": round(batch_seconds, 2),
        "step_sweep": options,
        "cheapest_option": best,
        "fastest_option": fastest,
        "notes": [
            "Timings are planning estimates from a 24 GB reference card and scale linearly with pixels and steps.",
            "Quality usually degrades below 20 steps on most checkpoints.",
            "Batch size amortises weight loading but needs more VRAM; see the VRAM planner.",
            "Turbo and LCM checkpoints render 8-10x faster than a 30-step base model.",
        ],
    }
    body = "\n".join([
        "Cost and throughput estimate",
        "",
        md_table([[k, v] for k, v in plan.items() if k not in ("step_sweep", "notes")], ["Setting", "Value"]),
        "",
        md_table([[o["steps"], o["seconds_per_image"], f"{o['cost_per_image']:.6f}", o["throughput_per_hour"]]
                  for o in options], ["Steps", "Seconds/image", "USD/image", "Images/hour"]),
        "",
        "Notes:",
        _lines(plan["notes"]),
    ])
    data = plan
    return _report(ctx, "cost-estimate", "Cost and throughput estimate", body, data,
                   f"Estimated {plan['estimated_cost_per_image_usd']:.6f} USD per image.",
                   {"seconds": plan["estimated_seconds_per_image"], "cost": plan["estimated_cost_per_image_usd"]})


def qa_pipeline_builder(ctx) -> Dict[str, Any]:
    path = ctx.path("path")
    policy = str(ctx.get("policy", "default"))
    rubric = load_asset(RUBRIC)
    policies = {"default": {"delivery_channel": "web", "min_score": 80,
                            "require_alt_text": True, "max_bytes": rubric["delivery"]["web"]["max_bytes"]},
                "print": {"delivery_channel": "print", "min_score": 85, "require_alt_text": False,
                          "min_dpi": rubric["print"]["dpi_targets"].get("photo", 300)
                          if isinstance(rubric["print"].get("dpi_targets"), dict) else 300}}
    policy = policy if policy in policies else "default"
    settings = policies[policy]
    stages = [
        {"stage": "integrity", "tool": "asset-image-probe", "fails_on": "unreadable or truncated file"},
        {"stage": "dimensions", "tool": "asset-image-probe", "fails_on": "below the channel minimum"},
        {"stage": "technical-quality", "tool": "quality-evaluation.artifact-detection-specialist",
         "fails_on": "artifact score below the threshold"},
        {"stage": "prompt-adherence", "tool": "quality-evaluation.prompt-adherence-evaluator",
         "fails_on": "adherence below the threshold"},
        {"stage": "contrast", "tool": "accessibility-delivery.color-contrast-accessibility-reviewer",
         "fails_on": "contrast ratio below 4.5 for body text or 3.0 for large text"},
        {"stage": "rights-and-safety", "tool": "governance-provenance.copyright-risk-screener",
         "fails_on": "a high-risk hit"},
        {"stage": "delivery", "tool": "accessibility-delivery.delivery-readiness-auditor",
         "fails_on": "a missing format or an over-weight file"},
    ]
    pipeline = {
        "policy": policy,
        "input": core.rel_path(path) if path else None,
        "stages": stages,
        "settings": settings,
        "reporting": {"on_failure": "write the failing asset id, the stage and the observed value",
                      "summary": "write a per-run JSON summary next to the outputs",
                      "exit_code": "non-zero when any stage fails"},
        "execution": "run each stage through `python3 tools-image-toolkit.py run <capability>`; "
                     "the pipeline is an ordered manifest, not a hidden runner",
        "quarantine": "move failing assets to a quarantine directory instead of deleting them",
    }
    body = "\n".join([
        "Automated QA pipeline",
        "",
        md_table([[s["stage"], s["tool"], s["fails_on"]] for s in stages],
                 ["Stage", "Capability", "Fails when"]),
        "",
        "Policy settings:",
        "```json",
        json.dumps(settings, indent=2, ensure_ascii=False) if settings else "(none)",
        "```",
        "",
        "Reporting:",
        _lines([f"{k}: {v}" for k, v in pipeline["reporting"].items()]),
        "",
        "Execution:",
        pipeline["execution"],
    ])
    data = pipeline
    return _report(ctx, "qa-pipeline", "Automated QA pipeline", body, data,
                   f"Built a {len(stages)}-stage QA pipeline for policy '{policy}'.",
                   {"stages": len(stages), "policy": policy})


def hitl_workflow(ctx) -> Dict[str, Any]:
    workflow = str(ctx.get("workflow", "batch_generation"))
    volume = int(ctx.get("volume", 500))
    design = {
        "workflow": workflow,
        "volume": volume,
        "stages": [
            {"name": "generate", "owner": "automation", "automatable": True,
             "output": "candidate set with seeds and parameters"},
            {"name": "auto-qa", "owner": "automation", "automatable": True,
             "output": "pass/fail per asset from the QA pipeline"},
            {"name": "triage", "owner": "reviewer", "automatable": False,
             "output": "rejects with a defect class"},
            {"name": "creative-review", "owner": "creative lead", "automatable": False,
             "output": "approved candidates with notes"},
            {"name": "rights-and-release", "owner": "rights owner", "automatable": False,
             "output": "release checklist and disclosure text"},
            {"name": "deliver", "owner": "automation", "automatable": True,
             "output": "published assets with provenance records"},
        ],
        "queues": {"auto": "everything passes the automated checks", "human": "everything else"},
        "sampling": {"auto_review_share": 0.10,
                     "rule": "always review every rejected asset plus a random 10% of accepted ones"},
        "service_levels": {"triage_hours": 4, "creative_review_hours": 24, "release_hours": 8},
        "escalation": {"to_creative_lead": "3 consecutive rejects for the same prompt template",
                       "to_rights_owner": "any consent or rights question",
                       "to_engineering": "an auto-QA stage fails on more than 5% of assets"},
        "audit": "every decision is logged with the asset id, the stage, the reviewer and the reason",
    }
    body = "\n".join([
        "Human-in-the-loop workflow",
        "",
        md_table([[s["name"], s["owner"], "yes" if s["automatable"] else "human", s["output"]]
                  for s in design["stages"]], ["Stage", "Owner", "Automated", "Output"]),
        "",
        "Service levels:",
        md_table([[k, f"{v} h"] for k, v in design["service_levels"].items()], ["Stage", "Target"]),
        "",
        "Escalation:",
        _lines([f"{k}: {v}" for k, v in design["escalation"].items()]),
        "",
        "Audit rule:",
        design["audit"],
    ])
    data = design
    return _report(ctx, "hitl-workflow", "Human-in-the-loop workflow", body, data,
                   f"Designed a {len(design['stages'])}-stage review workflow for {volume} items.",
                   {"stages": len(design["stages"]), "volume": volume})


# ---------------------------------------------------------------------------
# Category 21 - Performance and hardware optimisation
# ---------------------------------------------------------------------------

PRECISION_FACTOR = {"fp32": 2.0, "fp16": 1.0, "bf16": 1.0, "fp8": 0.55, "int8": 0.55}

ACTIVATION_GB_PER_MP = {
    "sd15": 1.1, "sd21": 1.4, "sdxl": 2.4, "sdxl-turbo": 2.4,
    "sdxl-photoreal": 2.4, "sdxl-anime": 2.4, "sd3-medium": 3.6,
    "sd3-large": 5.2, "flux1": 5.6,
}


def _vram_estimate(model: Dict[str, Any], width: int, height: int, batch_size: int,
                   precision: str, controlnets: int, loras: int) -> Dict[str, Any]:
    factor = PRECISION_FACTOR.get(precision, 1.0)
    weights = (float(model["checkpoint_gb"])
               + float(model["text_encoder_gb"]) * int(model.get("text_encoder_count", 1))
               + float(model["vae_gb"]))
    weights *= factor
    megapixels = (width * height) / 1_000_000.0
    per_mp = ACTIVATION_GB_PER_MP.get(model["id"], 2.5)
    activations = megapixels * per_mp * batch_size * (1.0 if precision in ("fp16", "bf16") else 1.3)
    control = controlnets * 1.45 * factor
    lora = loras * 0.12 * factor
    overhead = 0.6 + 0.25 * batch_size
    total = weights + activations + control + lora + overhead
    return {
        "model": model["id"],
        "precision": precision,
        "weights_gb": round(weights, 2),
        "activations_gb": round(activations, 2),
        "controlnet_gb": round(control, 2),
        "lora_gb": round(lora, 2),
        "overhead_gb": round(overhead, 2),
        "total_gb": round(total, 2),
        "megapixels": round(megapixels, 3),
        "batch_size": batch_size,
    }


def vram_planner(ctx) -> Dict[str, Any]:
    model_id = str(ctx.get("model", "sdxl"))
    width = int(ctx.get("width", 1024))
    height = int(ctx.get("height", 1024))
    batch_size = int(ctx.get("batch_size", 1))
    precision = str(ctx.get("precision", "fp16"))
    controlnets = int(ctx.get("controlnets", 0))
    loras = int(ctx.get("loras", 0))
    model = _model(model_id)
    estimate = _vram_estimate(model, width, height, batch_size, precision, controlnets, loras)
    tiers = load_asset(PROFILES)["gpu_tiers"]
    fits = [t for t in tiers if t["vram_gb"] >= estimate["total_gb"]]
    suggestions = []
    if estimate["total_gb"] > 24:
        suggestions.append("enable VAE tiling and attention slicing to cut the activation peak")
        suggestions.append("render at 768 px and upscale in two passes")
        suggestions.append("switch the VAE to fp16 or tiled decoding")
    elif estimate["total_gb"] > 16:
        suggestions.append("enable attention slicing and keep the batch size at 1")
        suggestions.append("render at 896 px and upscale rather than rendering at full size")
    else:
        suggestions.append("the configuration fits a mid-range card; batch size 2 is usually still safe")
    body = "\n".join([
        "VRAM budget",
        "",
        md_table([[k, v] for k, v in estimate.items()], ["Component", "Value"]),
        "",
        ("Fits: " + ", ".join(f"{t['id']} ({t['vram_gb']} GB, {t['typical']})" for t in fits))
        if fits else "No tier in the packaged list is large enough for this configuration.",
        "",
        "Advice:",
        _lines(suggestions),
        "",
        "Method:",
        _lines([
            "Weights = checkpoint + text encoders + VAE, scaled by the precision factor.",
            "Activations = latent area x per-megapixel working set x batch size.",
            "ControlNet and LoRA add their own resident weights.",
            "The 0.6 GB overhead covers the CUDA context and the sampler scratch buffers.",
            "These are planning estimates; measure on the target card before committing to a batch size.",
        ]),
    ])
    data = estimate
    return _report(ctx, "vram-plan", "VRAM budget plan", body, data,
                   f"Estimated {estimate['total_gb']} GB of VRAM for this configuration.",
                   {"total_gb": estimate["total_gb"]})


def low_memory_optimizer(ctx) -> Dict[str, Any]:
    vram = float(ctx.get("vram_gb", 8))
    model_id = str(ctx.get("model", "sdxl"))
    model = _model(model_id)
    estimate = _vram_estimate(model, 1024, 1024, 1, "fp16", 0, 0)
    actions = [
        {"action": "enable VAE tiling", "saving": "0.3-0.8 GB", "cost": "slightly slower decoding"},
        {"action": "enable attention slicing", "saving": "0.5-1.5 GB", "cost": "noticeably slower at high resolution"},
        {"action": "use the fp16 VAE", "saving": "0.2-0.4 GB", "cost": "possible black frames on old VAEs"},
        {"action": "keep the batch size at 1", "saving": "1.5-3 GB per extra image", "cost": "lower throughput"},
        {"action": "render at 768 px and upscale", "saving": "1.0-2.0 GB", "cost": "one extra pass"},
        {"action": "use xformers or SDPA attention", "saving": "0.8-2.0 GB", "cost": "needs a matching torch build"},
        {"action": "offload the text encoder after prompt encoding", "saving": "0.3-1.4 GB", "cost": "one extra copy per run"},
    ]
    if vram < 8:
        actions.insert(0, {"action": "switch to a smaller checkpoint (sd15 or sd21)",
                            "saving": "3-5 GB", "cost": "lower native resolution and weaker prompt adherence"})
    if vram < 6:
        actions.insert(0, {"action": "run the model on CPU with a small VAE",
                            "saving": "all of it", "cost": "minutes instead of seconds per image"})
    tiers = load_asset(PROFILES)["gpu_tiers"]
    body = "\n".join([
        f"Low-memory plan for {model.get('name')} on a {vram} GB card",
        "",
        md_table([[a["action"], a["saving"], a["cost"]] for a in actions], ["Action", "Saving", "Cost"]),
        "",
        md_table([[t["id"], t["vram_gb"], t["typical"], t["notes"]] for t in tiers],
                 ["Tier", "VRAM", "Hardware", "Notes"]),
        "",
        f"Unoptimised estimate at 1024x1024 fp16: {estimate['total_gb']} GB.",
        "",
        "Order of operations:",
        _lines([
            "Apply the free wins first: fp16 VAE, attention slicing, batch size 1.",
            "Then reduce the working set: render smaller and upscale.",
            "Only then change the checkpoint or the hardware.",
            "Re-measure after every change; the savings interact and do not simply add up.",
        ]),
    ])
    data = {"vram_gb": vram, "model": model_id, "baseline": estimate, "actions": actions}
    return _report(ctx, "low-memory-plan", "Low-memory diffusion plan", body, data,
                   f"Produced {len(actions)} low-memory actions for {vram} GB.",
                   {"actions": len(actions)})


def speed_tuner(ctx) -> Dict[str, Any]:
    model_id = str(ctx.get("model", "sdxl"))
    target = float(ctx.get("target", 20))
    model = _model(model_id)
    baseline = float(model.get("seconds_per_image", 5.6))
    options = []
    for alt in ("sd15", "sd21", "sdxl", "sdxl-turbo"):
        try:
            candidate = _model(alt)
        except InvalidInput:
            continue
        options.append({"model": alt, "seconds_per_image": candidate.get("seconds_per_image"),
                        "quality_score": candidate.get("quality_score"),
                        "speed_score": candidate.get("speed_score")})
    fastest = min(options, key=lambda o: o["seconds_per_image"] or 999)
    steps_needed = max(1, round(30 * target / baseline))
    plan = {
        "target_seconds_per_image": target,
        "current_model": model_id,
        "current_seconds_per_image": baseline,
        "meets_target": baseline <= target,
        "steps_to_hit_target": steps_needed,
        "options": sorted(options, key=lambda o: o["seconds_per_image"] or 999),
        "fastest_option": fastest,
        "techniques": [
            {"technique": "switch to a distilled checkpoint (SDXL-Turbo, LCM)", "gain": "5-10x",
             "quality_cost": "less detail and weaker guidance control"},
            {"technique": "reduce the sampler step count to 20", "gain": "about 1.4x",
             "quality_cost": "slightly softer output"},
            {"technique": "use DPM++ 2M with the Karras scheduler", "gain": "about 1.3x",
             "quality_cost": "none at equal steps"},
            {"technique": "render at 768 px and upscale to 1024", "gain": "about 1.6x",
             "quality_cost": "slightly less fine detail"},
            {"technique": "batch multiple prompts on one weight load", "gain": "1.2-1.5x",
             "quality_cost": "more VRAM"},
            {"technique": "quantise to fp8 or int8", "gain": "about 1.4x",
             "quality_cost": "small quality loss, needs a matching runtime"},
        ],
    }
    body = "\n".join([
        f"Speed plan for {model.get('name')}",
        "",
        md_table([[k, v] for k, v in plan.items() if k not in ("techniques", "options")], ["Setting", "Value"]),
        "",
        md_table([[o["model"], o["seconds_per_image"], o["speed_score"], o["quality_score"]]
                  for o in plan["options"]], ["Model", "Seconds/image", "Speed score", "Quality score"]),
        "",
        "Techniques:",
        md_table([[t["technique"], t["gain"], t["quality_cost"]] for t in plan["techniques"]],
                 ["Technique", "Speed-up", "Quality cost"]),
        "",
        "Measure on the target hardware: these ratios are planning estimates, not guarantees.",
    ])
    data = plan
    return _report(ctx, "speed-plan", "Inference speed plan", body, data,
                   f"Target {target}s per image; current estimate {baseline}s.",
                   {"baseline": baseline, "meets_target": plan["meets_target"]})


def gpu_troubleshooter(ctx) -> Dict[str, Any]:
    symptom = str(ctx.get("symptom", "") or "")
    entries = [
        {"match": ["out of memory", "oom", "cuda out of memory"],
         "cause": "not enough VRAM for the resolution, batch size, ControlNets or LoRAs",
         "fixes": ["lower the resolution or the batch size", "enable VAE tiling and attention slicing",
                   "close other GPU applications", "switch to a smaller checkpoint"]},
        {"match": ["black image", "black output", "all black"],
         "cause": "a broken or mismatched VAE, or a scheduler and sampler combination the checkpoint cannot use",
         "fixes": ["switch back to the original VAE", "use the fp16 VAE only if the checkpoint supports it",
                   "set the scheduler to match the model family", "render at the checkpoint's base resolution"]},
        {"match": ["nan", "garbage", "noise", "purple"],
         "cause": "fp16 overflow, an unsupported precision or a corrupt checkpoint",
         "fixes": ["switch to fp32 or bf16", "re-download the checkpoint and verify the file size",
                   "lower the CFG scale", "disable xformers and use the default attention"]},
        {"match": ["slow", "slow down", "taking forever", "freeze", "hang"],
         "cause": "CPU offloading, a cold page cache, attention without an optimised backend, or an under-powered GPU",
         "fixes": ["keep the checkpoint in VRAM", "install an optimised attention backend",
                   "close other GPU workloads", "check GPU utilisation with nvidia-smi"]},
        {"match": ["cuda not found", "no cuda", "driver", "nvidia-smi not found"],
         "cause": "the toolkit or the driver does not match the installed CUDA runtime",
         "fixes": ["check `nvidia-smi` runs", "install the driver that matches the CUDA runtime",
                   "verify the torch build reports CUDA support"]},
        {"match": ["text", "gibberish", "letters", "word"],
         "cause": "the checkpoint has weak text rendering, or the prompt asks for text the encoder cannot spell",
         "fixes": ["use a checkpoint with stronger typography", "keep the text short and quoted",
                   "render the text as a separate layer"]},
        {"match": ["duplicate", "two heads", "extra fingers", "extra limb"],
         "cause": "prompt repetition, an over-weighted subject term or a low-quality VAE",
         "fixes": ["remove repeated subject words", "lower the CFG scale to 5-6",
                   "use a negative prompt for the duplicate", "use a modern checkpoint"]},
        {"match": ["controlnet", "control net", "pose not followed"],
         "cause": "the control image resolution does not match the generation resolution",
         "fixes": ["resize the control image to the target resolution",
                   "lower the control strength to 0.6-0.8", "check the preprocessor type"]},
    ]
    lowered = symptom.lower()
    matched = []
    for entry in entries:
        hits = [m for m in entry["match"] if m in lowered]
        if hits:
            matched.append({"matched_on": hits, "cause": entry["cause"], "fixes": entry["fixes"]})
    if not matched and symptom:
        matched = [{"matched_on": [], "cause": "no known symptom matched",
                    "fixes": ["enable verbose logging", "reproduce with the smallest possible prompt",
                              "capture the exact error text and the model, sampler and settings",
                              "check the toolkit doctor output for the runtime environment"]}]
    body = "\n".join([
        "GPU troubleshooting",
        "",
        f"Symptom: {symptom or '(not specified)'}",
        "",
        md_table([[m["cause"], "; ".join(m["fixes"])] for m in matched],
                 ["Likely cause", "Fixes"]) if matched else
        md_table([["(no symptom supplied)", "describe the symptom to get targeted advice"]],
                 ["Likely cause", "Fixes"]),
        "",
        "General order of investigation:",
        _lines([
            "Confirm the environment first: `nvidia-smi` and the toolkit doctor output.",
            "Reduce to a minimal case: 512 px, batch 1, 20 steps, no ControlNet, no LoRA.",
            "Change one variable at a time and record what changed.",
            "Check GPU utilisation: 0% means the work is running on the CPU.",
        ]),
    ])
    data = {"symptom": symptom, "matches": matched}
    return _report(ctx, "gpu-troubleshoot", "GPU troubleshooting", body, data,
                   f"Matched {len(matched)} known symptom pattern(s).",
                   {"matches": len(matched)})


def model_storage_manager(ctx) -> Dict[str, Any]:
    path = ctx.path("path")
    apply = bool(ctx.get("apply", False))
    extensions = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")
    entries: List[Dict[str, Any]] = []
    total = 0
    if path and os.path.isdir(path):
        for root, _dirs, names in os.walk(path):
            for name in sorted(names):
                if name.lower().endswith(extensions):
                    full = os.path.join(root, name)
                    size = os.path.getsize(full)
                    total += size
                    entries.append({"path": core.rel_path(full), "name": name,
                                    "size_bytes": size, "size_mb": round(size / 1_048_576, 1),
                                    "modified_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                 time.gmtime(os.path.getmtime(full)))})
    entries.sort(key=lambda e: -e["size_bytes"])
    by_hash: Dict[str, List[str]] = {}
    for entry in entries:
        full = entry["path"]
        absolute = os.path.abspath(full)
        if os.path.isfile(absolute):
            digest = core.sha256_file(absolute)
            by_hash.setdefault(digest, []).append(entry["path"])
    duplicates = {d: paths for d, paths in by_hash.items() if len(paths) > 1}
    reclaimable = sum(len(paths) - 1 for paths in duplicates.values())
    stale = [e for e in entries if e["modified_utc"] < "2000-01-01"]
    plan = {
        "root": core.rel_path(path) if path else None,
        "files": len(entries),
        "total_bytes": total,
        "total_gb": round(total / (1024 ** 3), 2),
        "duplicate_sets": [{"sha256": d, "paths": paths} for d, paths in duplicates.items()],
        "duplicate_files": reclaimable,
        "reclaimable_gb": round(sum(
            (os.path.getsize(p) for paths in duplicates.values() for p in paths[1:]), 0) / (1024 ** 3), 2),
        "largest": entries[:10],
        "apply": apply,
        "policy": [
            "Keep safetensors only; convert older .ckpt files once and delete the source.",
            "Store models outside the project tree and reference them by path.",
            "Delete a duplicate only after confirming no workflow points at it.",
            "Record every deletion in the manifest before running it.",
        ],
    }
    body = "\n".join([
        "Model storage inventory",
        "",
        md_table([[e["name"], f"{e['size_mb']} MB", e["modified_utc"]] for e in entries[:20]],
                 ["Model file", "Size", "Modified"]) if entries else
        md_table([["(no model files found)", "-", "-"]], ["Model file", "Size", "Modified"]),
        "",
        f"Total {plan['total_gb']} GB across {plan['files']} file(s); "
        f"{plan['duplicate_files']} duplicate file(s) worth about {plan['reclaimable_gb']} GB.",
        "",
        "Policy:",
        _lines(plan["policy"]),
    ])
    data = plan
    return _report(ctx, "model-storage", "Model storage inventory", body, data,
                   f"Inventoried {plan['files']} model file(s), {plan['total_gb']} GB.",
                   {"files": plan["files"], "total_gb": plan["total_gb"]})


def env_reproducibility(ctx) -> Dict[str, Any]:
    name = str(ctx.get("name", "diffusion-env"))
    spec = {
        "environment_name": name,
        "python": ">=3.10,<3.13",
        "system_packages": ["git", "ffmpeg (optional, for video output)"],
        "python_packages": [
            {"name": "torch", "constraint": "match the CUDA build of the host", "optional": False},
            {"name": "torchvision", "constraint": "same torch version", "optional": False},
            {"name": "pillow", "constraint": ">=10.0", "optional": True,
             "note": "the toolkit falls back to a pure-python codec when it is missing"},
            {"name": "numpy", "constraint": ">=1.26", "optional": True,
             "note": "the toolkit falls back to pure python when it is missing"},
            {"name": "safetensors", "constraint": ">=0.4", "optional": True},
            {"name": "transformers", "constraint": ">=4.40", "optional": True},
            {"name": "diffusers", "constraint": ">=0.27", "optional": True},
            {"name": "accelerate", "constraint": ">=0.30", "optional": True},
            {"name": "xformers", "constraint": "match the torch build", "optional": True},
        ],
        "environment_variables": [
            {"name": "IMAGE_API_TOKEN", "purpose": "token for a hosted generation API", "secret": True},
            {"name": "COMFYUI_URL", "purpose": "base URL of a ComfyUI server", "secret": False},
            {"name": "HF_HOME", "purpose": "cache location for downloaded checkpoints", "secret": False},
        ],
        "pinning": {
            "checkpoints": "record the sha256 of every checkpoint and LoRA",
            "python": "commit a lock file produced by `pip freeze`",
            "workflows": "store the workflow JSON next to the model it references",
            "seeds": "store the seed with every output",
        },
        "verification": [
            "python3 tools-image-toolkit.py doctor",
            "python3 tools-image-toolkit.py capabilities --json",
            "render one known seed and compare against the stored reference",
        ],
    }
    requirements = "\n".join(
        f"{p['name']}{'' if p['optional'] else ''}" for p in spec["python_packages"])
    lock_path = ctx.out(f"{slugify(name, 40)}-environment.json")
    core.write_json(lock_path, spec)
    ctx.emit(lock_path)
    body = "\n".join([
        "Environment reproducibility specification",
        "",
        "```json",
        json.dumps(spec, indent=2, ensure_ascii=False),
        "```",
        "",
        "Setup commands:",
        "```bash",
        "python3 -m venv .venv",
        ". .venv/bin/activate",
        "python3 -m pip install --upgrade pip",
        "pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121",
        "pip install -r tools-requirements.txt",
        "python3 tools-image-toolkit.py doctor",
        "```",
        "",
        "Pinning rules:",
        _lines([f"{k}: {v}" for k, v in spec["pinning"].items()]),
        "",
        "Secrets stay in the environment or a secret manager; never in this specification file.",
    ])
    data = spec
    return _report(ctx, "environment-spec", "Environment reproducibility specification", body, data,
                   f"Produced a reproducibility specification for '{name}'.",
                   {"packages": len(spec["python_packages"])}, [core.rel_path(lock_path)])


def thermal_monitor(ctx) -> Dict[str, Any]:
    log_path = ctx.path("log")
    samples: List[Dict[str, Any]] = []
    source = "none"
    if log_path and os.path.isfile(log_path):
        source = core.rel_path(log_path)
        text = read_text(log_path)
        if log_path.lower().endswith(".json"):
            payload = read_json(log_path)
            rows = payload if isinstance(payload, list) else payload.get("samples", [])
            for row in rows:
                if isinstance(row, dict):
                    samples.append({"timestamp": row.get("timestamp", ""),
                                    "temperature_c": float(row.get("temperature_c", row.get("temp_c", 0)) or 0),
                                    "load_pct": float(row.get("load_pct", row.get("utilisation_pct", 0)) or 0),
                                    "power_w": float(row.get("power_w", 0) or 0)})
        else:
            for line in text.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    samples.append({"timestamp": parts[0],
                                    "temperature_c": float(parts[1]),
                                    "load_pct": float(parts[2]) if len(parts) > 2 else 0.0,
                                    "power_w": float(parts[3]) if len(parts) > 3 else 0.0})
                except ValueError:
                    continue
    temps = [s["temperature_c"] for s in samples if s["temperature_c"]]
    loads = [s["load_pct"] for s in samples if s["load_pct"]]
    summary = {
        "source": source,
        "samples": len(samples),
        "max_temperature_c": max(temps) if temps else None,
        "mean_temperature_c": round(sum(temps) / len(temps), 1) if temps else None,
        "max_load_pct": max(loads) if loads else None,
        "mean_load_pct": round(sum(loads) / len(loads), 1) if loads else None,
    }
    thresholds = [
        {"level": "nominal", "temperature_c": "below 75", "action": "run normally"},
        {"level": "warm", "temperature_c": "75-85", "action": "reduce the batch size and the step count"},
        {"level": "hot", "temperature_c": "85-95", "action": "pause the queue, improve airflow, lower the power limit"},
        {"level": "throttling", "temperature_c": "above 95", "action": "stop rendering and let the card recover"},
    ]
    correlation = None
    if len(temps) > 2 and len(loads) == len(temps) and len(set(loads)) > 1:
        mean_t = sum(temps) / len(temps)
        mean_l = sum(loads) / len(loads)
        num = sum((t - mean_t) * (l - mean_l) for t, l in zip(temps, loads))
        den = (sum((t - mean_t) ** 2 for t in temps) * sum((l - mean_l) ** 2 for l in loads)) ** 0.5
        correlation = round(num / den, 3) if den else None
    body = "\n".join([
        "Thermal and reliability monitoring",
        "",
        f"Source: {source or 'no log supplied'}",
        "",
        md_table([[k, v] for k, v in summary.items()], ["Measure", "Value"]),
        "",
        ("Temperature / load correlation: " + str(correlation)) if correlation is not None else
        "Temperature / load correlation: not enough data",
        "",
        "Thresholds:",
        md_table([[t["level"], t["temperature_c"], t["action"]] for t in thresholds],
                 ["Level", "Temperature (C)", "Action"]),
        "",
        "Logging format:",
        "```",
        "timestamp,temperature_c,load_pct,power_w",
        "2026-01-01T10:00:00Z,62.0,85,210",
        "```",
        "",
        "Record the temperature and the load together: a hot card at low load points at cooling, "
        "a cool card at high load points at the workload.",
    ])
    data = {"summary": summary, "correlation": correlation, "thresholds": thresholds}
    return _report(ctx, "thermal-monitor", "Thermal and reliability monitoring", body, data,
                   f"Analysed {summary['samples']} thermal sample(s).",
                   {"samples": summary["samples"], "max_temp_c": summary["max_temperature_c"]})


def cloud_gpu_planner(ctx) -> Dict[str, Any]:
    model_id = str(ctx.get("model", "sdxl"))
    concurrency = max(1, int(ctx.get("concurrency", 2)))
    model = _model(model_id)
    profiles = load_asset(PROFILES)
    seconds = float(model.get("seconds_per_image", 5.6))
    cost_profiles = profiles["cost_profiles"]
    tiers = profiles["gpu_tiers"]
    estimates = []
    for profile in cost_profiles:
        hourly = float(profile["hourly_usd"])
        per_image = seconds / 3600.0 * hourly
        estimates.append({"profile": profile["id"], "label": profile["label"],
                          "hourly_usd": hourly,
                          "cost_per_image_usd": round(per_image, 6),
                          "cost_per_1000_usd": round(per_image * 1000, 2),
                          "images_per_hour_per_gpu": round(3600 / seconds, 1),
                          "images_per_hour_cluster": round(3600 / seconds * concurrency, 1)})
    cheapest = min(estimates, key=lambda e: e["cost_per_image_usd"])
    plan = {
        "model": model_id,
        "model_name": model.get("name"),
        "concurrency": concurrency,
        "seconds_per_image": seconds,
        "gpu_tiers": [{"id": t["id"], "vram_gb": t["vram_gb"], "typical": t["typical"],
                       "suitable": t["vram_gb"] >= model.get("min_vram_gb", 8)} for t in tiers],
        "cost_estimates": estimates,
        "cheapest": cheapest,
        "deployment": {
            "images": "run behind a small queue service that owns the checkpoint",
            "model_loading": "load the checkpoint once per worker and reuse it for every request",
            "scaling": "scale on queue depth, not on GPU temperature",
            "storage": "keep outputs in object storage; keep the checkpoints on a local disk",
            "network": "return image bytes over HTTP; never expose the worker port directly",
        },
        "notes": [
            "Timings are planning estimates from a 24 GB reference card.",
            "Cost per image is dominated by the checkpoint load time at low volume.",
            "A warm worker changes the cost by roughly 10-20% at high volume.",
        ],
    }
    body = "\n".join([
        "Cloud GPU deployment plan",
        "",
        md_table([[k, v] for k, v in plan.items()
                  if k in ("model", "model_name", "concurrency", "seconds_per_image", "cheapest")],
                 ["Setting", "Value"]),
        "",
        md_table([[e["label"], e["hourly_usd"], f"{e['cost_per_image_usd']:.6f}",
                   f"{e['cost_per_1000_usd']:.2f}", e["images_per_hour_cluster"]]
                  for e in estimates],
                 ["Cost profile", "USD/hour", "USD/image", "USD/1000 images", "Images/hour (cluster)"]),
        "",
        md_table([[t["id"], t["vram_gb"], t["typical"], "yes" if t["suitable"] else "no"]
                  for t in plan["gpu_tiers"]], ["GPU tier", "VRAM", "Hardware", "Sufficient for this model"]),
        "",
        "Deployment:",
        _lines([f"{k}: {v}" for k, v in plan["deployment"].items()]),
    ])
    data = plan
    return _report(ctx, "cloud-gpu-plan", "Cloud GPU deployment plan", body, data,
                   f"Planned a {concurrency}-GPU deployment of {model.get('name')}.",
                   {"concurrency": concurrency, "cost_per_image": cheapest["cost_per_image_usd"]})


# ---------------------------------------------------------------------------
# Category 23 - Accessibility, localisation and delivery (production side)
# ---------------------------------------------------------------------------


def cultural_reviewer(ctx) -> Dict[str, Any]:
    description = str(ctx.require("description"))
    locale = str(ctx.get("locale", "global"))
    rules = load_asset(SAFETY)["cultural"]
    lowered = description.lower()
    triggers = []
    keyword_map = {
        "gesture": ["thumbs up", "ok hand", "peace sign", "finger", "hand gesture", "pointing"],
        "clothing": ["headscarf", "hijab", "veil", "sleeveless", "miniskirt", "suit and tie",
                     "traditional dress"],
        "symbol": ["flag", "cross", "star and crescent", "religious symbol", "emblem"],
        "colour": ["white dress", "black suit", "red dress", "mourning", "funeral"],
        "food": ["steak", "pork", "beef", "rice", "chopsticks", "fork", "wine", "alcohol"],
        "map": ["map of", "border", "country", "nation", "flag on a map"],
        "holiday": ["christmas", "diwali", "ramadan", "new year", "celebration", "parade"],
        "naming": ["name", "inscription", "caption", "sign", "label", "text"],
    }
    for flag in rules["flags"]:
        hits = [k for k in keyword_map.get(flag["id"], []) if k in lowered]
        if hits:
            triggers.append({"id": flag["id"], "label": flag["label"],
                             "detail": flag["detail"], "matched": hits})
    verdict = "clear" if not triggers else "review"
    body = "\n".join([
        f"Cultural review for locale: {locale}",
        "",
        f"Description: {description}",
        f"Verdict: {verdict}",
        "",
        md_table([[t["label"], ", ".join(t["matched"]), t["detail"]] for t in triggers],
                 ["Area", "Matched cues", "What to check"]) if triggers else
        md_table([["none", "-", "-"]], ["Area", "Matched cues", "What to check"]),
        "",
        rules["note"],
        "",
        "Process:",
        _lines([
            "Review with a native speaker or a local reviewer for the target locale.",
            "Check the whole asset, not only the caption: props, clothing and gestures carry meaning too.",
            "Never assume a locale is homogeneous; check the market, not just the country.",
            "Record the review outcome with the asset.",
        ]),
    ])
    data = {"locale": locale, "verdict": verdict, "triggers": triggers}
    return _report(ctx, "cultural-review", "Cultural context review", body, data,
                   f"Cultural review verdict: {verdict} ({len(triggers)} area(s) flagged).",
                   {"verdict": verdict, "triggers": len(triggers)})


def handoff_manager(ctx) -> Dict[str, Any]:
    path = ctx.path("path")
    name = str(ctx.get("name", "image-handoff"))
    rubric = load_asset(RUBRIC)
    files: List[Dict[str, Any]] = []
    if path and os.path.isdir(path):
        for root, _dirs, names in os.walk(path):
            for filename in sorted(names):
                full = os.path.join(root, filename)
                if filename.startswith("."):
                    continue
                files.append({"path": core.rel_path(full), "bytes": os.path.getsize(full)})
    elif path and os.path.isfile(path):
        files.append({"path": core.rel_path(path), "bytes": os.path.getsize(path)})
    manifest = {
        "package_name": slugify(name, 40),
        "created_utc": utc_now(),
        "source": core.rel_path(path) if path else None,
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
        "contents": [
            {"item": "images", "purpose": "final deliverables in the delivery formats"},
            {"item": "provenance", "purpose": "one provenance record per image (prompt, model, seed)"},
            {"item": "rights", "purpose": "licences, consent records and model licences"},
            {"item": "disclosure", "purpose": "the disclosure text used for each channel"},
            {"item": "alt-text", "purpose": "alt text per image and per placement"},
            {"item": "release-checklist", "purpose": "the completed release checklist"},
            {"item": "manifest", "purpose": "this file: names, sizes and checksums"},
        ],
        "delivery_specs": rubric["delivery"],
        "naming_rules": rubric["naming"],
        "rules": [
            "One folder per delivery channel, so a reviewer can diff one channel at a time.",
            "Keep the editable source next to the exported files.",
            "Record the sha256 of every file in the manifest.",
            "Never ship a file without its provenance record and its alt text.",
        ],
    }
    manifest_path = ctx.out(f"{manifest['package_name']}-manifest.json")
    core.write_json(manifest_path, manifest)
    ctx.emit(manifest_path)
    body = "\n".join([
        "Handoff package manifest",
        "",
        md_table([[f["path"], f["bytes"]] for f in files[:40]], ["File", "Bytes"]) if files else
        md_table([["(no source files)", "-"]], ["File", "Bytes"]),
        "",
        "Package contents:",
        md_table([[c["item"], c["purpose"]] for c in manifest["contents"]], ["Item", "Purpose"]),
        "",
        "Naming rules:",
        _lines(rubric["naming"]["rules"]),
        "",
        "Package rules:",
        _lines(manifest["rules"]),
    ])
    data = manifest
    return _report(ctx, "handoff-manifest", "Handoff package manifest", body, data,
                   f"Prepared a handoff manifest covering {len(files)} file(s).",
                   {"files": len(files)}, [core.rel_path(manifest_path)])
