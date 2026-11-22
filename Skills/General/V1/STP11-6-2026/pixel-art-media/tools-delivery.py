"""pixel-art-media: delivery, engines, and pipeline authoring.

Platform export presets, contact sheets, engine integration files (Godot,
Unity, Web, Phaser, PixiJS, RPG Maker, GameMaker, Unreal, retro, VTT),
tilemap data export (TMX/TSX/Tiled-JSON/CSV), storyboards/animatics,
caption burning + SRT, external-tool script authoring, and a self-contained
HTML pixel-art player. Scripts are authored, never executed by this tool.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

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


def G():
    return I().sibling("generators")


def S():
    return I().sibling("sheets")


def P():
    return I().sibling("palettes")


def presets():
    return I().load_asset_json("asset-presets.json")


# ---------------------------------------------------------- platform export -

def fit_integer(img, w, h, background=(0, 0, 0, 0), downscale="box"):
    """Integer-scale to fit, center; downscale with box only when necessary."""
    L = I()
    fx, fy = w / img.width, h / img.height
    f = min(fx, fy)
    if f >= 1:
        fi = int(f)
        scaled = L.nearest_scale(img, fi)
        note = f"integer x{fi}"
    else:
        scaled = L.scale_to(img, max(1, round(img.width * f)), max(1, round(img.height * f)), downscale)
        note = "downscaled (non-integer; review at target size)"
    canvas = Image.new("RGBA", (w, h), background)
    canvas.alpha_composite(scaled, ((w - scaled.width) // 2, (h - scaled.height) // 2))
    return canvas, note


def cmd_platform_export(a, ctx):
    L = I()
    presets_all = presets()["platforms"]
    if a.preset not in presets_all:
        raise L.ToolError("bad-arg", f"Unknown preset '{a.preset}'. Options: {', '.join(sorted(presets_all))}")
    img = L.load_image(a.input, ctx.cfg)
    if a.preset == "sticker":
        img = L.outline(img, L.parse_color(a.outline_color), mode="outer")
        img = L.outline(img, (255, 255, 255, 255), mode="outer")
    bg = L.parse_color(a.background)
    spec = presets_all[a.preset]
    outdir = Path(a.outdir)
    manifest = {"preset": a.preset, "source": a.input, "files": []}
    stem = Path(a.input).stem
    for entry in spec["sizes"]:
        if isinstance(entry, dict):
            name, (w, h) = entry["name"], entry["size"]
        else:
            w = h = entry
            name = f"{w}x{h}"
        L.check_dims(ctx.cfg, w, h)
        out, note = fit_integer(img, w, h, bg)
        fname = f"{stem}-{a.preset}-{name}.png"
        L.save_image(out, str(outdir / fname), ctx)
        manifest["files"].append({"file": fname, "size": [w, h], "scaling": note})
    if a.preset == "favicon":
        ico_path = str(outdir / f"{stem}-favicon.ico")
        sizes = [(e if isinstance(e, int) else e["size"][0]) for e in spec["sizes"]]
        imgs = [fit_integer(img, s, s, bg)[0] for s in sizes]
        def writer(tmp):
            imgs[-1].save(tmp, format="ICO", sizes=[(s, s) for s in sizes])
        L.atomic_write(ctx, ico_path, writer)
        manifest["files"].append({"file": Path(ico_path).name, "ico_sizes": sizes})
    L.write_json(manifest, str(outdir / f"{stem}-{a.preset}.json"), ctx)
    return {"outdir": a.outdir, "preset": a.preset, "files": len(manifest["files"]),
            "manifest": manifest}


def cmd_contact_sheet(a, ctx):
    L = I()
    G_ = G()
    files = L.collect_inputs(a.inputs)
    if len(files) > ctx.cfg["limits"]["max_inputs"]:
        raise L.ToolError("limit-inputs", "Too many inputs for one contact sheet.")
    cell = a.cell
    label_h = 10 if a.labels else 0
    cols = a.cols or min(6, len(files))
    rows = math.ceil(len(files) / cols)
    pad = a.padding
    title_h = 16 if a.title else 0
    sheet = Image.new("RGBA", (cols * (cell + pad) + pad,
                               rows * (cell + pad + label_h) + pad + title_h),
                      L.parse_color(a.background))
    d = ImageDraw.Draw(sheet)
    if a.checker:
        for yy in range(0, sheet.height, 8):
            for xx in range(0, sheet.width, 8):
                if (xx // 8 + yy // 8) % 2:
                    d.rectangle((xx, yy, xx + 7, yy + 7), fill=(255, 255, 255, 14))
    if a.title:
        t = G_.render_text(a.title, "3x5", (240, 240, 245, 255))
        sheet.alpha_composite(t, (pad, 4))
    index = []
    for i, f in enumerate(files):
        img = L.load_image(str(f), ctx.cfg)
        thumb, note = fit_integer(img, cell, cell)
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), title_h + pad + r * (cell + pad + label_h)
        sheet.alpha_composite(thumb, (x, y))
        if a.labels:
            lbl = G_.render_text(f.stem[: (cell // 4)], "3x5", (200, 205, 220, 255))
            sheet.alpha_composite(lbl, (x, y + cell + 2))
        index.append({"file": f.name, "cell": [r, c]})
    L.check_dims(ctx.cfg, sheet.width, sheet.height)
    L.save_image(sheet, a.output, ctx)
    res = {"output": a.output, "items": len(files), "grid": [cols, rows]}
    if a.index:
        L.write_json({"items": index}, a.index, ctx)
        res["index"] = a.index
    return res


# ------------------------------------------------------------- tilemap data -

def read_map(path, legend=None):
    L = I()
    p = Path(path)
    if not p.is_file():
        raise L.ToolError("input-not-found", f"Map file not found: {path}")
    text = p.read_text(encoding="utf-8").strip()
    rows = [r.strip() for r in text.splitlines() if r.strip()]
    grid = []
    if "," in rows[0]:
        for r in rows:
            grid.append([int(v) for v in r.split(",")])
    elif legend:
        lmap = {}
        for pair in legend.split(","):
            k, v = pair.split("=")
            lmap[k.strip()] = int(v)
        for r in rows:
            grid.append([lmap.get(ch, -1) for ch in r])
    else:
        raise L.ToolError("bad-map", "ASCII maps need --legend 'A=0,B=1'; CSV maps use comma-separated gids.")
    w = len(grid[0])
    if any(len(r) != w for r in grid):
        raise L.ToolError("bad-map", "All map rows must have equal length.")
    return grid


def cmd_tilemap_export(a, ctx):
    L = I()
    grid = read_map(a.map, a.legend)
    h, w = len(grid), len(grid[0])
    ts_img = L.load_image(a.tileset_image, ctx.cfg)
    ts = a.tile_size
    if ts_img.width % ts or ts_img.height % ts:
        raise L.ToolError("tileset-mismatch", f"Tileset {ts_img.size} not divisible by tile size {ts}.",
                          "Generate tilesets with --padding 0 for grid tilemap export.")
    cols_n = ts_img.width // ts
    tilecount = cols_n * (ts_img.height // ts)
    flat = [v + 1 if v >= 0 else 0 for row in grid for v in row]  # TMX gids: 0 = empty
    bad = [v for v in flat if v > tilecount]
    if bad:
        raise L.ToolError("gid-out-of-range", f"GIDs {sorted(set(bad))[:5]} exceed tilecount {tilecount}.")
    img_name = Path(a.tileset_image).name
    outdir = Path(a.outdir)
    stem = a.name
    written = []
    fmt = a.format
    if fmt in ("tmx", "all"):
        root = ET.Element("map", {"version": "1.10", "tiledversion": "1.10.2",
                                  "orientation": "orthogonal", "renderorder": "right-down",
                                  "width": str(w), "height": str(h),
                                  "tilewidth": str(ts), "tileheight": str(ts),
                                  "infinite": "0", "nextlayerid": "2", "nextobjectid": "1"})
        tset = ET.SubElement(root, "tileset", {"firstgid": "1", "name": stem,
                                               "tilewidth": str(ts), "tileheight": str(ts),
                                               "tilecount": str(tilecount), "columns": str(cols_n)})
        ET.SubElement(tset, "image", {"source": img_name, "width": str(ts_img.width),
                                      "height": str(ts_img.height)})
        layer = ET.SubElement(root, "layer", {"id": "1", "name": "ground",
                                              "width": str(w), "height": str(h)})
        data = ET.SubElement(layer, "data", {"encoding": "csv"})
        data.text = "\n" + ",\n".join(",".join(str(v) for v in row) for row in
                                      [flat[i * w:(i + 1) * w] for i in range(h)]) + "\n"
        ET.indent(root)
        path = str(outdir / f"{stem}.tmx")
        L.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode"), path, ctx)
        written.append(path)
    if fmt in ("tsx", "all"):
        root = ET.Element("tileset", {"name": stem, "tilewidth": str(ts), "tileheight": str(ts),
                                      "tilecount": str(tilecount), "columns": str(cols_n)})
        ET.SubElement(root, "image", {"source": img_name, "width": str(ts_img.width),
                                      "height": str(ts_img.height)})
        ET.indent(root)
        path = str(outdir / f"{stem}.tsx")
        L.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode"), path, ctx)
        written.append(path)
    if fmt in ("json", "all"):
        obj = {"compressionlevel": -1, "height": h, "infinite": False,
               "layers": [{"data": flat, "height": h, "id": 1, "name": "ground",
                           "opacity": 1, "type": "tilelayer", "visible": True,
                           "width": w, "x": 0, "y": 0}],
               "nextlayerid": 2, "nextobjectid": 1, "orientation": "orthogonal",
               "renderorder": "right-down", "tiledversion": "1.10.2",
               "tileheight": ts, "tilewidth": ts,
               "tilesets": [{"firstgid": 1, "image": img_name, "imageheight": ts_img.height,
                             "imagewidth": ts_img.width, "tilewidth": ts, "tileheight": ts,
                             "tilecount": tilecount, "columns": cols_n, "name": stem,
                             "margin": 0, "spacing": 0}],
               "type": "map", "version": "1.10", "width": w}
        path = str(outdir / f"{stem}.json")
        L.write_json(obj, path, ctx)
        written.append(path)
    if fmt in ("csv", "all"):
        path = str(outdir / f"{stem}.csv")
        buf = io.StringIO()
        wr = csv.writer(buf)
        for row in [flat[i * w:(i + 1) * w] for i in range(h)]:
            wr.writerow(row)
        L.write_text(buf.getvalue(), path, ctx)
        written.append(path)
    return {"written": written, "map_size": [w, h], "tilecount": tilecount,
            "gid_convention": "1-based TMX gids; 0 = empty; input -1 = empty"}


# ---------------------------------------------------------- engine exports --

def cmd_engine_export(a, ctx):
    L = I()
    target = a.target
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True) if not ctx.dry_run else None
    written = []
    name = a.name or (Path(a.input).stem if a.input else "sprite")
    if target == "godot":
        if not a.input:
            raise L.ToolError("bad-arg", "godot export needs --input (sheet or image).")
        img = L.load_image(a.input, ctx.cfg)
        res_path = f"res://{Path(a.input).name}"
        lines = ["[gd_resource type=\"SpriteFrames\" load_steps=2 format=3]", ""]
        frames = []
        if a.atlas_json:
            meta = L.read_json(a.atlas_json)
            fl = meta.get("frames", [])
            if isinstance(fl, dict):
                fl = [{"name": k, **v} for k, v in fl.items()]
            for i, f in enumerate(fl):
                fr = f.get("frame", f)
                lines.append(f"[sub_resource type=\"AtlasTexture\" id=\"AtlasTexture_{i}\"]")
                lines.append(f"atlas = ExtResource(\"1\")")
                lines.append(f"region = Rect2({fr['x']}, {fr['y']}, {fr['w']}, {fr['h']})")
                lines.append("")
                frames.append((f.get("name", f"frame-{i}"), f"SubResource(\"AtlasTexture_{i}\")",
                               f.get("duration", 100)))
            load_steps = len(fl) + 2
        else:
            frames = [(name, f"ExtResource(\"1\")", 100)]
            load_steps = 2
        lines[0] = f"[gd_resource type=\"SpriteFrames\" load_steps={load_steps} format=3]"
        lines.insert(1, f"[ext_resource type=\"Texture2D\" path=\"{res_path}\" id=\"1\"]")
        anim = {"frames": [{"duration": d / 100.0, "texture": t} for _, t, d in frames],
                "loop": True, "name": name, "speed": float(a.fps)}
        res = "[resource]\nanimations = [{\n\"frames\": ["
        res += ", ".join("{\n\"duration\": %.2f,\n\"texture\": %s\n}" % (d / 100.0, t)
                         for _, t, d in frames)
        res += "],\n\"loop\": true,\n\"name\": &\"%s\",\n\"speed\": %.1f\n}]\n" % (name, a.fps)
        path = str(outdir / f"{name}-spriteframes.tres")
        L.write_text("\n".join(lines) + res, path, ctx)
        written.append(path)
        guide = f"""# Godot 4 pixel-art import settings

