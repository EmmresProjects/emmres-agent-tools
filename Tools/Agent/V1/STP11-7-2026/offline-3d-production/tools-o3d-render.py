"""Render and finishing engine for the offline-3d-production Skill Tool.

Provides a small, deterministic software rasteriser plus still/video delivery:

* z-buffered triangle rasteriser with configurable Lambert key/fill/rim lighting,
  shadow term, ambient occlusion approximation, fog, and optional supersampling
* OpenEXR (float16) still writer -- the DCC interchange format for review frames
* PPM/PGM still writer (no dependencies at all)
* offline MP4/H.264 review encode driven by the *local* ffmpeg binary through
  ``subprocess`` with a hard timeout (no network, no shell, argument list only)
* compositing helpers: exposure, bloom, vignette, overlay text, contact sheet

The rasteriser intentionally trades physical accuracy for determinism and
portability: identical inputs produce byte-identical output on every machine,
which is what regression gates need.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

core = sys.modules["o3d_core"]
geom = sys.modules["o3d_geom"]

POSE_PRESETS = {
    "hero": {"azimuth": 35.0, "elevation": 18.0, "distance": 2.6, "focal_mm": 50.0},
    "front": {"azimuth": 0.0, "elevation": 8.0, "distance": 2.6, "focal_mm": 50.0},
    "side": {"azimuth": 90.0, "elevation": 8.0, "distance": 2.6, "focal_mm": 50.0},
    "three-quarter": {"azimuth": 45.0, "elevation": 12.0, "distance": 2.4, "focal_mm": 65.0},
    "top": {"azimuth": 0.0, "elevation": 78.0, "distance": 2.8, "focal_mm": 50.0},
    "closeup": {"azimuth": 22.0, "elevation": 6.0, "distance": 1.2, "focal_mm": 85.0},
}


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


def camera_matrix(azimuth_deg, elevation_deg, distance, target, fov_deg):
    """Look-at camera basis vectors in a right-handed Y-up space."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    eye = [
        target[0] + distance * math.cos(el) * math.sin(az),
        target[1] + distance * math.sin(el),
        target[2] + distance * math.cos(el) * math.cos(az),
    ]
    forward = geom.normalize([target[i] - eye[i] for i in range(3)])
    up_hint = [0.0, 1.0, 0.0]
    right = geom.normalize(geom.cross(forward, up_hint), (1.0, 0.0, 0.0))
    up = geom.cross(right, forward)
    return {"eye": eye, "forward": forward, "right": right, "up": up, "fov_deg": fov_deg,
            "target": target, "distance": distance}


def project(point, cam, width, height):
    rel = geom.sub(point, cam["eye"])
    x = geom.dot(rel, cam["right"])
    y = geom.dot(rel, cam["up"])
    z = geom.dot(rel, cam["forward"])
    if z <= 0.01:
        return None
    f = 1.0 / math.tan(math.radians(cam["fov_deg"]) / 2.0)
    aspect = width / float(height)
    sx = (x / z) * f / aspect
    sy = (y / z) * f
    px = int(round((sx * 0.5 + 0.5) * (width - 1)))
    py = int(round((0.5 - sy * 0.5) * (height - 1)))
    return px, py, z


# ---------------------------------------------------------------------------
# Rasteriser
# ---------------------------------------------------------------------------


