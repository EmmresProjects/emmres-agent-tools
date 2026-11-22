#!/usr/bin/env python3
"""tools-neural.py — offline neural image and motion generation.

Two honest modes:
1. DETERMINISTIC STAND-INS (no models required): seeded procedural generators
   built on the local imaging engine. They never pretend to be diffusion
   models — every result is marked "engine": "procedural-standin" so triage
   and review gates treat them as drafts, not model output.
2. EXTERNAL RUNTIMES (optional): when a local runtime command is configured
   (studio-config.json -> neural.runtimes), the module builds the exact
   launch command, runs it bounded via core.run_command, and verifies the
   promised outputs exist. Nothing is downloaded; nothing is faked. If a
   runtime is not configured, callers receive a structured
   runtime_not_configured error and can fall back to stand-ins explicitly.
Also: prompt/seed recipes, provenance records, quantization planning and
generated-asset triage with an approval gate. Pure stdlib.
"""
import math

core = None
im = None
err = None
require = None


def _init():
    global core, im
    if core is None:
        import importlib.util as ilu
        import sys as sys_
        from pathlib import Path as P

        def sib(name):
            key = "studio." + name
            mod = sys_.modules.get(key)
            if mod is not None:
                return mod
            p = P(__file__).resolve().parent / ("tools-" + name + ".py")
            spec = ilu.spec_from_file_location(key, p)
            mod = ilu.module_from_spec(spec)
            sys_.modules[key] = mod
            spec.loader.exec_module(mod)
            return mod
        core = sib("core")
        im = sib("imaging")
    return core, im


def err(code, message, **details):
    _init()
    return core.err(code, message, **details)


def require(cond, code, message, **details):
    _init()
    return core.require(cond, code, message, **details)


# --------------------------------------------------------- runtime mgmt -----

def runtime_report():
    """List configured neural runtimes and their availability."""
    _init()
    cfg = core.load_config()
    runtimes = core.cfg_get(cfg, "neural.runtimes") or {}
    out = []
    for name, rt in runtimes.items():
        rt = rt if isinstance(rt, dict) else {"command": str(rt)}
        exe = rt.get("command", "")
        avail = bool(core.find_executable(exe)) if exe else False
        out.append({"name": name, "command": exe, "available": avail,
                    "purpose": rt.get("purpose", ""),
                    "model_path": rt.get("model_path", "") or None})
    return {"runtimes": out,
            "configured": bool(out),
            "note": "no runtimes configured: procedural stand-ins remain available; "
                    "configure neural.runtimes in studio-config.json to enable local "
                    "model inference (nothing is downloaded automatically)"}


def run_runtime(name, args, expect_files=None, timeout=None):
    """Run a configured local runtime bounded, then verify outputs exist."""
    _init()
    cfg = core.load_config()
    runtimes = core.cfg_get(cfg, "neural.runtimes") or {}
    rt = runtimes.get(name)
    if rt is None:
        raise err("runtime_not_configured",
                  "neural runtime '%s' is not configured; add it to "
                  "studio-config.json (neural.runtimes) or use the procedural "
                  "stand-in generators" % name, runtime=name)
    rt = rt if isinstance(rt, dict) else {"command": str(rt)}
    exe = rt.get("command", "")
    found = core.find_executable(exe)
    if not found:
        raise err("runtime_not_available",
                  "configured runtime '%s' command not found on this machine: %s"
                  % (name, exe), runtime=name)
    argv = [found] + [str(a) for a in (args or [])]
    tmo = timeout or core.cfg_get(cfg, "neural.default_timeout") or 900
    res = core.run_command(argv, timeout=tmo)
    missing = []
    for f in (expect_files or []):
        import os
        if not os.path.isfile(str(f)):
            missing.append(str(f))
    return {**res, "argv": argv, "missing_outputs": missing,
            "outputs_verified": not missing}