For `{Path(a.input).name}` select the texture in the Import panel:
- Filter: **Nearest** (Project Settings > Rendering > Textures > Canvas Textures > Default Texture Filter = Nearest)
- Repeat: Disabled; Mipmaps: Off; Compress Mode: Lossless
- Project Settings > Display > Window > Stretch: Mode = `viewport`, Aspect = `keep`
- Enable integer scaling: Display > Window > Stretch > Scale Mode = `integer`
- AnimatedSprite2D: assign `{name}-spriteframes.tres`
- Camera2D: enable position smoothing off for pixel-perfect motion; snap 2D transforms to pixel.

The .tres references `{res_path}`; copy the PNG beside your project root or update the path.
"""
        path = str(outdir / "godot-setup.md")
        L.write_text(guide, path, ctx)
        written.append(path)
    elif target == "unity":
        if not a.input:
            raise L.ToolError("bad-arg", "unity export needs --input (image).")
        guid = uuid.uuid4().hex
        ppu = a.ppu
        meta = f"""fileFormatVersion: 2
guid: {guid}
TextureImporter:
  internalIDToNameTable: []
  externalObjects: {{}}
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    enableMipMap: 0
    sRGBTexture: 1
    linearTexture: 0
    fadeOut: 0
    borderMipMap: 0
    mipMapsPreserveCoverage: 0
    alphaTestReferenceValue: 0.5
    mipMapFadeDistanceStart: 1
    mipMapFadeDistanceEnd: 3
  bumpmap:
    convertToNormalMap: 0
    externalNormalMap: 0
    heightScale: 0.25
    normalMapFilter: 0
  isReadable: 0
  streamingMipmaps: 0
  grayScaleToAlpha: 0
  generateCubemap: 6
  cubemapConvolution: 0
  seamlessCubemap: 0
  textureFormat: 1
  maxTextureSize: 2048
  textureSettings:
    serializedVersion: 2
    filterMode: 0
    aniso: 1
    mipBias: 0
    wrapU: 1
    wrapV: 1
    wrapW: 1
  nPOTScale: 0
  lightmap: 0
  compressionQuality: 50
  spriteMode: 1
  spriteExtrude: 1
  spriteMeshType: 1
  alignment: 0
  spritePivot: {{x: 0.5, y: 0.5}}
  spritePixelsToUnits: {ppu}
  spriteBorder: {{x: 0, y: 0, z: 0, w: 0}}
  spriteGenerateFallbackPhysicsShape: 1
  alphaUsage: 1
  alphaIsTransparency: 1
  spriteTessellationDetail: -1
  textureType: 8
  textureShape: 1
  singleChannelComponent: 0
  flipbookRows: 1
  flipbookColumns: 1
  maxTextureSizeSet: 0
  compressionQualitySet: 0
  textureFormatSet: 0
  ignorePngGamma: 0
  applyGammaDecoding: 0
  platformSettings:
  - serializedVersion: 3
    buildTarget: DefaultTexturePlatform
    maxTextureSize: 2048
    resizeAlgorithm: 0
    textureFormat: -1
    textureCompression: 0
    compressionQuality: 50
    crunchedCompression: 0
    allowsAlphaSplitting: 0
    overridden: 0
    androidETC2FallbackOverride: 0
    forceMaximumCompressionQuality_BC6H_BC7: 0
  spriteSheet:
    serializedVersion: 2
    sprites: []
    outline: []
    physicsShape: []
    bones: []
    spriteID: {uuid.uuid4().hex}
    internalID: 0
    vertices: []
    indices:
    edges: []
    weights: []
    secondaryTextures: []
  spritePackingTag:
  pSDRemoveMatte: 0
  pSDShowRemoveMatteOption: 0
  userData:
  assetBundleName:
  assetBundleVariant:
"""
        path = str(outdir / f"{Path(a.input).name}.meta")
        L.write_text(meta, path, ctx)
        written.append(path)
        cam = """// PixelPerfectCameraSetup.cs — attach to your Camera (Unity 2D Pixel Perfect package)
using UnityEngine;
using UnityEngine.U2D;

[RequireComponent(typeof(PixelPerfectCamera))]
public class PixelPerfectCameraSetup : MonoBehaviour
{
    public int assetsPPU = 16;
    public int referenceResolutionY = 180;
    void Awake()
    {
        var ppc = GetComponent<PixelPerfectCamera>();
        ppc.assetsPPU = assetsPPU;
        ppc.refResolutionX = Mathf.RoundToInt(referenceResolutionY * (float)Screen.width / Screen.height);
        ppc.refResolutionY = referenceResolutionY;
        ppc.upscaleRT = true;
        ppc.pixelSnapping = true;
        ppc.cropFrameX = false;
        ppc.cropFrameY = false;
        ppc.stretchFill = false;
    }
}
"""
        path = str(outdir / "PixelPerfectCameraSetup.cs")
        L.write_text(cam, path, ctx)
        written.append(path)
        guide = f"""# Unity pixel-art setup

1. Copy `{Path(a.input).name}` **and** its generated `.meta` into Assets/ (meta sets Point filter,
   no compression, no mipmaps, Sprite type, PPU={a.ppu}). If Unity regenerates the meta, re-apply those settings.