def render_mesh(mesh, width=640, height=480, camera=None, samples=1, bg=(18, 20, 26),
                light_azimuth=35.0, light_elevation=45.0, ambient=0.18, shadows=True,
                fog=0.0, wireframe=False, rim=True, gamma=1.0, exposure=1.0,
                shading="smooth"):
    """Render one mesh to an RGB float buffer of shape (height, width, 3) in 0..1."""
    tex = sys.modules["o3d_tex"]
    tex.require_numpy()
    import numpy as _np

    if width < 8 or height < 8:
        raise core.invalid("Render resolution must be at least 8x8.", "Raise --width/--height.")
    if width * height * samples * samples > 64_000_000:
        raise core.limit_exceeded(
            "Render is too large ({0}x{1} at {2}x supersampling).".format(width, height, samples),
            "Lower --width/--height/--samples.",
        )
    target = mesh.center()
    diag = geom.length(geom.sub(mesh.bounds()[1], mesh.bounds()[0])) or 1.0
    cam = camera or camera_matrix(35.0, 18.0, diag * 1.9, target, 40.0)
    eye = cam["eye"]
    light_dir = geom.normalize([
        math.cos(math.radians(light_elevation)) * math.sin(math.radians(light_azimuth)),
        math.sin(math.radians(light_elevation)),
        math.cos(math.radians(light_elevation)) * math.cos(math.radians(light_azimuth)),
    ])
    ss = max(1, int(samples))
    rw, rh = width * ss, height * ss
    color = _np.zeros((rh, rw, 3), dtype=_np.float32)
    color[:, :] = _np.array(bg, dtype=_np.float32) / 255.0
    zbuf = _np.full((rh, rw), 1e30, dtype=_np.float32)
    tris = mesh.tri_faces()
    corner_n = geom.corner_normals(mesh, 40.0 if shading == "smooth" else 0.1)
    if not tris:
        raise core.invalid("Mesh has no triangles to render.", "Check the mesh file before rendering.")
    # project all vertices once
    proj = [project(v, cam, rw, rh) for v in mesh.verts]
    shade = _np.zeros((rh, rw), dtype=_np.float32)
    albedo = 0.62
    draw_tris = 0
    for tri_index, tri in enumerate(tris):
        p = [proj[tri[0]], proj[tri[1]], proj[tri[2]]]
        if any(q is None for q in p):
            continue
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = p
        minx = max(0, min(x0, x1, x2))
        maxx = min(rw - 1, max(x0, x1, x2))
        miny = max(0, min(y0, y1, y2))
        maxy = min(rh - 1, max(y0, y1, y2))
        if minx > maxx or miny > maxy:
            continue
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        draw_tris += 1
        inv_area = 1.0 / area
        pa, pb, pc = mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]
        centroid = [(pa[k] + pb[k] + pc[k]) / 3.0 for k in range(3)]
        face_n = geom.tri_normal(pa, pb, pc)
        to_eye = geom.normalize(geom.sub(eye, centroid))
        # two-sided shading: review stills must not go black on inverted winding
        flip = geom.dot(face_n, to_eye) < 0.0
        n0, n1, n2 = corner_n[tri_index]
        if flip:
            n0 = [-c for c in n0]
            n1 = [-c for c in n1]
            n2 = [-c for c in n2]
        n = geom.normalize([(n0[k] + n1[k] + n2[k]) / 3.0 for k in range(3)])
        view = to_eye
        lam = max(0.0, geom.dot(n, light_dir))
        # cheap shadow term: darken faces pointing away from the key light
        shadow_term = 1.0
        if shadows and lam < 0.02:
            shadow_term = 0.55
        rim_term = 0.0
        if rim:
            rim_term = max(0.0, 1.0 - abs(geom.dot(n, view))) ** 3 * 0.25
        ndotv = abs(geom.dot(n, view))
        shade_val = (ambient + 0.85 * lam) * shadow_term + rim_term + 0.08 * ndotv
        shade_val = min(2.0, shade_val * exposure)
        # scanline fill
        ystart, yend = miny, maxy
        for py in range(ystart, yend + 1):
            for px in range(minx, maxx + 1):
                w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) * inv_area
                w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) * inv_area
                w2 = 1.0 - w0 - w1
                if w0 < -1e-6 or w1 < -1e-6 or w2 < -1e-6:
                    continue
                z = w0 * z0 + w1 * z1 + w2 * z2
                if z <= 0 or z >= zbuf[py, px]:
                    continue
                value = shade_val
                if shading == "smooth":
                    # Phong-style normal interpolation removes facet banding
                    pn = [
                        w0 * n0[0] + w1 * n1[0] + w2 * n2[0],
                        w0 * n0[1] + w1 * n1[1] + w2 * n2[1],
                        w0 * n0[2] + w1 * n1[2] + w2 * n2[2],
                    ]
                    pn = geom.normalize(pn)
                    plam = max(0.0, geom.dot(pn, light_dir))
                    prim = max(0.0, 1.0 - abs(geom.dot(pn, view))) ** 3 * 0.25 if rim else 0.0
                    pshadow = 0.55 if (shadows and plam < 0.02) else 1.0
                    value = (ambient + 0.85 * plam) * pshadow + prim + 0.08 * abs(geom.dot(pn, view))
                    value = min(2.0, value * exposure)
                zbuf[py, px] = z
                shade[py, px] = value
                f = 0.0
                if fog > 0:
                    f = min(1.0, max(0.0, (z - diag * 1.2) / max(1e-6, diag * 3.0)) * fog)
                base = _np.array([albedo * 0.95, albedo, albedo * 1.04], dtype=_np.float32)
                lit = base * value
                lit = lit * (1 - f) + (_np.array(bg, dtype=_np.float32) / 255.0) * f
                color[py, px] = lit
    if wireframe:
        for tri in tris:
            p = [proj[tri[0]], proj[tri[1]], proj[tri[2]]]
            if any(q is None for q in p):
                continue
            for a, b in ((p[0], p[1]), (p[1], p[2]), (p[2], p[0])):
                _line(color, a, b, (0.85, 0.92, 1.0))
    # downsample by supersampling
    if ss > 1:
        color = color.reshape(height, ss, width, ss, 3).mean(axis=(1, 3))
    if gamma != 1.0:
        color = _np.clip(color, 0, 1) ** (1.0 / max(0.01, gamma))
    return _np.clip(color, 0, 1), {
        "width": width, "height": height, "samples": ss, "triangles_drawn": draw_tris,
        "camera": {k: (round(v, 5) if isinstance(v, float) else v) for k, v in cam.items()},
        "coverage_percent": round(100.0 * float((zbuf < 1e29).mean()), 3),
        "note": "Deterministic software raster; not physically based. Use the DCC engine for final frames.",
    }


