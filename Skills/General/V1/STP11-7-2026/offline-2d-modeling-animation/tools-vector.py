#!/usr/bin/env python3
"""tools-vector.py — vector shapes, boolean geometry, SVG tooling, typography.

Covers the vector illustration and text/lettering domains: path math,
simplification, boolean operations (via high-resolution mask rasterization and
contour tracing), SVG audit/optimization, vector-to-raster export, shape
sheets, gradients, symbols, font coverage inspection (reads TTF/OTF cmap
tables directly), speech balloons and credit cards. Pure stdlib.
"""
import math
import re
import struct

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


# ------------------------------------------------------------ path math -----

def pts_bbox(polys):
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def transform_pts(polys, m):
    return [[[m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]] for p in poly]
            for poly in polys]


def mat_identity():
    return [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def mat_translate(dx, dy):
    return [1.0, 0.0, 0.0, 1.0, dx, dy]


def mat_scale(sx, sy=None):
    sy = sx if sy is None else sy
    return [sx, 0.0, 0.0, sy, 0.0, 0.0]


def mat_rotate(deg):
    a = math.radians(deg)
    return [math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0.0, 0.0]


def mat_mul(a, b):
    return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]]


def polygon_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def polygon_centroid(poly):
    a = polygon_area(poly)
    if abs(a) < 1e-12:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cr = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cr
        cy += (y0 + y1) * cr
    return (cx / (6 * a), cy / (6 * a))


