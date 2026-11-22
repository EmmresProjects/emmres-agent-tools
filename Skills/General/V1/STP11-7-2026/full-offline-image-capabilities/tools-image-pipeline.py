#!/usr/bin/env python3
"""Generation planning, prompt systems, runtime assessment and batch control.

Role: everything that turns intent into a reproducible, validated, offline job
specification. Loads presets and prompt recipes, compiles prompt templates
with variables and weighting, builds txt2img / img2img / inpaint / outpaint /
upscale / controlnet / lora / remove-bg / restore-face job specs, plans
memory fallbacks from a VRAM budget, records provenance, emits standalone
runner scripts, and builds and runs bounded batch queues.

The runner-script emitter writes a script; it never executes it. Diffusion
execution needs local weights plus a local runtime, and when either is absent
the plan is reported as BLOCKED instead of reaching for the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_spec = importlib.util.spec_from_file_location("foic_core", os.path.join(_HERE, "tools-image-core.py"))
core = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("foic_core", core)
_spec.loader.exec_module(core)

ToolError = core.ToolError
LOG = core.LOG

DEFAULT_MODEL = "sdxl-base"
KINDS = ["txt2img", "img2img", "inpaint", "outpaint", "upscale", "controlnet", "lora", "remove-bg", "restore-face"]

OFFLINE_EXECUTABLE_OPS = ("inspect", "validate", "convert", "mask", "canvas", "screen", "manifest")
WEIGHT_DEPENDENT_OPS = ("generate", "upscale-model", "segment", "restore-face-model")

RUNTIME_PROBES = [
    ("python", "__builtins__"),
    ("pillow", "PIL.Image"),
    ("numpy", "numpy"),
    ("torch", "torch"),
    ("diffusers", "diffusers"),
    ("transformers", "transformers"),
    ("safetensors", "safetensors"),
    ("accelerate", "accelerate"),
    ("onnxruntime", "onnxruntime"),
    ("opencv", "cv2"),
    ("realesrgan", "realesrgan"),
    ("gfpgan", "gfpgan"),
    ("segment-anything", "segment_anything"),
]


# --------------------------------------------------------------------------
# asset loading
# --------------------------------------------------------------------------
def load_presets(path=None):
    return core.read_json(path or os.path.join(core.TOOL_DIR, core.DEFAULT_PRESETS_ASSET))


def load_recipes(path=None):
    return core.read_json(path or os.path.join(core.TOOL_DIR, core.DEFAULT_RECIPES_ASSET))


def load_capability_catalog(path=None):
    return core.read_json(path or os.path.join(core.TOOL_DIR, core.DEFAULT_CAPABILITY_CATALOG_ASSET))


# --------------------------------------------------------------------------
# runtime and hardware assessment (the `doctor` capability)
# --------------------------------------------------------------------------
def probe_runtime():
    runtimes = []
    for name, module in RUNTIME_PROBES:
        if name == "python":
            runtimes.append({"name": "python", "available": True, "version": sys.version.split()[0]})
            continue
        found = importlib.util.find_spec(module.split(".")[0]) is not None
        entry = {"name": name, "available": bool(found), "version": None}
        if found:
            entry["version"] = _module_version(module.split(".")[0])
        runtimes.append(entry)
    return runtimes


def _module_version(name):
    try:
        module = importlib.import_module(name)
    except Exception:
        return None
    for attr in ("__version__", "VERSION", "version"):
        value = getattr(module, attr, None)
        if isinstance(value, str):
            return value
    return "installed"


def probe_gpu():
    gpu = {"nvidia_smi": False, "devices": [], "driver": None, "torch_cuda": False, "torch_device_count": 0}
    for candidate in ("nvidia-smi", "/usr/bin/nvidia-smi"):
        try:
            import subprocess

            out = subprocess.run(
                [candidate, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip():
                gpu["nvidia_smi"] = True
                for line in out.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        gpu["devices"].append({"name": parts[0], "memory": parts[1], "driver": parts[2]})
                    if gpu["driver"] is None and len(parts) >= 3:
                        gpu["driver"] = parts[2]
                break
        except Exception:
            continue
    try:
        import torch  # noqa: F401

        gpu["torch_cuda"] = bool(torch.cuda.is_available())
        gpu["torch_device_count"] = int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
    except Exception:
        pass
    return gpu


def _disk_stats(path):
    try:
        usage = os.statvfs(path)
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        return {"path": path, "total_bytes": total, "free_bytes": free,
                "free_human": core.human_bytes(free), "total_human": core.human_bytes(total)}
    except OSError:
        return {"path": path, "error": "unavailable"}


def doctor(cfg, base_dir=None, deadline=None):
    base_dir = base_dir or os.getcwd()
    runtimes = probe_runtime()
    gpu = probe_gpu()
    checks = []
    missing_required = ["pillow"]
    have = {r["name"] for r in runtimes if r["available"]}
    for name in missing_required:
        checks.append(
            {
                "id": "runtime-%s" % name,
                "status": "pass" if name in have else "warn",
                "severity": "warn",
                "message": "%s present" % name if name in have else
                "%s missing: JPEG/WebP/TIFF pixel access falls back to metadata-only" % name,
            }
        )
    diffusion_ready = "torch" in have and "diffusers" in have
    checks.append(
        {
            "id": "runtime-diffusion",
            "status": "pass" if diffusion_ready else "fail" if not diffusion_ready else "pass",
            "severity": "error" if not diffusion_ready else "error",
            "message": "torch + diffusers available" if diffusion_ready else
            "torch and/or diffusers unavailable: generation plans are emitted, not executed (no downloads attempted)",
        }
    )
    checks.append(
        {
            "id": "network-policy",
            "status": "pass",
            "severity": "info",
            "message": "network policy = %s (this tool performs no outbound requests)" % cfg.get("runtime.network"),
        }
    )
    gpu_ok = bool(gpu["devices"]) or gpu["torch_cuda"]
    checks.append(
        {
            "id": "gpu",
            "status": "pass" if gpu_ok else "warn",
            "severity": "warn",
            "message": "GPU detected: %s" % (", ".join(d["name"] for d in gpu["devices"]) or "torch cuda")
            if gpu_ok else "no NVIDIA GPU detected: plans fall back to CPU-safe settings",
        }
    )
    disk = _disk_stats(base_dir)
    if "free_bytes" in disk:
        low = disk["free_bytes"] < (5 << 30)
        checks.append(
            {
                "id": "disk-space",
                "status": "warn" if low else "pass",
                "severity": "warn",
                "message": "%s free on %s%s" % (disk["free_human"], base_dir, " (below 5 GB recommended)" if low else ""),
            }
        )
    model_dir = core.resolve_under(base_dir, cfg.get("paths.model_dir"))
    model_present = os.path.isdir(model_dir)
    model_files = 0
    model_bytes = 0
    if model_present:
        for path in core.iter_files(model_dir):
            model_files += 1
            try:
                model_bytes += os.path.getsize(path)
            except OSError:
                pass
            if model_files > 5000:
                break
    checks.append(
        {
            "id": "model-dir",
            "status": "pass" if model_present and model_files else "warn",
            "severity": "warn",
            "message": "%s: %d file(s), %s" % (model_dir, model_files, core.human_bytes(model_bytes))
            if model_present else "model directory not found: %s" % model_dir,
        }
    )
    wheelhouse = core.resolve_under(base_dir, cfg.get("paths.wheelhouse_dir"))
    checks.append(
        {
            "id": "wheelhouse",
            "status": "pass" if os.path.isdir(wheelhouse) else "warn",
            "severity": "warn",
            "message": "wheelhouse present: %s" % wheelhouse if os.path.isdir(wheelhouse)
            else "no local wheelhouse at %s; offline installs need pinned wheels staged locally" % wheelhouse,
        }
    )
    status = "fail" if any(c["status"] == "fail" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "pass")
    return {
        "schema": "foic.doctor/1",
        "generated_at": core.iso_now(),
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "config": {"source": cfg.source, "fingerprint": cfg.fingerprint(),
                   "backend": cfg.get("runtime.image_backend"), "network": cfg.get("runtime.network")},
        "platform": {"system": sys.platform, "python": sys.version.split()[0], "cwd": os.path.abspath(base_dir)},
        "runtimes": runtimes,
        "gpu": gpu,
        "disk": disk,
        "model_dir": {"path": model_dir, "present": model_present, "files": model_files, "bytes": model_bytes},
        "status": status,
        "checks": checks,
        "diffusion_ready": diffusion_ready,
        "capabilities_blocked_without_weights": WEIGHT_DEPENDENT_OPS,
    }


# --------------------------------------------------------------------------
# prompt system
# --------------------------------------------------------------------------
def compile_prompt(template_name, variables=None, recipes=None, negative=None,
                   weight_style="none", model=None, presets=None):
    recipes = recipes or load_recipes()
    presets = presets or load_presets()
    templates = recipes.get("templates", {})
    if template_name not in templates:
        raise ToolError(
            "unknown prompt template %r (available: %s)" % (template_name, ", ".join(sorted(templates)) or "none"),
            code="unknown-template",
            exit_code=core.EXIT_USAGE,
        )
    spec = templates[template_name]
    values = dict(spec.get("defaults", {}))
    values.update(variables or {})
    missing = [v for v in spec.get("variables", []) if v not in values]
    if missing:
        raise ToolError(
            "missing template variable(s): %s (pass with --set var=value)"
            % ", ".join(missing),
            code="missing-variable",
            exit_code=core.EXIT_USAGE,
        )
    try:
        prompt = spec["template"].format(**values)
    except KeyError as exc:
        raise ToolError("template %r uses undefined variable %s" % (template_name, exc), code="template-error")
    prompt = re.sub(r"\s+", " ", prompt).strip(" ,")

    neg_name = negative or spec.get("negative")
    neg_terms = list(recipes.get("negative_libraries", {}).get(neg_name, [])) if neg_name else []
    negative_prompt = ", ".join(neg_terms)

    weights = recipes.get("weight_styles", {})
    if weight_style not in weights:
        raise ToolError(
            "unknown weight style %r (available: %s)" % (weight_style, ", ".join(weights) or "none"),
            code="usage",
            exit_code=core.EXIT_USAGE,
        )
    styled = _apply_weight_style(prompt, weight_style, weights[weight_style])

    limit = None
    if model and model in presets.get("models", {}):
        limit = presets["models"][model].get("tokenizer_limit")
    chars_per_token = float(recipes.get("token_estimate", {}).get("chars_per_token", 4.0))
    estimate = _estimate_tokens(styled, chars_per_token, recipes)

    flags = []
    for pattern in recipes.get("injection_patterns", []):
        match = re.search(pattern, styled)
        if match:
            flags.append({"pattern": pattern, "matched": match.group(0)[:80]})
    warnings = []
    if flags:
        warnings.append(
            "prompt text matches %d injection filter pattern(s); %s"
            % (len(flags), recipes.get("injection_action_note", "flagged only"))
        )
    if limit and estimate["tokens"] > limit:
        warnings.append(
            "estimated %d tokens exceeds the %s limit of %d; shorten the prompt or split the job"
            % (estimate["tokens"], model, limit)
        )
    return {
        "schema": "foic.prompt/1",
        "template": template_name,
        "variables": values,
        "prompt": styled,
        "negative_prompt": negative_prompt,
        "negative_library": neg_name,
        "weight_style": weight_style,
        "token_estimate": estimate,
        "tokenizer_limit": limit,
        "injection_flags": flags,
        "warnings": warnings,
        "sha256": core.sha256_bytes(styled.encode("utf-8")),
    }


def _apply_weight_style(prompt, style, spec):
    if style == "none" or not spec.get("suffix"):
        return prompt
    return "%s%s" % (prompt, spec["suffix"])


def _estimate_tokens(text, chars_per_token, recipes):
    tokens = int(round(len(text) / chars_per_token))
    commas = text.count(",") * int(recipes.get("token_estimate", {}).get("comma_bonus_tokens", 0))
    total = tokens + commas
    return {"tokens": total, "chars": len(text), "chars_per_token": chars_per_token,
            "method": "heuristic character budget, not a tokenizer"}


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def memory_plan(cfg, presets, model_name, width, height, kind):
    fb = presets.get("memory_fallbacks", {})
    model = presets.get("models", {}).get(model_name, {})
    vram = cfg.get("runtime.vram_gb")
    device_pref = cfg.get("runtime.device", "auto")
    notes = []
    have_torch = importlib.util.find_spec("torch") is not None
    if device_pref == "cpu" or not have_torch:
        plan = dict(fb.get("cpu_only", {}))
        notes.append("torch not installed or device=cpu: CPU-safe plan selected")
        return {
            "device": "cpu",
            "vram_gb": vram,
            "fallbacks": list(plan.get("apply", [])),
            "max_resolution": list(plan.get("max_resolution", [512, 512])),
            "notes": notes + [plan.get("note", "")],
        }
    max_res = list(model.get("default_resolution", [1024, 1024]))
    fallbacks = []
    if vram is None:
        notes.append(
            "runtime.vram_gb is unset: set it (or pass --set runtime.vram_gb=N) for a concrete ladder; "
            "defaulting to the model's recommended resolution"
        )
        return {"device": "cuda-or-mps (unverified)", "vram_gb": None, "fallbacks": [],
                "max_resolution": max_res, "notes": notes}
    for rule in fb.get("rules", []):
        if vram < rule["when_vram_below_gb"]:
            fallbacks = list(rule.get("apply", []))
            max_res = list(rule.get("max_resolution", max_res))
            break
    if width > max_res[0] or height > max_res[1]:
        notes.append(
            "requested %dx%d exceeds the %dx%d ceiling for %s GB VRAM; enable tiling or lower the resolution"
            % (width, height, max_res[0], max_res[1], vram)
        )
    recommended = model.get("vram_gb_recommended")
    if recommended and vram < recommended:
        notes.append(
            "model %s recommends %s GB VRAM; %s GB will rely on the fallbacks above"
            % (model_name, recommended, vram)
        )
    return {"device": "cuda", "vram_gb": vram, "fallbacks": fallbacks,
            "max_resolution": max_res, "notes": [n for n in notes if n]}


def resolve_model_path(model_dir, catalog, component_id):
    if not model_dir or not os.path.isdir(model_dir):
        return None
    comp = catalog.get("components", {}).get(component_id)
    if not comp:
        return None
    target = comp.get("filename", "").lower()
    rng = comp.get("size_bytes_range")
    for path in core.iter_files(model_dir):
        name = os.path.basename(path).lower()
        if name == target or (target and target in name):
            size = os.path.getsize(path)
            if rng and not (rng[0] <= size <= rng[1]) and not name.endswith((".json", ".txt")):
                continue
            return path
    return None


def build_plan(cfg, kind, model_name=None, preset=None, overrides=None, prompt=None,
               negative_prompt=None, seed=None, width=None, height=None, presets=None,
               base_dir=None, catalog=None, inputs=None, loras=None, control=None):
    presets = presets or load_presets()
    catalog = catalog or (core.read_json(os.path.join(core.TOOL_DIR, core.DEFAULT_MODEL_CATALOG_ASSET)))
    if kind not in KINDS:
        raise ToolError("unknown kind %r (available: %s)" % (kind, ", ".join(KINDS)), code="usage", exit_code=core.EXIT_USAGE)
    model_name = model_name or DEFAULT_MODEL
    if model_name not in presets["models"]:
        raise ToolError(
            "unknown model preset %r (available: %s)" % (model_name, ", ".join(sorted(presets["models"]))),
            code="unknown-model",
            exit_code=core.EXIT_USAGE,
        )
    model = presets["models"][model_name]
    op = dict(presets["operations"][kind])

    user_preset = {}
    if preset:
        if isinstance(preset, dict):
            user_preset = preset
        else:
            user_preset = core.read_json(preset)
        if user_preset.get("kind") and user_preset["kind"] != kind:
            raise ToolError(
                "preset kind %r does not match --kind %r" % (user_preset["kind"], kind),
                code="preset-mismatch",
            )
        op = core.deep_merge(op, {k: v for k, v in user_preset.items() if k not in ("kind", "name")})

    for item in overrides or []:
        if "=" not in item:
            raise ToolError("--set expects key=value, got %r" % item, code="usage", exit_code=core.EXIT_USAGE)
        key, _, value = item.partition("=")
        op[key.strip()] = core.coerce_value(value.strip())

    sampler_name = op.get("sampler", "dpm++_2m_karras")
    sampler = presets["samplers"].get(sampler_name)
    if not sampler:
        raise ToolError(
            "unknown sampler %r (available: %s)" % (sampler_name, ", ".join(sorted(presets["samplers"]))),
            code="unknown-sampler",
            exit_code=core.EXIT_USAGE,
        )
    steps = int(op.get("steps") or sampler["default_steps"])
    if steps < int(sampler["min_steps"]):
        steps = int(sampler["min_steps"])
    res_w, res_h = (width, height) if (width and height) else tuple(model["default_resolution"])
    if "resolution" in op and isinstance(op["resolution"], list):
        res_w, res_h = op["resolution"]
    res_w, res_h = int(res_w), int(res_h)
    final_seed = int(seed) if seed is not None else int(op.get("seed", -1))

    parameters = {
        "seed": final_seed,
        "steps": steps,
        "guidance": float(op.get("guidance", 7.0)),
        "sampler": sampler_name,
        "scheduler": sampler["scheduler"],
        "width": res_w,
        "height": res_h,
        "batch_size": int(op.get("batch_size", 1)),
    }
    for extra in ("denoise_strength", "mask_blur", "inpaint_area", "scale", "tile_size",
                  "tile_overlap", "face_enhance", "control_type", "control_weight",
                  "guidance_start", "guidance_end", "control_mode", "mask_feather",
                  "fidelity_weight", "alpha_matting", "matting_erode", "segmenter",
                  "upscaler", "restorer", "sharpen_after", "denoise_before_upscale", "resize_mode"):
        if extra in op:
            parameters[extra] = op[extra]
    if "expand" in op:
        parameters["expand"] = op["expand"]

    lora_entries = []
    for entry in loras or op.get("loras") or []:
        if isinstance(entry, str):
            parts = entry.split(":")
            lora_entries.append({"name": parts[0], "weight": float(parts[1]) if len(parts) > 1 else 0.8,
                                 "trigger_tokens": []})
        else:
            lora_entries.append(entry)

    job = {
        "schema": "foic.job-spec/1",
        "job_id": "%s-%s-%08d" % (kind, model_name, abs(final_seed) % 100000000),
        "kind": kind,
        "preset": (preset if isinstance(preset, str) else (user_preset.get("name") if user_preset else None)),
        "model": {
            "name": model_name,
            "family": model["family"],
            "runtime": model["runtime"],
            "format": model["format"],
            "checkpoint": model["component_files"][0],
            "vae": model.get("vae"),
            "text_encoders": list(model.get("text_encoders", [])),
            "tokenizer_limit": model.get("tokenizer_limit"),
        },
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "parameters": parameters,
        "loras": lora_entries,
        "inputs": inputs or {},
        "memory_plan": memory_plan(cfg, presets, model_name, res_w, res_h, kind),
        "output": dict(presets.get("output", {})),
        "provenance": {
            "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
            "created_at": core.iso_now(),
            "config_fingerprint": cfg.fingerprint(),
            "presets_schema": presets.get("schema"),
            "resolved_from": {"kind": kind, "model": model_name, "preset": preset},
        },
    }
    job["output"]["filename"] = presets["output"]["filename_template"].format(
        kind=kind, model=model_name, seed=abs(final_seed), job_id=job["job_id"]
    )

    # local-availability assessment: never downloads, always reports blockers
    blockers = []
    model_dir = core.resolve_under(base_dir or os.getcwd(), cfg.get("paths.model_dir"))
    for comp_id in [model_name.replace("-", "_")] + list(model.get("text_encoders", [])) + [model.get("vae")]:
        if not comp_id:
            continue
        found = resolve_model_path(model_dir, catalog, comp_id)
        if not found:
            blockers.append("local weight not found for '%s' under %s" % (comp_id, model_dir))
    if importlib.util.find_spec("torch") is None or importlib.util.find_spec("diffusers") is None:
        blockers.append("diffusion runtime unavailable (torch/diffusers not importable)")
    job["blockers"] = blockers
    job["status"] = "ready-to-run-locally" if not blockers else "blocked"
    return job


def validate_plan(plan, schema_path=None):
    schema_path = schema_path or os.path.join(core.TOOL_DIR, core.DEFAULT_PLAN_SCHEMA_ASSET)
    schema = core.read_json(schema_path)
    problems = core.validate_schema(plan, schema)
    return {"valid": not problems, "problems": problems, "schema": schema_path}


RUNNER_TEMPLATE = '''#!/usr/bin/env python3
"""Standalone offline runner emitted by full-offline-image-capabilities.