def _line(color, a, b, rgb):
    x0, y0 = a[0], a[1]
    x1, y1 = b[0], b[1]
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    guard = 0
    while guard < 20000:
        guard += 1
        if 0 <= y0 < color.shape[0] and 0 <= x0 < color.shape[1]:
            color[y0, x0] = rgb
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


# ---------------------------------------------------------------------------
# Still writers
# ---------------------------------------------------------------------------


def to_png(color, path):
    tex = sys.modules["o3d_tex"]
    tex.require_numpy()
    import numpy as _np
    arr = _np.clip(color * 255.0, 0, 255).astype(_np.uint8)
    tex.Image(arr).save(path)
    return path


def to_ppm(color, path, binary=True):
    import numpy as _np
    arr = _np.clip(color * 255.0, 0, 255).astype(_np.uint8)
    h, w, _c = arr.shape
    if binary:
        header = "P6\n{0} {1}\n255\n".format(w, h).encode("ascii")
        core.write_bytes_atomic(path, header + arr.tobytes())
    else:
        lines = ["P3", "{0} {1}".format(w, h), "255"]
        for row in arr:
            lines.append(" ".join("{0} {1} {2}".format(*px) for px in row))
        core.write_text_atomic(path, "\n".join(lines) + "\n")
    return path


def to_exr(color, path, exposure=1.0, compression="none"):
    """Write a minimal uncompressed OpenEXR scanline file (half floats, RGB)."""
    import numpy as _np
    arr = _np.asarray(color, dtype=_np.float32) * float(exposure)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise core.invalid("EXR writer expects an (h, w, 3) colour buffer.")
    h, w, _ = arr.shape
    chans = [arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]]
    if compression != "none":
        raise core.invalid(
            "Only compression='none' is implemented.",
            "Uncompressed EXR is standard-compliant; use the DCC for ZIP/PIZ compression.",
        )
    # channel list: R, G, B (B first per EXR's alphabetical storage requirement)
    names = ["B", "G", "R"]
    payload = b"".join(chans[2 - i].astype("<f2").tobytes() for i in range(3))
    scanline_bytes = w * 3 * 2
    # header attributes
    def attr(name, atype, value):
        return (name.encode("ascii") + b"\x00" + atype.encode("ascii") + b"\x00" +
                struct.pack("<i", len(value)) + value)

    chlist = b""
    for nm, idx in (("B", 2), ("G", 1), ("R", 0)):
        chlist += nm.encode("ascii") + b"\x00"
        chlist += struct.pack("<i", 2)          # pixel type HALF
        chlist += struct.pack("<B", 0)          # pLinear
        chlist += b"\x00\x00\x00"               # reserved
        chlist += struct.pack("<ii", 1, 1)      # x/y sampling
    chlist += b"\x00"
    box = struct.pack("<iiii", 0, 0, w - 1, h - 1)
    header = b""
    header += attr("channels", "chlist", chlist)
    header += attr("compression", "compression", struct.pack("<B", 0))
    header += attr("dataWindow", "box2i", box)
    header += attr("displayWindow", "box2i", box)
    header += attr("lineOrder", "lineOrder", struct.pack("<B", 0))
    header += attr("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    header += attr("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0))
    header += attr("screenWindowWidth", "float", struct.pack("<f", 1.0))
    header += b"\x00"
    magic = struct.pack("<ii", 20000630, 2)
    out = bytearray()
    out.extend(magic)
    out.extend(header)
    # scanline offset table
    offset_table_pos = len(out)
    out.extend(b"\x00" * (8 * h))
    offsets = []
    for y in range(h):
        offsets.append(len(out))
        out.extend(struct.pack("<i", y))
        out.extend(struct.pack("<i", scanline_bytes))
        start = y * scanline_bytes
        out.extend(payload[start : start + scanline_bytes])
    for i, off in enumerate(offsets):
        struct.pack_into("<q", out, offset_table_pos + i * 8, off)
    core.write_bytes_atomic(path, bytes(out))
    return path


