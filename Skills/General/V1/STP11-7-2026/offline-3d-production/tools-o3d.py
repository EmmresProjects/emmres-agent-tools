#!/usr/bin/env python3
"""offline-3d-production -- primary entry point.

A reusable, air-gap-safe Skill Tool component for offline 3D modeling, animation,
rendering, validation and delivery. This file is the only launcher you need:

    python tools-o3d.py capabilities
    python tools-o3d.py help
    python tools-o3d.py mesh inspect --mesh model.obj

It loads the sibling modules by exact filename (no directory needs to be a
package), validates every input before work starts, writes structured JSON or
text reports, and returns documented exit codes.

Exit codes
----------
0  success
1  runtime error
2  invalid input / validation failure
3  approval required for a guarded action
4  limit exceeded, timeout or cancellation
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import traceback

TOOL_NAME = "offline-3d-production"
VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

MODULE_FILES = [
    ("o3d_core", "tools-o3d-core.py"),
    ("o3d_geom", "tools-o3d-geom.py"),
    ("o3d_tex", "tools-o3d-texture.py"),
    ("o3d_anim", "tools-o3d-anim.py"),
    ("o3d_render", "tools-o3d-render.py"),
    ("o3d_pipeline", "tools-o3d-pipeline.py"),
]


def load_modules():
    """Load every sibling module by file path and register it in sys.modules."""
    loaded = {}
    for mod_name, filename in MODULE_FILES:
        path = os.path.join(HERE, filename)
        if not os.path.exists(path):
            sys.stderr.write(
                "FATAL: required module '{0}' is missing from {1}.\n"
                "The Skill Tool folder must be copied whole; do not rename or move files.\n".format(
                    filename, os.path.basename(HERE))
            )
            raise SystemExit(1)
        spec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.stderr.write("FATAL: failed to load {0}\n{1}\n".format(filename, traceback.format_exc()))
            raise SystemExit(1)
        loaded[mod_name] = module
    return loaded


MODS = load_modules()
core = sys.modules["o3d_core"]
geom = sys.modules["o3d_geom"]
tex = sys.modules["o3d_tex"]
anim = sys.modules["o3d_anim"]
render = sys.modules["o3d_render"]
pipeline = sys.modules["o3d_pipeline"]


# ---------------------------------------------------------------------------
# Capability registry (also used by the 'capabilities' command)
# ---------------------------------------------------------------------------

CAPABILITIES = [
    # (command path, summary, catalog categories covered)
    ("capabilities", "List every command, its purpose and the catalog categories it serves.", "meta"),
    ("help", "Show full usage, options and examples.", "meta"),
    ("doctor", "Report environment, optional dependencies and offline status.", "1, 16, 23"),
    ("fixture", "Write deterministic fixture meshes, a BVH capture and textures for testing.", "1, 14"),
    ("scaffold", "Create the standard production folder tree and project configuration.", "2"),
    ("standards naming", "Emit (or validate) a parseable asset/scene naming standard.", "2"),
    ("standards units", "Emit a unit, scale and gravity standard for the project.", "2"),
    ("standards axis", "Emit a coordinate-system, handedness and rotation-order standard.", "2"),
    ("standards config", "Emit a machine-readable project configuration (fps, colour, paths).", "2"),
    ("standards handoff", "Emit a department handoff template with every required field.", "2, 32"),
    ("validate names", "Detect duplicate, reserved, illegal or non-portable names and propose renames.", "2"),
    ("mesh inspect", "Measure topology: bounds, area, volume, manifoldness, valence, quality.", "3, 5, 29"),
    ("mesh validate", "Run the geometry gate against a project profile and emit findings.", "3, 5, 29"),
    ("mesh repair", "Weld, unify winding, delete degenerate/loose geometry and fill small holes.", "3, 4, 5"),
    ("mesh convert", "Convert OBJ/PLY/STL/GLB/JSON between formats with axis and unit handling.", "26, 2"),
    ("mesh decimate", "Reduce to a triangle budget with deterministic vertex clustering.", "5, 27"),
    ("mesh remesh", "Voxel remesh into a watertight production mesh.", "4, 5"),
    ("mesh subdivide", "Catmull-Clark subdivide a control cage with a triangle ceiling.", "3, 4"),
    ("mesh smooth", "Boundary-aware Laplacian smoothing for cleanup and cloth/fabric prep.", "3, 4, 18"),
    ("mesh symmetry", "Mirror-match vertices and repair symmetry along an axis.", "3, 4"),
    ("mesh sample", "Area-weighted surface point sampling for scattering and bake cages.", "21, 8"),
    ("mesh uv-analysis", "Measure UV stretch, flips, texel density and overlap risk.", "6, 7, 29"),
    ("mesh render", "Render a deterministic review still (PNG/PPM/EXR) with a look preset.", "10, 23, 25"),
    ("texture generate", "Generate procedural maps: checker, UV layout, height, normal, AO, masks.", "6, 7, 8"),
    ("texture pbr-set", "Synthesise a coherent PBR set for stone/metal/fabric/skin/wood families.", "7, 9"),
    ("texture normal", "Derive a tangent-space normal map from any height PNG.", "8, 22"),
    ("texture pack", "Pack grayscale maps into ORM/RMA/engine channel conventions.", "7, 27"),
    ("texture audit", "Audit resolution, colour space, banding, normals, dilation and seams.", "7, 25, 29"),
    ("texture variant", "Derive worn/wet/dirty/colorway/damaged/clean variants from one source.", "7"),
    ("texture mips", "Generate and inspect a mip chain for bleeding and energy loss.", "7, 23, 27"),
    ("anim sample", "Evaluate keyframes with easing and resample to a new frame range or rate.", "15"),
    ("anim stats", "Summarise curve extent, speed and range for animation review.", "15"),
    ("anim reduce", "Reduce redundant keys with a bounded error budget (RDP).", "15, 27"),
    ("anim loop", "Build a seamless loop with seam-drift measurement.", "15"),
    ("anim retime", "Retime keys to slow or speed up an action.", "15"),
    ("anim gate", "Validate curves for pops, late starts, early ends and loop seams.", "15, 29"),
    ("mocap inspect", "Parse BVH captures and report skeleton, frames, fps and channel layout.", "14"),
    ("mocap contacts", "Detect foot plant/contact runs and sliding from forward kinematics.", "14, 17"),
    ("mocap rootmotion", "Extract root translation, planar travel and average speed.", "14"),
    ("mocap retarget", "Hip-normalised retarget plan between chains of different lengths.", "14"),
    ("review turntable", "Render a deterministic turntable frame sequence (PNG/EXR) for review.", "23, 25"),
    ("mocap synth", "Write a deterministic synthetic BVH capture for tests and fixtures.", "14"),
    ("lipsync text", "Map dialogue to viseme cues with frame timing; export JSON/VTT/SRT.", "13"),
    ("lipsync audio", "Drive visemes from a WAV amplitude envelope, fully offline.", "13"),
    ("plan vram", "Estimate peak VRAM from geometry, textures, samples and resolution.", "1, 23"),
    ("plan ram", "Estimate resident RAM for sculpting, caches, baking and comp.", "1, 4, 20"),
    ("plan disk", "Budget caches, bakes, proxies and autosaves with safety margin.", "1, 24"),
    ("plan render", "Estimate render wall-clock time and recommend chunking.", "24"),
    ("plan hardware", "Audit reported hardware against project requirements.", "1, 23"),
    ("install manifest", "Build and checksum an offline installer/dependency bundle manifest.", "1"),
    ("install verify", "Verify a bundle or delivery against recorded SHA-256 digests.", "1, 31"),
    ("license audit", "Check asset licenses against the local policy for use, redistribution and training.", "30"),
    ("license consent", "Audit performer consent records for scope, territory, term and training.", "14, 30"),
    ("license provenance", "Record model, version, seed, inputs and hashes for a generated output.", "22, 30"),
    ("hash", "Compute SHA-256 digests and sizes for any set of files.", "1, 31"),
    ("validate geometry", "Full geometry gate report (alias of mesh validate with JSON output).", "3, 29"),
    ("validate animation", "Full animation gate report (alias of anim gate with JSON output).", "15, 29"),
    ("validate package", "Check a delivery folder for missing, empty, oversized or unexpected files.", "29, 31, 32"),
    ("gate", "Show every gate, its blocking codes and how to clear them.", "29"),
    ("report", "Render a Markdown or HTML report from any JSON result.", "32"),
    ("sheet", "Compose a labelled contact sheet PNG from review images.", "25, 32"),
    ("review encode", "Encode a PNG frame sequence to H.264 MP4 with the local ffmpeg binary.", "23, 24, 25"),
]


def cmd_capabilities(args, ctx):
    rows = []
    for path, summary, cats in CAPABILITIES:
        rows.append({"command": path, "summary": summary, "catalog_categories": cats})
    groups = {}
    for row in rows:
        groups.setdefault(row["command"].split()[0], []).append(row)
    return {
        "tool": TOOL_NAME,
        "version": VERSION,
        "command_count": len(rows),
        "catalog_coverage": {
            "categories": 32,
            "skills": 446,
            "note": "Every catalog category is represented by at least one command; see knowledge-readme.md "
                    "for the category-to-command map.",
        },
        "groups": groups,
        "commands": rows,
    }


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class Parser(argparse.ArgumentParser):
    def error(self, message):  # exit code 2 with a structured hint
        sys.stderr.write("usage error: {0}\n".format(message))
        sys.stderr.write("Run 'python tools-o3d.py help' for usage.\n")
        raise SystemExit(core.EXIT_INVALID_INPUT)



GLOBAL_FLAGS_WITH_VALUE = {"--config", "--workspace", "--out", "--format", "--timeout", "--overrides"}
GLOBAL_FLAGS_BOOL = {"--approve", "--dry-run", "--allow-outside", "--verbose"}


def normalize_argv(argv):
    """Allow global options to appear before or after the subcommand.

    argparse only accepts parent options before the subcommand token, but users
    (and scripts) naturally write them at the end. This moves them to the front
    while preserving order, and leaves command-specific options untouched.
    """
    head, rest = [], []
    i = 0
    while i < len(argv):
        token = argv[i]
        name = token.split("=", 1)[0]
        if name in GLOBAL_FLAGS_BOOL:
            head.append(token)
            i += 1
            continue
        if name in GLOBAL_FLAGS_WITH_VALUE:
            if "=" in token:
                head.append(token)
                i += 1
                continue
            if i + 1 < len(argv):
                head.extend([token, argv[i + 1]])
                i += 2
                continue
            head.append(token)
            i += 1
            continue
        rest.append(token)
        i += 1
    if "--" in rest:
        idx = rest.index("--")
        return head + rest[:idx] + rest[idx:], True
    return head + rest, False


def build_parser():
    p = Parser(prog="tools-o3d.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to a configuration JSON (default: asset-default-config.json beside this file)")
    p.add_argument("--workspace", default=".", help="base directory; all relative paths resolve inside it")
    p.add_argument("--out", help="primary output file or directory")
    p.add_argument("--format", choices=["json", "text"], default="json", help="report format (default: json)")
    p.add_argument("--timeout", type=float, help="wall-clock budget in seconds for this command")
    p.add_argument("--approve", action="store_true", help="approve a guarded/destructive action")
    p.add_argument("--dry-run", action="store_true", help="validate and plan without writing outputs")
    p.add_argument("--allow-outside", action="store_true", help="permit absolute/outside-workspace paths")
    p.add_argument("--verbose", action="store_true", help="include tracebacks and extra detail on error")
    p.add_argument("--overrides", help="JSON object merged over the configuration for this run")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, help_text):
        return sub.add_parser(name, help=help_text, description=help_text)

    sub.add_parser("capabilities", help="list all commands and catalog coverage")
    sub.add_parser("help", help="show usage and examples")
    sub.add_parser("doctor", help="report environment and dependencies")
    sub.add_parser("gate", help="show the gate catalogue")

    f = add("fixture", "write deterministic fixtures")
    f.add_argument("--dir", default="fixtures", help="output directory (default: fixtures)")
    f.add_argument("--force", action="store_true", help="overwrite existing fixtures")

    s = add("scaffold", "create the production folder tree")
    s.add_argument("--project", required=True, help="project code, e.g. hero-film")
    s.add_argument("--dir", default=None, help="root directory (default: <workspace>/<project>)")
    s.add_argument("--fps", type=float, default=24)
    s.add_argument("--units", default="m")
    s.add_argument("--up-axis", default="Z", choices=["X", "Y", "Z"])

    r = add("report", "render a report from JSON")
    r.add_argument("--input", help="JSON result file to summarise")
    r.add_argument("--title", default="Production Report")
    r.add_argument("--html", action="store_true", help="emit HTML instead of Markdown")

    sh = add("sheet", "compose a contact sheet PNG")
    sh.add_argument("--image", action="append", default=[], help="image path; repeat for each item (label=path)")
    sh.add_argument("--columns", type=int, default=4)
    sh.add_argument("--cell", type=int, default=160)

    # --- standards -------------------------------------------------------
    st = add("standards", "emit production standards documents")
    st_sub = st.add_subparsers(dest="topic", metavar="TOPIC")
    st_n = st_sub.add_parser("naming", help="naming standard")
    st_n.add_argument("--asset-code")
    st_n.add_argument("--max-length", type=int, default=64)
    st_u = st_sub.add_parser("units", help="unit and scale standard")
    st_u.add_argument("--unit", default="m")
    st_u.add_argument("--character-height", type=float, default=1.8)
    st_u.add_argument("--gravity", type=float, default=9.81)
    st_u.add_argument("--for-print", action="store_true")
    st_a = st_sub.add_parser("axis", help="coordinate system standard")
    st_a.add_argument("--up", default="Z", choices=["X", "Y", "Z"])
    st_a.add_argument("--forward", default="Y", choices=["X", "-X", "Y", "-Y", "Z", "-Z"])
    st_a.add_argument("--handedness", default="right", choices=["right", "left"])
    st_a.add_argument("--rotation-order", default="XYZ", choices=["XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"])
    st_c = st_sub.add_parser("config", help="project configuration")
    st_c.add_argument("--project", required=True)
    st_c.add_argument("--fps", type=float, default=24)
    st_c.add_argument("--resolution", default="1920x1080")
    st_c.add_argument("--handles", type=int, default=8)
    st_c.add_argument("--color", default="ACEScg")
    st_h = st_sub.add_parser("handoff", help="department handoff template")
    st_h.add_argument("--department", default="modeling")
    st_h.add_argument("--project", required=True)

    # --- validate --------------------------------------------------------
    v = add("validate", "run validation gates")
    v_sub = v.add_subparsers(dest="gate", metavar="GATE")
    v_n = v_sub.add_parser("names", help="validate names against a standard")
    v_n.add_argument("--name", action="append", default=[], help="name to check; repeat")
    v_n.add_argument("--names-file", help="text file with one name per line")
    v_n.add_argument("--standard", help="naming standard JSON (default: generated standard)")
    v_g = v_sub.add_parser("geometry", help="geometry gate")
    v_g.add_argument("--mesh", required=True)
    v_g.add_argument("--profile", help="profile JSON (default: config mesh.profile)")
    v_a = v_sub.add_parser("animation", help="animation gate")
    v_a.add_argument("--curves", required=True, help="JSON file: {curve: [[frame,value],...]}")
    v_a.add_argument("--fps", type=float)
    v_a.add_argument("--loop", action="store_true")
    v_p = v_sub.add_parser("package", help="delivery package gate")
    v_p.add_argument("--dir", required=True)
    v_p.add_argument("--profile", help="package profile JSON")
    v_l = v_sub.add_parser("license", help="license audit")
    v_l.add_argument("--assets", required=True, help="asset metadata JSON")
    v_l.add_argument("--policy", help="policy JSON (default: asset-license-policy.json)")
    v_c = v_sub.add_parser("consent", help="performer consent audit")
    v_c.add_argument("--records", required=True)

    # --- mesh ------------------------------------------------------------
    m = add("mesh", "mesh operations")
    m_sub = m.add_subparsers(dest="op", metavar="OP")
    mi = m_sub.add_parser("inspect", help="measure topology")
    mi.add_argument("--mesh", required=True)
    mi.add_argument("--uv", action="store_true", help="include UV analysis")
    mi.add_argument("--symmetry-axis", default="X", choices=["X", "Y", "Z"])
    mv = m_sub.add_parser("validate", help="geometry gate")
    mv.add_argument("--mesh", required=True)
    mv.add_argument("--profile")
    mr = m_sub.add_parser("repair", help="repair mesh")
    mr.add_argument("--mesh", required=True)
    mr.add_argument("--weld-distance", type=float, default=1e-5)
    mr.add_argument("--fill", action="store_true", help="fill small boundary loops")
    mr.add_argument("--max-hole-edges", type=int, default=64)
    mc = m_sub.add_parser("convert", help="convert mesh format")
    mc.add_argument("--mesh", required=True)
    mc.add_argument("--to", required=True, help="target extension: obj, ply, stl, glb, json")
    mc.add_argument("--from-unit", default=None)
    mc.add_argument("--to-unit", default=None)
    mc.add_argument("--scale", type=float, default=None)
    mc.add_argument("--up-from", default=None, choices=["X", "Y", "Z"])
    mc.add_argument("--up-to", default=None, choices=["X", "Y", "Z"])
    md = m_sub.add_parser("decimate", help="reduce triangle count")
    md.add_argument("--mesh", required=True)
    md.add_argument("--target-tris", type=int, required=True)
    mrm = m_sub.add_parser("remesh", help="voxel remesh")
    mrm.add_argument("--mesh", required=True)
    mrm.add_argument("--voxel-size", type=float, required=True)
    mrm.add_argument("--max-tris", type=int, default=200000)
    mrm.add_argument("--smooth-iterations", type=int, default=2)
    ms = m_sub.add_parser("subdivide", help="Catmull-Clark subdivide")
    ms.add_argument("--mesh", required=True)
    ms.add_argument("--levels", type=int, default=1)
    msm = m_sub.add_parser("smooth", help="Laplacian smooth")
    msm.add_argument("--mesh", required=True)
    msm.add_argument("--iterations", type=int, default=2)
    msm.add_argument("--factor", type=float, default=0.5)
    msy = m_sub.add_parser("symmetry", help="symmetry report or repair")
    msy.add_argument("--mesh", required=True)
    msy.add_argument("--axis", default="X", choices=["X", "Y", "Z"])
    msy.add_argument("--tolerance", type=float, default=0.0015)
    msy.add_argument("--repair", action="store_true", help="mirror the +side onto the -side")
    msa = m_sub.add_parser("sample", help="surface point sampling")
    msa.add_argument("--mesh", required=True)
    msa.add_argument("--count", type=int, default=1000)
    msa.add_argument("--seed", type=int, default=0)
    mu = m_sub.add_parser("uv-analysis", help="UV distortion report")
    mu.add_argument("--mesh", required=True)
    mu.add_argument("--resolution", type=int, default=2048)
    mrp = m_sub.add_parser("render", help="render a review still")
    mrp.add_argument("--mesh", required=True)
    mrp.add_argument("--width", type=int, default=960)
    mrp.add_argument("--height", type=int, default=640)
    mrp.add_argument("--samples", type=int, default=2)
    mrp.add_argument("--pose", default="hero", choices=sorted(render.POSE_PRESETS))
    mrp.add_argument("--bg", default=None, help="background r,g,b (0-255)")
    mrp.add_argument("--exposure", type=float, default=1.0)
    mrp.add_argument("--fog", type=float, default=0.0)
    mrp.add_argument("--wireframe", action="store_true")
    mrp.add_argument("--shading", default="smooth", choices=["smooth", "flat"])
    mrp.add_argument("--still-format", default="png", choices=["png", "ppm", "exr"])
    mrp.add_argument("--label", default=None, help="burn-in label text")

    # --- texture ---------------------------------------------------------
    t = add("texture", "texture operations")
    t_sub = t.add_subparsers(dest="op", metavar="OP")
    tg = t_sub.add_parser("generate", help="procedural map")
    tg.add_argument("--kind", required=True,
                    choices=["checker", "uv-layout", "gradient", "noise", "height", "normal", "ao",
                             "roughness", "metallic", "basecolor", "mask"])
    tg.add_argument("--width", type=int, default=512)
    tg.add_argument("--height", type=int, default=512)
    tg.add_argument("--seed", type=int, default=0)
    tg.add_argument("--channels", type=int, default=3, choices=[1, 2, 3, 4])
    tg.add_argument("--tiles", type=int, default=1)
    tg.add_argument("--cells", type=int, default=8)
    tp = t_sub.add_parser("pbr-set", help="coherent PBR set")
    tp.add_argument("--family", default="stone", choices=["stone", "metal", "fabric", "skin", "wood"])
    tp.add_argument("--width", type=int, default=512)
    tp.add_argument("--height", type=int, default=512)
    tp.add_argument("--seed", type=int, default=0)
    tp.add_argument("--prefix", default="pbr")
    tn = t_sub.add_parser("normal", help="normal map from height")
    tn.add_argument("--height-map", required=True)
    tn.add_argument("--strength", type=float, default=2.0)
    tpk = t_sub.add_parser("pack", help="channel packing")
    tpk.add_argument("--source", action="append", required=True, help="name=path; repeat")
    tpk.add_argument("--layout", required=True, help="e.g. R=ao:r,G=roughness:r,B=metallic:r")
    tpk.add_argument("--channels", type=int, default=3, choices=[3, 4])
    ta = t_sub.add_parser("audit", help="texture audit")
    ta.add_argument("--image", required=True)
    ta.add_argument("--role", default=None,
                    choices=[None, "basecolor", "albedo", "normal", "roughness", "metallic", "opacity", "alpha", "height"])
    ta.add_argument("--expected-size", default=None, help="WxH")
    tv = t_sub.add_parser("variant", help="texture variant")
    tv.add_argument("--image", required=True)
    tv.add_argument("--mode", required=True, choices=["worn", "wet", "dirty", "colorway", "damaged", "clean"])
    tv.add_argument("--amount", type=float, default=0.5)
    tv.add_argument("--seed", type=int, default=0)
    tm = t_sub.add_parser("mips", help="mip chain inspection")
    tm.add_argument("--image", required=True)
    tm.add_argument("--levels", type=int, default=4)

    # --- anim ------------------------------------------------------------
    a = add("anim", "keyframe animation operations")
    a_sub = a.add_subparsers(dest="op", metavar="OP")
    asm = a_sub.add_parser("sample", help="evaluate and resample keys")
    asm.add_argument("--keys", required=True, help="JSON file: [[frame,value],...] or a curve name map")
    asm.add_argument("--curve", default=None, help="curve name when the file is a map")
    asm.add_argument("--start", type=int, default=None)
    asm.add_argument("--end", type=int, default=None)
    asm.add_argument("--step", type=int, default=1)
    asm.add_argument("--easing", default="smooth",
                     choices=["linear", "smooth", "ease-in", "ease-out", "ease-in-out", "anticipate",
                              "overshoot", "step"])
    ast = a_sub.add_parser("stats", help="curve statistics")
    ast.add_argument("--keys", required=True)
    ast.add_argument("--curve", default=None)
    ard = a_sub.add_parser("reduce", help="reduce keys")
    ard.add_argument("--keys", required=True)
    ard.add_argument("--curve", default=None)
    ard.add_argument("--tolerance", type=float, default=0.01)
    alp = a_sub.add_parser("loop", help="build a loop")
    alp.add_argument("--keys", required=True)
    alp.add_argument("--curve", default=None)
    alp.add_argument("--loop-frames", type=int, required=True)
    alp.add_argument("--loops", type=int, default=2)
    art = a_sub.add_parser("retime", help="retime keys")
    art.add_argument("--keys", required=True)
    art.add_argument("--curve", default=None)
    art.add_argument("--factor", type=float, required=True)
    ag = a_sub.add_parser("gate", help="animation gate")
    ag.add_argument("--curves", required=True)
    ag.add_argument("--fps", type=float)
    ag.add_argument("--loop", action="store_true")

    # --- mocap -----------------------------------------------------------
    mo = add("mocap", "motion capture operations")
    mo_sub = mo.add_subparsers(dest="op", metavar="OP")
    moi = mo_sub.add_parser("inspect", help="parse and summarise BVH")
    moi.add_argument("--bvh", required=True)
    moc = mo_sub.add_parser("contacts", help="foot contact detection")
    moc.add_argument("--bvh", required=True)
    moc.add_argument("--foot", action="append", default=[], help="foot joint name; repeat")
    moc.add_argument("--threshold", type=float, default=0.02)
    mor = mo_sub.add_parser("rootmotion", help="root motion extraction")
    mor.add_argument("--bvh", required=True)
    more = mo_sub.add_parser("retarget", help="retarget plan")
    more.add_argument("--target", required=True, help="name:length,name:length,...")
    more.add_argument("--source", required=True, help="name:length,name:length,...")
    more.add_argument("--style", default="hips-normalized")
    mos = mo_sub.add_parser("synth", help="write a synthetic BVH")
    mos.add_argument("--frames", type=int, default=48)
    mos.add_argument("--fps", type=int, default=24)
    mos.add_argument("--seed", type=int, default=7)

    # --- lipsync ---------------------------------------------------------
    ls = add("lipsync", "offline lip-sync")
    ls_sub = ls.add_subparsers(dest="mode", metavar="MODE")
    lst = ls_sub.add_parser("text", help="visemes from dialogue text")
    lst.add_argument("--text", required=True)
    lst.add_argument("--fps", type=float, default=24)
    lst.add_argument("--start-frame", type=int, default=1)
    lst.add_argument("--duration", type=float, default=None)
    lst.add_argument("--captions", default="vtt", choices=["vtt", "srt", "both", "none"])
    lsa = ls_sub.add_parser("audio", help="visemes from a WAV envelope")
    lsa.add_argument("--wav", required=True)
    lsa.add_argument("--fps", type=float, default=24)
    lsa.add_argument("--sensitivity", type=float, default=1.0)
    lsa.add_argument("--captions", default="vtt", choices=["vtt", "srt", "both", "none"])

    # --- review (turntable + mp4) ----------------------------------------
    rv = add("review", "review deliverables: turntable sequences and MP4 encodes")
    rv_sub = rv.add_subparsers(dest="op", metavar="OP")
    rt = rv_sub.add_parser("turntable", help="render a turntable frame sequence")
    rt.add_argument("--mesh", required=True)
    rt.add_argument("--frames", type=int, default=24)
    rt.add_argument("--width", type=int, default=480)
    rt.add_argument("--height", type=int, default=480)
    rt.add_argument("--samples", type=int, default=1)
    rt.add_argument("--elevation", type=float, default=18.0)
    rt.add_argument("--still-format", default="png", choices=["png", "ppm", "exr"])
    rt.add_argument("--label", default=None)
    rb = rv_sub.add_parser("encode", help="encode a PNG sequence to MP4 (local ffmpeg)")
    rb.add_argument("--frames-dir", required=True)
    rb.add_argument("--fps", type=float, default=24)
    rb.add_argument("--bitrate", default="4M")
    rb.add_argument("--ffmpeg-timeout", type=float, default=600)

    # --- plan ------------------------------------------------------------
    pl = add("plan", "capacity and schedule planning")
    pl_sub = pl.add_subparsers(dest="op", metavar="OP")
    pv = pl_sub.add_parser("vram", help="VRAM estimate")
    pv.add_argument("--triangles", type=int, required=True)
    pv.add_argument("--texture", action="append", default=[], help="WxHxC name, e.g. 4096x4096x3=base")
    pv.add_argument("--samples", type=int, default=256)
    pv.add_argument("--resolution", default="1920x1080")
    pv.add_argument("--device-gb", type=float, default=8.0)
    pv.add_argument("--concurrent-apps-gb", type=float, default=0.0)
    pr = pl_sub.add_parser("ram", help="RAM estimate")
    pr.add_argument("--triangles", type=int, default=0)
    pr.add_argument("--sculpt-millions", type=float, default=0.0)
    pr.add_argument("--cache-gb", type=float, action="append", default=[])
    pr.add_argument("--system-gb", type=float, default=32.0)
    pd = pl_sub.add_parser("disk", help="scratch disk budget")
    pd.add_argument("--frames", type=int, default=240)
    pd.add_argument("--frame-mb", type=float, default=24.0)
    pd.add_argument("--cache-versions", type=int, default=3)
    pd.add_argument("--available-gb", type=float, default=500.0)
    prd = pl_sub.add_parser("render", help="render schedule estimate")
    prd.add_argument("--frames", type=int, required=True)
    prd.add_argument("--seconds-per-frame", type=float, required=True)
    prd.add_argument("--machines", type=int, default=1)
    prd.add_argument("--target-hours", type=float, default=None)
    ph = pl_sub.add_parser("hardware", help="hardware audit")
    ph.add_argument("--requirements", required=True, help="JSON requirements")
    ph.add_argument("--reported", required=True, help="JSON reported values")

    # --- install ---------------------------------------------------------
    ins = add("install", "offline install and integrity")
    ins_sub = ins.add_subparsers(dest="op", metavar="OP")
    im = ins_sub.add_parser("manifest", help="build an installer manifest")
    im.add_argument("--entries", required=True, help="JSON list of installer entries")
    im.add_argument("--bundle-dir", default=None, help="bundle directory to checksum")
    iv = ins_sub.add_parser("verify", help="verify files against a manifest")
    iv.add_argument("--manifest", required=True)
    iv.add_argument("--base-dir", default=None)

    # --- license ---------------------------------------------------------
    lic = add("license", "licensing, consent and provenance")
    lic_sub = lic.add_subparsers(dest="op", metavar="OP")
    lg = lic_sub.add_parser("audit", help="asset license audit")
    lg.add_argument("--assets", required=True)
    lg.add_argument("--policy")
    lc = lic_sub.add_parser("consent", help="performer consent audit")
    lc.add_argument("--records", required=True)
    lc.add_argument("--policy")
    lp = lic_sub.add_parser("provenance", help="record provenance for an output")
    lp.add_argument("--file", required=True)
    lp.add_argument("--model")
    lp.add_argument("--model-version")
    lp.add_argument("--seed", type=int)
    lp.add_argument("--license-id")
    lp.add_argument("--operator")
    lp.add_argument("--input", action="append", default=[])

    # --- misc ------------------------------------------------------------
    hs = add("hash", "SHA-256 digests")
    hs.add_argument("--file", action="append", required=True)
    hs.add_argument("--base-dir", default=None)
    return p


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class Context:
    def __init__(self, args):
        self.args = args
        overrides = None
        if getattr(args, "overrides", None):
            import json

            try:
                overrides = json.loads(args.overrides)
            except json.JSONDecodeError as exc:
                raise core.invalid("--overrides is not valid JSON: {0}".format(exc))
        self.config = core.load_config(args.config, overrides)
        self.guard = core.PathGuard(args.workspace, allow_outside=args.allow_outside)
        timeout = args.timeout if args.timeout is not None else self.config.get("limits.timeout_seconds")
        self.deadline = core.Deadline(timeout)
        self.dry_run = bool(args.dry_run)
        self.audit_dir = None
        audit_cfg = self.config.get("limits.audit_dir")
        if audit_cfg:
            try:
                self.audit_dir = self.guard.resolve(audit_cfg, "audit directory", create=True)
            except core.O3DError:
                self.audit_dir = None

    def path(self, value, purpose, must_exist=False, create=False):
        return self.guard.resolve(value, purpose, must_exist=must_exist, create=create)

    def out_path(self, default_name=None, create=False):
        args = self.args
        if args.out:
            path = self.guard.resolve(args.out, "output", create=True)
            if create and not self.dry_run:
                core.ensure_dir(path if os.path.splitext(path)[1] == "" else os.path.dirname(path))
            return path
        if not default_name:
            raise core.invalid("This command needs --out.", "Pass --out with a file or directory path.")
        base = self.config.get("limits.output_dir") or "outputs"
        base_path = self.guard.resolve(base, "output directory", create=True)
        return os.path.join(base_path, default_name)


# ---------------------------------------------------------------------------
# Shared I/O helpers
# ---------------------------------------------------------------------------


def load_mesh_arg(ctx, value, purpose="mesh"):
    path = ctx.path(value, purpose, must_exist=True)
    mesh = geom.load_mesh(path)
    return path, mesh


def parse_pairs(text, sep=":", numeric=float):
    out = []
    for chunk in str(text).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if sep not in chunk:
            raise core.invalid(
                "'{0}' is not in name{1}value form.".format(chunk, sep),
                "Use e.g. Hips:95,Spine:12",
            )
        name, value = chunk.rsplit(sep, 1)
        try:
            out.append((name.strip(), numeric(value)))
        except ValueError:
            raise core.invalid("'{0}' has a non-numeric length.".format(chunk))
    if not out:
        raise core.invalid("No pairs were provided.")
    return out


def write_result_json(ctx, payload, default_name):
    path = ctx.out_path(default_name)
    if os.path.isdir(path):
        path = os.path.join(path, default_name)
    if not ctx.dry_run:
        core.dump_json(path, payload)
    return path


def guard_approval(ctx, action, reason):
    """Require --approve for destructive or publishing actions."""
    if not ctx.args.approve:
        raise core.approval_required(
            action,
            "{0} Re-run with --approve to confirm (a --dry-run report is available without it).".format(reason),
        )


# ---------------------------------------------------------------------------
# Command handlers: meta
# ---------------------------------------------------------------------------


def cmd_help(args, ctx):
    parser = build_parser()
    usage = parser.format_help()
    examples = """EXAMPLES (run from the Skill Tool folder; sh/bash syntax)
  python tools-o3d.py capabilities
  python tools-o3d.py doctor
  python tools-o3d.py fixture --dir fixtures
  python tools-o3d.py scaffold --project hero-film --dir projects/hero-film
  python tools-o3d.py mesh inspect --mesh fixtures/fixture-cube.obj --uv
  python tools-o3d.py mesh validate --mesh fixtures/fixture-sphere.obj --out outputs/sphere-gate.json
  python tools-o3d.py mesh convert --mesh fixtures/fixture-cube.obj --to glb --out outputs/cube.glb
  python tools-o3d.py mesh render --mesh fixtures/fixture-sphere.obj --pose hero --width 640 --height 480 --out outputs/hero.png
  python tools-o3d.py texture pbr-set --family stone --width 256 --height 256 --out outputs/pbr
  python tools-o3d.py texture audit --image outputs/pbr/pbr-stone-basecolor.png --role basecolor
  python tools-o3d.py mocap inspect --bvh fixtures/fixture-capture.bvh
  python tools-o3d.py mocap contacts --bvh fixtures/fixture-capture.bvh
  python tools-o3d.py lipsync text --text "offline pipeline ready" --captions vtt --out outputs/sync.vtt
  python tools-o3d.py plan vram --triangles 250000 --texture 4096x4096x3=base --device-gb 8
  python tools-o3d.py license audit --assets outputs/assets.json --policy asset-license-policy.json
  python tools-o3d.py validate package --dir outputs/delivery

