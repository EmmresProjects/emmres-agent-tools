#!/usr/bin/env python3
"""Numeric planners, schedulers and local system probes for ai-image-toolkit (standard library only).

Operations: compute.tensor_spec, compute.latent_interpolation, compute.diffusion_schedule,
compute.sweep_grid, compute.resolution, compute.print_resolution, compute.contrast,
compute.vram_budget, compute.throughput, compute.cloud_budget, compute.gpu_queue,
compute.tile_plan, compute.control_conflicts, compute.region_layout, compute.occlusion_order,
compute.training_plan, compute.blind_pairs, system.probe, system.model_storage, system.env_lock.

All estimates are planning values. Nothing here trains, renders or measures GPU work
except system.probe, which reads the local machine state and never changes it.
"""
import csv
import importlib.metadata
import importlib.util
import itertools
import json
import math
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_common():
    name = "aiit_common"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _HERE / "tools-common.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


C = _load_common()
ToolError, P, op, write_json, slugify, sha256_file = C.ToolError, C.P, C.op, C.write_json, C.slugify, C.sha256_file
PROFILES = C.load_asset("asset-model-profiles.json")
FAMILIES = sorted(PROFILES["families"].keys())

SWEEP_DEFAULTS = {
    "sampler": {"sampler": ["euler", "dpm++2m", "ddim"], "steps": [20, 30], "cfg": [5.0, 7.0], "seed": [1]},
    "merge": {"merge_alpha": [0.25, 0.5, 0.75], "seed": [1]},
    "general": {"seed": [1]},
}


# ----------------------------------------------------------------- compute.tensor_spec

def op_tensor_spec(p, ctx):
    w, h, c, b = p["width"], p["height"], p["channels"], p["batch"]
    bits = int(p["bit_depth"])
    warnings = []
    if w % 8 or h % 8:
        warnings.append("width and height should be multiples of 8 for VAE latents")
    if w % 64 or h % 64:
        warnings.append("width and height are not multiples of 64; many UNet/DiT pipelines expect 64-multiples")
    px = w * h
    elements = b * c * px
    raw_bytes = elements * bits // 8
    if p["layout"] == "NCHW":
        shape = [b, c, h, w]
    else:
        shape = [b, h, w, c]
    rng = [0.0, 1.0] if bits < 32 else [0.0, 1.0]
    lw, lh = w // p["vae_scale"], h // p["vae_scale"]
    latent_shape = [b, p["latent_channels"], lh, lw]
    latent_bytes_fp32 = b * p["latent_channels"] * lh * lw * 4
    result = {
        "shape": shape, "layout": p["layout"], "dtype": f"uint{bits}" if bits < 32 else "float32",
        "elements": elements, "raw_bytes": raw_bytes, "raw_megabytes": round(raw_bytes / 2**20, 3),
        "value_range_after_normalisation": rng,
        "latent_shape": latent_shape, "latent_bytes_fp32": latent_bytes_fp32,
        "formula": "bytes = batch * channels * height * width * bit_depth / 8; normalise uint8 by dividing by 255",
    }
    result["warnings_list"] = warnings
    return result


# ----------------------------------------------------------------- compute.latent_interpolation

def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def op_latent_interpolation(p, ctx):
    steps, lw, lh, lc = p["steps"], p["latent_w"], p["latent_h"], p["latent_channels"]
    n = lc * lh * lw
    if n > 262144:
        raise ToolError("invalid_input", f"latent has {n} values; reduce latent_w/latent_h", field="latent_w")
    if steps > 32:
        raise ToolError("invalid_input", "steps must be 32 or fewer for this planning helper", field="steps")
    if p["seed_a"] == p["seed_b"]:
        raise ToolError("invalid_input", "seed_a and seed_b must differ", field="seed_b")
    a = [random.Random(p["seed_a"]).gauss(0, 1) for _ in range(n)]
    b = [random.Random(p["seed_b"]).gauss(0, 1) for _ in range(n)]
    na, nb = math.sqrt(_dot(a, a)), math.sqrt(_dot(b, b))
    cosang = max(-1.0, min(1.0, _dot(a, b) / (na * nb)))
    theta = math.acos(cosang)
    frames = []
    for k in range(steps):
        t = k / (steps - 1)
        if p["method"] == "slerp" and theta > 1e-6:
            sa = math.sin((1 - t) * theta) / math.sin(theta)
            sb = math.sin(t * theta) / math.sin(theta)
            v = [sa * x + sb * y for x, y in zip(a, b)]
        else:
            v = [(1 - t) * x + t * y for x, y in zip(a, b)]
        norm = math.sqrt(_dot(v, v))
        frames.append({"step": k, "t": round(t, 4), "norm": round(norm, 3),
                       "cos_to_a": round(_dot(v, a) / (norm * na), 4), "cos_to_b": round(_dot(v, b) / (norm * nb), 4)})
    return {"method": p["method"], "latent_values": n, "latent_shape": [lc, lh, lw],
            "angle_degrees": round(math.degrees(theta), 2), "norm_a": round(na, 3), "norm_b": round(nb, 3),
            "min_norm": min(f["norm"] for f in frames), "frames": frames,
            "note": "slerp keeps the norm near that of Gaussian noise; lerp shrinks it in the middle. Numbers only; no image is rendered."}


# ----------------------------------------------------------------- compute.diffusion_schedule

