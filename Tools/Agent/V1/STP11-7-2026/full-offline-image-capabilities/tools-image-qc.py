#!/usr/bin/env python3
"""Quality inspection, validation and comparison engine.

Role: everything that decides whether an image is a finished candidate or a
reject. Implements decode, dimension, aspect-ratio, file-size, empty/blank,
alpha, artifact, colour, text-region, bit-depth, metadata and duplicate
checks, plus perceptual fingerprints, a visual regression comparator and the
Markdown/JSON QC report builder.

Loaded by tools-image-capabilities.py through core.load_sibling().
"""

from __future__ import annotations

import importlib.util
import os
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


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------
def load_policy(cfg, policy_path=None, preset=None, overrides=None):
    path = policy_path or os.path.join(core.TOOL_DIR, core.DEFAULT_QC_POLICY_ASSET)
    raw = core.read_json(path)
    policy = {"defaults": dict(raw.get("defaults", {})), "thresholds": dict(raw.get("thresholds", {}))}
    presets = raw.get("presets", {})
    if preset:
        if preset not in presets:
            raise ToolError(
                "unknown QC preset %r (available: %s)"
                % (preset, ", ".join(sorted(presets)) or "none"),
                code="unknown-preset",
                exit_code=core.EXIT_USAGE,
            )
        for key, value in presets[preset].items():
            if key in raw.get("thresholds", {}):
                policy["thresholds"][key] = value
            else:
                policy["defaults"][key] = value
    policy["severity"] = dict(raw.get("severity", {}))
    policy["preset"] = preset
    policy["source"] = path
    for item in overrides or []:
        if "=" not in item:
            raise ToolError("--set expects key=value", code="usage", exit_code=core.EXIT_USAGE)
        key, _, value = item.partition("=")
        key = key.strip()
        if key.startswith("thresholds."):
            policy["thresholds"][key.split(".", 1)[1]] = core.coerce_value(value.strip())
        else:
            policy["defaults"][key] = core.coerce_value(value.strip())
    return policy


def severity_for(policy, check_id, default="warn"):
    return policy.get("severity", {}).get(check_id, default)


# --------------------------------------------------------------------------
# metadata inspection
# --------------------------------------------------------------------------
def inspect_image(path, cfg, with_stats=True, with_fingerprints=True):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ToolError("not a file: %s" % path, code="not-found")
    size = core.check_file_limit(path, cfg)
    sha256 = core.sha256_file(path)[0] if size <= (256 << 20) else None
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    info = {
        "path": path,
        "filename": os.path.basename(path),
        "extension": ext,
        "file_size": size,
        "file_size_human": core.human_bytes(size),
    }
    if sha256:
        info["sha256"] = sha256
    else:
        info["sha256"] = None
        info["sha256_skipped"] = "file above 256 MB quick-hash limit"
    try:
        img = core.load_image(path, cfg)
    except ToolError as exc:
        info["decode"] = {"ok": False, "backend": None, "error": str(exc), "code": exc.code}
        return info
    info["decode"] = {"ok": True, "backend": img.backend, "error": None}
    info.update(
        {
            "format": img.format,
            "width": img.width,
            "height": img.height,
            "megapixels": round(img.width * img.height / 1e6, 3),
            "mode": img.mode,
            "channels": img.channels,
            "has_alpha": img.has_alpha,
            "aspect_ratio": _ratio_label(img.width, img.height),
            "bit_depth": img.meta.get("bit_depth"),
            "has_icc": bool(img.meta.get("icc")),
            "has_exif": bool(img.meta.get("exif")),
            "dpi": img.meta.get("dpi"),
            "pixels_available": img.has_pixels,
            "truncated": img.truncated,
        }
    )
    if img.has_pixels and with_fingerprints:
        info["fingerprints"] = {"dhash": core.dhash(img), "ahash": core.ahash(img)}
    if img.has_pixels and with_stats:
        info["stats"] = core.analyze(img)
    elif not img.has_pixels:
        info["note"] = "pixel analysis unavailable: install Pillow for non-PNG formats"
    return info


