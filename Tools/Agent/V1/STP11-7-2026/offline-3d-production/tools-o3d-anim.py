"""Animation, rigging and motion engine for the offline-3d-production Skill Tool.

Pure standard library. Provides:

* cubic Hermite / Bezier F-curve evaluation with named easing presets
* keyframe optimisation (Ramer-Douglas-Peucker in time-value space)
* BVH mocap parsing (with a deterministic synthetic capture generator for tests)
* hip-normalised retargeting between skeletons with different proportions
* root-motion extraction and foot-contact detection
* audio-envelope-driven viseme lip-sync (offline, no model download)
* WebVTT / SRT caption export

Nothing here touches the network: "solving" is deterministic maths, and every
randomised helper takes an explicit seed.
"""

from __future__ import annotations

import math
import struct
import sys
import wave

core = sys.modules["o3d_core"]

FPS_DEFAULT = 24
EASING = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "smooth": (0.42, 0.0, 0.58, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
    "anticipate": (0.36, 0.0, 0.66, -0.56),
    "overshoot": (0.34, 1.56, 0.64, 1.0),
    "step": (0.0, 0.0, 0.0, 1.0),
}


# ---------------------------------------------------------------------------
# Curve maths
# ---------------------------------------------------------------------------


def cubic_bezier(t, c1x, c1y, c2x, c2y):
    """Solve y for x=t on a CSS-style cubic-bezier by deterministic bisection."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2.0
        x = 3 * (1 - mid) ** 2 * mid * c1x + 3 * (1 - mid) * mid ** 2 * c2x + mid ** 3
        if x < t:
            lo = mid
        else:
            hi = mid
    u = (lo + hi) / 2.0
    return 3 * (1 - u) ** 2 * u * c1y + 3 * (1 - u) * u ** 2 * c2y + u ** 3


def eval_curve(keys, frame, easing="smooth", extrapolation="constant"):
    """Evaluate scalar keys ``[(frame, value), ...]`` at ``frame``.

    ``easing`` may be a preset name from :data:`EASING` or explicit control points.
    """
    if not keys:
        return 0.0
    keys = sorted(keys, key=lambda k: k[0])
    if len(keys) == 1:
        return keys[0][1]
    if frame <= keys[0][0]:
        if extrapolation == "linear" and len(keys) > 1:
            return keys[0][1]
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    for i in range(len(keys) - 1):
        f0, v0 = keys[i]
        f1, v1 = keys[i + 1]
        if f0 <= frame <= f1:
            span = float(f1 - f0) or 1.0
            t = (frame - f0) / span
            if easing == "step":
                return v0
            if easing == "smooth":
                t = t * t * (3 - 2 * t)
            elif easing in EASING:
                c1x, c1y, c2x, c2y = EASING[easing]
                t = cubic_bezier(t, c1x, c1y, c2x, c2y)
            elif isinstance(easing, (list, tuple)) and len(easing) == 4:
                t = cubic_bezier(t, *easing)
            return v0 + (v1 - v0) * t
    return keys[-1][1]


def resample(keys, fps, start=1, end=None, easing="smooth", step=1):
    if not keys:
        raise core.invalid("Cannot resample an empty curve.", "Provide at least one key.")
    end = end if end is not None else max(k[0] for k in keys)
    start = start if start is not None else min(k[0] for k in keys)
    out = []
    f = start
    while f <= end:
        out.append((f, round(eval_curve(keys, f, easing), 6)))
        f += step
    return out


def rdp_simplify(points, tolerance):
    """Ramer-Douglas-Peucker over [(frame, value)] with vertical+horizontal error."""
    if len(points) < 3 or tolerance <= 0:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    scale_f = max(1e-6, (points[-1][0] - points[0][0])) * 0.001
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        f0, v0 = points[a]
        f1, v1 = points[b]
        best_i, best_d = -1, 0.0
        for i in range(a + 1, b):
            fi, vi = points[i]
            if f1 == f0:
                d = abs(vi - v0)
            else:
                t = (fi - f0) / float(f1 - f0)
                d = abs(vi - (v0 + (v1 - v0) * t))
            d += abs((fi - f0) / max(1.0, f1 - f0)) * scale_f
            if d > best_d:
                best_d, best_i = d, i
        if best_d > tolerance and best_i > 0:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [p for p, k in zip(points, keep) if k]


def curve_stats(keys, fps, start=None, end=None):
    if not keys:
        return {}
    keys = sorted(keys)
    start = start if start is not None else keys[0][0]
    end = end if end is not None else keys[-1][0]
    values = [v for f, v in keys if start <= f <= end]
    if not values:
        values = [v for _f, v in keys]
    speeds = []
    for i in range(1, len(keys)):
        df = keys[i][0] - keys[i - 1][0]
        if df > 0:
            speeds.append(abs(keys[i][1] - keys[i - 1][1]) / (df / float(fps)))
    return {
        "keys": len(keys),
        "start_frame": keys[0][0],
        "end_frame": keys[-1][0],
        "duration_seconds": round((keys[-1][0] - keys[0][0]) / float(fps), 4),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "range": round(max(values) - min(values), 6),
        "mean": round(sum(values) / len(values), 6),
        "max_speed_per_second": round(max(speeds), 6) if speeds else 0.0,
    }


# ---------------------------------------------------------------------------
# Loop / retime / reduce
# ---------------------------------------------------------------------------


def build_loop(keys, fps, loop_len_frames, loops=2):
    """Extend a single-cycle curve into a periodic loop with seam continuity."""
    if not keys:
        raise core.invalid("Loop requires at least one key.")
    keys = sorted(keys)
    first_v = keys[0][1]
    last_v = keys[-1][1]
    drift = round(last_v - first_v, 6)
    out = []
    for n in range(max(1, loops)):
        offset = n * loop_len_frames
        for f, v in keys:
            if n > 0 and f == keys[0][0]:
                continue
            out.append((f + offset, round(v, 6)))
    return out, {"seam_drift": drift, "loop_frames": loop_len_frames, "loops": loops}


def retime(keys, factor, fps, start_frame=1):
    if factor <= 0:
        raise core.invalid("Retime factor must be positive.", "Use 2.0 to slow down, 0.5 to speed up.")
    return [(round((f - start_frame) / factor + start_frame, 6), v) for f, v in keys]


def reduce_keys(keys, tolerance=0.01):
    before = len(keys)
    after = rdp_simplify(sorted(keys), tolerance)
    return after, {
        "keys_before": before,
        "keys_after": len(after),
        "reduction_percent": round(100.0 * (before - len(after)) / before, 2) if before else 0.0,
        "tolerance": tolerance,
    }


# ---------------------------------------------------------------------------
# BVH parsing
# ---------------------------------------------------------------------------


class Bvh:
    def __init__(self):
        self.joints = []          # ordered list of joint names
        self.parents = {}         # name -> parent name or None
        self.offsets = {}         # name -> [x,y,z]
        self.channels = {}        # name -> list of channel names
        self.motion = []          # list of frames -> dict name -> {'offset': [...], 'rot': [...]}
        self.fps = FPS_DEFAULT
        self.frame_time = 1.0 / FPS_DEFAULT
        self.frames = 0
        self.order = []           # flat channel order used by the MOTION block

    @property
    def duration_seconds(self):
        return round(self.frames * self.frame_time, 4)


def parse_bvh(path, max_frames=200000):
    text = core.read_text(path, max_mb=64)
    lines = [ln.strip() for ln in text.splitlines()]
    if not any(ln.upper() == "MOTION" for ln in lines):
        raise core.invalid(
            "File is not a BVH (no MOTION block).",
            "This command reads BVH only; convert FBX/C3D with the DCC first.",
        )
    bvh = Bvh()
    stack = []
    i = 0
    while i < len(lines):
        line = lines[i]
        token = line.split()
        if not token:
            i += 1
            continue
        head = token[0].upper()
        if head == "HIERARCHY":
            i += 1
            continue
        if head == "MOTION":
            break
        if head in ("ROOT", "JOINT"):
            name = token[1]
            parent = stack[-1] if stack else None
            bvh.joints.append(name)
            bvh.parents[name] = parent
            bvh.offsets[name] = [0.0, 0.0, 0.0]
            bvh.channels[name] = []
            bvh.order.extend([])
            stack.append(name)
        elif head == "{":
            pass
        elif head == "}":
            if stack:
                stack.pop()
        elif head == "END":
            if stack:
                stack.pop()
        elif head == "OFFSET":
            if not stack:
                raise core.invalid("BVH OFFSET appears outside a joint.")
            bvh.offsets[stack[-1]] = [float(token[1]), float(token[2]), float(token[3])]
        elif head == "CHANNELS":
            n = int(token[1])
            names = token[2 : 2 + n]
            cur = stack[-1]
            bvh.channels[cur] = names
            bvh.order.extend("|".join([cur, c]) for c in names)
        i += 1
    # MOTION
    if i >= len(lines):
        raise core.invalid("BVH has no MOTION section.")
    i += 1
    while i < len(lines) and not lines[i]:
        i += 1
    if i >= len(lines) or not lines[i].lower().startswith("frames:"):
        raise core.invalid("BVH MOTION block must start with 'Frames:'.")
    try:
        bvh.frames = int(lines[i].split(":")[1].strip())
    except (IndexError, ValueError):
        raise core.invalid("BVH frame count is malformed.")
    i += 1
    if i >= len(lines) or not lines[i].lower().startswith("frame time:"):
        raise core.invalid("BVH MOTION block must include 'Frame Time:'.")
    bvh.frame_time = float(lines[i].split(":")[1].strip())
    if bvh.frame_time <= 0:
        raise core.invalid("BVH frame time must be positive.")
    bvh.fps = round(1.0 / bvh.frame_time, 3)
    i += 1
    if bvh.frames > max_frames:
        raise core.limit_exceeded(
            "BVH declares {0} frames, above the {1} frame ceiling.".format(bvh.frames, max_frames),
            "Trim the capture or raise --max-frames.",
        )
    needed = len(bvh.order)
    frame_data = []
    for _ in range(bvh.frames):
        while i < len(lines) and not lines[i]:
            i += 1
        if i >= len(lines):
            raise core.invalid(
                "BVH is truncated: expected {0} frames, found {1}.".format(bvh.frames, len(frame_data)),
                "Re-export the capture; partial files cannot be repaired reliably.",
            )
        vals = lines[i].split()
        if len(vals) < needed:
            raise core.invalid(
                "BVH frame {0} has {1} values, expected {2}.".format(len(frame_data) + 1, len(vals), needed),
            )
        frame_data.append([float(v) for v in vals[:needed]])
        i += 1
    pos = 0
    for fr in frame_data:
        frame = {}
        p = 0
        for name in bvh.joints:
            chans = bvh.channels[name]
            entry = {"offset": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0]}
            for c in chans:
                val = fr[p]
                p += 1
                if c.startswith("Xposition"):
                    entry["offset"][0] = val
                elif c.startswith("Yposition"):
                    entry["offset"][1] = val
                elif c.startswith("Zposition"):
                    entry["offset"][2] = val
                elif c.startswith("Xrotation"):
                    entry["rot"][0] = val
                elif c.startswith("Yrotation"):
                    entry["rot"][1] = val
                elif c.startswith("Zrotation"):
                    entry["rot"][2] = val
            frame[name] = entry
        bvh.motion.append(frame)
        pos += p
    return bvh


def rot_matrix_xyz(rx, ry, rz, order="ZXY"):
    """Local rotation matrix from degrees, applying channels in ``order``."""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    mx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    my = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    mz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    mats = {"X": mx, "Y": my, "Z": mz}
    out = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for axis in order:
        out = matmul3(out, mats[axis])
    return out


def matmul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def matvec3(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def bvh_world_positions(bvh, frame_index, max_joints=200):
    """Forward kinematics: world joint positions for one frame.

    Channel order is read from the file, so ZXY/XZY captures both work.
    """
    frame = bvh.motion[frame_index]
    world = {}
    rot = {}
    for name in bvh.joints[:max_joints]:
        parent = bvh.parents.get(name)
        entry = frame[name]
        rx, ry, rz = entry["rot"]
        order = "".join(c[0] for c in bvh.channels.get(name, []) if c.endswith("rotation"))
        local_r = rot_matrix_xyz(rx, ry, rz, order or "ZXY")
        local_t = [bvh.offsets[name][k] + entry["offset"][k] for k in range(3)]
        if parent is None or parent not in world:
            world[name] = local_t
            rot[name] = local_r
        else:
            moved = matvec3(rot[parent], local_t)
            world[name] = [world[parent][k] + moved[k] for k in range(3)]
            rot[name] = matmul3(rot[parent], local_r)
    return world


def bvh_summary(bvh):
    motion_roots = [j for j in bvh.joints if bvh.parents[j] is None]
    root_translation_range = None
    if bvh.motion and motion_roots:
        root = motion_roots[0]
        xs = [f[root]["offset"][0] for f in bvh.motion]
        ys = [f[root]["offset"][1] for f in bvh.motion]
        zs = [f[root]["offset"][2] for f in bvh.motion]
        root_translation_range = [
            round(max(xs) - min(xs), 4),
            round(max(ys) - min(ys), 4),
            round(max(zs) - min(zs), 4),
        ]
    return {
        "joints": len(bvh.joints),
        "joint_names": bvh.joints[:64],
        "roots": motion_roots,
        "frames": bvh.frames,
        "fps": bvh.fps,
        "duration_seconds": bvh.duration_seconds,
        "channels": len(bvh.order),
        "root_translation_range": root_translation_range,
    }


BVH_PARENTS = [
    ("Hips", None), ("Spine", "Hips"), ("Chest", "Spine"), ("Neck", "Chest"), ("Head", "Neck"),
    ("LeftArm", "Chest"), ("LeftForeArm", "LeftArm"), ("LeftHand", "LeftForeArm"),
    ("RightArm", "Chest"), ("RightForeArm", "RightArm"), ("RightHand", "RightForeArm"),
    ("LeftUpLeg", "Hips"), ("LeftFoot", "LeftUpLeg"), ("RightUpLeg", "Hips"), ("RightFoot", "RightUpLeg"),
]
BVH_OFFSETS = {
    "Hips": [0.0, 95.0, 0.0], "Spine": [0.0, 12.0, 0.0], "Chest": [0.0, 18.0, 0.0],
    "Neck": [0.0, 20.0, 0.0], "Head": [0.0, 9.0, 0.0],
    "LeftArm": [16.0, 4.0, 0.0], "LeftForeArm": [26.0, 0.0, 0.0], "LeftHand": [24.0, 0.0, 0.0],
    "RightArm": [-16.0, 4.0, 0.0], "RightForeArm": [-26.0, 0.0, 0.0], "RightHand": [-24.0, 0.0, 0.0],
    "LeftUpLeg": [9.0, -4.0, 0.0], "LeftFoot": [0.0, -45.0, 0.0],
    "RightUpLeg": [-9.0, -4.0, 0.0], "RightFoot": [0.0, -45.0, 0.0],
}


def synthetic_bvh(frames=48, fps=24, stride_cm=None, jitter=0.03, seed=7):
    """Deterministic synthetic capture used for fixtures and self-tests.

    Produces a walk-like cycle with contact frames on the feet so contact
    detection, retargeting and root-motion commands have real input to work on.
    """
    import random

    rng = random.Random(seed)
    joints = [name for name, _p in BVH_PARENTS]
    if stride_cm is None:
        stride_cm = 100.0
    lines = ["HIERARCHY"]

    def emit(name, depth, is_root=False):
        pad = "\t" * depth
        lines.append("{0}{1} {2}".format(pad, "ROOT" if is_root else "JOINT", name))
        lines.append(pad + "{")
        lines.append("{0}\tOFFSET {1:.4f} {2:.4f} {3:.4f}".format(pad, *BVH_OFFSETS[name]))
        if is_root:
            lines.append(pad + "\tCHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation")
        else:
            lines.append(pad + "\tCHANNELS 3 Zrotation Xrotation Yrotation")
        for child, parent in BVH_PARENTS:
            if parent == name:
                emit(child, depth + 1)
        lines.append(pad + "}")

    emit("Hips", 0, True)
    lines.append("MOTION")
    lines.append("Frames: {0}".format(frames))
    lines.append("Frame Time: {0:.6f}".format(1.0 / fps))
    for i in range(frames):
        phase = 2.0 * math.pi * i / max(1, frames)
        row = [stride_cm * i / float(max(1, frames - 1)), 95.0 + 1.5 * math.sin(phase * 2), 0.0, 0.0, 0.0, 0.0]
        for name in joints[1:]:
            if name in ("LeftUpLeg", "RightUpLeg"):
                s = 1.0 if name.startswith("Left") else -1.0
                row.extend([0.0, -28.0 * math.sin(phase + (0.0 if s > 0 else math.pi)), 0.0])
            elif name in ("LeftFoot", "RightFoot"):
                s = 1.0 if name.startswith("Left") else -1.0
                row.extend([0.0, 6.0 * math.sin(phase + (0.0 if s > 0 else math.pi)), 0.0])
            elif name.startswith(("Left", "Right")):
                s = 1.0 if name.startswith("Left") else -1.0
                row.extend([0.0, 0.0, -14.0 * math.sin(phase + (0.0 if s > 0 else math.pi))])
            else:
                row.extend([0.0, 1.2 * math.sin(phase), 0.0])
        lines.append(" ".join("{0:.4f}".format(v + rng.uniform(-jitter, jitter)) for v in row))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Contacts, root motion, retarget
# ---------------------------------------------------------------------------


def detect_foot_contacts(bvh, foot_names=("LeftFoot", "RightFoot"), threshold=0.02, world=True):
    """Detect plant (contact) frames per foot from joint height minima."""
    if not bvh.motion:
        raise core.invalid("BVH contains no motion frames.")
    out = {}
    for foot in foot_names:
        if foot not in bvh.joints:
            continue
        heights = []
        for i in range(bvh.frames):
            if world:
                pos = bvh_world_positions(bvh, i).get(foot)
            else:
                pos = bvh.motion[i][foot]["offset"]
            if pos is None:
                heights = []
                break
            heights.append(pos[1])
        if not heights:
            continue
        lo = min(heights)
        span = max(1e-6, max(heights) - lo)
        plant = [i + 1 for i, h in enumerate(heights) if (h - lo) / span <= threshold]
        runs = []
        for f in plant:
            if runs and f == runs[-1][1] + 1:
                runs[-1][1] = f
            else:
                runs.append([f, f])
        sliding = []
        for a, b in runs:
            drift = abs(heights[b - 1] - heights[a - 1]) / max(1.0, span)
            if drift > threshold:
                sliding.append({"start": a, "end": b, "drift": round(drift, 4)})
        out[foot] = {
            "plant_frames": len(plant),
            "plant_ratio": round(len(plant) / float(bvh.frames), 4),
            "contact_runs": runs[:40],
            "sliding_runs": sliding[:20],
            "min_height": round(lo, 4),
            "max_height": round(max(heights), 4),
        }
    return out


def extract_root_motion(bvh):
    if not bvh.motion:
        raise core.invalid("BVH contains no motion frames.")
    roots = [j for j in bvh.joints if bvh.parents[j] is None]
    if not roots:
        raise core.invalid("BVH has no root joint.")
    root = roots[0]
    track = []
    for i, frame in enumerate(bvh.motion):
        o = frame[root]["offset"]
        track.append({"frame": i + 1, "x": round(o[0], 4), "y": round(o[1], 4), "z": round(o[2], 4)})
    total = [
        round(track[-1]["x"] - track[0]["x"], 4),
        round(track[-1]["y"] - track[0]["y"], 4),
        round(track[-1]["z"] - track[0]["z"], 4),
    ]
    planar = math.hypot(total[0], total[2])
    speed = planar / max(1e-6, bvh.duration_seconds)
    return {
        "root": root,
        "frames": len(track),
        "translation_total": total,
        "planar_distance_m": round(planar, 4),
        "average_speed_m_per_s": round(speed, 4),
        "sample": track[:8] + track[-3:],
        "track": track,
    }


def retarget(target_chain, source_chain, style="hips-normalized"):
    """Scale a source bone chain onto a target with different limb lengths.

    ``*_chain`` is a list of (name, length) ordered root->tip. Rotations are
    preserved; translations are rescaled by the ratio of chain lengths, which is
    the standard hip-normalised retarget used for stylised characters.
    """
    if not target_chain or not source_chain:
        raise core.invalid("Both target and source chains are required.")
    t_len = sum(l for _n, l in target_chain)
    s_len = sum(l for _n, l in source_chain)
    if s_len <= 0:
        raise core.invalid("Source chain has zero length.")
    scale = t_len / s_len
    return {
        "target_chain": [n for n, _l in target_chain],
        "source_chain": [n for n, _l in source_chain],
        "scale": round(scale, 6),
        "style": style,
        "mapping": [{"source": s, "target": t, "length_ratio": round(tl / sl, 6) if sl else None}
                    for (s, sl), (t, tl) in zip(source_chain, target_chain)],
        "notes": "Rotations copied unchanged; translations multiplied by scale.",
    }


# ---------------------------------------------------------------------------
# Lip-sync
# ---------------------------------------------------------------------------


VISEME_SETS = {
    "en-15": [
        {"viseme": "sil", "label": "silence", "jaw": 0.0, "wide": 0.0, "round": 0.0, "chars": ""},
        {"viseme": "AA", "label": "father", "jaw": 0.85, "wide": 0.6, "round": 0.1, "chars": "a"},
        {"viseme": "EE", "label": "see", "jaw": 0.35, "wide": 0.9, "round": 0.0, "chars": "ei"},
        {"viseme": "IH", "label": "sit", "jaw": 0.3, "wide": 0.7, "round": 0.0, "chars": "iy"},
        {"viseme": "OH", "label": "go", "jaw": 0.7, "wide": 0.0, "round": 0.9, "chars": "o"},
        {"viseme": "OU", "label": "you", "jaw": 0.45, "wide": 0.0, "round": 0.95, "chars": "u"},
        {"viseme": "UH", "label": "cup", "jaw": 0.5, "wide": 0.3, "round": 0.35, "chars": ""},
        {"viseme": "MBP", "label": "closed lips", "jaw": 0.02, "wide": 0.15, "round": 0.2, "chars": "mbp"},
        {"viseme": "FV", "label": "labiodental", "jaw": 0.12, "wide": 0.35, "round": 0.0, "chars": "fv"},
        {"viseme": "TH", "label": "think", "jaw": 0.22, "wide": 0.4, "round": 0.0, "chars": "th"},
        {"viseme": "L", "label": "lip", "jaw": 0.35, "wide": 0.3, "round": 0.0, "chars": "l"},
        {"viseme": "SS", "label": "s/z", "jaw": 0.18, "wide": 0.8, "round": 0.0, "chars": "sz"},
        {"viseme": "SH", "label": "sh/ch", "jaw": 0.25, "wide": 0.0, "round": 0.75, "chars": ""},
        {"viseme": "WG", "label": "w/q", "jaw": 0.2, "wide": 0.0, "round": 0.9, "chars": "wq"},
        {"viseme": "RR", "label": "r", "jaw": 0.3, "wide": 0.0, "round": 0.55, "chars": "r"},
    ]
}
DIGRAPHS = {"sh": "SH", "ch": "SH", "th": "TH", "ph": "FV", "wh": "WG", "ck": "SS"}
CHAR_MAP = {
    "a": "AA", "e": "EE", "i": "IH", "y": "IH", "o": "OH", "u": "OU",
    "m": "MBP", "b": "MBP", "p": "MBP", "f": "FV", "v": "FV",
    "l": "L", "s": "SS", "z": "SS", "j": "SH", "r": "RR", "w": "WG", "q": "WG",
    "t": "TH", "d": "TH", "k": "SS", "g": "SS", "h": "AA", "n": "TH", "c": "SS", "x": "SS",
}


def read_wav(path, max_seconds=120):
    try:
        with wave.open(path, "rb") as wf:
            nch = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.getnframes()
            if frames / float(rate) > max_seconds:
                raise core.limit_exceeded(
                    "WAV is {0:.1f}s, above the {1}s analysis ceiling.".format(frames / float(rate), max_seconds),
                    "Trim the dialogue or raise --max-seconds.",
                )
            raw = wf.readframes(frames)
    except wave.Error as exc:
        raise core.invalid(
            "Cannot read WAV: {0}".format(exc),
            "Provide uncompressed PCM WAV (16-bit mono preferred). Convert with: ffmpeg -i in.mp3 -ac 1 -ar 16000 out.wav",
        )
    if width not in (1, 2, 3, 4):
        raise core.invalid("Unsupported WAV sample width: {0} bytes.".format(width))
    if width == 1:
        samples = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        n = len(raw) // 2
        samples = [v / 32768.0 for v in struct.unpack("<" + "h" * n, raw[: n * 2])]
    elif width == 3:
        samples = []
        for i in range(0, len(raw) - 2, 3):
            val = int.from_bytes(raw[i : i + 3], "little", signed=False)
            if val & 0x800000:
                val -= 1 << 24
            samples.append(val / 8388608.0)
    else:
        n = len(raw) // 4
        samples = [v / 2147483648.0 for v in struct.unpack("<" + "i" * n, raw[: n * 4])]
    if nch > 1:
        mono = []
        for i in range(0, len(samples) - nch + 1, nch):
            chunk = samples[i : i + nch]
            mono.append(sum(chunk) / nch)
        samples = mono
    return samples, rate


def envelope(samples, rate, hop_ms=20, window_ms=40):
    hop = max(1, int(rate * hop_ms / 1000.0))
    win = max(hop, int(rate * window_ms / 1000.0))
    out = []
    for start in range(0, max(1, len(samples) - win + 1), hop):
        chunk = samples[start : start + win]
        rms = math.sqrt(sum(s * s for s in chunk) / max(1, len(chunk)))
        out.append({"time": round(start / float(rate), 4), "rms": round(rms, 6)})
    return out


def text_to_visemes(text, fps=24, start_frame=1, duration_seconds=None, chars_per_second=13.0):
    """Phoneme-free grapheme mapping: deterministic, diagnostic, offline."""
    text = (text or "").strip()
    if not text:
        raise core.invalid("Provide --text for text-driven lip-sync.", "Or pass --audio for audio-driven sync.")
    table = {v["viseme"]: v for v in VISEME_SETS["en-15"]}
    tokens = []
    lower = text.lower()
    i = 0
    while i < len(lower):
        ch = lower[i]
        pair = lower[i : i + 2]
        if pair in DIGRAPHS:
            tokens.append((DIGRAPHS[pair], ch))
            i += 2
            continue
        if ch.isalpha():
            tokens.append((CHAR_MAP.get(ch, "UH"), ch))
            i += 1
            continue
        if ch in ".,;:!?":
            tokens.append(("sil", ch))
        elif ch == " ":
            tokens.append(("sil", " "))
        i += 1
    if not tokens:
        raise core.invalid("No pronounceable tokens found in the text.")
    per_token = 1.0 / max(1.0, chars_per_second)
    total = duration_seconds if duration_seconds else len(tokens) * per_token
    step = total / len(tokens)
    cues = []
    for idx, (vis, ch) in enumerate(tokens):
        t0 = idx * step
        cues.append(
            {
                "start": round(t0, 4),
                "end": round(t0 + step, 4),
                "start_frame": int(round(start_frame + t0 * fps)),
                "viseme": vis,
                "char": ch,
                "jaw": table[vis]["jaw"],
                "wide": table[vis]["wide"],
                "round": table[vis]["round"],
            }
        )
    return cues


def visemes_from_audio(samples, rate, fps=24, start_frame=1, sensitivity=1.0, min_gap=2):
    """Drive a viseme sequence from an amplitude envelope (no ML model needed)."""
    env = envelope(samples, rate)
    if not env:
        raise core.invalid("Audio contains no samples to analyse.")
    peak = max((e["rms"] for e in env), default=0.0) or 1e-6
    frames = []
    for e in env:
        level = min(1.0, (e["rms"] / peak) * sensitivity)
        f = int(round(start_frame + e["time"] * fps))
        if frames and frames[-1]["frame"] == f:
            continue
        if level < 0.08:
            viseme, jaw = "sil", 0.0
        elif level < 0.25:
            viseme, jaw = "MBP", 0.05 + 0.1 * level
        elif level < 0.45:
            viseme, jaw = "FV", 0.15 + 0.2 * level
        elif level < 0.65:
            viseme, jaw = "EE", 0.3 + 0.2 * level
        elif level < 0.85:
            viseme, jaw = "OH", 0.5 + 0.2 * level
        else:
            viseme, jaw = "AA", 0.7 + 0.3 * level
        frames.append({"frame": f, "time": e["time"], "level": round(level, 4), "viseme": viseme, "jaw": round(jaw, 4)})
    # collapse runs shorter than min_gap frames
    merged = []
    for item in frames:
        if merged and item["viseme"] == merged[-1]["viseme"]:
            merged[-1]["end_frame"] = item["frame"]
        else:
            merged.append(dict(item, end_frame=item["frame"]))
    out = [m for m in merged if (m["end_frame"] - m["frame"]) >= (min_gap - 1) or m["viseme"] == "sil"]
    if not out:
        raise core.invalid("Audio produced no usable viseme cues.", "Lower --sensitivity or check the recording level.")
    return out, {"peak_rms": round(peak, 6), "envelope_samples": len(env)}


def cues_to_vtt(cues, fps=24):
    lines = ["WEBVTT", ""]

    def stamp(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return "{0:02d}:{1:02d}:{2:06.3f}".format(h, m, s)

    for c in cues:
        start = c.get("start")
        end = c.get("end")
        if start is None:
            start = (c.get("frame", 1) - 1) / float(fps)
        if end is None:
            end = start + 0.04
        label = c.get("viseme") or c.get("text") or ""
        lines.append("{0} --> {1}".format(stamp(start), stamp(end)))
        lines.append("{0} jaw={1} round={2} wide={3}".format(
            label, c.get("jaw", ""), c.get("round", ""), c.get("wide", "")))
        lines.append("")
    return "\n".join(lines)


def cues_to_srt(cues, fps=24):
    def stamp(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return "{0:02d}:{1:02d}:{2:02d},{3:03d}".format(h, m, s, ms)

    lines = []
    for i, c in enumerate(cues, 1):
        start = c.get("start", (c.get("frame", 1) - 1) / float(fps))
        end = c.get("end", start + 0.04)
        lines.append(str(i))
        lines.append("{0} --> {1}".format(stamp(start), stamp(end)))
        lines.append(c.get("viseme") or c.get("text") or "")
        lines.append("")
    return "\n".join(lines)