2. Install **2D Pixel Perfect** (Package Manager) and attach `PixelPerfectCameraSetup.cs` to the Camera.
3. SpriteRenderer materials: use Sprites/Default; avoid post-processing that resamples.
4. For atlases use Sprite Atlas v2 with Filter Mode Point, Compression None, padding >= 2.
"""
        path = str(outdir / "unity-setup.md")
        L.write_text(guide, path, ctx)
        written.append(path)
    elif target in ("web", "phaser", "pixi"):
        if not a.input:
            raise L.ToolError("bad-arg", f"{target} export needs --input.")
        if target == "web":
            return _web_player(a, ctx)
        if target == "phaser":
            js = f"""// Phaser 3 — pixel-art scene fragment (atlas from sheet-pack --meta-format json-hash)
// Preload:
this.load.atlas('{name}', '{Path(a.input).name}', '{name}.json');
// Create:
this.anims.create({{
  key: '{name}-loop',
  frames: this.anims.generateFrameNames('{name}'),
  frameRate: {a.fps},
  repeat: -1
}});
const sprite = this.add.sprite(160, 120, '{name}').play('{name}-loop');
// Game config essentials:
// {{ pixelArt: true, roundPixels: true, scale: {{ mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH }} }}
"""
            path = str(outdir / f"{name}-phaser.js")
            L.write_text(js, path, ctx)
            written.append(path)
        else:
            js = f"""// PixiJS v8 — pixel-art sprite fragment
import {{ Assets, Sprite, AnimatedSprite }} from 'pixi.js';
const atlas = await Assets.load('{name}.json'); // json-hash atlas from sheet-pack
const textures = Object.keys(atlas.textures).map(k => atlas.textures[k]);
const anim = new AnimatedSprite(textures);
anim.animationSpeed = {a.fps} / 60;
anim.play();
// app = new Application(); await app.init({{ antialias: false, roundPixels: true }});
// Texture.style.defaultOptions.scaleMode = 'nearest';
"""
            path = str(outdir / f"{name}-pixi.mjs")
            L.write_text(js, path, ctx)
            written.append(path)
    elif target == "rpgmaker":
        # MV/MZ layout templates with grids + naming guide
        layouts = {"charset-single": (144, 192, 48, 48, "$"),
                   "charset-8": (576, 384, 48, 48, ""),
                   "faceset": (576, 288, 144, 144, "")}
        for lname, (w, h, cw, ch, prefix) in layouts.items():
            img = Image.new("RGBA", (w, h), (30, 32, 44, 255))
            d = ImageDraw.Draw(img)
            for yy in range(0, h, ch):
                for xx in range(0, w, cw):
                    d.rectangle((xx, yy, xx + cw - 1, yy + ch - 1), outline=(120, 130, 160, 255))
            path = str(outdir / f"{prefix}template-{lname}.png")
            L.save_image(img, path, ctx)
            written.append(path)
        guide = """# RPG Maker MV/MZ asset layout

- Characters: 3 columns x 4 rows per character (down/left/right/up). Default cell = 48x48 (MV/MZ).
  Single-character sheets: prefix `$` (144x192). 8-character sheets: 576x384, no prefix.
- Big characters: prefix `!` disables the 6px upward shift; `$!` combines both.
- Faces: 144x144 each, 4x2 grid = 576x288 per faceset file.
- Tilesets: A1 768x576 (animated water/autotiles), A2 768x576 (ground autotiles),
  A3 768x384 (buildings), A4 768x720 (walls), B/C/D/E 768x768 (decor). 48px tiles.
- Place files in img/characters, img/faces, img/tilesets. Transparent PNG-24/32.
Templates written here are correctly sized grids to draw over.
"""
        path = str(outdir / "rpgmaker-layout.md")
        L.write_text(guide, path, ctx)
        written.append(path)
    elif target in ("gamemaker", "unreal"):
        if target == "gamemaker":
            guide = """# GameMaker pixel-art setup

- Sprite Editor: Origin = Bottom Center (or your pivot convention); leave Edge Filtering OFF.
- Game Options > (platform) > Graphics: uncheck "Interpolate colors between pixels".
- Or at runtime: `gpu_set_texfilter(false);`
- Animation speed: set per-sprite (frames per game frame or per second consistently).
- Tilesets: create Tile Set assets from strips with tile size matching your grid; keep 0 separation
  or use texture-group padding of 2+ with extruded edges (sheet-pack --extrude 2 --padding 2).
- Texture groups: group per-level atlases to control memory; keep pages <= 2048 for mobile.
- Rooms: camera size should be an integer divisor of display scale for crisp pixels
  (e.g. 320x180 camera on 1280x720 window = x4).
"""
        else:
            guide = """# Unreal Engine Paper 2D pixel-art setup

- Import PNG; in Texture Editor set: Texture Group = 2D Pixels (unfiltered), Filter = Nearest,
  Mip Gen Settings = NoMipmaps, Compression Settings = UserInterface2D (RGBA), sRGB on.