def _ratio_label(w, h):
    if not w or not h:
        return "unknown"
    best = None
    best_err = None
    for label in ("1:1", "5:4", "4:3", "3:2", "16:9", "21:9", "2:3", "3:4", "4:5", "9:16"):
        a, b = label.split(":")
        target = float(a) / float(b)
        err = abs((w / float(h)) - target)
        if best_err is None or err < best_err:
            best, best_err = label, err
    if best_err is not None and best_err <= 0.02:
        return best
    return "%.4f:1" % (w / float(h))


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def _status_from(condition, severity):
    if condition:
        return "pass"
    return "fail" if severity == "error" else "warn"


def check_decode(ctx):
    img = ctx["img"]
    info = ctx["info"]
    if img is None:
        return {
            "id": "decode",
            "status": "fail",
            "severity": "error",
            "message": "decode failed: %s" % info.get("decode", {}).get("error", "unknown"),
            "metrics": {},
        }
    if img.truncated:
        return {
            "id": "decode",
            "status": "warn",
            "severity": "error",
            "message": "decoded with truncation; %d/%d rows recovered"
            % (img.meta.get("rows_decoded", img.height), img.height),
            "metrics": {"rows": img.meta.get("rows_decoded", img.height), "expected": img.height},
        }
    return {
        "id": "decode",
        "status": "pass",
        "severity": "error",
        "message": "decoded %dx%d %s via %s backend" % (img.width, img.height, img.mode, img.backend),
        "metrics": {"backend": img.backend, "pixels": img.has_pixels},
    }


def check_dimensions(ctx):
    d = ctx["policy"]["defaults"]
    img = ctx["img"]
    if img is None:
        return _skip("dimensions", "no decoded image")
    w, h = img.width, img.height
    problems = []
    if w < d["min_width"] or h < d["min_height"]:
        problems.append("below %dx%d minimum" % (d["min_width"], d["min_height"]))
    if w > d["max_width"] or h > d["max_height"]:
        problems.append("above %dx%d maximum" % (d["max_width"], d["max_height"]))
    mult = d.get("require_multiple_of")
    if mult and (w % mult or h % mult):
        problems.append("not a multiple of %d" % mult)
    return _verdict(
        "dimensions",
        not problems,
        "; ".join(problems) if problems else "%dx%d within limits" % (w, h),
        {"width": w, "height": h},
        ctx,
    )


def check_aspect_ratio(ctx):
    d = ctx["policy"]["defaults"]
    img = ctx["img"]
    if img is None:
        return _skip("aspect_ratio", "no decoded image")
    allowed = d.get("allowed_aspect_ratios") or []
    if not allowed:
        return _skip("aspect_ratio", "no ratio list configured")
    tol = float(d.get("aspect_tolerance", 0.02))
    actual = img.width / float(img.height)
    ok = False
    for label in allowed:
        if ":" not in label:
            continue
        a, b = label.split(":")
        try:
            target = float(a) / float(b)
        except ZeroDivisionError:
            continue
        if abs(actual - target) <= tol:
            ok = True
            break
    return _verdict(
        "aspect_ratio",
        ok,
        "ratio %.4f (%s)" % (actual, _ratio_label(img.width, img.height)),
        {"actual": round(actual, 4), "allowed": allowed, "tolerance": tol},
        ctx,
    )


def check_file_size(ctx):
    d = ctx["policy"]["defaults"]
    info = ctx["info"]
    size = info["file_size"]
    problems = []
    if size < d["min_file_bytes"]:
        problems.append("smaller than %s" % core.human_bytes(d["min_file_bytes"]))
    if size > d["max_file_bytes"]:
        problems.append("larger than %s" % core.human_bytes(d["max_file_bytes"]))
    bpm = d.get("max_bytes_per_megapixel")
    img = ctx["img"]
    if bpm and img:
        mp = img.width * img.height / 1e6
        if mp > 0 and size / mp > bpm:
            problems.append("bytes-per-megapixel above %s" % core.human_bytes(int(bpm)))
    return _verdict(
        "file_size",
        not problems,
        "; ".join(problems) if problems else "%s acceptable" % core.human_bytes(size),
        {"bytes": size, "bytes_per_megapixel": round(size / max(1e-6, (img.width * img.height / 1e6) if img else 1), 1)},
        ctx,
    )