Emitted, NOT executed by the emitting tool. It performs no downloads: every
component path must already exist locally. Run it where model weights and a
diffusion runtime are installed.

Usage:
    python3 {script_name} --plan <plan.json> --model-dir <dir> --out <file>
"""

import argparse
import json
import os
import sys


def fail(message):
    print("BLOCKED: " + message, file=sys.stderr)
    return 2


def main():
    parser = argparse.ArgumentParser(description="Offline job runner for {job_id}")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)

    try:
        import torch
        import diffusers
    except ImportError as exc:
        return fail("diffusion runtime missing: %s" % exc)

    checkpoint = os.path.join(args.model_dir, plan["model"]["checkpoint"])
    if not os.path.isfile(checkpoint):
        return fail("checkpoint not found locally: %s" % checkpoint)

    torch.manual_seed(plan["parameters"]["seed"] if plan["parameters"]["seed"] >= 0 else 0)
    kind = plan["kind"]
    params = plan["parameters"]
    torch_dtype = torch.float16 if args.device == "cuda" else torch.float32

    if kind in ("txt2img", "lora"):
        pipe = diffusers.AutoPipelineForText2Image.from_single_file(
            checkpoint, torch_dtype=torch_dtype, use_safetensors=True, local_files_only=True
        )
        for lora in plan.get("loras", []):
            pipe.load_lora_weights(args.model_dir, weight_name=lora["name"])
        pipe = pipe.to(args.device)
        image = pipe(
            prompt=plan.get("prompt") or "",
            negative_prompt=plan.get("negative_prompt"),
            num_inference_steps=params["steps"],
            guidance_scale=params["guidance"],
            width=params["width"],
            height=params["height"],
        ).images[0]
    elif kind == "img2img":
        from PIL import Image

        pipe = diffusers.AutoPipelineForImage2Image.from_single_file(
            checkpoint, torch_dtype=torch_dtype, use_safetensors=True, local_files_only=True
        ).to(args.device)
        init = Image.open(plan["inputs"]["image"]).convert("RGB")
        image = pipe(
            prompt=plan.get("prompt") or "",
            image=init,
            strength=params.get("denoise_strength", 0.65),
            num_inference_steps=params["steps"],
            guidance_scale=params["guidance"],
        ).images[0]
    elif kind in ("inpaint", "outpaint"):
        from PIL import Image

        pipe = diffusers.AutoPipelineForInpainting.from_single_file(
            checkpoint, torch_dtype=torch_dtype, use_safetensors=True, local_files_only=True
        ).to(args.device)
        init = Image.open(plan["inputs"]["image"]).convert("RGB")
        mask = Image.open(plan["inputs"]["mask"]).convert("L")
        image = pipe(
            prompt=plan.get("prompt") or "",
            image=init,
            mask_image=mask,
            strength=params.get("denoise_strength", 0.85),
            num_inference_steps=params["steps"],
            guidance_scale=params["guidance"],
        ).images[0]
    elif kind == "controlnet":
        from PIL import Image

        control = Image.open(plan["inputs"]["control_image"]).convert("RGB")
        pipe = diffusers.StableDiffusionXLControlNetPipeline.from_single_file(
            checkpoint,
            controlnet=diffusers.ControlNetModel.from_single_file(
                os.path.join(args.model_dir, plan["inputs"].get("controlnet", "controlnet-canny-sdxl-1.0.safetensors")),
                torch_dtype=torch_dtype, local_files_only=True,
            ),
            torch_dtype=torch_dtype,
            use_safetensors=True,
            local_files_only=True,
        ).to(args.device)
        image = pipe(
            prompt=plan.get("prompt") or "",
            image=control,
            controlnet_conditioning_scale=params.get("control_weight", 0.85),
            num_inference_steps=params["steps"],
            guidance_scale=params["guidance"],
        ).images[0]
    elif kind == "upscale":
        return fail(
            "upscale execution requires a local Real-ESRGAN build; use the emitted plan with your "
            "local upscaler, or implement the branch against your local package."
        )
    else:
        return fail("kind '%s' has no bundled execution branch; the plan is complete but the "
                    "runtime integration is site-specific." % kind)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    image.save(args.out)
    print("WROTE " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def emit_runner_script(plan, out_path, overwrite=False):
    text = RUNNER_TEMPLATE.format(
        script_name=os.path.basename(out_path),
        job_id=plan.get("job_id", "job"),
    )
    core.atomic_write_text(out_path, text, overwrite=overwrite)
    try:
        os.chmod(out_path, 0o755)
    except OSError:
        pass
    return {"path": os.path.abspath(out_path), "bytes": len(text), "executed": False,
            "note": "emitted for local execution where weights and a diffusion runtime exist; never auto-run"}


def write_provenance(plan, out_path, extra=None, overwrite=False):
    record = {
        "schema": "foic.provenance/1",
        "recorded_at": core.iso_now(),
        "job": plan,
        "extra": extra or {},
    }
    core.write_json(out_path, record, overwrite=overwrite)
    return out_path


# --------------------------------------------------------------------------
# batch
# --------------------------------------------------------------------------
def build_batch(base_plan, matrix, out_dir, cfg, max_jobs=None, name="batch"):
    jobs = []
    seeds = matrix.get("seeds") or [base_plan["parameters"]["seed"]]
    resolutions = matrix.get("resolutions") or [[base_plan["parameters"]["width"], base_plan["parameters"]["height"]]]
    models = matrix.get("models") or [base_plan["model"]["name"]]
    loras = matrix.get("loras") or [None]
    controls = matrix.get("controlnet") or [None]
    limit = int(max_jobs or cfg.get("runtime.max_batch_jobs") or 5000)
    for model in models:
        for res in resolutions:
            for lora in loras:
                for control in controls:
                    for seed in seeds:
                        if len(jobs) >= limit:
                            break
                        job = json.loads(json.dumps(base_plan))
                        job["model"]["name"] = model
                        job["parameters"]["width"], job["parameters"]["height"] = int(res[0]), int(res[1])
                        job["parameters"]["seed"] = int(seed)
                        if lora:
                            job["loras"] = [lora] if isinstance(lora, dict) else [{"name": lora, "weight": 0.8, "trigger_tokens": []}]
                        if control:
                            job["inputs"]["control_image"] = control
                            job["parameters"]["control_type"] = matrix.get("control_type", "canny")
                        job["job_id"] = "%s-%s-%sx%s-%08d" % (
                            job["kind"], model, int(res[0]), int(res[1]), abs(int(seed)) % 100000000
                        )
                        job["output"]["filename"] = job["job_id"] + "." + job["output"].get("format", "png")
                        jobs.append(job)
    queue = {
        "schema": "foic.batch/1",
        "name": name,
        "created_at": core.iso_now(),
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "output_dir": os.path.abspath(out_dir),
        "limits": {"max_jobs": limit, "timeout_seconds": cfg.get("runtime.timeout_seconds"),
                   "retry": cfg.get("runtime.retry")},
        "job_count": len(jobs),
        "jobs": jobs,
    }
    return queue


def run_batch(queue, cfg, out_dir, deadline=None, jobs_workers=1, resume_state=None,
              executor=None):
    """Run a queue. Offline ops execute; weight-dependent ops are reported blocked."""
    state = resume_state or {"completed": {}, "failed": {}, "blocked": {}, "skipped": {}}
    results = []
    core.ensure_dir(out_dir)
    for job in queue.get("jobs", []):
        if deadline:
            deadline.check("running batch")
        job_id = job.get("job_id")
        if job_id in state["completed"]:
            results.append({"job_id": job_id, "status": "skipped", "reason": "already completed (resume)"})
            state["skipped"][job_id] = "resume"
            continue
        op = job.get("op", "generate")
        try:
            if op in WEIGHT_DEPENDENT_OPS or op == "generate":
                blockers = job.get("blockers") or []
                why = "; ".join(blockers) if blockers else "requires local weights and a diffusion runtime"
                state["blocked"][job_id] = why
                results.append({"job_id": job_id, "status": "blocked", "reason": why,
                                "plan": job})
                continue
            if executor is None:
                raise ToolError("no executor bound for op %r" % op, code="no-executor")
            outcome = core.retry_call(
                lambda: executor(job, out_dir),
                attempts=int((cfg.get("runtime.retry") or {}).get("attempts", 1)),
                backoff=float((cfg.get("runtime.retry") or {}).get("backoff_seconds", 0.5)),
                deadline=deadline,
                what="job %s" % job_id,
            )
            state["completed"][job_id] = outcome
            results.append({"job_id": job_id, "status": "completed", "result": outcome})
        except ToolError as exc:
            state["failed"][job_id] = {"code": exc.code, "message": str(exc)}
            results.append({"job_id": job_id, "status": "failed", "error": str(exc), "code": exc.code})
    summary = {
        "total": len(queue.get("jobs", [])),
        "completed": len(state["completed"]),
        "failed": len(state["failed"]),
        "blocked": len(state["blocked"]),
        "skipped": len(state["skipped"]),
    }
    return {
        "schema": "foic.batch-run/1",
        "generated_at": core.iso_now(),
        "output_dir": os.path.abspath(out_dir),
        "summary": summary,
        "state": state,
        "results": results,
    }
