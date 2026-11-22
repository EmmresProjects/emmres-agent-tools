#!/usr/bin/env python3
"""pixel-art-media: built-in test suite.

Exercises every command group through the real CLI entry point (in-process),
plus error paths, approval guards, dry-run, determinism, batch recovery, and
a clean-skill-root relative-path subprocess test. Run:

    python3 pixel-art-media/tools-pixel-media.py selftest
    python3 pixel-art-media/tools-selftest.py          # equivalent

Creates only temporary files (auto-removed). Exit code 0 = all pass.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import contextlib
import importlib.util
import io
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

TOOL_DIR = Path(__file__).resolve().parent


def load_entry():
    m = sys.modules.get("tools_pixel_media")
    if m is None:
        spec = importlib.util.spec_from_file_location("tools_pixel_media",
                                                      TOOL_DIR / "tools-pixel-media.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules["tools_pixel_media"] = m
        spec.loader.exec_module(m)
    return m


ENTRY = load_entry()


def run_cli(argv, expect=0):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = ENTRY.main([str(v) for v in argv])
        except SystemExit as e:
            code = e.code or 0
    if expect is not None and code != expect:
        raise AssertionError(f"argv={argv}\nexpected exit {expect}, got {code}\n"
                             f"stdout={out.getvalue()[:800]}\nstderr={err.getvalue()[:800]}")
    text = out.getvalue().strip()
    data = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    return code, data, err.getvalue()


def make_test_image(path, w=24, h=24, seed=5):
    rng = random.Random(seed)
    img = Image.new("RGBA", (w, h), (240, 235, 220, 255))
    px = img.load()
    for y in range(4, h - 4):
        for x in range(6, w - 6):
            if (x - w // 2) ** 2 + (y - h // 2) ** 2 < 60:
                px[x, y] = (rng.choice([180, 200, 220]), 70, 60, 255)
    img.save(path)
    return img


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pam-selftest-"))
        cls.src = str(cls.tmp / "src.png")
        make_test_image(cls.src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def p(self, name):
        return str(self.tmp / name)

    # ------------------------------------------------------------- meta ---
    def test_01_help(self):
        code, _, _ = run_cli(["--help"], expect=0)

    def test_02_capabilities(self):
        _, d, _ = run_cli(["capabilities"])
        self.assertGreaterEqual(len(d["commands"]), 70)
        self.assertEqual(d["catalog_coverage"]["categories"], 29)
        self.assertEqual(d["catalog_coverage"]["skills"], 318)

    def test_03_capabilities_catalog(self):
        _, d, _ = run_cli(["capabilities", "--catalog", "--category", "7", "--verbose"])
        self.assertEqual(len(d["categories"][0]["skills"]), 12)
        self.assertTrue(all(s["ops"] is not None for s in d["categories"][0]["skills"]))

    # ---------------------------------------------------------- imaging ---
    def test_10_convert(self):
        _, d, _ = run_cli(["convert", self.src, self.p("conv.png"), "--width", "32",
                           "--colors", "8", "--dither", "bayer4", "--cleanup", "--preview-scale", "3"])
        img = Image.open(self.p("conv.png"))
        self.assertEqual(img.width, 32)
        self.assertTrue(Path(d["preview"]).is_file())
        self.assertLessEqual(d["colors_used"], 9)

    def test_11_convert_missing_input(self):
        run_cli(["convert", self.p("nope.png"), self.p("x.png")], expect=3)

    def test_12_convert_bad_color(self):
        run_cli(["outline", self.src, self.p("x.png"), "--color", "notacolor"], expect=2)

    def test_13_scale_integer(self):
        _, d, _ = run_cli(["scale", self.p("conv.png"), self.p("conv-x4.png"), "--factor", "4"])
        self.assertEqual(d["new_size"], [128, d["new_size"][1]])
        img = Image.open(self.p("conv-x4.png"))
        px = img.load()
        self.assertEqual(px[0, 0], px[3, 3])  # 4x4 block uniform

    def test_14_scale_non_integer_rejected(self):
        run_cli(["scale", self.src, self.p("x.png"), "--factor", "2.5"], expect=2)

    def test_15_transform(self):
        _, d, _ = run_cli(["transform", self.src, self.p("rot.png"), "rot90"])
        self.assertEqual(d["new_size"], [24, 24])
        run_cli(["transform", self.src, self.p("fh.png"), "flip-h"])
        run_cli(["transform", self.p("fh.png"), self.p("fh2.png"), "flip-h"])
        self.assertEqual(Image.open(self.src).tobytes(), Image.open(self.p("fh2.png")).tobytes())

    def test_16_crop_bounds(self):
        run_cli(["crop", self.src, self.p("x.png"), "--x", "20", "--y", "0",
                 "--width", "10", "--height", "10"], expect=2)
        run_cli(["crop", self.src, self.p("crop.png"), "--x", "4", "--y", "4",
                 "--width", "8", "--height", "8"], expect=0)

    def test_17_trim_meta(self):
        _, d, _ = run_cli(["trim", self.src, self.p("trim.png"), "--meta", self.p("trim.json")])
        self.assertEqual(d["original_size"], [24, 24])
        meta = json.loads(Path(self.p("trim.json")).read_text())
        self.assertEqual(meta["offset"][0] + meta["trimmed_size"][0] <= 24, True)

    def test_18_pad(self):
        run_cli(["pad", self.src, self.p("pad.png"), "--width", "48", "--height", "48",
                 "--anchor", "center", "--background", "#101018"])
        img = Image.open(self.p("pad.png"))
        self.assertEqual(img.size, (48, 48))
        self.assertEqual(img.getpixel((0, 0)), (16, 16, 24, 255))

    def test_19_outline(self):
        # transparent-background sprite so an outer outline has room to grow
        spr = Image.new("RGBA", (16, 16))
        for x in range(4, 12):
            for y in range(4, 12):
                spr.putpixel((x, y), (200, 80, 60, 255))
        spr.save(self.p("sil.png"))
        run_cli(["outline", self.p("sil.png"), self.p("sil-out.png"), "--color", "#000000"])
        before = sum(1 for px in Image.open(self.p("sil.png")).getdata() if px[3] > 0)
        after = sum(1 for px in Image.open(self.p("sil-out.png")).getdata() if px[3] > 0)
        self.assertGreater(after, before)

    def test_20_cleanup_orphan(self):
        img = Image.new("RGBA", (8, 8), (100, 100, 100, 255))
        img.putpixel((4, 4), (255, 0, 0, 255))
        img.save(self.p("orphan.png"))
        _, d, _ = run_cli(["cleanup", self.p("orphan.png"), self.p("orphan-fixed.png")])
        self.assertEqual(d["orphans_fixed"], 1)
        self.assertNotEqual(Image.open(self.p("orphan-fixed.png")).getpixel((4, 4)), (255, 0, 0, 255))

    def test_21_dither(self):
        run_cli(["palette-generate", self.p("dpal.json"), "--mood", "cool", "--ramps", "1"])
        _, d, _ = run_cli(["dither", self.src, self.p("dith.png"), "--palette", self.p("dpal.json"),
                           "--matrix", "8", "--strength", "60"])
        self.assertEqual(d["output"], self.p("dith.png"))

    def test_22_shadow_silhouette(self):
        run_cli(["shadow", self.p("sil.png"), self.p("shadow.png"), "--blob"])
        self.assertTrue(Path(self.p("shadow.png")).is_file())

    def test_23_recolor_night(self):
        _, d, _ = run_cli(["recolor", self.src, self.p("night.png"), "--preset", "night"])
        a = Image.open(self.src).convert("L")
        b = Image.open(self.p("night.png")).convert("L")
        self.assertLess(sum(b.getdata()), sum(a.getdata()))

    def test_24_remove_bg(self):
        _, d, _ = run_cli(["remove-bg", self.src, self.p("nobg.png"), "--tolerance", "20"])
        img = Image.open(self.p("nobg.png"))
        self.assertEqual(img.getpixel((0, 0))[3], 0)
        self.assertEqual(img.getpixel((12, 12))[3], 255)

    def test_25_split_composite(self):
        img = Image.new("RGBA", (20, 10))
        for x in range(2, 7):
            for y in range(2, 8):
                img.putpixel((x, y), (200, 40, 40, 255))
        for x in range(12, 17):
            for y in range(2, 8):
                img.putpixel((x, y), (40, 40, 200, 255))
        img.save(self.p("two-blobs.png"))
        _, d, _ = run_cli(["split-layers", self.p("two-blobs.png"), self.p("layers"), "--mode", "component"])
        self.assertEqual(len(d["layers"]), 2)
        run_cli(["composite", self.p("recomp.png"), "--layers-json", self.p("layers/layers.json")])
        self.assertEqual(Image.open(self.p("two-blobs.png")).tobytes(),
                         Image.open(self.p("recomp.png")).tobytes())

    def test_26_glitch_determinism(self):
        run_cli(["glitch", self.src, self.p("g1.png"), "--seed", "9"])
        run_cli(["glitch", self.src, self.p("g2.png"), "--seed", "9"])
        run_cli(["glitch", self.src, self.p("g3.png"), "--seed", "10"])
        self.assertEqual(Path(self.p("g1.png")).read_bytes(), Path(self.p("g2.png")).read_bytes())
        self.assertNotEqual(Path(self.p("g1.png")).read_bytes(), Path(self.p("g3.png")).read_bytes())

    def test_27_display_sim_all(self):
        for mode in ("crt", "lcd", "led", "gameboy", "mono", "1bit"):
            run_cli(["display-sim", self.src, self.p(f"sim-{mode}.png"), "--mode", mode, "--scale", "2"])

    def test_28_normal_map(self):
        flat = Image.new("RGBA", (8, 8), (120, 120, 120, 255))
        flat.save(self.p("flat.png"))
        run_cli(["normal-map", self.p("flat.png"), self.p("nrm.png")])
        self.assertEqual(Image.open(self.p("nrm.png")).getpixel((4, 4)), (128, 128, 255, 255))

    def test_29_svg_export(self):
        import xml.etree.ElementTree as ET
        run_cli(["svg-export", self.p("conv.png"), self.p("conv.svg")])
        root = ET.parse(self.p("conv.svg")).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertGreater(len(list(root)), 10)

    # --------------------------------------------------------- palettes ---
    def test_30_palette_roundtrips(self):
        pal = [(10, 20, 30), (200, 100, 50), (250, 250, 240), (0, 0, 0)]
        run_cli(["palette-generate", self.p("rt.json"), "--base", "#3070c0", "--ramps", "1", "--steps", "4"])
        for ext in ("json", "gpl", "pal", "hex", "act", "ase", "png"):
            target = self.p(f"rt-copy.{ext}")
            run_cli(["palette-convert", self.p("rt.json"), target])
            run_cli(["palette-convert", target, self.p(f"rt-back-{ext}.json")])
            a = json.loads(Path(self.p("rt.json")).read_text())["colors"]
            b = json.loads(Path(self.p(f"rt-back-{ext}.json")).read_text())["colors"]
            self.assertEqual(a, b[:len(a)], f"roundtrip failed for {ext}")

    def test_31_palette_determinism(self):
        run_cli(["palette-generate", self.p("pd1.json"), "--mood", "neon", "--seed", "4"])
        run_cli(["palette-generate", self.p("pd2.json"), "--mood", "neon", "--seed", "4"])
        self.assertEqual(Path(self.p("pd1.json")).read_bytes(), Path(self.p("pd2.json")).read_bytes())

    def test_32_palette_analyze(self):
        dup = {"colors": ["#ff0000", "#ff0000", "#00ff00", "#0000ff"]}
        Path(self.p("dup.json")).write_text(json.dumps(dup))
        _, d, _ = run_cli(["palette-analyze", self.p("dup.json"), "--swatches", self.p("cvd.png"),
                           "--report", self.p("pal-report.json")])
        self.assertTrue(d["duplicates"])
        self.assertIn("protanopia", d["cvd"])

    def test_33_palette_swap(self):
        img = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
        img.save(self.p("red.png"))
        Path(self.p("s.json")).write_text(json.dumps({"colors": ["#ff0000"]}))
        Path(self.p("t.json")).write_text(json.dumps({"colors": ["#0000ff"]}))
        run_cli(["palette-swap", self.p("red.png"), self.p("blue.png"),
                 "--src", self.p("s.json"), "--dst", self.p("t.json")])
        self.assertEqual(Image.open(self.p("blue.png")).getpixel((1, 1)), (0, 0, 255, 255))

    def test_34_palette_apply_indexed(self):
        _, d, _ = run_cli(["palette-apply", self.src, self.p("q8.png"), "--colors", "8",
                           "--indexed", self.p("q8-indexed.png")])
        idx = Image.open(self.p("q8-indexed.png"))
        self.assertEqual(idx.mode, "P")
        self.assertIn("transparency", idx.info)

    def test_35_palette_cycle(self):
        _, d, _ = run_cli(["palette-cycle", self.src, self.p("cycle.gif"), "--colors", "8",
                           "--start", "0", "--end", "4", "--fps", "10"])
        self.assertEqual(d["frames"], 4)

    # -------------------------------------------------------- generators ---
    def test_40_sprite_determinism(self):
        run_cli(["sprite-generate", self.p("sp1"), "--count", "3", "--seed", "77"])
        run_cli(["sprite-generate", self.p("sp2"), "--count", "3", "--seed", "77"])
        for f in Path(self.p("sp1")).glob("*.png"):
            self.assertEqual(f.read_bytes(), (Path(self.p("sp2")) / f.name).read_bytes())

    def test_41_icon_all(self):
        _, d, _ = run_cli(["icon-generate", self.p("icons"), "--kind", "all", "--sheet", self.p("icons.png")])
        self.assertEqual(d["count"], 12)

    def test_42_pattern_seamless(self):
        for kind in ("checker", "brick", "weave", "water", "scales", "wood"):
            run_cli(["pattern-generate", self.p(f"pat-{kind}.png"), "--kind", kind, "--size", "32"])
            _, d, _ = run_cli(["seam-check", self.p(f"pat-{kind}.png"), "--preview", self.p(f"pat-{kind}-tiled.png")])
            self.assertTrue(d["pass"], f"{kind} not seamless: {d}")

    def test_43_tileset_modes(self):
        Path(self.p("map.txt")).write_text("....\n.##.\n.##.\n....\n")
        _, d16, _ = run_cli(["tileset-generate", self.p("t16.png"), "--mode", "16", "--tile-size", "16",
                             "--preview-map", self.p("map.txt")])
        _, d47, _ = run_cli(["tileset-generate", self.p("t47.png"), "--mode", "47", "--tile-size", "16",
                             "--preview-map", self.p("map.txt"), "--padding", "0"])
        self.assertEqual(d16["tiles"], 16)
        self.assertEqual(d47["tiles"], 47)
        self.assertTrue(Path(d47["preview_map"]).is_file())

    def test_44_effects_all(self):
        for kind in ("fire", "rain", "snow", "sparkle", "explosion", "smoke", "water", "energy"):
            _, d, _ = run_cli(["effect-generate", self.p(f"fx-{kind}"), "--kind", kind,
                               "--size", "32", "--frames", "8", "--animation", self.p(f"fx-{kind}.gif")])
            self.assertEqual(d["frames"], 8)
            self.assertTrue(Path(self.p(f"fx-{kind}.gif")).is_file())

    def test_45_motion_all(self):
        run_cli(["icon-generate", self.p("micons"), "--kind", "coin"])
        coin = str(next(Path(self.p("micons")).glob("coin-*.png")))
        for kind in ("idle-bob", "hover", "shake", "jump", "walk-bob", "coin-spin",
                     "hit-flash", "dissolve", "pulse"):
            run_cli(["motion-generate", coin, "--kind", kind, "--frames", "6",
                     "--animation", self.p(f"mo-{kind}.gif")])

    def test_46_text_render(self):
        _, d, _ = run_cli(["text-render", "HELLO 42 {heart}", self.p("text.png"),
                           "--font", "5x7", "--scale", "2", "--outline", "#101018"])
        img = Image.open(self.p("text.png"))
        self.assertGreater(img.width, 100)
        _, d2, _ = run_cli(["text-render", "TYPING", self.p("type.gif"), "--effect", "type",
                            "--frames", "6", "--scale", "1"])
        self.assertEqual(d2["frames"], 6)

    def test_47_font_sheet(self):
        _, d, _ = run_cli(["font-sheet", self.p("font.png"), "--font", "3x5"])
        mapping = json.loads(Path(d["mapping"]).read_text())
        self.assertEqual(len(mapping["glyphs"]), d["glyphs"])

    def test_48_ui_all(self):
        for kind, extra in (("panel", []), ("button", ["--label", "PLAY"]),
                            ("bar", ["--value", "0.5", "--segmented"]),
                            ("dialog", ["--label", "NPC"]), ("cursor", []),
                            ("slot", ["--cols", "4", "--rows", "2", "--width", "20"]),
                            ("heart", [])):
            run_cli(["ui-generate", kind, self.p(f"ui-{kind}.png"), "--width", "80",
                     "--height", "24"] + extra)
        states = json.loads(Path(self.p("ui-button.json")).read_text())
        self.assertEqual(len(states["states"]), 5)

    def test_49_voxel_pipeline(self):
        _, d, _ = run_cli(["voxel-build", self.p("model.vox"), "--box", "6x6x4", "--color", "#7a9b4e"])
        self.assertEqual(d["voxels"], 144)
        run_cli(["voxel-render", self.p("model.vox"), self.p("vox.png"), "--view", "iso", "--scale", "6"])
        _, dt, _ = run_cli(["voxel-turntable", self.p("model.vox"), self.p("vt"),
                            "--animation", self.p("vt.gif")])
        self.assertEqual(len(dt["angles"]), 4)
        _, ds, _ = run_cli(["voxel-stack", self.p("model.vox"), self.p("stack.png")])
        self.assertEqual(ds["layers"], 4)
        run_cli(["voxel-build", self.p("model2.json"), "--from-model", self.p("model.vox")])
        m = json.loads(Path(self.p("model2.json")).read_text())
        self.assertEqual(len(m["voxels"]), 144)

    def test_50_iso_primitives(self):
        for kind in ("tile", "cube", "slab"):
            run_cli(["iso-primitive", kind, self.p(f"iso-{kind}.png"), "--size", "16"])

    # ------------------------------------------------------------ sheets ---
    def test_51_pack_slice_roundtrip(self):
        _, d, _ = run_cli(["sheet-pack", self.p("sp1"), "--output", self.p("atlas.png"),
                           "--layout", "grid", "--cols", "3", "--padding", "1",
                           "--meta", self.p("atlas-simple.json"), "--meta-format", "json-simple"])
        self.assertEqual(d["frames"], 3)
        _, ds, _ = run_cli(["sheet-slice", self.p("atlas.png"), self.p("sliced"), "--auto" if False else "--grid", "18x18"])
        self.assertGreaterEqual(ds["frames"], 3)

    def test_52_auto_slice(self):
        sheet = Image.new("RGBA", (60, 20))
        for i, x in enumerate((2, 22, 42)):
            for dx in range(10):
                for dy in range(10):
                    sheet.putpixel((x + dx, 5 + dy), (60 * i + 60, 90, 160, 255))
        sheet.save(self.p("three.png"))
        _, d, _ = run_cli(["sheet-slice", self.p("three.png"), self.p("auto-sliced")])
        self.assertEqual(d["frames"], 3)
        self.assertEqual(d["mode"], "auto")

    def test_53_atlas_meta_formats(self):
        for fmt, ext in (("json-hash", "json"), ("json-array", "json"), ("csv", "csv"),
                         ("xml", "xml"), ("plist", "plist")):
            run_cli(["atlas-meta", self.p("atlas-simple.json"), self.p(f"meta-{fmt}.{ext}"),
                     "--format", fmt, "--image-name", "atlas.png"])
            self.assertTrue(Path(self.p(f"meta-{fmt}.{ext}")).is_file())
        import xml.etree.ElementTree as ET
        import plistlib
        ET.parse(self.p("meta-xml.xml"))
        plistlib.loads(Path(self.p("meta-plist.plist")).read_bytes())

    def test_54_anim_formats(self):
        for ext in ("gif", "png", "webp"):
            _, d, _ = run_cli(["anim-encode", self.p("fx-water"), "--output", self.p(f"anim.{ext}"),
                               "--fps", "12"])
            self.assertEqual(d["frames"], 8)
            _, dd, _ = run_cli(["anim-decode", self.p(f"anim.{ext}"), self.p(f"dec-{ext}")])
            self.assertEqual(dd["frames"], 8)
        # webp lossless pixel equality
        a = Image.open(str(sorted(Path(self.p("fx-water")).glob("frame-*.png"))[0])).convert("RGBA")
        b = Image.open(self.p("dec-webp/frame-000.png")).convert("RGBA")
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_55_anim_timing_preserved(self):
        # source GIF with 250ms delays must keep them on re-encode without --fps
        frames = [Image.new("RGBA", (8, 8), (i * 60, 40, 90, 255)) for i in range(3)]
        frames[0].save(self.p("slow.gif"), save_all=True, append_images=frames[1:],
                       duration=[250, 250, 250], loop=0)
        run_cli(["anim-encode", self.p("slow.gif"), "--output", self.p("slow2.gif")])
        img = Image.open(self.p("slow2.gif"))
        self.assertEqual(img.info.get("duration"), 250)

    def test_56_sequence_export(self):
        _, d, _ = run_cli(["sequence-export", self.p("fx-water"), "--outdir", self.p("seq"),
                           "--format", "png", "--prefix", "f-"])
        self.assertTrue(Path(self.p("seq/f-0000.png")).is_file())
        self.assertEqual(d["pattern"], "f-%04d.png")

    def test_57_aseprite_export(self):
        _, d, _ = run_cli(["aseprite-export", self.p("fx-water"), "--output", self.p("ase.png"),
                           "--tag", "flow"])
        obj = json.loads(Path(d["json"]).read_text())
        self.assertEqual(obj["meta"]["frameTags"][0]["name"], "flow")
        self.assertEqual(len(obj["frames"]), 8)

    def test_58_tags_lifecycle(self):
        run_cli(["tags", self.p("tags.json"), "--init", "--frames", "8", "--size", "16x16",
                 "--add-tag", "idle:0:7", "--add-event", "3:hit", "--set-pivot", "feet:0.5,1"])
        # in-place edit without approval must fail with exit 5
        run_cli(["tags", self.p("tags.json"), "--add-tag", "walk:0:3"], expect=5)
        run_cli(["tags", self.p("tags.json"), "--add-tag", "walk:0:3", "--force"])
        # invalid range -> validation failure exit 7
        run_cli(["tags", self.p("tags.json"), "--add-tag", "bad:5:99", "--force"], expect=7)

    # ---------------------------------------------------------- delivery ---
    def test_60_platform_export(self):
        _, d, _ = run_cli(["platform-export", self.p("conv.png"), self.p("plat"), "--preset", "app-icon"])
        self.assertEqual(d["files"], 9)
        _, df, _ = run_cli(["platform-export", self.p("conv.png"), self.p("fav"), "--preset", "favicon"])
        ico = [f["file"] for f in df["manifest"]["files"] if f["file"].endswith(".ico")]
        self.assertTrue(ico)
        Image.open(self.p(f"fav/{ico[0]}")).load()

    def test_61_contact_sheet(self):
        _, d, _ = run_cli(["contact-sheet", self.p("sp1"), "--output", self.p("contact.png"),
                           "--labels", "--title", "TEST PACK", "--index", self.p("contact.json")])
        self.assertEqual(d["items"], 3)

    def test_62_tilemap_export(self):
        Path(self.p("level.csv")).write_text("0,1,2\n3,4,5\n-1,0,1\n")
        _, d, _ = run_cli(["tilemap-export", "--map", self.p("level.csv"),
                           "--tileset-image", self.p("t16.png"), "--tile-size", "16",
                           "--outdir", self.p("mapout"), "--name", "lvl", "--format", "all"])
        self.assertEqual(len(d["written"]), 4)
        import xml.etree.ElementTree as ET
        root = ET.parse(self.p("mapout/lvl.tmx")).getroot()
        self.assertEqual(root.attrib["width"], "3")
        tj = json.loads(Path(self.p("mapout/lvl.json")).read_text())
        self.assertEqual(tj["layers"][0]["data"][0], 1)  # gid 0 -> 1
        self.assertEqual(tj["layers"][0]["data"][6], 0)  # -1 -> empty
        Path(self.p("badlevel.csv")).write_text("0,999\n")
        run_cli(["tilemap-export", "--map", self.p("badlevel.csv"),
                 "--tileset-image", self.p("t16.png"), "--outdir", self.p("mapout2")], expect=2)

    def test_63_engine_exports(self):
        for target, extra in (("godot", ["--input", self.p("atlas.png"),
                                         "--atlas-json", self.p("atlas-simple.json"), "--name", "hero"]),
                              ("unity", ["--input", self.p("conv.png"), "--ppu", "16"]),
                              ("web", ["--input", self.p("atlas.png")]),
                              ("phaser", ["--input", self.p("atlas.png")]),
                              ("pixi", ["--input", self.p("atlas.png")]),
                              ("rpgmaker", []), ("gamemaker", []), ("unreal", []),
                              ("retro", ["--system", "gameboy"]),
                              ("vtt", ["--input", self.p("conv.png")])):
            _, d, _ = run_cli(["engine-export", target, self.p(f"eng-{target}")] + extra)
            self.assertTrue(d["written"], target)
        tres = Path(self.p("eng-godot/hero-spriteframes.tres")).read_text()
        self.assertIn("AtlasTexture", tres)
        meta = next(Path(self.p("eng-unity")).glob("*.meta")).read_text()
        self.assertIn("filterMode: 0", meta)
        self.assertIn("textureType: 8", meta)
        html = next(Path(self.p("eng-web")).glob("*.html")).read_text()
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("image-rendering:pixelated", html)

    def test_64_captions(self):
        Path(self.p("cues.srt")).write_text(
            "1\n00:00:00,000 --> 00:00:00,400\nHELLO HERO\n\n2\n00:00:00,400 --> 00:00:01,000\nGO EAST\n")
        _, d, _ = run_cli(["caption-render", self.p("anim.gif"), self.p("captioned.gif"),
                           "--cues", self.p("cues.srt"), "--srt-out", self.p("out.srt")])
        self.assertEqual(d["cues"], 2)
        self.assertIn("-->", Path(self.p("out.srt")).read_text())

    def test_65_storyboard(self):
        _, d, _ = run_cli(["storyboard-generate", "--demo", "--output", self.p("board.png"),
                           "--animatic", self.p("animatic.gif")])
        self.assertEqual(d["shots"], 4)
        self.assertTrue(Path(self.p("animatic.gif")).is_file())

    def test_66_script_author(self):
        for kind in ("ffmpeg", "imagemagick", "aseprite-lua", "batch-job"):
            _, d, _ = run_cli(["script-author", kind, self.p(f"scripts-{kind}")])
            self.assertTrue(d["written"])
        _, dr, _ = run_cli(["reopen-test", self.p("scripts-ffmpeg"), self.p("scripts-imagemagick"),
                            self.p("eng-phaser"), self.p("eng-pixi")])
        self.assertTrue(dr["pass"], dr)

    # ----------------------------------------------------------- quality ---
    def test_70_validate_pass_fail(self):
        _, d, _ = run_cli(["validate", self.p("q8.png"), "--max-colors", "8", "--binary-alpha"])
        self.assertTrue(d["pass"])
        run_cli(["validate", self.src, "--max-colors", "2"], expect=7)
        run_cli(["validate", self.p("conv.png"), "--naming", "hero-[0-9]+\\.png"], expect=7)

    def test_71_validate_atlas(self):
        _, d, _ = run_cli(["validate", self.p("atlas-simple.json"), "--sheet", self.p("atlas.png")])
        self.assertTrue(d["pass"])
        bad = json.loads(Path(self.p("atlas-simple.json")).read_text())
        bad["frames"][0]["x"] = 99999
        Path(self.p("bad-atlas.json")).write_text(json.dumps(bad))
        run_cli(["validate", self.p("bad-atlas.json"), "--sheet", self.p("atlas.png")], expect=7)

    def test_72_compare(self):
        run_cli(["scale", self.p("conv.png"), self.p("conv-copy.png"), "--factor", "1"])
        _, d, _ = run_cli(["compare", self.p("conv.png"), self.p("conv-copy.png")])
        self.assertTrue(d["pass"])
        mod = Image.open(self.p("conv.png")).convert("RGBA")
        mod.putpixel((5, 5), (0, 255, 0, 255))
        mod.save(self.p("conv-mod.png"))
        code, d2, _ = run_cli(["compare", self.p("conv-mod.png"), self.p("conv.png"),
                               "--diff-image", self.p("diff.png")], expect=7)
        self.assertFalse(d2["pass"])
        self.assertTrue(Path(self.p("diff.png")).is_file())

    def test_73_restore(self):
        small = Image.new("RGBA", (12, 12))
        rng = random.Random(3)
        small.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256), 255)
                       for _ in range(144)])
        small.save(self.p("native.png"))
        damaged = small.resize((60, 60), Image.Resampling.BILINEAR)
        damaged.save(self.p("damaged.png"))
        _, d, _ = run_cli(["restore", self.p("damaged.png"), self.p("restored.png"),
                           "--cleanup", "--upscale", "5"])
        self.assertEqual(d["block_used"], 5)
        self.assertEqual(d["native_size"], [12, 12])
        self.assertLess(d["colors_after"], d["colors_before"])

    def test_74_critique(self):
        _, d, _ = run_cli(["critique", self.src, "--output", self.p("crit.md"),
                           "--json-out", self.p("crit.json")])
        self.assertTrue(d["findings"])
        self.assertIn("Findings", Path(self.p("crit.md")).read_text())

    def test_75_audit_secrets(self):
        audit_dir = self.tmp / "audit-me"
        audit_dir.mkdir(exist_ok=True)
        (audit_dir / "leak.json").write_text('{"api_key": "AKIAIOSFODNN7EXAMPLE"}')
        (audit_dir / "ok.png").write_bytes(Path(self.p("conv.png")).read_bytes())
        _, d, _ = run_cli(["audit", str(audit_dir), self.p("audit.json"),
                           "--creator", "Test", "--license", "CC0", "--markdown", self.p("audit.md")])
        self.assertGreaterEqual(d["secret_findings"], 1)
        self.assertFalse(d["clean"])
        manifest = json.loads(Path(self.p("audit.json")).read_text())
        self.assertTrue(all(len(f["sha256"]) == 64 for f in manifest["files"]))

    def test_76_license_record(self):
        _, d, _ = run_cli(["license-record", "hero.png", self.p("lic.json"),
                           "--creator", "A. Artist", "--license", "CC-BY-4.0", "--status", "approved"])
        self.assertIn("CC-BY-4.0", d["record"]["attribution_text"])

    def test_77_seam_fix(self):
        # gradient tile: wrap transition (255->0) far harsher than internal steps
        grad = Image.new("RGBA", (16, 16))
        for y in range(16):
            for x in range(16):
                grad.putpixel((x, y), (x * 17, 100, 150, 255))
        grad.save(self.p("grad-tile.png"))
        code, d, _ = run_cli(["seam-check", self.p("grad-tile.png"), "--fix", self.p("fixed-tile.png"),
                              "--preview", self.p("fixed-tiled.png")], expect=7)
        self.assertFalse(d["pass"])
        self.assertTrue(Path(self.p("fixed-tile.png")).is_file())

    # ---------------------------------------------------------- planning ---
    def test_80_planning_docs(self):
        _, db, _ = run_cli(["brief", self.p("brief.json"), "--idea", "a pikachu-like mascot",
                            "--platform", "game-mobile"])
        self.assertTrue(db["ip_flags"])
        run_cli(["constraints", self.p("cons.json"), "--platform", "retro-handheld", "--system", "gameboy"])
        _, ds, _ = run_cli(["scope", self.p("scope.json"), "--items", "characters:2,tiles:32,icons:10"])
        self.assertGreater(ds["estimated_hours"], 0)
        run_cli(["art-bible", self.p("bible.md"), "--title", "Test", "--palette", self.p("rt.json")])
        run_cli(["rubric", self.p("rubric.json")])
        _, dp, _ = run_cli(["practice", self.p("practice.json"), "--count", "4", "--seed", "42"])
        self.assertEqual(dp["exercises"], 4)

    def test_81_guide(self):
        _, d, _ = run_cli(["guide", self.p("guide.md"), "--skill", "Palette Cycling Animator"])
        self.assertEqual(d["category"], 10)
        text = Path(self.p("guide.md")).read_text()
        self.assertIn("palette-cycle", text)
        run_cli(["guide", self.p("g2.md"), "--skill", "Nonexistent Skill XYZ"], expect=3)
        run_cli(["guide", self.p("g3.md"), "--category", "29"])

    # ------------------------------------------------------ batch & safety -
    def test_90_batch_pipeline(self):
        jobs = {"jobs": [
            {"id": "gen", "op": "palette-generate", "positional": [self.p("b-pal.json")],
             "args": {"mood": "desert", "ramps": 2}},
            {"id": "apply", "op": "palette-apply", "positional": [self.src, self.p("b-art.png")],
             "args": {"palette": self.p("b-pal.json")}},
            {"id": "check", "op": "validate", "positional": [self.p("b-art.png")],
             "args": {"max_colors": 12, "binary_alpha": True}}]}
        Path(self.p("jobs.json")).write_text(json.dumps(jobs))
        _, d, _ = run_cli(["batch", self.p("jobs.json"), "--report", self.p("batch-report.json")])
        self.assertTrue(d["pass"])
        self.assertEqual(d["jobs_run"], 3)

    def test_91_batch_stop_and_continue(self):
        jobs = {"jobs": [
            {"id": "bad", "op": "validate", "positional": [self.src], "args": {"max_colors": 1}},
            {"id": "never", "op": "palette-generate", "positional": [self.p("never.json")]}]}
        Path(self.p("jobs-fail.json")).write_text(json.dumps(jobs))
        run_cli(["batch", self.p("jobs-fail.json")], expect=7)
        self.assertFalse(Path(self.p("never.json")).exists())
        _, d, _ = run_cli(["batch", self.p("jobs-fail.json"), "--continue-on-error"], expect=7)
        self.assertEqual(d["jobs_run"], 2)
        self.assertTrue(Path(self.p("never.json")).exists())

    def test_92_overwrite_guard(self):
        run_cli(["silhouette", self.src, self.p("guard.png")])
        run_cli(["silhouette", self.src, self.p("guard.png")], expect=5)
        run_cli(["silhouette", self.src, self.p("guard.png"), "--force"])

    def test_93_dry_run(self):
        _, d, _ = run_cli(["silhouette", self.src, self.p("dry.png"), "--dry-run"])
        self.assertFalse(Path(self.p("dry.png")).exists())
        self.assertTrue(any("dry-run" in w for w in d["written_files"]))

    def test_94_limits_and_config(self):
        run_cli(["convert", self.src, self.p("big.png"), "--width", "99999"], expect=4)
        run_cli(["capabilities", "--config", self.p("missing-config.json")], expect=3)
        Path(self.p("bad-config.json")).write_text("{not json")
        run_cli(["capabilities", "--config", self.p("bad-config.json")], expect=2)
        Path(self.p("cfg.json")).write_text(json.dumps({"limits": {"max_side": 32}}))
        run_cli(["convert", self.src, self.p("big2.png"), "--width", "64",
                 "--config", self.p("cfg.json")], expect=4)

    def test_95_clean_skill_root_relative(self):
        """Copy the tool beside a test SKILL.md in a clean root; run with relative paths only."""
        root = Path(tempfile.mkdtemp(prefix="pam-skillroot-"))
        try:
            (root / "SKILL.md").write_text("# Test skill root\n")
            dest = root / TOOL_DIR.name
            shutil.copytree(TOOL_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
            proc = subprocess.run(
                [sys.executable, f"{TOOL_DIR.name}/tools-pixel-media.py", "capabilities", "--quiet"],
                cwd=root, capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            make_test_image(str(root / "in.png"))
            proc = subprocess.run(
                [sys.executable, f"{TOOL_DIR.name}/tools-pixel-media.py", "convert", "in.png",
                 "out.png", "--width", "32", "--colors", "8"],
                cwd=root, capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertTrue((root / "out.png").is_file())
            self.assertEqual(Image.open(root / "out.png").width, 32)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def run(verbose=False):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2 if verbose else 1)
    result = runner.run(suite)
    report = {"suite": "pixel-art-media selftest", "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "pass": result.wasSuccessful(),
              "details": stream.getvalue() if verbose else
                         [f"{t}: {msg.splitlines()[-1] if msg else ''}"
                          for t, msg in result.failures + result.errors]}
    return report


if __name__ == "__main__":
    rep = run(verbose="--verbose" in sys.argv)
    print(json.dumps(rep, indent=2))
    sys.exit(0 if rep["pass"] else 7)