def check_empty(ctx):
    th = ctx["policy"]["thresholds"]
    stats = ctx.get("stats")
    if not stats:
        return _skip("empty", "no pixel statistics")
    flat = stats["std_luma"] < float(th["flat_stddev_max"])
    few = stats["unique_colors_est"] < int(th["flat_unique_colors_min"])
    problems = []
    if flat:
        problems.append("luma stddev %.2f below %.2f" % (stats["std_luma"], th["flat_stddev_max"]))
    if few:
        problems.append("only ~%d distinct colours" % stats["unique_colors_est"])
    if stats["flat_block_ratio"] > float(th["flat_block_ratio_max"]):
        problems.append(
            "%.0f%% of %dpx blocks are flat (over-smoothed or empty)"
            % (stats["flat_block_ratio"] * 100, th["block_size"])
        )
    return _verdict(
        "empty",
        not (flat and few) and stats["flat_block_ratio"] <= float(th["flat_block_ratio_max"]),
        "; ".join(problems) if problems else "content present (stddev %.2f)" % stats["std_luma"],
        {k: stats[k] for k in ("std_luma", "unique_colors_est", "flat_block_ratio")},
        ctx,
    )


def check_alpha(ctx):
    d = ctx["policy"]["defaults"]
    img = ctx["img"]
    stats = ctx.get("stats")
    if img is None:
        return _skip("alpha", "no decoded image")
    expect = d.get("expect_alpha", "any")
    cov = (stats or {}).get("alpha_coverage", 0.0)
    metrics = {
        "mode": img.mode,
        "expect_alpha": expect,
        "alpha_coverage": cov,
        "semi_transparent_ratio": (stats or {}).get("semi_transparent_ratio", 0.0),
    }
    if not img.has_alpha:
        ok = expect in ("any", "absent")
        return _verdict("alpha", ok, "no alpha channel (expected %s)" % expect, metrics, ctx)
    if expect == "absent":
        return _verdict("alpha", False, "alpha channel present but policy expects none", metrics, ctx)
    lo = float(d.get("alpha_coverage_min", 0.0))
    hi = float(d.get("alpha_coverage_max", 1.0))
    ok = lo <= cov <= hi
    return _verdict(
        "alpha",
        ok,
        "alpha coverage %.3f outside [%.3f, %.3f]" % (cov, lo, hi) if not ok else "alpha coverage %.3f" % cov,
        metrics,
        ctx,
    )


def check_artifacts(ctx):
    th = ctx["policy"]["thresholds"]
    stats = ctx.get("stats")
    if not stats:
        return _skip("artifacts", "no pixel statistics")
    findings = []
    if stats["clipped_ratio"] > float(th["clipped_pixel_ratio_max"]):
        findings.append(
            "%.2f%% clipped highlights/shadows (limit %.2f%%)"
            % (stats["clipped_ratio"] * 100, float(th["clipped_pixel_ratio_max"]) * 100)
        )
    if stats["std_luma"] < 20.0 and stats["banding_step_ratio"] > float(th["banding_step_ratio_max"]):
        findings.append(
            "banding pattern: %.0f%% of steps are 1-2 levels in a flat image"
            % (stats["banding_step_ratio"] * 100)
        )
    if stats["std_luma"] > float(th["noise_stddev_max"]):
        findings.append("luma stddev %.1f above %.1f (high-frequency noise)" % (stats["std_luma"], th["noise_stddev_max"]))
    if stats["std_luma"] < float(th["noise_stddev_min"]):
        findings.append("luma stddev %.2f below %.2f (dead-flat output)" % (stats["std_luma"], th["noise_stddev_min"]))
    return _verdict(
        "artifacts",
        not findings,
        "; ".join(findings) if findings else "no clipping, banding or noise artifacts detected",
        {
            "clipped_ratio": stats["clipped_ratio"],
            "banding_step_ratio": stats["banding_step_ratio"],
            "std_luma": stats["std_luma"],
            "edge_density": stats["edge_density"],
        },
        ctx,
    )


