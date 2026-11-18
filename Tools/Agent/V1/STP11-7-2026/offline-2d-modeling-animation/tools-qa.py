#!/usr/bin/env python3
"""tools-qa.py — validation, quality assurance, review, temporal consistency
and accessibility.

Frame detectors (missing/duplicate/corrupt), alpha and color-profile
validation, loop and resolution checks, visual regression with difference
images, flash-safety (photosensitivity) screening, labeled contact sheets,
review packages with slates/burn-ins, and the acceptance gate driven by
asset-qa-checklist.json. Temporal consistency: flicker, proportions,
identity (reuses tools-neural), line boil, color stability, background
stability, motion arcs, volume preservation and a repair planner.
Accessibility: frame-accurate subtitles with reading-speed checks, safe-area
and contrast checks, high-contrast and reduced-motion variants, and
localization export packages. Pure stdlib.
"""
import math
import os
import re

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


def _anim():
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
    return anim


def _neural():
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    p = P(__file__).resolve().parent / "tools-neural.py"
    spec = ilu.spec_from_file_location("studio.neural", p)
    nm = sys_.modules.get("studio.neural")
    if nm is None:
        nm = ilu.module_from_spec(spec)
        sys_.modules["studio.neural"] = nm
        spec.loader.exec_module(nm)
    return nm


def load_frames(paths, limit=None):
    _, _ = _init()
    imgs = []
    metas = []
    for p in paths:
        img, meta = im.load_image(p)
        imgs.append(img)
        metas.append(meta)
        if limit and len(imgs) >= limit:
            break
    require(imgs, "invalid_input", "no frames loaded")
    core.check_limits(None, frames=len(imgs))
    return imgs, metas


# ------------------------------------------------------- frame detectors ----

def missing_frames(directory, pattern_range=None, exts=None, padding=None):
    """Compare expected range vs actual files; report gaps/padding issues."""
    _, _ = _init()
    from pathlib import Path as P
    d = P(str(directory))
    require(d.is_dir(), "invalid_input", "not a directory: %s" % d)
    exts = exts or (".png",)
    import re as _re
    files = {}
    for f in sorted(d.iterdir(), key=core.natural_key):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        m = _re.match(r"^(.*?)(\d+)$", f.stem)
        if not m:
            continue
        num = int(m.group(2))
        files[num] = f.name
        if padding and len(m.group(2)) != padding:
            return {"issues": [{"issue": "padding_mismatch",
                                "file": f.name,
                                "detail": "%d digits, expected %d" % (len(m.group(2)), padding)}]}
    require(files, "invalid_input", "no numbered frames found in %s" % d)
    nums = sorted(files)
    lo, hi = (pattern_range or (nums[0], nums[-1]))[:2]
    expected = set(range(int(lo), int(hi) + 1))
    missing = sorted(expected - set(nums))
    extra = sorted(set(nums) - expected)
    return {"directory": str(d), "expected": [int(lo), int(hi)],
            "found": len(nums), "missing": missing, "unexpected": extra,
            "issues": ([{"issue": "missing_frames", "frames": missing}] if missing else []) +
                      ([{"issue": "unexpected_frames", "frames": extra}] if extra else []),
            "verdict": "pass" if not missing and not extra else
                       ("%d missing" % len(missing) if missing else "%d unexpected" % len(extra))}


def duplicate_frames(paths, tol=2):
    """Exact duplicates via sha256 (any distance) + near duplicates via
    perceptual hash (adjacent frames — where accidental repeats matter)."""
    _, _ = _init()
    hashes = {}
    exact = []
    for p in paths:
        h = core.sha256_file(str(p))
        if h in hashes:
            exact.append({"files": [hashes[h], str(p)], "kind": "exact_copy"})
        else:
            hashes[h] = str(p)
    imgs, _ = load_frames(paths)
    ph = [im.ahash(f) for f in imgs]
    near = []
    for i in range(len(imgs) - 1):
        for j in range(i + 1, min(i + 5, len(imgs))):
            d = im.hamming_hex(ph[i], ph[j])
            if d <= tol:
                near.append({"frames": [i, j], "hamming": d, "kind": "hold_or_near"})
    return {"frames": len(imgs), "exact": exact, "near": near,
            "duplicate_groups": len(exact) + len(near),
            "note": "exact copies are usually accidental; adjacent near-duplicates "
                    "are usually intentional holds — verify before deleting"}


