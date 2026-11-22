"""pixel-art-media: production planning, guidance, and capability registry.

Deterministic brief/constraint/scope/art-bible/rubric/practice generators,
catalog-driven guidance for every one of the 318 catalog skills, and the
capability listing that maps catalog categories to implemented operations.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

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


def catalog():
    return I().load_asset_json("asset-catalog.json")


def guides():
    return I().load_asset_json("asset-guides.json")


def presets():
    return I().load_asset_json("asset-presets.json")


PLATFORM_HINTS = {
    "game-pc": {"canvas": "320x180 or 480x270 viewport", "scale": "integer x4..x8", "colors": "16-64 per asset"},
    "game-mobile": {"canvas": "320x180 viewport, safe areas", "scale": "integer fit + letterbox", "colors": "16-32 per asset"},
    "game-console": {"canvas": "320x180 / 640x360", "scale": "integer to 1080p/4K", "colors": "16-64"},
    "web": {"canvas": "any; CSS image-rendering: pixelated", "scale": "integer preferred", "colors": "PNG8 when possible"},
    "social": {"canvas": "1080x1080 / 1080x1920", "scale": "export presets", "colors": "unrestricted"},
    "streaming": {"canvas": "emotes 28/56/112", "scale": "platform presets", "colors": "readable at 28px"},
    "retro-handheld": {"canvas": "160x144 (GB-class)", "scale": "integer", "colors": "4 per layer"},
    "print": {"canvas": "native grid, then nearest x8+", "scale": "integer only", "colors": "palette-limited"},
}


def doc_paths(output):
    """Planning docs emit JSON + Markdown twins; accept either extension as the anchor."""
    p = Path(output)
    if p.suffix.lower() == ".md":
        return str(p.with_suffix(".json")), str(p)
    return str(p), str(p.with_suffix(".md"))


def cmd_brief(a, ctx):
    L = I()
    platform = a.platform if a.platform in PLATFORM_HINTS else "game-pc"
    hints = PLATFORM_HINTS[platform]
    idea = a.idea.strip()
    if not idea:
        raise L.ToolError("bad-arg", "--idea must describe the asset or scene.")
    ip_flags = []
    for kw in ("mario", "pokemon", "pikachu", "zelda", "sonic", "disney", "marvel", "star wars",
               "minecraft", "logo of", "in the style of"):
        if kw in idea.lower():
            ip_flags.append(kw)
    brief = {
        "title": a.title or idea[:60],
        "idea": idea,
        "genre": a.genre, "audience": a.audience, "platform": platform,
        "canvas_recommendation": a.size or hints["canvas"],
        "scaling_policy": hints["scale"],
        "palette_budget": a.palette_budget or hints["colors"],
        "tile_grid": a.tile_grid,
        "animation_needs": a.animation or ["idle"],
        "frame_budget": "8-12 fps; 4-8 frames per loop; keys first, then breakdowns",
        "deliverables": ["editable master (PNG layers or editor project)",
                         "native-resolution PNG exports with transparency",
                         "sprite sheet + JSON metadata (if animated)",
                         "palette file (.gpl/.json)", "preview GIF/APNG at display scale",
                         "license/attribution record"],
        "acceptance_criteria": [
            "Reads at actual display size on target background",
            "Palette within budget; binary alpha unless deliberately soft",
            "Integer-scaled previews only; no resampling damage",
            "Loop closure verified; no flash-risk brightness swings",
            "Original design; IP review passed",
        ],
        "risks": ([f"Possible protected reference detected: '{k}' — redesign toward an original alternative."
                   for k in ip_flags] or ["No obvious IP keywords detected; still review references manually."]),
        "milestones": ["1. Brief + constraints approved", "2. Silhouette/thumbnail pass",
                       "3. Palette + linework pass", "4. Shading + polish",
                       "5. Animation (if any)", "6. Export + QA + handoff"],
    }
    json_path, md_path = doc_paths(a.output)
    L.write_json(brief, json_path, ctx)
    md = f"# Production brief: {brief['title']}\n\n"
    md += f"**Idea:** {idea}\n\n"
    md += f"- Genre: {a.genre} | Audience: {a.audience} | Platform: {platform}\n"
    md += f"- Canvas: {brief['canvas_recommendation']} | Scaling: {brief['scaling_policy']}\n"
    md += f"- Palette budget: {brief['palette_budget']} | Tile grid: {a.tile_grid}px\n"
    md += f"- Animation: {', '.join(brief['animation_needs'])} ({brief['frame_budget']})\n\n"
    md += "## Deliverables\n" + "".join(f"- {d}\n" for d in brief["deliverables"])
    md += "\n## Acceptance criteria\n" + "".join(f"- {c}\n" for c in brief["acceptance_criteria"])
    md += "\n## Risks\n" + "".join(f"- {r}\n" for r in brief["risks"])
    md += "\n## Milestones\n" + "".join(f"{m}\n" for m in brief["milestones"])
    L.write_text(md, md_path, ctx)
    return {"output": json_path, "markdown": md_path, "ip_flags": ip_flags, "brief": brief}


def cmd_constraints(a, ctx):
    L = I()
    platform = a.platform if a.platform in PLATFORM_HINTS else "game-pc"
    hints = PLATFORM_HINTS[platform]
    plan = {
        "platform": platform,
        "viewport": a.viewport or hints["canvas"],
        "tile_grid": a.tile_grid,
        "color_limit_per_asset": a.colors,
        "transparency": "binary alpha (1-bit) unless a platform requires soft alpha",
        "max_asset_px": f"{a.tile_grid * 4}x{a.tile_grid * 4} for props; characters up to 3x tile height",
        "animation_budget": {"fps": a.fps, "frames_per_loop": "4-8", "max_simultaneous_frames": 256},
        "file_size_budget_kb": {"icon": 8, "sprite": 32, "tileset": 256, "background": 512, "gif_preview": 2048},
        "scaling": hints["scale"],
        "hardware_notes": presets()["retro"].get(a.system, {}).get("notes", "No hardware constraint selected."),
        "validation_commands": [
            "validate <file> --max-colors N --binary-alpha --max-bytes N",
            "validate <sheet.json> --sheet <sheet.png>",
            "seam-check <tile.png> --preview tiled.png",
        ],
    }
    L.write_json(plan, a.output, ctx)
    return {"output": a.output, "plan": plan}


def cmd_scope(a, ctx):
    L = I()
    items = {}
    if a.items:
        try:
            for pair in a.items.split(","):
                k, v = pair.split(":")
                items[k.strip()] = int(v)
        except ValueError:
            raise L.ToolError("bad-arg", f"--items must be 'category:count,...', got '{a.items}'.",
                              "Example: --items 'characters:3,props:10,tiles:20'")
        if not items:
            raise L.ToolError("bad-arg", "--items parsed to zero entries.")
    elif a.spec:
        items = L.read_json(a.spec)
    else:
        raise L.ToolError("bad-arg", "Provide --items 'characters:3,props:10,...' or --spec file.json")
    effort = {"characters": 6, "props": 2, "tiles": 0.5, "icons": 1, "animations": 3,
              "backgrounds": 8, "ui": 2, "effects": 2, "portraits": 4, "variants": 0.5}
    rows = []
    total = 0.0
    for k, n in items.items():
        unit = effort.get(k, 2)
        hours = n * unit
        total += hours
        rows.append({"category": k, "count": n, "hours_per_unit": unit, "estimated_hours": hours,
                     "priority": "high" if k in ("characters", "tiles", "ui") else "medium"})
    plan = {"items": rows, "estimated_hours": total,
            "buffer_30pct": round(total * 0.3, 1),
            "note": "Heuristic planning estimates for pixel-art production; adjust to team velocity.",
            "suggested_order": ["tileset + palette foundation", "main character + idle/walk",
                                "props/icons", "enemies/NPC variants", "backgrounds/parallax",
                                "UI/HUD", "effects + polish", "export/QA/handoff"]}
    json_path, md_path = doc_paths(a.output)
    L.write_json(plan, json_path, ctx)
    md = "# Asset scope estimate\n\n| Category | Count | h/unit | Hours | Priority |\n|---|---|---|---|---|\n"
    for r in rows:
        md += f"| {r['category']} | {r['count']} | {r['hours_per_unit']} | {r['estimated_hours']} | {r['priority']} |\n"
    md += f"\n**Total: {total}h (+30% buffer = {plan['buffer_30pct']}h)**\n\n"
    md += "Suggested order:\n" + "".join(f"{i+1}. {s}\n" for i, s in enumerate(plan["suggested_order"]))
    L.write_text(md, md_path, ctx)
    return {"output": json_path, "markdown": md_path, "estimated_hours": total}


def cmd_art_bible(a, ctx):
    L = I()
    pal = None
    if a.palette:
        pal = I().sibling("palettes").read_palette_any(a.palette)
    bible = f"""# Art bible: {a.title}