def check_color(ctx):
    th = ctx["policy"]["thresholds"]
    d = ctx["policy"]["defaults"]
    stats = ctx.get("stats")
    if not stats:
        return _skip("color", "no pixel statistics")
    findings = []
    if stats["mean_saturation"] < float(th["gray_saturation_max"]) and stats["std_luma"] > 5:
        findings.append(
            "near-grayscale output (mean saturation %.1f); colour profile may be missing"
            % stats["mean_saturation"]
        )
    if d.get("require_icc") and not ctx["info"].get("has_icc"):
        findings.append("policy requires an embedded ICC profile and none was found")
    return _verdict(
        "color",
        not findings,
        "; ".join(findings) if findings else "mean saturation %.1f, mean luma %.1f"
        % (stats["mean_saturation"], stats["mean_luma"]),
        {"mean_saturation": stats["mean_saturation"], "mean_luma": stats["mean_luma"], "has_icc": ctx["info"].get("has_icc")},
        ctx,
    )


def check_text_region(ctx):
    th = ctx["policy"]["thresholds"]
    d = ctx["policy"]["defaults"]
    stats = ctx.get("stats")
    expect = d.get("expect_text", None)
    if expect is None:
        return _skip("text_region", "expect_text not set (heuristic disabled)")
    if not stats:
        return _skip("text_region", "no pixel statistics")
    density = stats["edge_density"]
    if expect:
        ok = float(th["edge_density_text_min"]) <= density <= float(th["edge_density_text_max"])
        msg = "edge density %.3f %s [%.3f, %.3f] for expected typography" % (
            density,
            "inside" if ok else "outside",
            th["edge_density_text_min"],
            th["edge_density_text_max"],
        )
    else:
        ok = density <= float(th["edge_density_photo_max"])
        msg = "edge density %.3f %s photo limit %.3f" % (
            density,
            "within" if ok else "above",
            th["edge_density_photo_max"],
        )
    return _verdict("text_region", ok, msg, {"edge_density": density, "expect_text": expect}, ctx)


def check_bit_depth(ctx):
    info = ctx["info"]
    img = ctx["img"]
    if img is None:
        return _skip("bit_depth", "no decoded image")
    depth = info.get("bit_depth")
    if depth is None:
        return _skip("bit_depth", "bit depth not reported by backend")
    ok = depth in (8, 16)
    return _verdict(
        "bit_depth",
        ok,
        "bit depth %s, mode %s" % (depth, img.mode),
        {"bit_depth": depth, "mode": img.mode, "channels": img.channels},
        ctx,
    )


def check_metadata(ctx):
    d = ctx["policy"]["defaults"]
    info = ctx["info"]
    findings = []
    if d.get("forbid_gps_exif") and info.get("has_exif"):
        findings.append(
            "EXIF block present; run `convert --strip-metadata` or `screen` before release if GPS data may be embedded"
        )
    if d.get("require_icc") and not info.get("has_icc"):
        findings.append("ICC profile required by policy but absent")
    return _verdict(
        "metadata",
        not findings,
        "; ".join(findings) if findings else "metadata policy satisfied",
        {"has_exif": info.get("has_exif"), "has_icc": info.get("has_icc"), "dpi": info.get("dpi")},
        ctx,
    )


def check_duplicate(ctx):
    th = ctx["policy"]["thresholds"]
    fp = (ctx["info"].get("fingerprints") or {}).get("dhash")
    registry = ctx.setdefault("registry", {})
    if not fp:
        return _skip("duplicate", "no fingerprint available")
    max_ham = int(th.get("deduplicate_hamming_max", 8))
    warn_ham = int(th.get("similarity_warn_hamming", 20))
    best = None
    for other_fp, other_path in registry.items():
        dist = core.hamming_hex(fp, other_fp)
        if dist is None:
            continue
        if best is None or dist < best[0]:
            best = (dist, other_path)
    registry[fp] = ctx["info"]["path"]
    if best and best[0] <= max_ham:
        return {
            "id": "duplicate",
            "status": "warn",
            "severity": severity_for(ctx["policy"], "duplicate"),
            "message": "near-duplicate of %s (dHash distance %d <= %d)" % (best[1], best[0], max_ham),
            "metrics": {"distance": best[0], "match": best[1], "dhash": fp},
        }
    if best and best[0] <= warn_ham:
        return {
            "id": "duplicate",
            "status": "warn",
            "severity": severity_for(ctx["policy"], "duplicate"),
            "message": "similar to %s (dHash distance %d)" % (best[1], best[0]),
            "metrics": {"distance": best[0], "match": best[1], "dhash": fp},
        }
    return {
        "id": "duplicate",
        "status": "pass",
        "severity": severity_for(ctx["policy"], "duplicate"),
        "message": "unique within this batch (dHash %s)" % fp,
        "metrics": {"dhash": fp},
    }