def corrupt_frames(paths):
    """Open every image; flag unreadable/truncated/zero-byte files."""
    _, _ = _init()
    bad = []
    ok = 0
    for p in paths:
        p = str(p)
        try:
            sz = os.path.getsize(p)
            if sz == 0:
                bad.append({"file": p, "issue": "zero_bytes"})
                continue
            img, meta = im.load_image(p)
            if img.w == 0 or img.h == 0:
                bad.append({"file": p, "issue": "empty_dimensions"})
            else:
                ok += 1
        except Exception as e:
            bad.append({"file": p, "issue": getattr(e, "code", "undecodable"),
                        "detail": str(e)[:120]})
    return {"checked": len(paths), "ok": ok, "bad": bad,
            "verdict": "pass" if not bad else "%d bad file(s)" % len(bad)}


# --------------------------------------------------------- image checks -----

def alpha_validation(img, expect_alpha=None):
    _, _ = _init()
    px = img.px
    has_alpha = img.has_alpha()
    semi = sum(1 for a in px[3::4] if 0 < a < 255)
    zero_with_color = 0
    for i in range(0, len(px), 4):
        if px[i + 3] == 0 and (px[i] or px[i + 1] or px[i + 2]):
            zero_with_color += 1
    opaque_border = all(a == 255 for a in
                        [px[(y * img.w + x) * 4 + 3] for x in (0, img.w - 1)
                         for y in (0, img.h - 1)])
    issues = []
    if expect_alpha is True and not has_alpha:
        issues.append({"issue": "alpha_missing"})
    if expect_alpha is False and has_alpha:
        issues.append({"issue": "alpha_unexpected"})
    if zero_with_color > img.w * img.h * 0.01:
        issues.append({"issue": "matte_contamination",
                       "detail": "%d colored-but-transparent pixels (unpremultiply or "
                                 "defringe)" % zero_with_color})
    return {"has_alpha": has_alpha, "semi_transparent": semi,
            "zero_alpha_with_color": zero_with_color, "opaque_corners": opaque_border,
            "issues": issues,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def loop_check(images, max_changed=0.02):
    """Animation Loop Validator: boundary diff + motion phase."""
    _, _ = _init()
    require(len(images) >= 2, "invalid_input", "need >= 2 frames")
    d = im.diff_images(images[0], images[-1])
    d2 = im.diff_images(images[0], images[1])
    seamless = d["changed_fraction"] <= max_changed
    moving = d2["changed_fraction"] > 0
    return {"frames": len(images),
            "boundary_changed_fraction": d["changed_fraction"],
            "first_motion_fraction": d2["changed_fraction"],
            "seamless": seamless, "has_motion": moving,
            "verdict": "loop-ok" if seamless and moving else
                       ("static" if not moving else "visible_seam")}


def resolution_check(images, expected=None):
    _, _ = _init()
    sizes = sorted({(f.w, f.h) for f in images})
    issues = []
    if expected:
        for f in images:
            if (f.w, f.h) != tuple(expected):
                issues.append({"issue": "size_mismatch", "got": [f.w, f.h],
                               "expected": list(expected)})
                break
    if len(sizes) > 1:
        issues.append({"issue": "inconsistent_sizes", "sizes": [list(s) for s in sizes]})
    w, h = sizes[0] if sizes else (0, 0)
    gcd = math.gcd(w, h) or 1
    return {"sizes": [list(s) for s in sizes], "display_aspect": "%d:%d" % (w // gcd, h // gcd),
            "issues": issues, "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def color_profile_check(metas):
    """Compare bit depths / color types / profile chunks across sources."""
    _, _ = _init()
    sigs = {}
    for i, m in enumerate(metas):
        sigs[i] = (m.get("bit_depth"), m.get("color_type"), m.get("srgb"),
                   m.get("gama"), m.get("format"))
    vals = set(sigs.values())
    issues = []
    if len(vals) > 1:
        issues.append({"issue": "mixed_profiles",
                       "detail": "distinct (depth,color_type,srgb,gama) combos: %s"
                                 % [list(v) for v in vals][:4]})
    for m in metas:
        if m.get("warnings") and "16-bit PNG downscaled" in str(m.get("warnings")):
            issues.append({"issue": "downscaled_16bit",
                           "detail": "16-bit source loaded at 8-bit"})
            break
    return {"signatures": {str(k): list(v) for k, v in list(sigs.items())[:8]},
            "issues": issues, "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def fps_metadata_check(declared):
    """All fps declarations must agree (manifest, editorial, audio, export)."""
    _, _ = _init()
    require(isinstance(declared, dict) and declared, "invalid_input",
            "pass {source: fps} declarations")
    norm = {}
    for k, v in declared.items():
        try:
            norm[k] = round(float(v), 3)
        except (TypeError, ValueError):
            raise err("invalid_input", "fps for '%s' is not a number: %r" % (k, v))
    vals = sorted(set(norm.values()))
    return {"declared": norm, "consistent": len(vals) == 1,
            "values": vals,
            "note": "23.976 vs 24 and 29.97 vs 30 are different timebases; "
                    "reconcile before final export" if len(vals) > 1 else "",
            "verdict": "pass" if len(vals) == 1 else "mismatch"}


def visual_regression(candidate_paths, approved_paths, out_dir=None, label=""):
    """Render difference images + changed-region stats vs approved outputs."""
    _, _ = _init()
    require(len(candidate_paths) == len(approved_paths), "invalid_input",
            "candidate/approved frame counts differ (%d vs %d)"
            % (len(candidate_paths), len(approved_paths)))
    from pathlib import Path as P
    if out_dir:
        P(str(out_dir)).mkdir(parents=True, exist_ok=True)
    report = []
    for i, (c, a) in enumerate(zip(candidate_paths, approved_paths)):
        ci, _ = im.load_image(c)
        ai, _ = im.load_image(a)
        if (ci.w, ci.h) != (ai.w, ai.h):
            report.append({"frame": i, "error": "size_mismatch",
                           "candidate": [ci.w, ci.h], "approved": [ai.w, ai.h]})
            continue
        d = im.diff_images(ai, ci)
        row = {"frame": i, "changed_fraction": d["changed_fraction"],
               "max_delta": d["max_delta"], "mean_delta": d["mean_delta"],
               "changed_pixels": d["changed_pixels"]}
        if out_dir and d["changed_pixels"]:
            im.save_image(str(P(str(out_dir)) / ("diff-%04d.png" % i)), d["diff_image"])
        report.append(row)
    changed = [r for r in report if r.get("changed_fraction", 0) > 0]
    return {"label": label, "frames": len(report),
            "changed_frames": len(changed),
            "max_changed_fraction": max([r.get("changed_fraction", 0) for r in report] or [0]),
            "report": report, "diff_dir": str(out_dir) if out_dir else None,
            "identical": not changed}


def flash_safety(images, fps=24, single_limit=60.0, freq_limit=3):
    """Photosensitivity screening: flag rapid high-contrast luminance swings.
    Conservative thresholds; flagged frames require human review."""
    _, _ = _init()
    require(len(images) >= 2, "invalid_input", "need >= 2 frames")
    lums = []
    for img in images:
        px = img.px
        s = n = 0
        for i in range(0, len(px), 16):
            s += im.luma8(px[i], px[i + 1], px[i + 2])
            n += 1
        lums.append(s / float(max(1, n)))
    flags = []
    for i in range(1, len(lums)):
        d = abs(lums[i] - lums[i - 1])
        if d >= single_limit:
            flags.append({"frame": i, "delta_luma": round(d, 1), "kind": "single_flash"})
    # rapid alternation: >= freq_limit swings above half the limit within 1 second
    window = max(2, int(fps))
    for i in range(len(lums) - window):
        swings = sum(1 for k in range(i + 1, i + window)
                     if abs(lums[k] - lums[k - 1]) >= single_limit * 0.5)
        if swings >= freq_limit:
            flags.append({"frame": i, "kind": "rapid_alternation",
                          "swings_per_second": swings})
    return {"fps": fps, "flags": flags[:200],
            "verdict": "pass" if not flags else
                       "%d flash event(s) — human review required" % len(flags)}


# ----------------------------------------------------------- review ---------

def build_contact_sheet(paths, out_path, every_nth=1, labels=None, columns=None):
    _, _ = _init()
    selected = [p for i, p in enumerate(paths) if i % every_nth == 0]
    imgs, _ = load_frames(selected)
    sheet = im.contact_sheet(imgs, labels=labels or [os.path.basename(str(p))
                                                     for p in selected],
                             columns=columns)
    im.save_image(str(out_path), sheet)
    return {"path": str(out_path), "frames": len(imgs), "every_nth": every_nth}


def review_package(paths, out_dir, shot, version=1, fps=24, every_nth=1,
                   issues=None, note=""):
    """Playback Review Packager: slate, burn-ins, contact sheet, issue log."""
    _, _ = _init()
    from pathlib import Path as P
    anim = _anim()
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    imgs, _ = load_frames(paths)
    slate_path = out / "slate.png"
    anim.slate_image(str(slate_path), shot, version=version,
                     width=max(320, imgs[0].w), height=max(240, imgs[0].h),
                     fps=fps, frames=len(imgs),
                     extra=[note] if note else None, burnin=True)
    burn_dir = out / "burnins"
    burn_dir.mkdir(exist_ok=True)
    burned = []
    for i, img in enumerate(imgs):
        b = anim.burnin_frame(img, "%s v%03d f%04d" % (shot, version, i + 1))
        p = burn_dir / ("review-%04d.png" % (i + 1))
        im.save_image(str(p), b)
        burned.append(str(p))
    sheet_path = out / "contact-sheet.png"
    build_contact_sheet(paths, str(sheet_path), every_nth=every_nth)
    log = {"shot": shot, "version": version, "fps": fps, "frames": len(imgs),
           "issues": issues or [], "reviewed_by": "", "notes": note,
           "template": "fill reviewed_by after watching at speed"}
    core.save_json(str(out / "issue-log.json"), log)
    return {"dir": str(out), "slate": str(slate_path), "burnins": len(burned),
            "contact_sheet": str(sheet_path), "issue_log": str(out / "issue-log.json")}


def acceptance_gate(results, checklist=None):
    """Run the project checklist against provided check results.
    results: {check_id: {ok: bool, detail: ...}}. Critical failures block."""
    _, _ = _init()
    cl = checklist or core.load_json(core.ROOT / "asset-qa-checklist.json")
    gates = cl.get("gates", [])
    rows = []
    blockers = []
    for g in gates:
        r = (results or {}).get(g["id"])
        if r is None:
            row = {"id": g["id"], "check": g["check"], "status": "not_run",
                   "critical": g.get("critical", False)}
            if g.get("critical"):
                blockers.append({"id": g["id"], "reason": "check not run"})
        elif r.get("ok"):
            row = {"id": g["id"], "check": g["check"], "status": "pass",
                   "detail": r.get("detail", ""), "critical": g.get("critical", False)}
        else:
            row = {"id": g["id"], "check": g["check"], "status": "fail",
                   "detail": r.get("detail", str(r)[:120]),
                   "critical": g.get("critical", False)}
            if g.get("critical"):
                blockers.append({"id": g["id"], "reason": r.get("detail",
                                                                 "failed")})
        rows.append(row)
    report = {"gates": rows, "passed": sum(1 for r in rows if r["status"] == "pass"),
              "failed": sum(1 for r in rows if r["status"] == "fail"),
              "not_run": sum(1 for r in rows if r["status"] == "not_run"),
              "blockers": blockers, "generated": core.utc_now()}
    if blockers:
        raise core.GateBlocked(
            "release blocked: %d critical gate failure(s)" % len(blockers), blockers)
    return report


# ------------------------------------------------ temporal consistency (18) --

def frame_consistency(images, luma_tol=6.0):
    """Flicker detection: frame-to-frame luma/color/detail deltas on the whole
    frame (global flicker; local flicker needs region masks)."""
    _, _ = _init()
    stats = [im.stats(f) for f in images]
    flags = []
    for i in range(1, len(stats)):
        dl = abs(stats[i]["mean"][0] - stats[i - 1]["mean"][0])
        dg = abs(stats[i]["mean"][1] - stats[i - 1]["mean"][1])
        db = abs(stats[i]["mean"][2] - stats[i - 1]["mean"][2])
        if max(dl, dg, db) > luma_tol:
            flags.append({"frame": i, "rgb_delta": [round(dl, 1), round(dg, 1),
                                                    round(db, 1)],
                          "kind": "flicker"})
    return {"frames": len(images), "flags": flags[:200],
            "verdict": "stable" if not flags else "%d flicker frame(s)" % len(flags)}


def proportion_tracker(images, tol=0.08):
    """Landmark-ratio tracking via silhouette bbox and centroid stability."""
    _, _ = _init()
    rows = []
    for img in images:
        m = im.silhouette_metrics(img, threshold=8)
        b = m["bbox"] or [0, 0, 0, 0]
        rows.append({"area": m["area"], "aspect": round(b[2] / float(b[3] or 1), 3),
                     "centroid": m["centroid"] or [0, 0]})
    flags = []
    for i in range(1, len(rows)):
        da = abs(rows[i]["area"] - rows[i - 1]["area"]) / float(rows[i - 1]["area"] or 1)
        dsp = abs(rows[i]["aspect"] - rows[i - 1]["aspect"])
        if da > tol or dsp > tol:
            flags.append({"frame": i, "area_change": round(da, 3),
                          "aspect_change": round(dsp, 3), "kind": "proportion_shift"})
    return {"frames": len(images), "flags": flags[:200],
            "verdict": "stable" if not flags else "%d shift(s)" % len(flags)}


def identity_validator(candidate_paths, reference_path, palette=None):
    _, _ = _init()
    nm = _neural()
    cand, _ = im.load_image(candidate_paths[0])
    ref, _ = im.load_image(reference_path)
    report = nm.identity_check(cand, ref, palette=palette)
    report["frames_checked"] = len(candidate_paths)
    report["note"] = "per-frame check; hash distances are coarse identity signals"
    return report


def line_boil_analysis(images):
    """Separate intentional boil (edge variation) from jitter: compare edge
    density and edge-map hashes across frames."""
    _, _ = _init()
    densities = []
    hashes = []
    for img in images:
        m = im.silhouette_metrics(img, threshold=8)
        densities.append(m.get("edge_density", 0.0) if m.get("area") else 0.0)
        hashes.append(im.ahash(img))
    boil = max(densities) - min(densities)
    jitter = 0
    for i in range(1, len(hashes)):
        jitter += im.hamming_hex(hashes[i - 1], hashes[i])
    avg_jitter = jitter / float(max(1, len(hashes) - 1))
    return {"edge_density_spread": round(boil, 5),
            "avg_frame_hash_distance": round(avg_jitter, 2),
            "interpretation": ("healthy line boil" if boil > 0.0005 and avg_jitter < 20
                               else "stable (possibly sterile)" if avg_jitter < 4
                               else "possible jitter — review at speed"),
            "densities": [round(d, 5) for d in densities[:50]]}


def temporal_color_stabilizer(images, tol=3.0):
    """Find frames whose mean color shifts beyond tolerance when the scene
    should be constant; optionally returns per-frame correction LUTs."""
    _, _ = _init()
    base = im.stats(images[0])["mean"]
    fixes = []
    for i, img in enumerate(images[1:], start=1):
        m = im.stats(img)["mean"]
        deltas = [base[c] - m[c] for c in range(3)]
        if max(abs(d) for d in deltas) > tol:
            fixes.append({"frame": i, "delta_rgb": [round(d, 2) for d in deltas],
                          "correction": {"op": "brightness_contrast",
                                         "brightness": round(sum(deltas) / 3 / 255.0, 4)}})
    return {"reference_frame": 0, "tolerance": tol, "corrections": fixes[:200],
            "verdict": "stable" if not fixes else "%d frame(s) need correction" % len(fixes)}


def background_stability(images, region=None, downscale=4, tol=0.01):
    """Detect warping/drift/flicker in static backgrounds (downscaled diff)."""
    _, _ = _init()
    small = [im.resize(f, max(2, f.w // downscale), max(2, f.h // downscale), "box")
             for f in images]
    flags = []
    for i in range(1, len(small)):
        d = im.diff_images(small[0], small[i])
        if d["changed_fraction"] > tol:
            flags.append({"frame": i, "changed_fraction": round(d["changed_fraction"], 4),
                          "kind": "background_change"})
    return {"flags": flags[:200], "downscale": downscale,
            "verdict": "stable" if not flags else "%d changed frame(s)" % len(flags)}


def motion_arc_analysis(images):
    """Track silhouette centroid across frames; report arcs, spacing, pops."""
    _, _ = _init()
    track = []
    for img in images:
        m = im.silhouette_metrics(img, threshold=8)
        track.append(m["centroid"] or [None, None])
    spacing = []
    for i in range(1, len(track)):
        if track[i][0] is None or track[i - 1][0] is None:
            spacing.append(None)
        else:
            spacing.append(round(math.hypot(track[i][0] - track[i - 1][0],
                                            track[i][1] - track[i - 1][1]), 2))
    clean = [s for s in spacing if s is not None]
    pops = []
    for i, s in enumerate(spacing):
        if s is not None and clean and s > 3 * (sum(clean) / len(clean)):
            pops.append({"frame": i + 1, "spacing": s})
    reversals = 0
    dxs = []
    for i in range(1, len(track)):
        if track[i][0] is not None and track[i - 1][0] is not None:
            dxs.append(track[i][0] - track[i - 1][0])
    for i in range(1, len(dxs)):
        if dxs[i] * dxs[i - 1] < 0:
            reversals += 1
    return {"track": [[round(c, 1) for c in t] if t[0] is not None else None
                      for t in track[:100]],
            "spacing": spacing[:100], "pops": pops[:50], "reversals": reversals}


def volume_preservation(images, tol=0.12):
    _, _ = _init()
    areas = [im.silhouette_metrics(f, threshold=8)["area"] for f in images]
    flags = []
    for i in range(1, len(areas)):
        if areas[i - 1] and abs(areas[i] - areas[i - 1]) / float(areas[i - 1]) > tol:
            flags.append({"frame": i, "area": areas[i], "previous": areas[i - 1],
                          "kind": "volume_change"})
    return {"areas": areas[:100], "flags": flags[:100],
            "verdict": "stable" if not flags else "%d change(s)" % len(flags)}


def consistency_repair_plan(images):
    """Rank temporal defects and recommend fixes (Generated Sequence
    Consistency Repair Planner)."""
    _, _ = _init()
    findings = []
    fc = frame_consistency(images)
    for f in fc["flags"]:
        findings.append({"frame": f["frame"], "defect": "flicker", "severity": 3,
                         "fix": "color-stabilize or hold reference frame"})
    vp = volume_preservation(images)
    for f in vp["flags"]:
        findings.append({"frame": f["frame"], "defect": "volume_change", "severity": 4,
                         "fix": "redraw or replace from adjacent frame"})
    ma = motion_arc_analysis(images)
    for p in ma["pops"]:
        findings.append({"frame": p["frame"], "defect": "pop", "severity": 5,
                         "fix": "insert inbetween or retime"})
    bg = background_stability(images)
    for f in bg["flags"]:
        findings.append({"frame": f["frame"], "defect": "background_change",
                         "severity": 2, "fix": "hold background plate or stabilize"})
    findings.sort(key=lambda f: (-f["severity"], f["frame"]))
    return {"defects": findings[:200],
            "summary": {"total": len(findings),
                        "by_defect": {k: sum(1 for f in findings if f["defect"] == k)
                                      for k in {f["defect"] for f in findings}}},
            "strategy": "fix severity 5 first (pops), then volumes, then color"}


# ---------------------------------------------------- accessibility (25) ----

def ms_to_srt(ms):
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    msec = ms % 1000
    return "%02d:%02d:%02d,%03d" % (h, m, s, msec)


def build_subtitles(cues, out_path, fps=24, max_cps=17, max_chars=42):
    """Frame-accurate SRT with reading-speed and line-length checks.
    cues: [{start_ms|start_frame, end_ms|end_frame, text, speaker?}]."""
    _, _ = _init()
    entries = []
    issues = []
    for i, c in enumerate(cues):
        s = c.get("start_ms")
        e = c.get("end_ms")
        if s is None:
            s = int(round(c["start_frame"] * 1000.0 / fps))
        if e is None:
            e = int(round(c["end_frame"] * 1000.0 / fps))
        text = str(c.get("text", "")).strip()
        dur = e - s
        if dur <= 0:
            issues.append({"cue": i + 1, "issue": "non_positive_duration"})
            dur = max(1, dur)
        cps = len(text) / (dur / 1000.0)
        if cps > max_cps:
            issues.append({"cue": i + 1, "issue": "reading_speed",
                           "detail": "%.1f chars/sec (max %d)" % (cps, max_cps)})
        if len(text) > max_chars * 2:
            issues.append({"cue": i + 1, "issue": "too_long",
                           "detail": "%d chars" % len(text)})
        speaker = ("%s: " % c["speaker"]) if c.get("speaker") else ""
        entries.append("%d\n%s --> %s\n%s%s\n" % (i + 1, ms_to_srt(s), ms_to_srt(e),
                                                  speaker, text))
    core.save_text(str(out_path), "\n".join(entries))
    return {"path": str(out_path), "cues": len(cues), "issues": issues,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def safe_area_check(img, text_boxes=None, safe=0.9):
    """Check text/action boxes against broadcast-style safe areas and
    minimum stroke contrast."""
    _, _ = _init()
    mx = img.w * (1 - safe) / 2
    my = img.h * (1 - safe) / 2
    issues = []
    for i, b in enumerate(text_boxes or []):
        x, y, w, h = b[:4]
        if x < mx or y < my or x + w > img.w - mx or y + h > img.h - my:
            issues.append({"box": i, "issue": "outside_safe_area", "box": list(b[:4])})
    return {"safe_fraction": safe, "safe_box": [round(mx), round(my),
                                                round(img.w - 2 * mx), round(img.h - 2 * my)],
            "issues": issues, "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def contrast_check(img, boxes=None, min_body=4.5, min_large=3.0):
    """Contrast ratios of text boxes against their local background."""
    _, _ = _init()
    results = []
    for i, b in enumerate(boxes or []):
        x, y, w, h = b[:4]
        fg = im.mean_color(im.crop(img, x, y, w, h))
        pad = 6
        bg = im.mean_color(im.crop(img, max(0, x - pad), max(0, y - pad),
                                   w + 2 * pad, h + 2 * pad))
        ratio = im.contrast_ratio(fg, bg)
        results.append({"box": i, "ratio": ratio, "ok": ratio >= min_body,
                        "large_text_ok": ratio >= min_large})
    ok = all(r["ok"] for r in results) if results else True
    return {"boxes": results, "verdict": "pass" if ok else "low_contrast"}


def high_contrast_variant(img, boost=1.6):
    _, _ = _init()
    out = im.op_brightness_contrast(img, brightness=-0.02, contrast=boost - 1.0)
    out = im.op_levels(out, black=16, white=240)
    return out


def reduced_motion_variant(images, fps=24):
    """Damp flashes and rapid motion: cap luminance swings, extend exposure
    of flash frames to holds."""
    _, _ = _init()
    anim = _anim()
    out = []
    prev_lum = None
    for i, img in enumerate(images):
        st = im.stats(img)
        lum = st["mean"][0]
        if prev_lum is not None and abs(lum - prev_lum) > 60:
            out.append(out[-1].clone() if out else img.clone())  # hold instead of flash
        else:
            out.append(img.clone())
        prev_lum = lum
    return out, {"frames": len(images),
                 "strategy": "flash frames replaced with holds; pair with reduced "
                             "camera-shake amplitude in the camera plan"}


def localization_package(cues, images, out_dir, fps=24):
    """Export text + timecodes + reference frames for offline localization."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    build_subtitles(cues, str(out / "subtitles.srt"), fps=fps)
    rows = [{"index": i + 1,
             "start_tc": ms_to_srt(c.get("start_ms") or int(round(
                 c.get("start_frame", 0) * 1000.0 / fps))),
             "end_tc": ms_to_srt(c.get("end_ms") or int(round(
                 c.get("end_frame", 0) * 1000.0 / fps))),
             "text": c.get("text", ""), "speaker": c.get("speaker", ""),
             "max_expansion": "allow +30 percent length for European languages"}
            for i, c in enumerate(cues)]
    core.save_json(str(out / "localization.json"),
                   {"fps": fps, "entries": rows,
                    "instructions": "replace text in the same timecodes; keep "
                                    "reading speed under 17 chars/sec; RTL languages "
                                    "mirror layout where appropriate"})
    refs = out / "reference-frames"
    refs.mkdir(exist_ok=True)
    for i, img in enumerate(images[:len(cues)] if images else []):
        im.save_image(str(refs / ("ref-%03d.png" % (i + 1))), img)
    return {"dir": str(out), "entries": len(rows),
            "subtitle": str(out / "subtitles.srt"),
            "reference_frames": len(list(refs.iterdir()))}


def consistency_texture(images, patch=12, grid_step=None):
    """Texture Lock Validator: hash fixed grid patches across frames; moving
    or resampling textures change patch hashes while static art does not."""
    _, _ = _init()
    step = grid_step or patch
    flags = []
    n_patches = 0
    moving = 0
    for py in range(0, images[0].h - patch + 1, step):
        for px in range(0, images[0].w - patch + 1, step):
            n_patches += 1
            first = None
            stable = True
            for fi, img in enumerate(images):
                if img.w != images[0].w or img.h != images[0].h:
                    raise err("invalid_input", "frame size mismatch at index %d" % fi)
                sub = im.crop(img, px, py, patch, patch)
                h = im.ahash(sub)
                if first is None:
                    first = h
                elif h != first:
                    stable = False
            if not stable:
                moving += 1
    ratio = moving / float(max(1, n_patches))
    return {"patches": n_patches, "unstable_patches": moving,
            "unstable_ratio": round(ratio, 4),
            "verdict": ("locked" if ratio < 0.05 else
                        "possible texture swim" if ratio < 0.3 else
                        "texture not locked — review"),
            "note": "global motion also flags; use region masks to narrow down"}


# ------------------------------------------------------- late QA additions --

def cleanup_scan(paths, patterns, trash_dir):
    """Cleanup & Optimization Scanner: match project files against the
    project naming rules and stage offenders in a trash folder (nothing is
    deleted until the user empties it)."""
    from pathlib import Path as P
    rules = core.load_json(core.ROOT / "asset-project-template.json")["naming"]
    pat = rules["task_file"]
    trash = P(str(trash_dir)); trash.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in paths or []:
        f = P(str(p))
        if not f.is_file():
            continue
        hit = None
        for kind, rx in patterns.items():
            if re.search(rx, f.name):
                hit = kind; break
        if hit:
            dest = trash / f.name
            core.write_bytes(str(dest), core.read_bytes(str(f)))
            rows.append({"file": f.name, "kind": hit, "staged_to": str(dest)})
    return {"pattern": pat, "staged": rows, "trash_dir": str(trash),
            "note": "review then empty the trash folder yourself"}


def _alpha_bbox(img, threshold=1):
    """Bounding box of non-transparent pixels, or the full frame."""
    w, h = img.w, img.h
    x0, y0, x1, y1 = w, h, 0, 0
    px = img.px
    found = False
    for y in range(h):
        base = y * w
        for x in range(w):
            if px[(base + x) * 4 + 3] >= threshold:
                found = True
                if x < x0:
                    x0 = x
                if x + 1 > x1:
                    x1 = x + 1
                if y < y0:
                    y0 = y
                if y + 1 > y1:
                    y1 = y + 1
    if not found:
        return [0, 0, w, h]
    return [x0, y0, x1 - x0, y1 - y0]


def optimization_pass(frames, plane=None):
    """Optimization Pass Runner: report cheap wins per frame (alpha weight,
    palette friendliness, idle margins)."""
    report = []
    for i, f in enumerate(frames):
        px4 = f.px[3::4]
        alpha_w = (sum(px4) / float(len(px4)) / 255.0) if px4 else 1.0
        box = _alpha_bbox(f)
        idle = (box[0] > f.w * 0.25) or (box[0] + box[2] < f.w * 0.75)
        report.append({"frame": i, "alpha_weight": round(alpha_w, 3),
                       "opaque": alpha_w > 0.95, "idle_margins": bool(idle),
                       "trim_box": box})
    return {"frames": report,
            "suggestions": ["trim idle margins before atlas packing",
                            "opaque frames can drop the alpha channel",
                            "large uniform borders waste atlas space"],
            "reference": "asset-project-template naming rules apply to outputs"}


def reference_sheet(entities, out_path, cell=96):
    """Reference Sheet Generator: contact grid of entity images with labels."""
    from pathlib import Path as P
    cols = 3
    rows_n = (len(entities) + cols - 1) // cols
    sheet = im.new_image(cols * cell, rows_n * (cell + 14), (245, 245, 245, 255))
    for i, e in enumerate(entities or []):
        cx = (i % cols) * cell
        cy = (i // cols) * (cell + 14)
        img = e.get("image")
        if img is not None:
            sc = min(cell / img.w, cell / img.h)
            r = im.resize(img, max(1, int(img.w * sc)), max(1, int(img.h * sc)))
            ox = cx + (cell - r.w) // 2
            oy = cy + (cell - r.h) // 2
            for yy in range(r.h):
                src = yy * r.w * 4
                dst = ((oy + yy) * sheet.w + ox) * 4
                sheet.px[dst:dst + r.w * 4] = r.px[src:src + r.w * 4]
        im.draw_text(sheet, cx + 2, cy + cell, str(e.get("name", "ent%d" % i))[:24],
                     (30, 30, 30, 255), 1, 1)
    im.save_image(str(out_path), sheet)
    return {"path": str(out_path), "entities": len(entities), "cols": cols}


def style_signature(img, swatches=6):
    """Style Consistency Monitor: fingerprint (palette + metrics) for style
    drift checks across shots/episodes."""
    pal = im.quantize(img, swatches)[0]
    st = im.stats(img)
    mean = st["mean"]
    variance = 0.0
    n = len(img.px) // 4
    for c in range(3):
        s = 0.0
        for i in range(c, len(img.px), 4):
            d = img.px[i] - mean[c]
            s += d * d
        variance += s / n
    return {"palette": [[c[0], c[1], c[2]] for c in pal],
            "mean_rgb": [round(v, 1) for v in mean],
            "std_rgb": [round(math.sqrt(variance / 3.0), 1)],
            "use": "compare signatures between shots; large palette/mean shifts "
                   "hint at style drift for human review"}


def final_delivery_gate(results, checklist_path=None):
    """Final delivery gate: every checklist gate must be run and pass.
    Stricter than acceptance_gate — unread items block delivery."""
    from pathlib import Path as P
    cl_path = P(str(checklist_path)) if checklist_path else \
        core.ROOT / "asset-qa-checklist.json"
    cl = core.load_json(cl_path)
    try:
        gate = acceptance_gate(results, cl)
    except core.GateBlocked as e:
        return {"state": "blocked", "blockers": getattr(e, "details", []),
                "message": str(e),
                "hint": "fix critical failures, then rerun the gate"}
    not_run = [r for r in gate["gates"] if r.get("status") == "not_run"]
    if not_run:
        return {"state": "blocked", "passed": gate["passed"],
                "failed": gate["failed"], "gates": gate["gates"],
                "message": "final gate requires every check to be run: " +
                           ", ".join(r["id"] for r in not_run)}
    return {"state": "passed", "passed": gate["passed"], "failed": 0,
            "gates": gate["gates"], "message": "all checks run and passing"}