SAFETY
  Destructive or publishing commands (mesh repair --fill overwrite, install verify --base-dir
  outside the workspace, provenance writes) require --approve. Every command supports --dry-run.
"""
    return {
        "usage": usage,
        "examples": examples,
        "exit_codes": {
            "0": "success",
            "1": "runtime error",
            "2": "invalid input or failed validation",
            "3": "approval required",
            "4": "limit/timeout/cancellation",
        },
        "commands": [c[0] for c in CAPABILITIES],
    }


def cmd_doctor(args, ctx):
    import shutil

    deps = {
        "python": sys.version.split()[0],
        "numpy": getattr(sys.modules.get("numpy"), "__version__", None) if "numpy" in sys.modules or _has("numpy") else None,
        "ffmpeg": render.ffmpeg_version(),
        "blender": shutil.which("blender"),
        "imagemagick_convert": shutil.which("convert"),
    }
    config_summary = {
        "config_path": os.path.basename(ctx.config.path) if ctx.config.path else None,
        "fps": ctx.config.get("animation.fps"),
        "up_axis": ctx.config.get("project.up_axis"),
        "units": ctx.config.get("project.units"),
        "timeout_seconds": ctx.config.get("limits.timeout_seconds"),
    }
    advisories = []
    if deps["numpy"] is None:
        advisories.append("numpy is missing: every texture, render and sheet command will fail. "
                          "Install with: python -m pip install 'numpy>=1.24'.")
    if not deps["ffmpeg"]["available"]:
        advisories.append("ffmpeg is not on PATH: MP4 review encodes return dependency_unavailable. "
                          "Copy a static build and set O3D_FFMPEG, or keep delivering PNG/EXR sequences.")
    if ctx.config.get("limits.timeout_seconds") in (None, 0):
        advisories.append("No default timeout is configured; pass --timeout for long renders.")
    return {
        "environment": core.environment_info(),
        "dependencies": deps,
        "config": config_summary,
        "network_required": False,
        "advisories": advisories,
        "status": "ok" if not advisories else "degraded",
    }


def _has(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def cmd_gate(args, ctx):
    rows = [
        {"gate": "geometry", "fail_on": "error",
         "blocking_codes": ["empty_mesh", "nonmanifold_edges", "open_boundary", "scale_defect",
                            "symmetry_defect", "triangle_budget_exceeded", "flipped_uv", "uv_missing"],
         "clear_with": "mesh repair / mesh decimate / mesh symmetry --repair / re-unwrap"},
        {"gate": "animation", "fail_on": "warning",
         "blocking_codes": ["empty_curve", "jitter_or_pop", "loop_seam", "late_start", "early_end"],
         "clear_with": "anim reduce / anim loop / re-time the offending curve"},
        {"gate": "package", "fail_on": "error",
         "blocking_codes": ["missing_deliverable", "empty_file", "missing_readme", "package_too_large"],
         "clear_with": "re-export missing files, add a README, split the delivery"},
        {"gate": "license", "fail_on": "error",
         "blocking_codes": ["unknown_license", "no_commercial_use", "redistribution_forbidden",
                            "ai_training_forbidden", "missing_attribution"],
         "clear_with": "replace the asset, obtain a new license, or record attribution"},
        {"gate": "consent", "fail_on": "error",
         "blocking_codes": ["missing_consent_field", "training_not_permitted", "no_territory", "consent_expired"],
         "clear_with": "obtain signed consent for the required scope before release"},
        {"gate": "install", "fail_on": "error",
         "blocking_codes": ["checksum_mismatch", "installer_missing", "missing_file"],
         "clear_with": "re-copy the verified artifact; never run an unverified installer"},
    ]
    return {"gates": rows, "count": len(rows)}


def cmd_fixture(args, ctx):
    out_dir = ctx.path(args.dir, "fixture directory", create=True)
    if os.path.isdir(out_dir) and os.listdir(out_dir) and not args.force and not ctx.dry_run:
        raise core.invalid(
            "Fixture directory is not empty: {0}".format(args.dir),
            "Pass --force to overwrite fixtures, or choose an empty --dir.",
        )
    if ctx.dry_run:
        return {"status": "dry-run", "would_write": out_dir}
    made = pipeline.make_fixture_meshes(out_dir)
    pbr = tex.pbr_set(128, 128, seed=3, family="stone", name_prefix="fixture-pbr")
    for name, img in pbr["images"].items():
        img.save(os.path.join(out_dir, "fixture-pbr-{0}.png".format(name)))
    uv = tex.generate_texture("uv-layout", 256, 256, seed=0, options={"tiles": 2, "margin": 10})
    uv.save(os.path.join(out_dir, "fixture-uv-layout.png"))
    return {"status": "ok", "directory": args.dir, "mesh_fixtures": made,
            "textures": sorted(os.listdir(out_dir)), "count": len(os.listdir(out_dir))}


def cmd_scaffold(args, ctx):
    root_arg = args.dir or os.path.join("projects", args.project)
    root = ctx.path(root_arg, "project root", create=True)
    if ctx.dry_run:
        return {"status": "dry-run", "root": root_arg, "folders": pipeline.SCAFFOLD_FOLDERS}
    result = pipeline.scaffold(root, args.project, fps=args.fps, units=args.units, up_axis=args.up_axis)
    result["root"] = root_arg
    return result


# ---------------------------------------------------------------------------
# standards
# ---------------------------------------------------------------------------


def cmd_standards(args, ctx):
    if not args.topic:
        raise core.invalid("Choose a standards topic.", "Topics: naming, units, axis, config, handoff")
    if args.topic == "naming":
        result = pipeline.naming_standard(max_length=args.max_length)
        name = "naming-standard.json"
    elif args.topic == "units":
        result = pipeline.unit_standard(unit=args.unit) if False else pipeline.unit_standard(
            scene_unit=args.unit, character_height=args.character_height,
            gravity=args.gravity, for_print=args.for_print)
        name = "unit-standard.json"
    elif args.topic == "axis":
        result = pipeline.axis_standard(up_axis=args.up, forward_axis=args.forward,
                                       handedness=args.handedness, rotation_order=args.rotation_order)
        name = "axis-standard.json"
    elif args.topic == "config":
        try:
            w, h = args.resolution.lower().split("x")
            resolution = (int(w), int(h))
        except ValueError:
            raise core.invalid("--resolution must look like 1920x1080.")
        result = pipeline.project_config(args.project, fps=args.fps, resolution=resolution,
                                        handles=args.handles, color_management=args.color)
        name = "{0}-project-config.json".format(args.project)
    else:
        result = handoff_template(args.project, args.department)
        name = "{0}-handoff-{1}.json".format(args.project, args.department)
    path = write_result_json(ctx, result, name)
    return {"standard": args.topic, "output": os.path.basename(path), "document": result}


def handoff_template(project, department):
    return {
        "schema": "o3d/handoff/1",
        "generated_utc": core.now_iso(),
        "project": project,
        "department": department,
        "fields": {
            "source_version": "asset_model_body_v001",
            "upstream_dependencies": ["rig-free mesh", "approved sculpt v002"],
            "scene_unit": "m",
            "up_axis": "Z",
            "frame_range": [1, 120],
            "handles": 8,
            "color_space": "ACEScg",
            "expected_output": "OBJ + 4K textures + rig reference",
            "owner": "modeling",
            "reviewer": "asset-supervisor",
            "approval_status": "pending",
            "known_limitations": [],
            "notes": "",
        },
        "rules": [
            "Every field is mandatory before handoff; use 'n/a' with a reason instead of leaving blanks.",
            "Record the exact source version, not 'latest'.",
            "Attach checksums for binary deliverables (run: hash).",
        ],
    }


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args, ctx):
    if not args.gate:
        raise core.invalid("Choose a gate.", "Gates: names, geometry, animation, package, license, consent")
    if args.gate == "names":
        names = list(args.name)
        if args.names_file:
            text = core.read_text(ctx.path(args.names_file, "names file", must_exist=True))
            names.extend([ln.strip() for ln in text.splitlines() if ln.strip()])
        if not names:
            raise core.invalid("No names supplied.", "Use --name (repeatable) or --names-file.")
        if args.standard:
            standard = core.load_json(ctx.path(args.standard, "naming standard", must_exist=True), "naming standard")
        else:
            standard = pipeline.naming_standard()
        result = pipeline.validate_names(names, standard)
        result["standard_source"] = os.path.basename(args.standard) if args.standard else "built-in"
        path = write_result_json(ctx, result, "names-validation.json")
        result["output"] = os.path.basename(path)
        return result
    if args.gate == "geometry":
        _, mesh = load_mesh_arg(ctx, args.mesh)
        profile = load_profile(ctx, args.profile, "mesh.profile")
        result = pipeline.geometry_gate(mesh, profile)
        path = write_result_json(ctx, result, "{0}-geometry-gate.json".format(mesh.name))
        result["output"] = os.path.basename(path)
        return result
    if args.gate == "animation":
        curves = load_curves_arg(ctx, args.curves)
        profile = dict(ctx.config.get("animation.profile") or {})
        profile["loop"] = bool(args.loop)
        fps = args.fps or ctx.config.get("animation.fps", 24)
        result = pipeline.animation_gate(curves, fps, profile)
        path = write_result_json(ctx, result, "animation-gate.json")
        result["output"] = os.path.basename(path)
        return result
    if args.gate == "package":
        directory = ctx.path(args.dir, "package directory", must_exist=True)
        profile = load_profile(ctx, args.profile, "qa.package_profile")
        paths = []
        for root_dir, _dirs, files in os.walk(directory):
            for f in sorted(files):
                paths.append(os.path.join(root_dir, f))
        if not paths:
            raise core.invalid("Package directory contains no files: {0}".format(args.dir),
                               "Export the deliverables before running the package gate.")
        result = pipeline.package_gate(paths, directory, profile)
        path = write_result_json(ctx, result, "package-gate.json")
        result["output"] = os.path.basename(path)
        return result
    if args.gate == "license":
        assets = load_json_arg(ctx, args.assets)
        policy = load_policy(ctx, args.policy)
        if isinstance(assets, dict):
            assets = assets.get("assets", [])
        result = pipeline.license_audit(assets, policy)
        path = write_result_json(ctx, result, "license-audit.json")
        result["output"] = os.path.basename(path)
        return result
    if args.gate == "consent":
        records = load_json_arg(ctx, args.records)
        policy = load_policy(ctx, None, filename="asset-consent-policy.json")
        if isinstance(records, dict):
            records = records.get("records", [])
        policy = dict(policy)
        policy.setdefault("today", core.now_iso()[:10])
        result = pipeline.consent_audit(records, policy)
        path = write_result_json(ctx, result, "consent-audit.json")
        result["output"] = os.path.basename(path)
        return result
    raise core.invalid("Unknown gate '{0}'.".format(args.gate))


def load_profile(ctx, value, config_key):
    if value:
        return core.load_json(ctx.path(value, "profile", must_exist=True), "profile")
    profile = ctx.config.get(config_key)
    if not profile:
        raise core.invalid(
            "No profile supplied and {0} is empty in the configuration.".format(config_key),
            "Pass --profile or set {0} in the configuration file.".format(config_key),
        )
    return dict(profile)


def load_policy(ctx, value, filename="asset-license-policy.json"):
    if value:
        return core.load_json(ctx.path(value, "policy", must_exist=True), "policy")
    if ctx.config.path:
        candidate = os.path.join(os.path.dirname(ctx.config.path), filename)
        if os.path.exists(candidate):
            return core.load_json(candidate, "policy")
    raise core.invalid(
        "Policy file not found: {0}.".format(filename),
        "Pass --policy, or keep {0} beside the configuration file.".format(filename),
    )


def load_json_arg(ctx, value):
    return core.load_json(ctx.path(value, "JSON input", must_exist=True), "JSON input")


def load_curves_arg(ctx, value):
    data = load_json_arg(ctx, value)
    if isinstance(data, dict) and "curves" in data:
        data = data["curves"]
    if not isinstance(data, dict) or not data:
        raise core.invalid(
            "Curves file must be an object mapping curve names to [[frame, value], ...].",
            'Example: {"root_z": [[1, 0], [24, 1.2], [48, 0]]}',
        )
    curves = {}
    for name, keys in data.items():
        pairs = []
        for k in keys:
            if not (isinstance(k, (list, tuple)) and len(k) == 2):
                raise core.invalid("Curve '{0}' has a key that is not [frame, value].".format(name))
            pairs.append((float(k[0]), float(k[1])))
        curves[name] = pairs
    return curves


def load_curve_pairs(ctx, value, curve_name=None):
    data = load_json_arg(ctx, value)
    if isinstance(data, dict):
        if curve_name:
            if curve_name not in data:
                raise core.invalid("Curve '{0}' not found in file.".format(curve_name),
                                   "Available: " + ", ".join(sorted(data)))
            data = data[curve_name]
        elif "keys" in data:
            data = data["keys"]
        else:
            raise core.invalid("Multiple curves present; pass --curve NAME.", "Available: " + ", ".join(sorted(data)))
    if not isinstance(data, list) or not data:
        raise core.invalid("Curve data must be a non-empty list of [frame, value] pairs.")
    pairs = []
    for k in data:
        if not (isinstance(k, (list, tuple)) and len(k) == 2):
            raise core.invalid("Key {0} is not a [frame, value] pair.".format(k))
        pairs.append((float(k[0]), float(k[1])))
    return pairs


# ---------------------------------------------------------------------------
# mesh
# ---------------------------------------------------------------------------


def cmd_mesh(args, ctx):
    if not args.op:
        raise core.invalid("Choose a mesh operation.", "Operations: " + ", ".join(
            ["inspect", "validate", "repair", "convert", "decimate", "remesh", "subdivide",
             "smooth", "symmetry", "sample", "uv-analysis", "render"]))
    op = args.op
    mesh_path, mesh = load_mesh_arg(ctx, args.mesh)
    ctx.deadline.check("mesh load")

    if op == "inspect":
        report = inspect_mesh(mesh, uv=args.uv, symmetry_axis=args.symmetry_axis)
        report["source"] = os.path.basename(mesh_path)
        path = write_result_json(ctx, report, "{0}-inspect.json".format(mesh.name))
        report["output"] = os.path.basename(path)
        return report

    if op == "validate":
        profile = load_profile(ctx, args.profile, "mesh.profile")
        result = pipeline.geometry_gate(mesh, profile)
        result["source"] = os.path.basename(mesh_path)
        path = write_result_json(ctx, result, "{0}-geometry-gate.json".format(mesh.name))
        result["output"] = os.path.basename(path)
        return result

    if op == "repair":
        repaired, report = geom.repair_mesh(mesh, weld_distance=args.weld_distance, do_fill=args.fill,
                                            max_hole_edges=args.max_hole_edges)
        out = ctx.out_path("{0}-repaired.obj".format(mesh.name))
        if not ctx.dry_run:
            geom.write_mesh_auto(repaired, out)
        return {
            "operation": "repair",
            "source": os.path.basename(mesh_path),
            "output": os.path.relpath(out, ctx.guard.base) if os.path.isabs(out) else out,
            "operations": report,
            "before": {"triangles": mesh.tri_count, "vertices": len(mesh.verts)},
            "after": {"triangles": repaired.tri_count, "vertices": len(repaired.verts),
                      "boundary_edges": sum(1 for u in geom.edge_map(repaired).values() if len(u) == 1),
                      "nonmanifold_edges": sum(1 for u in geom.edge_map(repaired).values() if len(u) > 2)},
            "status": "ok",
        }

    if op == "convert":
        target = args.to.lower().lstrip(".")
        if target not in ("obj", "ply", "stl", "glb", "json"):
            raise core.invalid("Unsupported target format '{0}'.".format(target),
                               "Use one of: obj, ply, stl, glb, json.")
        transformed = mesh.clone()
        transforms = []
        if args.from_unit or args.to_unit:
            if not (args.from_unit and args.to_unit):
                raise core.invalid("--from-unit and --to-unit must be given together.")
            factor = geom.unit_factor(args.from_unit, args.to_unit)
            transformed.scale(factor)
            transforms.append({"units": [args.from_unit, args.to_unit], "factor": round(factor, 9)})
        if args.scale:
            if args.scale <= 0:
                raise core.invalid("--scale must be positive.")
            transformed.scale(args.scale)
            transforms.append({"scale": args.scale})
        if args.up_from or args.up_to:
            if not (args.up_from and args.up_to):
                raise core.invalid("--up-from and --up-to must be given together.")
            _, conv = geom.apply_axis_conversion(transformed, args.up_from, args.up_to)
            transforms.append({"axis": conv})
        out = ctx.out_path("{0}.{1}".format(mesh.name, target))
        if not ctx.dry_run:
            geom.write_mesh_auto(transformed, out)
        return {
            "operation": "convert",
            "source": os.path.basename(mesh_path),
            "output": os.path.basename(out),
            "target_format": target,
            "transforms": transforms,
            "triangles": transformed.tri_count,
            "vertices": len(transformed.verts),
            "bytes": os.path.getsize(out) if not ctx.dry_run and os.path.exists(out) else None,
            "status": "ok",
        }

    if op == "decimate":
        if args.target_tris <= 0:
            raise core.invalid("--target-tris must be positive.")
        if args.target_tris >= mesh.tri_count:
            raise core.invalid(
                "Target {0} triangles is not below the current {1}.".format(args.target_tris, mesh.tri_count),
                "Pick a target below the current count, or skip decimation.",
            )
        out_mesh, report = geom.cluster_decimate(mesh, args.target_tris)
        out = ctx.out_path("{0}-lod.obj".format(mesh.name))
        if not ctx.dry_run:
            geom.write_mesh_auto(out_mesh, out)
        return {"operation": "decimate", "output": os.path.basename(out), "report": report, "status": "ok"}

    if op == "remesh":
        out_mesh, report = geom.voxel_remesh(mesh, args.voxel_size, max_tris=args.max_tris,
                                             smooth_iterations=args.smooth_iterations)
        out = ctx.out_path("{0}-remesh.obj".format(mesh.name))
        if not ctx.dry_run:
            geom.write_mesh_auto(out_mesh, out)
        return {"operation": "remesh", "output": os.path.basename(out), "report": report, "status": "ok"}

    if op == "subdivide":
        if args.levels < 1:
            raise core.invalid("--levels must be at least 1.")
        out_mesh = geom.subdivide_catmull_clark(mesh, args.levels,
                                                max_tris=int(ctx.config.get("limits.max_output_triangles", 400000)))
        out = ctx.out_path("{0}-subd.obj".format(mesh.name))
        if not ctx.dry_run:
            geom.write_mesh_auto(out_mesh, out)
        return {"operation": "subdivide", "output": os.path.basename(out),
                "before_triangles": mesh.tri_count, "after_triangles": out_mesh.tri_count,
                "levels": args.levels, "status": "ok"}

    if op == "smooth":
        out_mesh = geom.smooth(mesh, args.iterations, args.factor)
        out = ctx.out_path("{0}-smooth.obj".format(mesh.name))
        if not ctx.dry_run:
            geom.write_mesh_auto(out_mesh, out)
        return {"operation": "smooth", "output": os.path.basename(out),
                "iterations": args.iterations, "factor": args.factor,
                "before_area": round(mesh.surface_area(), 6),
                "after_area": round(out_mesh.surface_area(), 6), "status": "ok"}

    if op == "symmetry":
        report = geom.symmetry_report(mesh, axis=args.axis, tolerance=args.tolerance)
        if not args.repair:
            path = write_result_json(ctx, report, "{0}-symmetry.json".format(mesh.name))
            report["output"] = os.path.basename(path)
            report["status"] = "ok"
            return report
        guard_approval(ctx, "mesh symmetry --repair", "Repair overwrites geometry on the -axis side.")
        repaired = mirror_repair(mesh, axis=args.axis, tolerance=args.tolerance)
        out = ctx.out_path("{0}-symmetric.obj".format(mesh.name))
        if not ctx.dry_run:
            geom.write_mesh_auto(repaired, out)
        after = geom.symmetry_report(repaired, axis=args.axis, tolerance=args.tolerance)
        return {"operation": "symmetry-repair", "output": os.path.basename(out),
                "before": report, "after": after, "status": "ok"}

    if op == "sample":
        if args.count <= 0 or args.count > 500000:
            raise core.invalid("--count must be between 1 and 500000.")
        pts = geom.sample_surface(mesh, args.count, args.seed)
        payload = {"mesh": mesh.name, "count": len(pts), "seed": args.seed, "points": pts}
        path = write_result_json(ctx, payload, "{0}-samples.json".format(mesh.name))
        return {"operation": "sample", "output": os.path.basename(path), "count": len(pts),
                "seed": args.seed, "status": "ok"}

    if op == "uv-analysis":
        report = geom.uv_report(mesh, texel_resolution=args.resolution)
        report["source"] = os.path.basename(mesh_path)
        path = write_result_json(ctx, report, "{0}-uv.json".format(mesh.name))
        report["output"] = os.path.basename(path)
        report["status"] = "ok"
        return report

    if op == "render":
        ctx.deadline.check("render start")
        preset = render.POSE_PRESETS[args.pose]
        bg = (18, 20, 26)
        if args.bg:
            bg = tuple(int(float(x)) for x in str(args.bg).split(","))
            if len(bg) != 3:
                raise core.invalid("--bg must be three comma-separated numbers.")
        color, info = render.render_mesh(
            mesh, width=args.width, height=args.height, samples=args.samples,
            bg=bg, exposure=args.exposure, fog=args.fog, wireframe=args.wireframe,
            shading=getattr(args, "shading", "smooth"),
        )
        if args.label:
            color = render.overlay_label(color, args.label)
        ext = {"png": "png", "ppm": "ppm", "exr": "exr"}[args.still_format]
        out = ctx.out_path("{0}-{1}.{2}".format(mesh.name, args.pose, ext))
        if not ctx.dry_run:
            if ext == "exr":
                render.to_exr(color, out)
            elif ext == "ppm":
                render.to_ppm(color, out)
            else:
                render.to_png(color, out)
        return {"operation": "render", "output": os.path.basename(out), "pose": args.pose,
                "preset": preset, "render": info, "status": "ok"}

    raise core.invalid("Unknown mesh operation '{0}'.".format(op))


def inspect_mesh(mesh, uv=False, symmetry_axis="X"):
    edges = geom.edge_map(mesh)
    boundary = sum(1 for u in edges.values() if len(u) == 1)
    nonmanifold = sum(1 for u in edges.values() if len(u) > 2)
    loops = geom.boundary_loops(mesh)
    comps = geom.connected_components(mesh)
    report = {
        "mesh": mesh.name,
        "format": mesh.source.get("format"),
        "sha256": mesh.source.get("sha256"),
        "vertices": len(mesh.verts),
        "faces": len(mesh.faces),
        "triangles": mesh.tri_count,
        "quads": sum(1 for f in mesh.faces if len(f) == 4),
        "ngons": sum(1 for f in mesh.faces if len(f) > 4),
        "bounds": [[round(c, 5) for c in mesh.bounds()[0]], [round(c, 5) for c in mesh.bounds()[1]]],
        "dimensions": [round(d, 5) for d in mesh.dimensions()],
        "center": [round(c, 5) for c in mesh.center()],
        "surface_area": round(mesh.surface_area(), 6),
        "volume": round(mesh.signed_volume(), 6),
        "edges": len(edges),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "boundary_loops": len(loops),
        "connected_components": len(comps),
        "component_sizes": sorted((len(c) for c in comps), reverse=True)[:10],
        "degenerate_faces": len(geom.degenerate_faces(mesh)),
        "valence": geom.valence_stats(mesh),
        "quality": geom.face_quality(mesh),
        "symmetry": geom.symmetry_report(mesh, symmetry_axis),
        "watertight": boundary == 0 and nonmanifold == 0,
    }
    if uv:
        try:
            report["uv"] = geom.uv_report(mesh)
        except core.O3DError as exc:
            report["uv"] = {"error": exc.message, "hint": exc.hint}
    return report


def mirror_repair(mesh, axis="X", tolerance=0.0015):
    """Delete the -axis half and mirror the +axis half across the symmetry plane."""
    idx = {"X": 0, "Y": 1, "Z": 2}[axis]
    lo, hi = mesh.bounds()
    mid = (lo[idx] + hi[idx]) / 2.0
    keep_faces = []
    for f in mesh.faces:
        c = [sum(mesh.verts[i][k] for i in f) / len(f) for k in range(3)]
        if c[idx] >= mid - tolerance:
            keep_faces.append(f)
    if not keep_faces:
        raise core.invalid("No geometry found on the +{0} side to mirror.".format(axis))
    used = set()
    for f in keep_faces:
        used.update(f)
    remap = {}
    verts = []
    for i in sorted(used):
        remap[i] = len(verts)
        v = list(mesh.verts[i])
        if abs(v[idx] - mid) <= tolerance:
            v[idx] = mid
        verts.append(v)
    faces = [[remap[i] for i in f] for f in keep_faces]
    # mirrored copies
    mirror_remap = {}
    for i in sorted(used):
        v = list(mesh.verts[i])
        if abs(v[idx] - mid) <= tolerance:
            mirror_remap[i] = remap[i]
            continue
        v[idx] = 2.0 * mid - v[idx]
        mirror_remap[i] = len(verts)
        verts.append(v)
    for f in keep_faces:
        faces.append([mirror_remap[i] for i in reversed(f)])
    out = geom.Mesh(verts, faces, name=mesh.name or "mirrored")
    out, _ = geom.weld_vertices(out, tolerance * 2)
    out, _ = geom.unify_winding(out)
    return out


# ---------------------------------------------------------------------------
# texture
# ---------------------------------------------------------------------------


def cmd_texture(args, ctx):
    if not args.op:
        raise core.invalid("Choose a texture operation.",
                           "Operations: generate, pbr-set, normal, pack, audit, variant, mips")
    op = args.op
    tex.require_numpy()
    if op == "generate":
        if not (8 <= args.width <= 8192 and 8 <= args.height <= 8192):
            raise core.invalid("Resolution must be between 8 and 8192 pixels per side.")
        options = {"tiles": args.tiles, "cells": args.cells}
        img = tex.generate_texture(args.kind, args.width, args.height, seed=args.seed,
                                   channels=args.channels, options=options)
        out = ctx.out_path("{0}-{1}x{2}.png".format(args.kind, args.width, args.height))
        if not ctx.dry_run:
            img.save(out)
        return {"operation": "generate", "kind": args.kind, "output": os.path.basename(out),
                "width": img.width, "height": img.height, "channels": img.channels,
                "seed": args.seed, "bytes": os.path.getsize(out) if not ctx.dry_run else None, "status": "ok"}
    if op == "pbr-set":
        if not (16 <= args.width <= 4096 and 16 <= args.height <= 4096):
            raise core.invalid("PBR set resolution must be between 16 and 4096.")
        out_arg = ctx.args.out
        out_dir = ctx.out_path(args.prefix + "-maps", create=True)
        if out_arg and os.path.splitext(out_dir)[1]:
            # --out named a file: treat it as the directory stem instead
            out_dir = os.path.splitext(out_dir)[0] + "-maps"
        if not ctx.dry_run:
            core.ensure_dir(out_dir)
        result = tex.pbr_set(args.width, args.height, seed=args.seed, family=args.family, name_prefix=args.prefix)
        written = {}
        for name, img in result["images"].items():
            path = os.path.join(out_dir, "{0}-{1}-{2}.png".format(args.prefix, args.family, name))
            if not ctx.dry_run:
                img.save(path)
            written[name] = os.path.basename(path)
        rel = os.path.relpath(out_dir, ctx.guard.base).replace(os.sep, "/")
        return {"operation": "pbr-set", "family": args.family, "seed": args.seed,
                "directory": rel, "maps": written, "map_count": len(written), "status": "ok"}
    if op == "normal":
        src = ctx.path(args.height_map, "height map", must_exist=True)
        img = tex.Image.load(src)
        if img.channels not in (1, 3, 4):
            raise core.invalid("Height map must be grayscale or RGB(A).")
        height_arr = img.as_float()[:, :, 0]
        arr = tex.height_to_normal(height_arr, args.strength)
        out = ctx.out_path(os.path.splitext(os.path.basename(src))[0] + "-normal.png")
        if not ctx.dry_run:
            tex.Image(arr).save(out)
        audit = tex.audit_texture(out, role="normal") if not ctx.dry_run else {}
        return {"operation": "normal", "output": os.path.basename(out), "strength": args.strength,
                "audit": {k: audit.get(k) for k in ("width", "height", "errors", "warnings")} if audit else None,
                "status": "ok"}
    if op == "pack":
        sources = {}
        for item in args.source:
            if "=" not in item:
                raise core.invalid("--source must be name=path (got '{0}').".format(item))
            name, path = item.split("=", 1)
            sources[name.strip()] = ctx.path(path.strip(), "pack source", must_exist=True)
        layout = {}
        for chunk in str(args.layout).split(","):
            if "=" not in chunk:
                raise core.invalid("--layout must look like R=ao:r,G=roughness:r,B=metallic:r")
            letter, spec = chunk.split("=", 1)
            letter = letter.strip().upper()
            if letter not in ("R", "G", "B", "A"):
                raise core.invalid("Layout channel '{0}' must be R, G, B or A.".format(letter))
            layout[letter] = spec.strip()
        img = tex.pack_channels(sources, layout, args.channels)
        out = ctx.out_path("packed-{0}ch.png".format(args.channels))
        if not ctx.dry_run:
            img.save(out)
        return {"operation": "pack", "output": os.path.basename(out), "layout": layout,
                "channels": img.channels, "width": img.width, "height": img.height, "status": "ok"}
    if op == "audit":
        src = ctx.path(args.image, "image", must_exist=True)
        expected = None
        if args.expected_size:
            try:
                w, h = args.expected_size.lower().split("x")
                expected = (int(w), int(h))
            except ValueError:
                raise core.invalid("--expected-size must look like 2048x2048.")
        result = tex.audit_texture(src, role=args.role, expected_size=expected)
        result["gate"] = pipeline.gate(result["findings"], ctx.config.get("texture.fail_on", "error"))
        path = write_result_json(ctx, result, "{0}-audit.json".format(os.path.splitext(os.path.basename(src))[0]))
        result["output"] = os.path.basename(path)
        return result
    if op == "variant":
        src = ctx.path(args.image, "image", must_exist=True)
        img = tex.Image.load(src)
        if not (0 < args.amount <= 1.0):
            raise core.invalid("--amount must be in (0, 1].")
        variant = tex.make_variant(img, mode=args.mode, amount=args.amount, seed=args.seed)
        stem = os.path.splitext(os.path.basename(src))[0]
        out = ctx.out_path("{0}-{1}.png".format(stem, args.mode))
        if not ctx.dry_run:
            variant.save(out)
        return {"operation": "variant", "output": os.path.basename(out), "mode": args.mode,
                "amount": args.amount, "seed": args.seed, "status": "ok"}
    if op == "mips":
        src = ctx.path(args.image, "image", must_exist=True)
        img = tex.Image.load(src)
        levels, last = tex.downsample_chain(img, levels=max(1, min(args.levels, 10)))
        out_dir = ctx.out_path("mips", create=True)
        written = []
        if not ctx.dry_run:
            core.ensure_dir(out_dir)
            stem = os.path.splitext(os.path.basename(src))[0]
            for i, lvl in enumerate(levels, 1):
                pass
            # write each level explicitly to keep the report verifiable
            current = img
            for i in range(len(levels)):
                current = current.resize(max(1, current.width // 2), max(1, current.height // 2))
                p = os.path.join(out_dir, "{0}-mip{1}.png".format(stem, i + 1))
                current.save(p)
                written.append(os.path.basename(p))
        return {"operation": "mips", "levels": levels, "written": written,
                "smallest": {"width": last.width, "height": last.height}, "status": "ok"}
    raise core.invalid("Unknown texture operation '{0}'.".format(op))


# ---------------------------------------------------------------------------
# anim
# ---------------------------------------------------------------------------


def cmd_anim(args, ctx):
    if not args.op:
        raise core.invalid("Choose an animation operation.", "Operations: sample, stats, reduce, loop, retime, gate")
    if args.op == "gate":
        curves = load_curves_arg(ctx, args.curves)
        profile = dict(ctx.config.get("animation.profile") or {})
        profile["loop"] = bool(args.loop)
        fps = args.fps or ctx.config.get("animation.fps", 24)
        result = pipeline.animation_gate(curves, fps, profile)
        path = write_result_json(ctx, result, "animation-gate.json")
        result["output"] = os.path.basename(path)
        return result
    fps = ctx.config.get("animation.fps", 24)
    keys = load_curve_pairs(ctx, args.keys, getattr(args, "curve", None))
    if args.op == "sample":
        out = anim.resample(keys, fps, args.start, args.end, args.easing, max(1, args.step))
        payload = {"fps": fps, "easing": args.easing, "keys": out,
                   "source_keys": len(keys), "output_keys": len(out)}
        path = write_result_json(ctx, payload, "curve-sampled.json")
        payload["output"] = os.path.basename(path)
        payload["status"] = "ok"
        return payload
    if args.op == "stats":
        stats = anim.curve_stats(keys, fps)
        path = write_result_json(ctx, stats, "curve-stats.json")
        stats["output"] = os.path.basename(path)
        stats["status"] = "ok"
        return stats
    if args.op == "reduce":
        if args.tolerance <= 0:
            raise core.invalid("--tolerance must be positive.", "Start at 0.01 for metres or 0.5 for degrees.")
        reduced, report = anim.reduce_keys(keys, args.tolerance)
        payload = {"keys": reduced, "report": report, "fps": fps}
        path = write_result_json(ctx, payload, "curve-reduced.json")
        payload["output"] = os.path.basename(path)
        payload["status"] = "ok"
        return payload
    if args.op == "loop":
        if args.loop_frames <= 0:
            raise core.invalid("--loop-frames must be positive.")
        looped, report = anim.build_loop(keys, fps, args.loop_frames, args.loops)
        payload = {"keys": looped, "report": report, "fps": fps}
        path = write_result_json(ctx, payload, "curve-loop.json")
        payload["output"] = os.path.basename(path)
        payload["status"] = "ok"
        return payload
    if args.op == "retime":
        out = anim.retime(keys, args.factor, fps)
        payload = {"keys": out, "factor": args.factor, "fps": fps}
        path = write_result_json(ctx, payload, "curve-retimed.json")
        payload["output"] = os.path.basename(path)
        payload["status"] = "ok"
        return payload
    raise core.invalid("Unknown animation operation '{0}'.".format(args.op))


# ---------------------------------------------------------------------------
# mocap / lipsync
# ---------------------------------------------------------------------------


def cmd_mocap(args, ctx):
    if not args.op:
        raise core.invalid("Choose a mocap operation.", "Operations: inspect, contacts, rootmotion, retarget, synth")
    if args.op == "retarget":
        target = parse_pairs(args.target)
        source = parse_pairs(args.source)
        if len(target) != len(source):
            raise core.invalid(
                "Target and source chains have different lengths ({0} vs {1}).".format(len(target), len(source)),
                "Map joint-for-joint; split or merge chains first.",
            )
        result = anim.retarget(target, source, args.style)
        result["status"] = "ok"
        path = write_result_json(ctx, result, "retarget-plan.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "synth":
        if args.frames <= 0 or args.frames > 20000:
            raise core.invalid("--frames must be between 1 and 20000.")
        text = anim.synthetic_bvh(frames=args.frames, fps=args.fps, seed=args.seed)
        out = ctx.out_path("capture-{0}f-{1}fps.bvh".format(args.frames, args.fps))
        if not ctx.dry_run:
            core.write_text_atomic(out, text)
        return {"operation": "synth", "output": os.path.basename(out), "frames": args.frames,
                "fps": args.fps, "seed": args.seed, "bytes": len(text), "status": "ok"}
    bvh_path = ctx.path(args.bvh, "BVH file", must_exist=True)
    ctx.deadline.check("bvh parse")
    bvh = anim.parse_bvh(bvh_path, max_frames=int(ctx.config.get("limits.max_bvh_frames", 200000)))
    if args.op == "inspect":
        result = anim.bvh_summary(bvh)
        result["source"] = os.path.basename(bvh_path)
        result["status"] = "ok"
        path = write_result_json(ctx, result, "mocap-inspect.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "contacts":
        feet = args.foot or [j for j in bvh.joints if "foot" in j.lower()] or []
        if not feet:
            raise core.invalid(
                "No foot joints found in the BVH.",
                "Pass --foot JOINTNAME for each foot joint (the skeleton may use another naming scheme).",
            )
        result = {"source": os.path.basename(bvh_path), "fps": bvh.fps, "frames": bvh.frames,
                  "feet": anim.detect_foot_contacts(bvh, tuple(feet), threshold=args.threshold),
                  "status": "ok"}
        path = write_result_json(ctx, result, "mocap-contacts.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "rootmotion":
        result = anim.extract_root_motion(bvh)
        result["source"] = os.path.basename(bvh_path)
        result["status"] = "ok"
        track = result.pop("track")
        path = write_result_json(ctx, dict(result, track=track), "mocap-rootmotion.json")
        result["track_samples"] = len(track)
        result["output"] = os.path.basename(path)
        return result
    raise core.invalid("Unknown mocap operation '{0}'.".format(args.op))


def cmd_lipsync(args, ctx):
    if not args.mode:
        raise core.invalid("Choose a lip-sync mode.", "Modes: text, audio")
    if args.mode == "text":
        if not (1 <= args.fps <= 240):
            raise core.invalid("--fps must be between 1 and 240.")
        cues = anim.text_to_visemes(args.text, fps=args.fps, start_frame=args.start_frame,
                                    duration_seconds=args.duration)
        meta = {"mode": "text", "fps": args.fps, "cue_count": len(cues)}
    else:
        wav_path = ctx.path(args.wav, "WAV file", must_exist=True)
        samples, rate = anim.read_wav(wav_path)
        cues, meta = anim.visemes_from_audio(samples, rate, fps=args.fps, sensitivity=args.sensitivity)
        meta.update({"mode": "audio", "fps": args.fps, "sample_rate": rate,
                     "source": os.path.basename(wav_path), "cue_count": len(cues)})
    written = {}
    payload = {"meta": meta, "cues": cues}
    path = write_result_json(ctx, payload, "lipsync-cues.json")
    written["cues"] = os.path.basename(path)
    caps = args.captions
    stem = os.path.splitext(path)[0]
    if caps in ("vtt", "both"):
        caption = stem + ".vtt"
        if not ctx.dry_run:
            core.write_text_atomic(caption, anim.cues_to_vtt(cues, args.fps))
        written["vtt"] = os.path.basename(caption)
    if caps in ("srt", "both"):
        caption = stem + ".srt"
        if not ctx.dry_run:
            core.write_text_atomic(caption, anim.cues_to_srt(cues, args.fps))
        written["srt"] = os.path.basename(caption)
    return {"meta": meta, "written": written, "cue_preview": cues[:6],
            "viseme_library": "asset-anim-presets.json", "status": "ok"}


# ---------------------------------------------------------------------------
# plan / install / license / misc
# ---------------------------------------------------------------------------


def parse_resolution(text):
    try:
        w, h = str(text).lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise core.invalid("Resolution must look like 1920x1080.")


def cmd_review(args, ctx):
    if not args.op:
        raise core.invalid("Choose a review operation.", "Operations: turntable, encode")
    if args.op == "turntable":
        mesh_path, mesh = load_mesh_arg(ctx, args.mesh)
        if not (1 <= args.frames <= 720):
            raise core.invalid("--frames must be between 1 and 720.",
                               "Split longer turntables into several runs.")
        target = mesh.center()
        diag = geom.length(geom.sub(mesh.bounds()[1], mesh.bounds()[0])) or 1.0
        out_dir = ctx.out_path("turntable", create=True)
        if not ctx.dry_run:
            core.ensure_dir(out_dir)
        ext = args.still_format
        written = []
        for i in range(args.frames):
            ctx.deadline.check("turntable frame {0}".format(i + 1))
            azimuth = 360.0 * i / float(args.frames)
            cam = render.camera_matrix(azimuth, args.elevation, diag * 1.9, target, 40.0)
            color, info = render.render_mesh(mesh, width=args.width, height=args.height,
                                             samples=args.samples, camera=cam)
            if args.label:
                color = render.overlay_label(color, "{0} {1:03d}".format(args.label, i + 1))
            name = "frame-{0:04d}.{1}".format(i + 1, ext)
            if not ctx.dry_run:
                path = os.path.join(out_dir, name)
                if ext == "exr":
                    render.to_exr(color, path)
                elif ext == "ppm":
                    render.to_ppm(color, path)
                else:
                    render.to_png(color, path)
            written.append(name)
        rel = os.path.relpath(out_dir, ctx.guard.base).replace(os.sep, "/")
        return {
            "operation": "turntable",
            "source": os.path.basename(mesh_path),
            "directory": rel,
            "frames": len(written),
            "first_frame": written[0] if written else None,
            "last_frame": written[-1] if written else None,
            "resolution": [args.width, args.height],
            "format": ext,
            "encode_hint": "python tools-o3d.py review encode --frames-dir {0} --fps {1}".format(
                rel, ctx.config.get("animation.fps", 24)),
            "status": "ok",
        }
    frames_dir = ctx.path(args.frames_dir, "frames directory", must_exist=True)
    exts = (".png",)
    frames = sorted(
        os.path.join(frames_dir, f) for f in os.listdir(frames_dir) if f.lower().endswith(exts)
    )
    if not frames:
        raise core.invalid(
            "No PNG frames found in {0}.".format(args.frames_dir),
            "Render first: python tools-o3d.py review turntable --mesh <mesh>",
        )
    if not (1 <= args.fps <= 240):
        raise core.invalid("--fps must be between 1 and 240.")
    out = ctx.out_path("review.mp4")
    result = render.encode_mp4(frames, out, args.fps, timeout=args.ffmpeg_timeout, bitrate=args.bitrate)
    result["frames_dir"] = args.frames_dir
    result["operation"] = "encode"
    if result["status"] == "ok":
        result["output"] = os.path.relpath(out, ctx.guard.base).replace(os.sep, "/")
    return result


def cmd_plan(args, ctx):
    if not args.op:
        raise core.invalid("Choose a planning operation.", "Operations: vram, ram, disk, render, hardware")
    if args.op == "vram":
        textures = []
        for spec in args.texture:
            name = None
            if "=" in spec:
                spec, name = spec.split("=", 1)
            bits = spec.lower().split("x")
            if len(bits) < 2:
                raise core.invalid("Texture spec must be WxH or WxHxC.")
            try:
                w, h = int(bits[0]), int(bits[1])
                c = int(bits[2]) if len(bits) > 2 else 3
            except ValueError:
                raise core.invalid("Texture spec '{0}' is not numeric.".format(spec))
            textures.append({"name": name or spec, "width": w, "height": h, "channels": c})
        res = parse_resolution(args.resolution)
        result = pipeline.vram_plan(args.triangles, textures, samples=args.samples, resolution=res,
                                    concurrent_apps_gb=args.concurrent_apps_gb, device_gb=args.device_gb)
        result["status"] = "ok"
        path = write_result_json(ctx, result, "vram-plan.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "ram":
        result = pipeline.ram_plan(args.triangles, sculpt_millions=args.sculpt_millions,
                                   caches_gb=tuple(args.cache_gb or [0, 0, 0]), system_gb=args.system_gb)
        result["status"] = "ok"
        path = write_result_json(ctx, result, "ram-plan.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "disk":
        result = pipeline.disk_plan(exr_frames=args.frames, exr_mb=args.frame_mb,
                                    cache_versions=args.cache_versions, available_gb=args.available_gb)
        result["status"] = "ok"
        path = write_result_json(ctx, result, "disk-plan.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "render":
        result = pipeline.render_job_plan(args.frames, args.seconds_per_frame, machines=args.machines,
                                          target_hours=args.target_hours)
        result["status"] = "ok"
        path = write_result_json(ctx, result, "render-plan.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "hardware":
        requirements = load_json_arg(ctx, args.requirements)
        reported = load_json_arg(ctx, args.reported)
        result = pipeline.hardware_audit(requirements, reported)
        result["status"] = "ok" if result["gate"] == "pass" else "fail"
        path = write_result_json(ctx, result, "hardware-audit.json")
        result["output"] = os.path.basename(path)
        return result
    raise core.invalid("Unknown planning operation '{0}'.".format(args.op))


def cmd_install(args, ctx):
    if not args.op:
        raise core.invalid("Choose an install operation.", "Operations: manifest, verify")
    if args.op == "manifest":
        entries = load_json_arg(ctx, args.entries)
        if isinstance(entries, dict):
            entries = entries.get("files") or entries.get("entries") or []
        if not isinstance(entries, list) or not entries:
            raise core.invalid("Entries JSON must be a non-empty list of installer entries.",
                               'Example: [{"file": "blender-4.2.0-linux-x64.tar.xz", "version": "4.2.0", "license": "GPL-3.0"}]')
        bundle_dir = ctx.path(args.bundle_dir, "bundle directory", must_exist=True) if args.bundle_dir else None
        result = pipeline.build_install_manifest(entries, bundle_dir)
        result["status"] = "ok" if result["gate"] == "pass" else "fail"
        path = write_result_json(ctx, result, "offline-install-manifest.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "verify":
        manifest = load_json_arg(ctx, args.manifest)
        base_dir = ctx.path(args.base_dir, "base directory", must_exist=True) if args.base_dir else ctx.guard.base
        ctx.deadline.check("verify")
        result = pipeline.verify_manifest(manifest, base_dir)
        result["base_dir"] = os.path.basename(base_dir)
        result["status"] = "ok" if result["gate"] == "pass" else "fail"
        path = write_result_json(ctx, result, "manifest-verification.json")
        result["output"] = os.path.basename(path)
        return result
    raise core.invalid("Unknown install operation '{0}'.".format(args.op))


def cmd_license(args, ctx):
    if not args.op:
        raise core.invalid("Choose a license operation.", "Operations: audit, consent, provenance")
    if args.op == "audit":
        assets = load_json_arg(ctx, args.assets)
        policy = load_policy(ctx, args.policy)
        if isinstance(assets, dict):
            assets = assets.get("assets", [])
        result = pipeline.license_audit(assets, policy)
        result["status"] = "ok" if result["gate"] == "pass" else "fail"
        path = write_result_json(ctx, result, "license-audit.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "consent":
        records = load_json_arg(ctx, args.records)
        policy = dict(load_policy(ctx, args.policy, filename="asset-consent-policy.json"))
        policy.setdefault("today", core.now_iso()[:10])
        if isinstance(records, dict):
            records = records.get("records", [])
        result = pipeline.consent_audit(records, policy)
        result["status"] = "ok" if result["gate"] == "pass" else "fail"
        path = write_result_json(ctx, result, "consent-audit.json")
        result["output"] = os.path.basename(path)
        return result
    if args.op == "provenance":
        target = ctx.path(args.file, "output file", must_exist=True)
        record = pipeline.provenance_record(target, model=args.model, version=args.model_version,
                                            seed=args.seed, inputs=args.input, license_id=args.license_id,
                                            operator=args.operator)
        record["status"] = "ok"
        path = write_result_json(ctx, record, "{0}-provenance.json".format(os.path.splitext(os.path.basename(target))[0]))
        record["output"] = os.path.basename(path)
        return record
    raise core.invalid("Unknown license operation '{0}'.".format(args.op))


def cmd_hash(args, ctx):
    paths = []
    for value in args.file:
        paths.append(ctx.path(value, "file", must_exist=True))
    base = ctx.path(args.base_dir, "base directory") if args.base_dir else ctx.guard.base
    result = pipeline.hash_paths(paths, base_dir=base)
    result["status"] = "ok"
    path = write_result_json(ctx, result, "checksums.json")
    result["output"] = os.path.basename(path)
    return result


def cmd_report(args, ctx):
    if not args.input:
        raise core.invalid("--input is required.", "Point it at any JSON result this tool wrote.")
    data = load_json_arg(ctx, args.input)
    if not isinstance(data, dict):
        raise core.invalid("Report input must be a JSON object.")
    summary = {}
    for key in ("status", "gate", "operation", "command", "worst", "counts", "triangles", "vertices",
                "width", "height", "output", "source", "family", "frames", "fps"):
        if key in data:
            summary[key] = data[key] if not isinstance(data[key], (dict, list)) else str(data[key])
    sections = []
    findings = data.get("findings")
    if isinstance(findings, list) and findings:
        sections.append({
            "heading": "Findings",
            "table": {
                "columns": ["Severity", "Code", "Message", "Recovery"],
                "rows": [["{0}:".format(f.get("severity")), f.get("code"), f.get("message"), f.get("hint") or "-"]
                         for f in findings],
            },
        })
    scalars = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) and k not in summary}
    if scalars:
        sections.append({
            "heading": "Metrics",
            "table": {"columns": ["Metric", "Value"], "rows": [[k, v] for k, v in sorted(scalars.items())]},
        })
    if data.get("recommendation"):
        sections.append({"heading": "Recommendation", "body": data["recommendation"]})
    if data.get("component_stats") or data.get("components_gib"):
        comp = data.get("components_gib") or data.get("components_used") or {}
        sections.append({
            "heading": "Breakdown",
            "table": {"columns": ["Component", "Value"], "rows": [[k, v] for k, v in sorted(comp.items())]},
        })
    sections.append({
        "heading": "Reproduce",
        "bullets": [
            "Command output written by {0} {1}.".format(TOOL_NAME, VERSION),
            "Re-run the same command with the same inputs to reproduce this report deterministically.",
            "No network access was used; every input path is relative to the skill root.",
        ],
    })
    title = args.title
    if args.html:
        text = pipeline.report_html(title, summary, sections)
        out = ctx.out_path(os.path.splitext(os.path.basename(args.input))[0] + "-report.html")
    else:
        text = pipeline.report_markdown(title, summary, sections)
        out = ctx.out_path(os.path.splitext(os.path.basename(args.input))[0] + "-report.md")
    if not ctx.dry_run:
        core.write_text_atomic(out, text)
    return {"output": os.path.basename(out), "format": "html" if args.html else "markdown",
            "sections": len(sections), "summary": summary, "status": "ok"}


def cmd_sheet(args, ctx):
    if not args.image:
        raise core.invalid("--image is required.", "Pass --image label=path once per tile.")
    items = []
    for spec in args.image:
        if "=" in spec:
            label, path = spec.split("=", 1)
        else:
            label, path = os.path.splitext(os.path.basename(spec))[0], spec
        items.append({"label": label, "path": ctx.path(path, "sheet image", must_exist=True)})
    img = pipeline.contact_sheet(items, columns=args.columns, cell=max(32, min(args.cell, 512)))
    out = ctx.out_path("contact-sheet.png")
    if not ctx.dry_run:
        img.save(out)
    return {"output": os.path.basename(out), "items": len(items), "columns": args.columns,
            "width": img.width, "height": img.height, "status": "ok"}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HANDLERS = {
    "capabilities": cmd_capabilities,
    "help": cmd_help,
    "doctor": cmd_doctor,
    "gate": cmd_gate,
    "fixture": cmd_fixture,
    "scaffold": cmd_scaffold,
    "report": cmd_report,
    "sheet": cmd_sheet,
    "standards": cmd_standards,
    "validate": cmd_validate,
    "mesh": cmd_mesh,
    "texture": cmd_texture,
    "anim": cmd_anim,
    "mocap": cmd_mocap,
    "review": cmd_review,
    "lipsync": cmd_lipsync,
    "plan": cmd_plan,
    "install": cmd_install,
    "license": cmd_license,
    "hash": cmd_hash,
}


def print_text(result, stream=sys.stdout):
    def emit(line=""):
        stream.write(line + "\n")

    status = result.get("status") or result.get("gate") or "ok"
    emit("{0} {1}: {2}".format(TOOL_NAME, result.get("operation") or result.get("command") or "result", status))
    for key, value in result.items():
        if key in ("findings", "groups", "commands", "usage", "examples"):
            continue
        if isinstance(value, (dict, list)):
            emit("  {0}: {1}".format(key, json_short(value)))
        else:
            emit("  {0}: {1}".format(key, value))
    findings = result.get("findings")
    if isinstance(findings, list) and findings:
        emit("  findings:")
        for f in findings:
            emit("    [{0}] {1}: {2}".format(f.get("severity"), f.get("code"), f.get("message")))
    groups = result.get("groups")
    if isinstance(groups, dict):
        emit("  commands:")
        for group in sorted(groups):
            emit("    {0}:".format(group))
            for row in groups[group]:
                emit("      {0:<26} {1}".format(row["command"], row["summary"]))


def json_short(value, limit=220):
    import json as _json

    text = _json.dumps(core.json_safe(value), ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print_help_hint()
        return core.EXIT_INVALID_INPUT
    argv, _passthrough = normalize_argv(list(argv))
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if not args.command:
        print_help_hint()
        return core.EXIT_INVALID_INPUT
    handler = HANDLERS.get(args.command)
    if handler is None:
        sys.stderr.write("Unknown command '{0}'. Run 'python tools-o3d.py capabilities'.\n".format(args.command))
        return core.EXIT_INVALID_INPUT
    ctx = None
    try:
        ctx = Context(args)
        result = handler(args, ctx)
        result.setdefault("command", args.command)
        result["tool"] = {"name": TOOL_NAME, "version": VERSION, "utc": core.now_iso(), "network_used": False}
        if ctx.dry_run:
            result["dry_run"] = True
        if ctx.audit_dir:
            core.audit_log(ctx.audit_dir, "command", command=args.command,
                           subcommand=(getattr(args, "topic", None) or getattr(args, "gate", None)
                                       or getattr(args, "op", None) or getattr(args, "mode", None)),
                           status=result.get("status") or result.get("gate"),
                           out=args.out, approve=bool(args.approve))
        if args.format == "text":
            print_text(result)
        else:
            payload = core.json_safe(result)
            sys.stdout.write(json_dumps(payload))
        status = result.get("status") or result.get("gate")
        if status in ("fail", "fail_gate"):
            return core.EXIT_INVALID_INPUT
        return core.EXIT_OK
    except core.O3DError as exc:
        payload = {
            "tool": {"name": TOOL_NAME, "version": VERSION, "utc": core.now_iso(), "network_used": False},
            "command": args.command if hasattr(args, "command") else None,
            "status": "error",
            "error": exc.to_dict(),
        }
        if args.format == "text":
            sys.stderr.write("ERROR [{0}] {1}\n".format(exc.code, exc.message))
            if exc.hint:
                sys.stderr.write("HINT: {0}\n".format(exc.hint))
        else:
            sys.stdout.write(json_dumps(payload))
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("Cancelled by user.\n")
        return core.EXIT_LIMIT
    except Exception as exc:  # noqa: BLE001 - top-level guard with structured output
        err = core.error_from_exception(exc)
        if args.verbose:
            traceback.print_exc()
        payload = {
            "tool": {"name": TOOL_NAME, "version": VERSION, "utc": core.now_iso()},
            "command": args.command,
            "status": "error",
            "error": err.to_dict(),
        }
        if args.format == "text":
            sys.stderr.write("ERROR [{0}] {1}\n".format(err.code, err.message))
            if err.hint:
                sys.stderr.write("HINT: {0}\n".format(err.hint))
        else:
            sys.stdout.write(json_dumps(payload))
        return err.exit_code
    finally:
        if ctx is not None and ctx.deadline:
            pass


def json_dumps(payload):
    import json as _json

    return _json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def print_help_hint():
    sys.stderr.write(
        "{0} {1} -- offline 3D production Skill Tool\n"
        "Run: python tools-o3d.py help\n"
        "     python tools-o3d.py capabilities\n".format(TOOL_NAME, VERSION)
    )


if __name__ == "__main__":
    sys.exit(main())
