#!/usr/bin/env python3
"""spriteforge engine: engine-specific import packs from one neutral sheet.

Each operation reads a spriteforge sheet JSON (``compose --metadata`` or
``atlas --metadata``) and writes the files an engine importer expects. No engine
runtime is invoked: these are import packs and plans that a human or automated
importer applies.

Operations
----------
  unity        pixels-per-unit, slicing rects, pivots, clip plan, atlas notes
  godot        SpriteFrames .tres resource with regions, offsets, loops
  unreal       Paper2D flipbook/pivot/collision plan
  gamemaker    sequence origins, image speed, collision masks, naming
  rpgmaker     RPG Maker grid plan (MV/MZ 48px, VX Ace 32px, XP 32px)
  web          Canvas/Phaser/PixiJS/Three atlas JSON variants
  mobile       atlas size, memory, format and draw-call plan
  retro        hardware-limit planner (palette/tile/sprite/scanline/memory)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_engine", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_engine"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image


def load_meta(args, root: Path) -> tuple[dict, Path]:
    if not args.metadata:
        raise SFError("MISSING_INPUT", "--metadata is required (produce it with sheet.py compose/atlas)")
    mp = Path(args.metadata) if Path(args.metadata).is_absolute() else root / args.metadata
    meta = core.load_json(mp)
    if not isinstance(meta, dict) or not isinstance(meta.get("frames"), list) or not meta["frames"]:
        raise SFError("BAD_METADATA", "metadata needs a non-empty 'frames' list")
    return meta, mp


def require_approval(args, token: str, reason: str) -> None:
    """Engine packs change what a build ships, so they are explicitly approved."""
    core.require_approval(args, token, reason)


def group_actions(meta: dict, fallback: str = "default") -> dict[str, list[dict]]:
    """Group frames into clips using meta['actions'] when present, else name prefixes."""
    frames = meta["frames"]
    groups: dict[str, list[dict]] = {}
    actions = meta.get("actions") or []
    if actions:
        for a in actions:
            subset = frames[a.get("from", 0): a.get("to", len(frames))]
            if subset:
                groups[a["name"]] = subset
        covered = sum(len(v) for v in groups.values())
        if covered < len(frames):
            rest = [f for f in frames if not any(f in v for v in groups.values())]
            groups.setdefault("extra", []).extend(rest)
        return groups
    for f in frames:
        stem = f["name"].rsplit("_", 2)[0] if "_" in f["name"] else fallback
        groups.setdefault(stem or fallback, []).append(f)
    return groups


def write_json_guarded(args, root: Path, rep: core.Reporter, payload, suffix: str) -> Path:
    out = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                            root, args.allow_outside_root)
    if out.suffix and out.suffix != ".json":
        out = out.with_suffix(out.suffix + suffix + ".json")
    elif not out.suffix:
        out = out.with_suffix(suffix + ".json")
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "engine-pack")
    return out


# --------------------------------------------------------------------------- #
# unity
# --------------------------------------------------------------------------- #

def op_unity(args, root: Path, rep: core.Reporter):
    """Unity import pack: PPU, slicing rects, pivots, clip plan and atlas notes."""
    require_approval(args, "engine-unity", "engine packs land in a real build, so confirm the target")
    meta, mp = load_meta(args, root)
    ppu = args.ppu
    if ppu <= 0:
        raise SFError("BAD_ARGUMENT", "--ppu must be positive")
    sheet = meta.get("sheet", {})
    cell = meta.get("cell", {})
    clips = []
    for name, frames in group_actions(meta).items():
        clips.append({
            "name": name,
            "frameCount": len(frames),
            "sampleRate": args.sample_rate,
            "loop": bool(args.loop),
            "frames": [f["name"] for f in frames],
            "spriteNames": [f"{meta.get('name', 'sheet')}_{f['name']}" for f in frames],
        })
    pack = {
        "engine": "unity",
        "texture": meta.get("image"),
        "textureType": "Sprite (2D and UI)",
        "spriteMode": "Multiple",
        "pixelsPerUnit": ppu,
        "filterMode": "Point (no filter)" if args.pixel_art else "Bilinear",
        "compression": args.compression,
        "maxTextureSize": args.max_texture,
        "sRGB": True,
        "alphaIsTransparency": True,
        "meshType": "Tight" if args.mesh_tight else "Full Rect",
        "generateMipMaps": False,
        "spriteRects": [
            {"name": f["name"], "x": f["frame"]["x"], "y": f["frame"]["y"],
             "width": f["frame"]["w"], "height": f["frame"]["h"],
             "pivotX": round(args.pivot_x, 4), "pivotY": round(args.pivot_y, 4)}
            for f in meta["frames"]
        ],
        "animationClips": clips,
        "worldUnits": {
            "spriteWidth": round(cell.get("width", 0) / ppu, 4),
            "spriteHeight": round(cell.get("height", 0) / ppu, 4),
            "sheetWidth": round(sheet.get("width", 0), 3),
            "sheetHeight": round(sheet.get("height", 0), 3),
        },
        "importChecklist": [
            "Import the sheet, set Sprite Mode = Multiple",
            f"Slice by Grid Size ({cell.get('width', '?')}x{cell.get('height', '?')}) with padding from meta.grid",
            f"Set Pixels Per Unit = {ppu}",
            "Set each sprite pivot to the shared pivot values in spriteRects",
            "Create Animation clips from animationClips; disable 'Loop Time' for hit/death states",
            "Use an Animator Controller driven by the state machine plan",
        ],
        "notes": "Rect y values are Unity bottom-left; use meta frames directly only if the importer expects top-left",
        "sourceMetadata": str(mp),
    }
    out = write_json_guarded(args, root, rep, pack, "-unity")
    if args.template and not args.dry_run:
        tp = core.guard_output(root / args.template, root, args.allow_outside_root)
        tp.write_text(_unity_importer_note(pack), encoding="utf-8")
        rep.add_output(tp, "importer-note")
    return {"summary": {"sprites": len(pack["spriteRects"]), "clips": len(clips), "ppu": ppu,
                        "output": str(out)}}


def _unity_importer_note(pack: dict) -> str:
    lines = ["# Unity import steps", "",
             "1. Drop the texture into `Assets/Art/` and select it.",
             "2. Inspector -> Sprite Mode: Multiple; Pixels Per Unit: "
             f"{pack['pixelsPerUnit']}; Filter Mode: {pack['filterMode']}.",
             "3. Sprite Editor -> Slice -> Grid By Cell Size, then Apply.",
             "4. Set pivots from `spriteRects` in the generated JSON (custom pivot, normalized).",
             "5. Create Animation clips and add frame sprites in order:", ""]
    for clip in pack["animationClips"]:
        lines.append(f"   - {clip['name']}: {clip['frameCount']} frames @ {clip['sampleRate']} fps, loop={clip['loop']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# godot
# --------------------------------------------------------------------------- #

def op_godot(args, root: Path, rep: core.Reporter):
    """Godot SpriteFrames resource with regions, offsets and loop settings."""
    require_approval(args, "engine-godot", "engine packs land in a real build, so confirm the target")
    meta, mp = load_meta(args, root)
    texture = args.texture or meta.get("image") or "sheet.png"
    groups = group_actions(meta)
    extra_ids = []
    for clip_name, frames in groups.items():
        for i, f in enumerate(frames):
            extra_ids.append((clip_name, i, f))
    # Godot's load_steps counts every resource in the file plus one:
    # one AtlasTexture sub_resource per frame + the single external texture + 1.
    load_steps = len(extra_ids) + 1 + 1
    lines = [
        f'[gd_resource type="SpriteFrames" load_steps={load_steps} format=3]',
        "",
        f'[ext_resource type="Texture2D" path="{args.texture_path}{texture}" id="1_texture"]',
        "",
    ]
    for idx, (clip_name, i, f) in enumerate(extra_ids, start=1):
        lines.append(f'[sub_resource type="AtlasTexture" id="atlas_{idx}"]')
        lines.append('atlas = ExtResource("1_texture")')
        lines.append(f"region = Rect2({f['frame']['x']}, {f['frame']['y']}, {f['frame']['w']}, {f['frame']['h']})")
        lines.append("")
    lines.append("[resource]")
    animations = []
    idx_by_clip: dict[str, list[tuple[int, dict]]] = {}
    for idx, (clip_name, i, f) in enumerate(extra_ids, start=1):
        idx_by_clip.setdefault(clip_name, []).append((idx, f))
    for clip_name, items in idx_by_clip.items():
        fps = args.fps
        # SpriteFrames durations are in seconds per frame; derive them from the
        # sheet's own per-frame duration so timing survives the round trip.
        frames = []
        for idx, f in items:
            ms = f.get("duration") or (1000.0 / fps if fps else 1000.0)
            frames.append({"duration": round(float(ms) / 1000.0, 4),
                           "texture": f'SubResource("atlas_{idx}")'})
        animation = {
            "frames": frames,
            "loop": bool(items and items[0][1].get("loop", True)),
            "name": f"{clip_name}",
            "speed": float(fps),
        }
        animations.append(animation)
    def fmt_anim(a: dict) -> str:
        frame_strs = [
            '{\n"duration": %.4f,\n"texture": %s\n}' % (fr["duration"], fr["texture"])
            for fr in a["frames"]
        ]
        loop = "true" if a["loop"] else "false"
        return ('{\n"frames": [' + ", ".join(frame_strs) + '], "loop": '
                + loop + f', "name": "{a["name"]}", "speed": {a["speed"]:.1f}}}')
    lines.append("animations = [" + ", ".join(fmt_anim(a) for a in animations) + "]")
    content = "\n".join(lines) + "\n"
    out = Path(args.output) if Path(args.output).is_absolute() else root / args.output
    if out.suffix != ".tres":
        out = out.with_suffix(".tres")
    out = core.guard_output(out, root, args.allow_outside_root)
    if not args.dry_run:
        out.write_text(content, encoding="utf-8")
        rep.add_output(out, "godot-resource")
    pack = {
        "engine": "godot",
        "resource": str(out),
        "texturePath": args.texture_path + texture,
        "animations": [{"name": a["name"], "frames": len(a["frames"]), "speed": a["speed"],
                        "loop": a["loop"]} for a in animations],
        "importSettings": {"filter": not args.pixel_art, "mipmaps": False,
                           "compressMode": "Lossless" if args.pixel_art else "VRAM Compressed"},
        "notes": ["Attach the SpriteFrames resource to an AnimatedSprite2D",
                  "Set texture import 'Filter' off for pixel art", "Offsets come from the shared pivot in metadata"],
        "sourceMetadata": str(mp),
    }
    write_json_guarded(args, root, rep, pack, "-godot")
    return {"summary": {"animations": len(animations), "frames": len(extra_ids), "output": str(out)}}


# --------------------------------------------------------------------------- #
# unreal
# --------------------------------------------------------------------------- #

def op_unreal(args, root: Path, rep: core.Reporter):
    """Paper2D flipbook, pivot and collision plan."""
    require_approval(args, "engine-unreal", "engine packs land in a real build, so confirm the target")
    meta, mp = load_meta(args, root)
    cell = meta.get("cell", {})
    groups = group_actions(meta)
    flipbooks = []
    for name, frames in groups.items():
        flipbooks.append({
            "flipbook": f"FB_{meta.get('name', 'sheet')}_{name}",
            "frames": [f"{meta.get('name', 'sheet')}_{f['name']}" for f in frames],
            "framesPerSecond": args.fps,
            "loop": bool(args.loop),
        })
    pack = {
        "engine": "unreal-paper2d",
        "texture": meta.get("image"),
        "spriteMode": "Multiple",
        "pixelsPerUnrealUnit": round(1 / args.ppu, 6),
        "pivot": {"x": round(args.pivot_x, 4), "y": round(args.pivot_y, 4),
                  "note": "Paper2D pivots are normalized with origin top-left"},
        "collision": {"geometry": "Box", "sizeX": cell.get("width", 0) * args.collision_scale,
                      "sizeY": cell.get("height", 0) * args.collision_scale,
                      "bodySetup": "UseSimpleAsComplex for 2D"},
        "flipbooks": flipbooks,
        "textureSettings": {"textureGroup": "2D Pixels", "filter": "Nearest" if args.pixel_art else "Bilinear",
                            "compression": "UserInterface2D (RGBA)", "mipGen": "NoMipmaps"},
        "importChecklist": [
            "Import the texture, set Texture Group = 2D Pixels, Compression = UserInterface2D",
            "Right-click -> Sprite Actions -> Apply Paper2D Texture Settings",
            "Create sprites from the sheet, set pivots from this pack",
            "Build Flipbooks from the flipbooks list, set FPS and looping",
            "Set collision: box from the collision block, simple-for-complex",
        ],
        "sourceMetadata": str(mp),
    }
    out = write_json_guarded(args, root, rep, pack, "-unreal")
    return {"summary": {"flipbooks": len(flipbooks), "frames": len(meta["frames"]), "output": str(out)}}


# --------------------------------------------------------------------------- #
# gamemaker
# --------------------------------------------------------------------------- #

def op_gamemaker(args, root: Path, rep: core.Reporter):
    """GameMaker sequence origins, image speed, collision masks and naming."""
    require_approval(args, "engine-gamemaker", "engine packs land in a real build, so confirm the target")
    meta, mp = load_meta(args, root)
    cell = meta.get("cell", {})
    groups = group_actions(meta)
    sequences = []
    for name, frames in groups.items():
        sequences.append({
            "sequence": f"seq_{meta.get('name', 'sheet')}_{name}",
            "spriteName": f"spr_{meta.get('name', 'sheet')}_{name}",
            "frameCount": len(frames),
            "imageSpeed": round(args.fps / 60, 4),
            "origin": {"x": int(cell.get("width", 0) * args.pivot_x),
                       "y": int(cell.get("height", 0) * (1 - args.pivot_y))},
            "mask": {"shape": args.mask_type, "width": cell.get("width", 0),
                     "height": cell.get("height", 0), "precise": args.mask_type == "precise"},
            "loops": bool(args.loop),
        })
    pack = {
        "engine": "gamemaker",
        "texture": meta.get("image"),
        "sequences": sequences,
        "naming": {"spritePrefix": "spr_", "sequencePrefix": "seq_", "frameSuffix": "_{index}"},
        "importChecklist": [
            "Import as a strip or separate frames, then create one sprite per sequence",
            "Set the origin to the values in sequences[].origin (GameMaker origin is bottom-left-ish)",
            "Set image_speed from imageSpeed (frames per game step at 60 steps/s)",
            "Set collision mask: precise for organic shapes, rectangle for platformers",
            "Pack the sprites into one texture page to keep draw calls low",
        ],
        "notes": "GameMaker origin is measured from top-left with y down in the IDE; values here are pre-converted",
        "sourceMetadata": str(mp),
    }
    out = write_json_guarded(args, root, rep, pack, "-gamemaker")
    return {"summary": {"sequences": len(sequences), "output": str(out)}}


# --------------------------------------------------------------------------- #
# rpgmaker
# --------------------------------------------------------------------------- #

RPG_PRESETS = {
    "mv": {"cell": 48, "face": 144, "icon": 32, "battlerMax": 576},
    "mz": {"cell": 48, "face": 144, "icon": 32, "battlerMax": 576},
    "vx-ace": {"cell": 32, "face": 96, "icon": 24, "battlerMax": 512},
    "xp": {"cell": 32, "face": 96, "icon": 24, "battlerMax": 512},
}


def op_rpgmaker(args, root: Path, rep: core.Reporter):
    """RPG Maker character/face/icon/tile/animation grid plan."""
    require_approval(args, "engine-rpgmaker", "engine packs land in a real build, so confirm the target")
    meta, mp = load_meta(args, root)
    preset = RPG_PRESETS[args.version]
    frame_count = len(meta["frames"])
    cell = preset["cell"]
    chars_per_sheet = 8 if args.version in {"mv", "mz"} else 4
    walk_frames = 3 if args.version in {"mv", "mz"} else 4
    frames_per_char = chars_per_sheet and (walk_frames * 4)
    payload = {
        "engine": f"rpgmaker-{args.version}",
        "grid": {"cell": cell, "sheet": "12 cells wide x 8 cells tall (MV/MZ)" if args.version in {"mv", "mz"}
                 else "4 chars x 4 directions x 4 frames (XP/VX Ace)"},
        "characters": {
            "chunksPerSheet": chars_per_sheet,
            "directions": ["down", "left", "right", "up"],
            "walkFrames": walk_frames,
            "framesPerCharacter": frames_per_char,
            "inputFrames": frame_count,
            "fits": frame_count % frames_per_char == 0 if frames_per_char else False,
        },
        "faces": {"cell": preset["face"], "grid": "4x2 faces per sheet", "maxFaces": 8},
        "icons": {"cell": preset["icon"], "grid": "16x16 icons per sheet"},
        "battlers": {"maxSide": preset["battlerMax"],
                     "note": "front-view battlers are single images; side-view uses 9-cell sheets in older versions"},
        "animations": {"cell": preset["cell"], "grid": "5 frames wide", "note": "Animation tab uses a fixed 5-column strip"},
        "importChecklist": [
            f"Character sheets must use {cell}px cells with {walk_frames} walk frames per direction",
            "Place sheets in img/characters, faces in img/faces, icons in img/system",
            "Use $ prefix for a single-character sheet and ! prefix to disable the 8px y-offset",
            "Verify the walk cycle starts on the centre frame",
        ],
        "sourceMetadata": str(mp),
        "sourceCell": meta.get("cell"),
    }
    out = write_json_guarded(args, root, rep, payload, "-rpgmaker")
    return {"summary": {"version": args.version, "cell": cell,
                        "fits": payload["characters"]["fits"], "output": str(out)}}


# --------------------------------------------------------------------------- #
# web
# --------------------------------------------------------------------------- #

def op_web(args, root: Path, rep: core.Reporter):
    """Canvas/Phaser/PixiJS/Three atlas JSON packs."""
    require_approval(args, "engine-web", "engine packs ship to runtime, so confirm the target")
    meta, mp = load_meta(args, root)
    frames = meta["frames"]
    runtime = args.runtime
    if runtime == "phaser":
        data = {"frames": [{"filename": f["name"], "frame": {"x": f["frame"]["x"], "y": f["frame"]["y"],
                                                            "w": f["frame"]["w"], "h": f["frame"]["h"]},
                            "rotated": False, "trimmed": False,
                            "spriteSourceSize": {"x": 0, "y": 0, "w": f["frame"]["w"], "h": f["frame"]["h"]},
                            "sourceSize": {"w": f["frame"]["w"], "h": f["frame"]["h"]}} for f in frames],
                "meta": {"app": "spriteforge", "version": core.TOOL_VERSION, "image": meta.get("image"),
                         "format": "RGBA8888", "size": meta.get("sheet"), "scale": "1",
                         "frameTags": [{"name": a.get("name", "clip"), "from": a.get("from", 0),
                                        "to": max(0, a.get("to", 1) - 1), "direction": "forward"}
                                       for a in (meta.get("actions") or [])]}}
    elif runtime == "pixi":
        data = {"frames": {f["name"]: {"frame": {"x": f["frame"]["x"], "y": f["frame"]["y"],
                                                 "w": f["frame"]["w"], "h": f["frame"]["h"]},
                                       "rotated": False, "trimmed": False,
                                       "spriteSourceSize": {"x": 0, "y": 0, "w": f["frame"]["w"], "h": f["frame"]["h"]},
                                       "sourceSize": {"w": f["frame"]["w"], "h": f["frame"]["h"]}} for f in frames},
                "meta": {"app": "spriteforge", "image": meta.get("image"), "size": meta.get("sheet"),
                         "scale": "1"}}
    elif runtime == "three":
        data = {"texture": meta.get("image"), "sheet": meta.get("sheet"),
                "frames": [{"name": f["name"], "uv": {
                    "u0": round(f["frame"]["x"] / max(1, meta["sheet"]["width"]), 6),
                    "v0": round(1 - (f["frame"]["y"] + f["frame"]["h"]) / max(1, meta["sheet"]["height"]), 6),
                    "u1": round((f["frame"]["x"] + f["frame"]["w"]) / max(1, meta["sheet"]["width"]), 6),
                    "v1": round(1 - f["frame"]["y"] / max(1, meta["sheet"]["height"]), 6),
                }} for f in frames]}
    else:  # canvas
        data = {"image": meta.get("image"), "sheet": meta.get("sheet"),
                "frames": [{"name": f["name"], "sx": f["frame"]["x"], "sy": f["frame"]["y"],
                            "sw": f["frame"]["w"], "sh": f["frame"]["h"]} for f in frames],
                "animations": meta.get("actions", [])}
    out = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                            root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(data, out)
        rep.add_output(out, "atlas-json")
        if args.loader_html:
            hp = core.guard_output(root / args.loader_html, root, args.allow_outside_root)
            hp.write_text(_web_loader(data, runtime), encoding="utf-8")
            rep.add_output(hp, "loader-html")
    return {"summary": {"runtime": runtime, "frames": len(frames), "output": str(out)}}


def _web_loader(data, runtime: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>spriteforge loader</title>
<style>body{{background:#16161a;color:#ddd;font:14px system-ui;display:flex;flex-direction:column;
align-items:center;gap:12px;padding:24px}}canvas{{image-rendering:pixelated;background:#26262c}}</style>
</head><body>
<h1>{runtime} sprite preview</h1>
<canvas id="c" width="256" height="256"></canvas>
<p>Serve this file next to the atlas image and JSON. Load path: <code>{data.get('image') or data.get('meta', {}).get('image')}</code></p>
<script>
const meta = {json.dumps(data)[:400000]};
const img = new Image();
img.onload = () => {{
  const c = document.getElementById('c'), ctx = c.getContext('2d');
  const frames = Array.isArray(meta.frames) ? meta.frames : Object.values(meta.frames);
  let i = 0;
  setInterval(() => {{
    const f = frames[i % frames.length];
    const r = f.frame || {{x: f.sx, y: f.sy, w: f.sw, h: f.sh}};
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(img, r.x, r.y, r.w, r.h, 32, 32, r.w, r.h);
    i++;
  }}, 120);
}};
img.src = {json.dumps(data.get('image') or data.get('meta', {}).get('image') or 'sheet.png')};
</script></body></html>
"""