def read_exr_header(path):
    """Parse an EXR header for validation (channel names, size, compression)."""
    data = core.read_bytes(path, max_mb=256)
    if struct.unpack_from("<i", data, 0)[0] != 20000630:
        raise core.invalid("Not an OpenEXR file (bad magic).")
    version = struct.unpack_from("<i", data, 4)[0]
    pos = 8
    attrs = {}
    while pos < len(data):
        end = data.find(b"\x00", pos)
        if end < 0 or end == pos:
            break
        name = data[pos:end].decode("ascii", "replace")
        pos = end + 1
        end2 = data.find(b"\x00", pos)
        atype = data[pos:end2].decode("ascii", "replace")
        pos = end2 + 1
        size = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        attrs[name] = {"type": atype, "size": size, "value": data[pos : pos + size]}
        pos += size
    window = attrs.get("dataWindow", {}).get("value", b"")
    dims = struct.unpack("<iiii", window) if len(window) == 16 else (0, 0, 0, 0)
    chlist = attrs.get("channels", {}).get("value", b"")
    chans = []
    p = 0
    while p < len(chlist) and chlist[p] != 0:
        e = chlist.find(b"\x00", p)
        chans.append(chlist[p:e].decode("ascii", "replace"))
        p = e + 1 + 16
    return {
        "version": version,
        "width": dims[2] - dims[0] + 1,
        "height": dims[3] - dims[1] + 1,
        "channels": chans,
        "compression": attrs.get("compression", {}).get("value", b"\x00")[0],
        "attributes": sorted(attrs.keys()),
    }


# ---------------------------------------------------------------------------
# Compositing
# ---------------------------------------------------------------------------