CHECKS = [
    check_decode,
    check_dimensions,
    check_aspect_ratio,
    check_file_size,
    check_empty,
    check_alpha,
    check_artifacts,
    check_color,
    check_text_region,
    check_bit_depth,
    check_metadata,
    check_duplicate,
]

CHECK_IDS = [fn.__name__.replace("check_", "") for fn in CHECKS]


def _skip(check_id, reason):
    return {"id": check_id, "status": "skip", "severity": "info", "message": reason, "metrics": {}}


def _verdict(check_id, ok, message, metrics, ctx):
    severity = severity_for(ctx["policy"], check_id)
    return {
        "id": check_id,
        "status": _status_from(ok, severity),
        "severity": severity,
        "message": message,
        "metrics": metrics or {},
    }


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def validate_images(paths, cfg, policy, checks=None, deadline=None, registry=None):
    selected = [c for c in CHECKS if checks is None or c.__name__.replace("check_", "") in checks]
    shared_registry = registry if registry is not None else {}
    results = []
    for path in paths:
        if deadline:
            deadline.check("validating %s" % path)
        entry = {"path": os.path.abspath(path), "checks": []}
        info = inspect_image(path, cfg)
        entry["image"] = {
            k: info.get(k)
            for k in ("width", "height", "mode", "format", "file_size", "has_alpha", "aspect_ratio", "backend")
            if k in info
        }
        entry["image"]["backend"] = (info.get("decode") or {}).get("backend")
        entry["fingerprints"] = info.get("fingerprints", {})
        try:
            img = core.load_image(path, cfg) if info.get("decode", {}).get("ok") else None
        except ToolError:
            img = None
        stats = info.get("stats")
        if img is not None and img.has_pixels and stats is None:
            stats = core.analyze(img)
        ctx = {
            "info": info,
            "img": img,
            "stats": stats,
            "policy": policy,
            "registry": shared_registry,
        }
        for fn in selected:
            try:
                entry["checks"].append(fn(ctx))
            except ToolError as exc:
                entry["checks"].append(
                    {"id": fn.__name__.replace("check_", ""), "status": "fail", "severity": "error",
                     "message": "check error: %s" % exc, "metrics": {}}
                )
        statuses = [c["status"] for c in entry["checks"]]
        if "fail" in statuses:
            entry["status"] = "fail"
        elif "warn" in statuses:
            entry["status"] = "warn"
        elif all(s == "skip" for s in statuses):
            entry["status"] = "pass"
        else:
            entry["status"] = "pass"
        results.append(entry)
    counts = {
        "checked": len(results),
        "passed": sum(1 for r in results if r["status"] == "pass"),
        "warned": sum(1 for r in results if r["status"] == "warn"),
        "failed": sum(1 for r in results if r["status"] == "fail"),
        "skipped": sum(1 for r in results if r["status"] == "skip"),
    }
    if counts["failed"]:
        verdict = "fail"
    elif counts["warned"]:
        verdict = "warn"
    else:
        verdict = "pass"
    counts["verdict"] = verdict
    return {
        "schema": "foic.qc-report/1",
        "generated_at": core.iso_now(),
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "policy": "%s%s" % (os.path.basename(policy["source"]), " (%s preset)" % policy["preset"] if policy["preset"] else ""),
        "backend": "pillow" if core.pillow_available() else "stdlib",
        "checks_run": [fn.__name__.replace("check_", "") for fn in selected],
        "summary": counts,
        "results": results,
    }