# --------------------------------------------------------------------------- #
# mobile / retro planners
# --------------------------------------------------------------------------- #

def op_mobile(args, root: Path, rep: core.Reporter):
    """Mobile atlas size, memory, format, scale and draw-call plan."""
    require_approval(args, "engine-mobile", "engine packs land in a real build, so confirm the target")
    meta, mp = load_meta(args, root)
    w = meta.get("sheet", {}).get("width", 0)
    h = meta.get("sheet", {}).get("height", 0)
    bpp = {"rgba32": 4, "rgba16": 2, "etc2": 0.5, "astc-4x4": 0.5, "astc-6x6": 0.22}
    if args.format not in bpp:
        raise SFError("BAD_FORMAT", f"format must be one of {sorted(bpp)}")
    raw_mb = w * h * 4 / 1_048_576
    packed_mb = w * h * bpp[args.format] / 1_048_576
    scale_variants = {}
    for scale in args.scales:
        sw, sh = int(w * scale), int(h * scale)
        scale_variants[f"x{scale}"] = {"size": [sw, sh], "memory_mb": round(sw * sh * bpp[args.format] / 1_048_576, 3)}
    pack = {
        "engine": "mobile",
        "atlas": {"width": w, "height": h, "power_of_two": not (w & (w - 1)) and not (h & (h - 1)),
                  "max_size_ok": max(w, h) <= args.max_texture},
        "memory": {"raw_rgba_mb": round(raw_mb, 3), "packed_mb": round(packed_mb, 3),
                   "format": args.format, "budget_mb": args.budget_mb,
                   "fits_budget": packed_mb <= args.budget_mb},
        "scale_variants": scale_variants,
        "draw_calls": {"sprites": len(meta["frames"]),
                       "batched_draw_calls": 1,
                       "note": "one atlas = one draw call when the material is shared"},
        "recommendations": [
            f"Use {args.format} for shipping builds; keep RGBA32 only for UI atlases",
            "Split atlases above the device texture limit rather than shrinking sprites",
            "Drop mipmaps unless sprites are scaled in 3D",
            "Test on the smallest target device for decompression spikes",
        ],
        "sourceMetadata": str(mp),
    }
    if not pack["memory"]["fits_budget"]:
        rep.warn("OVER_BUDGET", f"packed atlas {packed_mb:.2f}MB exceeds budget {args.budget_mb}MB")
    out = write_json_guarded(args, root, rep, pack, "-mobile")
    return {"summary": {"packed_mb": pack["memory"]["packed_mb"], "fits_budget": pack["memory"]["fits_budget"],
                        "output": str(out)}}