def op_diffusion_schedule(p, ctx):
    T = p["train_steps"]
    if T < 2 or T > 1000:
        raise ToolError("invalid_input", "train_steps must be 2-1000", field="train_steps")
    if not (0 < p["beta_start"] < p["beta_end"] < 1):
        raise ToolError("invalid_input", "require 0 < beta_start < beta_end < 1", field="beta_start")
    betas = [p["beta_start"] + (p["beta_end"] - p["beta_start"]) * i / (T - 1) for i in range(T)]
    abar, acc = [], 1.0
    for bt in betas:
        acc *= (1 - bt)
        abar.append(acc)
    k = min(p["sample_steps"], T)
    ts = sorted(set(round(i * (T - 1) / max(1, k - 1)) for i in range(k)))
    rows = []
    for t in ts:
        a = abar[t]
        snr = a / (1 - a) if a < 1 else float("inf")
        rows.append({"t": t, "beta": round(betas[t], 6), "alpha_bar": round(a, 8),
                     "sqrt_alpha_bar": round(math.sqrt(a), 6), "sqrt_one_minus_alpha_bar": round(math.sqrt(1 - a), 6),
                     "log10_snr": round(math.log10(snr), 3) if snr not in (0, float("inf")) else None})
    return {"schedule": "linear betas", "train_steps": T, "rows": rows,
            "note": "Forward process: x_t = sqrt(alpha_bar_t) x_0 + sqrt(1 - alpha_bar_t) eps. Denoisers are trained against this family of noise levels."}


# ----------------------------------------------------------------- compute.sweep_grid

def sweep_items(prompt, negative, axes, name_template, limit, extra_fields=None):
    """Cartesian product of sweep axes. Shared with provider.batch.

    extra_fields are top-level request values (e.g. model, width) usable in name_template.
    """
    keys = sorted(axes.keys())
    for k in keys:
        if not isinstance(axes[k], list) or not axes[k]:
            raise ToolError("invalid_input", f"axis '{k}' must be a non-empty list", field="axes")
    combos = list(itertools.product(*[axes[k] for k in keys]))
    if len(combos) > limit:
        raise ToolError("invalid_input", f"sweep has {len(combos)} items; limit is {limit}", field="axes",
                        hint="Reduce axis values or raise limits.max_batch_items in the config.")
    items = []
    for i, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        fields = {k: str(v) for k, v in (extra_fields or {}).items()}
        fields.update({k: (f"{v:g}" if isinstance(v, float) else str(v)) for k, v in params.items()})
        try:
            name = name_template.format(**fields)
        except KeyError as e:
            raise ToolError("invalid_input", f"name_template uses unknown axis {e}", field="name_template")
        items.append({"id": f"{i:03d}-{slugify(name, 80)}", "prompt": prompt, "negative_prompt": negative,
                      "settings": params})
    return items


def op_sweep_grid(p, ctx):
    axes = p.get("axes") or SWEEP_DEFAULTS[p["domain"]]
    if not isinstance(axes, dict):
        raise ToolError("invalid_input", "axes must be a JSON object", field="axes")
    items = sweep_items(p["prompt"], p.get("negative_prompt") or "", axes, p["name_template"],
                        ctx.limits["max_batch_items"])
    path = ctx.out_path(p.get("output"), "sweep.json")
    write_json(path, {"axes": axes, "items": items})
    ctx.record(path)
    return {"item_count": len(items), "axes": sorted(axes.keys()), "sweep_file": str(path),
            "first_items": items[:3],
            "note": "One item per combination. Keep prompt, model and size fixed while sweeping, or the comparison is not valid."}


# ----------------------------------------------------------------- compute.resolution

COMMON_ASPECTS = ["1:1", "4:3", "3:2", "16:9", "9:16", "2:3", "3:4", "21:9"]


def _bucket(budget, a, mult):
    h = math.sqrt(budget / a)
    w = a * h
    wm = max(mult, int(round(w / mult)) * mult)
    hm = max(mult, int(round(h / mult)) * mult)
    return wm, hm


def op_resolution(p, ctx):
    fam = PROFILES["families"][p["family"]]
    budget = fam["budget_side"] ** 2
    mult = p["multiple"]
    try:
        aw, ah = [float(x) for x in p["aspect"].split(":")]
    except ValueError:
        raise ToolError("invalid_input", "aspect must look like 3:2", field="aspect")
    if aw <= 0 or ah <= 0 or max(aw, ah) / min(aw, ah) > 4:
        raise ToolError("invalid_input", "aspect ratio must be positive and no more extreme than 4:1", field="aspect")
    w, h = _bucket(budget, aw / ah, mult)
    buckets = []
    for asp in COMMON_ASPECTS:
        x, y = [float(v) for v in asp.split(":")]
        bw, bh = _bucket(budget, x / y, mult)
        buckets.append({"aspect": asp, "width": bw, "height": bh, "pixels": bw * bh,
                        "pixel_ratio_to_budget": round(bw * bh / budget, 3)})
    warnings = []
    if abs((w * h) / budget - 1) > 0.15:
        warnings.append("rounding to multiples moved the pixel count more than 15% from the budget")
    return {"family": p["family"], "label": fam["label"], "requested_aspect": p["aspect"],
            "width": w, "height": h, "pixels": w * h, "budget_side": fam["budget_side"],
            "buckets": buckets, "warnings_list": warnings,
            "note": "Starting point only. Confirm the resolution range your checkpoint was trained on."}


# ----------------------------------------------------------------- compute.print_resolution

_VIEW_DPI = [(0.6, 300), (1.5, 150), (3.0, 100), (10.0, 45)]


def op_print_resolution(p, ctx):
    w_in, h_in, dpi, bleed = p["width_in"], p["height_in"], p["dpi"], p["bleed_in"]
    if dpi < 72 or dpi > 1200:
        raise ToolError("invalid_input", "dpi must be 72-1200", field="dpi")
    px_w = round(w_in * dpi)
    px_h = round(h_in * dpi)
    bleed_px = round(bleed * dpi)
    total_w, total_h = px_w + 2 * bleed_px, px_h + 2 * bleed_px
    rec = next((d for lim, d in _VIEW_DPI if p["viewing_distance_m"] <= lim), 45)
    result = {"trim_pixels": [px_w, px_h], "bleed_pixels": bleed_px, "file_pixels_with_bleed": [total_w, total_h],
              "requested_dpi": dpi, "rule_of_thumb_dpi_for_distance": rec,
              "rule_note": "Rule-of-thumb viewing-distance guidance; confirm with your printer's specification."}
    if p.get("source_width") and p.get("source_height"):
        eff = min(p["source_width"] / w_in, p["source_height"] / h_in)
        result["effective_dpi_from_source"] = round(eff, 1)
        if eff < dpi:
            result["warnings_list"] = [f"source gives {eff:.0f} DPI at this size; it is below the requested {dpi} DPI. Upscale or print smaller."]
    return result