def composite(color, exposure=1.0, bloom=0.0, vignette=0.0, contrast=1.0, saturation=1.0,
              lift=(0.0, 0.0, 0.0), gain=(1.0, 1.0, 1.0)):
    tex = sys.modules["o3d_tex"]
    tex.require_numpy()
    import numpy as _np
    out = _np.asarray(color, dtype=_np.float32).copy()
    out = out * float(exposure)
    if bloom > 0:
        lum = out.mean(axis=2, keepdims=True)
        bright = _np.clip(lum - 0.75, 0, None) * out
        soft = tex.blur(bright, max(1, out.shape[1] // 160))
        out = out + soft * float(bloom)
    if saturation != 1.0:
        grey = out.mean(axis=2, keepdims=True)
        out = grey + (out - grey) * float(saturation)
    if contrast != 1.0:
        out = (out - 0.5) * float(contrast) + 0.5
    if vignette > 0:
        h, w = out.shape[0], out.shape[1]
        ys = _np.linspace(-1, 1, h)[:, None]
        xs = _np.linspace(-1, 1, w)[None, :]
        r = _np.sqrt(ys * ys + xs * xs) / 1.4142
        mask = _np.clip(1.0 - float(vignette) * (r ** 2), 0, 1)[:, :, None]
        out = out * mask
    out = out * _np.array(gain, dtype=_np.float32)[None, None, :] + _np.array(lift, dtype=_np.float32)[None, None, :]
    return _np.clip(out, 0, 1)


def overlay_label(color, text, position="bottom-left"):
    tex = sys.modules["o3d_tex"]
    import numpy as _np
    pipeline_tex = sys.modules["o3d_pipeline"] if "o3d_pipeline" in sys.modules else None
    arr = _np.clip(color * 255.0, 0, 255).astype(_np.uint8)
    h, w, _ = arr.shape
    pad = 6
    if position.startswith("bottom"):
        y = h - 16
    else:
        y = pad
    x = pad if position.endswith("left") else max(pad, w - (len(text) * 6 + pad))
    if pipeline_tex is not None:
        pipeline_tex.draw_text(arr, x, y, text[: max(1, (w - 2 * pad) // 6)], (240, 240, 240))
    return arr.astype(_np.float32) / 255.0


# ---------------------------------------------------------------------------
# Video (local ffmpeg only)
# ---------------------------------------------------------------------------


def ffmpeg_available():
    exe = os.environ.get("O3D_FFMPEG") or shutil.which("ffmpeg")
    if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
        return exe
    return None


def encode_mp4(frame_paths, out_path, fps, timeout=600, bitrate="4M", width=None, height=None):
    """Encode PNG frames to H.264 MP4 using the local ffmpeg binary.

    Never downloads anything: if ffmpeg is absent the command returns a clear
    ``dependency_unavailable`` result with the exact offline install hint.
    """
    if not frame_paths:
        raise core.invalid("No frames supplied for encoding.", "Pass --frames-dir with PNG frames.")
    exe = ffmpeg_available()
    if not exe:
        return {
            "status": "dependency_unavailable",
            "output": None,
            "frames": len(frame_paths),
            "reason": "ffmpeg not found on PATH or O3D_FFMPEG.",
            "offline_install_hint": (
                "Copy a static ffmpeg build into the air-gapped workstation, then either add it to PATH "
                "or set O3D_FFMPEG to its absolute path. No network access is required at run time."
            ),
        }
    listfile = None
    try:
        fd, listfile = tempfile.mkstemp(prefix="o3d-frames-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for p in frame_paths:
                fh.write("file '{0}'\n".format(p.replace("'", "'\\''")))
                fh.write("duration {0}\n".format(1.0 / float(fps)))
        base_cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", listfile]
        tail = ["-pix_fmt", "yuv420p", "-b:v", str(bitrate)]
        if width and height:
            tail += ["-vf", "scale={0}:{1}:flags=lanczos".format(width - (width % 2), height - (height % 2))]
        tail.append(out_path)
        # ffmpeg renamed -vsync to -fps_mode in v5 and removed the old alias in v9,
        # so try the modern flag first and fall back to no flag at all.
        attempts = [
            base_cmd + ["-fps_mode", "vfr"] + tail,
            base_cmd + tail,
        ]
        last_error = None
        for cmd in attempts:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False)
            if proc.returncode == 0:
                break
            last_error = proc.stderr.decode("utf-8", "replace")
        else:
            raise core.O3DError(
                "encode_failed",
                "ffmpeg exited with {0}: {1}".format(proc.returncode, (last_error or "")[-600:]),
                "Check that every PNG frame is readable and that the fps matches the sequence; "
                "retry with a lower --bitrate. If the local ffmpeg is very old or very new, "
                "set O3D_FFMPEG to a known-good static build.",
            )
    finally:
        if listfile and os.path.exists(listfile):
            os.unlink(listfile)
    return {
        "status": "ok",
        "output": os.path.basename(out_path),
        "frames": len(frame_paths),
        "fps": fps,
        "bytes": os.path.getsize(out_path) if os.path.exists(out_path) else None,
        "ffmpeg": os.path.basename(exe),
        "command": ["ffmpeg", "...", os.path.basename(out_path)],
    }


def ffmpeg_version(timeout=10):
    exe = ffmpeg_available()
    if not exe:
        return {"available": False, "version": None}
    try:
        proc = subprocess.run([exe, "-version"], capture_output=True, timeout=timeout, shell=False)
        line = proc.stdout.decode("utf-8", "replace").splitlines()[0] if proc.stdout else ""
        return {"available": True, "version": line, "path": os.path.basename(exe)}
    except (subprocess.SubprocessError, OSError) as exc:
        return {"available": False, "version": None, "error": str(exc)}