def quantization_plan(params_b, target="RAM", available_bytes=None, purpose="inference"):
    """Model memory planning: pick precision to fit the machine."""
    _init()
    rep = core.machine_report()
    avail = available_bytes
    if avail is None:
        if target == "RAM":
            try:
                with open("/proc/meminfo") as fh:
                    for line in fh:
                        if line.startswith("MemAvailable:"):
                            avail = int(line.split()[1]) * 1024
                            break
            except OSError:
                avail = None
        if avail is None:
            avail = 2 * 1024 ** 3
    sizes = {"fp32": 4, "fp16": 2, "q8": 1, "q4": 0.5}
    plans = []
    for prec, bpc in sizes.items():
        need = params_b * bpc
        plans.append({"precision": prec, "bytes_per_param": bpc,
                      "model_bytes": int(need),
                      "fits": need <= avail,
                      "overhead_note": "runtime overhead typically adds 20-50 percent"})
    fits = [p for p in plans if p["fits"]]
    best = fits[0] if fits else None
    return {"purpose": purpose, "available_bytes": avail,
            "available_human": core.human_size(avail),
            "recommended": best, "plans": plans,
            "note": "estimates only; measure the actual runtime before committing"}


def model_provenance(path, meta=None, out_path=None):
    """Record model source, hash and license terms (provenance + license audit)."""
    _init()
    import hashlib
    meta = meta or {}
    rec = {"model": str(path), "sha256": core.sha256_file(path),
           "bytes": core.human_size(len(core.read_bytes(path))),
           "source": meta.get("source", "unknown"),
           "version": meta.get("version", ""),
           "license_class": meta.get("license_class", "unknown"),
           "commercial_use_allowed": meta.get("commercial_use_allowed"),
           "output_ownership_claimed": meta.get("output_ownership_claimed"),
           "attribution_required": meta.get("attribution_required", False),
           "training_data_disclosure": meta.get("training_data_disclosure", ""),
           "recorded": core.utc_now()}
    if out_path:
        core.save_json(out_path, rec)
    return rec


# ------------------------------------------------------------ recipes -------

def save_recipe(out_path, prompt, seed, kind="txt2img", **params):
    """Prompt/Seed Reproducer: everything needed to recreate an output."""
    _init()
    recipe = {"kind": kind, "prompt": prompt,
              "negative_prompt": params.pop("negative_prompt", ""),
              "seed": seed, "sampler": params.pop("sampler", "procedural"),
              "schedule": params.pop("schedule", ""),
              "engine": params.pop("engine", "procedural-standin"),
              "model_version": params.pop("model_version", ""),
              "params": params,
              "tool_version": core.TOOL_VERSION,
              "created": core.utc_now()}
    core.save_json(out_path, recipe)
    return recipe


def load_recipe(path):
    _init()
    return core.load_json(path)


# ------------------------------------------------- procedural generators ----

def _palette_for(prompt, seed):
    """Deterministically pick/generate a palette from the prompt + seed."""
    _init()
    rng = core.RNG("pal:%s:%s" % (prompt, seed))
    try:
        pal_data = core.load_json(core.ROOT / "asset-color-palettes.json")
        names = sorted(pal_data.get("palettes", {}).keys())
    except Exception:
        names = ["studio-neutral"]
    name = names[int(rng.random() * len(names)) % len(names)]
    try:
        colors = [c["hex"] for c in pal_data["palettes"][name]["colors"]]
    except Exception:
        colors = ["#20262e", "#8a8a96", "#c9c9d2", "#e0a54b"]
    return name, [im.hex_to_rgb(c) for c in colors]