# ----------------------------------------------------------------- compute.contrast

def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _ratio(fg, bg):
    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _grade(ratio, size):
    need_aa = 3.0 if size == "large" else 4.5
    need_aaa = 4.5 if size == "large" else 7.0
    return {"ratio": round(ratio, 2), "AA": ratio >= need_aa, "AAA": ratio >= need_aaa,
            "AA_threshold": need_aa, "AAA_threshold": need_aaa}


def op_contrast(p, ctx):
    pairs = p.get("pairs")
    if pairs:
        out = []
        for i, pr in enumerate(pairs, 1):
            fg, bg = C.parse_hex(pr.get("fg", ""), "pairs.fg"), C.parse_hex(pr.get("bg", ""), "pairs.bg")
            g = _grade(_ratio(fg, bg), p["text_size"])
            out.append({"name": pr.get("name", f"pair-{i}"), "fg": pr["fg"], "bg": pr["bg"], **g})
        return {"results": out, "failing": [r["name"] for r in out if not r["AA"]],
                "note": "WCAG 2.x contrast ratio. Colour alone must not carry meaning; check grayscale too."}
    fg, bg = C.parse_hex(p["foreground"], "foreground"), C.parse_hex(p["background"], "background")
    g = _grade(_ratio(fg, bg), p["text_size"])
    g["relative_luminance"] = {"foreground": round(_lum(fg), 4), "background": round(_lum(bg), 4)}
    g["grayscale_delta"] = round(abs(_lum(fg) - _lum(bg)), 4)
    g["text_size"] = p["text_size"]
    g["note"] = "WCAG 2.x contrast ratio. Use 'large' only for text 24px+ or 18.66px+ bold."
    return g


# ----------------------------------------------------------------- compute.vram_budget

def op_vram_budget(p, ctx):
    fam = PROFILES["families"][p["family"]]
    prec_b = PROFILES["precision_bytes"][p["precision"]]
    resid = PROFILES["residency_factor"][p["residency"]]
    mp = p["width"] * p["height"] / 1e6
    act_factor = 0.5 if p["low_memory"] else 1.0
    comps = {
        "unet_or_transformer_gb": round(fam["unet_params_billion"] * prec_b * resid, 3),
        "text_encoders_gb": round(fam["text_encoder_params_billion"] * prec_b * resid, 3),
        "vae_gb": round(fam["vae_params_million"] / 1000 * 4.0 * resid, 3),
        "activations_gb": round(fam["activation_gb_per_megapixel"] * mp * p["batch"] * act_factor, 3),
        "controlnets_gb": round(p["controlnets"] * PROFILES["control_adapter_gb"], 3),
        "loras_gb": round(p["loras"] * PROFILES["lora_gb"], 3),
        "runtime_overhead_gb": PROFILES["overhead_gb"],
    }
    total = round(sum(comps.values()), 2)
    result = {"family": p["family"], "precision": p["precision"], "residency": p["residency"],
              "components_gb": comps, "estimated_total_gb": total,
              "note": "Planning estimate from rounded parameter counts and constants in asset-model-profiles.json. Measure on your hardware."}
    suggestions = []
    if p["vram_gb"]:
        usable = p["vram_gb"] * 0.9
        result["vram_gb"] = p["vram_gb"]
        result["fits_with_10_percent_margin"] = total <= usable
        if total > usable:
            if p["precision"] in ("fp32", "fp16", "bf16"):
                suggestions.append("try int8 or fp16 precision if your interface supports it")
            if p["residency"] == "none":
                suggestions.append("enable CPU offload (residency 'cpu') or sequential loading (residency 'sequential')")
            if not p["low_memory"]:
                suggestions.append("enable tiled VAE and attention slicing (low_memory=true in this estimate)")
            if p["batch"] > 1:
                suggestions.append("reduce batch size to 1")
            suggestions.append("reduce width and height or split into tiles (see compute.tile_plan)")
    result["suggestions"] = suggestions
    return result


# ----------------------------------------------------------------- compute.throughput

def op_throughput(p, ctx):
    rate = p.get("gpu_hour_cost")
    sec_img = p.get("seconds_per_image")
    fit = None
    if p["mode"] == "benchmark":
        runs = p.get("benchmark")
        if not isinstance(runs, list) or not runs:
            raise ToolError("invalid_input", "benchmark mode needs 'benchmark': list of {steps, batch, seconds}",
                            field="benchmark")
        pts = []
        for r in runs:
            try:
                pts.append((float(r["steps"]), float(r["seconds"]) / max(1, int(r.get("batch", 1)))))
            except (KeyError, TypeError, ValueError):
                raise ToolError("invalid_input", "each benchmark run needs numeric steps and seconds", field="benchmark")
        sec_img = sum(y for _, y in pts) / len(pts)
        xs = sorted(set(x for x, _ in pts))
        if len(xs) >= 2:
            mx = sum(x for x, _ in pts) / len(pts)
            my = sec_img
            den = sum((x - mx) ** 2 for x, _ in pts)
            slope = sum((x - mx) * (y - my) for x, y in pts) / den if den else 0.0
            fit = {"seconds_per_step": round(slope, 5), "intercept_seconds": round(my - slope * mx, 4)}
    if not sec_img or sec_img <= 0:
        raise ToolError("invalid_input", "provide seconds_per_image > 0 or a benchmark list", field="seconds_per_image")
    per_hour = 3600.0 / sec_img
    result = {"seconds_per_image": round(sec_img, 3), "images_per_hour": round(per_hour, 1),
              "images_per_day_24h": round(per_hour * 24, 1)}
    if rate is not None:
        result["cost_per_image"] = round(rate / per_hour, 5)
        result["cost_per_1000_images"] = round(rate / per_hour * 1000, 2)
    if p.get("target_images"):
        hours = p["target_images"] / per_hour
        result["hours_for_target"] = round(hours, 2)
        if rate is not None:
            result["cost_for_target"] = round(hours * rate, 2)
    if p.get("target_seconds") and fit and fit["seconds_per_step"] > 0:
        steps_for_target = (p["target_seconds"] - fit["intercept_seconds"]) / fit["seconds_per_step"]
        result["steps_for_target_seconds"] = max(1, int(steps_for_target))
    if fit:
        result["fit"] = fit
    result["note"] = "Estimates from supplied timings. Benchmark on the real workload; queueing and I/O add time."
    return result