- Right-click texture > Sprite Actions > Extract Sprites (grid or auto).
- Create Flipbook from sprites; set Frames Per Second to your animation fps.
- Material: use Masked/Unlit sprite material; disable texture streaming for pixel textures if blurry.
- Camera: orthographic; set Ortho Width to viewport pixels / desired integer scale.
- Project Settings > Engine > Rendering: disable anti-aliasing for the sprite pass or use r.PostProcessAAQuality 0.
"""
        path = str(outdir / f"{target}-setup.md")
        L.write_text(guide, path, ctx)
        written.append(path)
    elif target == "retro":
        pres = presets()["retro"]
        if a.system not in pres:
            raise L.ToolError("bad-arg", f"Unknown system '{a.system}'. Options: {', '.join(pres)}")
        info = pres[a.system]
        pal = [tuple(I().parse_color(c)[:3]) for c in info["palette"]]
        for ext in (".gpl", ".pal", ".json"):
            path = str(outdir / f"{a.system}-palette{ext}")
            P().write_palette(pal, path, ctx, name=f"{a.system} palette")
            written.append(path)
        guide = f"# {a.system} constraints (documented approximations)\n\n"
        guide += f"- Resolution: {info['resolution']}\n- Colors: {info['colors']}\n"
        guide += f"- Notes: {info['notes']}\n\nPalette files written beside this guide. "
        guide += "These are reference palettes/constraints for homebrew-style art, not hardware emulation.\n"
        path = str(outdir / f"{a.system}-constraints.md")
        L.write_text(guide, path, ctx)
        written.append(path)
    elif target == "vtt":
        if not a.input:
            raise L.ToolError("bad-arg", "vtt export needs --input.")
        img = L.load_image(a.input, ctx.cfg)
        for size in (256, 512):
            token = Image.new("RGBA", (size, size))
            d = ImageDraw.Draw(token)
            ring = max(4, size // 32)
            d.ellipse((0, 0, size - 1, size - 1), fill=(20, 20, 26, 255))
            inner_small, _ = fit_integer(img, size - ring * 4, size - ring * 4)
            inner = Image.new("RGBA", (size, size))
            inner.alpha_composite(inner_small, ((size - inner_small.width) // 2,
                                                (size - inner_small.height) // 2))
            mask = Image.new("L", (size, size))
            md = ImageDraw.Draw(mask)
            md.ellipse((ring * 2, ring * 2, size - ring * 2, size - ring * 2), fill=255)
            token.paste(inner, (0, 0), mask)
            d.ellipse((ring // 2, ring // 2, size - ring // 2, size - ring // 2),
                      outline=L.parse_color(a.border), width=ring)
            path = str(outdir / f"{name}-token-{size}.png")
            L.save_image(token, path, ctx)
            written.append(path)
        guide = ("# Virtual tabletop tokens\n\n- 256px and 512px circular tokens with border ring.\n"
                 "- Foundry VTT: grid size commonly 100px; scale token scale 1 = 1 square.\n"
                 "- Roll20: 70px grid; upload tokens at 280/560 for crisp x4/x8 display.\n")
        path = str(outdir / "vtt-notes.md")
        L.write_text(guide, path, ctx)
        written.append(path)
    else:
        raise L.ToolError("bad-arg", f"Unknown engine target '{target}'.")
    return {"target": target, "written": written}


def _web_player(a, ctx):
    L = I()
    img_path = Path(a.input)
    data = base64.b64encode(img_path.read_bytes()).decode("ascii")
    frames_js = "null"
    if a.atlas_json:
        meta = L.read_json(a.atlas_json)
        frames_js = json.dumps(meta.get("frames", []))
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>__TITLE__ — pixel preview</title>
<style>
 body{background:#14141c;color:#dfe3f0;font-family:monospace;margin:0;padding:16px}
 canvas{image-rendering:pixelated;image-rendering:crisp-edges;background:
   repeating-conic-gradient(#1e2030 0% 25%, #262a3e 0% 50%) 0 0/16px 16px}
 .bar{margin:10px 0;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 button,input{background:#262a3e;color:#dfe3f0;border:1px solid #4a5070;padding:4px 10px;font-family:monospace}
</style></head><body>
<h3>__TITLE__</h3>
<div class="bar">
 <label>Zoom <input id="zoom" type="range" min="1" max="16" value="4"></label>
 <span id="zl">x4</span>
 <button id="play">Pause</button>
 <label><input id="grid" type="checkbox"> pixel grid</label>
 <span id="info"></span>
</div>
<canvas id="cv"></canvas>
<script>
const img = new Image();
img.src = "data:image/png;base64,__DATA__";
const frames = __FRAMES__;
const fps = __FPS__;
const cv = document.getElementById('cv'), cx = cv.getContext('2d');
let zoom = 4, playing = true, fi = 0, last = 0, grid = false;
img.onload = () => { draw(); requestAnimationFrame(tick); };
function draw(){
  const f = frames ? frames[fi % frames.length] : null;
  const w = f ? f.w : img.width, h = f ? f.h : img.height;
  cv.width = w * zoom; cv.height = h * zoom;
  cx.imageSmoothingEnabled = false;
  if (f) cx.drawImage(img, f.x, f.y, f.w, f.h, 0, 0, cv.width, cv.height);
  else cx.drawImage(img, 0, 0, cv.width, cv.height);
  if (grid && zoom >= 4){
    cx.strokeStyle = 'rgba(255,255,255,0.08)'; cx.beginPath();
    for(let x=0;x<=w;x++){cx.moveTo(x*zoom+.5,0);cx.lineTo(x*zoom+.5,cv.height);}
    for(let y=0;y<=h;y++){cx.moveTo(0,y*zoom+.5);cx.lineTo(cv.width,y*zoom+.5);}
    cx.stroke();
  }
  document.getElementById('info').textContent = w + 'x' + h + (frames ? '  frame ' + (fi%frames.length+1) + '/' + frames.length : '');
}
function tick(t){
  if (frames && playing && t - last > 1000/fps){ fi++; last = t; draw(); }
  requestAnimationFrame(tick);
}
document.getElementById('zoom').oninput = e => { zoom = +e.target.value; document.getElementById('zl').textContent = 'x'+zoom; draw(); };
document.getElementById('play').onclick = e => { playing = !playing; e.target.textContent = playing ? 'Pause' : 'Play'; };
document.getElementById('grid').onchange = e => { grid = e.target.checked; draw(); };
</script></body></html>
"""
    html = (html.replace("__TITLE__", img_path.stem).replace("__DATA__", data)
            .replace("__FRAMES__", frames_js).replace("__FPS__", str(a.fps)))
    path = str(Path(a.outdir) / f"{img_path.stem}-player.html")
    L.write_text(html, path, ctx)
    return {"target": "web", "written": [path],
            "note": "Self-contained HTML player (embedded image, integer zoom, pixel grid, playback)."}


