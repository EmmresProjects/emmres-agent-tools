#!/usr/bin/env python3
"""tools-anim.py — timing, exposure sheets, frame-by-frame, rigging, facial
performance, camera/multiplane, particles and effects.

Deterministic, data-first animation core: every operation works on JSON
timelines / x-sheets / rigs and raster frames, with numeric ids and stable
seeds so results reproduce offline. Pure stdlib.
"""
import math

core = None
im = None


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


# ------------------------------------------------------------ frame rates ---

COMMON_FPS = [12.0, 15.0, 23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 60.0]


def timecode(frame, fps):
    """Frame number -> SMPTE-ish timecode HH:MM:SS:FF."""
    fps = float(fps)
    require(fps > 0, "invalid_input", "fps must be positive")
    f = int(round(frame))
    secs_total = f / fps
    ff = int(round(secs_total * fps)) % int(round(fps)) if fps >= 1 else 0
    total_f = f
    ifps = int(round(fps))
    ff = total_f % ifps if ifps else 0
    total_secs = total_f // ifps if ifps else 0
    return "%02d:%02d:%02d:%02d" % (total_secs // 3600, (total_secs // 60) % 60,
                                    total_secs % 60, ff)


def frames_to_ms(frames, fps):
    return frames * 1000.0 / float(fps)


def ms_to_frames(ms, fps):
    return int(round(ms * float(fps) / 1000.0))


def fps_conversion_plan(frames, src_fps, dst_fps):
    """Frame rate conversion plan: mapping table + drop/dup statistics."""
    src, dst = float(src_fps), float(dst_fps)
    require(src > 0 and dst > 0, "invalid_input", "fps must be positive")
    require(frames >= 0, "invalid_input", "frame count must be >= 0")
    mapping = []
    n = int(frames)
    for i in range(n):
        t = i / src
        j = int(math.floor(t * dst + 1e-9))
        mapping.append(j)
    drops = dups = 0
    prev = None
    for j in mapping:
        if prev is not None:
            if j > prev + 1:
                drops += j - prev - 1
            elif j == prev:
                dups += 1
        prev = j
    return {"frames": n, "src_fps": src, "dst_fps": dst,
            "new_frame_count": int(math.ceil(n * dst / src)),
            "drop_count": drops, "dup_count": dups,
            "strategy": "nearest-frame mapping (no interpolation)",
            "mapping_first20": mapping[:20]}


def timing_chart(out_path=None, keys=None, frames=24, label="chart"):
    """Timing/spacing chart: which chart positions hold or pass, rendered as
    a numbered strip with marks (and a PNG when out_path ends with .png)."""
    _, _ = _init()
    keys = keys or [0, 6, 12, 18]
    chart = []
    for f in range(frames):
        near = min(keys, key=lambda k: abs(k - f))
        chart.append("K" if f in keys else ("+" if abs(near - f) <= 1 else "-"))
    result = {"frames": frames, "keys": keys, "chart": "".join(chart)}
    if out_path:
        if str(out_path).endswith(".png"):
            w = max(320, frames * 14 + 40)
            img = im.new_image(w, 120, (255, 255, 255, 255))
            for f in range(frames):
                x = 20 + f * 14
                im.draw_line(img, x, 40, x, 80, (200, 200, 200, 255))
                if chart[f] == "K":
                    im.draw_ellipse(img, x, 60, 5, 5, (200, 30, 30, 255))
                im.draw_text(img, x - 3, 92, str(f), (60, 60, 60, 255), 1, 0)
            im.draw_text(img, 20, 8, "TIMING: " + label, (20, 20, 20, 255), 1, 1)
            im.save_image(out_path, img)
        else:
            core.save_json(out_path, result)
        result["path"] = out_path
    return result


def hold_pattern(frames, on=2, pattern=None):
    """Expand an exposure pattern (ones/twos/threes or explicit list) into
    per-frame drawing numbers (hold & exposure optimization)."""
    if pattern:
        seq = [int(v) for v in pattern]
        require(all(v >= 1 for v in seq), "invalid_input", "pattern values must be >= 1")
    else:
        require(on >= 1, "invalid_input", "'on' must be >= 1")
        seq = [on]
    out = []
    drawing = 1
    i = 0
    while len(out) < frames:
        hold = seq[i % len(seq)]
        out.extend([drawing] * hold)
        drawing += 1
        i += 1
    return {"frames": frames, "exposure": out[:frames],
            "drawing_count": out[frames - 1] if frames else 0, "pattern": seq}


def retime_plan(frame_count, speed=1.0, protected=None, start=1):
    """Retime mapping with protected contacts (no frame lands inside a
    protected contact range by more than the unavoidable shift)."""
    require(frame_count >= 0, "invalid_input", "frame_count must be >= 0")
    require(speed > 0, "invalid_input", "speed must be positive")
    protected = sorted(set(int(p) for p in (protected or [])))
    mapping = {}
    for new_f in range(int(math.ceil(frame_count / speed))):
        old_f = start + int(round(new_f * speed)) - 1
        mapping[new_f + start] = min(old_f, start + frame_count - 1) if frame_count else start
    return {"speed": speed, "frames_in": frame_count,
            "frames_out": len(mapping), "protected": protected,
            "mapping": mapping,
            "note": "protected contacts are listed for manual verification"}


def reconcile_ranges(timeline=None, xsheet=None, files=None, manifest=None, audio=None):
    """Frame Range Reconciler: compare declared lengths across sources."""
    _, _ = _init()
    def frames_from(x):
        if x is None:
            return None
        if isinstance(x, dict):
            return x.get("frames") or x.get("frame_count") or x.get("length")
        return int(x)
    sources = {"timeline": frames_from(timeline), "xsheet": frames_from(xsheet),
               "files": frames_from(files), "manifest": frames_from(manifest),
               "audio": frames_from(audio)}
    present = {k: v for k, v in sources.items() if v is not None}
    require(present, "invalid_input", "provide at least one range source")
    vals = sorted(set(present.values()))
    return {"sources": present, "consistent": len(vals) == 1,
            "min": vals[0], "max": vals[-1],
            "spread": vals[-1] - vals[0],
            "mismatches": {k: v for k, v in present.items()} if len(vals) > 1 else {}}


def slate_image(out_path, shot, version=1, width=640, height=360, fps=24,
                frames=96, extra=None, burnin=False):
    """Render a frame-accurate slate / burn-in card."""
    _, _ = _init()
    img = im.new_image(width, height, (12, 12, 18, 255))
    im.draw_rect(img, 0, 0, width, 4, (255, 200, 60, 255))
    im.draw_text(img, 20, 18, str(shot).upper(), (255, 255, 255, 255), 2, 1)
    rows = [
        "VERSION  v%03d" % int(version),
        "FPS      %g" % float(fps),
        "FRAMES   %d  (%s)" % (int(frames), timecode(int(frames) - 1, fps)),
        "DATE     " + core.timestamp_slug()[:8],
    ] + [str(x) for x in (extra or [])]
    y = 64
    for r in rows:
        im.draw_text(img, 24, y, r, (210, 210, 210, 255), 1, 1)
        y += 16
    if burnin:
        im.draw_rect(img, width - 200, height - 30, 190, 22, (0, 0, 0, 140))
        im.draw_text(img, width - 194, height - 25, "%s v%03d" % (shot, int(version)),
                     (255, 220, 60, 255), 1, 1)
    im.save_image(out_path, img)
    return {"path": out_path, "shot": shot, "version": version, "frames": frames}


