#!/usr/bin/env python3
"""spriteforge selftest: exercise every operation against a seeded project root.

Creates a throwaway project, builds the fixtures it needs, then runs all
112 module operations plus the 5 hub operations and asserts the exit code and
error code of each case (including 20 deliberate failure cases).

    python3 tools-spriteforge-selftest.py            # run everything
    python3 tools-spriteforge-selftest.py edit       # only cases matching "edit"
    python3 tools-spriteforge-selftest.py --keep     # keep the temp project

Writes into a temporary directory and removes it afterwards, so it never
touches your project files. Exit 0 only when every case passes.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
T = HERE
KEEP = "--keep" in sys.argv
ROOT = Path(tempfile.mkdtemp(prefix="spriteforge-selftest-")) if not KEEP else Path(
    tempfile.mkdtemp(prefix="spriteforge-selftest-"))
OPP = str(T / "tools-spriteforge.py")
F = "build/frames"
FRAME = "build/frames/1234_idle_000.png"
META = "build/hero.json"
SHEET = "build/hero.png"
PAL = "build/palette.json"
SPEC = str(T / "asset-sf-sample-spec.json")
VOX = str(T / "asset-sf-voxel-spec.json")
RIG = str(T / "asset-sf-rig-spec.json")
MATRIX = str(T / "asset-sf-action-matrix.json")
PIPE = str(T / "asset-sf-pipeline-spec.json")

# (label, module, op, extra argv, expect)   expect: 0 or exit code or error code string
CASES = [
    # hub
    ("hub-capabilities", None, None, ["capabilities"], 0),
    ("hub-modules", None, None, ["modules", "-v"], 0),
    ("hub-doctor", None, None, ["doctor"], 0),
    ("hub-route", None, None, ["route", "--query", "pack atlas for unity"], 0),
    ("hub-run", None, None, ["run", "sheet", "compose", "-i", F, "-o", "work/hub.png"], 0),
    ("hub-bad-module", None, None, ["run", "nope", "x"], 2),
    # author
    ("author-brief", "author", "brief", ["--text", "armoured knight with cape", "-o", "work/brief.json"], 0),
    ("author-frames", "author", "frames", ["--spec", SPEC, "-o", "work/frames"], 0),
    ("author-from-frames", "author", "from-frames", ["-i", F, "-o", "work/action"], 0),
    ("author-palette", "author", "palette", ["--seed", "hero", "-o", "work/pal.json"], 0),
    ("author-rigplan", "author", "rigplan", ["--spec", RIG, "-o", "work/rig.json"], 0),
    ("author-silhouette", "author", "silhouette", ["-i", F, "-o", "work/sil.json"], 0),
    ("author-tokens", "author", "tokens", ["--spec", SPEC, "-o", "work/tokens.json"], 0),
    ("author-turntable", "author", "turntable", ["--spec", VOX, "--yaws", "0,90", "-o", "work/turn"], 0),
    ("author-variants", "author", "variants", ["--base", "knight", "--axis", "team=blue,red", "-o", "work/var"], 0),
    ("author-voxel", "author", "voxel", ["--spec", VOX, "-o", "work/vox"], 0),
    ("author-bad-spec", "author", "frames", ["--spec", "nope.json", "-o", "work/x"], "SPEC_NOT_FOUND"),
    # plan
    ("plan-action-matrix", "plan", "action-matrix", ["-i", MATRIX, "-o", "work/matrix.json"], 0),
    ("plan-catalog", "plan", "catalog", ["--self", "-o", "work/cat.json"], 0),
    ("plan-change-request", "plan", "change-request", ["--text", "frame 4 outline broken", "-o", "work/cr.json"], 0),
    ("plan-dependency", "plan", "dependency", ["--edge", "a=b", "--edge", "b=c", "-o", "work/deps.json"], 0),
    ("plan-estimate", "plan", "estimate", ["--units", "6", "--actions", "idle,walk", "-o", "work/est.json"], 0),
    ("plan-handoff", "plan", "handoff", ["-i", F, "-o", "work/handoff.json"], 0),
    ("plan-rights", "plan", "rights", ["--creator", "studio", "--license", "cc-by", "-o", "work/rights.json"], 0),
    ("plan-router", "plan", "router", ["--query", "atlas for unity", "-o", "work/router.json"], 0),
    ("plan-sheet-plan", "plan", "sheet-plan", ["--frames", "64", "--cell", "64x64", "-o", "work/sp.json"], 0),
    ("plan-state-machine", "plan", "state-machine", ["--states", "idle,walk", "-o", "work/sm.json"], 0),
    ("plan-style-bible", "plan", "style-bible", ["--project", "p", "-o", "work/bible.json"], 0),
    ("plan-version", "plan", "version", ["--asset", "hero", "--version", "1.0.0", "-o", "work/v.json"], 0),
    ("plan-catalog-dir", "plan", "catalog", ["-i", F, "-o", "work/x.json"], "INPUT_IS_DIR"),
    # sheet
    ("sheet-atlas", "sheet", "atlas", ["-i", F, "-o", "work/atlas.png", "--metadata", "work/atlas.json"], 0),
    ("sheet-compose", "sheet", "compose", ["-i", F, "-o", "work/s.png", "--metadata", "work/s.json"], 0),
    ("sheet-detect", "sheet", "detect", ["-i", SHEET, "-o", "work/det.json"], 0),
    ("sheet-extract", "sheet", "extract", ["-i", SHEET, "--grid", "5x3", "--padding", "2", "-o", "work/ex"], 0),
    ("sheet-metadata", "sheet", "metadata", ["-i", META, "-o", "work/m.unity", "--format", "unity"], 0),
    ("sheet-preview", "sheet", "preview", ["-i", SHEET, "--metadata", META, "-o", "work/prev.png"], 0),
    ("sheet-repack", "sheet", "repack", ["-i", SHEET, "-o", "work/rp.png", "--metadata", "work/rp.json", "--cols", "4"], 0),
    ("sheet-slices", "sheet", "slices", ["-i", SHEET, "--metadata", META, "-o", "work/sl"], 0),
    ("sheet-tile", "sheet", "tile", ["-i", FRAME, "-o", "work/tiles"], 0),
    ("sheet-bad-ext", "sheet", "compose", ["-i", F, "-o", "work/x.psd"], "BAD_EXTENSION"),
    # anim
    ("anim-interp", "anim", "interp", ["-i", FRAME, "-o", "work/ip", "--path", "orbit", "--steps", "6"], 0),
    ("anim-loop-probe", "anim", "loop-probe", ["-i", F, "-o", "work/lp.json"], 0),
    ("anim-loop-repair", "anim", "loop-repair", ["-i", F, "-o", "work/lr.json"], 0),
    ("anim-playback", "anim", "playback", ["-i", F, "-o", "work/pb.gif"], 0),
    ("anim-secondary", "anim", "secondary", ["-i", F, "--layer", f"cape={FRAME}", "-o", "work/sec"], 0),
    ("anim-timing", "anim", "timing", ["-i", F, "-o", "work/tm.json"], 0),
    ("anim-tween", "anim", "tween", ["-i", F, "-o", "work/tw", "--steps", "2"], 0),
    # edit
    ("edit-crop", "edit", "crop", ["-i", FRAME, "-o", "work/c.png", "--box", "8,8,56,56"], 0),
    ("edit-pad", "edit", "pad", ["-i", FRAME, "-o", "work/p2.png", "--size", "96x96"], 0),
    ("edit-offset", "edit", "offset", ["-i", FRAME, "-o", "work/o.png", "--dx", "2", "--dy", "1"], 0),
    ("edit-resize", "edit", "resize", ["-i", FRAME, "-o", "work/rz.png", "--size", "128x128"], 0),
    ("edit-rotate", "edit", "rotate", ["-i", FRAME, "-o", "work/rot.png", "--degrees", "90"], 0),
    ("edit-transform", "edit", "transform", ["-i", FRAME, "-o", "work/tf.png", "--scale", "2"], 0),
    ("edit-outline", "edit", "outline", ["-i", F, "-o", "work/ol", "--width", "1"], 0),
    ("edit-threshold-alpha", "edit", "threshold-alpha", ["-i", F, "-o", "work/ta", "--threshold", "128"], 0),
    ("edit-decontaminate", "edit", "decontaminate", ["-i", F, "-o", "work/dc"], 0),
    ("edit-remove-bg", "edit", "remove-bg", ["-i", FRAME, "-o", "work/rb.png", "--color", "#000000"], 0),
    ("edit-palette-map", "edit", "palette-map", ["-i", F, "-o", "work/pm", "--palette", PAL], 0),
    ("edit-quantize", "edit", "quantize", ["-i", F, "-o", "work/q", "--colours", "16"], 0),
    ("edit-swap-color", "edit", "swap-color", ["-i", FRAME, "-o", "work/sc.png", "--old", "#3f6fb5", "--new", "#b53f6f"], 0),
    ("edit-hue-shift", "edit", "hue-shift", ["-i", F, "-o", "work/hs", "--hue", "30"], 0),
    ("edit-pixelate", "edit", "pixelate", ["-i", FRAME, "-o", "work/px.png", "--block", "4"], 0),
    ("edit-despill", "edit", "despill", ["-i", F, "-o", "work/ds"], 0),
    ("edit-despeckle", "edit", "despeckle", ["-i", F, "-o", "work/dk"], 0),
    ("edit-alpha-bleed", "edit", "alpha-bleed", ["-i", F, "-o", "work/ab"], 0),
    ("edit-clean-edges", "edit", "clean-edges", ["-i", F, "-o", "work/ce"], 0),
    ("edit-normalize-size", "edit", "normalize-size", ["-i", F, "-o", "work/ns"], 0),
    ("edit-symmetry", "edit", "symmetry", ["-i", F, "-o", "work/sy"], 0),
    ("edit-upscale", "edit", "upscale", ["-i", FRAME, "-o", "work/us.png", "--factor", "2"], 0),
    ("edit-downscale", "edit", "downscale", ["-i", FRAME, "-o", "work/dw.png", "--factor", "0.5"], 0),
    ("edit-recolor-series", "edit", "recolor-series", ["-i", F, "-o", "work/rs", "--set", "team=#8f3fa0"], 0),
    ("edit-composite", "edit", "composite", ["--layer", FRAME, "--layer", FRAME, "-o", "work/co.png"], 0),
    ("edit-bad-box", "edit", "crop", ["-i", FRAME, "-o", "work/x.png", "--box", "1,2,3"], "BAD_BOX"),
    ("edit-missing", "edit", "crop", ["-i", "nope.png", "-o", "work/x.png", "--box", "0,0,4,4"], "INPUT_NOT_FOUND"),
    ("edit-outside-root", "edit", "crop", ["-i", FRAME, "-o", "/tmp/escape.png", "--box", "0,0,4,4"], "OUTSIDE_ROOT"),
    # convert
    ("convert-image", "convert", "image", ["-i", FRAME, "-o", "work/cv.webp"], 0),
    ("convert-animated", "convert", "animated", ["-i", F, "-o", "work/cv.gif"], 0),
    ("convert-vector", "convert", "vector", ["-i", FRAME, "-o", "work/cv.svg"], 0),
    ("convert-alpha-check", "convert", "alpha-check", ["-i", F, "-o", "work/al.json"], 0),
    ("convert-colormode", "convert", "colormode", ["-i", FRAME, "-o", "work/cm.png", "--mode", "P"], 0),
    ("convert-rename", "convert", "rename", ["-i", F, "-o", "work/rn", "--map-output", "work/rn.json"], 0),
    ("convert-split-source", "convert", "split-source", ["-i", F, "-o", "work/sp"], 0),
    ("convert-batch", "convert", "batch", ["-i", F, "-o", "work/bt", "--scales", "1,0.5"], 0),
    ("convert-preview-video", "convert", "preview-video", ["-i", F, "-o", "work/pv.gif"], 0),
    ("convert-mp4-rejected", "convert", "preview-video", ["-i", F, "-o", "work/pv.mp4"], "BAD_EXTENSION"),
    # validate
    ("validate-sheet", "validate", "sheet", ["-i", SHEET, "--metadata", META, "-o", "work/v1.json"], 0),
    ("validate-flicker", "validate", "flicker", ["-i", F, "-o", "work/v2.json"], 0),
    ("validate-silhouette", "validate", "silhouette", ["-i", F, "-o", "work/v3.json"], 0),
    ("validate-palette", "validate", "palette", ["-i", F, "--palette", PAL, "-o", "work/v4.json"], 0),
    ("validate-seam", "validate", "seam", ["-i", "work/tile-seamless.png", "--tile", "-o", "work/v5.json"], 0),
    ("validate-anchors", "validate", "anchors", ["-i", F, "-o", "work/v6.json"], 0),
    ("validate-regression", "validate", "regression", ["-i", F, "--approved", F, "-o", "work/v7.json"], 0),
    ("validate-accessibility", "validate", "accessibility", ["-i", F, "-o", "work/v8.json"], 0),
    ("validate-consistency", "validate", "consistency", ["-i", F, "-o", "work/v9.json"], 0),
    ("validate-similarity", "validate", "similarity", ["-i", F, "--protect", "work/ex", "--approve", "validate-similarity", "-o", "work/v10.json"], 0),
    ("validate-content-policy", "validate", "content-policy", ["--name", "hero", "--approve", "validate-content-policy", "-o", "work/v11.json"], 0),
    ("validate-cultural", "validate", "cultural", ["--name", "hero", "--approve", "validate-cultural", "-o", "work/v12.json"], 0),
    ("validate-engine-smoke", "validate", "engine-smoke", ["--engine", "unity", "-i", SHEET, "--metadata", META, "--approve", "validate-engine-smoke", "-o", "work/v13.json"], 0),
    ("validate-guard", "validate", "similarity", ["-i", F, "--protect", "work/ex"], "APPROVAL_REQUIRED"),
    # analyze
    ("analyze-catalog", "analyze", "catalog", ["-i", F, "-o", "work/a1.json"], 0),
    ("analyze-duplicates", "analyze", "duplicates", ["-i", F, "-o", "work/a2.json"], 0),
    ("analyze-style", "analyze", "style", ["-i", F, "-o", "work/a3.json"], 0),
    ("analyze-gaps", "analyze", "gaps", ["-i", F, "--plan", MATRIX, "-o", "work/a4.json"], 0),
    ("analyze-layers", "analyze", "layers", ["-i", FRAME, "-o", "work/a5.json"], 0),
    ("analyze-autotile", "analyze", "autotile", ["--mode", "generate", "-o", "work/a6.json"], 0),
    ("analyze-collision", "analyze", "collision", ["-i", FRAME, "-o", "work/a7.json"], 0),
    ("analyze-depth", "analyze", "depth", ["-i", FRAME, "-o", "work/a8"], 0),
    ("analyze-motion", "analyze", "motion", ["-i", F, "-o", "work/a9.json"], 0),
    ("analyze-incremental", "analyze", "incremental", ["-i", F, "--state", "work/inc.json", "-o", "work/a10.json"], 0),
    ("analyze-frame-detect", "analyze", "frame-detect", ["-i", SHEET, "-o", "work/a11.json"], 0),
    ("analyze-review-package", "analyze", "review-package", ["-i", F, "-o", "work/a12"], 0),
    # engine
    ("engine-unity", "engine", "unity", ["--metadata", META, "-o", "work/e1.json", "--approve", "engine-unity"], 0),
    ("engine-godot", "engine", "godot", ["--metadata", META, "-o", "work/e2.tres", "--approve", "engine-godot"], 0),
    ("engine-unreal", "engine", "unreal", ["--metadata", META, "-o", "work/e3.json", "--approve", "engine-unreal"], 0),
    ("engine-gamemaker", "engine", "gamemaker", ["--metadata", META, "-o", "work/e4.json", "--approve", "engine-gamemaker"], 0),
    ("engine-rpgmaker", "engine", "rpgmaker", ["--metadata", META, "-o", "work/e5.json", "--approve", "engine-rpgmaker"], 0),
    ("engine-web", "engine", "web", ["--metadata", META, "-o", "work/e6.json", "--approve", "engine-web"], 0),
    ("engine-mobile", "engine", "mobile", ["--metadata", META, "-o", "work/e7.json", "--approve", "engine-mobile"], 0),
    ("engine-retro", "engine", "retro", ["--metadata", META, "--system", "nes", "-o", "work/e8.json", "--approve", "engine-retro"], 0),
    ("engine-guard", "engine", "unity", ["--metadata", META, "-o", "work/e9.json"], "APPROVAL_REQUIRED"),
    # govern
    ("govern-style-gate", "govern", "style-gate", ["-i", F, "--size", "64x64", "--approve", "govern-stylegate", "-o", "work/g1.json"], 0),
    ("govern-license", "govern", "license", ["-i", "work/LICENCE.txt", "--approve", "govern-license", "-o", "work/g2.json"], 0),
    ("govern-batch-gate", "govern", "batch-gate", ["--action", "publish", "--target", F, "--reason", "rc", "--approve", "govern-batch-gate", "-o", "work/g3.json"], 0),
    ("govern-guard", "govern", "style-gate", ["-i", F], "APPROVAL_REQUIRED"),
    # pipeline
    ("pipeline-run", "pipeline", "run", ["--spec", PIPE, "--journal", "work/p1.json"], 0),
    ("pipeline-watch", "pipeline", "watch", ["-i", F, "--cycles", "1", "--approve", "pipeline-watch", "-o", "work/p2.json", "--state", "work/p2s.json"], 0),
    ("pipeline-recover", "pipeline", "recover", ["--journal", "work/p1.json"], 0),
    ("pipeline-farm", "pipeline", "farm", ["--spec", "work/farm-spec.json", "--workers", "2", "-o", "work/p3"], 0),
    ("pipeline-bad-spec", "pipeline", "run", ["--spec", "nope.json"], "SPEC_NOT_FOUND"),
]


def seed_project() -> None:
    """Build the project fixtures every case depends on."""
    ROOT.mkdir(parents=True, exist_ok=True)
    steps = [
        [sys.executable, OPP, "--root", str(ROOT), "pipeline", "run",
         "--spec", str(T / "asset-sf-pipeline-spec.json"), "--journal", "work/seed.json"],
    ]
    for cmd in steps:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        if proc.returncode != 0:
            print("fixture bootstrap failed:")
            print(proc.stdout[-2000:])
            print(proc.stderr[-2000:])
            raise SystemExit(1)
    (ROOT / "work").mkdir(exist_ok=True)
    (ROOT / "work" / "LICENCE.txt").write_text(
        "Released under CC BY 4.0. Credit: Studio Example.\n", encoding="utf-8")
    # a wrap-safe tile for the seam check
    from PIL import Image, ImageDraw
    tile = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, 0, 31, 31], fill=(111, 174, 74, 255))
    for i in range(0, 32, 4):
        for box in ([i, 0, i + 1, 3], [i, 28, i + 1, 31],
                    [0, i, 3, i + 1], [28, i, 31, i + 1]):
            draw.rectangle(box, fill=(63, 122, 72, 255))
    tile.save(ROOT / "work" / "tile-seamless.png")
    # a farm spec with weights and one dependency
    farm = {"name": "selftest-farm", "jobs": [
        {"id": "idle", "weight": 4, "module": "author", "op": "frames",
         "args": {"spec": "asset-sf-sample-spec.json", "output": "work/farm/idle"}},
        {"id": "walk", "weight": 3, "module": "author", "op": "frames",
         "args": {"spec": "asset-sf-sample-spec.json", "output": "work/farm/walk"}},
        {"id": "attack", "weight": 2, "module": "author", "op": "frames",
         "args": {"spec": "asset-sf-sample-spec.json", "output": "work/farm/attack"}},
        {"id": "sheet", "weight": 1, "module": "sheet", "op": "compose", "depends_on": ["idle"],
         "args": {"input": "build/frames", "output": "work/farm/hero.png",
                  "metadata": "work/farm/hero.json"}},
    ]}
    (ROOT / "work" / "farm-spec.json").write_text(json.dumps(farm, indent=1), encoding="utf-8")
    # protect set for the similarity screen
    src = sorted((ROOT / "build" / "frames").glob("*.png"))
    prot = ROOT / "work" / "protect"
    prot.mkdir(parents=True, exist_ok=True)
    if src:
        shutil.copy(src[0], prot / "ref-a.png")


def run_case(label, module, op, extra, expect):
    if module is None:
        cmd = [sys.executable, OPP, "--root", str(ROOT)] + extra
    else:
        cmd = [sys.executable, OPP, "--root", str(ROOT), "run", module, op] + extra
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    return proc, cmd


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = args[0] if args else None
    print(f"selftest root: {ROOT}")
    seed_project()
    passed = failed = 0
    failures = []
    for label, module, op, extra, expect in CASES:
        if only and only not in label:
            continue
        proc, cmd = run_case(label, module, op, extra, expect)
        if expect == 0:
            ok = proc.returncode == 0
            detail = "" if ok else (proc.stderr.strip().splitlines() or [""])[0]
        elif isinstance(expect, int):
            ok = proc.returncode == expect
            detail = "" if ok else f"expected exit {expect}, got {proc.returncode}"
        else:
            ok = proc.returncode != 0 and expect in (proc.stdout + proc.stderr)
            detail = "" if ok else f"expected {expect}, got exit {proc.returncode}"
        if ok:
            passed += 1
            print(f"PASS {label}")
        else:
            failed += 1
            failures.append((label, detail, " ".join(cmd)))
            print(f"FAIL {label}: {detail}")
    total = passed + failed
    print(f"\n{passed}/{total} passed, {failed} failed")
    if failures:
        print("\nFailures:")
        for label, detail, cmd in failures:
            print(f"  {label}: {detail}\n    {cmd}")
    else:
        print("\nall operations behave as specified")
    if KEEP:
        print(f"kept: {ROOT}")
    else:
        shutil.rmtree(ROOT, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