## Resolution & scale
- Native canvas: {a.canvas}; tile grid: {a.tile}px; characters ~{a.tile * 2}px tall.
- Integer nearest-neighbor scaling only; validate at actual display size.
- Pixel density is uniform: never mix 1px and 2px "fat pixel" detail in one asset family.

## Palette
- Budget: {a.colors} colors per asset family; shared ramps across assets.
- Hue-shift shadows toward cool, lights toward warm; avoid pure black/white except accents.
{('- Palette file: ' + a.palette + f' ({len(pal)} colors)') if pal else '- Generate a palette: palette-generate out.json --mood forest --ramps 3'}

## Linework & clusters
- Outlines: {"selective dark outline (not pure black)" if a.style != "lineless" else "lineless; use value contrast for edges"}.
- Clean contour rhythm (1-2-3 steps, avoid 1-1-1 jaggies); no orphan pixels.
- Clusters before detail; negative space must read at play size.

## Shading & light
- Single key light direction: top-left unless a scene requires otherwise.
- 3-4 value steps per material; dither only for gradients/retro effect, never to hide banding.

## Animation
- {a.fps} fps base; 4-8 frame loops; strong keys, then breakdowns.
- Idle: 1-2px motion; walk: contact/down/pass/up; loops must close (validate loop-closure).
- Secondary motion (hair/cloth) follows 1-2 frames behind the body.