def _perp_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def simplify_rdp(poly, tol):
    """Ramer-Douglas-Peucker simplification (keeps endpoints)."""
    if len(poly) < 3:
        return [list(p) for p in poly]
    keep = [0] * len(poly)
    keep[0] = keep[-1] = 1
    stack = [(0, len(poly) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            d = _perp_dist(poly[k], poly[i], poly[j])
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = 1
            stack.append((i, bi))
            stack.append((bi, j))
    return [list(poly[k]) for k in range(len(poly)) if keep[k]]


def simplify_polys(polys, tol=0.75):
    return [simplify_rdp(p, tol) for p in polys if len(p) >= 3]


def resample_poly(poly, step):
    """Resample a polyline to ~equal spacing (for timing charts and motion paths)."""
    if len(poly) < 2:
        return [list(p) for p in poly]
    out = [list(poly[0])]
    acc = 0.0
    for i in range(len(poly) - 1):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        pos = step - acc
        while pos <= seg:
            t = pos / seg
            out.append([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t])
            pos += step
        acc = (seg + acc) % step if acc < seg else acc - seg
        acc = (acc + seg) % step
    out.append(list(poly[-1]))
    return out


def smooth_chaikin(poly, iterations=2, closed=False):
    """Chaikin corner cutting — smooths without overshoot."""
    pts = [list(p) for p in poly]
    for _ in range(max(0, iterations)):
        if len(pts) < 3:
            return pts
        out = []
        n = len(pts)
        rng = range(n) if closed else range(n - 1)
        for i in rng:
            a, b = pts[i], pts[(i + 1) % n]
            out.append([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25])
            out.append([a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75])
        if not closed:
            out = [pts[0]] + out + [pts[-1]]
        pts = out
    return pts


def path_lengths(poly):
    out = [0.0]
    for i in range(len(poly) - 1):
        out.append(out[-1] + math.hypot(poly[i + 1][0] - poly[i][0], poly[i + 1][1] - poly[i][1]))
    return out


def point_at(poly, t):
    """Point at normalized arc-length t in [0,1]."""
    if not poly:
        return (0.0, 0.0)
    if len(poly) == 1 or t <= 0:
        return tuple(poly[0])
    ls = path_lengths(poly)
    total = ls[-1]
    if total == 0 or t >= 1:
        return tuple(poly[-1])
    target = t * total
    for i in range(len(ls) - 1):
        if ls[i + 1] >= target:
            f = (target - ls[i]) / (ls[i + 1] - ls[i]) if ls[i + 1] > ls[i] else 0
            return (poly[i][0] + (poly[i + 1][0] - poly[i][0]) * f,
                    poly[i][1] + (poly[i + 1][1] - poly[i][1]) * f)
    return tuple(poly[-1])


# ------------------------------------------------- boolean geometry ---------

def _mask_ops(polys_a, polys_b, w, h, op):
    ma = im.mask_from_polygons(w, h, polys_a) if polys_a else bytearray(w * h)
    mb = im.mask_from_polygons(w, h, polys_b) if polys_b else bytearray(w * h)
    if op == "union":
        return bytearray(1 if (a or b) else 0 for a, b in zip(ma, mb))
    if op == "difference":
        return bytearray(1 if (a and not b) else 0 for a, b in zip(ma, mb))
    if op == "intersection":
        return bytearray(1 if (a and b) else 0 for a, b in zip(ma, mb))
    if op == "exclusion":
        return bytearray(1 if bool(a) != bool(b) else 0 for a, b in zip(ma, mb))
    raise err("invalid_input", "unknown boolean op '%s'" % op)


def _dilate(mask, w, h, r):
    if r <= 0:
        return bytearray(mask)
    out = bytearray(mask)
    for _ in range(r):
        src = bytearray(out)
        for y in range(h):
            base = y * w
            for x in range(w):
                if not src[base + x]:
                    if (x > 0 and src[base + x - 1]) or (x < w - 1 and src[base + x + 1]) or \
                       (y > 0 and src[base - w + x]) or (y < h - 1 and src[base + w + x]):
                        out[base + x] = 1
    return out


def _erode(mask, w, h, r):
    if r <= 0:
        return bytearray(mask)
    out = bytearray(mask)
    for _ in range(r):
        src = bytearray(out)
        for y in range(h):
            base = y * w
            for x in range(w):
                if src[base + x]:
                    if (x > 0 and not src[base + x - 1]) or (x < w - 1 and not src[base + x + 1]) or \
                       (y > 0 and not src[base - w + x]) or (y < h - 1 and not src[base + w + x]):
                        out[base + x] = 0
    return out


def boolean_ops(polys_a, polys_b, op, grid=512, simplify_tol=0.8):
    """Boolean geometry via mask rasterization + contour tracing.

    Approximate but robust: results are polygon outlines on an integer grid of
    `grid` cells along the larger bbox axis, then simplified. Deterministic.
    """
    _, _ = _init()
    allp = [p for poly in (polys_a or []) + (polys_b or []) for p in poly]
    if not allp:
        return []
    x0, y0, bw, bh = pts_bbox((polys_a or []) + (polys_b or []))
    k = grid / float(max(bw, bh, 1))
    k = max(0.01, min(k, 50.0))

    def to_grid(polys):
        return transform_pts(polys, [k, 0, 0, k, -x0 * k, -y0 * k])

    w = max(2, int(bw * k) + 2)
    h = max(2, int(bh * k) + 2)
    mask = _mask_ops(to_grid(polys_a), to_grid(polys_b), w, h, op)
    loops = im.marching_squares(mask, w, h)
    inv = [k, 0, 0, k, x0, y0]
    back = simplify_polys(transform_pts(loops, inv), simplify_tol / max(k, 1e-9))
    return [p for p in back if len(p) >= 3]


def offset_polygons(polys, amount, grid=512, simplify_tol=0.8):
    """Positive = dilate (outset), negative = erode (inset)."""
    _, _ = _init()
    allp = [p for poly in polys for p in poly]
    if not allp:
        return []
    x0, y0, bw, bh = pts_bbox(polys)
    pad = abs(amount) * 3
    k = max(0.01, min(grid / float(max(bw + pad * 2, bh + pad * 2, 1)), 50.0))
    w = max(2, int((bw + pad * 2) * k) + 2)
    h = max(2, int((bh + pad * 2) * k) + 2)
    g = transform_pts(polys, [k, 0, 0, k, -(x0 - pad) * k, -(y0 - pad) * k])
    mask = im.mask_from_polygons(w, h, g)
    r = max(1, int(round(abs(amount) * k)))
    mask = _dilate(mask, w, h, r) if amount >= 0 else _erode(mask, w, h, r)
    loops = im.marching_squares(mask, w, h)
    inv = [1.0 / k, 0, 0, 1.0 / k, x0 - pad, y0 - pad]
    back = simplify_polys(transform_pts(loops, inv), simplify_tol / max(k, 1e-9))
    return [p for p in back if len(p) >= 3]


def stroke_to_outline(polys, width, grid=512):
    """Expand stroked paths into filled outlines (both sides + round joins)."""
    _, _ = _init()
    out = []
    for poly in polys:
        if len(poly) < 2:
            continue
        x0, y0, bw, bh = pts_bbox([poly])
        pad = width
        k = max(0.02, min(grid / float(max(bw + pad * 2, bh + pad * 2, 1)), 50.0))
        w = max(2, int((bw + pad * 2) * k) + 2)
        h = max(2, int((bh + pad * 2) * k) + 2)
        g = transform_pts([poly], [k, 0, 0, k, -(x0 - pad) * k, -(y0 - pad) * k])
        mask = bytearray(w * h)
        ls = path_lengths(poly)
        total = ls[-1] or 1.0
        steps = max(2, int(total / max(width * 0.25, 0.01)))
        for s in range(steps + 1):
            px, py = point_at(poly, s / float(steps))
            gx = int((px - (x0 - pad)) * k)
            gy = int((py - (y0 - pad)) * k)
            if 0 <= gx < w and 0 <= gy < h:
                mask[gy * w + gx] = 1
        mask = _dilate(mask, w, h, max(1, int(round(width * k / 2))))
        loops = im.marching_squares(mask, w, h)
        inv = [1.0 / k, 0, 0, 1.0 / k, x0 - pad, y0 - pad]
        out.extend([p for p in simplify_polys(transform_pts(loops, inv), 0.8 / max(k, 1e-9))
                    if len(p) >= 3])
    return out


def polys_to_svg_path(polys, prec=2):
    def f(v):
        return ("%." + str(prec) + "f") % v

    d = []
    for poly in polys:
        if len(poly) < 2:
            continue
        d.append("M%s %s" % (f(poly[0][0]), f(poly[0][1])))
        for p in poly[1:]:
            d.append("L%s %s" % (f(p[0]), f(p[1])))
        d.append("Z")
    return " ".join(d)


# ------------------------------------------------------------- SVG tools ----

_SVG_RISKY = {
    "filter": "filters are not supported by all consumers (rasterize or drop)",
    "text": "text requires font availability (convert to paths for delivery)",
    "textPath": "text-on-path rarely transfers (convert to paths)",
    "tspan": "rich text runs require font support (convert to paths)",
    "clipPath": "clipping may be ignored by simple consumers",
    "mask": "masks may be ignored by simple consumers",
    "pattern": "pattern fills may rasterize inconsistently",
    "image": "embedded images may use absolute or missing links",
    "font": "font-face declarations are deprecated in SVG 1.1",
    "foreignObject": "foreign content is not portable",
    "animate": "SMIL animation is not supported everywhere",
    "use": "use references require target ids to travel with the file",
}


def svg_audit(path_or_text):
    """Audit an SVG for features that may break in target consumers."""
    _, _ = _init()
    text = core.load_text(path_or_text) if not str(path_or_text).lstrip().startswith("<") \
        else str(path_or_text)
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise err("invalid_svg", "SVG is not well-formed XML: %s" % e)
    findings = []
    counts = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        counts[tag] = counts.get(tag, 0) + 1
        if tag in _SVG_RISKY:
            findings.append({"element": tag, "risk": _SVG_RISKY[tag],
                             "id": el.get("id", "")})
    hrefs = re.findall(r"xlink:href=[\"']([^\"']+)[\"']|href=[\"']([^\"']+)[\"']", text)
    for h1, h2 in hrefs:
        href = h1 or h2
        if href and (href.startswith("file:") or re.match(r"^[A-Za-z]:\\\\", href) or
                     href.startswith("/") and not href.startswith("/#")):
            findings.append({"element": "link", "risk": "absolute path reference: %s" % href,
                             "id": ""})
    if re.search(r"style\s*=\s*[\"'][^\"']*(font-family|mix-blend-mode)", text):
        findings.append({"element": "style", "risk":
                         "inline style uses font-family or mix-blend-mode "
                         "(inconsistent across consumers)", "id": ""})
    return {"elements": counts, "findings": findings,
            "risk_count": len(findings),
            "verdict": "clean" if not findings else
            ("%d risk(s) found" % len(findings))}


def svg_optimize(path_or_text, out_path=None, prec=2, strip_meta=True):
    """Minify SVG at the text level: drop comments/metadata/whitespace,
    round path coordinates, collapse redundant number formatting.
    The tree structure is preserved byte-for-byte elsewhere."""
    _, _ = _init()
    text = core.load_text(path_or_text) if not str(path_or_text).lstrip().startswith("<") \
        else str(path_or_text)
    out = text
    if strip_meta:
        out = re.sub(r"<!--.*?-->", "", out, flags=re.S)
        out = re.sub(r"<\?(?:xml-stylesheet|xml)\b[^>]*\?>", "", out)
        out = re.sub(r"<(?:metadata|desc|title)\b[^>]*>.*?</(?:metadata|desc|title)>", "",
                     out, flags=re.S | re.I)
        out = re.sub(r"<metadata\b[^>]*/>", "", out, flags=re.I)
    # round path data
    def round_d(m):
        return 'd="%s"' % _round_path_d(m.group(1), prec)
    out = re.sub(r'd="([^"]*)"', round_d, out)
    # round x/y/cx/cy/r/width/height/points attribute numbers
    def round_num_attr(m):
        name, val = m.group(1), m.group(2)
        return '%s="%s"' % (name, _round_path_d(val, prec))
    out = re.sub(r'\b(x|y|x1|y1|x2|y2|cx|cy|r|rx|ry|width|height|stroke-width)="([-\d.]+[, ][-\d., ]*)?"',
                 round_num_attr, out)
    out = re.sub(r"\bpoints=\"([^\"]*)\"", lambda m: 'points="%s"' % _round_path_d(m.group(1), prec),
                 out)
    # collapse whitespace between tags and strip trailing zeros like 1.50 -> 1.5
    out = re.sub(r">\s+<", "><", out)
    out = re.sub(r"(\d)\.0+(?=[\s\"<>,])", r"\1", out)
    out = re.sub(r"(\.\d*?)0+(?=[\s\"<>,])", r"\1", out)
    out = out.strip() + "\n"
    if out_path:
        core.save_text(out_path, out)
    return {"optimized_bytes": len(out.encode("utf-8")),
            "original_bytes": len(text.encode("utf-8")),
            "saved_bytes": len(text.encode("utf-8")) - len(out.encode("utf-8")),
            "path": out_path or None}


def _round_path_d(d, prec):
    def rnd(m):
        v = float(m.group(0))
        return ("%." + str(prec) + "f") % v
    return re.sub(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", rnd, d)


def svg_symbolize(path_or_text, out_path=None):
    """Find identical repeated subtrees and replace them with <defs> symbols +
    <use> clones (symbol/clone management for repeated props and parts)."""
    _, _ = _init()
    text = core.load_text(path_or_text) if not str(path_or_text).lstrip().startswith("<") \
        else str(path_or_text)
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise err("invalid_svg", "SVG is not well-formed XML: %s" % e)
    ser = {}

    def serialize(el):
        s = ET.tostring(el, encoding="unicode")
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r' id="[^"]*"', "", s)
        return s
    candidates = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("g", "path", "rect", "circle", "ellipse", "polygon", "polyline") and \
                len(list(el)) >= 0:
            s = serialize(el)
            if len(s) > 40:
                ser.setdefault(s, []).append(el)
    for s, els in ser.items():
        if len(els) >= 2:
            candidates.append(els)
    replaced = 0
    defs = None
    idx = 0
    for els in candidates:
        if any(e.get("data-studio-symbol") for e in els):
            continue
        idx += 1
        sid = "sym%d" % idx
        for el in els[1:]:
            parent_map = {c: p for p in root.iter() for c in p}
            parent = parent_map.get(el)
            if parent is None:
                continue
            pos = list(parent).index(el)
            parent.remove(el)
            use = ET.Element("use")
            use.set("href", "#" + sid)
            use.set("xlink:href", "#" + sid)
            if el.get("transform"):
                use.set("transform", el.get("transform"))
            parent.insert(pos, use)
            replaced += 1
        if replaced:
            if defs is None:
                defs = ET.SubElement(root, "defs")
                root.insert(list(root).index(defs), defs) if list(root).index(defs) else None
            sym = ET.SubElement(defs, "g")
            sym.set("id", sid)
            sym.append(els[0])
            # keep one instance visible where the first occurrence was
            use0 = ET.Element("use")
            use0.set("href", "#" + sid)
            use0.set("xlink:href", "#" + sid)
            if els[0].get("transform"):
                use0.set("transform", els[0].get("transform"))
            pm = {c: p for p in root.iter() for c in p}
            p0 = pm.get(els[0])
            if p0 is not None:
                pos0 = list(p0).index(els[0])
                p0.remove(els[0])
                p0.insert(pos0, use0)
    out = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    if out_path:
        core.save_text(out_path, out)
    return {"symbols_created": idx, "clones_replaced": replaced, "path": out_path or None}


def vector_to_raster(svg_path, out_path, width=None, height=None, scale=None,
                     bg=None, ss=2):
    """Rasterize an SVG at exact pixel size or scale (vector-to-raster export)."""
    _, _ = _init()
    text = core.load_text(svg_path)
    if scale and not width and not height:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
        vb = root.get("viewBox")
        m = re.match(r"([-\d.]+)", str(root.get("width", "")) or "")
        if vb:
            parts = [float(p) for p in re.findall(r"[-\d.]+", vb)[:4]]
            width = int(parts[2] * scale)
            height = int(parts[3] * scale)
        elif m:
            width = int(float(m.group(1)) * scale)
            m2 = re.match(r"([-\d.]+)", str(root.get("height", "")) or "")
            height = int((float(m2.group(1)) if m2 else width) * scale)
        else:
            raise err("invalid_svg", "SVG has no viewBox/size; pass --width/--height")
    img, meta = im.rasterize_svg(text, width=width, height=height, bg=bg, ss=ss)
    im.save_image(out_path, img)
    return {"size": [img.w, img.h], "warnings": meta.get("warnings", []), "path": out_path}


def shape_sheet(out_path, kinds=None, size=64, seed="shapes"):
    """Generate a sheet of reusable geometric primitives (shape language study)."""
    _, _ = _init()
    c = core
    rng = c.RNG("sheet:" + seed)
    kinds = kinds or ["circle", "square", "triangle", "roundrect", "capsule", "star", "wedge"]
    cols = min(7, len(kinds)) or 1
    rows = (len(kinds) + cols - 1) // cols
    sheet = im.new_image(cols * size, rows * size, (255, 255, 255, 255))
    for i, kind in enumerate(kinds):
        r, cc = divmod(i, cols)
        cx = cc * size + size // 2
        cy = r * size + size // 2
        s = size * 0.36
        shade = (int(40 + rng.random() * 80), int(40 + rng.random() * 80),
                 int(60 + rng.random() * 120), 255)
        if kind == "circle":
            im.draw_ellipse(sheet, cx, cy, s, s, shade)
        elif kind == "square":
            im.draw_rect(sheet, cx - s, cy - s, s * 2, s * 2, shade)
        elif kind == "triangle":
            im.fill_polygon(sheet, [[cx, cy - s], [cx + s, cy + s], [cx - s, cy + s]], shade)
        elif kind == "roundrect":
            pts = []
            rr = s * 0.45
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([cx - s + rr - rr * math.cos(a), cy - s + rr - rr * math.sin(a)])
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([cx + s - rr + rr * math.sin(a), cy - s + rr - rr * math.cos(a)])
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([cx + s - rr + rr * math.cos(a), cy + s - rr + rr * math.sin(a)])
            for k in range(6):
                a = math.pi / 2 * k / 6.0
                pts.append([cx - s + rr - rr * math.sin(a), cy + s - rr + rr * math.cos(a)])
            im.fill_polygon(sheet, pts, shade)
        elif kind == "capsule":
            im.fill_polygon(sheet, [[cx - s, cy - s * 0.4], [cx + s, cy - s * 0.4],
                                    [cx + s, cy + s * 0.4], [cx - s, cy + s * 0.4]], shade)
            im.draw_ellipse(sheet, cx - s, cy, s * 0.4, s * 0.4, shade)
            im.draw_ellipse(sheet, cx + s, cy, s * 0.4, s * 0.4, shade)
        elif kind == "star":
            pts = []
            for k in range(10):
                a = math.pi / 5 * k - math.pi / 2
                rr = s if k % 2 == 0 else s * 0.45
                pts.append([cx + rr * math.cos(a), cy + rr * math.sin(a)])
            im.fill_polygon(sheet, pts, shade)
        elif kind == "wedge":
            pts = [[cx - s, cy + s]]
            for k in range(13):
                a = -math.pi / 2 + math.pi * k / 12.0
                pts.append([cx + s * 0.6 * math.cos(a), cy + s * 0.6 * math.sin(a)])
            im.fill_polygon(sheet, pts, shade)
        im.draw_text(sheet, cc * size + 2, r * size + size - 10, kind[:9], (30, 30, 30, 255), 1, 1)
    if str(out_path).endswith(".svg"):
        cells = []
        for i, kind in enumerate(kinds):
            r, cc = divmod(i, cols)
            cells.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" '
                         'stroke="#999" stroke-width="1"/>' % (cc * size, r * size, size, size))
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">'
               '<rect width="100%%" height="100%%" fill="#fff"/>%s</svg>'
               % (cols * size, rows * size, "".join(cells)))
        core.save_text(out_path, svg)
    else:
        im.save_image(out_path, sheet)
    return {"kinds": kinds, "cells": len(kinds), "path": out_path}