def txt2img(prompt, size=(320, 240), seed=None, cells=None, octaves=3):
    """Procedural text-to-image stand-in: deterministic composition sketch
    (regions, palette, value structure, texture) seeded by prompt+seed.
    Useful for layout/value studies and pipeline tests — NOT a semantic
    diffusion model; it cannot depict prompt subjects."""
    _init()
    seed = seed if seed is not None else core.slugify(prompt)[:24] or "seed"
    require(len(size) == 2 and size[0] > 0 and size[1] > 0, "invalid_input", "bad size")
    core.check_limits(None, pixels=size[0] * size[1])
    w, h = size
    rng = core.RNG("t2i:%s:%s" % (prompt, seed))
    pal_name, pal = _palette_for(prompt, seed)
    img = im.new_image(w, h, pal[0] + (255,))
    # split canvas into horizon bands (value structure)
    horizon = h * (0.35 + rng.random() * 0.3)
    for band, ci in ((horizon, 1), (h, 2)):
        band_img = im.new_image(w, h, (0, 0, 0, 0))
        im.draw_rect(band_img, 0, int(horizon) if ci == 2 else 0, w,
                     int(h - horizon) if ci == 2 else int(horizon), pal[ci] + (255,))
        img = im.composite(img, band_img, "normal", 0.85 if ci == 1 else 1.0)
    # focal mass blobs
    n_masses = 2 + int(rng.random() * 3)
    for i in range(n_masses):
        cx = rng.random() * w
        cy = horizon + rng.random() * (h - horizon) * 0.9
        r = (0.08 + rng.random() * 0.22) * min(w, h)
        ci = 3 if i == 0 else (1 + int(rng.random() * (len(pal) - 2)))
        blob = im.new_image(w, h, (0, 0, 0, 0))
        im.draw_ellipse(blob, int(cx), int(cy), int(r), int(r * (0.6 + rng.random() * 0.6)),
                        pal[ci % len(pal)] + (255,))
        img = im.composite(img, blob, "normal", 0.9)
    # texture field
    noise = im.value_noise(w, h, "t2i:%s:%s" % (prompt, seed),
                           cells=cells or int(4 + rng.random() * 6), octaves=octaves)
    px = img.px
    for p in range(w * h):
        i = p * 4
        d = (noise[p] - 128) * 0.18
        px[i] = im._clamp8(px[i] + d)
        px[i + 1] = im._clamp8(px[i + 1] + d)
        px[i + 2] = im._clamp8(px[i + 2] + d)
    return img, {"engine": "procedural-standin", "prompt": prompt, "seed": seed,
                 "palette": pal_name, "size": [w, h],
                 "limitations": ["no semantic understanding of the prompt",
                                 "cannot depict subjects; produces value/layout studies"]}