# ----------------------------------------------------------------- compute.cloud_budget

def op_cloud_budget(p, ctx):
    rate_storage = p["storage_rate"] if p.get("storage_rate") is not None else ctx.cfg["economics"]["storage_rate_gb_month"]
    compute_cost = p["hourly_rate"] * p["hours_per_day"] * p["days"]
    storage_cost = p["storage_gb"] * rate_storage * (p["days"] / 30.0)
    total = round(compute_cost + storage_cost, 2)
    result = {"compute_cost": round(compute_cost, 2), "storage_cost": round(storage_cost, 2), "total_cost": total,
              "daily_budget_to_stay_under_cap": round(p["cost_cap"] / p["days"], 2),
              "recommended_auto_shutdown_minutes": p["shutdown_idle_minutes"],
              "warnings_list": []}
    if total > p["cost_cap"]:
        result["warnings_list"].append(f"projected cost {total} exceeds cap {p['cost_cap']}; reduce hours or days")
        result["within_cap"] = False
    else:
        result["within_cap"] = True
    result["note"] = "Set provider billing alerts as well. This is an estimate; check current rates before committing."
    return result


# ----------------------------------------------------------------- compute.gpu_queue

def op_gpu_queue(p, ctx):
    jobs = p["jobs"]
    workers = p["workers"]
    if not jobs or not workers:
        raise ToolError("invalid_input", "jobs and workers must be non-empty lists", field="jobs")
    if len(jobs) > ctx.limits["max_batch_items"]:
        raise ToolError("invalid_input", "too many jobs", field="jobs")
    for j in jobs:
        for k in ("id", "vram_gb", "seconds"):
            if k not in j:
                raise ToolError("invalid_input", f"each job needs '{k}'", field="jobs")
    order = sorted(jobs, key=lambda j: (-int(j.get("priority", 0)), -float(j["vram_gb"]), str(j["id"])))
    free_at = {w["id"]: 0.0 for w in workers}
    vram = {w["id"]: float(w["vram_gb"]) for w in workers}
    schedule, unscheduled = [], []
    for j in order:
        need = float(j["vram_gb"])
        cands = [wid for wid in free_at if vram[wid] >= need]
        if not cands:
            unscheduled.append({"id": j["id"], "reason": f"needs {need} GB; largest worker has {max(vram.values())} GB"})
            continue
        load = float(j.get("load_seconds", 0))
        wid = min(cands, key=lambda x: (free_at[x] + load + float(j["seconds"]), x))
        start = free_at[wid]
        end = start + load + float(j["seconds"])
        free_at[wid] = end
        schedule.append({"job": j["id"], "worker": wid, "start_s": round(start, 2), "end_s": round(end, 2)})
    makespan = max([s["end_s"] for s in schedule], default=0.0)
    busy = sum(s["end_s"] - s["start_s"] for s in schedule)
    util = round(busy / (makespan * len(workers)), 3) if makespan else 0.0
    return {"schedule": schedule, "unscheduled": unscheduled, "makespan_s": round(makespan, 2),
            "worker_utilisation": util,
            "note": "Greedy list scheduling: priority first, then largest VRAM need. One job per worker at a time."}


# ----------------------------------------------------------------- compute.tile_plan

def op_tile_plan(p, ctx):
    W, H, T, O, S = p["width"], p["height"], p["tile"], p["overlap"], p["scale"]
    if O >= T:
        raise ToolError("invalid_input", "overlap must be smaller than tile", field="overlap")
    step = T - O
    def starts(L):
        if L <= T:
            return [0]
        xs = list(range(0, L - T, step)) + [L - T]
        return sorted(set(xs))
    xs, ys = starts(W), starts(H)
    if len(xs) * len(ys) > 10000:
        raise ToolError("invalid_input", "tile count above 10000; increase tile size", field="tile")
    tiles = []
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            tw, th = min(T, W), min(T, H)
            tiles.append({"index": [i, j], "source_box": [x, y, min(W, x + T), min(H, y + T)],
                          "output_box": [x * S, y * S, min(W, x + T) * S, min(H, y + T) * S]})
    return {"grid": [len(xs), len(ys)], "tile_count": len(tiles), "tiles": tiles,
            "seam_blend_pixels_output": O * S,
            "note": "Blend overlapping output regions with a linear ramp of seam_blend_pixels_output width to hide seams."}


# ----------------------------------------------------------------- compute.control_conflicts

REF_PRIORITY = ["identity", "pose", "composition", "style", "palette", "object"]