def gradient_svg(stops, kind="linear", coords=None, units="objectBoundingBox"):
    """Build a reusable <linearGradient>/<radialGradient> defs snippet."""
    _, _ = _init()
    require(isinstance(stops, list) and stops, "invalid_input", "gradient needs stop list")
    if kind == "linear":
        attrs = dict(zip(("x1", "y1", "x2", "y2"), coords or (0, 0, 1, 0)))
    elif kind == "radial":
        attrs = dict(zip(("cx", "cy", "r", "fx", "fy"), coords or (0.5, 0.5, 0.5, 0.5, 0.5)))
    else:
        raise err("invalid_input", "gradient kind must be linear or radial")
    a = "".join(' %s="%s"' % (k, v) for k, v in attrs.items())
    s = "".join('<stop offset="%s" stop-color="%s" stop-opacity="%s"/>'
                % (st.get("offset", i / float(max(1, len(stops) - 1))),
                   st.get("color", "#000000"), st.get("opacity", 1))
                for i, st in enumerate(stops))
    gid = "grad%d" % (abs(hash(str(stops) + kind)) % 100000)
    return ('<%s id="%s" gradientUnits="%s"%s>%s</%s>'
            % (kind + "Gradient", gid, units, a, s, kind + "Gradient"))


def hatch_pattern_svg(gid="hatch", spacing=6, angle=45, color="#000000", width=1):
    """Reusable diagonal hatch pattern defs snippet."""
    return ('<pattern id="%s" width="%d" height="%d" patternUnits="userSpaceOnUse" '
            'patternTransform="rotate(%d)">'
            '<line x1="0" y1="0" x2="0" y2="%d" stroke="%s" stroke-width="%d"/></pattern>'
            % (gid, spacing, spacing, angle, spacing, color, width))


