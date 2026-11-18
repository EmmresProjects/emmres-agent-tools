"""pixel-art-media: QA, validation, restoration, critique, auditing.

Grid-integrity and palette compliance checks, animation playback/flash QA,
atlas metadata validation, visual regression diffs, damaged-art restoration,
measurement-based critique, provenance + secret scanning + completeness
audits, license records, and reopen tests for final artifacts.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

import importlib.util
import sys


def I():
    m = sys.modules.get("tools_imaging")
    if m is None:
        p = Path(__file__).resolve().parent / "tools-imaging.py"
        spec = importlib.util.spec_from_file_location("tools_imaging", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["tools_imaging"] = m
        spec.loader.exec_module(m)
    return m


def S():
    return I().sibling("sheets")


def check(name, ok, detail=""):
    return {"check": name, "pass": bool(ok), "detail": detail}


# ------------------------------------------------------------- image checks -

def flash_analysis(frames, durations):
    """WCAG-style general flash heuristic on mean relative luminance."""
    L = I()
    lums = []
    for f in frames:
        small = f.convert("RGBA").resize((16, 16), Image.Resampling.BOX)
        px = list(small.getdata())
        opaque = [p for p in px if p[3] > 128] or px
        lums.append(sum(L.luminance(p) for p in opaque) / len(opaque))
    t = 0.0
    times = []
    for d in durations:
        times.append(t)
        t += max(1, d) / 1000.0
    flashes = 0
    pairs = []
    for i in range(1, len(lums)):
        dl = abs(lums[i] - lums[i - 1])
        if dl > 0.1:
            pairs.append((times[i], dl))
    # count direction reversals within any 1s window
    risk = False
    for i in range(len(pairs)):
        window = [p for p in pairs if 0 <= p[0] - pairs[i][0] < 1.0]
        if len(window) >= 6:  # ~3 full flash cycles per second
            risk = True
    return {"mean_luminance_range": [round(min(lums), 3), round(max(lums), 3)] if lums else [0, 0],
            "large_luminance_jumps": len(pairs), "flash_risk": risk}


def animation_checks(frames, durations, cfg):
    L = I()
    checks = []
    sizes = {f.size for f in frames}
    checks.append(check("frame-size-consistency", len(sizes) == 1, str(sorted(sizes))))
    checks.append(check("frame-count", 1 <= len(frames) <= cfg["limits"]["max_frames"],
                        f"{len(frames)} frames"))
    bad_d = [d for d in durations if d <= 0]
    checks.append(check("frame-durations", not bad_d, f"{len(bad_d)} non-positive durations"))
    if len(frames) > 1 and frames[0].size == frames[-1].size:
        a, b = frames[0].resize((32, 32), Image.Resampling.BOX), frames[-1].resize((32, 32), Image.Resampling.BOX)
        diff = sum(1 for pa, pb in zip(a.getdata(), b.getdata())
                   if max(abs(pa[i] - pb[i]) for i in range(4)) > 24)
        closure = 1 - diff / (32 * 32)
        checks.append(check("loop-closure(first~last)", closure > 0.75,
                            f"similarity {closure:.2f} (informational for non-loop anims)"))
        dup = sum(1 for i in range(1, len(frames))
                  if frames[i].tobytes() == frames[i - 1].tobytes())
        checks.append(check("duplicate-consecutive-frames", dup == 0, f"{dup} duplicates"))
    fa = flash_analysis(frames, durations)
    checks.append(check("flash-risk(WCAG-style)", not fa["flash_risk"], json.dumps(fa)))
    return checks, fa


def atlas_checks(meta, sheet_size=None):
    checks = []
    frames = meta.get("frames")
    if frames is None:
        return [check("atlas-structure", False, "missing frames")]
    if isinstance(frames, dict):  # json-hash
        items = []
        for name, f in frames.items():
            fr = f.get("frame", {})
            items.append({"name": name, "x": fr.get("x"), "y": fr.get("y"),
                          "w": fr.get("w"), "h": fr.get("h"),
                          "pivot": f.get("pivot")})
        size = meta.get("meta", {}).get("size", {})
        sheet_size = sheet_size or (size.get("w"), size.get("h"))
    else:  # json-simple / json-array
        items = []
        for f in frames:
            fr = f.get("frame", f)
            items.append({"name": f.get("name", f.get("filename", "?")),
                          "x": fr.get("x"), "y": fr.get("y"), "w": fr.get("w"), "h": fr.get("h"),
                          "pivot": f.get("pivot")})
        sz = meta.get("image_size")
        if sz:
            sheet_size = sheet_size or tuple(sz)
    checks.append(check("atlas-nonempty", len(items) > 0, f"{len(items)} frames"))
    names = [it["name"] for it in items]
    checks.append(check("atlas-unique-names", len(names) == len(set(names))))
    oob = []
    rects = []
    for it in items:
        x, y, w, h = it["x"], it["y"], it["w"], it["h"]
        if None in (x, y, w, h) or w <= 0 or h <= 0 or x < 0 or y < 0:
            oob.append(it["name"]); continue
        if sheet_size and (x + w > sheet_size[0] or y + h > sheet_size[1]):
            oob.append(it["name"])
        rects.append((x, y, x + w, y + h, it["name"]))
    checks.append(check("atlas-bounds", not oob, f"out-of-bounds: {oob[:5]}"))
    overlaps = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                overlaps.append((a[4], b[4]))
    checks.append(check("atlas-no-overlap", not overlaps, f"overlaps: {overlaps[:5]}"))
    badp = [it["name"] for it in items if it.get("pivot") and
            not (0 <= it["pivot"][0] <= 1 and 0 <= it["pivot"][1] <= 1)]
    checks.append(check("atlas-pivots", not badp, f"bad pivots: {badp[:5]}"))
    return checks


def cmd_validate(a, ctx):
    L = I()
    p = Path(a.input)
    if not p.exists():
        raise L.ToolError("input-not-found", f"Input not found: {a.input}")
    checks = []
    report = {"input": a.input}
    if p.suffix.lower() == ".json":
        meta = L.read_json(a.input)
        sheet_size = None
        if a.sheet:
            img = L.load_image(a.sheet, ctx.cfg)
            sheet_size = img.size
        checks = atlas_checks(meta, sheet_size)
        report["kind"] = "atlas"
    else:
        frames, durations, info = S().decode_animation(a.input, ctx.cfg)
        img = frames[0]
        report["kind"] = "animation" if len(frames) > 1 else "image"
        report["size"] = list(img.size)
        report["format"] = info["format"]
        report["frames"] = len(frames)
        if a.width:
            checks.append(check("width", img.width == a.width, f"{img.width} vs required {a.width}"))
        if a.height:
            checks.append(check("height", img.height == a.height, f"{img.height} vs required {a.height}"))
        if a.multiple_of:
            m = a.multiple_of
            checks.append(check("dimensions-multiple", img.width % m == 0 and img.height % m == 0,
                                f"multiple of {m}"))
        if a.power_of_two:
            pot = lambda v: v > 0 and (v & (v - 1)) == 0
            checks.append(check("power-of-two", pot(img.width) and pot(img.height)))
        cols = L.image_colors(img)
        report["colors"] = len(cols)
        if a.max_colors:
            checks.append(check("max-colors", len(cols) <= a.max_colors,
                                f"{len(cols)} > {a.max_colors}"))
        if a.palette:
            pal = set(I().sibling("palettes").read_palette_any(a.palette))
            off = [c for c, _ in cols if c not in pal]
            checks.append(check("palette-compliance", not off,
                                f"{len(off)} off-palette colors, e.g. {[L.hex6(c) for c in off[:4]]}"))
        alpha = img.getchannel("A")
        amin, amax = alpha.getextrema()
        has_alpha = amin < 255
        semi = sum(1 for v in alpha.getdata() if 0 < v < 255)
        report["alpha"] = {"present": has_alpha, "semi_transparent_pixels": semi}
        if a.require_alpha:
            checks.append(check("alpha-present", has_alpha))
        if a.forbid_alpha:
            checks.append(check("alpha-absent", not has_alpha))
        if a.binary_alpha:
            checks.append(check("binary-alpha", semi == 0, f"{semi} semi-transparent pixels"))
        if a.max_bytes:
            sz = p.stat().st_size
            report["bytes"] = sz
            checks.append(check("file-size-budget", sz <= a.max_bytes, f"{sz} > {a.max_bytes}"))
        if a.naming:
            checks.append(check("naming-pattern", re.fullmatch(a.naming, p.name) is not None,
                                f"{p.name} vs /{a.naming}/"))
        if a.grid_block:
            gd = L.detect_grid(img)
            report["grid"] = gd
            checks.append(check("grid-block", gd["block"] == a.grid_block,
                                f"detected {gd['block']} expected {a.grid_block}"))
        if a.detect_resample:
            gd = L.detect_grid(img)
            report["grid"] = gd
            checks.append(check("no-resample-damage", gd["block"] == 1 or gd["score"] >= 0.985,
                                f"block={gd['block']} score={gd['score']} method={gd['method']}"))
        if len(frames) > 1:
            ac, fa = animation_checks(frames, durations, ctx.cfg)
            if not getattr(a, "require_loop", False):
                for c in ac:
                    if c["check"] == "loop-closure(first~last)":
                        c["detail"] += " [informational; pass --require-loop to enforce]"
                        c["pass"] = True
            checks.extend(ac)
            report["flash"] = fa
            if a.min_frames:
                checks.append(check("min-frames", len(frames) >= a.min_frames))
            if a.max_frames:
                checks.append(check("max-frames", len(frames) <= a.max_frames))
    report["checks"] = checks
    report["pass"] = all(c["pass"] for c in checks) if checks else True
    report["failed"] = [c["check"] for c in checks if not c["pass"]]
    if a.report:
        L.write_json(report, a.report, ctx)
    return report


# ----------------------------------------------------------------- compare --

def cmd_compare(a, ctx):
    L = I()
    fa, da, ia = S().decode_animation(a.input, ctx.cfg)
    fb, db, ib = S().decode_animation(a.reference, ctx.cfg)
    checks = [check("frame-count-match", len(fa) == len(fb), f"{len(fa)} vs {len(fb)}")]
    n = min(len(fa), len(fb))
    total_px = 0
    diff_px = 0
    per_frame = []
    worst_bbox = None
    heat_frames = []
    for i in range(n):
        A, B = fa[i], fb[i]
        if A.size != B.size:
            checks.append(check(f"frame-{i}-size", False, f"{A.size} vs {B.size}"))
            continue
        pa, pb = list(A.getdata()), list(B.getdata())
        changed = [k for k in range(len(pa))
                   if max(abs(pa[k][j] - pb[k][j]) for j in range(4)) > a.tolerance]
        total_px += len(pa)
        diff_px += len(changed)
        pct = 100 * len(changed) / max(1, len(pa))
        per_frame.append({"frame": i, "changed_pixels": len(changed), "changed_pct": round(pct, 3)})
        if a.diff_image and changed:
            heat = A.copy().convert("RGBA")
            hp = heat.load()
            w = A.width
            for k in changed:
                hp[k % w, k // w] = (255, 40, 60, 255)
            heat_frames.append(heat)
    pct = 100 * diff_px / max(1, total_px)
    checks.append(check("diff-threshold", pct <= a.max_diff_pct,
                        f"{pct:.3f}% changed > {a.max_diff_pct}%"))
    ca = {c for c, _ in L.image_colors(fa[0])}
    cb = {c for c, _ in L.image_colors(fb[0])}
    report = {"input": a.input, "reference": a.reference,
              "changed_pixels": diff_px, "changed_pct": round(pct, 3),
              "palette_added": [L.hex6(c) for c in sorted(cb - ca)][:16],
              "palette_removed": [L.hex6(c) for c in sorted(ca - cb)][:16],
              "per_frame": per_frame[:64], "checks": checks,
              "pass": all(c["pass"] for c in checks)}
    if a.diff_image and heat_frames:
        if len(heat_frames) == 1:
            L.save_image(heat_frames[0], a.diff_image, ctx)
        else:
            S().encode_animation(heat_frames, da[:len(heat_frames)], a.diff_image, ctx)
        report["diff_image"] = a.diff_image
    if a.report:
        L.write_json(report, a.report, ctx)
    return report


# ----------------------------------------------------------------- restore --

def cmd_restore(a, ctx):
    L = I()
    img = L.load_image(a.input, ctx.cfg)
    gd = L.detect_grid(img)
    block = a.block or gd["block"]
    if block < 1:
        raise L.ToolError("bad-arg", "block must be >= 1")
    before = len(L.image_colors(img))
    if block > 1:
        nw, nh = max(1, img.width // block), max(1, img.height // block)
        native = L.scale_to(img, nw, nh, "box")
    else:
        native = img.copy()
    colors = a.colors or min(64, max(2, len(L.image_colors(native))))
    q, pal = L.quantize(native, colors=colors, dither="none")
    removed = 0
    if a.cleanup:
        q, removed = L.cleanup_orphans(q)
    L.save_image(q, a.output, ctx)
    report = {"output": a.output, "detected": gd, "block_used": block,
              "native_size": list(q.size), "colors_before": before,
              "colors_after": len(L.image_colors(q)), "orphans_fixed": removed}
    if a.upscale:
        up = L.nearest_scale(q, block if block > 1 else a.upscale)
        upath = str(Path(a.output).with_name(Path(a.output).stem + f"-x{up.width // q.width}.png"))
        L.save_image(up, upath, ctx)
        report["upscaled"] = upath
    return report


# ---------------------------------------------------------------- critique --

def cmd_critique(a, ctx):
    L = I()
    G_ = I().sibling("generators")
    frames, durations, info = S().decode_animation(a.input, ctx.cfg)
    img = frames[0]
    w, h = img.size
    cols = L.image_colors(img)
    findings = []
    metrics = {"size": [w, h], "colors": len(cols), "frames": len(frames)}
    # orphan pixels
    if w * h <= ctx.cfg["limits"]["op_pixels"]:
        _, orphans = L.cleanup_orphans(img)
        metrics["orphan_pixels"] = orphans
        if orphans > max(2, w * h // 200):
            findings.append(("medium", f"{orphans} isolated pixels add noise; run cleanup or merge them into clusters."))
    # palette size vs canvas
    if len(cols) > max(32, w * h // 8):
        findings.append(("medium", f"{len(cols)} colors on {w}x{h} suggests resampling or gradient noise; consider palette-apply."))
    # grid integrity
    gd = L.detect_grid(img)
    metrics["grid"] = gd
    if gd["block"] > 1 and gd["score"] < 0.985:
        findings.append(("high", f"Pixel grid damaged (block {gd['block']}, score {gd['score']}); restore to native grid."))
    # silhouette readability
    sil = L.silhouette(img)
    bbox = sil.getbbox()
    if bbox:
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        fill = sum(1 for p in sil.crop(bbox).getdata() if p[3] > 0) / max(1, bw * bh)
        metrics["silhouette_fill"] = round(fill, 3)
        metrics["silhouette_bbox"] = list(bbox)
        if fill < 0.25:
            findings.append(("low", "Sparse silhouette: verify readability at play size (thin shapes may vanish when scaled down)."))
        margin = min(bbox[0], bbox[1], w - bbox[2], h - bbox[3])
        metrics["margin_min"] = margin
        if margin == 0:
            findings.append(("low", "Art touches the canvas edge; add 1px transparent margin for outlines/rotation safety."))
    # contrast vs border/background
    border_cols = [img.getpixel((x, 0)) for x in range(w)] + [img.getpixel((x, h - 1)) for x in range(w)]
    bg = max(set(border_cols), key=border_cols.count)
    if bg[3] > 200 and cols:
        main = cols[0][0]
        cr = L.contrast_ratio(main, bg)
        metrics["contrast_vs_background"] = round(cr, 2)
        if cr < 2.0:
            findings.append(("medium", f"Main color contrast vs background is {cr:.2f}:1; strengthen value separation or add outline."))
    # value distribution
    lums = sorted(L.luminance(c) for c, _ in cols)
    if lums:
        spread = lums[-1] - lums[0]
        metrics["value_spread"] = round(spread, 3)
        if spread < 0.35:
            findings.append(("medium", "Narrow value range flattens form; extend darks/lights in the palette ramps."))
    # animation
    if len(frames) > 1:
        ac, fa = animation_checks(frames, durations, ctx.cfg)
        metrics["animation"] = {"checks": ac, "flash": fa}
        for c in ac:
            if not c["pass"] and c["check"] not in ("loop-closure(first~last)",):
                findings.append(("medium", f"Animation: {c['check']} — {c['detail']}"))
        if fa["flash_risk"]:
            findings.append(("high", "Flash-risk luminance pattern detected; reduce full-frame brightness swings (accessibility)."))
    if not findings:
        findings.append(("info", "No automatic issues detected. Review intent, style consistency, and originality manually."))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: order[f[0]])
    md = f"# Pixel art critique: {Path(a.input).name}\n\n"
    md += f"- Size: {w}x{h} | Colors: {len(cols)} | Frames: {len(frames)}\n"
    md += f"- Detected native grid: block {gd['block']} ({gd['method']}, score {gd['score']})\n\n"
    md += "## Findings (prioritized)\n\n"
    for sev, msg in findings:
        md += f"- **{sev.upper()}** — {msg}\n"
    md += ("\n## Review checklist (manual)\n\n"
           "- Silhouette reads at actual display size and on busy backgrounds\n"
           "- Clusters and contour rhythm are intentional (no accidental jaggies)\n"
           "- Palette ramps hue-shift; materials share a light direction\n"
           "- Animation keys, spacing, and loop closure match intent\n"
           "- Original design; no protected characters/logos/compositions\n")
    report = {"input": a.input, "metrics": metrics,
              "findings": [{"severity": s, "message": m} for s, m in findings]}
    if a.output:
        L.write_text(md, a.output, ctx)
        report["markdown"] = a.output
    if a.json_out:
        L.write_json(report, a.json_out, ctx)
        report["json"] = a.json_out
    return report


# ------------------------------------------------------------------- audit --

SECRET_PATTERNS = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic-api-key", re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\b\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]")),
    ("password-assignment", re.compile(r"(?i)\bpassword\s*[:=]\s*['\"][^'\"]{6,}['\"]")),
    ("bearer-token", re.compile(r"(?i)\bauthorization\b\s*[:=]\s*['\"]bearer\s+[A-Za-z0-9_\-\.]{16,}['\"]")),
    ("connection-string", re.compile(r"(?i)\b(mysql|postgres|postgresql|mongodb|redis)://[^\s'\"]+:[^\s'\"]+@")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
]
UNSAFE_PATH = re.compile(r"(?:[A-Za-z]:\\|/home/[a-z]|/Users/[A-Za-z]|\.\./)")
TEXT_EXTS = {".json", ".md", ".txt", ".lua", ".sh", ".js", ".mjs", ".py", ".css", ".html", ".csv",
             ".xml", ".tmx", ".tsx", ".gpl", ".pal", ".hex", ".srt", ".tres", ".meta", ".cs"}


def scan_text_for_secrets(text, label):
    findings = []
    for name, rx in SECRET_PATTERNS:
        for m in rx.finditer(text):
            snippet = m.group(0)
            masked = snippet[:10] + "…" + snippet[-4:] if len(snippet) > 18 else snippet[:6] + "…"
            findings.append({"file": label, "pattern": name, "masked_match": masked})
    return findings


def cmd_audit(a, ctx):
    L = I()
    root = Path(a.directory)
    if not root.is_dir():
        raise L.ToolError("input-not-found", f"Directory not found: {a.directory}")
    files = sorted(f for f in root.rglob("*") if f.is_file())
    if len(files) > 2000:
        raise L.ToolError("limit-files", "Audit limited to 2000 files.")
    entries, secrets, unsafe = [], [], []
    for f in files:
        rel = str(f.relative_to(root))
        data = f.read_bytes()
        entry = {"file": rel, "bytes": len(data),
                 "sha256": hashlib.sha256(data).hexdigest()}
        ext = f.suffix.lower()
        if ext in (".png", ".gif", ".webp", ".bmp", ".jpg", ".jpeg", ".tiff", ".tif"):
            try:
                img = Image.open(f)
                entry.update({"kind": "image", "format": img.format,
                              "size": list(img.size), "frames": getattr(img, "n_frames", 1)})
                if img.format == "PNG":
                    for k, v in img.info.items():
                        if isinstance(v, str) and k.lower() not in ("dpi",):
                            secrets.extend(scan_text_for_secrets(v, f"{rel} (PNG {k})"))
            except Exception as e:
                entry.update({"kind": "image", "error": str(e)})
        elif ext in TEXT_EXTS:
            text = data.decode("utf-8", errors="replace")
            entry["kind"] = "text"
            secrets.extend(scan_text_for_secrets(text, rel))
            for m in UNSAFE_PATH.finditer(text):
                unsafe.append({"file": rel, "match": m.group(0)})
        else:
            entry["kind"] = "binary"
        if a.naming and not re.fullmatch(a.naming, f.name):
            entry["naming_violation"] = True
        entries.append(entry)
    completeness = []
    if a.require:
        req = L.read_json(a.require)
        patterns = req.get("required", [])
        for pat in patterns:
            hits = [e["file"] for e in entries if re.search(pat, e["file"])]
            completeness.append({"pattern": pat, "found": len(hits), "ok": bool(hits),
                                 "examples": hits[:3]})
    manifest = {
        "tool": "pixel-art-media", "kind": "provenance-audit",
        "root": str(root), "file_count": len(entries),
        "creator": a.creator, "license": a.license,
        "sources": a.source or [], "notes": a.notes,
        "files": entries,
        "secret_findings": secrets, "unsafe_path_findings": unsafe[:50],
        "completeness": completeness,
        "clean": not secrets and all(c["ok"] for c in completeness),
    }
    L.write_json(manifest, a.output, ctx)
    md = f"# Provenance & safety audit: {root}\n\n"
    md += f"- Files: {len(entries)} | License: {a.license or 'unspecified'} | Creator: {a.creator or 'unspecified'}\n"
    md += f"- Secret findings: **{len(secrets)}** | Unsafe-path findings: {len(unsafe)}\n"
    if completeness:
        md += f"- Completeness: {sum(1 for c in completeness if c['ok'])}/{len(completeness)} required patterns found\n"
    md += "\n| File | Kind | Bytes | SHA-256 (first 12) |\n|---|---|---|---|\n"
    for e in entries[:200]:
        md += f"| {e['file']} | {e.get('kind','?')} | {e['bytes']} | `{e['sha256'][:12]}` |\n"
    if len(entries) > 200:
        md += f"\n… {len(entries)-200} more files in JSON manifest.\n"
    if a.markdown:
        L.write_text(md, a.markdown, ctx)
    return {"output": a.output, "files": len(entries), "secret_findings": len(secrets),
            "unsafe_paths": len(unsafe), "clean": manifest["clean"],
            "completeness": completeness}


def cmd_license_record(a, ctx):
    L = I()
    rec = {"asset": a.asset, "creator": a.creator, "license": a.license,
           "sources": a.source or [], "modifications": a.modifications or [],
           "attribution_text": a.attribution or "",
           "restrictions": a.restrictions or "", "approval_status": a.status}
    if not a.attribution:
        rec["attribution_text"] = f'"{a.asset}" by {a.creator or "unknown"} — {a.license or "license unspecified"}.'
        if a.source:
            rec["attribution_text"] += " Sources: " + "; ".join(a.source)
    L.write_json(rec, a.output, ctx)
    md = f"# License & attribution: {a.asset}\n\n"
    md += f"- Creator: {rec['creator'] or 'unspecified'}\n- License: {rec['license'] or 'unspecified'}\n"
    md += f"- Status: {rec['approval_status']}\n- Sources: {', '.join(rec['sources']) or 'none recorded'}\n"
    md += f"- Modifications: {', '.join(rec['modifications']) or 'none recorded'}\n"
    md += f"- Restrictions: {rec['restrictions'] or 'none recorded'}\n\n"
    md += f"**Attribution:** {rec['attribution_text']}\n"
    mdp = str(Path(a.output).with_suffix(".md"))
    L.write_text(md, mdp, ctx)
    return {"output": a.output, "markdown": mdp, "record": rec}


# -------------------------------------------------------------- reopen test -

def cmd_reopen_test(a, ctx):
    L = I()
    files = L.collect_inputs(a.inputs, exts=tuple(TEXT_EXTS | {".png", ".gif", ".webp", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff", ".vox", ".ase", ".act", ".ico", ".lua", ".sh", ".js", ".mjs"}))
    results = []
    node = shutil.which("node")
    bash = shutil.which("bash")
    for f in files:
        ext = f.suffix.lower()
        r = {"file": str(f), "ok": False, "method": ""}
        try:
            if ext in (".png", ".gif", ".webp", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff", ".ico"):
                img = Image.open(f)
                n = getattr(img, "n_frames", 1)
                for i in range(n):
                    img.seek(i)
                    img.convert("RGBA")
                r.update(ok=True, method=f"decoded {n} frame(s)", size=list(img.size))
            elif ext == ".json":
                json.loads(f.read_text(encoding="utf-8"))
                r.update(ok=True, method="JSON parsed")
            elif ext in (".xml", ".tmx", ".tsx", ".svg", ".plist"):
                ET.fromstring(f.read_text(encoding="utf-8"))
                r.update(ok=True, method="XML parsed")
            elif ext == ".vox":
                m = I().sibling("generators").vox_read(str(f))
                r.update(ok=True, method=f"VOX parsed ({len(m['voxels'])} voxels)")
            elif ext in (".ase", ".act", ".gpl", ".pal", ".hex"):
                pal = I().sibling("palettes").read_palette_any(str(f))
                r.update(ok=True, method=f"palette parsed ({len(pal)} colors)")
            elif ext == ".sh" and bash:
                p = subprocess.run([bash, "-n", str(f)], capture_output=True, timeout=10)
                r.update(ok=p.returncode == 0, method="bash -n syntax check",
                         error=p.stderr.decode()[:200] if p.returncode else "")
            elif ext in (".js", ".mjs") and node:
                p = subprocess.run([node, "--check", str(f)], capture_output=True, timeout=10)
                r.update(ok=p.returncode == 0, method="node --check syntax check",
                         error=p.stderr.decode()[:200] if p.returncode else "")
            elif ext == ".lua":
                r.update(ok=True, method="read as text (Lua runtime unavailable; not executed)")
                r["note"] = "Syntax reviewed by template only — validate inside Aseprite."
            else:
                f.read_text(encoding="utf-8")
                r.update(ok=True, method="read as UTF-8 text")
        except subprocess.TimeoutExpired:
            r.update(ok=False, method="syntax check timeout")
        except Exception as e:
            r.update(ok=False, method="error", error=str(e)[:200])
        results.append(r)
    report = {"files": results, "pass": all(r["ok"] for r in results),
              "checked": len(results)}
    if a.report:
        L.write_json(report, a.report, ctx)
    return report


# --------------------------------------------------------------- seam check -

def cmd_seam_check(a, ctx):
    L = I()
    img = L.load_image(a.input, ctx.cfg)
    w, h = img.size
    px = img.load()

    def col_diff(x1, x2):
        ds = [max(abs(px[x1, y][j] - px[x2, y][j]) for j in range(4)) for y in range(h)]
        return sum(ds) / len(ds)

    def row_diff(y1, y2):
        ds = [max(abs(px[x, y1][j] - px[x, y2][j]) for j in range(4)) for x in range(w)]
        return sum(ds) / len(ds)

    # A tile is seamless when the wrap transition is no harsher than the
    # harshest transition already inside the pattern (periodic patterns like
    # checker legitimately have different edge columns).
    wrap_x = col_diff(w - 1, 0)
    wrap_y = row_diff(h - 1, 0)
    internal_x = max((col_diff(x, x + 1) for x in range(w - 1)), default=0)
    internal_y = max((row_diff(y, y + 1) for y in range(h - 1)), default=0)
    seamless_x = wrap_x <= internal_x + a.tolerance
    seamless_y = wrap_y <= internal_y + a.tolerance
    report = {"input": a.input, "size": [w, h],
              "horizontal_wrap": {"wrap_diff": round(wrap_x, 2),
                                  "max_internal_diff": round(internal_x, 2),
                                  "seamless": seamless_x},
              "vertical_wrap": {"wrap_diff": round(wrap_y, 2),
                                "max_internal_diff": round(internal_y, 2),
                                "seamless": seamless_y},
              "pass": seamless_x and seamless_y}
    if a.fix and not report["pass"]:
        band = max(1, a.band)
        fixed = img.copy()
        fp = fixed.load()
        if not seamless_x:
            for x in range(band):
                t = (x + 1) / (band + 1)
                for y in range(h):
                    left, right = img.getpixel((x, y)), img.getpixel((w - 1 - x, y))
                    blend_a = tuple(int(left[j] * (1 - t * 0.5) + right[j] * t * 0.5) for j in range(4))
                    blend_b = tuple(int(right[j] * (1 - t * 0.5) + left[j] * t * 0.5) for j in range(4))
                    fp[x, y] = blend_a
                    fp[w - 1 - x, y] = blend_b
        if not seamless_y:
            src = fixed.copy()
            for y in range(band):
                t = (y + 1) / (band + 1)
                for x in range(w):
                    top, bot = src.getpixel((x, y)), src.getpixel((x, h - 1 - y))
                    fp[x, y] = tuple(int(top[j] * (1 - t * 0.5) + bot[j] * t * 0.5) for j in range(4))
                    fp[x, h - 1 - y] = tuple(int(bot[j] * (1 - t * 0.5) + top[j] * t * 0.5) for j in range(4))
        L.save_image(fixed, a.fix, ctx)
        report["fixed"] = a.fix
    if a.preview:
        reps = a.repeat
        L.check_dims(ctx.cfg, w * reps, h * reps)
        tiled = Image.new("RGBA", (w * reps, h * reps))
        src = Image.open(a.fix) if a.fix and Path(a.fix).is_file() and not ctx.dry_run else img
        for yy in range(reps):
            for xx in range(reps):
                tiled.paste(src, (xx * w, yy * h))
        L.save_image(tiled, a.preview, ctx)
        report["preview"] = a.preview
    return report


# --------------------------------------------------------------- register ---

def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    p = add("validate", "Validate image/animation/atlas against configurable checks", cmd_validate)
    p.add_argument("input")
    p.add_argument("--width", type=int); p.add_argument("--height", type=int)
    p.add_argument("--multiple-of", type=int); p.add_argument("--power-of-two", action="store_true")
    p.add_argument("--max-colors", type=int); p.add_argument("--palette")
    p.add_argument("--require-alpha", action="store_true"); p.add_argument("--forbid-alpha", action="store_true")
    p.add_argument("--binary-alpha", action="store_true")
    p.add_argument("--max-bytes", type=int); p.add_argument("--naming")
    p.add_argument("--grid-block", type=int); p.add_argument("--detect-resample", action="store_true")
    p.add_argument("--min-frames", type=int); p.add_argument("--max-frames", type=int)
    p.add_argument("--require-loop", action="store_true",
                   help="Fail validation when first/last frames do not loop cleanly")
    p.add_argument("--sheet", help="Atlas sheet image for bounds checks")
    p.add_argument("--report")

    p = add("compare", "Visual regression diff vs an approved reference", cmd_compare)
    p.add_argument("input"); p.add_argument("reference")
    p.add_argument("--tolerance", type=int, default=0)
    p.add_argument("--max-diff-pct", type=float, default=0.0)
    p.add_argument("--diff-image"); p.add_argument("--report")

    p = add("restore", "Repair upscaled/damaged pixel art back to a clean native grid", cmd_restore)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--block", type=int, help="Force native block size (default: auto-detect)")
    p.add_argument("--colors", type=int, help="Palette size after restoration")
    p.add_argument("--cleanup", action="store_true"); p.add_argument("--upscale", type=int, default=0)

    p = add("critique", "Measurement-based critique with prioritized findings", cmd_critique)
    p.add_argument("input"); p.add_argument("--output", help="Markdown critique path")
    p.add_argument("--json-out")

    p = add("audit", "Provenance manifest + checksums + secret scan + completeness", cmd_audit)
    p.add_argument("directory"); p.add_argument("output")
    p.add_argument("--creator"); p.add_argument("--license"); p.add_argument("--source", action="append")
    p.add_argument("--notes"); p.add_argument("--naming", help="Regex every filename must match")
    p.add_argument("--require", help="JSON {required:[regex,...]} completeness spec")
    p.add_argument("--markdown")

    p = add("license-record", "Record creator/license/sources/attribution for an asset", cmd_license_record)
    p.add_argument("asset"); p.add_argument("output")
    p.add_argument("--creator"); p.add_argument("--license"); p.add_argument("--source", action="append")
    p.add_argument("--modifications", action="append"); p.add_argument("--attribution")
    p.add_argument("--restrictions"); p.add_argument("--status", default="pending",
                   choices=["pending", "approved", "rejected"])

    p = add("reopen-test", "Reopen/parse/syntax-check every final artifact", cmd_reopen_test)
    p.add_argument("inputs", nargs="+"); p.add_argument("--report")

    p = add("seam-check", "Test tile wrap seams; optionally cross-fade fix + tiled preview", cmd_seam_check)
    p.add_argument("input")
    p.add_argument("--tolerance", type=float, default=8.0)
    p.add_argument("--fix", help="Write seam-blended copy here")
    p.add_argument("--band", type=int, default=2, help="Blend band width for --fix")
    p.add_argument("--preview", help="Write NxN tiled preview here")
    p.add_argument("--repeat", type=int, default=3)