def op_control_conflicts(p, ctx):
    items = p["items"]
    if not items:
        raise ToolError("invalid_input", "items must not be empty", field="items")
    warnings, errors = [], []
    cleaned = []
    for i, it in enumerate(items):
        name = str(it.get("name", f"item-{i + 1}"))
        try:
            w = float(it.get("weight", 1.0))
            s = float(it.get("start", 0.0))
            e = float(it.get("end", 1.0))
        except (TypeError, ValueError):
            errors.append(f"{name}: weight/start/end must be numbers")
            continue
        if not (0 <= w <= 2):
            errors.append(f"{name}: weight {w} must be between 0 and 2")
        if not (0 <= s < e <= 1):
            errors.append(f"{name}: need 0 <= start < end <= 1")
        if p["domain"] == "reference" and it.get("role") not in REF_PRIORITY:
            errors.append(f"{name}: role must be one of {REF_PRIORITY}")
        cleaned.append({"name": name, "role": it.get("role") or it.get("type"), "weight": w, "start": s, "end": e})
    if errors:
        raise ToolError("invalid_input", "; ".join(errors), field="items")
    total = sum(c["weight"] for c in cleaned)
    cap = p["cap"]
    if total > cap:
        warnings.append(f"total weight {total:.2f} exceeds cap {cap}; the controls will compete")
    for a, b in itertools.combinations(cleaned, 2):
        if a["role"] and a["role"] == b["role"] and a["weight"] > 0.5 and b["weight"] > 0.5:
            warnings.append(f"'{a['name']}' and '{b['name']}' share role/type '{a['role']}' at high weight")
        overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
        if overlap > 0 and a["weight"] + b["weight"] > 1.0:
            warnings.append(f"'{a['name']}' and '{b['name']}' overlap in time with combined weight {a['weight'] + b['weight']:.2f}")
    if p["domain"] == "reference":
        rank = {r: i for i, r in enumerate(REF_PRIORITY)}
        ordered = sorted(cleaned, key=lambda c: rank[c["role"]])
        priority = [c["name"] for c in ordered]
        factor = {}
        for i, c in enumerate(ordered):
            factor[c["name"]] = 1.0 if i == 0 else max(0.5, 1.0 - 0.15 * i)
    else:
        priority = [c["name"] for c in sorted(cleaned, key=lambda c: (c["start"], c["name"]))]
        factor = {c["name"]: 1.0 for c in cleaned}
    scaled = [c["weight"] * factor[c["name"]] for c in cleaned]
    st = sum(scaled)
    scale = min(1.0, cap / st) if st > 0 else 1.0
    recommended = [{"name": c["name"], "weight_before": c["weight"],
                    "weight_after": round(c["weight"] * factor[c["name"]] * scale, 3),
                    "start": c["start"], "end": c["end"]} for c in cleaned]
    return {"domain": p["domain"], "total_weight": round(total, 3), "cap": cap, "priority_order": priority,
            "recommended": recommended, "warnings_list": warnings,
            "note": "Recommendation only. Test one control at a time, then combine; keep seed and prompt fixed."}


# ----------------------------------------------------------------- compute.region_layout

