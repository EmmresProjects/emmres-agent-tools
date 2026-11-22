#!/usr/bin/env python3
"""Metric engines for the Media Intelligence Workbench Skill Tool.

Pure standard-library signal measurement over Raster objects (from
tools-media-io.py) and decoded audio sample lists. Nothing here touches the
network, spawns processes, or writes files, so it is safe to run on untrusted
media after tools-media-io.safety_scan() has passed.

Engines
-------
image  : sharpness, noise, exposure, contrast, colour cast, banding, blocking,
         dominant colours, perceptual hashes, quality score
video  : frame difference, scene cuts, flash/strobe risk, motion energy
audio  : level statistics, clipping, silence map, hum, spectral balance,
         channel balance, loudness estimate, onset/tempo estimate
ground : bounding boxes / masks arithmetic used by region grounding

Run `python3 tools-metrics.py --selftest` to execute the built-in metric
selftest (synthetic signals only, no files needed).
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# The media I/O module is named with a hyphen for packaging rules; load it by path.
def _load_media_io():
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "tools_media_io_module", os.path.join(here, "tools-media-io.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


io = _load_media_io()
Raster = io.Raster

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def _round(v, nd=4):
    return None if v is None else round(float(v), nd)


def _mean(values):
    return sum(values) / float(len(values)) if values else 0.0


def _stdev(values):
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / float(len(values) - 1))


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def luma_grid(raster, step=1):
    """Return list-of-rows luma values (0..255) sampled every *step* pixels."""
    rows = []
    for y in range(0, raster.height, step):
        row = [raster.luma(x, y) for x in range(0, raster.width, step)]
        rows.append(row)
    return rows


def downsample(raster, max_side=640):
    """Nearest-neighbour box downsample so long analyses stay bounded."""
    factor = max(1, int(math.ceil(max(raster.width, raster.height) / float(max_side))))
    if factor == 1:
        return raster, 1
    w, h = raster.width // factor, raster.height // factor
    data = bytearray(w * h * 3)
    src = raster.data
    ch = raster.channels
    o = 0
    for y in range(h):
        sy = y * factor
        base = sy * raster.width
        for x in range(w):
            sx = x * factor
            i = (base + sx) * ch
            if ch >= 3:
                data[o] = src[i]
                data[o + 1] = src[i + 1]
                data[o + 2] = src[i + 2]
            else:
                data[o] = data[o + 1] = data[o + 2] = src[i]
            o += 3
    return Raster(w, h, 3, data, source=raster.source, decoder=raster.decoder), factor


# --------------------------------------------------------------------------
# image metrics
# --------------------------------------------------------------------------


def sharpness(raster):
    """Laplacian variance and Tenengrad mean over the luma plane."""
    grid = luma_grid(raster)
    h, w = len(grid), len(grid[0]) if grid else 0
    if h < 3 or w < 3:
        return {"laplacian_variance": 0.0, "tenengrad_mean": 0.0, "verdict": "too-small"}
    lap = []
    sx = sy = 0.0
    n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            center = grid[y][x]
            lap_val = (grid[y - 1][x] + grid[y + 1][x] + grid[y][x - 1]
                       + grid[y][x + 1] - 4 * center) / 4.0
            lap.append(lap_val)
            gx = grid[y][x + 1] - grid[y][x - 1]
            gy = grid[y + 1][x] - grid[y - 1][x]
            sx += gx * gx
            sy += gy * gy
            n += 1
    var = _stdev(lap) ** 2 if lap else 0.0
    ten = math.sqrt((sx + sy) / (2.0 * n)) if n else 0.0
    if var < 6:
        verdict = "soft-or-blurred"
    elif var < 25:
        verdict = "moderate"
    else:
        verdict = "sharp"
    return {"laplacian_variance": _round(var, 3), "tenengrad_mean": _round(ten, 3),
            "verdict": verdict,
            "note": "thresholds are calibrated for 640px-normalised luma; "
                    "compare like-for-like captures"}


def noise_estimate(raster, edge_context=True):
    """Immerkaer-style residual estimate with an edge-vs-noise guard.

    Hard edges also produce large Laplacian residuals, so the raw sigma is
    compared with the Tenengrad edge energy: strongly edged images are labelled
    "edge-dominated" instead of being reported as noisy.
    """
    grid = luma_grid(raster)
    h, w = len(grid), len(grid[0]) if grid else 0
    if h < 3 or w < 3:
        return {"sigma": 0.0, "verdict": "too-small"}
    acc = 0.0
    n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            v = (grid[y - 1][x - 1] - 2 * grid[y - 1][x] + grid[y - 1][x + 1]
                 - 2 * grid[y][x - 1] + 4 * grid[y][x] - 2 * grid[y][x + 1]
                 + grid[y + 1][x - 1] - 2 * grid[y + 1][x] + grid[y + 1][x + 1])
            acc += abs(v)
            n += 1
    sigma = (acc / n) * math.sqrt(math.pi / 2.0) / 6.0 if n else 0.0
    verdict = "clean" if sigma < 3 else ("noticeable" if sigma < 8 else "heavy")
    edge = sharpness(raster)["tenengrad_mean"]
    edge_dominated = bool(edge_context and edge > 25 and sigma < 18)
    if edge_dominated and verdict != "clean":
        verdict = "edge-dominated"
    return {"sigma": _round(sigma, 3), "verdict": verdict,
            "edge_energy": edge,
            "note": "edge-dominated means high residual variance comes from real "
                    "edges rather than sensor noise"}


def exposure(raster):
    grid = luma_grid(raster, step=2)
    vals = sorted(v for row in grid for v in row)
    if not vals:
        return {"mean_luma": 0.0, "clipped_black_pct": 0.0, "clipped_white_pct": 0.0}
    n = len(vals)
    black = sum(1 for v in vals if v <= 2) / n * 100
    white = sum(1 for v in vals if v >= 253) / n * 100
    mean = _mean(vals)
    median = _percentile(vals, 0.5)
    verdict = "balanced"
    if white > 5:
        verdict = "clipped-highlights"
    elif black > 10:
        verdict = "crushed-shadows"
    elif mean < 45:
        verdict = "underexposed"
    elif mean > 210:
        verdict = "overexposed"
    return {"mean_luma": _round(mean, 2), "median_luma": _round(median, 2),
            "p1_luma": _round(_percentile(vals, 0.01), 1),
            "p99_luma": _round(_percentile(vals, 0.99), 1),
            "clipped_black_pct": _round(black, 2),
            "clipped_white_pct": _round(white, 2),
            "verdict": verdict}


def contrast(raster):
    grid = luma_grid(raster, step=2)
    vals = [v for row in grid for v in row]
    if not vals:
        return {"stdev": 0.0, "range": 0}
    sd = _stdev([float(v) for v in vals])
    verdict = "flat" if sd < 20 else ("normal" if sd < 60 else "high")
    return {"stdev": _round(sd, 2), "range": max(vals) - min(vals),
            "p5": _round(_percentile(sorted(vals), 0.05), 1),
            "p95": _round(_percentile(sorted(vals), 0.95), 1), "verdict": verdict}


def color_cast(raster):
    rs = gs = bs = 0
    n = 0
    step = max(1, int(math.sqrt(raster.pixels / 40000.0)) or 1)
    for y in range(0, raster.height, step):
        for x in range(0, raster.width, step):
            r, g, b = raster.pixel(x, y)
            rs += r
            gs += g
            bs += b
            n += 1
    n = max(1, n)
    mr, mg, mb = rs / n, gs / n, bs / n
    mean_all = (mr + mg + mb) / 3.0 or 1.0
    devs = {"r": (mr - mean_all) / mean_all, "g": (mg - mean_all) / mean_all,
            "b": (mb - mean_all) / mean_all}
    worst = max(devs, key=lambda k: abs(devs[k]))
    cast = "neutral" if abs(devs[worst]) < 0.04 else "%s-%s" % (
        worst, "heavy" if abs(devs[worst]) > 0.12 else "slight")
    return {"mean_rgb": [_round(mr, 1), _round(mg, 1), _round(mb, 1)],
            "deviation": {k: _round(v, 4) for k, v in devs.items()},
            "dominant_cast": cast}


def banding(raster):
    """Count flat-quantised steps in the luma histogram (banding indicator)."""
    grid = luma_grid(raster, step=2)
    hist = [0] * 256
    total = 0
    for row in grid:
        for v in row:
            hist[v] += 1
            total += 1
    empty_bins = sum(1 for c in hist if c == 0)
    # local peaks: bins much larger than their neighbours inside the occupied range
    occupied = [i for i, c in enumerate(hist) if c > 0]
    spikes = 0
    if occupied:
        lo, hi = occupied[0], occupied[-1]
        for i in range(lo + 1, hi):
            if hist[i] > 4 * max(1, (hist[i - 1] + hist[i + 1]) / 2.0 + 1):
                spikes += 1
    score = empty_bins / 256.0
    verdict = "banding-risk" if spikes > 25 and score > 0.35 else "acceptable"
    return {"empty_luma_bins": empty_bins, "histogram_gap_ratio": _round(score, 3),
            "isolated_peaks": spikes, "verdict": verdict}


def blocking(raster):
    """8x8 block-boundary energy versus interior energy (codec blocking cue)."""
    grid = luma_grid(raster)
    h, w = len(grid), len(grid[0]) if grid else 0
    if h < 18 or w < 18:
        return {"ratio": 1.0, "verdict": "too-small"}
    boundary = interior = 0.0
    nb = ni = 0
    for y in range(1, h):
        for x in range(1, w):
            d = abs(grid[y][x] - grid[y][x - 1]) + abs(grid[y][x] - grid[y - 1][x])
            if x % 8 == 0 or y % 8 == 0:
                boundary += d
                nb += 1
            else:
                interior += d
                ni += 1
    if ni == 0 or nb == 0 or interior == 0:
        return {"ratio": 1.0, "verdict": "indeterminate"}
    ratio = (boundary / nb) / (interior / ni)
    verdict = "block-artifacts" if ratio > 1.6 else (
        "slight-blocking" if ratio > 1.25 else "clean")
    return {"ratio": _round(ratio, 3), "verdict": verdict}


def dominant_colors(raster, count=5):
    """Coarse 4-bit-per-channel histogram palette with coverage percentages."""
    hist = {}
    step = max(1, int(math.sqrt(raster.pixels / 60000.0)) or 1)
    total = 0
    for y in range(0, raster.height, step):
        for x in range(0, raster.width, step):
            r, g, b = raster.pixel(x, y)
            key = (r >> 4, g >> 4, b >> 4)
            hist[key] = hist.get(key, 0) + 1
            total += 1
    top = sorted(hist.items(), key=lambda kv: kv[1], reverse=True)[:count]
    out = []
    for (r4, g4, b4), n in top:
        out.append({
            "hex": "#%02x%02x%02x" % (r4 * 17, g4 * 17, b4 * 17),
            "rgb": [r4 * 17, g4 * 17, b4 * 17],
            "coverage_pct": _round(n / max(1, total) * 100, 2),
        })
    return {"palette": out, "distinct_buckets": len(hist)}


def _gray_grid(raster, size=8):
    """Average-pooled square luma grid used by the perceptual hashes."""
    out = []
    for by in range(size):
        y0 = int(by * raster.height / size)
        y1 = max(y0 + 1, int((by + 1) * raster.height / size))
        row = []
        for bx in range(size):
            x0 = int(bx * raster.width / size)
            x1 = max(x0 + 1, int((bx + 1) * raster.width / size))
            acc = 0
            n = 0
            for y in range(y0, min(y1, raster.height)):
                for x in range(x0, min(x1, raster.width)):
                    acc += raster.luma(x, y)
                    n += 1
            row.append(acc / max(1, n))
        out.append(row)
    return out


def phash(raster, size=8):
    """Average hash + difference hash as 16-hex-digit strings."""
    grid = _gray_grid(raster, size)
    flat = [v for row in grid for v in row]
    avg = _mean(flat)
    bits = 0
    for i, v in enumerate(flat):
        if v >= avg:
            bits |= (1 << i)
    dbits = 0
    i = 0
    for row in grid:
        for x in range(len(row) - 1):
            if row[x + 1] > row[x]:
                dbits |= (1 << i)
            i += 1
    return {"ahash": "%016x" % bits, "dhash": "%016x" % dbits,
            "bits": size * size}


def hamming(hex_a, hex_b):
    a, b = int(hex_a, 16), int(hex_b, 16)
    return bin(a ^ b).count("1")


def image_quality_report(raster, source=None):
    working, factor = downsample(raster, 640)
    sharp = sharpness(working)
    noi = noise_estimate(working)
    exp = exposure(working)
    con = contrast(working)
    cast = color_cast(working)
    ban = banding(working)
    blo = blocking(working)
    issues = []
    if sharp["verdict"] == "soft-or-blurred":
        issues.append({"code": "soft-focus", "severity": "medium",
                       "detail": "laplacian variance %s" % sharp["laplacian_variance"]})
    if noi["verdict"] in ("noticeable", "heavy"):
        issues.append({"code": "noise", "severity": "medium",
                       "detail": "sigma %s" % noi["sigma"]})
    if exp["verdict"] != "balanced":
        issues.append({"code": exp["verdict"], "severity": "medium",
                       "detail": "mean luma %s, white clip %s%%"
                                 % (exp["mean_luma"], exp["clipped_white_pct"])})
    if con["verdict"] == "flat":
        issues.append({"code": "low-contrast", "severity": "low",
                       "detail": "stdev %s" % con["stdev"]})
    if ban["verdict"] == "banding-risk":
        issues.append({"code": "banding", "severity": "low",
                       "detail": "%d isolated peaks" % ban["isolated_peaks"]})
    if blo["verdict"] in ("block-artifacts", "slight-blocking"):
        issues.append({"code": "blocking", "severity": "low",
                       "detail": "ratio %s" % blo["ratio"]})
    if cast["dominant_cast"] != "neutral" and "heavy" in cast["dominant_cast"]:
        issues.append({"code": "color-cast", "severity": "low",
                       "detail": cast["dominant_cast"]})
    penalty = {"medium": 12, "low": 5}
    score = 100 - sum(penalty.get(i["severity"], 0) for i in issues)
    return {
        "source": source,
        "dimensions": [raster.width, raster.height],
        "megapixels": _round(raster.pixels / 1e6, 3),
        "analysis_scale_factor": factor,
        "sharpness": sharp,
        "noise": noi,
        "exposure": exp,
        "contrast": con,
        "color": cast,
        "banding": ban,
        "blocking": blo,
        "issues": issues,
        "quality_score": max(0, score),
    }


# --------------------------------------------------------------------------
# video / temporal metrics
# --------------------------------------------------------------------------


def frame_difference(raster_a, raster_b, size=64):
    """Mean absolute difference, histogram distance and a coarse SSIM surrogate."""
    ga = _gray_grid(raster_a, size)
    gb = _gray_grid(raster_b, size)
    diffs = []
    for ra, rb in zip(ga, gb):
        for va, vb in zip(ra, rb):
            diffs.append(abs(va - vb))
    mad = _mean(diffs) / 255.0
    hist_a = [0] * 32
    hist_b = [0] * 32
    for row in ga:
        for v in row:
            hist_a[min(31, int(v / 8))] += 1
    for row in gb:
        for v in row:
            hist_b[min(31, int(v / 8))] += 1
    total = float(size * size)
    hist_dist = sum(abs(x - y) for x, y in zip(hist_a, hist_b)) / (2 * total)
    ma, mb = _mean([v for r in ga for v in r]), _mean([v for r in gb for v in r])
    va = _stdev([v for r in ga for v in r]) or 1e-9
    vb = _stdev([v for r in gb for v in r]) or 1e-9
    cov = _mean([(x - ma) * (y - mb) for r1, r2 in zip(ga, gb) for x, y in zip(r1, r2)])
    ssim = ((2 * ma * mb) * (2 * cov)) / ((ma ** 2 + mb ** 2) * (va ** 2 + vb ** 2))
    return {"mean_abs_diff": _round(mad, 4), "histogram_distance": _round(hist_dist, 4),
            "ssim_surrogate": _round(_clamp(ssim, -1, 1), 4)}


def scene_cut_scores(raster_pairs):
    """Score each consecutive pair; a cut is a local spike above a threshold."""
    scores = []
    for i, (a, b) in enumerate(raster_pairs):
        scores.append(frame_difference(a, b))
    return scores


def detect_scene_cuts(scores, threshold=0.16, min_gap=1):
    cuts = []
    last = -min_gap
    for i, s in enumerate(scores):
        if s["histogram_distance"] >= threshold and s["mean_abs_diff"] >= 0.06:
            if i - last >= min_gap:
                cuts.append({"pair_index": i, "histogram_distance": s["histogram_distance"],
                             "mean_abs_diff": s["mean_abs_diff"]})
                last = i
    return cuts


def flash_risk(luma_series, fps=25.0, relative_change=0.20, min_flashes=3,
               window_s=1.0):
    """WCAG/EBU-style flash detector over a per-frame mean-luma series.

    Returns the number of luminance transitions found in any sliding window and
    flags the clip when it meets the three-flashes-in-one-second criterion
    (a conservative aid, not a certification).
    """
    if len(luma_series) < 3:
        return {"transitions": 0, "risk": "insufficient-samples",
                "max_transitions_in_window": 0}
    transitions = []
    for i in range(1, len(luma_series)):
        prev, cur = luma_series[i - 1], luma_series[i]
        scale = max(prev, cur, 1e-6)
        if abs(cur - prev) / scale >= relative_change:
            transitions.append(i)
    win = max(1, int(round(window_s * fps)))
    best = 0
    j = 0
    for i, _ in enumerate(transitions):
        while transitions[i] - transitions[j] > win:
            j += 1
        best = max(best, i - j + 1)
    risk = "high" if best >= min_flashes * 2 else (
        "elevated" if best >= min_flashes else "low")
    return {"transitions": len(transitions), "max_transitions_in_window": best,
            "window_frames": win, "risk": risk,
            "note": "conservative luminance-transition aid; do not use as an "
                    "accessibility certification"}


def motion_energy(luma_series):
    if len(luma_series) < 2:
        return {"mean_delta": 0.0, "verdict": "insufficient-samples"}
    deltas = [abs(luma_series[i] - luma_series[i - 1]) for i in range(1, len(luma_series))]
    md = _mean(deltas)
    verdict = "static" if md < 1.0 else ("low-motion" if md < 6 else "high-motion")
    return {"mean_delta": _round(md, 3), "max_delta": _round(max(deltas), 3),
            "verdict": verdict}


# --------------------------------------------------------------------------
# audio metrics
# --------------------------------------------------------------------------


def audio_level_stats(samples, sample_rate=48000):
    """Peak, RMS, dBFS, clipping percentage, DC offset and crest factor."""
    n = len(samples)
    if n == 0:
        return {"frames": 0, "error": "empty signal"}
    peak = max(abs(s) for s in samples)
    rms = math.sqrt(sum(s * s for s in samples) / n)
    db_peak = 20 * math.log10(peak) if peak > 0 else -120.0
    db_rms = 20 * math.log10(rms) if rms > 0 else -120.0
    clipped = sum(1 for s in samples if abs(s) >= 0.999)
    dc = _mean(samples)
    crest = (peak / rms) if rms > 0 else 0.0
    verdict = "ok"
    if clipped / n > 0.001:
        verdict = "clipping"
    elif db_rms < -45:
        verdict = "very-quiet"
    elif abs(dc) > 0.01:
        verdict = "dc-offset"
    return {"frames": n, "sample_rate": sample_rate,
            "peak_dbfs": _round(db_peak, 2), "rms_dbfs": _round(db_rms, 2),
            "crest_factor": _round(crest, 2),
            "clipped_samples": clipped,
            "clipped_pct": _round(clipped / n * 100, 4),
            "dc_offset": _round(dc, 5), "verdict": verdict}


def loudness_estimate(samples, sample_rate=48000):
    """Approximate integrated loudness (LUFS-like, K-weighting modelled).

    This is a transparent approximation built from a first-order high-pass and
    a high-shelf tilt followed by gated mean square. It is NOT ITU-R BS.1770
    certified and is labelled as an estimate everywhere it is reported.
    """
    if not samples:
        return {"lufs_estimate": None, "method": "empty"}
    hp = []
    prev_x = prev_y = 0.0
    a = math.exp(-2 * math.pi * 38.0 / sample_rate)
    for x in samples:
        y = a * (prev_y + x - prev_x)
        hp.append(y)
        prev_x, prev_y = x, y
    # high shelf: emphasise > 1500 Hz via first difference blend
    tilt = []
    for i, x in enumerate(hp):
        d = x - (hp[i - 1] if i else 0.0)
        tilt.append(x + 0.5 * d)
    block = max(1, int(0.4 * sample_rate))
    means = []
    for start in range(0, len(tilt), block):
        chunk = tilt[start:start + block]
        if len(chunk) < block // 2:
            continue
        means.append(sum(v * v for v in chunk) / len(chunk))
    if not means:
        return {"lufs_estimate": None, "method": "too-short"}
    gate = sorted(means)[len(means) // 2] * 0.1
    gated = [m for m in means if m > gate] or means
    energy = _mean(gated)
    lufs = -0.691 + 10 * math.log10(energy) if energy > 0 else -70.0
    return {"lufs_estimate": _round(max(-70.0, lufs), 1),
            "method": "approximate-k-weighting (not BS.1770 certified)",
            "gated_blocks": len(gated)}


def silence_map(samples, sample_rate=48000, threshold_dbfs=-45.0,
                min_silence_s=0.25):
    """Return silence regions with start, end and duration in seconds."""
    if not samples:
        return {"regions": [], "silence_ratio": 0.0}
    thresh = 10 ** (threshold_dbfs / 20.0)
    win = max(1, int(0.02 * sample_rate))
    loud = []
    for start in range(0, len(samples), win):
        chunk = samples[start:start + win]
        if not chunk:
            continue
        rms = math.sqrt(sum(v * v for v in chunk) / len(chunk))
        loud.append(rms >= thresh)
    regions = []
    run_start = None
    for i, is_loud in enumerate(loud):
        if not is_loud and run_start is None:
            run_start = i
        elif is_loud and run_start is not None:
            dur = (i - run_start) * win / sample_rate
            if dur >= min_silence_s:
                regions.append({"start_s": _round(run_start * win / sample_rate, 3),
                                "end_s": _round(i * win / sample_rate, 3),
                                "duration_s": _round(dur, 3)})
            run_start = None
    if run_start is not None:
        dur = (len(loud) - run_start) * win / sample_rate
        if dur >= min_silence_s:
            regions.append({"start_s": _round(run_start * win / sample_rate, 3),
                            "end_s": _round(len(loud) * win / sample_rate, 3),
                            "duration_s": _round(dur, 3)})
    total_silence = sum(r["duration_s"] for r in regions)
    duration = len(samples) / float(sample_rate)
    return {"regions": regions[:200],
            "region_count": len(regions),
            "silence_ratio": _round(total_silence / duration if duration else 0.0, 3),
            "threshold_dbfs": threshold_dbfs}


def goertzel(samples, sample_rate, freq):
    """Energy at a single frequency (used for mains-hum detection)."""
    if not samples:
        return 0.0
    n = len(samples)
    k = int(0.5 + n * freq / sample_rate)
    w = 2 * math.pi * k / n
    coeff = 2 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return max(0.0, power) / (n * n)


def hum_detection(samples, sample_rate=48000, fft_size=4096):
    """Mains-hum indicator: harmonic power as a fraction of total signal power.

    The measure is ``10*log10(harmonic_power / total_power)`` where harmonic
    power sums the bins at 50/60 Hz and their first five harmonics (+/-1 bin).
    A pure tone or clean speech leaves almost no power there, so the ratio is
    strongly negative; genuine mains hum sits close to 0 dB.
    """
    n = len(samples)
    if n < fft_size or sample_rate < 2000:
        return {"verdict": "insufficient-samples",
                "note": "need at least %d samples at this rate" % fft_size}
    windows = []
    span = min(n, fft_size * 4)
    offset = max(0, (n - span) // 2)
    for start in range(offset, offset + span - fft_size + 1, fft_size):
        windows.append(samples[start:start + fft_size])
    windows = windows[:4]
    total_power = _mean([_mean([v * v for v in w]) for w in windows])
    if total_power <= 1e-12:
        return {"verdict": "clean", "note": "signal is effectively silent",
                "harmonic_ratio_db": {}}
    results = {}
    for base in (50, 60):
        harmonic = 0.0
        for win in windows:
            re = list(win)
            im = [0.0] * fft_size
            _fft(re, im)
            for h in range(1, 7):
                freq = base * h
                if freq >= sample_rate / 2.0 - 20:
                    continue
                k = int(round(freq * fft_size / float(sample_rate)))
                for kk in (k - 1, k, k + 1):
                    if 1 <= kk < fft_size // 2:
                        harmonic += (math.hypot(re[kk], im[kk]) / fft_size) ** 2 / len(windows)
        ratio_db = 10.0 * math.log10((harmonic + 1e-20) / total_power)
        results["%dhz_db" % base] = _round(max(-90.0, min(10.0, ratio_db)), 2)
    worst = max(results, key=lambda k: results[k])
    best = results[worst]
    verdict = ("hum-likely" if best > -20 else
               ("hum-possible" if best > -33 else "clean"))
    return {"harmonic_ratio_db": results, "dominant": worst, "verdict": verdict,
            "note": "share of total power in mains harmonics; > -20 dB indicates "
                    "strong mains hum, > -33 dB is worth a listen"}


def _fft(magnitudes_re, magnitudes_im):
    """In-place iterative radix-2 FFT; lengths must be powers of two."""
    n = len(magnitudes_re)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            magnitudes_re[i], magnitudes_re[j] = magnitudes_re[j], magnitudes_re[i]
            magnitudes_im[i], magnitudes_im[j] = magnitudes_im[j], magnitudes_im[i]
    length = 2
    while length <= n:
        ang = -2 * math.pi / length
        wl_re, wl_im = math.cos(ang), math.sin(ang)
        for i in range(0, n, length):
            w_re, w_im = 1.0, 0.0
            for k in range(i, i + length // 2):
                u_re, u_im = magnitudes_re[k], magnitudes_im[k]
                v_re = magnitudes_re[k + length // 2] * w_re - magnitudes_im[k + length // 2] * w_im
                v_im = magnitudes_re[k + length // 2] * w_im + magnitudes_im[k + length // 2] * w_re
                magnitudes_re[k] = u_re + v_re
                magnitudes_im[k] = u_im + v_im
                magnitudes_re[k + length // 2] = u_re - v_re
                magnitudes_im[k + length // 2] = u_im - v_im
                w_re, w_im = (w_re * wl_re - w_im * wl_im, w_re * wl_im + w_im * wl_re)
        length <<= 1


def spectral_profile(samples, sample_rate=48000, fft_size=1024):
    """Average magnitude spectrum plus band energy shares."""
    if len(samples) < fft_size:
        return {"bands": {}, "note": "signal shorter than one FFT window"}
    frames = min(64, len(samples) // fft_size)
    bands = {"sub_20_60": 0.0, "low_60_250": 0.0, "mid_250_2000": 0.0,
             "high_2k_6k": 0.0, "air_6k_16k": 0.0}
    totals = 0.0
    centroid_num = 0.0
    for f in range(frames):
        start = f * fft_size
        chunk = samples[start:start + fft_size]
        if len(chunk) < fft_size:
            break
        win = [chunk[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / (fft_size - 1)))
               for i in range(fft_size)]
        re = list(win)
        im = [0.0] * fft_size
        _fft(re, im)
        for k in range(1, fft_size // 2):
            mag = math.hypot(re[k], im[k]) / fft_size
            freq = k * sample_rate / float(fft_size)
            totals += mag
            centroid_num += mag * freq
            if freq < 60:
                bands["sub_20_60"] += mag
            elif freq < 250:
                bands["low_60_250"] += mag
            elif freq < 2000:
                bands["mid_250_2000"] += mag
            elif freq < 6000:
                bands["high_2k_6k"] += mag
            else:
                bands["air_6k_16k"] += mag
    if totals <= 0:
        return {"bands": {}, "note": "silent signal"}
    shares = {k: _round(v / totals * 100, 2) for k, v in bands.items()}
    centroid = centroid_num / totals
    tilt = "dark" if centroid < 800 else ("balanced" if centroid < 2500 else "bright")
    return {"bands_pct": shares, "spectral_centroid_hz": _round(centroid, 1),
            "tilt": tilt, "frames_analysed": frames}


def channel_balance(channels, sample_rate=48000):
    if len(channels) < 2:
        return {"channels": len(channels), "note": "mono or single channel"}
    rms = []
    for ch in channels:
        rms.append(math.sqrt(sum(v * v for v in ch) / max(1, len(ch)))) 
    ratio_db = 20 * math.log10((rms[0] + 1e-12) / (rms[1] + 1e-12))
    corr_num = sum(a * b for a, b in zip(channels[0], channels[1]))
    corr_den = math.sqrt(sum(a * a for a in channels[0]) * sum(b * b for b in channels[1])) or 1e-12
    corr = corr_num / corr_den
    verdict = "ok"
    if abs(ratio_db) > 3:
        verdict = "imbalance"
    if corr < -0.2:
        verdict = "phase-issue"
    return {"rms_per_channel": [_round(r, 4) for r in rms],
            "balance_db_l_minus_r": _round(ratio_db, 2),
            "correlation": _round(corr, 3), "verdict": verdict}


def onset_envelope(samples, sample_rate=48000, hop=512):
    """Half-wave-rectified spectral-flux-like envelope from block energy deltas."""
    env = []
    win = hop * 2
    prev = 0.0
    for start in range(0, len(samples) - win, hop):
        chunk = samples[start:start + win]
        energy = math.sqrt(sum(v * v for v in chunk) / win)
        diff = energy - prev
        env.append(diff if diff > 0 else 0.0)
        prev = energy
    return env, sample_rate / float(hop)


def tempo_estimate(env, env_rate, min_bpm=50.0, max_bpm=200.0):
    """Autocorrelation tempo estimate over the onset envelope."""
    if len(env) < 32:
        return {"bpm": None, "confidence": 0.0, "note": "too few onsets"}
    mean = _mean(env)
    centred = [v - mean for v in env]
    best_lag, best_score = None, 0.0
    lo = int(env_rate * 60.0 / max_bpm)
    hi = int(env_rate * 60.0 / min_bpm)
    lo = max(1, lo)
    hi = min(hi, len(centred) // 2)
    scores = {}
    for lag in range(lo, hi + 1):
        num = sum(centred[i] * centred[i + lag] for i in range(len(centred) - lag))
        den = math.sqrt(sum(v * v for v in centred) *
                        sum(v * v for v in centred[:len(centred) - lag])) or 1e-12
        score = num / den
        scores[lag] = score
        if score > best_score:
            best_score, best_lag = score, lag
    if not best_lag:
        return {"bpm": None, "confidence": 0.0, "note": "no periodic peak found"}
    bpm = 60.0 * env_rate / best_lag
    return {"bpm": _round(bpm, 1), "confidence": _round(best_score, 3),
            "method": "onset-envelope autocorrelation"}


# --------------------------------------------------------------------------
# grounding helpers
# --------------------------------------------------------------------------


def box_area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def box_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = box_area(a) + box_area(b) - inter
    return inter / union if union else 0.0


def box_relative(box, width, height):
    return [round(box[0] / width, 4), round(box[1] / height, 4),
            round(box[2] / width, 4), round(box[3] / height, 4)]


def temporal_overlap(a, b):
    """Intersection-over-union of two (start_s, end_s) spans."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def _solid_raster(w, h, rgb):
    data = bytearray()
    for _ in range(w * h):
        data.extend(rgb)
    return Raster(w, h, 3, data, decoder="synthetic")