## Export & naming
- Names: `family-asset-state-direction-frame.png` (lowercase-hyphen, zero-padded frames).
- Masters: layered editor file + native PNG; derivatives: sheets + JSON metadata + palette.
- GIF/APNG previews at integer display scale; engines import native resolution.

## QA gates
- validate (colors, alpha, size, naming) → seam-check (tiles) → compare (regressions)
  → reopen-test (final artifacts) → audit (provenance + secrets).
"""
    L.write_text(bible, a.output, ctx)
    return {"output": a.output, "title": a.title, "palette_colors": len(pal) if pal else None}


RUBRIC_CRITERIA = [
    ("Readability", "Silhouette, pose, and state read at actual display size", 20),
    ("Craft", "Cluster discipline, contour rhythm, no orphans/jaggies/banding", 20),
    ("Palette & light", "Coherent ramps, hue shifting, consistent light direction", 15),
    ("Consistency", "Scale, perspective, outline weight, naming across the set", 15),
    ("Animation", "Timing, spacing, loop closure, secondary motion (if animated)", 10),
    ("Technical validity", "Grid integrity, alpha, formats, metadata, budgets", 10),
    ("Originality & delivery", "Original design, licenses recorded, complete handoff", 10),
]


def cmd_rubric(a, ctx):
    L = I()
    rows = [{"criterion": c, "description": d, "weight": w} for c, d, w in RUBRIC_CRITERIA]
    json_path, md_path = doc_paths(a.output)
    L.write_json({"rubric": rows, "scale": "0-5 per criterion, weighted"}, json_path, ctx)
    md = f"# Pixel art review rubric: {a.title}\n\nScore each 0-5, multiply by weight.\n\n"
    md += "| Criterion | What to look for | Weight |\n|---|---|---|\n"
    for r in rows:
        md += f"| {r['criterion']} | {r['description']} | {r['weight']}% |\n"
    md += "\n0 = absent/broken, 1 = major issues, 2 = weak, 3 = acceptable, 4 = strong, 5 = exemplary.\n"
    L.write_text(md, md_path, ctx)
    return {"output": json_path, "markdown": md_path, "criteria": len(rows)}


PRACTICE_POOL = [
    ("Silhouette study", "Draw 6 readable silhouettes of {theme} at 16x16 using only 2 values.", "silhouette"),
    ("Cluster discipline", "Render a {theme} prop at 24x24 with <=8 colors and zero orphan pixels.", "cleanup"),
    ("Palette ramps", "Build a 5-step hue-shifted ramp for {theme} and shade a sphere/cube/cylinder.", "palette-generate"),
    ("Material study", "Render {theme} in metal, wood, and cloth using the same ramp structure.", "palette-analyze"),
    ("Tile seamless", "Create a seamless 16x16 {theme} ground tile; verify with seam-check.", "seam-check"),
    ("Idle loop", "Animate a 4-frame idle of {theme}; loop closure must pass validate.", "motion-generate"),
    ("Walk cycle", "Animate a 6-frame walk of {theme} with contact/down/pass/up keys.", "anim-encode"),
    ("Icon set", "Design 4 consistent 16x16 icons for {theme} with shared framing and light.", "icon-generate"),
    ("Autotile", "Draw a 16-mask {theme} terrain set and preview a map.", "tileset-generate"),
    ("Restriction drill", "Redraw {theme} in exactly 4 colors (handheld-style).", "palette-apply"),
    ("Dither gradient", "Render a sky gradient for {theme} with ordered dithering only.", "dither"),
    ("Turnaround", "Draw {theme} front/side/back at consistent proportions.", "transform"),
]
THEMES = ["a forest shrine", "a desert outpost", "a cozy kitchen", "a space dock", "a haunted well",
          "a tide pool", "a mountain pass", "a night market", "a crystal cave", "a clocktower"]


def cmd_practice(a, ctx):
    L = I()
    rng = random.Random(a.seed)
    picks = rng.sample(PRACTICE_POOL, min(a.count, len(PRACTICE_POOL)))
    exercises = []
    for i, (name, tmpl, cmd) in enumerate(picks):
        theme = rng.choice(THEMES)
        exercises.append({"n": i + 1, "name": name, "task": tmpl.format(theme=theme),
                          "supporting_command": cmd,
                          "time_box_minutes": rng.choice([20, 30, 45, 60]),
                          "measurable_goal": "Passes the supporting command's checks + reads at x1 display size"})
    json_path, md_path = doc_paths(a.output)
    L.write_json({"seed": a.seed, "exercises": exercises}, json_path, ctx)
    md = f"# Practice session (seed {a.seed})\n\n"
    for e in exercises:
        md += f"## {e['n']}. {e['name']} ({e['time_box_minutes']} min)\n{e['task']}\n"
        md += f"- Goal: {e['measurable_goal']}\n- Supporting tool: `{e['supporting_command']}`\n\n"
    L.write_text(md, md_path, ctx)
    return {"output": json_path, "markdown": md_path, "exercises": len(exercises), "seed": a.seed}


def find_skill(query):
    cat = catalog()
    q = query.strip().lower()
    for c in cat["categories"]:
        if str(c["id"]) == q or c["name"].lower() == q:
            return ("category", c)
    for c in cat["categories"]:
        for s in c["skills"]:
            if s["name"].lower() == q:
                return ("skill", (c, s))
    matches = [(c, s) for c in cat["categories"] for s in c["skills"] if q in s["name"].lower()]
    if len(matches) == 1:
        return ("skill", matches[0])
    if len(matches) > 1:
        return ("ambiguous", [s["name"] for _, s in matches][:12])
    return (None, None)


def cmd_guide(a, ctx):
    L = I()
    g = guides()
    cat_data = catalog()
    if a.skill:
        kind, hit = find_skill(a.skill)
        if kind is None:
            raise L.ToolError("skill-not-found", f"No catalog skill/category matches '{a.skill}'.",
                              "Run: capabilities --catalog to list categories, or capabilities --category N.")
        if kind == "ambiguous":
            raise L.ToolError("ambiguous-skill", f"Multiple matches: {hit}", "Use the exact skill name.")
        if kind == "category":
            c = hit
            skills = c["skills"]
            header = f"# Guide: {c['name']} (category {c['id']})\n\n"
        else:
            c, s = hit
            skills = [s]
            header = f"# Guide: {s['name']}\n\n{s['description']}\n\nCategory: {c['name']}\n\n"
    elif a.category:
        c = next((c for c in cat_data["categories"] if c["id"] == a.category), None)
        if c is None:
            raise L.ToolError("bad-arg", f"Category {a.category} not in catalog (1..29).")
        skills = c["skills"]
        header = f"# Guide: {c['name']} (category {c['id']})\n\n"
    else:
        raise L.ToolError("bad-arg", "Provide --skill NAME or --category N.")
    guide = g["categories"].get(str(c["id"]), {})
    md = header
    md += "## Workflow\n\n" + "".join(f"{i+1}. {step}\n" for i, step in enumerate(guide.get("workflow", [])))
    md += "\n## Checklist\n\n" + "".join(f"- [ ] {item}\n" for item in guide.get("checklist", []))
    md += "\n## Pitfalls\n\n" + "".join(f"- {p}\n" for p in guide.get("pitfalls", []))
    md += "\n## Implemented operations for this area\n\n"
    seen = set()
    for s in skills:
        ops = s.get("ops", [])
        mode = s.get("mode", "guide")
        if ops:
            line = f"- **{s['name']}** ({mode}): `{'`, `'.join(ops)}`\n"
        else:
            line = f"- **{s['name']}** (guidance): apply the workflow above; no direct automation.\n"
        if line not in seen:
            md += line
            seen.add(line)
    md += ("\n## Example commands\n\n"
           "Prefix every command below with the entry point: "
           "`python3 pixel-art-media/tools-pixel-media.py <command> ...`\n\n```bash\n"
           + "\n".join(guide.get("commands", [])) + "\n```\n")
    md += ("\n> Modes: **command** = direct automated operation; **assist** = the tool produces "
           "measurements/artifacts that support the skill (artistic judgment stays with you); "
           "**guide** = structured production guidance.\n")
    L.write_text(md, a.output, ctx)
    return {"output": a.output, "skills_covered": len(skills), "category": c["id"]}


def cmd_capabilities(a, ctx):
    L = I()
    cat_data = catalog()
    if a.catalog or a.category:
        cats = cat_data["categories"]
        if a.category:
            cats = [c for c in cats if c["id"] == a.category]
            if not cats:
                raise L.ToolError("bad-arg", "Category must be 1..29.")
        out = {"title": cat_data["title"], "categories": []}
        for c in cats:
            entry = {"id": c["id"], "name": c["name"], "skills": []}
            for s in c["skills"]:
                entry["skills"].append({"n": s["n"], "name": s["name"],
                                        "description": s["description"] if a.verbose else None,
                                        "mode": s.get("mode", "guide"), "ops": s.get("ops", [])})
            out["categories"].append(entry)
        return out
    commands = ctx.commands if hasattr(ctx, "commands") else []
    by_module = {}
    for name, help_, module in commands:
        by_module.setdefault(module, []).append({"command": name, "summary": help_})
    modes = {"command": 0, "assist": 0, "guide": 0}
    for c in cat_data["categories"]:
        for s in c["skills"]:
            modes[s.get("mode", "guide")] = modes.get(s.get("mode", "guide"), 0) + 1
    return {"tool": "pixel-art-media", "version": "1.0.0",
            "commands": [{"command": n, "summary": h, "module": m} for n, h, m in commands],
            "modules": by_module,
            "catalog_coverage": {"categories": len(cat_data["categories"]),
                                 "skills": sum(len(c["skills"]) for c in cat_data["categories"]),
                                 "modes": modes},
            "limitations": [
                "No network access, no AI image generation, no editor/GUI automation execution.",
                "Video codecs (MP4/WebM) are authored as FFmpeg scripts, not encoded here.",
                "Native .aseprite/.psd/.kra project writing is not implemented; Aseprite-compatible JSON + sheets are.",
                "Artistic skills (pose edits, inpainting, hand lettering) are assist/guide mode, not automated drawing.",
            ]}


def register(sub, common):
    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(func=fn)
        return p

    p = add("brief", "Turn an idea into a precise production brief (JSON + Markdown)", cmd_brief)
    p.add_argument("output"); p.add_argument("--idea", required=True)
    p.add_argument("--title"); p.add_argument("--genre", default="fantasy")
    p.add_argument("--audience", default="general"); p.add_argument("--platform", default="game-pc")
    p.add_argument("--size"); p.add_argument("--palette-budget"); p.add_argument("--tile-grid", type=int, default=16)
    p.add_argument("--animation", action="append")

    p = add("constraints", "Plan canvas/palette/frame/file-size constraints for a platform", cmd_constraints)
    p.add_argument("output"); p.add_argument("--platform", default="game-pc")
    p.add_argument("--viewport"); p.add_argument("--tile-grid", type=int, default=16)
    p.add_argument("--colors", type=int, default=16); p.add_argument("--fps", type=int, default=12)
    p.add_argument("--system", default="", help="Optional retro system key (gameboy, nes, ...)")

    p = add("scope", "Estimate asset scope and effort from an item list", cmd_scope)
    p.add_argument("output"); p.add_argument("--items", help="characters:3,props:10,tiles:40,...")
    p.add_argument("--spec", help="JSON {category:count}")

    p = add("art-bible", "Generate a project art bible from palette + style parameters", cmd_art_bible)
    p.add_argument("output"); p.add_argument("--title", default="Untitled Project")
    p.add_argument("--palette"); p.add_argument("--canvas", default="320x180")
    p.add_argument("--tile", type=int, default=16); p.add_argument("--colors", type=int, default=16)
    p.add_argument("--fps", type=int, default=12); p.add_argument("--style", default="outlined",
                   choices=["outlined", "lineless", "selective-outline"])

    p = add("rubric", "Generate a weighted pixel-art review rubric", cmd_rubric)
    p.add_argument("output"); p.add_argument("--title", default="Asset Review")

    p = add("practice", "Generate seeded practice exercises with measurable goals", cmd_practice)
    p.add_argument("output"); p.add_argument("--count", type=int, default=5)
    p.add_argument("--seed", type=int, default=7)

    p = add("guide", "Catalog-driven production guide for any skill or category", cmd_guide)
    p.add_argument("output"); p.add_argument("--skill", help="Exact or partial catalog skill name")
    p.add_argument("--category", type=int)

    p = add("capabilities", "List commands, modules, and full catalog coverage", cmd_capabilities)
    p.add_argument("--catalog", action="store_true", help="List the 29-category / 318-skill coverage map")
    p.add_argument("--category", type=int)
    p.add_argument("--verbose", action="store_true", help="Include catalog descriptions")