def img2img(source, strength=0.6, seed=None, mode="stylize"):
    """Procedural image-to-image stand-in: posterize + palette-map + edge
    lift + grain at a given strength (0..1). Deterministic."""
    _init()
    require(0.0 <= strength <= 1.0, "invalid_input", "strength must be 0..1")
    seed = seed or "i2i"
    rng = core.RNG("i2i:%s" % seed)
    _, pal = _palette_for("i2i:%s" % seed, seed)
    out = source.clone()
    if mode == "stylize":
        levels = max(2, int(round(10 - strength * 7)))
        out = im.op_posterize(out, levels)
        lut = [0] * 256
        n = len(pal) - 1
        for v in range(256):
            lut[v] = pal[min(n, v * n // 255)][0]
        out = im.apply_lut(out, lut)
        if strength > 0.4:
            out = im.op_hsv(out, hue_shift=rng.random() * 40 * strength,
                            sat_scale=1.0 + strength * 0.5)
        out = im.add_grain(out, int(8 * strength), seed)
    elif mode == "edge":
        gray = im.apply_lut(out, [ (v * 54 + v * 183 + v * 19) >> 8 for v in range(256)])
        edges = im.new_image(out.w, out.h, (255, 255, 255, 255))
        px, ep = gray.px, edges.px
        for y in range(1, out.h - 1):
            for x in range(1, out.w - 1):
                i = (y * out.w + x) * 4
                gx = abs(px[i - 4] - px[i + 4])
                gy = abs(px[i - out.w * 4] - px[i + out.w * 4])
                g = im._clamp8((gx + gy) * (1.5 + strength * 2))
                ep[i] = ep[i + 1] = ep[i + 2] = 255 - g
                ep[i + 3] = 255
        out = edges
    else:
        raise err("invalid_input", "mode must be stylize or edge")
    return out, {"engine": "procedural-standin", "mode": mode, "strength": strength,
                 "seed": seed, "source_size": [source.w, source.h]}


def inpaint(source, mask, seed=None, blur=6):
    """Procedural inpainting stand-in: fill the masked region with surrounding
    color statistics + noise. Structure-aware filling needs a real model
    (configure a runtime); results here are for cleanup drafts only.
    mask: Image with alpha>128 marking the region to replace."""
    _init()
    require(source.w == mask.w and source.h == mask.h, "invalid_input",
            "mask size mismatch")
    seed = seed or "inpaint"
    # collect border statistics
    samples = []
    spx, mpx = source.px, mask.px
    for y in range(source.h):
        for x in range(source.w):
            i = (y * source.w + x) * 4
            if mpx[i + 3] > 128:
                for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < source.w and 0 <= ny < source.h:
                        j = (ny * source.w + nx) * 4
                        if mpx[j + 3] <= 128 and spx[j + 3] > 0:
                            samples.append((spx[j], spx[j + 1], spx[j + 2]))
    out = source.clone()
    if not samples:
        fill = (128, 128, 128)
        rng = core.RNG(seed)
        samples = [(int(rng.random() * 255), int(rng.random() * 255),
                    int(rng.random() * 255)) for _ in range(16)]
        fill = samples[0]
    noise = im.value_noise(source.w, source.h, "inpaint:%s" % seed, cells=24, octaves=2)
    n = len(samples)
    for p in range(source.w * source.h):
        i = p * 4
        if mpx[i + 3] > 128:
            s = samples[(p * 7 + i) % n]
            d = (noise[p] - 128) * 0.25
            out.px[i] = im._clamp8(s[0] + d)
            out.px[i + 1] = im._clamp8(s[1] + d)
            out.px[i + 2] = im._clamp8(s[2] + d)
    if blur > 0:
        blurred = im.blur_image(out, blur)
        for p in range(source.w * source.h):
            i = p * 4
            if mpx[i + 3] > 128:
                out.px[i] = blurred.px[i]
                out.px[i + 1] = blurred.px[i + 1]
                out.px[i + 2] = blurred.px[i + 2]
    return out, {"engine": "procedural-standin", "seed": seed,
                 "note": "statistical fill; use a real runtime for structure-aware inpainting"}


def remove_background(source, tolerance=26, edge_refine=True):
    """Local background removal: flood-fill from borders over similar colors
    (keying), then optional edge cleanup. Deterministic; no model needed."""
    _init()
    w, h = source.w, source.h
    out = source.clone()
    px = out.px
    visited = bytearray(w * h)
    stack = []
    for x in range(w):
        stack.append(x)
        stack.append((h - 1) * w + x)
    for y in range(h):
        stack.append(y * w)
        stack.append(y * w + w - 1)
    seeds = list(dict.fromkeys(stack))
    if not seeds:
        return out, {"engine": "flood-key", "removed": 0}
    base = {}
    removed = 0
    for p in seeds:
        i = p * 4
        if px[i + 3] > 0:
            base[(px[i], px[i + 1], px[i + 2])] = base.get((px[i], px[i + 1], px[i + 2]), 0) + 1
    ref = max(base, key=base.get) if base else None
    if ref is None:
        return out, {"engine": "flood-key", "removed": 0}
    stack = [p for p in seeds]
    while stack:
        p = stack.pop()
        if visited[p]:
            continue
        visited[p] = 1
        i = p * 4
        if px[i + 3] == 0:
            continue
        d = abs(px[i] - ref[0]) + abs(px[i + 1] - ref[1]) + abs(px[i + 2] - ref[2])
        if d > tolerance * 3:
            continue
        px[i + 3] = 0
        removed += 1
        x, y = p % w, p // w
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                q = ny * w + nx
                if not visited[q]:
                    stack.append(q)
    if edge_refine:
        out = im.defringe(out, radius=1)
    return out, {"engine": "flood-key", "removed": removed,
                 "reference_color": list(ref), "tolerance": tolerance}


def cleanup_lineart(source, threshold=96, despeckle=2):
    """Line-art cleanup stand-in: binarize, despeckle, light close."""
    _init()
    w, h = source.w, source.h
    out = im.new_image(w, h, (255, 255, 255, 255))
    px, spx = out.px, source.px
    for p in range(w * h):
        i = p * 4
        luma = (spx[i] * 54 + spx[i + 1] * 183 + spx[i + 2] * 19) >> 8
        v = 0 if luma < threshold else 255
        px[i] = px[i + 1] = px[i + 2] = v
        px[i + 3] = 255 if spx[i + 3] > 8 else 0
    # despeckle: remove isolated dark pixels
    for _ in range(max(1, despeckle)):
        src = out.clone()
        sp = src.px
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                i = (y * w + x) * 4
                if sp[i] == 0:
                    dark_n = 0
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx or dy:
                                j = ((y + dy) * w + x + dx) * 4
                                if sp[j] == 0:
                                    dark_n += 1
                    if dark_n == 0:
                        px[i] = px[i + 1] = px[i + 2] = 255
    return out, {"engine": "procedural-standin", "threshold": threshold}


def colorize(source, palette=None, seed=None):
    """Colorization stand-in: map luminance onto a palette ramp (editable
    output — artists re-paint on top)."""
    _init()
    seed = seed or "colorize"
    if palette is None:
        _, palette = _palette_for("colorize:%s" % seed, seed)
    ramp = sorted(palette)
    lut = []
    n = len(ramp) - 1
    for v in range(256):
        t = v / 255.0 * n
        i0 = min(n, int(t))
        i1 = min(n, i0 + 1)
        f = t - i0
        lut.append(tuple(int(ramp[i0][c] + (ramp[i1][c] - ramp[i0][c]) * f) for c in range(3)))
    out = source.clone()
    px = out.px
    for i in range(0, len(px), 4):
        luma = (px[i] * 54 + px[i + 1] * 183 + px[i + 2] * 19) >> 8
        c = lut[luma]
        px[i], px[i + 1], px[i + 2] = c
    return out, {"engine": "procedural-standin", "ramp": [[c[0], c[1], c[2]] for c in ramp],
                 "note": "luminance->palette mapping; repaint on top for final art"}


def interpolate_frames(a, b, count=1, mode="dissolve"):
    """Candidate inbetweens between two frames (dissolve or centroid-shift).
    Always flagged for artifact review — occlusions and lines will not morph
    correctly without a real model."""
    _init()
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    p = P(__file__).resolve().parent / "tools-anim.py"
    spec = ilu.spec_from_file_location("studio.anim", p)
    anim = sys_.modules.get("studio.anim")
    if anim is None:
        anim = ilu.module_from_spec(spec)
        sys_.modules["studio.anim"] = anim
        spec.loader.exec_module(anim)
    out = []
    for i in range(1, count + 1):
        t = i / float(count + 1)
        out.append(anim.inbetween(a, b, t, mode=mode if mode != "auto" else "dissolve+shift"))
    return out, {"engine": "procedural-standin", "count": count, "mode": mode,
                 "warnings": ["review occlusion areas and line art for artifacts",
                              "reject frames with ghosting on fast motion"]}


def pose_guidance_overlay(size, skeleton, color=(80, 200, 255, 200)):
    """Render a stick-figure pose skeleton as an alignment reference image."""
    _init()
    w, h = size
    img = im.new_image(w, h, (0, 0, 0, 0))
    bones = skeleton.get("bones", [])
    joints = skeleton.get("joints", {})
    for b in bones:
        a, b2 = joints.get(b["from"]), joints.get(b["to"])
        if a and b2:
            im.draw_line(img, int(a[0] * w), int(a[1] * h), int(b2[0] * w), int(b2[1] * h),
                         color, 2)
    for name, j in joints.items():
        x, y = int(j[0] * w), int(j[1] * h)
        im._fill_circle_ish(img, x, y, 3.0, (255, 255, 255, 220))
    return img, {"engine": "procedural", "joints": list(joints.keys())}


def character_reference_sheet(reference, out_path=None, views=("front", "side", "back")):
    """Consistency pack from one reference: palette extraction, perceptual
    hash identity, and silhouette metrics (Character Consistency Operator,
    deterministic portion)."""
    _init()
    pal = im.dominant_colors(reference, n=8)
    rec = {"palette": pal,
           "ahash": im.ahash(reference), "dhash": im.dhash(reference),
           "silhouette": im.silhouette_metrics(reference),
           "size": [reference.w, reference.h],
           "views": list(views)}
    if out_path:
        core.save_json(out_path, rec)
    return rec


def identity_check(candidate, reference, palette=None, hash_tol=12):
    """Validate a candidate frame against a reference identity pack.
    ahash/dhash distances are 0..64; identical frames are ~0-6, unrelated
    frames commonly 18+. The band between is exactly what human review is
    for — this check flags, it does not decide."""
    _init()
    report = {"ahash_distance": im.hamming_hex(im.ahash(candidate), im.ahash(reference)),
              "dhash_distance": im.hamming_hex(im.dhash(candidate), im.dhash(reference)),
              "hash_tolerance": hash_tol}
    report["hash_ok"] = report["ahash_distance"] <= hash_tol
    if palette:
        dom = [c["rgb"] for c in im.dominant_colors(candidate, n=len(palette))]
        def near(c):
            return any(sum(abs(c[k] - d[k]) for k in range(3)) < 90 for d in palette)
        report["palette_match"] = round(
            sum(1 for c in dom if near(c)) / float(max(1, len(dom))), 3)
    sa, sb = im.silhouette_metrics(reference), im.silhouette_metrics(candidate)
    if sa["area"] and sb["area"]:
        report["area_ratio"] = round(sb["area"] / float(sa["area"]), 3)
    report["verdict"] = "consistent" if report["hash_ok"] else "review"
    return report


def motion_candidates(trajectory, frames=24, size=(320, 240), seed="motion"):
    """Procedural motion candidates: ghosted motion paths from a trajectory
    for study or as reference layers (Motion Generation stand-in)."""
    _init()
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    p = P(__file__).resolve().parent / "tools-anim.py"
    spec = ilu.spec_from_file_location("studio.anim", p)
    anim = sys_.modules.get("studio.anim")
    if anim is None:
        anim = ilu.module_from_spec(spec)
        sys_.modules["studio.anim"] = anim
        spec.loader.exec_module(anim)
    rng = core.RNG("motion:%s" % seed)
    out = []
    for v in range(3):
        pts = [[x + (rng.random() - 0.5) * 18 * v, y + (rng.random() - 0.5) * 18 * v]
               for x, y in trajectory]
        out.extend(anim.trail_frames(pts, frames, size=size,
                                     color=(120 + v * 40, 160, 255 - v * 60, 200)))
    return out, {"engine": "procedural-standin", "variants": 3}


def neural_lipsync(audio_events, fps=24, chart_path=None):
    """Local lip-sync: onset/energy events -> editable viseme track.
    Uses the audio analysis (no model required); marked approx for review."""
    _init()
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    p = P(__file__).resolve().parent / "tools-anim.py"
    spec = ilu.spec_from_file_location("studio.anim", p)
    anim = sys_.modules.get("studio.anim")
    if anim is None:
        anim = ilu.module_from_spec(spec)
        sys_.modules["studio.anim"] = anim
        spec.loader.exec_module(anim)
    return anim.lipsync_from_audio(audio_events, fps=fps, chart_path=chart_path)


# ---------------------------------------------------------------- triage ----

TRIAGE_CLASSES = ["inspiration", "reference", "editable-source", "temporary",
                  "approved-production"]


def triage(item, out_path=None):
    """Classify a generated output and apply the matching review gate.
    approval='approved' required to reach approved-production."""
    _init()
    require(item.get("class") in TRIAGE_CLASSES, "invalid_input",
            "class must be one of %s" % ", ".join(TRIAGE_CLASSES))
    rec = {"file": item.get("file", ""), "class": item["class"],
           "engine": item.get("engine", "unknown"),
           "seed": item.get("seed"),
           "recipe": item.get("recipe"),
           "approved": bool(item.get("approved", False)),
           "reviewed_by": item.get("reviewed_by", ""),
           "recorded": core.utc_now()}
    if rec["class"] == "approved-production" and not rec["approved"]:
        raise core.ApprovalRequired(
            "approve_generated_asset",
            {"file": rec["file"],
             "hint": "generated asset cannot be marked approved-production without an "
                     "explicit approval record (reviewed_by + approved=true)"})
    if out_path:
        core.save_json(out_path, rec)
    return rec