RETRO_PRESETS = {
    "nes": {"colours": 25, "palette_per_sprite": 4, "tile": "8x8", "sprites_per_scanline": 8,
            "total_sprites": 64, "vram_kb": 6},
    "snes": {"colours": 256, "palette_per_sprite": 16, "tile": "8x8", "sprites_per_scanline": 32,
             "total_sprites": 128, "vram_kb": 64},
    "gameboy": {"colours": 4, "palette_per_sprite": 4, "tile": "8x8", "sprites_per_scanline": 10,
                "total_sprites": 40, "vram_kb": 8},
    "gba": {"colours": 512, "palette_per_sprite": 16, "tile": "8x8", "sprites_per_scanline": 128,
            "total_sprites": 128, "vram_kb": 96},
    "genesis": {"colours": 61, "palette_per_sprite": 16, "tile": "8x8", "sprites_per_scanline": 20,
                "total_sprites": 80, "vram_kb": 64},
}


def op_retro(args, root: Path, rep: core.Reporter):
    """Hardware-limit planner for retro targets."""
    require_approval(args, "engine-retro", "engine packs land in a real build, so confirm the target")
    preset = RETRO_PRESETS[args.system]
    meta, mp = load_meta(args, root)
    frames = meta["frames"]
    cell = meta.get("cell", {})
    colours = args.colours
    problems = []
    if colours > preset["colours"]:
        problems.append(f"{colours} colours exceeds the {preset['colours']} colour limit")
    tw, th = (int(v) for v in preset["tile"].split("x"))
    if cell.get("width") and cell["width"] % tw:
        problems.append(f"cell width {cell['width']} is not a multiple of the {tw}px tile")
    if cell.get("height") and cell["height"] % th:
        problems.append(f"cell height {cell['height']} is not a multiple of the {th}px tile")
    body = int(cell.get("height", 0) / th) * int(cell.get("width", 0) / tw) if cell.get("width") else 0
    scanline_use = args.on_screen_sprites * max(1, int(cell.get("width", tw) / tw))
    if scanline_use > preset["sprites_per_scanline"]:
        problems.append(f"{scanline_use} hardware sprite tiles per scanline exceeds "
                        f"{preset['sprites_per_scanline']}")
    if args.on_screen_sprites > preset["total_sprites"]:
        problems.append(f"{args.on_screen_sprites} sprites on screen exceeds {preset['total_sprites']}")
    vram = (meta.get("sheet", {}).get("width", 0) * meta.get("sheet", {}).get("height", 0)) / 1024
    if vram > preset["vram_kb"]:
        problems.append(f"sheet uses ~{vram:.0f}KiB, above the {preset['vram_kb']}KiB VRAM budget")
    for p in problems:
        rep.warn("LIMIT_EXCEEDED", p)
    pack = {
        "engine": f"retro-{args.system}",
        "hardware": preset,
        "asset": {"frames": len(frames), "cell": cell,
                  "tiles_per_sprite": body, "colours": colours,
                  "sheet_kib": round(vram, 1)},
        "budget": {"on_screen_sprites": args.on_screen_sprites,
                   "scanline_tiles_used": scanline_use,
                   "scanline_tiles_limit": preset["sprites_per_scanline"]},
        "problems": problems,
        "recommendations": [
            f"Quantise to <= {preset['colours']} colours and use {preset['palette_per_sprite']}-colour sub-palettes",
            f"Keep every sprite on a {preset['tile']} tile grid",
            "Prefer sprite reuse and palette swaps over bigger sheets",
        ],
        "sourceMetadata": str(mp),
    }
    out = write_json_guarded(args, root, rep, pack, "-retro")
    return {"summary": {"system": args.system, "problems": len(problems), "output": str(out)}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _common(p, out_default="engine-pack.json"):
    p.add_argument("--metadata", required=True, help="spriteforge sheet JSON")
    p.add_argument("--output", "-o", default=out_default)


def _p_unity(p):
    _common(p)
    p.add_argument("--ppu", type=int, default=100, help="pixels per unit")
    p.add_argument("--pixel-art", action="store_true", default=True)
    p.add_argument("--compression", default="Compressed", choices=["Compressed", "Uncompressed"])
    p.add_argument("--max-texture", type=int, default=2048)
    p.add_argument("--mesh-tight", action="store_true")
    p.add_argument("--pivot-x", type=float, default=0.5)
    p.add_argument("--pivot-y", type=float, default=0.0)
    p.add_argument("--sample-rate", type=float, default=12.0)
    p.add_argument("--loop", action="store_true", default=True)
    p.add_argument("--template", help="also write an importer note Markdown file")


def _p_godot(p):
    _common(p, "sprite_frames.tres")
    p.add_argument("--texture", help="atlas image filename")
    p.add_argument("--texture-path", default="res://art/")
    p.add_argument("--fps", type=float, default=12.0)
    p.add_argument("--pixel-art", action="store_true", default=True)


def _p_unreal(p):
    _common(p, "paper2d-plan.json")
    p.add_argument("--ppu", type=int, default=100)
    p.add_argument("--pixel-art", action="store_true", default=True)
    p.add_argument("--pivot-x", type=float, default=0.5)
    p.add_argument("--pivot-y", type=float, default=1.0)
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--loop", action="store_true", default=True)
    p.add_argument("--collision-scale", type=float, default=0.8)


def _p_gm(p):
    _common(p, "gamemaker-plan.json")
    p.add_argument("--fps", type=float, default=12)
    p.add_argument("--pivot-x", type=float, default=0.5)
    p.add_argument("--pivot-y", type=float, default=0.0)
    p.add_argument("--mask-type", default="rectangle", choices=["rectangle", "ellipse", "precise"])
    p.add_argument("--loop", action="store_true", default=True)


def _p_rpg(p):
    _common(p, "rpgmaker-plan.json")
    p.add_argument("--version", default="mz", choices=sorted(RPG_PRESETS))


def _p_web(p):
    _common(p, "atlas.json")
    p.add_argument("--runtime", default="canvas", choices=["canvas", "phaser", "pixi", "three"])
    p.add_argument("--loader-html", help="also write a standalone preview loader")


def _p_mobile(p):
    _common(p, "mobile-plan.json")
    p.add_argument("--format", default="astc-4x4", choices=["rgba32", "rgba16", "etc2", "astc-4x4", "astc-6x6"])
    p.add_argument("--budget-mb", type=float, default=16.0)
    p.add_argument("--max-texture", type=int, default=2048)
    p.add_argument("--scales", type=float, nargs="+", default=[1.0, 0.5])


def _p_retro(p):
    _common(p, "retro-plan.json")
    p.add_argument("--system", default="snes", choices=sorted(RETRO_PRESETS))
    p.add_argument("--colours", type=int, default=16)
    p.add_argument("--on-screen-sprites", type=int, default=16)


OPS = {
    "unity": (_p_unity, op_unity),
    "godot": (_p_godot, op_godot),
    "unreal": (_p_unreal, op_unreal),
    "gamemaker": (_p_gm, op_gamemaker),
    "rpgmaker": (_p_rpg, op_rpgmaker),
    "web": (_p_web, op_web),
    "mobile": (_p_mobile, op_mobile),
    "retro": (_p_retro, op_retro),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "engine",
        "spriteforge engine: import packs and plans for Unity, Godot, Unreal, GameMaker, RPG Maker, web, mobile and retro targets",
        OPS,
    ))