def _rect_overlap(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def op_region_layout(p, ctx):
    if len(p["canvas"]) != 2 or p["canvas"][0] <= 0 or p["canvas"][1] <= 0:
        raise ToolError("invalid_input", "canvas must be [width, height] with positive values", field="canvas")
    cw, ch = p["canvas"]
    regs = p["regions"]
    warnings = []
    for r in regs:
        box = r.get("box")
        if not box or len(box) != 4 or any(not isinstance(v, (int, float)) for v in box):
            raise ToolError("invalid_input", f"region '{r.get('name')}' needs box [x, y, w, h]", field="regions")
        x, y, w, h = box
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > cw or y + h > ch:
            warnings.append(f"region '{r.get('name')}' is outside the canvas or has no area")
    overlaps = []
    for a, b in itertools.combinations(regs, 2):
        area = _rect_overlap(a["box"], b["box"])
        if area > 0:
            overlaps.append({"a": a.get("name"), "b": b.get("name"), "overlap_pixels": area})
    step_x, step_y = max(1, cw // 200), max(1, ch // 200)
    covered = total = 0
    for yy in range(0, ch, step_y):
        for xx in range(0, cw, step_x):
            total += 1
            if any(r["box"][0] <= xx < r["box"][0] + r["box"][2] and r["box"][1] <= yy < r["box"][1] + r["box"][3] for r in regs):
                covered += 1
    cov = round(100.0 * covered / total, 2) if total else 0.0
    if overlaps:
        warnings.append(f"{len(overlaps)} overlapping region pair(s); assign one prompt per pixel or set priorities")
    per = [{"name": r.get("name"), "area_percent": round(100.0 * r["box"][2] * r["box"][3] / (cw * ch), 2),
            "prompt": r.get("prompt")} for r in regs]
    return {"canvas": [cw, ch], "coverage_percent": cov, "regions": per, "overlaps": overlaps,
            "warnings_list": warnings,
            "note": "Coverage is sampled on a grid. Regions outside coverage use the global prompt."}


# ----------------------------------------------------------------- compute.occlusion_order

def op_occlusion_order(p, ctx):
    objs = p["objects"]
    if not objs:
        raise ToolError("invalid_input", "objects must not be empty", field="objects")
    for o in objs:
        if "depth" not in o or "box" not in o:
            raise ToolError("invalid_input", "each object needs 'depth' (smaller is nearer) and 'box'", field="objects")
    paint = sorted(objs, key=lambda o: (-float(o["depth"]), str(o.get("name"))))
    overlaps = []
    for a, b in itertools.combinations(objs, 2):
        area = _rect_overlap(a["box"], b["box"])
        if area > 0:
            front = a if float(a["depth"]) < float(b["depth"]) else b
            back = b if front is a else a
            overlaps.append({"front": front.get("name"), "behind": back.get("name"), "overlap_pixels": area})
    return {"paint_order_far_to_near": [o.get("name") for o in paint], "overlaps": overlaps,
            "note": "Depth is a relative value you supply; equal depths are ordered by name."}


# ----------------------------------------------------------------- compute.training_plan

def op_training_plan(p, ctx):
    mode = p["mode"]
    dfl = PROFILES["training_defaults"][mode]
    n = p["num_images"]
    if n < 1:
        raise ToolError("invalid_input", "num_images must be >= 1", field="num_images")
    lr = p.get("learning_rate") if p.get("learning_rate") is not None else dfl["learning_rate"]
    res = p.get("resolution") or dfl["resolution"]
    rank = p.get("rank") if p.get("rank") is not None else dfl.get("rank")
    alpha = p.get("alpha") if p.get("alpha") is not None else dfl.get("alpha")
    samples = n * p["repeats"] * p["epochs"]
    steps = math.ceil(samples / (p["batch_size"] * p["grad_accum"]))
    per_image = steps / n
    warnings = []
    if per_image > dfl["warn_steps_per_image"]:
        warnings.append(f"{per_image:.0f} steps per image is above the {dfl['warn_steps_per_image']} guideline; overfitting and memorisation risk rises. Use the overfitting auditor and early checkpoints.")
    if n < 10 and mode == "lora":
        warnings.append("fewer than 10 images is a thin dataset for LoRA; expect instability")
    if res > 1024:
        warnings.append("resolution above 1024 is rarely needed for training and increases VRAM")
    checkpoint_every = max(1, steps // 4)
    return {"mode": mode, "total_steps": steps, "steps_per_image": round(per_image, 1),
            "learning_rate": lr, "resolution": res, "rank": rank, "alpha": alpha,
            "checkpoint_every_steps": checkpoint_every,
            "validation": "run prompt.eval_suite at each checkpoint with fixed seeds",
            "warnings_list": warnings,
            "execution": "not implemented in this toolkit; use your trainer with these numbers as a starting point"}


# ----------------------------------------------------------------- compute.blind_pairs

def op_blind_pairs(p, ctx):
    items = p["items"]
    if len(set(items)) != len(items) or len(items) < 2:
        raise ToolError("invalid_input", "items must be at least 2 unique ids", field="items")
    pairs = list(itertools.combinations(items, 2))
    rng = random.Random(p["seed"])
    rng.shuffle(pairs)
    pairs = pairs[: p["max_pairs"]]
    out = []
    for i, (a, b) in enumerate(pairs, 1):
        left, right = (a, b) if rng.random() < 0.5 else (b, a)
        out.append({"pair_id": f"pair-{i:03d}", "left": left, "right": right})
    path = ctx.out_path(p.get("output"), "ballot.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["pair_id", "left_item", "right_item", "preferred_side(left/right/tie)", "rater"])
        for r in out:
            wr.writerow([r["pair_id"], r["left"], r["right"], "", ""])
    ctx.record(path)
    key_path = ctx.cap_dir / "ballot-key.json"
    write_json(key_path, {"seed": p["seed"], "pairs": out})
    ctx.record(key_path)
    return {"pair_count": len(out), "ballot_file": str(path), "key_file": str(key_path),
            "note": "Raters see only the ballot CSV. Open the key file only after scoring. Left/right assignment is randomised."}


# ----------------------------------------------------------------- system.probe

def _meminfo():
    p = Path("/proc/meminfo")
    if not p.exists():
        return None
    vals = {}
    for line in p.read_text().splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            vals[m.group(1)] = int(m.group(2)) * 1024
    return {"total_bytes": vals.get("MemTotal"), "available_bytes": vals.get("MemAvailable")}


def _nvidia():
    if not shutil.which("nvidia-smi"):
        return {"available": False, "reason": "nvidia-smi not found on PATH"}
    cmd = ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu,driver_version",
           "--format=csv,noheader,nounits"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "nvidia-smi timed out after 5 seconds"}
    if out.returncode != 0:
        return {"available": False, "reason": out.stderr.strip()[:300]}
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 6:
            gpus.append({"name": parts[0], "memory_total_mib": _to_num(parts[1]), "memory_used_mib": _to_num(parts[2]),
                         "temperature_c": _to_num(parts[3]), "utilisation_percent": _to_num(parts[4]),
                         "driver": parts[5]})
    return {"available": True, "gpus": gpus}


def _to_num(s):
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def _packages():
    res = {}
    for name in ("Pillow", "numpy", "torch"):
        res[name] = importlib.util.find_spec(name.lower() if name != "Pillow" else "PIL") is not None
    return res


def op_system_probe(p, ctx):
    now = {"python": sys.version.split()[0], "platform": platform.platform(), "cpu_count": __import__("os").cpu_count(),
           "memory": _meminfo(), "gpu": _nvidia(), "optional_packages_installed": _packages()}
    du = shutil.disk_usage(ctx.output_dir if ctx.output_dir.exists() else Path.cwd())
    now["disk_free_bytes_output_location"] = du.free
    now["pillow_version"] = None
    try:
        import PIL
        now["pillow_version"] = PIL.__version__
    except ImportError:
        pass
    if p["mode"] in ("setup-check", "gpu-compat"):
        if p["mode"] == "gpu-compat":
            hints = []
            if not now["gpu"]["available"]:
                hints.append("No NVIDIA GPU reported. CPU-only generation is slow; check driver installation and PATH.")
            if now["optional_packages_installed"]["torch"] is False:
                hints.append("PyTorch is not installed; install the build matching your GPU driver from the official index.")
            now["hints"] = hints
        return now
    samples = []
    for i in range(p["samples"]):
        sample = {"t": i, "memory_available_bytes": (_meminfo() or {}).get("available_bytes"),
                  "load_average": list(__import__("os").getloadavg()) if hasattr(__import__("os"), "getloadavg") else None}
        g = _nvidia()
        if g.get("available"):
            sample["gpus"] = [{"name": x["name"], "temperature_c": x["temperature_c"], "utilisation_percent": x["utilisation_percent"],
                               "memory_used_mib": x["memory_used_mib"]} for x in g["gpus"]]
        samples.append(sample)
        if i + 1 < p["samples"]:
            time.sleep(p["interval"])
    now["samples"] = samples
    now["note"] = "Read-only sampling. Long batches: stop the run if temperature or memory trends upward."
    return now


# ----------------------------------------------------------------- system.model_storage

_KIND_HINTS = [("checkpoint", "checkpoints"), ("lora", "loras"), ("lycoris", "loras"), ("vae", "vae"),
               ("embedding", "embeddings"), ("controlnet", "controlnet"), ("upscale", "upscalers")]


def op_model_storage(p, ctx):
    root = Path(p["root"])
    exts = {e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower() for e in p["extensions"]}
    files = []
    for f in root.rglob("*"):
        if f.is_file() and f.suffix.lower() in exts:
            files.append(f)
        if len(files) > ctx.limits["max_batch_items"] * 10:
            raise ToolError("invalid_input", "too many model files; narrow the root folder", field="root")
    inv = []
    for f in sorted(files):
        rel_parts = [x.lower() for x in f.relative_to(root).parts[:-1]]
        kind = "unknown"
        for part in rel_parts:
            for key, label in _KIND_HINTS:
                if key in part:
                    kind = label
                    break
            if kind != "unknown":
                break
        size = f.stat().st_size
        entry = {"path": str(f.relative_to(root)), "kind": kind, "size_bytes": size}
        if size == 0:
            entry["warning"] = "zero-byte file"
        if p["hash"]:
            entry["sha256"] = sha256_file(f)
        inv.append(entry)
    dup = {}
    if p["hash"]:
        for e in inv:
            dup.setdefault(e["sha256"], []).append(e["path"])
    dups = [v for v in dup.values() if len(v) > 1]
    if not p["hash"]:
        bysize = {}
        for e in inv:
            bysize.setdefault(e["size_bytes"], []).append(e["path"])
        dups = [v for v in bysize.values() if len(v) > 1]
    path = ctx.out_path(p.get("output"), "model-inventory.json")
    write_json(path, {"root_label": root.name, "files": inv, "duplicate_groups": dups})
    ctx.record(path)
    total = sum(e["size_bytes"] for e in inv)
    if not p["hash"]:
        ctx.warn("hashing disabled: duplicate groups are based on identical file sizes only, which is a weak signal")
    return {"file_count": len(inv), "total_gb": round(total / 1e9, 3), "by_kind": _count_kinds(inv),
            "duplicate_groups": dups, "inventory_file": str(path),
            "note": "Read-only. Nothing is moved, linked or deleted. Review duplicates before any cleanup."}


def _count_kinds(inv):
    out = {}
    for e in inv:
        out[e["kind"]] = out.get(e["kind"], 0) + 1
    return out


# ----------------------------------------------------------------- system.env_lock

def op_env_lock(p, ctx):
    pkgs = {}
    names = set(p.get("packages") or []) | {"pillow"}
    for n in sorted(names):
        try:
            pkgs[n] = importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            pkgs[n] = None
    if p.get("include_all"):
        for d in importlib.metadata.distributions():
            nm = d.metadata.get("Name")
            if nm and nm not in pkgs:
                pkgs[nm] = d.version
    record = {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
              "packages": dict(sorted(pkgs.items())), "toolkit_version": C.TOOLKIT_VERSION}
    path = ctx.out_path(p.get("output"), "environment-record.json")
    write_json(path, record)
    ctx.record(path)
    missing = [k for k, v in pkgs.items() if v is None]
    if missing:
        ctx.warn(f"not installed: {', '.join(missing)}")
    return {"environment_file": str(path), "package_count": len(pkgs), "missing": missing,
            "note": "A record of what ran, not a pinned lock file. Pin versions in your own requirements when rebuilding."}


# ----------------------------------------------------------------- registry

OPS = {
    "compute.tensor_spec": op(op_tensor_spec, [
        P("width", "int", True, min=1, max=65536), P("height", "int", True, min=1, max=65536),
        P("channels", "int", default=3, min=1, max=16),
        P("bit_depth", "choice", default="8", choices=["8", "16", "32"]),
        P("batch", "int", default=1, min=1, max=1024),
        P("layout", "choice", default="NCHW", choices=["NCHW", "NHWC"]),
        P("latent_channels", "int", default=4, min=1, max=64),
        P("vae_scale", "int", default=8, min=1, max=16),
    ], "Shape, memory and latent size for an image tensor"),
    "compute.latent_interpolation": op(op_latent_interpolation, [
        P("steps", "int", default=8, min=2, max=32), P("latent_w", "int", default=64, min=8, max=512),
        P("latent_h", "int", default=64, min=8, max=512), P("latent_channels", "int", default=4, min=1, max=16),
        P("seed_a", "int", default=1, min=0), P("seed_b", "int", default=2, min=0),
        P("method", "choice", default="slerp", choices=["slerp", "lerp"]),
    ], "Interpolate between two seeded latent vectors and report norms"),
    "compute.diffusion_schedule": op(op_diffusion_schedule, [
        P("train_steps", "int", default=1000, min=2, max=1000),
        P("beta_start", "float", default=0.00085, min=1e-6, max=0.5),
        P("beta_end", "float", default=0.012, min=1e-6, max=0.9),
        P("sample_steps", "int", default=20, min=1, max=1000),
    ], "Compute linear-beta noise levels and SNR at sampled timesteps"),
    "compute.sweep_grid": op(op_sweep_grid, [
        P("prompt", "str", True), P("negative_prompt", "str"),
        P("axes", "json", help="JSON object of axis name -> list of values"),
        P("domain", "choice", default="sampler", choices=["sampler", "merge", "general"]),
        P("name_template", "str", default="{sampler}_s{steps}_cfg{cfg}_seed{seed}"),
        P("output", "outfile"),
    ], "Expand sweep axes into a deterministic list of comparable runs"),
    "compute.resolution": op(op_resolution, [
        P("family", "choice", True, choices=FAMILIES),
        P("aspect", "str", default="1:1", help="e.g. 3:2"),
        P("multiple", "int", default=64, choices=None, min=8, max=128),
    ], "Pick model-friendly width and height near the family's pixel budget"),
    "compute.print_resolution": op(op_print_resolution, [
        P("width_in", "float", True, min=0.1), P("height_in", "float", True, min=0.1),
        P("dpi", "int", default=300, min=72, max=1200), P("bleed_in", "float", default=0.125, min=0, max=1),
        P("viewing_distance_m", "float", default=0.5, min=0.1, max=50),
        P("source_width", "int", min=1), P("source_height", "int", min=1),
    ], "Pixel size, bleed and effective DPI for print"),
    "compute.contrast": op(op_contrast, [
        P("foreground", "str"), P("background", "str"),
        P("text_size", "choice", default="normal", choices=["normal", "large"]),
        P("pairs", "json", help="JSON list of {name, fg, bg} to check many pairs"),
    ], "WCAG contrast ratio for text colour pairs"),
    "compute.vram_budget": op(op_vram_budget, [
        P("family", "choice", True, choices=FAMILIES),
        P("precision", "choice", default="fp16", choices=sorted(PROFILES["precision_bytes"].keys())),
        P("width", "int", default=1024, min=64, max=8192), P("height", "int", default=1024, min=64, max=8192),
        P("batch", "int", default=1, min=1, max=64),
        P("controlnets", "int", default=0, min=0, max=8), P("loras", "int", default=0, min=0, max=32),
        P("residency", "choice", default="none", choices=sorted(PROFILES["residency_factor"].keys())),
        P("low_memory", "bool", default=False), P("vram_gb", "float", min=1, max=1024),
    ], "Estimate GPU memory for a diffusion setup"),
    "compute.throughput": op(op_throughput, [
        P("mode", "choice", default="estimate", choices=["estimate", "benchmark"]),
        P("seconds_per_image", "float", min=0.001), P("gpu_hour_cost", "float", min=0),
        P("target_images", "int", min=1), P("target_seconds", "float", min=0.1),
        P("benchmark", "json", help="List of {steps, batch, seconds} from real runs"),
    ], "Images per hour, cost per image and step guidance"),
    "compute.cloud_budget": op(op_cloud_budget, [
        P("hourly_rate", "float", True, min=0), P("hours_per_day", "float", True, min=0, max=24),
        P("days", "int", True, min=1, max=3660), P("cost_cap", "float", True, min=0),
        P("storage_gb", "float", default=0, min=0), P("storage_rate", "float", min=0),
        P("shutdown_idle_minutes", "int", default=15, min=1, max=1440),
    ], "Cloud GPU cost estimate against a hard cap"),
    "compute.gpu_queue": op(op_gpu_queue, [
        P("jobs", "json", True, help="List of {id, vram_gb, seconds, priority?, load_seconds?}"),
        P("workers", "json", True, help="List of {id, vram_gb}"),
    ], "Schedule jobs across workers by VRAM and priority"),
    "compute.tile_plan": op(op_tile_plan, [
        P("width", "int", True, min=1, max=65536), P("height", "int", True, min=1, max=65536),
        P("tile", "int", default=512, min=64, max=4096), P("overlap", "int", default=64, min=0, max=2048),
        P("scale", "int", default=2, min=1, max=4),
    ], "Tile grid with overlap and seam-blend width for large images"),
    "compute.control_conflicts": op(op_control_conflicts, [
        P("items", "json", True, help="List of {name, role|type, weight, start, end}"),
        P("domain", "choice", default="control", choices=["control", "reference"]),
        P("cap", "float", default=2.0, min=0.1, max=10),
    ], "Find competing conditioning signals and recommend weights"),
    "compute.region_layout": op(op_region_layout, [
        P("canvas", "intlist", True, help="[width, height]"),
        P("regions", "json", True, help="List of {name, box:[x,y,w,h], prompt}"),
    ], "Check regional prompt boxes for bounds, overlap and coverage"),
    "compute.occlusion_order": op(op_occlusion_order, [
        P("objects", "json", True, help="List of {name, box:[x,y,w,h], depth} (smaller depth = nearer)"),
    ], "Paint order and front-behind pairs for inserted objects"),
    "compute.training_plan": op(op_training_plan, [
        P("mode", "choice", True, choices=["lora", "dreambooth", "textual-inversion"]),
        P("num_images", "int", True, min=1, max=100000),
        P("repeats", "int", default=10, min=1, max=1000), P("epochs", "int", default=10, min=1, max=10000),
        P("batch_size", "int", default=1, min=1, max=64), P("grad_accum", "int", default=1, min=1, max=64),
        P("learning_rate", "float", min=0), P("rank", "int", min=1, max=512), P("alpha", "float", min=0),
        P("resolution", "int", min=256, max=4096),
    ], "Plan steps, checkpoints and guidance for a training run (no training)"),
    "compute.blind_pairs": op(op_blind_pairs, [
        P("items", "list", True, help="Comma list of unique item ids"),
        P("seed", "int", default=0, min=0), P("max_pairs", "int", default=50, min=1, max=500),
        P("output", "outfile"),
    ], "Randomised blind A/B pairs with a rater ballot CSV"),
    "system.probe": op(op_system_probe, [
        P("mode", "choice", default="setup-check", choices=["setup-check", "gpu-compat", "monitor"]),
        P("samples", "int", default=3, min=1, max=60), P("interval", "float", default=1.0, min=0.5, max=10),
    ], "Read-only environment, GPU and memory probe (monitor mode samples over time)"),
    "system.model_storage": op(op_model_storage, [
        P("root", "dir", True, help="Folder that holds model files (read-only scan)"),
        P("extensions", "list", default=".safetensors,.ckpt,.pt,.pth,.bin,.gguf,.onnx"),
        P("hash", "bool", default=True), P("output", "outfile"),
    ], "Inventory model files with kind, size, SHA-256 and duplicates (no changes)"),
    "system.env_lock": op(op_env_lock, [
        P("packages", "list", help="Extra package names to record"),
        P("include_all", "bool", default=False, help="Record every installed distribution"),
        P("output", "outfile"),
    ], "Record Python, platform and package versions for reproducibility"),
}