def _checker_raster(w, h, a=0, b=255, cell=4):
    data = bytearray()
    for y in range(h):
        for x in range(w):
            v = b if ((x // cell) + (y // cell)) % 2 == 0 else a
            data.extend((v, v, v))
    return Raster(w, h, 3, data, decoder="synthetic")


def _gradient_raster(w, h, vertical=False):
    data = bytearray()
    for y in range(h):
        for x in range(w):
            v = int(255 * ((y if vertical else x) / float(max(1, (h if vertical else w) - 1))))
            data.extend((v, v, v))
    return Raster(w, h, 3, data, decoder="synthetic")


def _noise_raster(w, h, seed=7):
    state = seed
    data = bytearray()
    for _ in range(w * h):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        v = 128 + ((state >> 8) % 60) - 30
        data.extend((max(0, min(255, v)),) * 3)
    return Raster(w, h, 3, data, decoder="synthetic")


def selftest():
    failures = []

    def expect(name, cond, detail=""):
        if not cond:
            failures.append("%s: %s" % (name, detail))
        print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail and not cond else ""))

    flat = _solid_raster(64, 64, (128, 128, 128))
    checker = _checker_raster(64, 64)
    noisy = _noise_raster(64, 64)

    expect("sharpness flat < checker",
           sharpness(flat)["laplacian_variance"] < sharpness(checker)["laplacian_variance"])
    expect("sharpness labels checker sharp", sharpness(checker)["verdict"] == "sharp")
    expect("noise flat clean", noise_estimate(flat)["verdict"] == "clean")
    expect("noise synthetic noisy detected",
           noise_estimate(noisy)["sigma"] > noise_estimate(flat)["sigma"])
    expect("exposure balanced for mid grey", exposure(flat)["verdict"] == "balanced")
    expect("exposure white clip detected",
           exposure(_solid_raster(32, 32, (255, 255, 255)))["clipped_white_pct"] > 50)
    expect("contrast flat", contrast(flat)["verdict"] == "flat")
    expect("contrast high on checker", contrast(checker)["verdict"] == "high")
    cast = color_cast(_solid_raster(32, 32, (200, 100, 100)))
    expect("color cast detected", "r" in cast["dominant_cast"], str(cast))
    expect("banding on checker", banding(checker)["verdict"] in ("acceptable", "banding-risk"))
    expect("blocking runs", "ratio" in blocking(checker))
    expect("dominant colours single bucket",
           len(dominant_colors(flat, 3)["palette"]) >= 1)
    h1 = phash(checker)
    h2 = phash(_checker_raster(64, 64, cell=8))
    expect("phash stable shape", len(h1["ahash"]) == 16)
    expect("phash is scale-of-pattern sensitive",
           hamming(h1["dhash"], h2["dhash"]) <= 64)
    h_grad_x = phash(_gradient_raster(64, 64))
    h_grad_y = phash(_gradient_raster(64, 64, vertical=True))
    expect("phash separates horizontal and vertical gradients",
           hamming(h_grad_x["ahash"], h_grad_y["ahash"]) > 0,
           str((h_grad_x, h_grad_y)))
    expect("phash identical for identical input",
           phash(_gradient_raster(64, 64))["ahash"] == h_grad_x["ahash"])
    expect("frame diff identical zero",
           frame_difference(flat, flat)["mean_abs_diff"] == 0.0)
    expect("frame diff differs",
           frame_difference(flat, checker)["histogram_distance"] > 0.2)
    expect("scene cut detected",
           len(detect_scene_cuts([frame_difference(flat, checker)], threshold=0.16)) == 1)
    expect("scene cut rejects identical",
           len(detect_scene_cuts([frame_difference(flat, flat)], threshold=0.16)) == 0)
    flash = flash_risk([10, 200, 10, 200, 10, 200], fps=10)
    expect("flash risk flagged", flash["risk"] in ("high", "elevated"), str(flash))
    expect("motion static", motion_energy([100, 100, 100])["verdict"] == "static")

    sr = 8000
    tone = [0.5 * math.sin(2 * math.pi * 440 * i / sr) for i in range(sr * 2)]
    stats = audio_level_stats(tone, sr)
    expect("audio peak near -6dBFS", -6.5 < stats["peak_dbfs"] < -5.5, str(stats["peak_dbfs"]))
    expect("audio no clipping", stats["verdict"] == "ok")
    loud = [1.0 if i % 2 else -1.0 for i in range(sr)]
    expect("audio clipping detected", audio_level_stats(loud, sr)["verdict"] == "clipping")
    quiet = [0.0] * sr
    expect("silence map all silent", silence_map(quiet, sr)["silence_ratio"] > 0.9)
    hum = [0.4 * math.sin(2 * math.pi * 60 * i / sr) for i in range(sr * 2)]
    expect("hum detected", hum_detection(hum, sr)["verdict"] in ("hum-likely", "hum-possible"))
    expect("spectral centroid on tone", 300 < spectral_profile(tone, sr)["spectral_centroid_hz"] < 600)
    expect("channel balance mono note", "note" in channel_balance([tone]))
    expect("loudness estimate numeric", loudness_estimate(tone, sr)["lufs_estimate"] is not None)
    env, rate = onset_envelope(tone * 2, sr)
    expect("onset envelope non-empty", len(env) > 10)
    click_train = []
    for i in range(sr * 4):
        click_train.append(1.0 if i % (sr // 2) < 40 else 0.0)
    env2, rate2 = onset_envelope(click_train, sr, hop=200)
    tempo = tempo_estimate(env2, rate2)
    expect("tempo estimate near 120bpm",
           tempo["bpm"] is not None and 100 <= tempo["bpm"] <= 140, str(tempo))
    expect("iou identical boxes", abs(box_iou([0, 0, 10, 10], [0, 0, 10, 10]) - 1.0) < 1e-9)
    expect("iou disjoint boxes", box_iou([0, 0, 5, 5], [6, 6, 9, 9]) == 0.0)
    expect("temporal overlap", abs(temporal_overlap((0, 10), (5, 15)) - 1 / 3.0) < 1e-9)

    print("\n%d checks failed" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