def report_to_markdown(report, cfg, template_path=None):
    template_path = template_path or os.path.join(core.TOOL_DIR, core.DEFAULT_REPORT_TEMPLATE_ASSET)
    with open(template_path, "r", encoding="utf-8") as fh:
        template = fh.read()
    blocks = []
    for entry in report["results"]:
        lines = ["### `%s`" % entry["path"], ""]
        lines.append("Status: **%s**" % entry["status"])
        lines.append("")
        lines.append("| Check | Status | Severity | Detail |")
        lines.append("| --- | --- | --- | --- |")
        for chk in entry["checks"]:
            detail = chk["message"].replace("|", "\\|")
            lines.append("| `%s` | %s | %s | %s |" % (chk["id"], chk["status"], chk["severity"], detail))
        lines.append("")
        fp = entry.get("fingerprints") or {}
        if fp:
            lines.append("Fingerprints: dHash `%s`, aHash `%s`" % (fp.get("dhash", "-"), fp.get("ahash", "-")))
            lines.append("")
        blocks.append("\n".join(lines))
    notes = []
    if report["summary"]["verdict"] == "fail":
        notes.append("- One or more images failed a mandatory check; do not release until resolved.")
    if not core.pillow_available():
        notes.append("- Pillow is not installed: non-PNG files were validated by metadata only.")
    notes.append("- Thresholds come from the policy file; tune with `--preset` or `--set`.")
    summary = report["summary"]
    text = template
    for key, value in (
        ("tool", report["tool"]),
        ("policy", report["policy"]),
        ("generated_at", report["generated_at"]),
        ("backend", report["backend"]),
        ("verdict", summary["verdict"].upper()),
        ("checked", str(summary["checked"])),
        ("passed", str(summary["passed"])),
        ("warned", str(summary["warned"])),
        ("failed", str(summary["failed"])),
        ("skipped", str(summary["skipped"])),
        ("results_section", "\n".join(blocks) or "_No images checked._"),
        ("notes_section", "\n".join(notes)),
        ("config_fingerprint", cfg.fingerprint()),
    ):
        text = text.replace("{%s}" % key, value)
    return text


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def compare_images(path_a, path_b, cfg, tolerance=0.0):
    a = core.load_image(path_a, cfg)
    b = core.load_image(path_b, cfg)
    out = {
        "schema": "foic.compare/1",
        "a": {"path": os.path.abspath(path_a), "width": a.width, "height": a.height, "mode": a.mode, "format": a.format},
        "b": {"path": os.path.abspath(path_b), "width": b.width, "height": b.height, "mode": b.mode, "format": b.format},
        "generated_at": core.iso_now(),
    }
    out["dimensions_match"] = (a.width, a.height) == (b.width, b.height)
    if a.has_pixels and b.has_pixels:
        out["dhash"] = {"a": core.dhash(a), "b": core.dhash(b)}
        out["ahash"] = {"a": core.ahash(a), "b": core.ahash(b)}
        out["dhash_distance"] = core.hamming_hex(out["dhash"]["a"], out["dhash"]["b"])
        out["ahash_distance"] = core.hamming_hex(out["ahash"]["a"], out["ahash"]["b"])
        if out["dimensions_match"]:
            ga, gb = core.to_gray(a), core.to_gray(b)
            diff = 0
            mad = 0
            n = len(ga)
            for i in range(0, n, max(1, n // 200000)):
                d = abs(ga[i] - gb[i])
                mad += d
                if d > 8:
                    diff += 1
            sampled = len(range(0, n, max(1, n // 200000)))
            out["mean_abs_diff"] = round(mad / float(sampled), 3)
            out["changed_pixel_ratio"] = round(diff / float(sampled), 5)
        else:
            out["mean_abs_diff"] = None
            out["changed_pixel_ratio"] = None
            out["note"] = "pixel diff skipped: dimensions differ"
    else:
        out["note"] = "pixel comparison unavailable for this format without Pillow"
        out["dhash_distance"] = None
    dist = out.get("dhash_distance")
    if dist is None:
        out["verdict"] = "unknown"
    elif dist == 0:
        out["verdict"] = "identical-structure"
    elif dist <= max(0, int(tolerance)):
        out["verdict"] = "within-tolerance"
    elif dist <= 12:
        out["verdict"] = "similar"
    else:
        out["verdict"] = "different"
    return out