# -------------------------------------------------------------- storyboard --

def parse_srt(text):
    cues = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for b in blocks:
        lines = [l for l in b.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", b)
        if not m:
            continue
        g = [int(v) for v in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        tlines = lines[2:] if lines[0].strip().isdigit() else lines[1:]
        cues.append({"start": start, "end": end, "text": " ".join(tlines)})
    return cues


def srt_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = int(t % 60); ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cmd_caption_render(a, ctx):
    L = I()
    G_ = G()
    frames, durations, info = S().decode_animation(a.input, ctx.cfg) if Path(a.input).suffix.lower() in (".gif", ".webp", ".png") else (None, None, None)
    if frames is None or len(frames) == 0:
        raise L.ToolError("bad-input", "caption-render needs an animated or still image input.")
    cues_path = Path(a.cues)
    if not cues_path.is_file():
        raise L.ToolError("input-not-found", f"Cues file not found: {a.cues}")
    if cues_path.suffix.lower() == ".srt":
        cues = parse_srt(cues_path.read_text(encoding="utf-8"))
    else:
        cues = L.read_json(a.cues)
        if isinstance(cues, dict):
            cues = cues.get("cues", [])
    if not cues:
        raise L.ToolError("bad-cues", "No cues parsed from input.")
    total = 0
    timeline = []
    for d in durations:
        timeline.append(total)
        total += d / 1000.0
    out = []
    w, h = frames[0].size
    for fr, t in zip(frames, timeline):
        active = [c for c in cues if c["start"] <= t < c["end"]]
        canvas = fr.copy()
        if active:
            text = "\n".join(c["text"] for c in active)[:200]
            scale = max(1, w // 160)
            txt = G_.render_text(text.upper(), "3x5", (255, 255, 255, 255), scale=scale,
                                 outline_color=(10, 10, 14, 255), align="center")
            if txt.width > w - 4:
                txt = txt.resize((w - 4, max(1, round(txt.height * (w - 4) / txt.width))), Image.Resampling.NEAREST)
            box = Image.new("RGBA", (txt.width + 4, txt.height + 4), (10, 10, 14, 160))
            box.alpha_composite(txt, (2, 2))
            canvas.alpha_composite(box, ((w - box.width) // 2, h - box.height - max(2, h // 20)))
        out.append(canvas)
    written = S().encode_animation(out, durations, a.output, ctx)
    res = {"output": a.output, "cues": len(cues), **written}
    if a.srt_out:
        srt = "\n\n".join(f"{i+1}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}"
                          for i, c in enumerate(cues)) + "\n"
        L.write_text(srt, a.srt_out, ctx)
        res["srt"] = a.srt_out
    return res


def cmd_storyboard_generate(a, ctx):
    L = I()
    G_ = G()
    if a.demo:
        shots = [
            {"title": "ESTABLISH", "description": "Wide shot of the valley at dawn", "duration": 2.0, "camera": "static wide"},
            {"title": "HERO ENTERS", "description": "Hero walks in from the left", "duration": 1.5, "camera": "pan right"},
            {"title": "DISCOVERY", "description": "Hero spots the glowing relic", "duration": 1.5, "camera": "push in"},
            {"title": "TITLE CARD", "description": "Game logo over darkened scene", "duration": 2.0, "camera": "static"},
        ]
    else:
        if not a.input:
            raise L.ToolError("bad-arg", "Provide --input shots.json or --demo.")
        shots = L.read_json(a.input)
        if isinstance(shots, dict):
            shots = shots.get("shots", [])
    if not shots or len(shots) > 64:
        raise L.ToolError("bad-shots", "Storyboard needs 1..64 shots.")
    pw, ph = a.panel_width, a.panel_height
    cols = min(4, len(shots))
    rows = math.ceil(len(shots) / cols)
    board = Image.new("RGBA", (cols * (pw + 8) + 8, rows * (ph + 34) + 8), (18, 18, 26, 255))
    anim_frames, anim_durs = [], []
    base_dir = Path(a.input).parent if a.input else Path(".")
    for i, sh in enumerate(shots):
        title = str(sh.get("title", f"SHOT {i+1}"))[:24]
        desc = str(sh.get("description", ""))[:80]
        dur = float(sh.get("duration", 1.5))
        panel = Image.new("RGBA", (pw, ph), (34, 36, 52, 255))
        if sh.get("image"):
            im = L.load_image(str(base_dir / sh["image"]), ctx.cfg)
            fitted, _ = fit_integer(im, pw, ph)
            panel.alpha_composite(fitted)
        d = ImageDraw.Draw(panel)
        d.rectangle((0, 0, pw - 1, ph - 1), outline=(90, 96, 130, 255))
        num = G_.render_text(f"{i+1:02d}", "3x5", (255, 210, 74, 255), scale=2)
        panel.alpha_composite(num, (4, 4))
        cam = G_.render_text(str(sh.get("camera", ""))[:20].upper(), "3x5", (150, 200, 255, 255))
        panel.alpha_composite(cam, (4, ph - 9))
        r, c = divmod(i, cols)
        x, y = 8 + c * (pw + 8), 8 + r * (ph + 34)
        board.alpha_composite(panel, (x, y))
        t = G_.render_text(title.upper(), "3x5", (240, 240, 245, 255))
        board.alpha_composite(t, (x, y + ph + 3))
        dt = G_.render_text(f"{dur:.1f}s", "3x5", (150, 155, 175, 255))
        board.alpha_composite(dt, (x + pw - dt.width, y + ph + 3))
        hold = max(1, round(dur * a.fps))
        hold = min(hold, ctx.cfg["limits"]["max_frames"])
        big = Image.new("RGBA", (pw, ph), (24, 24, 34, 255))
        big.alpha_composite(panel)
        tt = G_.render_text(title.upper(), "5x7", (255, 255, 255, 255), scale=2, align="center",
                            outline_color=(10, 10, 14, 255))
        big.alpha_composite(tt, ((pw - tt.width) // 2, ph - tt.height - 12))
        anim_frames.append(big)
        anim_durs.append(int(dur * 1000 / hold))
        anim_frames.extend([big] * (hold - 1))
        anim_durs.extend([int(dur * 1000 / hold)] * (hold - 1))
        if len(anim_frames) >= ctx.cfg["limits"]["max_frames"]:
            anim_frames = anim_frames[:ctx.cfg["limits"]["max_frames"]]
            anim_durs = anim_durs[:ctx.cfg["limits"]["max_frames"]]
            break
    L.check_dims(ctx.cfg, board.width, board.height)
    L.save_image(board, a.output, ctx)
    md = "# Storyboard\n\n| # | Title | Camera | Duration | Description |\n|---|-------|--------|----------|-------------|\n"
    for i, sh in enumerate(shots):
        md += f"| {i+1} | {sh.get('title','')} | {sh.get('camera','')} | {sh.get('duration',1.5)}s | {sh.get('description','')} |\n"
    mdpath = str(Path(a.output).with_suffix(".md"))
    L.write_text(md, mdpath, ctx)
    res = {"board": a.output, "markdown": mdpath, "shots": len(shots)}
    if a.animatic:
        info = S().encode_animation(anim_frames, anim_durs, a.animatic, ctx)
        res["animatic"] = a.animatic
        res.update(info)
    return res


# ----------------------------------------------------------- script author --

def cmd_script_author(a, ctx):
    L = I()
    kind = a.kind
    outdir = Path(a.outdir)
    written = []
    fps = a.fps
    if kind == "ffmpeg":
        sh = f"""#!/usr/bin/env bash
# FFmpeg pixel-art delivery pipelines (generated by pixel-art-media script-author).
# Run from the directory containing your frame sequence. Requires ffmpeg on PATH.
set -euo pipefail
SEQ_PATTERN="${{1:-frame-%04d.png}}"
FPS="${{2:-{fps}}}"
SCALE="${{3:-4}}"

# 1) MP4 (H.264) — integer nearest upscale first, then encode.
ffmpeg -y -framerate "$FPS" -i "$SEQ_PATTERN" \\
  -vf "scale=iw*$SCALE:ih*$SCALE:flags=neighbor,format=yuv420p" \\
  -c:v libx264 -crf 16 -preset slow -movflags +faststart pixel-master.mp4

# 2) High-quality GIF via two-pass palette.
ffmpeg -y -framerate "$FPS" -i "$SEQ_PATTERN" \\
  -vf "scale=iw*$SCALE:ih*$SCALE:flags=neighbor,palettegen=stats_mode=diff" palette.png
ffmpeg -y -framerate "$FPS" -i "$SEQ_PATTERN" -i palette.png \\
  -lavfi "scale=iw*$SCALE:ih*$SCALE:flags=neighbor [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3" \\
  -loop 0 pixel-loop.gif

# 3) WebM (VP9) for web/social with alpha alternative: use -c:v libvpx-vp9 -pix_fmt yuva420p.
ffmpeg -y -framerate "$FPS" -i "$SEQ_PATTERN" \\
  -vf "scale=iw*$SCALE:ih*$SCALE:flags=neighbor" \\
  -c:v libvpx-vp9 -crf 24 -b:v 0 pixel-web.webm

# 4) Infinite ambient loop (3 repetitions of the source loop).
ffmpeg -y -stream_loop 2 -framerate "$FPS" -i "$SEQ_PATTERN" \\
  -vf "scale=iw*$SCALE:ih*$SCALE:flags=neighbor,format=yuv420p" \\
  -c:v libx264 -crf 16 pixel-ambient.mp4
"""
        path = str(outdir / "ffmpeg-pixel-pipeline.sh")
        L.write_text(sh, path, ctx)
        written.append(path)
    elif kind == "imagemagick":
        sh = f"""#!/usr/bin/env bash
# ImageMagick pixel-art workflow (generated by pixel-art-media script-author).
set -euo pipefail
IN="${{1:-input.png}}"

# Integer nearest upscale (never smooth pixel art).
magick "$IN" -filter point -resize 400% upscaled-x4.png

# Reduce to a 16-color palette without dithering.
magick "$IN" +dither -colors 16 PNG8:reduced-16.png

# Extract the used palette as text.
magick "$IN" -format %c histogram:info:- | sort -rn | head -32 > palette-report.txt

# Assemble a frame directory into a GIF (delay in 1/100s).
# magick -delay {max(2, round(100/fps))} -loop 0 frames/frame-*.png -layers Optimize animation.gif

# Contact sheet montage with labels.
# magick montage frames/frame-*.png -tile 6x -geometry +4+4 -background '#14141c' contact.png
"""
        path = str(outdir / "imagemagick-pixel-workflow.sh")
        L.write_text(sh, path, ctx)
        written.append(path)
    elif kind == "aseprite-lua":
        lua = f"""-- Aseprite export automation (generated by pixel-art-media script-author).
-- Run inside Aseprite: File > Scripts > Open Scripts Folder, copy this file, then run it.
-- Exports each tag as a horizontal strip + JSON (array, meta) with tags and slices.
local spr = app.sprite
if not spr then
  app.alert("Open a sprite first.")
  return
end
local base = app.fs.filePathAndTitle(spr.filename)
if base == "" then base = spr.filename end

app.command.ExportSpriteSheet({{
  ui = false,
  askOverwrite = false,
  type = SpriteSheetType.HORIZONTAL,
  columns = 0,
  rows = 0,
  bestFit = false,
  textureFilename = base .. "-sheet.png",
  dataFilename = base .. "-sheet.json",
  dataFormat = SpriteSheetDataFormat.JSON_ARRAY,
  borderPadding = 1,
  shapePadding = 1,
  innerPadding = 0,
  trimSprite = false,
  trim = false,
  listTags = true,
  listSlices = true,
  listLayers = false,
}})

-- Per-tag strips for engine import:
for i, tag in ipairs(spr.tags) do
  app.command.ExportSpriteSheet({{
    ui = false,
    askOverwrite = false,
    type = SpriteSheetType.HORIZONTAL,
    tag = tag.name,
    textureFilename = base .. "-" .. tag.name .. ".png",
    dataFilename = base .. "-" .. tag.name .. ".json",
    dataFormat = SpriteSheetDataFormat.JSON_ARRAY,
    borderPadding = 1,
    shapePadding = 1,
    trim = false,
    listTags = true,
  }})
end
app.alert("Exported " .. #spr.tags .. " tag strips + full sheet.")
"""
        path = str(outdir / "aseprite-export-tags.lua")
        L.write_text(lua, path, ctx)
        written.append(path)
    elif kind == "batch-job":
        job = {"defaults": {"json": True},
               "jobs": [
                   {"id": "reduce", "op": "palette-apply",
                    "args": {"input": "master.png", "output": "out/master-16.png",
                             "colors": 16, "dither": "none"}},
                   {"id": "sheet", "op": "sheet-pack",
                    "args": {"inputs": ["out/frames"], "output": "out/sheet.png",
                             "layout": "packed", "padding": 2, "extrude": 1,
                             "meta": "out/sheet.json", "meta_format": "json-hash"}},
                   {"id": "gif", "op": "anim-encode",
                    "args": {"inputs": ["out/frames"], "output": "out/preview.gif", "fps": 12, "scale": 4}},
                   {"id": "qa", "op": "validate",
                    "args": {"input": "out/sheet.png", "max_colors": 32, "require_alpha": True}}
               ]}
        path = str(outdir / "batch-job.json")
        L.write_json(job, path, ctx)
        written.append(path)
    else:
        raise L.ToolError("bad-arg", "kind must be ffmpeg|imagemagick|aseprite-lua|batch-job.")
    return {"kind": kind, "written": written,
            "note": "Scripts are authored for external tools; this tool never executes them."}


# --------------------------------------------------------------- register ---

def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    p = add("platform-export", "Export platform size sets (icons, emotes, wallpapers, store, ...)", cmd_platform_export)
    p.add_argument("input"); p.add_argument("outdir")
    p.add_argument("--preset", required=True)
    p.add_argument("--background", default="#00000000")
    p.add_argument("--outline-color", default="#1a1822")

    p = add("contact-sheet", "Labeled preview/catalog sheet from many images", cmd_contact_sheet)
    p.add_argument("inputs", nargs="+"); p.add_argument("--output", required=True)
    p.add_argument("--cell", type=int, default=64); p.add_argument("--cols", type=int, default=0)
    p.add_argument("--padding", type=int, default=6)
    p.add_argument("--background", default="#14141c")
    p.add_argument("--labels", action="store_true"); p.add_argument("--checker", action="store_true")
    p.add_argument("--title"); p.add_argument("--index")

    p = add("tilemap-export", "Export TMX/TSX/Tiled-JSON/CSV map data with validated gids", cmd_tilemap_export)
    p.add_argument("--map", required=True); p.add_argument("--tileset-image", required=True)
    p.add_argument("--tile-size", type=int, default=16)
    p.add_argument("--outdir", required=True); p.add_argument("--name", default="level")
    p.add_argument("--format", default="all", choices=["tmx", "tsx", "json", "csv", "all"])
    p.add_argument("--legend", help="ASCII map legend, e.g. 'G=0,D=1,W=2,.=-1'")

    p = add("engine-export", "Generate engine integration files and setup guides", cmd_engine_export)
    p.add_argument("target", choices=["godot", "unity", "web", "phaser", "pixi",
                                      "rpgmaker", "gamemaker", "unreal", "retro", "vtt"])
    p.add_argument("outdir")
    p.add_argument("--input"); p.add_argument("--atlas-json"); p.add_argument("--name")
    p.add_argument("--fps", type=int, default=12); p.add_argument("--ppu", type=int, default=16)
    p.add_argument("--system", default="gameboy", help="retro target system")
    p.add_argument("--border", default="#c8a24a", help="vtt token ring color")

    p = add("caption-render", "Burn pixel captions into animation + export SRT", cmd_caption_render)
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--cues", required=True, help=".srt or JSON cues [{start,end,text}]")
    p.add_argument("--srt-out")

    p = add("storyboard-generate", "Shot list -> storyboard board + markdown + animatic", cmd_storyboard_generate)
    p.add_argument("--output", required=True)
    p.add_argument("--input", help="shots.json [{title,description,duration,camera,image}]")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--panel-width", type=int, default=160); p.add_argument("--panel-height", type=int, default=96)
    p.add_argument("--fps", type=int, default=8); p.add_argument("--animatic")

    p = add("script-author", "Author FFmpeg/ImageMagick/Aseprite-Lua/batch scripts (not executed)", cmd_script_author)
    p.add_argument("kind", choices=["ffmpeg", "imagemagick", "aseprite-lua", "batch-job"])
    p.add_argument("outdir"); p.add_argument("--fps", type=int, default=12)