# ------------------------------------------------------------- typography ---

def read_font_coverage(path):
    """Read a TTF/OTF font's cmap table (pure struct parsing) and report
    covered Unicode ranges. No font rasterization — coverage data only."""
    _, _ = _init()
    data = core.read_bytes(path)
    require(data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"),
            "invalid_font", "not a TrueType/OpenType font: %s" % path)
    require(data[:4] != b"ttcf", "invalid_font", "font collections (ttcf) not supported")
    num_tables = struct.unpack(">H", data[4:6])[0]
    cmap_off = None
    for i in range(num_tables):
        rec = data[12 + i * 16:12 + i * 16 + 16]
        tag, _, off = struct.unpack(">4sII", rec[:12])
        if tag == b"cmap":
            cmap_off = off
            break
    require(cmap_off is not None, "invalid_font", "font has no cmap table")
    version, n = struct.unpack(">HH", data[cmap_off:cmap_off + 4])
    best = None
    best_score = -1
    for i in range(n):
        pid, eid, off = struct.unpack(">HHI", data[cmap_off + 4 + i * 8:cmap_off + 12 + i * 8])
        score = 0
        if (pid, eid) == (3, 10):
            score = 5
        elif (pid, eid) == (3, 1):
            score = 4
        elif (pid, eid) == (0, 4):
            score = 4
        elif (pid, eid) == (0, 3):
            score = 3
        elif (pid, eid) == (0, 0):
            score = 2
        if score > best_score:
            best_score = score
            best = cmap_off + off
    require(best is not None, "invalid_font", "no usable cmap subtable")
    fmt = struct.unpack(">H", data[best:best + 2])[0]
    codepoints = set()
    if fmt == 4:
        seg_count = struct.unpack(">H", data[best + 6:best + 8])[0] // 2
        end_base = best + 14
        start_base = end_base + seg_count * 2 + 2
        for i in range(seg_count):
            end = struct.unpack(">H", data[end_base + i * 2:end_base + i * 2 + 2])[0]
            start = struct.unpack(">H", data[start_base + i * 2:start_base + i * 2 + 2])[0]
            if start == 0xFFFF:
                continue
            codepoints.update(range(start, min(end, 0xFFFE) + 1))
    elif fmt == 12:
        ngroups = struct.unpack(">I", data[best + 12:best + 16])[0]
        for i in range(ngroups):
            s, e, _ = struct.unpack(">III", data[best + 16 + i * 12:best + 28 + i * 12])
            if e - s > 200000:
                continue
            codepoints.update(range(s, e + 1))
    else:
        raise err("invalid_font", "unsupported cmap format %d" % fmt)
    cps = sorted(codepoints)
    ranges = []
    for cp in cps:
        if ranges and cp == ranges[-1][1] + 1:
            ranges[-1][1] = cp
        else:
            ranges.append([cp, cp])
    scripts = {}
    blocks = _script_blocks()
    for cp in cps:
        for name, lo, hi in blocks:
            if lo <= cp <= hi:
                scripts[name] = scripts.get(name, 0) + 1
                break
    return {"glyph_count": len(cps), "ranges": ranges[:400],
            "scripts": dict(sorted(scripts.items(), key=lambda kv: -kv[1])),
            "covers_latin_basic": all(ord(ch) in codepoints for ch in
                                      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")}


def _script_blocks():
    return [
        ("Basic Latin", 0x20, 0x7E), ("Latin-1 Supplement", 0xA0, 0xFF),
        ("Latin Extended-A", 0x100, 0x17F), ("Latin Extended-B", 0x180, 0x24F),
        ("IPA Extensions", 0x250, 0x2AF), ("Greek and Coptic", 0x370, 0x3FF),
        ("Cyrillic", 0x400, 0x4FF), ("Hebrew", 0x590, 0x5FF), ("Arabic", 0x600, 0x6FF),
        ("Devanagari", 0x900, 0x97F), ("Thai", 0xE00, 0xE7F),
        ("CJK Unified Ideographs", 0x4E00, 0x9FFF), ("Hiragana", 0x3040, 0x309F),
        ("Katakana", 0x30A0, 0x30FF), ("Hangul Syllables", 0xAC00, 0xD7AF),
        ("Currency Symbols", 0x20A0, 0x20BF), ("Arrows", 0x2190, 0x21FF),
        ("Mathematical Operators", 0x2200, 0x22FF), ("Geometric Shapes", 0x25A0, 0x25FF),
        ("Private Use Area", 0xE000, 0xF8FF),
    ]


def check_glyph_coverage(font_path, text):
    """Verify a font covers every character in the supplied text."""
    _, _ = _init()
    cov = read_font_coverage(font_path)
    cps = _covered_set(cov)
    missing = []
    for ch in set(str(text)):
        if ch in ("\n", "\r", "\t"):
            continue
        if ord(ch) not in cps:
            missing.append(ch)
    return {"covered": not missing, "missing_characters": sorted(missing),
            "glyph_count": cov["glyph_count"],
            "fallback": "substitute characters or convert final text to outlines" if missing else None}


def _covered_set(cov):
    s = set()
    for lo, hi in cov["ranges"]:
        s.update(range(lo, hi + 1))
    return s


def typography_system(out_path, styles=None):
    """Write a reusable typography style system (JSON) with roles, sizes,
    spacing and fallback rules."""
    _, _ = _init()
    sys_ = {"version": "1.0", "styles": styles or [
        {"role": "title", "size_px": 72, "tracking": 2, "leading": 1.1, "weight": "bold",
         "align": "center", "fallback": "bundled 5x7 bitmap font"},
        {"role": "subtitle", "size_px": 36, "tracking": 1, "leading": 1.2, "weight": "regular",
         "align": "center", "fallback": "bundled 5x7 bitmap font"},
        {"role": "caption", "size_px": 24, "tracking": 0, "leading": 1.3, "weight": "regular",
         "align": "left", "fallback": "bundled 5x7 bitmap font"},
        {"role": "dialogue", "size_px": 22, "tracking": 0, "leading": 1.35, "weight": "regular",
         "align": "left", "min_contrast": 4.5},
        {"role": "credits", "size_px": 18, "tracking": 1, "leading": 1.5, "weight": "regular",
         "align": "center"},
        {"role": "interface", "size_px": 14, "tracking": 0, "leading": 1.2, "weight": "regular",
         "align": "left", "min_contrast": 3.0},
    ], "rules": [
        "minimum contrast ratios follow WCAG-like guidance (4.5 body, 3.0 large)",
        "all final-frame text is rendered or converted to outlines for delivery",
        "editable text sources are always preserved alongside outlined copies",
    ]}
    core.save_json(out_path, sys_)
    return sys_


def text_to_outlines(text, scale=1, tracking=0):
    """Convert text to editable polygon outlines using the bundled 5x7 utility
    font. Honest scope: this is a bitmap-derived outline font for burn-ins,
    slates and technical overlays — final typography still needs a real font
    application."""
    _, _ = _init()
    polys = []
    x = 0
    for ch in str(text):
        glyph = im.F5X7.get(ch, im.F5X7.get("?"))
        if glyph is None:
            x += (5 + tracking) * scale
            continue
        pts = []
        for gy, row in enumerate(glyph):
            for gx in range(5):
                if row & (1 << (4 - gx)):
                    pts.append([x + gx * scale, gy * scale])
        if pts:
            polys.append([[x + 0, 0], [x + 5 * scale, 0], [x + 5 * scale, 7 * scale],
                          [x + 0, 7 * scale]])
        x += (5 + tracking) * scale
    # build per-glyph outline masks via the boolean-safe grid path
    all_polys = []
    x = 0
    for ch in str(text):
        glyph = im.F5X7.get(ch, im.F5X7.get("?"))
        if glyph is not None:
            g = im.new_image(5, 7)
            for gy, row in enumerate(glyph):
                for gx in range(5):
                    if row & (1 << (4 - gx)):
                        g.set(gx, gy, (255, 255, 255, 255))
            mask = bytearray(1 if g.px[p * 4 + 3] else 0 for p in range(35))
            loops = im.marching_squares(mask, 5, 7)
            for loop in loops:
                all_polys.append([[p[0] * scale + x, p[1] * scale] for p in loop])
        x += (5 + tracking) * scale
    return {"polys": all_polys, "size": [x - tracking * scale if text else 0, 7 * scale],
            "svg_path": polys_to_svg_path(all_polys)}


def speech_balloon(out_path, text, style="oval", width=320, tail=(0.7, 1.0),
                   bg=(255, 255, 255, 255), fg=(0, 0, 0, 255), text_color=(0, 0, 0, 255)):
    """Render a speech balloon with tail and wrapped bitmap text (PNG) or SVG."""
    _, _ = _init()
    import xml.etree.ElementTree as ET
    words = str(text).split()
    lines = []
    cur = ""
    max_chars = max(8, (width - 24) // 6)
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    tw = max(im.text_width(l) for l in lines) if lines else 10
    th = len(lines) * 9 + 8
    bw = max(tw + 28, 60)
    bh = max(th + 16, 40)
    if str(out_path).endswith(".svg"):
        if style == "oval":
            shape = '<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="#%02x%02x%02x" ' \
                    'stroke="#%02x%02x%02x" stroke-width="2"/>' % (
                        bw // 2, bh // 2, bw // 2, bh // 2, bg[0], bg[1], bg[2],
                        fg[0], fg[1], fg[2])
        elif style == "box":
            shape = '<rect x="1" y="1" width="%d" height="%d" rx="6" fill="#%02x%02x%02x" ' \
                    'stroke="#%02x%02x%02x" stroke-width="2"/>' % (
                        bw - 2, bh - 2, bg[0], bg[1], bg[2], fg[0], fg[1], fg[2])
        else:  # burst
            pts = []
            import random as _r
            rr = _r.Random(7)
            for k in range(24):
                a = 2 * math.pi * k / 24.0
                rx = bw / 2 * (0.75 + rr.random() * 0.3)
                ry = bh / 2 * (0.75 + rr.random() * 0.3)
                pts.append("%d,%d" % (bw / 2 + rx * math.cos(a), bh / 2 + ry * math.sin(a)))
            shape = '<polygon points="%s" fill="#%02x%02x%02x" stroke="#%02x%02x%02x" ' \
                    'stroke-width="2"/>' % (" ".join(pts), bg[0], bg[1], bg[2],
                                            fg[0], fg[1], fg[2])
        tx, ty = tail
        tail_pts = "%d,%d %d,%d %d,%d" % (
            bw * tx - 10, bh * ty - 6, bw * tx, bh * ty + 26, bw * tx + 12, bh * ty - 6)
        texts = "".join(
            '<text x="%d" y="%d" font-family="monospace" font-size="8">%s</text>'
            % (bw // 2, 20 + i * 9, l) for i, l in enumerate(lines))
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">%s'
               '<polygon points="%s" fill="#%02x%02x%02x"/>%s</svg>'
               % (bw, bh + 30, shape, tail_pts, bg[0], bg[1], bg[2], texts))
        core.save_text(out_path, svg)
        return {"size": [bw, bh + 30], "lines": lines, "path": out_path, "format": "svg"}
    img = im.new_image(bw, bh + 30, (0, 0, 0, 0))
    if style == "oval":
        im.draw_ellipse(img, bw // 2, bh // 2, bw // 2 - 2, bh // 2 - 2, bg)
        im.draw_ellipse(img, bw // 2, bh // 2, bw // 2 - 2, bh // 2 - 2, fg, filled=False)
    elif style == "box":
        im.draw_rect(img, 1, 1, bw - 2, bh - 2, bg)
        im.draw_rect(img, 1, 1, bw - 2, bh - 2, fg, filled=False)
    else:
        pts = []
        rr = core.RNG("burst").random
        for k in range(24):
            a = 2 * math.pi * k / 24.0
            rx = bw / 2 * (0.75 + rr() * 0.3)
            ry = bh / 2 * (0.75 + rr() * 0.3)
            pts.append([bw / 2 + rx * math.cos(a), bh / 2 + ry * math.sin(a)])
        im.fill_polygon(img, pts, bg)
        im.draw_polyline(img, pts + [pts[0]], fg, 1)
    tx, ty = tail
    im.fill_polygon(img, [[bw * tx - 10, bh * ty - 4], [bw * tx, min(bh + 26, bh * ty + 24)],
                          [bw * tx + 12, bh * ty - 4]], bg)
    y = (bh - len(lines) * 9) // 2 + 7
    for l in lines:
        im.draw_text(img, (bw - im.text_width(l)) // 2, y, l, text_color, 1, 1)
        y += 9
    im.save_image(out_path, img)
    return {"size": [bw, bh + 30], "lines": lines, "path": out_path, "format": "png"}


def credits_card(out_path, title, names, size=(640, 360), bg=(10, 10, 14, 255),
                 color=(240, 240, 240, 255), accent=(255, 200, 60, 255)):
    """Render an offline credits / attribution card (PNG)."""
    _, _ = _init()
    img = im.new_image(size[0], size[1], bg)
    im.draw_rect(img, 0, 0, size[0], 6, accent)
    im.draw_rect(img, 0, size[1] - 6, size[0], 6, accent)
    im.draw_text(img, 20, 26, str(title)[:60], accent, 2, 1)
    y = 70
    for entry in names:
        if isinstance(entry, dict):
            name, role = entry.get("name", ""), entry.get("role", "")
        else:
            name, role = str(entry), ""
        im.draw_text(img, 24, y, name[:34], color, 1, 1)
        if role:
            im.draw_text(img, size[0] - 24 - im.text_width(role[:24]), y, role[:24],
                         (160, 160, 160, 255), 1, 1)
        y += 14
        if y > size[1] - 30:
            im.draw_text(img, 24, y, "...", color, 1, 1)
            break
    im.draw_text(img, 20, size[1] - 20, "OFFLINE RENDER - ALL RIGHTS PER LICENSE FILE",
                 (120, 120, 120, 255), 1, 1)
    im.save_image(out_path, img)
    return {"path": out_path, "entries": len(names), "size": list(size)}


# ----------------------------------------------- vector document (4.1) ------

def svg_document(title, canvas, layers, metadata=None, out_path=None):
    """Vector Document Architect: structured SVG scaffold — named layer
    groups, canvas, units, and metadata — ready to draw in."""
    _init()
    w, h = int(canvas[0]), int(canvas[1])
    require(w > 0 and h > 0, "invalid_input", "canvas must be positive")
    units = "px"
    meta = metadata or {}
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d%s" height="%d%s"'
             ' viewBox="0 0 %d %d">' % (w, units, h, units, w, h),
             '  <title>%s</title>' % _esc(str(title))]
    if meta:
        parts.append('  <desc>%s</desc>' % _esc(str(meta)))
    for name in layers or ["background", "midground", "foreground"]:
        gid = slugify_layer(name)
        parts.append('  <g id="%s" inkscape:label="%s" '
                     'inkscape:groupmode="layer" '
                     'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
                     '</g>' % (gid, _esc(str(name))))
    parts.append('</svg>')
    doc = "\n".join(parts)
    if out_path:
        core.save_text(out_path, doc)
    return {"svg": doc, "canvas": [w, h], "units": units,
            "layers": [slugify_layer(n) for n in (layers or
                        ["background", "midground", "foreground"])],
            "path": str(out_path) if out_path else None}


def slugify_layer(name):
    _init()
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "layer"


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))