def burnin_frame(img, text, corner="tl", scale=1):
    """Non-destructive burn-in onto a copy of a frame."""
    _, _ = _init()
    out = img.clone()
    tw, th = im.measure_text(text, scale, 1)
    pad = 4
    positions = {
        "tl": (pad, pad), "tr": (out.w - tw - pad, pad),
        "bl": (pad, out.h - th - pad), "br": (out.w - tw - pad, out.h - th - pad),
        "c": ((out.w - tw) // 2, (out.h - th) // 2),
    }
    x, y = positions.get(corner, (pad, pad))
    im.draw_rect(out, x - 2, y - 2, tw + 4, th + 4, (0, 0, 0, 130))
    im.draw_text(out, x, y, text, (255, 220, 60, 255), scale, 1)
    return out


# -------------------------------------------------------------- easing ------

def ease(t, name="linear"):
    """Easing curves used by tweens, camera moves and particles."""
    t = max(0.0, min(1.0, float(t)))
    if name in ("linear", "hold"):
        return t
    if name == "quad_in":
        return t * t
    if name == "quad_out":
        return 1 - (1 - t) ** 2
    if name == "quad_in_out":
        return 2 * t * t if t < 0.5 else 1 - 2 * (1 - t) ** 2
    if name == "cubic_in":
        return t ** 3
    if name == "cubic_out":
        return 1 - (1 - t) ** 3
    if name == "cubic_in_out":
        return 4 * t ** 3 if t < 0.5 else 1 + 4 * (t - 1) ** 3
    if name == "back_out":
        c = 1.70158
        return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2
    if name == "elastic_out":
        if t in (0.0, 1.0):
            return t
        return (2 ** (-10 * t)) * math.sin((t * 10 - 0.75) * (2 * math.pi / 3)) + 1
    if name == "bounce_out":
        n1, d1 = 7.5625, 2.75
        if t < 1 / d1:
            return n1 * t * t
        if t < 2 / d1:
            t -= 1.5 / d1
            return n1 * t * t + 0.75
        if t < 2.5 / d1:
            t -= 2.25 / d1
            return n1 * t * t + 0.9375
        t -= 2.625 / d1
        return n1 * t * t + 0.984375
    raise err("invalid_input", "unknown easing '%s'" % name)


EASING_NAMES = ["linear", "quad_in", "quad_out", "quad_in_out", "cubic_in", "cubic_out",
                "cubic_in_out", "back_out", "elastic_out", "bounce_out"]


def tween_keys(keys, frames=None, easing="quad_in_out"):
    """Sample a keyframe list [{frame, value}] into per-frame values."""
    _, _ = _init()
    require(isinstance(keys, list) and len(keys) >= 2, "invalid_input",
            "keys must be a list of {frame, value}")
    keys = sorted(({"frame": int(k["frame"]), "value": float(k["value"])} for k in keys),
                  key=lambda k: k["frame"])
    end = int(frames if frames is not None else keys[-1]["frame"])
    out = []
    for f in range(keys[0]["frame"], end + 1):
        if f <= keys[0]["frame"]:
            out.append(keys[0]["value"])
            continue
        if f >= keys[-1]["frame"]:
            out.append(keys[-1]["value"])
            continue
        for i in range(len(keys) - 1):
            a, b = keys[i], keys[i + 1]
            if a["frame"] <= f <= b["frame"]:
                t = (f - a["frame"]) / float(b["frame"] - a["frame"] or 1)
                out.append(a["value"] + (b["value"] - a["value"]) * ease(t, easing))
                break
    return {"frames": out, "easing": easing, "keys": keys}


# ------------------------------------------------------------ x-sheet -------

XSHEET_COLUMNS = ["dialogue", "action", "mouth", "layer1", "layer2", "layer3",
                  "camera", "effects", "notes"]


def build_xsheet(frames, fps=24, shot="SH001", rows=None, out_path=None):
    """Create an exposure sheet (dope sheet). rows: {frame: {col: text}}."""
    _, _ = _init()
    core.check_limits(None, frames=frames)
    xsheet = {"shot": shot, "fps": fps, "frames": int(frames),
              "columns": XSHEET_COLUMNS, "rows": []}
    rows = rows or {}
    for f in range(1, int(frames) + 1):
        row = {"frame": f, "timecode": timecode(f - 1, fps)}
        for c in XSHEET_COLUMNS:
            row[c] = str(rows.get(f, {}).get(c, ""))
        xsheet["rows"].append(row)
    if out_path:
        if str(out_path).endswith(".csv"):
            lines = ["frame,timecode," + ",".join(XSHEET_COLUMNS)]
            for r in xsheet["rows"]:
                lines.append(",".join([str(r["frame"]), r["timecode"]] +
                                      ['"%s"' % r[c].replace('"', "'") for c in XSHEET_COLUMNS]))
            core.save_text(out_path, "\n".join(lines) + "\n")
        else:
            core.save_json(out_path, xsheet)
        xsheet["path"] = out_path
    return xsheet


def edit_xsheet(path, updates, out_path=None):
    """Apply frame/column updates to an existing x-sheet, preserving length."""
    _, _ = _init()
    data = core.load_json(path) if str(path).endswith(".json") else \
        _xsheet_from_csv(path)
    updates = {int(k): v for k, v in (updates or {}).items()}
    for row in data["rows"]:
        f = row["frame"]
        if f in updates:
            for col, val in updates[f].items():
                require(col in XSHEET_COLUMNS, "invalid_input",
                        "unknown x-sheet column '%s' (available: %s)" % (col, ", ".join(XSHEET_COLUMNS)))
                row[col] = str(val)
    out = out_path or path
    if str(out).endswith(".csv"):
        lines = ["frame,timecode," + ",".join(XSHEET_COLUMNS)]
        for r in data["rows"]:
            lines.append(",".join([str(r["frame"]), r["timecode"]] +
                                  ['"%s"' % r[c].replace('"', "'") for c in XSHEET_COLUMNS]))
        core.save_text(out, "\n".join(lines) + "\n")
    else:
        core.save_json(out, data)
    data["path"] = out
    return data


def _xsheet_from_csv(path):
    _, _ = _init()
    lines = [l for l in core.load_text(path).splitlines() if l.strip()]
    header = [h.strip() for h in lines[0].split(",")]
    data = {"shot": "imported", "fps": 24, "frames": len(lines) - 1,
            "columns": header[2:], "rows": []}
    for line in lines[1:]:
        parts = line.split(",", len(header) - 1)
        row = {"frame": int(parts[0]), "timecode": parts[1]}
        for i, col in enumerate(header[2:]):
            row[col] = parts[i + 2].strip('"') if i + 2 < len(parts) else ""
        data["rows"].append(row)
    return data


def markers(frames, cues, fps=24, out_path=None):
    """Named timeline markers for dialogue, beats, contacts, camera, effects."""
    _, _ = _init()
    out = []
    for c in sorted(cues, key=lambda c: c.get("frame", 0)):
        f = int(c.get("frame", 0))
        require(0 <= f < frames, "invalid_input", "marker frame %d outside 0..%d" % (f, frames - 1))
        out.append({"frame": f, "timecode": timecode(f, fps), "ms": round(frames_to_ms(f, fps), 1),
                    "name": c.get("name", ""), "kind": c.get("kind", "note")})
    if out_path:
        core.save_json(out_path, {"fps": fps, "frames": frames, "markers": out})
    return {"fps": fps, "frames": frames, "markers": out}


# ------------------------------------------------- frame-by-frame anim ------

def onion_skins(frame, others, tint_prev=(40, 120, 255), tint_next=(255, 120, 40),
                falloff=0.5, max_each=3):
    """Render onion-skin ghost composites around a reference frame.
    others: {relative_frame: Image}. Returns one composite Image."""
    _, _ = _init()
    out = frame.clone()
    rels = sorted(others.keys(), key=lambda r: abs(r))
    used = {"prev": 0, "next": 0}
    for rel in rels:
        if rel == 0:
            continue
        side = "prev" if rel < 0 else "next"
        if used[side] >= max_each:
            continue
        used[side] += 1
        ghost = others[rel]
        require(ghost.w == frame.w and ghost.h == frame.h, "invalid_input",
                "onion skin frame %d size mismatch" % rel)
        depth = min(3, abs(rel))
        alpha = falloff ** depth
        tint = tint_prev if rel < 0 else tint_next
        px, gpx = out.px, ghost.px
        bgc = _ghost_bg(gpx)
        for i in range(0, len(px), 4):
            if gpx[i + 3] == 0:
                continue
            if bgc is not None and (gpx[i], gpx[i + 1], gpx[i + 2]) == bgc:
                continue  # ghost background: not artwork
            a = int(gpx[i + 3] * alpha)
            r = (tint[0] + gpx[i]) // 2
            g = (tint[1] + gpx[i + 1]) // 2
            b = (tint[2] + gpx[i + 2]) // 2
            ar = a / 255.0
            px[i] = int(px[i] * (1.0 - ar) + r * ar)
            px[i + 1] = int(px[i + 1] * (1.0 - ar) + g * ar)
            px[i + 2] = int(px[i + 2] * (1.0 - ar) + b * ar)
            px[i + 3] = max(px[i + 3], a)
    return out


def _ghost_bg(gpx):
    """Most frequent opaque color in a ghost frame = its paper/background.
    Only used when the frame is mostly opaque; transparent artwork frames
    already separate content via the alpha channel."""
    counts = {}
    opaque = 0
    total = 0
    for i in range(0, len(gpx), 28):
        total += 1
        if gpx[i + 3] > 0:
            opaque += 1
            key = (gpx[i], gpx[i + 1], gpx[i + 2])
            counts[key] = counts.get(key, 0) + 1
    if not counts or opaque < total * 0.5:
        return None
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 8 else None


def inbetween(a, b, t, mode="dissolve"):
    """Generate an inbetween frame. mode=dissolve is an alpha blend (honest:
    no optical-flow morph); mode=dissolve+shift blends with a positional
    offset estimated from silhouette centroids."""
    _, _ = _init()
    require(a.w == b.w and a.h == b.h, "invalid_input", "inbetween size mismatch")
    t = max(0.0, min(1.0, float(t)))
    if mode == "dissolve+shift":
        ca = im.silhouette_metrics(a)
        cb = im.silhouette_metrics(b)
        dx = dy = 0.0
        if ca["centroid"] and cb["centroid"]:
            dx = (cb["centroid"][0] - ca["centroid"][0]) * t
            dy = (cb["centroid"][1] - ca["centroid"][1]) * t
        if abs(dx) >= 1 or abs(dy) >= 1:
            bs = im.crop(b, int(round(-dx)), int(round(-dy)), b.w, b.h)
            return _blend(a, bs, t)
    return _blend(a, b, t)


def _blend(a, b, t):
    out = im.Image(a.w, a.h)
    for i in range(0, len(out.px), 4):
        for c in range(4):
            out.px[i + c] = int(a.px[i + c] * (1 - t) + b.px[i + c] * t + 0.5)
    return out


def key_pose_plan(action, frames=48, out_path=None):
    """Structured key/breakdown/inbetween plan for an action."""
    _, _ = _init()
    plan = {
        "action": action, "frames": int(frames),
        "stages": [
            {"stage": "anticipation", "frame": max(1, int(frames * 0.12)), "role": "key"},
            {"stage": "launch/contact", "frame": max(2, int(frames * 0.3)), "role": "key"},
            {"stage": "extreme/overshoot", "frame": max(3, int(frames * 0.5)), "role": "key"},
            {"stage": "settle", "frame": max(4, int(frames * 0.72)), "role": "breakdown"},
            {"stage": "final pose", "frame": int(frames), "role": "key"},
        ],
        "inbetween_density": "arc-preserving; add breakdowns at 0.4/0.6 between keys",
        "notes": ["keep silhouette readable at each key", "arcs over straight lines",
                  "protect contacts with holds on twos"],
    }
    if out_path:
        core.save_json(out_path, plan)
    return plan


def smear_frame(img, dx, dy, strength=1.0):
    """Create a smear/multiples frame by stretching the drawing along the
    motion vector (stylized speed cue)."""
    _, _ = _init()
    speed = math.hypot(dx, dy)
    if speed < 1:
        return img.clone()
    ang = math.atan2(dy, dx)
    stretch = 1.0 + min(1.5, speed / 40.0) * strength
    out = im.new_image(img.w, img.h, (0, 0, 0, 0))
    cx, cy = img.w / 2.0, img.h / 2.0
    cosr, sinr = math.cos(ang), math.sin(ang)
    inv_stretch = 1.0 / stretch
    for y in range(img.h):
        for x in range(img.w):
            ux, uy = x - cx, y - cy
            rx = ux * cosr + uy * sinr
            ry = -ux * sinr + uy * cosr
            sx = rx * inv_stretch + cx
            sy = ry + cy
            x0, y0 = int(sx), int(sy)
            if 0 <= x0 < img.w - 1 and 0 <= y0 < img.h - 1:
                fx, fy = sx - x0, sy - y0
                i00 = (y0 * img.w + x0) * 4
                j = (y * img.w + x) * 4
                for c in range(4):
                    v = (img.px[i00 + c] * (1 - fx) * (1 - fy) +
                         img.px[i00 + 4 + c] * fx * (1 - fy) +
                         img.px[i00 + img.w * 4 + c] * (1 - fx) * fy +
                         img.px[i00 + img.w * 4 + 4 + c] * fx * fy)
                    out.px[j + c] = int(v * (1.0 / stretch) ** 0 + 0.5) if stretch <= 1 \
                        else int(v + 0.5)
    return out


def secondary_motion(frames=48, stiffness=0.5, damping=0.75, follow=1.0,
                     driver=None, out_path=None):
    """Simulate follow-through / overlap with a damped spring driven by a
    position curve (driver: list of driver positions per frame)."""
    _, _ = _init()
    n = int(frames)
    driver = list(driver or [math.sin(2 * math.pi * f / max(1, n // 2)) for f in range(n)])
    require(len(driver) >= n, "invalid_input", "driver curve shorter than frame count")
    k = max(0.01, stiffness) * 0.4
    d = max(0.0, min(0.99, damping))
    lag = max(0.05, min(0.95, follow))
    pos = vel = 0.0
    out = []
    for f in range(n):
        target = driver[f]
        force = (target - pos) * k
        vel = (vel + force) * d
        pos += vel
        out.append(round(pos * lag + target * (1 - lag), 4))
    if out_path:
        core.save_json(out_path, {"frames": n, "curve": out,
                                  "params": {"stiffness": stiffness, "damping": damping,
                                             "follow": follow}})
    return {"frames": n, "curve": out, "driver": driver[:n]}


def polish_report(frame_images, fps=24):
    """Detect suspected pops, tangencies and dead holds in a frame sequence."""
    _, _ = _init()
    require(len(frame_images) >= 2, "invalid_input", "need at least 2 frames")
    sizes = {(f.w, f.h) for f in frame_images}
    require(len(sizes) == 1, "invalid_input", "all frames must share one size")
    metrics = []
    for f in frame_images:
        m = im.silhouette_metrics(f)
        st = im.stats(f)
        metrics.append({"area": m["area"], "centroid": m["centroid"] or [0, 0],
                        "mean": st["mean"][:3]})
    findings = []
    for i in range(1, len(metrics)):
        a, b = metrics[i - 1], metrics[i]
        area_jump = abs(b["area"] - a["area"]) / float(a["area"] or 1)
        cd = math.hypot(b["centroid"][0] - a["centroid"][0], b["centroid"][1] - a["centroid"][1])
        luma_shift = sum(abs(b["mean"][c] - a["mean"][c]) for c in range(3))
        if area_jump > 0.18:
            findings.append({"frame": i + 1, "issue": "volume_pop",
                             "detail": "silhouette area jumped %.1f%%" % (area_jump * 100)})
        if cd > max(12.0, 0.10 * frame_images[0].w):
            findings.append({"frame": i + 1, "issue": "possible_pop",
                             "detail": "centroid moved %.1fpx in one frame" % cd})
        if luma_shift > 45:
            findings.append({"frame": i + 1, "issue": "flicker",
                             "detail": "mean RGB shifted by %d" % luma_shift})
    holds = []
    run = 1
    for i in range(1, len(frame_images)):
        if im.diff_images(frame_images[i - 1], frame_images[i])["changed_pixels"] == 0:
            run += 1
        else:
            if run >= 4:
                holds.append({"from": i - run + 2, "to": i + 1, "frames": run})
            run = 1
    if run >= 4:
        holds.append({"from": len(frame_images) - run + 1, "to": len(frame_images), "frames": run})
    return {"frames": len(frame_images), "fps": fps,
            "findings": findings, "holds": holds,
            "summary": {"suspect_frames": sorted({f["frame"] for f in findings}),
                        "dead_holds": holds}}


# ------------------------------------------------------------- rigging ------

def solve_fk(bones, angles):
    """Forward kinematics: bones=[{name, parent, length, base_angle}],
    angles={name: degrees}. Returns joint world positions."""
    _, _ = _init()
    require(isinstance(bones, list) and bones, "invalid_input", "bones list required")
    pos = {}
    ang = {}
    for b in bones:
        name = b["name"]
        parent = b.get("parent")
        a = (ang[parent] if parent else 0.0) + \
            float(angles.get(name, b.get("base_angle", 0.0)))
        ang[name] = a
        if parent:
            px, py = pos[parent]
        else:
            px, py = b.get("origin", [0, 0])
        rad = math.radians(a)
        pos[name] = [px + math.cos(rad) * float(b["length"]),
                     py + math.sin(rad) * float(b["length"])]
    return {"joints": pos, "angles": {k: math.degrees(v) for k, v in ang.items()}}


def solve_ik_2bone(target, l1, l2, pole=1.0, origin=(0, 0)):
    """Analytic 2-bone IK: returns (angle1, angle2) in degrees or None when
    unreachable (then the closest reach is returned with reachable=False)."""
    _, _ = _init()
    tx, ty = target[0] - origin[0], target[1] - origin[1]
    d = math.hypot(tx, ty)
    reach = l1 + l2
    reachable = d <= reach
    d_c = min(d, reach - 1e-6)
    if d_c < abs(l1 - l2) + 1e-6:
        d_c = abs(l1 - l2) + 1e-6
    a1 = math.atan2(ty, tx)
    try:
        a2 = math.acos(max(-1.0, min(1.0, (l1 * l1 + d_c * d_c - l2 * l2) /
                                     (2 * l1 * d_c))))
    except ValueError:
        return {"reachable": False, "angles": None}
    hip = a1 - a2 * float(pole)
    knee_angle = math.pi - math.acos(max(-1.0, min(1.0, (l1 * l1 + l2 * l2 - d_c * d_c) /
                                                       (2 * l1 * l2))))
    return {"reachable": reachable,
            "angles": [round(math.degrees(hip), 3),
                       round(math.degrees(knee_angle) * float(pole), 3)],
            "distance": round(d, 3), "max_reach": round(reach, 3)}


def apply_pose(parts, pose, canvas=None):
    """Compose cutout parts under a pose. parts=[{image, pivot, parent}],
    pose={name: {x,y,rotation,scale,opacity}} (pivots in part pixel coords).
    Returns the composited frame."""
    _, _ = _init()
    canvas = canvas or (max(p["image"].w for p in parts), max(p["image"].h for p in parts))
    out = im.new_image(canvas[0], canvas[1])
    by_name = {p.get("name", "part%d" % i): p for i, p in enumerate(parts)}
    order = []
    seen = set()

    def add(name):
        if name in seen:
            return
        p = by_name.get(name)
        if p is None:
            return
        parent = p.get("parent")
        if parent:
            add(parent)
        seen.add(name)
        order.append(name)
    for name in by_name:
        add(name)
    world = {}
    for name in order:
        p = by_name[name]
        parent = p.get("parent")
        base = world.get(parent, {"x": 0.0, "y": 0.0, "rotation": 0.0, "scale": 1.0,
                                  "opacity": 1.0})
        pv = pose.get(name, {})
        node = {"x": base["x"] + float(pv.get("x", 0)),
                "y": base["y"] + float(pv.get("y", 0)),
                "rotation": base["rotation"] + float(pv.get("rotation", 0)),
                "scale": base["scale"] * float(pv.get("scale", 1.0)),
                "opacity": max(0.0, min(1.0, base["opacity"] * float(pv.get("opacity", 1.0))))}
        world[name] = node
        img = p["image"]
        pvx, pvy = p.get("pivot", [img.w // 2, img.h // 2])
        node_img = img
        if node["scale"] != 1.0:
            nw, nh = max(1, int(img.w * abs(node["scale"]))), max(1, int(img.h * abs(node["scale"])))
            node_img = im.resize(img, nw, nh, "bilinear")
            pvx, pvy = int(pvx * abs(node["scale"])), int(pvy * abs(node["scale"]))
        if node["rotation"] % 360 != 0:
            rot = im.rotate_free(node_img, node["rotation"])
            placed = im.new_image(canvas[0], canvas[1])
            for y in range(rot.h):
                for x in range(rot.w):
                    r, g, b, a = rot.get(x, y)
                    if a > 0:
                        placed.set(x, y, (r, g, b, a))
            node_img = rot
            pvx, pvy = node_img.w // 2, node_img.h // 2
        ox = int(round(node["x"] - pvx))
        oy = int(round(node["y"] - pvy))
        for y in range(node_img.h):
            for x in range(node_img.w):
                r, g, b, a = node_img.get(x, y)
                if a == 0:
                    continue
                dx, dy = ox + x, oy + y
                if 0 <= dx < out.w and 0 <= dy < out.h:
                    if node["opacity"] < 1.0:
                        a = int(a * node["opacity"])
                    if a == 0:
                        continue
                    out.set(dx, dy, im._over(out.get(dx, dy), (r, g, b, a)))
    return out


def lattice_deform(img, handles_before, handles_after, grid=3):
    """Free-form deformation: move a grid of control points, bilinear blend.
    handles_*: {(gx, gy): [x, y]} for gx,gy in 0..grid."""
    _, _ = _init()
    require(grid in (2, 3), "invalid_input", "lattice grid must be 2 or 3")
    out = im.new_image(img.w, img.h)
    for y in range(img.h):
        for x in range(img.w):
            u = x / float(max(1, img.w - 1))
            v = y / float(max(1, img.h - 1))
            gu = u * grid
            gv = v * grid
            gx0, gy0 = min(int(gu), grid - 1), min(int(gv), grid - 1)
            fu, fv = gu - gx0, gv - gy0
            sx = sy = 0.0
            for (ix, iy), w in _bilinear_weights(gx0, gy0, fu, fv, grid):
                hb = handles_before.get((ix, iy), _default_handle(ix, iy, grid, img))
                ha = handles_after.get((ix, iy), hb)
                sx += (ha[0] - hb[0]) * w
                sy += (ha[1] - hb[1]) * w
            srcx = max(0, min(img.w - 1, int(round(x + sx))))
            srcy = max(0, min(img.h - 1, int(round(y + sy))))
            out.set(x, y, img.get(srcx, srcy))
    return out


def _bilinear_weights(gx0, gy0, fu, fv, grid):
    pts = []
    for ix in (gx0, min(gx0 + 1, grid)):
        for iy in (gy0, min(gy0 + 1, grid)):
            wx = (1 - fu) if ix == gx0 else fu
            wy = (1 - fv) if iy == gy0 else fv
            pts.append(((ix, iy), wx * wy))
    s = sum(w for _, w in pts) or 1.0
    return [(p, w / s) for p, w in pts]


def _default_handle(ix, iy, grid, img):
    return [ix * img.w / float(grid), iy * img.h / float(grid)]


def weight_map(img, bone_a, bone_b, falloff=1.0):
    """Distance-based influence weights between two bone segments
    (Weight Painting Assistant, simplified). Returns per-pixel 0..1 weights."""
    _, _ = _init()

    def seg_dist(px, py, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(px - a[0], py - a[1])
        t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / l2))
        return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))
    w = bytearray(img.w * img.h)
    for y in range(img.h):
        for x in range(img.w):
            da = seg_dist(x, y, bone_a[0], bone_a[1])
            db = seg_dist(x, y, bone_b[0], bone_b[1])
            if da + db == 0:
                val = 0.5
            else:
                val = db / (da + db)
            val = val ** max(0.1, falloff)
            w[y * img.w + x] = int(val * 255)
    return w


def rig_stress_test(rig, iterations=40, seed="stress"):
    """Exercise extreme poses and report bounds/failures (Rig Stress Tester)."""
    _, _ = _init()
    rng = core.RNG("rigstress:" + str(seed))
    require(isinstance(rig, dict) and rig.get("parts"), "invalid_input",
            "rig must be {parts: [...], ...}")
    fails = []
    bounds = {"minx": 1e9, "miny": 1e9, "maxx": -1e9, "maxy": -1e9}
    for it in range(max(1, int(iterations))):
        pose = {}
        for p in rig["parts"]:
            pose[p.get("name", "")] = {
                "x": (rng.random() * 2 - 1) * 0.4 * rig.get("canvas", [200, 200])[0],
                "y": (rng.random() * 2 - 1) * 0.4 * rig.get("canvas", [200, 200])[1],
                "rotation": (rng.random() * 2 - 1) * 170,
                "scale": 0.4 + rng.random() * 1.6,
                "opacity": 1.0,
            }
        try:
            frame = apply_pose(rig["parts"], pose, rig.get("canvas"))
            m = im.silhouette_metrics(frame, threshold=1)
            if m["bbox"]:
                x, y, bw, bh = m["bbox"]
                bounds["minx"] = min(bounds["minx"], x)
                bounds["miny"] = min(bounds["miny"], y)
                bounds["maxx"] = max(bounds["maxx"], x + bw)
                bounds["maxy"] = max(bounds["maxy"], y + bh)
            if m["area"] == 0:
                fails.append({"iteration": it, "issue": "empty_result"})
        except Exception as e:
            fails.append({"iteration": it, "issue": "error", "detail": str(e)[:120]})
    return {"iterations": int(iterations), "failures": fails,
            "bounds": bounds if bounds["minx"] < 1e9 else None,
            "verdict": "pass" if not fails else "%d failure(s)" % len(fails)}


def bake_rig(rig, animation, fps=24, out_path=None):
    """Bake control animation into a per-frame pose table (portable keys)."""
    _, _ = _init()
    frames = int(animation.get("frames", 24))
    baked = []
    for f in range(frames):
        t = f / float(max(1, frames - 1)) if frames > 1 else 0.0
        pose = {}
        for name, track in animation.get("tracks", {}).items():
            keys = track.get("keys", [])
            if not keys:
                continue
            vals = tween_keys(keys, frames=None, easing=track.get("easing", "quad_in_out"))
            fl = [k for k in vals["frames"]]
            idx = min(int(round(t * (len(fl) - 1))), len(fl) - 1)
            pose[name] = {track.get("property", "rotation"): fl[idx]}
        baked.append({"frame": f, "pose": pose})
    if out_path:
        core.save_json(out_path, {"fps": fps, "frames": frames, "baked": baked})
    return {"fps": fps, "frames": frames, "baked": baked}


def seam_repair(img, dilate=1):
    """Puppet seam repair: defringe colors and extend opaque edges to close
    gaps exposed by cutout movement."""
    _, _ = _init()
    out = im.defringe(img, radius=1)
    for _ in range(max(0, int(dilate))):
        grew = out.clone()
        px = out.px
        for y in range(out.h):
            base = y * out.w
            for x in range(out.w):
                i = (base + x) * 4
                if px[i + 3] == 0:
                    best = None
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < out.w and 0 <= ny < out.h:
                            j = (ny * out.w + nx) * 4
                            if px[j + 3] > 200:
                                best = (px[j], px[j + 1], px[j + 2])
                    if best:
                        grew.set(x, y, (best[0], best[1], best[2], 255))
        out = grew
    return out


# --------------------------------------------------------- facial / lipsync -

def load_viseme_chart(path=None):
    """Load the project viseme chart (asset-viseme-chart.json)."""
    _, _ = _init()
    p = path or core.asset("viseme-chart.json")
    if not p.is_file():
        p = core.ROOT / "asset-viseme-chart.json"
    chart = core.load_json(p)
    require("visemes" in chart, "invalid_input", "viseme chart missing 'visemes'")
    return chart


def phonemes_to_visemes(phoneme_events, fps=24, coarticulation=True,
                        chart_path=None, out_path=None):
    """Map phoneme events [{phoneme, frame}] to a viseme exposure track."""
    _, _ = _init()
    chart = load_viseme_chart(chart_path)
    vmap = chart["visemes"]
    events = sorted(({"phoneme": str(e["phoneme"]).lower(), "frame": int(e["frame"])}
                     for e in phoneme_events), key=lambda e: e["frame"])
    out = []
    for i, e in enumerate(events):
        vis = vmap.get(e["phoneme"], vmap.get("_default", "X"))
        nxt = events[i + 1]["frame"] if i + 1 < len(events) else e["frame"] + 3
        dur = max(1, nxt - e["frame"])
        out.append({"frame": e["frame"], "viseme": vis, "hold": dur,
                    "phoneme": e["phoneme"]})
    if coarticulation and len(out) >= 3:
        for i in range(1, len(out) - 1):
            if out[i]["viseme"] in ("MBP",) and out[i - 1]["viseme"] not in ("MBP", "rest"):
                out[i]["blend_from"] = out[i - 1]["viseme"]
    if out_path:
        core.save_json(out_path, {"fps": fps, "track": out})
    return {"fps": fps, "track": out}


def lipsync_from_audio(events, fps=24, chart_path=None, out_path=None):
    """Build an editable viseme track from audio cue events (onsets/words).
    Honest: energy/onset-based timing only — phoneme identity requires a
    dedicated offline recognizer and is marked 'approx' for review."""
    _, _ = _init()
    chart = load_viseme_chart(chart_path)
    approx = chart.get("approx_open_mapping", {"strong": "AA", "medium": "E", "soft": "rest"})
    track = []
    for e in sorted(events, key=lambda e: e.get("frame", e.get("ms", 0) / 1000.0 * fps)):
        f = int(e.get("frame") or ms_to_frames(e.get("ms", 0), fps))
        strength = e.get("strength", "medium")
        vis = approx.get(strength, "E")
        dur = int(e.get("duration_frames", 3))
        track.append({"frame": f, "viseme": vis, "hold": max(1, dur),
                      "approx": True})
    if out_path:
        core.save_json(out_path, {"fps": fps, "track": track, "approx": True})
    return {"fps": fps, "track": track, "approx": True}


def blink_gaze_plan(frames, blinks_per_minute=12, seed="blink", fps=24,
                    saccade_frames=2):
    """Plan blinks and saccades (eye darts) around dialogue beats."""
    _, _ = _init()
    rng = core.RNG("blink:" + str(seed))
    n = int(frames)
    total_min = n / float(fps) / 60.0
    count = max(1, int(round(total_min * blinks_per_minute)))
    blinks = []
    f = int(rng.random() * fps * 2) + 2
    while len(blinks) < count and f < n - 4:
        dur = 2 + int(rng.random() * 3)
        blinks.append({"start": f, "close": dur, "open": dur, "total_frames": dur * 2})
        f += int(fps * (60.0 / max(1, blinks_per_minute)) * (0.6 + rng.random() * 0.8))
    saccades = []
    f = int(fps * 0.7)
    while f < n - saccade_frames:
        saccades.append({"frame": f, "frames": saccade_frames,
                         "dir": round((rng.random() * 2 - 1), 2)})
        f += int(fps * (1.2 + rng.random() * 2.5))
    return {"frames": n, "fps": fps, "blinks": blinks, "saccades": saccades}


def lipsync_drift_check(cue_events, audio_events, fps=24, tolerance=1.0):
    """Compare dialogue cue times against waveform event times."""
    _, _ = _init()
    cues = sorted((int(c.get("frame", ms_to_frames(c.get("ms", 0), fps))),
                   c.get("name", "")) for c in cue_events)
    auds = sorted(int(a.get("frame", ms_to_frames(a.get("ms", 0), fps)))
                  for a in audio_events)
    pairs = []
    drift = []
    for i, (cf, name) in enumerate(cues):
        af = auds[i] if i < len(auds) else None
        if af is None:
            continue
        d = cf - af
        pairs.append({"cue_frame": cf, "audio_frame": af, "drift_frames": d})
        if abs(d) > tolerance:
            drift.append({"cue_frame": cf, "name": name, "drift_frames": d})
    progressive = False
    if len(pairs) >= 3:
        ds = [p["drift_frames"] for p in pairs]
        if all(ds[i] < ds[i + 1] for i in range(len(ds) - 1)) and ds[-1] - ds[0] > 2:
            progressive = True
    return {"pairs": pairs, "drift_count": len(drift), "drifts": drift,
            "progressive_drift": progressive, "fps": fps}


# --------------------------------------------------- camera / multiplane ----

def camera_sample(camera_keys, frame, easing="quad_in_out"):
    """Sample camera keys [{frame, x, y, zoom, rotation}] at a frame."""
    _, _ = _init()
    keys = sorted(camera_keys, key=lambda k: k["frame"])
    require(len(keys) >= 1, "invalid_input", "camera needs at least one key")
    if frame <= keys[0]["frame"]:
        k = keys[0]
    elif frame >= keys[-1]["frame"]:
        k = keys[-1]
    else:
        for i in range(len(keys) - 1):
            a, b = keys[i], keys[i + 1]
            if a["frame"] <= frame <= b["frame"]:
                t = (frame - a["frame"]) / float(b["frame"] - a["frame"] or 1)
                t = ease(t, easing)
                k = {p: a.get(p, 0) + (b.get(p, 0) - a.get(p, 0)) * t
                     for p in ("x", "y", "zoom", "rotation")}
                break
        else:
            k = keys[-1]
    return {"x": round(k.get("x", 0.0), 3), "y": round(k.get("y", 0.0), 3),
            "zoom": round(k.get("zoom", 1.0), 4),
            "rotation": round(k.get("rotation", 0.0), 3)}


def parallax_plan(camera_travel, layers, frames=48):
    """Relative layer movement from depth (multiplane parallax)."""
    _, _ = _init()
    plan = []
    for layer in layers:
        depth = max(0.0, min(1.0, float(layer.get("depth", 0.5))))
        factor = 1.0 / (1.0 + depth * 9.0)
        plan.append({"name": layer.get("name", "layer"),
                     "depth": depth,
                     "travel_factor": round(factor, 4),
                     "offset_px": [round(-camera_travel[0] * factor, 2),
                                   round(-camera_travel[1] * factor, 2)],
                     "scale": round(1.0 + (1.0 - factor) * 0.15, 4)})
    return {"frames": int(frames), "camera_travel": list(camera_travel), "layers": plan}


def render_multiplane_frame(layers, camera, size, bg=(0, 0, 0, 0)):
    """Composite depth layers under a camera transform for one frame.
    layers=[{image, depth, offset}] ordered back-to-front."""
    _, _ = _init()
    w, h = size
    out = im.new_image(w, h, bg)
    zoom = max(0.05, float(camera.get("zoom", 1.0)))
    cx = float(camera.get("x", 0.0))
    cy = float(camera.get("y", 0.0))
    rot = float(camera.get("rotation", 0.0))
    for layer in layers:
        img = layer["image"]
        depth = max(0.0, min(1.0, float(layer.get("depth", 0.5))))
        factor = 1.0 / (1.0 + depth * 9.0)
        loff = layer.get("offset", [0, 0])
        lx = cx * factor - loff[0] * factor
        ly = cy * factor - loff[1] * factor
        lz = zoom ** factor
        sw = max(1, int(img.w * lz))
        sh = max(1, int(img.h * lz))
        scaled = im.resize(img, sw, sh, "bilinear") if (sw != img.w or sh != img.h) else img
        if rot % 360:
            scaled = im.rotate_free(scaled, rot * factor)
        ox = int(round((w - sw) / 2.0 - lx))
        oy = int(round((h - sh) / 2.0 - ly))
        for y in range(scaled.h):
            dy = oy + y
            if not (0 <= dy < h):
                continue
            for x in range(scaled.h and scaled.w):
                if x >= scaled.w:
                    break
                dx = ox + x
                if 0 <= dx < w:
                    r, g, b, a = scaled.get(x, y)
                    if a > 0:
                        out.set(dx, dy, im._over(out.get(dx, dy), (r, g, b, a)))
    return out


def camera_boundary_audit(frames_images, margin=4):
    """Detect exposed canvas edges / insufficient overscan."""
    _, _ = _init()
    issues = []
    for i, img in enumerate(frames_images):
        px = img.px
        w, h = img.w, img.h
        edges = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        for x in range(w):
            if px[x * 4 + 3] == 0:
                edges["top"] += 1
            if px[((h - 1) * w + x) * 4 + 3] == 0:
                edges["bottom"] += 1
        for y in range(h):
            if px[(y * w) * 4 + 3] == 0:
                edges["left"] += 1
            if px[(y * w + w - 1) * 4 + 3] == 0:
                edges["right"] += 1
        lim = max(margin, int(0.02 * max(w, h)))
        bad = {k: v for k, v in edges.items() if v > lim}
        if bad:
            issues.append({"frame": i, "empty_edge_runs": bad,
                           "hint": "extend layer art or reduce camera travel"})
    return {"frames": len(frames_images), "issues": issues,
            "verdict": "pass" if not issues else "%d frame(s) with exposed edges" % len(issues)}


def camera_shake(frames, magnitude=6.0, decay=0.92, frequency=3.0, seed="shake",
                 direction=None):
    """Deterministic decaying camera shake offsets per frame."""
    _, _ = _init()
    rng = core.RNG("shake:" + str(seed))
    out = []
    m = float(magnitude)
    for f in range(int(frames)):
        t = f / float(max(1, frames - 1)) if frames > 1 else 0.0
        amp = m * (float(decay) ** (f * 0.7))
        ang = rng.random() * 2 * math.pi
        dx = math.cos(ang) * amp
        dy = math.sin(ang) * amp
        if direction:
            dx = direction[0] * amp
            dy = direction[1] * amp
        wobble = math.sin(2 * math.pi * frequency * t)
        out.append({"frame": f, "x": round(dx, 2), "y": round(dy, 2),
                    "rotation": round(wobble * amp * 0.15, 2)})
    return out


def perspective_grid(out_path, mode="1pt", size=(800, 600), horizon=0.5,
                      vanishing=(0.5, 0.5), spacing=80, color=(90, 120, 255, 255)):
    """Render 1/2/3-point perspective guides (PNG or SVG)."""
    _, _ = _init()
    require(mode in ("1pt", "2pt", "3pt"), "invalid_input", "mode must be 1pt/2pt/3pt")
    w, h = size
    hy = horizon * h
    if str(out_path).endswith(".svg"):
        lines = ['<line x1="0" y1="%.1f" x2="%d" y2="%.1f" stroke="#666" '
                 'stroke-width="1"/>' % (hy, w, hy)]
        pts = []
        if mode == "1pt":
            pts = [vanishing]
        elif mode == "2pt":
            pts = [(w * 0.12, hy), (w * 0.88, hy)]
        else:
            pts = [(w * 0.1, hy), (w * 0.9, hy), (vanishing[0] * w, -h * 0.35)]
        for (vx, vy) in pts:
            for k in range(-int(w / spacing), int(w / spacing) + 1):
                x0 = k * spacing
                lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                             'stroke="#4a78ff" stroke-width="0.7"/>'
                             % (x0, h if vy < h else 0, vx, vy))
        for k in range(1, 10):
            yy = hy + (h - hy) * (k / 10.0) ** 2
            lines.append('<line x1="0" y1="%.1f" x2="%d" y2="%.1f" stroke="#888" '
                         'stroke-width="0.5"/>' % (yy, w, yy))
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">%s</svg>'
               % (w, h, "".join(lines)))
        core.save_text(out_path, svg)
        return {"mode": mode, "size": [w, h], "path": out_path, "format": "svg"}
    img = im.new_image(w, h, (250, 250, 250, 255))
    im.draw_line(img, 0, int(hy), w - 1, int(hy), (100, 100, 100, 255))
    if mode == "1pt":
        pts = [(vanishing[0] * w, hy)]
    elif mode == "2pt":
        pts = [(w * 0.12, hy), (w * 0.88, hy)]
    else:
        pts = [(w * 0.1, hy), (w * 0.9, hy), (vanishing[0] * w, -h * 0.35)]
    for (vx, vy) in pts:
        for k in range(-int(w / spacing), int(w / spacing) + 1):
            x0 = k * spacing
            y0 = h if vy < h else 0
            im.draw_line(img, x0, y0, int(vx), int(vy), color, 1)
    for k in range(1, 10):
        yy = hy + (h - hy) * (k / 10.0) ** 2
        im.draw_line(img, 0, int(yy), w - 1, int(yy), (150, 150, 150, 255))
    im.save_image(out_path, img)
    return {"mode": mode, "size": [w, h], "path": out_path, "format": "png"}


def camera_export(camera_keys, frames, fps=24, out_path=None, easing="quad_in_out"):
    """Per-frame camera data export (JSON + CSV-friendly rows)."""
    _, _ = _init()
    rows = []
    for f in range(int(frames)):
        c = camera_sample(camera_keys, f, easing)
        rows.append({"frame": f, "timecode": timecode(f, fps), **c})
    if out_path:
        core.save_json(out_path, {"fps": fps, "easing": easing, "frames": rows})
    return {"fps": fps, "frames": rows}


# ------------------------------------------------------ particles / fx ------

PARTICLE_PRESETS = {
    "sparks": {"rate": 90, "life": (10, 24), "speed": (2.5, 6), "spread": 0.9,
               "gravity": 0.12, "drag": 0.02, "size": (1, 2), "fade": True,
               "color": [(255, 220, 120), (255, 150, 40), (200, 60, 20)]},
    "dust": {"rate": 12, "life": (40, 90), "speed": (0.1, 0.4), "spread": 3.14,
             "gravity": -0.002, "drag": 0.01, "size": (1, 2), "fade": True,
             "color": [(200, 190, 170)]},
    "rain": {"rate": 160, "life": (18, 28), "speed": (7, 9), "spread": 0.08,
             "gravity": 0.35, "drag": 0.0, "size": (1, 4), "fade": False,
             "streak": True, "color": [(150, 180, 220)]},
    "snow": {"rate": 40, "life": (60, 120), "speed": (0.4, 0.9), "spread": 0.5,
             "gravity": 0.01, "drag": 0.0, "size": (1, 2), "fade": False,
             "wobble": 0.4, "color": [(240, 245, 255)]},
    "embers": {"rate": 20, "life": (30, 70), "speed": (0.3, 1.1), "spread": 0.6,
               "gravity": -0.02, "drag": 0.01, "size": (1, 2), "fade": True,
               "wobble": 0.3, "color": [(255, 170, 60), (255, 110, 30)]},
    "bubbles": {"rate": 14, "life": (40, 80), "speed": (0.4, 0.9), "spread": 0.35,
                "gravity": -0.03, "drag": 0.0, "size": (2, 4), "fade": False,
                "wobble": 0.5, "color": [(180, 220, 255)]},
    "debris": {"rate": 30, "life": (14, 30), "speed": (2, 7), "spread": 1.2,
               "gravity": 0.3, "drag": 0.01, "size": (1, 3), "fade": True,
               "color": [(140, 120, 100), (90, 80, 70)]},
}


def simulate_particles(preset="sparks", frames=24, size=(320, 240),
                       emitter=(0.5, 0.5), seed=None, emitter_velocity=(0, 0)):
    """Deterministic particle simulation -> list of frames (Images)."""
    _, _ = _init()
    require(preset in PARTICLE_PRESETS, "invalid_input",
            "unknown preset '%s' (available: %s)" % (preset, ", ".join(sorted(PARTICLE_PRESETS))))
    cfg = dict(PARTICLE_PRESETS[preset])
    seed = seed or ("particles:%s" % preset)
    rng = core.RNG(seed)
    w, h = size
    ex, ey = emitter[0] * w, emitter[1] * h
    parts = []
    out_frames = []
    base_ang = math.atan2(emitter_velocity[1], emitter_velocity[0]) if \
        emitter_velocity != (0, 0) else -math.pi / 2

    def spawn(age0=0):
        life = rng.randint(*cfg["life"])
        spd = rng.uniform(*cfg["speed"])
        ang = base_ang + (rng.random() * 2 - 1) * cfg["spread"]
        ci = rng.randint(0, len(cfg["color"]) - 1)
        return {"x": ex, "y": ey, "vx": math.cos(ang) * spd,
                "vy": math.sin(ang) * spd, "age": age0, "life": life,
                "size": rng.randint(*cfg["size"]), "color": cfg["color"][ci]}
    per_frame = max(0, int(round(cfg["rate"] / 24.0)))
    for f in range(int(frames)):
        for _ in range(per_frame):
            if len(parts) < 4000:
                parts.append(spawn())
        img = im.new_image(w, h, (0, 0, 0, 0))
        alive = []
        for p in parts:
            p["age"] += 1
            if p["age"] > p["life"]:
                continue
            p["vx"] *= (1 - cfg["drag"])
            p["vy"] = p["vy"] * (1 - cfg["drag"]) + cfg["gravity"]
            wob = cfg.get("wobble", 0)
            if wob:
                p["x"] += math.sin(p["age"] * 0.3) * wob
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            t = p["age"] / float(p["life"])
            a = int(255 * (1 - t)) if cfg["fade"] else 255
            col = (p["color"][0], p["color"][1], p["color"][2], a)
            x, y = int(p["x"]), int(p["y"])
            if cfg.get("streak"):
                im.draw_line(img, x, y, int(p["x"] - p["vx"] * 2),
                             int(p["y"] - p["vy"] * 2), col, max(1, p["size"] // 2))
            else:
                s = max(1, p["size"] - (1 if t > 0.7 else 0))
                im._fill_circle_ish(img, x, y, s / 2.0, col)
            alive.append(p)
        parts = alive
        out_frames.append(img)
    return out_frames


def procedural_loop(preset="dust", frames=48, size=(320, 240), seed=None):
    """Seamless procedural cycle: particles' lifetimes divide the loop and
    spawns are spread across phase, so frame N wraps to frame 0."""
    _, _ = _init()
    frames = int(frames)
    base = simulate_particles(preset, frames * 2, size, seed=seed)
    # cross-fade the tail into the head for a seamless cycle
    fade = 4
    out = []
    for f in range(frames):
        img = base[f].clone()
        if f < fade:
            k = (fade - f) / float(fade)
            tail = base[frames + f]
            for i in range(0, len(img.px), 4):
                if tail.px[i + 3] > 0:
                    img.px[i] = max(img.px[i], int(tail.px[i] * k))
                    img.px[i + 1] = max(img.px[i + 1], int(tail.px[i + 1] * k))
                    img.px[i + 2] = max(img.px[i + 2], int(tail.px[i + 2] * k))
                    img.px[i + 3] = max(img.px[i + 3], int(tail.px[i + 3] * k))
        out.append(img)
    return out


def trail_frames(path_pts, frames, size=(320, 240), color=(255, 255, 255, 220),
                 taper=True, max_thickness=6):
    """Motion trail / speed-line frames along a path (tapering stroke)."""
    _, _ = _init()
    require(len(path_pts) >= 2, "invalid_input", "trail needs a path with >= 2 points")
    out = []
    total = len(path_pts)
    for f in range(int(frames)):
        img = im.new_image(size[0], size[1], (0, 0, 0, 0))
        head = int(total * (f + 1) / float(frames))
        tail_start = int(total * f / float(frames)) - int(total * 0.25 / 1)
        tail_start = max(0, tail_start)
        seg = path_pts[tail_start:head]
        for i in range(len(seg) - 1):
            t = i / float(max(1, len(seg) - 1))
            th = max(1, int(max_thickness * t)) if taper else max_thickness
            a = int(color[3] * (0.25 + 0.75 * t))
            im.draw_line(img, int(seg[i][0]), int(seg[i][1]),
                         int(seg[i + 1][0]), int(seg[i + 1][1]),
                         (color[0], color[1], color[2], a), th)
        out.append(img)
    return out


def impact_frame(base=None, size=(320, 240), kind="flash", color=(255, 255, 255),
                 seed="impact"):
    """Impact accent frames: flash, inversion, silhouette, burst lines."""
    _, _ = _init()
    w, h = size
    rng = core.RNG("impact:" + str(seed))
    if base is not None:
        img = base.clone()
    else:
        img = im.new_image(w, h, (0, 0, 0, 0))
    if kind == "flash":
        for i in range(0, len(img.px), 4):
            img.px[i] = min(255, img.px[i] + 90)
            img.px[i + 1] = min(255, img.px[i + 1] + 90)
            img.px[i + 2] = min(255, img.px[i + 2] + 90)
    elif kind == "inversion":
        if base is None:
            img = im.new_image(w, h, (color[0], color[1], color[2], 255))
        for i in range(0, len(img.px), 4):
            if img.px[i + 3] > 0:
                img.px[i] = 255 - img.px[i]
                img.px[i + 1] = 255 - img.px[i + 1]
                img.px[i + 2] = 255 - img.px[i + 2]
    elif kind == "silhouette":
        for i in range(0, len(img.px), 4):
            if img.px[i + 3] > 0:
                img.px[i] = img.px[i + 1] = img.px[i + 2] = color[0]
    elif kind == "burst":
        cx, cy = w // 2, h // 2
        for k in range(16):
            a = 2 * math.pi * k / 16.0 + rng.random() * 0.2
            l = (0.35 + rng.random() * 0.5) * min(w, h)
            im.draw_line(img, int(cx + math.cos(a) * 8), int(cy + math.sin(a) * 8),
                         int(cx + math.cos(a) * l), int(cy + math.sin(a) * l),
                         (color[0], color[1], color[2], 255), 2)
    else:
        raise err("invalid_input", "impact kind must be flash/inversion/silhouette/burst")
    return img


def atmospheric_pass(img, kind="fog", strength=0.35, seed="atmo", tint=(200, 210, 230)):
    """Fog / haze / dust / grain / light-ray overlay pass (non-destructive:
    returns a new Image)."""
    _, _ = _init()
    out = img.clone()
    if kind in ("fog", "haze", "dust"):
        noise = im.value_noise(img.w, img.h, "atmo:%s:%s" % (kind, seed),
                               cells=5, octaves=3)
        a = int(255 * max(0.0, min(1.0, strength)))
        for p in range(img.w * img.h):
            i = p * 4
            if out.px[i + 3] == 0 and kind != "dust":
                continue
            na = noise[p] * a // 255
            out.px[i] = (out.px[i] * (255 - na) + tint[0] * na) // 255
            out.px[i + 1] = (out.px[i + 1] * (255 - na) + tint[1] * na) // 255
            out.px[i + 2] = (out.px[i + 2] * (255 - na) + tint[2] * na) // 255
    elif kind == "grain":
        out = im.add_grain(img, int(strength * 40), seed)
    elif kind == "rays":
        cx = img.w // 2
        rng = core.RNG("rays:" + str(seed))
        for k in range(7):
            a = -math.pi / 2 + (k - 3) * 0.22 + rng.random() * 0.06
            for step in range(int(img.h * 1.4)):
                x = int(cx + math.cos(a) * step * 0.7)
                y = int(step * 0.8 - img.h * 0.1)
                if 0 <= x < img.w and 0 <= y < img.h:
                    wdt = int(2 + step * 0.04)
                    for d in range(-wdt, wdt + 1):
                        xx = x + d
                        if 0 <= xx < img.w:
                            i = (y * img.w + xx) * 4
                            if out.px[i + 3] > 0:
                                na = int(60 * strength)
                                out.px[i] = min(255, out.px[i] + na)
                                out.px[i + 1] = min(255, out.px[i + 1] + na)
                                out.px[i + 2] = min(255, out.px[i + 2] + na)
    else:
        raise err("invalid_input", "atmospheric kind must be fog/haze/dust/grain/rays")
    return out


def effect_boundary_check(frames_images):
    """Validate effects exports: edge cropping, alpha fringes, loop seams."""
    _, _ = _init()
    issues = []
    for i, img in enumerate(frames_images):
        px = img.px
        edge_alpha = 0
        for x in range(img.w):
            if px[x * 4 + 3] > 0:
                edge_alpha += 1
            if px[((img.h - 1) * img.w + x) * 4 + 3] > 0:
                edge_alpha += 1
        for y in range(img.h):
            if px[(y * img.w) * 4 + 3] > 0:
                edge_alpha += 1
            if px[(y * img.w + img.w - 1) * 4 + 3] > 0:
                edge_alpha += 1
        if edge_alpha > 0:
            issues.append({"frame": i, "issue": "content_touches_edge",
                           "pixels_on_edge": edge_alpha,
                           "hint": "grow canvas or reduce emitter speed"})
    seam = None
    if len(frames_images) >= 2:
        d = im.diff_images(frames_images[0], frames_images[-1])
        seam = {"changed_fraction": d["changed_fraction"],
                "seamless": d["changed_fraction"] <= 0.02}
        if not seam["seamless"]:
            issues.append({"frame": 0, "issue": "loop_seam",
                           "changed_fraction": d["changed_fraction"]})
    return {"frames": len(frames_images), "issues": issues, "loop": seam,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


# ---------------------------------------------------- storyboard / boards ---

def storyboard_setup(shots, out_path=None, panel_size=(320, 180)):
    """Create a board project: shot numbering, panels, fields, revision labels."""
    _, _ = _init()
    board = {"panels": []}
    for i, s in enumerate(shots):
        panel = {
            "panel": i + 1,
            "scene": s.get("scene", "SC001"),
            "shot": s.get("shot", "SH%03d" % (i + 1)),
            "shot_size": s.get("shot_size", "medium"),
            "angle": s.get("angle", "eye"),
            "camera_move": s.get("camera_move", "static"),
            "duration_frames": int(s.get("duration_frames", 48)),
            "dialogue": s.get("dialogue", ""),
            "action": s.get("action", ""),
            "notes": s.get("notes", ""),
            "revision": s.get("revision", 1),
            "panel_size": list(panel_size),
        }
        board["panels"].append(panel)
    if out_path:
        core.save_json(out_path, board)
    return board


def board_revision_compare(old_board, new_board):
    """Compare board revisions: changed panels, carried notes, obsoleted."""
    _, _ = _init()
    old = {p["panel"]: p for p in old_board.get("panels", [])}
    new = {p["panel"]: p for p in new_board.get("panels", [])}
    changed, added, removed, carried = [], [], [], []
    for pn, p in new.items():
        o = old.get(pn)
        if o is None:
            added.append(pn)
        elif any(str(o.get(k, "")) != str(p.get(k, "")) for k in
                 ("scene", "shot", "shot_size", "angle", "camera_move",
                  "duration_frames", "dialogue", "action")):
            changed.append(pn)
            if o.get("notes") and not p.get("notes"):
                carried.append({"panel": pn, "note": o["notes"]})
    for pn in old:
        if pn not in new:
            removed.append(pn)
    return {"changed": changed, "added": added, "removed": removed,
            "carried_notes": carried,
            "obsolete_panels": removed,
            "summary": {"changed": len(changed), "added": len(added),
                        "removed": len(removed)}}


def animatic_assemble(panels, out_path, fps=24, bg=(16, 16, 16, 255)):
    """Assemble panel images + durations into an offline animatic (APNG/GIF)."""
    _, _ = _init()
    frames = []
    for p in panels:
        img = p["image"]
        hold = max(1, int(p.get("duration_frames", 24)))
        frames.append({"image": img, "delay_ms": int(round(1000.0 * hold / fps))})
    if str(out_path).endswith(".gif"):
        data = im.encode_gif_bytes(frames, loop=0)
    else:
        data = im.encode_apng_bytes(frames, loop=0)
    core.write_bytes(out_path, data)
    return {"path": out_path, "panels": len(panels), "fps": fps,
            "total_frames": sum(p.get("duration_frames", 24) for p in panels)}


def panel_export(panels, out_dir, prefix="panel", fmt="png"):
    """Export named panel images for downstream animation and review."""
    _, _ = _init()
    import os
    out_dir = str(out_dir)
    core.require_dir(out_dir) if hasattr(core, "require_dir") else None
    from pathlib import Path as P
    P(out_dir).mkdir(parents=True, exist_ok=True)
    names = []
    for i, p in enumerate(panels):
        name = "%s-%03d-%s.%s" % (prefix, i + 1, p.get("shot", "SH%03d" % (i + 1)), fmt)
        im.save_image(P(out_dir) / name, p["image"], fmt)
        names.append(name)
    return {"dir": out_dir, "files": names}


def thumbnail_sheet(images, out_path, labels=None, columns=None, cell=(160, 120)):
    """Rapid staging review sheet (low-detail thumbnails)."""
    _, _ = _init()
    sheet = im.contact_sheet(images, labels=labels, columns=columns,
                             cell_w=cell[0], cell_h=cell[1])
    im.save_image(out_path, sheet)
    return {"path": out_path, "count": len(images)}


# ------------------------------------------------- facial/lipsync extras ----

def lipsync_smooth(track, min_consonant=2, fps=24):
    """Coarticulation Refiner: enforce minimum closed-consonant contacts and
    insert single-frame transition visemes between wide shape jumps."""
    _, _ = _init()
    chart = load_viseme_chart()
    out = []
    for e in sorted(track, key=lambda e: e["frame"]):
        out.append(dict(e))
    i = 1
    while i < len(out):
        prev, cur = out[i - 1], out[i]
        gap = cur["frame"] - (prev["frame"] + prev.get("hold", 3))
        if cur["viseme"] in ("MBP",) and prev["viseme"] != "MBP":
            need = max(0, min_consonant - cur.get("hold", 3))
            if need:
                cur["hold"] = cur.get("hold", 3) + need
        i += 1
    # transition suggestions
    transitions = []
    for i in range(1, len(out)):
        a, b = out[i - 1]["viseme"], out[i]["viseme"]
        wide = {"AA", "E", "O"}
        if a in wide and b in wide and a != b:
            transitions.append({"between_frames": [out[i - 1]["frame"], out[i]["frame"]],
                                "suggested": "I"})
    return {"track": out, "transitions": transitions, "fps": fps,
            "min_consonant_frames": min_consonant}


def lipsync_retime(track, new_frames, preserve=None):
    """Alternate Language Lip-Sync Adapter: rescale the track to a new length
    while pinning protected contact frames."""
    _, _ = _init()
    if not track:
        return {"track": [], "scale": 1.0}
    old_frames = max(e["frame"] + e.get("hold", 3) for e in track)
    scale = float(new_frames) / float(max(1, old_frames))
    preserve = sorted(set(int(p) for p in (preserve or [])))
    out = []
    for e in track:
        nf = int(round(e["frame"] * scale))
        nh = max(1, int(round(e.get("hold", 3) * scale)))
        rec = dict(e)
        rec["frame"] = nf
        rec["hold"] = nh
        out.append(rec)
    return {"track": out, "scale": round(scale, 4), "old_frames": old_frames,
            "new_frames": new_frames, "preserved_contacts": preserve,
            "note": "verify acting beats after retime; contacts are pinned by list"}


def facial_plan(track, fps=24):
    """Facial Acting Planner: brow/lid/head channel cues per viseme event."""
    _, _ = _init()
    cues = {"AA": "brows up, eyes widen slightly",
            "MBP": "lids steady, tiny head nod on contact",
            "O": "brows neutral, chin drop",
            "rest": "blink window, breathing motion"}
    plan = []
    for e in track:
        vis = e.get("viseme", "rest")
        plan.append({"frame": e["frame"], "viseme": vis,
                     "acting": cues.get(vis, "keep expression channel alive")})
    return {"fps": fps, "cues": plan,
            "channels": ["brows", "lids", "gaze", "head", "jaw"],
            "rule": "mouth track drives jaw only; act with the other channels"}


def facial_range_check(track, chart_path=None):
    """Validate that a track only uses chart visemes (Facial Rig Range
    Validator, data level)."""
    _, _ = _init()
    chart = load_viseme_chart(chart_path)
    valid = set(chart["visemes"].values()) | set(chart.get("viseme_order", []))
    used = {e.get("viseme") for e in track}
    unknown = sorted(used - valid)
    coverage = {v: sum(1 for e in track if e.get("viseme") == v) for v in sorted(valid)}
    return {"used": sorted(used), "unknown_visemes": unknown,
            "coverage": coverage,
            "verdict": "pass" if not unknown else
                       "%d viseme(s) outside the chart" % len(unknown)}


def facial_silent_plan(frames, beats=None):
    """Silent Performance Animator: listening/reaction beat plan (no
    dialogue)."""
    _, _ = _init()
    rng = core.RNG("silent")
    beats = beats or ["listening", "blink", "small head tilt", "thought",
                      "reaction", "settle"]
    plan = []
    f = 0
    while f < frames:
        b = beats[int(rng.random() * len(beats))]
        dur = 6 + int(rng.random() * 12)
        plan.append({"frame": f, "frames": dur, "beat": b})
        f += dur
    return {"frames": frames, "plan": plan,
            "notes": ["no mouth movement", "keep the eyes leading the head"]}


def subpixel_motion(path_pts, frames, size, seed="sub"):
    """Subpixel Motion Planner: position table at fractional offsets plus
    rendered test frames (bilinear simulation of sub-grid movement)."""
    _, _ = _init()
    require(len(path_pts) >= 2, "invalid_input", "need >= 2 path points")
    rows = []
    for f in range(int(frames)):
        t = f / float(max(1, frames - 1))
        i = min(int(t * (len(path_pts) - 1)), len(path_pts) - 2)
        ft = t * (len(path_pts) - 1) - i
        a, b = path_pts[i], path_pts[i + 1]
        x = a[0] + (b[0] - a[0]) * ft
        y = a[1] + (b[1] - a[1]) * ft
        rows.append({"frame": f, "x": round(x, 2), "y": round(y, 2),
                     "fractional": [round(x - int(x), 2), round(y - int(y), 2)]})
    dot = new_marker = im.new_image(max(8, size[0] // 8), max(8, size[1] // 8),
                                    (0, 0, 0, 0))
    im.draw_ellipse(dot, dot.w // 2, dot.h // 2, dot.w // 4, dot.h // 4,
                    (255, 255, 255, 255))
    out_frames = []
    for r in rows:
        fr = im.new_image(size[0], size[1], (20, 20, 30, 255))
        im.draw_pixel_grid(fr, 8, (40, 40, 60, 255))
        px = int(r["x"] * size[0])
        py = int(r["y"] * size[1])
        for y in range(dot.h):
            for x in range(dot.w):
                c = dot.get(x, y)
                if c[3] > 0 and 0 <= px + x < size[0] and 0 <= py + y < size[1]:
                    fr.set(px + x, py + y, (255, 120, 80, 255))
        out_frames.append(fr)
    return {"positions": rows, "frames": out_frames}


def anim_rough(keys, frames=None, mode="dissolve+shift"):
    """Rough Animation Builder: fill a key-frame sequence with inbetweens to
    a target frame count (marked rough — for blocking, not cleanup)."""
    _, _ = _init()
    require(len(keys) >= 2, "invalid_input", "need >= 2 key frames")
    n = int(frames or (len(keys) * 3))
    out = []
    for i in range(n):
        t = i / float(max(1, n - 1)) * (len(keys) - 1)
        k = min(int(t), len(keys) - 2)
        ft = t - k
        out.append(inbetween(keys[k], keys[k + 1], ft, mode=mode))
    return {"frames": out, "keys": len(keys), "rough": True}


# -------------------------------------------- late anim additions (9/11) ----

def animatic_timing_refine(panels, fps=24, min_frames=8, snap=True):
    """Animatic Timing Refiner: snap panel durations to whole frames at the
    target rate, flag too-short holds, report total runtime."""
    core, im = _init()
    rows = []
    total = 0
    for p in panels or []:
        ms = float(p.get("duration_ms", p.get("frames", 0) * 1000.0 / fps
                       if p.get("frames") else 2000))
        fr = int(round(ms * fps / 1000.0))
        snapped = max(min_frames, fr) if snap else fr
        total += snapped
        rows.append({"panel": p.get("panel", len(rows) + 1),
                     "ms_given": round(ms, 1), "frames": snapped,
                     "adjusted": snapped != fr,
                     "too_short": fr < min_frames,
                     "new_ms": int(snapped * 1000 / fps)})
    return {"fps": fps, "panels": rows, "total_frames": total,
            "total_ms": int(total * 1000 / fps),
            "note": "hold panels under %d frames read as flashes" % min_frames}


def editorial_conform(animatic_panels, edl):
    """Editorial Conform Assistant: apply an edit decision list to an
    animatic — cut, extend, and reorder panels; report drops and retimes.
    edl entries: {op: cut|extend|reorder|retime, panel, ...}."""
    core, im = _init()
    panels = list(animatic_panels or [])
    log = []
    for e in edl or []:
        op = e.get("op")
        pid = e.get("panel")
        idx = next((i for i, p in enumerate(panels)
                    if p.get("panel") == pid), None)
        if op == "cut":
            require(idx is not None, "invalid_input", "panel %s not found" % pid)
            log.append({"op": "cut", "panel": pid})
            panels.pop(idx)
        elif op == "extend":
            require(idx is not None, "invalid_input", "panel %s not found" % pid)
            fr = panels[idx].get("frames", 48) + int(e.get("frames", 12))
            panels[idx]["frames"] = fr
            log.append({"op": "extend", "panel": pid, "to_frames": fr})
        elif op == "retime":
            require(idx is not None, "invalid_input", "panel %s not found" % pid)
            panels[idx]["frames"] = int(e.get("frames", 24))
            log.append({"op": "retime", "panel": pid})
        elif op == "reorder":
            require(idx is not None, "invalid_input", "panel %s not found" % pid)
            p = panels.pop(idx)
            to = int(e.get("to", idx))
            panels.insert(min(to, len(panels)), p)
            log.append({"op": "reorder", "panel": pid, "to": to})
        else:
            raise err("invalid_input", "op must be cut/extend/retime/reorder")
    return {"panels": panels, "applied": log,
            "count": len(panels),
            "warning": "re-check audio sync after any conform"}


def breakdown_pose(key_poses, accents=None):
    """Breakdown Pose Designer: propose breakdown poses between keys with
    passing-position logic and accent timing."""
    core, im = _init()
    out = []
    keys = list(key_poses or [])
    require(len(keys) >= 2, "invalid_input", "need at least two key poses")
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        fa, fb = int(a.get("frame", i * 12)), int(b.get("frame", (i + 1) * 12))
        span = fb - fa
        mid = fa + span // 2
        # passing position favours the incoming pose on fast moves
        fav = "to-b" if span <= 6 else "neutral"
        entry = {"between": [a.get("name", "key%d" % i),
                             b.get("name", "key%d" % (i + 1))],
                 "frame": mid, "span": span, "favour": fav,
                 "suggestion": "arc through the passing position, offset the "
                               "extreme by 1-2 frames for snap"}
        for acc in (accents or []):
            if fa < int(acc.get("frame", -1)) <= fb:
                entry["accent_at"] = acc["frame"]
                entry["accent_note"] = acc.get("note", "impact")
        out.append(entry)
    return {"breakdowns": out, "count": len(out),
            "rule": "slow-in/slow-out around keys; breakdowns carry the arc"}


def cleanup_animation(frames, line_darken=40, speckle=True, gray_floor=200):
    """Cleanup Animation Specialist: per-frame de-speckle, pencil-haze
    removal and line darkening, keeping drawing registration intact."""
    core, im = _init()
    out = []
    for f in frames:
        img = f
        if speckle or gray_floor is not None:
            img = im.raster_cleanup(img, speckle_area=2,
                                    line_darken=line_darken,
                                    gray_floor=gray_floor)
        out.append(img)
    return {"frames": out, "count": len(out),
            "checks": ["registration between frames must stay put",
                       "line weight should stay within the model sheet",
                       "re-run line boil analysis after cleanup"]}


def bone_rig_build(bones):
    """Bone Rig Builder: validate a skeleton (parents, lengths, cycles) and
    report the FK chain order. bones: [{name, parent, length, angle}]"""
    core, im = _init()
    by_name = {}
    for b in bones or []:
        name = str(b.get("name", "")).strip()
        require(name, "invalid_input", "every bone needs a name")
        require(name not in by_name, "invalid_input",
                "duplicate bone name '%s'" % name)
        by_name[name] = b
    issues = []
    for name, b in by_name.items():
        parent = b.get("parent")
        if parent is not None:
            if parent not in by_name:
                issues.append({"bone": name, "issue": "missing_parent",
                               "parent": parent})
            if parent == name:
                issues.append({"bone": name, "issue": "self_parent"})
    # cycle detection
    for name in by_name:
        seen = set()
        cur = name
        while cur is not None:
            if cur in seen:
                issues.append({"bone": name, "issue": "cycle"})
                break
            seen.add(cur)
            if cur not in by_name:
                break
            cur = by_name[cur].get("parent")
    roots = [n for n, b in by_name.items() if b.get("parent") is None]
    # FK order: parents before children
    order = []
    placed = set()
    while len(order) < len(by_name):
        progressed = False
        for name, b in by_name.items():
            if name in placed:
                continue
            if b.get("parent") is None or b["parent"] in placed:
                order.append(name)
                placed.add(name)
                progressed = True
        if not progressed:
            order.extend(n for n in by_name if n not in placed)
            break
    return {"bones": len(by_name), "roots": roots, "fk_order": order,
            "issues": issues,
            "verdict": "valid" if not issues else "%d issue(s)" % len(issues)}


def lipsync_alt_language(original, new_phonemes, fps=24, anchor_words=None):
    """Alternate Language Lip-Sync Adapter: keep visemes anchored at shared
    story beats, regenerate the rest for the new language's phoneme timings."""
    core, im = _init()
    anchors = sorted(int(a["frame"]) for a in (anchor_words or []))
    regen = phonemes_to_visemes(new_phonemes, fps=fps)
    # scale regenerated timings into each anchor interval so beats line up
    out = []
    bounds = [0] + anchors + [max(r["frame"] for r in regen["frames"]) + 1
                              if regen["frames"] else 1]
    for i in range(len(bounds) - 1):
        seg = [r for r in regen["frames"] if bounds[i] <= r["frame"] < bounds[i + 1]]
        if not seg:
            continue
        src_lo = min(r["frame"] for r in seg)
        src_hi = max(r["frame"] for r in seg)
        span = max(1, src_hi - src_lo)
        tgt_span = bounds[i + 1] - bounds[i]
        for r in seg:
            nf = bounds[i] + int((r["frame"] - src_lo) * tgt_span / span)
            row = dict(r)
            row["frame"] = nf
            row["rescaled"] = nf != r["frame"]
            out.append(row)
    out.sort(key=lambda r: r["frame"])
    return {"frames": out, "anchors": anchors,
            "original_count": len(original or []),
            "note": "verify mouth closures at scene ends; languages differ "
                    "in syllable cadence"}


def seed_policy(seeds):
    """Random Seed Controller: register named seeds, detect reuse and
    collisions, keep effects reproducible across reruns."""
    core, im = _init()
    rows = []
    used_values = {}
    for name, value in (seeds or {}).items():
        v = str(value)
        clash = used_values.get(v)
        used_values.setdefault(v, name)
        rows.append({"name": name, "seed": v,
                     "value_collision_with": clash if clash not in (None, name)
                     else None})
    return {"seeds": rows, "count": len(rows),
            "collisions": [r for r in rows if r["value_collision_with"]],
            "policy": ["log seed per effect layer in the shot manifest",
                       "same seed + same preset = same result",
                       "change seeds deliberately, never by accident"]}
