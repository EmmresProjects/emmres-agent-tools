"""imagekit plan: brief interpretation, prompt architecture, specs, composition,
art direction, style guides, reference analysis and palette development.

Every handler returns {"data": {...}, "outputs": [Path, ...], "warnings": [...]}.
Planning handlers write real structured artifacts (JSON + Markdown + SVG) and never
invent customer-facing copy: unknown values become explicit open questions.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import imagekit_core as core

COLOR_NAMES = {
    "black": "#111111", "white": "#f7f7f5", "grey": "#8a8a8a", "gray": "#8a8a8a",
    "red": "#d64541", "orange": "#e08a3c", "yellow": "#e8c547", "green": "#4f8a4a",
    "teal": "#2f8f8a", "blue": "#3a6ea5", "navy": "#1f3d5c", "purple": "#6b4fa0",
    "pink": "#e08aa0", "brown": "#7a5236", "beige": "#d9c8a9", "cream": "#f4ede4",
    "gold": "#c9a34f", "silver": "#c3c8cc", "sand": "#e0c9a6", "terracotta": "#b5643f",
}

USE_HINTS = {
    "poster": "poster-a3", "billboard": "poster-a3", "flyer": "print-a4",
    "story": "social-story", "reel": "social-story", "tiktok": "social-story",
    "thumbnail": "web-thumb", "hero": "web-hero", "banner": "web-hero",
    "cover": "social-square", "album": "social-square", "podcast": "social-square",
    "instagram": "social-square", "social": "social-square", "linkedin": "social-landscape",
    "packaging": "packaging", "package": "packaging", "label": "packaging",
    "app icon": "app-icon", "icon": "app-icon", "favicon": "app-icon",
    "product": "product-listing", "listing": "product-listing", "marketplace": "product-listing",
    "presentation": "presentation", "slide": "presentation", "deck": "presentation",
    "print": "print-a4", "magazine": "print-a4", "brochure": "print-a4",
    "archive": "archive-master", "master": "archive-master",
}

STYLE_HINTS = [
    ("photographic", ["photo", "photograph", "photoreal", "realistic", "camera", "lifestyle shot", "studio shot"]),
    ("3d-render", ["3d", "render", "cgi", "octane", "blender"]),
    ("editorial-illustration", ["illustration", "illustrated", "editorial art", "vector art", "flat art"]),
    ("watercolour", ["watercolor", "watercolour", "aquarelle"]),
    ("ink-line", ["ink", "line drawing", "linework", "pen and ink"]),
    ("line-art", ["line art", "outline", "coloring page", "colouring page"]),
    ("concept-art", ["concept art", "production art", "matte painting"]),
    ("storybook", ["storybook", "children's book", "picture book"]),
    ("risograph", ["risograph", "riso print"]),
    ("woodcut", ["woodcut", "linocut", "block print"]),
    ("halftone", ["halftone", "newsprint", "screen print", "screenprint"]),
    ("charcoal", ["charcoal", "graphite", "pencil sketch", "sketch"]),
    ("gouache", ["gouache", "acrylic paint", "oil painting"]),
    ("collage", ["collage", "cut paper"]),
    ("pixel-art", ["pixel art", "8-bit", "16-bit", "sprite art"]),
    ("technical-vector", ["diagram", "schematic", "infographic", "chart", "blueprint", "technical drawing"]),
]

EXCLUDE_MARKERS = ("no ", "avoid", "without", "exclude", "not ", "don't ", "do not ", "free of ", "must not")
INCLUDE_MARKERS = ("must include", "must show", "include ", "featuring", "with the", "show the", "add ")


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def presets(cfg):
    return core.read_json(cfg.resource("render_presets_file", core.PRESETS_FILE), required=False) or {}


def prompt_presets(cfg):
    return core.read_json(core.SKILL_ROOT / "asset-prompt-presets.json", required=True)


def brief_template(cfg):
    return core.read_json(cfg.brief_template_path(), required=True)


def read_text_input(a, field="brief"):
    """Accept --brief text, --input file, or --brief-file JSON produced by 'brief'."""
    text = getattr(a, field, None) or ""
    path = getattr(a, "input", None)
    if path:
        p = core.resolve_input(path)
        raw = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            try:
                return json.loads(raw), p
            except json.JSONDecodeError:
                pass
        return raw.strip(), p
    if text:
        return str(text).strip(), None
    raise core.usage_error(
        "No brief supplied.",
        "Pass --brief \"a short description\" or --input brief.md / brief.json (see 'brief' command).",
    )


def load_brief(a, ctx):
    """Return a full brief dict, interpreting free text when needed."""
    payload, path = read_text_input(a)
    if isinstance(payload, dict):
        brief = normalize_brief(payload)
        brief["source"] = core.rel_posix(path) if path else "json"
        if not brief.get("open_questions"):
            brief["open_questions"] = []
        return brief
    brief = interpret_brief(payload, cfg=ctx.config, project=getattr(a, "project", None))
    brief["source"] = core.rel_posix(path) if path else "text"
    return brief


def normalize_brief(data):
    """Fill missing keys from the template so downstream tools never KeyError."""
    tpl = brief_template(core.load_config()).get("fields", {})
    brief = {}
    for key, meta in tpl.items():
        default = meta.get("default")
        brief[key] = data.get(key, default)
    brief.update({k: v for k, v in data.items() if k not in brief or v not in (None, "", [], {})})
    brief.setdefault("project", "untitled-project")
    for key in ("must_include", "must_exclude", "copy", "references", "acceptance_criteria", "palette"):
        if not isinstance(brief.get(key), list):
            brief[key] = [brief.get(key)] if brief.get(key) else []
    return brief


def looks_like_path(value):
    return bool(re.match(r"^[\w./\\-]+\.(png|jpg|jpeg|webp|gif|bmp|tif|tiff)$", str(value), re.I))


def interpret_brief(text, cfg, project=None):
    """Deterministic brief interpretation from free text. Inferences are listed, not hidden."""
    if not isinstance(text, str) or not text.strip():
        raise core.input_error("Brief text is empty.", "Describe the image you need in one or two sentences.")
    if len(text) > cfg.limit("max_text_chars", 20000):
        raise core.input_error("Brief text exceeds limits.max_text_chars", "Split the request into smaller briefs.")
    pp = prompt_presets(cfg)
    low = text.lower()
    assumptions = []
    raw_sentences = core.sentences(text)

    subject_type = core.detect(text, core.SUBJECT_WORDS) or "abstract"
    mood = core.detect(text, core.MOOD_WORDS) or ""
    framing = core.detect(text, core.FRAMING_WORDS) or "medium"
    lighting = core.detect(text, core.LIGHTING_WORDS) or ""
    style = None
    for label, words in STYLE_HINTS:
        if any(w in low for w in words):
            style = label
            break
    if not style:
        style = "photographic" if subject_type in ("person", "product", "environment", "architecture") else "editorial-illustration"
        assumptions.append(f"style inferred as '{style}' from subject type; pass --style to override")

    # subject / setting / action from the first informative sentence
    first = raw_sentences[0] if raw_sentences else text
    subject = re.sub(
        r"^(please\s+)?(create|make|generate|design|produce|build|render|draw|paint|shoot|give me|i want|i need|we need)\s+",
        "", first.strip(), flags=re.I).strip(" .;:")
    subject = re.sub(r"^(me\s+)?(a|an|the|some)\s+", "", subject, flags=re.I)
    m = re.match(
        r"^(?:[\w#+-]+\s+){0,5}?(image|picture|photo|photograph|illustration|graphic|artwork|visual|render|banner|poster|icon|asset|shot)\s+"
        r"(?:of|showing|with|featuring)\s+", subject, flags=re.I)
    if m:
        subject = subject[m.end():]
    subject = re.split(r"\s+(?:for|so that|to be used|destined for)\s+(?:a|an|the|our|their)\b", subject, maxsplit=1)[0]
    subject = subject.strip(" .;:,")
    if not subject:
        subject = first.strip(" .;:")
    setting = ""
    m = re.search(r"\b(?:set\s+)?(?:in|at|inside|on|against|within)\s+(?:a|an|the)?\s*([a-z][a-z '-]{2,40})(?=[,.;]|$)", low)
    if m:
        setting = m.group(1).strip(" ,.")
    action = ""
    m = re.search(r"\b(?:while|as|who is|that is|someone|a person)\s+([a-z][a-z '-]{2,40})(?=[,.;]|$)", low)
    if m:
        action = m.group(1).strip(" ,.")

    # dimensions / aspect / use
    output_use = None
    for hint, use in USE_HINTS.items():
        if hint in low:
            output_use = use
            break
    dims = None
    m = re.search(r"\b(\d{2,5})\s*[x×]\s*(\d{2,5})\b", low)
    if m:
        dims = f"{int(m.group(1))}x{int(m.group(2))}"
    else:
        m = re.search(r"\b(\d{1,2})\s*:\s*(\d{1,2})\b", low)
        if m:
            ratio_w, ratio_h = int(m.group(1)), int(m.group(2))
            base = 1440 if ratio_w >= ratio_h else 1080
            dims = f"{base}x{int(round(base * ratio_h / ratio_w))}"
            assumptions.append(f"aspect {ratio_w}:{ratio_h} expanded to {dims} px as a working size")
    if not output_use:
        output_use = "web-hero" if not dims else "archive-master"
        if not dims:
            assumptions.append(f"output use not stated; defaulted to '{output_use}'")
    if not dims:
        dims = pp["output_uses"][output_use]["sizes"][0]

    # palette
    palette = re.findall(r"#[0-9a-fA-F]{6}\b", text)
    for name, hexv in COLOR_NAMES.items():
        if re.search(rf"\b{name}\b", low) and hexv not in palette:
            palette.append(hexv)
            assumptions.append(f"colour word '{name}' mapped to {hexv}")
    palette = palette[:6]
    palette_preset = None
    for preset_key, preset_val in pp.get("palettes", {}).items():
        words = [w for w in preset_key.split("-") if len(w) > 3]
        hint = preset_val.get("mood", "").lower()
        if any(w in low for w in words) or (hint and any(h in low for h in hint.split(", "))):
            palette_preset = preset_key
            break

    # exclusions / inclusions
    must_exclude, must_include = [], []
    for sentence in raw_sentences:
        for clause in re.split(r"[,;]|\band\b|\bbut\b", sentence):
            clause = clause.strip(" .")
            if not clause:
                continue
            c_low = clause.lower()
            if any(marker in c_low for marker in EXCLUDE_MARKERS):
                cleaned = re.sub(
                    r"^(no|avoid|without|exclude|not|don't|do not|free of|must not|nothing)\s+(have\s+)?",
                    "", clause, flags=re.I)
                if cleaned and len(cleaned) > 2:
                    must_exclude.append(cleaned)
            elif any(marker in c_low for marker in INCLUDE_MARKERS):
                cleaned = re.sub(
                    r"^(must include|must show|include|featuring|with the|show the|add)\s+", "", clause, flags=re.I)
                if cleaned and len(cleaned) > 2:
                    must_include.append(cleaned)

    audience = ""
    m = re.search(r"\bfor\s+([a-z0-9 ,'&-]{3,60})", low)
    if m:
        audience = re.split(r"[,.;]", m.group(1))[0].strip(" ,")
        audience = re.sub(r"\s*\b\d{3,5}\s*[x\u00d7]\s*\d{3,5}\b.*$", "", audience).strip(" ,")

    purpose = ""
    for key, words in pp.get("purposes", {}).items():
        if any(w in low for w in key.split("-")):
            purpose = key
            break
    if not purpose:
        purpose = "conversion" if subject_type == "product" else "awareness"

    text_in_image = bool(re.search(r"\b(headline|text|typography|title|logo|copy|call to action|cta|caption)\b", low))
    copy_strings = re.findall(r'"([^"]{2,80})"|\u201c([^\u201d]{2,80})\u201d', text)
    copy_strings = [(a or b) for a, b in copy_strings]

    transparency = bool(re.search(r"\b(transparent background|alpha|cutout|cut-out|no background)\b", low))

    brief = {
        "project": core.slugify(project or (re.search(r"\b(?:for|project)\s+([A-Z][\w &-]{2,30})", text).group(1)
                                            if re.search(r"\b(?:for|project)\s+([A-Z][\w &-]{2,30})", text) else "untitled-project")),
        "title": (first[:90].rsplit(" ", 1)[0] if len(first) > 90 else first),
        "purpose": purpose,
        "audience": audience,
        "subject": subject[:300],
        "subject_type": subject_type,
        "action": action,
        "setting": setting,
        "style": style,
        "mood": mood,
        "framing": framing,
        "lighting": lighting,
        "palette": palette,
        "palette_preset": palette_preset,
        "dimensions": dims,
        "output_use": output_use,
        "format": pp["output_uses"].get(output_use, {}).get("format", "png"),
        "transparency": transparency,
        "text_in_image": text_in_image,
        "copy": copy_strings[:6],
        "must_include": must_include[:8],
        "must_exclude": must_exclude[:8],
        "references": [],
        "acceptance_criteria": [],
        "rights_context": "",
        "disclosure_required": True,
        "keywords": core.keywords(text, 10),
        "assumptions": assumptions,
        "open_questions": [],
        "source_text": text.strip()[:4000],
    }
    brief["acceptance_criteria"] = default_acceptance(brief, cfg)
    brief["open_questions"] = open_questions(brief)
    return brief


def default_acceptance(brief, cfg):
    lib = brief_template(cfg).get("acceptance_criterion_library", {})
    out = list(lib.get("technical", [])[:2]) + list(lib.get("visual", [])[:2])
    if brief.get("subject_type") in ("product", "abstract") and brief.get("purpose") == "conversion":
        out.append("product occupies 80-90 percent of the frame with a clean contact shadow")
    if brief.get("text_in_image"):
        out.append("all rendered text is spelled correctly and survives the 50 percent scale check")
    if brief.get("must_exclude"):
        out.append("excluded elements absent: " + "; ".join(brief["must_exclude"][:3]))
    out.append("no synthetic disclosure missing when the image can be mistaken for a photograph")
    return out


def open_questions(brief):
    qs = []
    if not brief.get("subject"):
        qs.append("What must be visible in the image?")
    if not brief.get("audience"):
        qs.append("Who is the audience, and where will this be seen?")
    if brief.get("text_in_image") and not brief.get("copy"):
        qs.append("Which exact strings should appear in the image? Copy is never invented.")
    if brief.get("references") and not brief.get("rights_context"):
        qs.append("What rights context covers the supplied references (licence, consent, brand permission)?")
    if not qs:
        qs.append("Confirm brand/rights context before external publication.")
    return qs


# --------------------------------------------------------------------------
# 1. Creative brief interpreter
# --------------------------------------------------------------------------


def op_brief(ctx, a):
    text = getattr(a, "brief", None)
    if getattr(a, "input", None) and not text:
        p = core.resolve_input(a.input)
        payload = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            brief = normalize_brief(json.loads(payload))
            brief.setdefault("open_questions", open_questions(brief))
            brief.setdefault("assumptions", [])
            brief.setdefault("keywords", core.keywords(json.dumps(brief), 8))
            brief["source"] = core.rel_posix(p)
        else:
            brief = interpret_brief(payload, ctx.config, getattr(a, "project", None))
            brief["source"] = core.rel_posix(p)
    elif text:
        brief = interpret_brief(text, ctx.config, getattr(a, "project", None))
        brief["source"] = "text"
    else:
        raise core.usage_error("The 'brief' command needs --brief TEXT or --input FILE.",
                               "Example: tools-imagekit.py brief --brief \"soft studio photo of a serum bottle\" --json")

    safety = core.enforce_policy(ctx, json.dumps(brief, ensure_ascii=False), "brief-intake")
    brief["safety"] = {"level": safety["summary"]["level"], "gates": safety["summary"]["gates"],
                       "disclosures": safety["disclosures"]}
    if safety["summary"]["level"] == "disclose":
        ctx.note("Photorealistic wording detected: authenticity disclosure will be attached on delivery.")

    name = core.slugify(f"{brief['project']}-brief")
    md = render_brief_md(brief)
    outs = [core.emit_json(ctx, name, brief), core.emit_text(ctx, name, "md", md)]
    ctx.audit("brief.interpreted", project=brief["project"], source=brief["source"])
    return {"data": {"brief": brief, "files": [core.summary_line(ctx, p) for p in outs]}, "outputs": outs}


def render_brief_md(brief):
    lines = [f"# Image brief: {brief.get('title') or brief.get('project')}", "",
             f"- **Project**: {brief.get('project')}",
             f"- **Purpose**: {brief.get('purpose')}",
             f"- **Audience**: {brief.get('audience') or '_not stated_'}",
             f"- **Subject type**: {brief.get('subject_type')}",
             f"- **Subject**: {brief.get('subject')}",
             f"- **Setting**: {brief.get('setting') or '_not stated_'}",
             f"- **Style / medium**: {brief.get('style')}",
             f"- **Mood**: {brief.get('mood') or '_not stated_'}",
             f"- **Framing**: {brief.get('framing')}",
             f"- **Lighting**: {brief.get('lighting') or '_inferred from style_'}",
             f"- **Dimensions**: {brief.get('dimensions')} ({core.aspect_label(*core.parse_size(brief.get('dimensions')))})",
             f"- **Output use**: {brief.get('output_use')}",
             f"- **Format / transparency**: {brief.get('format')} / {'alpha' if brief.get('transparency') else 'opaque'}",
             f"- **Palette**: {', '.join(brief.get('palette') or []) or '_director choice_'}",
             "", "## Must include"]
    lines += [f"- {item}" for item in brief.get("must_include") or ["_nothing specified_"]]
    lines += ["", "## Must exclude"]
    lines += [f"- {item}" for item in brief.get("must_exclude") or ["_nothing specified_"]]
    lines += ["", "## Acceptance criteria"]
    lines += [f"- {item}" for item in brief.get("acceptance_criteria") or []]
    if brief.get("assumptions"):
        lines += ["", "## Assumptions made by the interpreter"]
        lines += [f"- {item}" for item in brief["assumptions"]]
    lines += ["", "## Open questions before production"]
    lines += [f"- {item}" for item in brief.get("open_questions") or []]
    if brief.get("keywords"):
        lines += ["", f"**Keywords**: {', '.join(brief['keywords'])}"]
    if brief.get("safety"):
        lines += ["", f"**Safety screen**: {brief['safety']['level']}"
                       + (f" (gates: {', '.join(brief['safety']['gates'])})" if brief["safety"]["gates"] else "")]
        for d in brief["safety"].get("disclosures", []):
            lines.append(f"- disclosure [{d['category']}]: {d['text']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 2. Prompt architect
# --------------------------------------------------------------------------


def nearest_color_name(hex_value):
    rgb = core.hex_to_rgb(hex_value)
    best, best_d = "custom", 1e9
    for name, hx in COLOR_NAMES.items():
        r2 = core.hex_to_rgb(hx)
        d = sum((rgb[i] - r2[i]) ** 2 for i in range(3))
        if d < best_d:
            best, best_d = name, d
    return best


def build_prompt(brief, cfg, seed_text="", dialect="generic", variant=None):
    """Assemble a model-ready prompt plus structured sections and negatives."""
    pp = prompt_presets(cfg)
    variant = variant or {}
    lens_key = variant.get("lens") or ("macro" if "macro" in str(brief.get("framing", "")).lower() else "normal")
    if lens_key not in pp["lenses"]:
        lens_key = "normal"
    light_key = variant.get("lighting") or brief.get("lighting") or "natural-daylight"
    light_key = light_key if light_key in pp["lighting"] else "natural-daylight"
    palette_key = variant.get("palette")
    palette_hex = brief.get("palette") or (pp["palettes"].get(palette_key, {}).get("colors") if palette_key else None) \
        or pp["palettes"]["warm-neutral"]["colors"]
    comp_key = variant.get("composition") or "rule-of-thirds"
    media_key = variant.get("medium") or brief.get("style") or "photographic"
    era_key = variant.get("era")

    subject_type = brief.get("subject_type", "abstract")
    sections = {}
    subject_bits = [brief.get("subject") or "an unspecified subject"]
    if brief.get("action"):
        subject_bits.append(brief["action"])
    if brief.get("setting"):
        subject_bits.append(f"set in {brief['setting']}")
    sections["subject"] = ", ".join(subject_bits)

    hierarchy = [pp["composition_devices"].get(comp_key, pp["composition_devices"]["rule-of-thirds"])]
    if subject_type in ("product", "abstract"):
        hierarchy.append("subject reads first, texture second, background last")
    elif subject_type == "person":
        hierarchy.append("eyes and hands carry the focal weight")
    elif subject_type == "data":
        hierarchy.append("primary data series reads before annotations")
    else:
        hierarchy.append("single dominant mass with two supporting layers")
    if brief.get("must_include"):
        hierarchy.append("include: " + "; ".join(brief["must_include"][:4]))
    sections["composition"] = "; ".join(hierarchy)

    camera = [pp["lenses"][lens_key]["text"], pp["camera_heights"].get("eye-level", "")]
    if subject_type == "product":
        camera.append("slightly above the product's mid-height, product axis vertical")
    if brief.get("framing") in ("close-up", "top-down"):
        camera.append(f"{brief['framing']} framing")
    camera.append("no lens distortion on straight edges unless stated")
    sections["camera"] = "; ".join(c for c in camera if c)

    light = pp["lighting"][light_key]["text"]
    warmth = pp["lighting"][light_key].get("temp_k")
    sections["lighting"] = f"{light}" + (f"; colour temperature near {warmth}K" if warmth else "")

    material_map = {
        "person": "skin with visible pore structure, fabric with real weave and weight, hair with individual strand grouping",
        "product": "label edges respond to the container curvature, material micro-roughness consistent, contact shadow present",
        "environment": "weathered surfaces, correct ground texture scale, atmospheric perspective on distant layers",
        "architecture": "material joints respect construction logic, glass reflects the actual surroundings",
        "creature": "anatomy follows a single silhouette logic, skin and scale transitions deliberate",
        "abstract": "surface imperfection at human scale, no vector-flat gradients unless intended",
        "data": "uniform stroke weights, consistent arrowheads, labels never overlap",
    }
    sections["materials"] = material_map.get(subject_type, material_map["abstract"])
    sections["style"] = pp["media"].get(media_key, media_key)
    if era_key and era_key in pp["eras"]:
        sections["style"] = f"{sections['style']}; {pp['eras'][era_key]}"
    palette_text = ", ".join(f"{nearest_color_name(h)} {h}" for h in palette_hex[:5])
    sections["palette"] = palette_text
    if brief.get("mood"):
        sections["mood"] = brief["mood"]
    sections["output"] = (f"{brief.get('dimensions')} ({core.aspect_label(*core.parse_size(brief.get('dimensions')))}) "
                          f"{brief.get('format')}, {'transparent background' if brief.get('transparency') else 'opaque background'}")
    if brief.get("must_exclude"):
        sections["exclusions"] = "; ".join(brief["must_exclude"][:5])

    negative_bits = [pp["negatives"]["general"]]
    neg_key = {"person": "portrait", "product": "product", "architecture": "architecture",
               "data": "diagram", "creature": "character"}.get(subject_type)
    if neg_key:
        negative_bits.append(pp["negatives"][neg_key])
    if brief.get("must_exclude"):
        negative_bits.append("; ".join(brief["must_exclude"]))
    negative = " | ".join(negative_bits)

    if dialect == "diffusion":
        positive = " --".join([", ".join(sections[k] for k in ("subject", "composition", "camera", "lighting",
                                                                "materials", "style", "palette"))])
        positive = ", ".join(sections[k] for k in ("subject", "composition", "camera", "lighting",
                                                    "materials", "style", "palette")) + ", high detail, professional finish"
    elif dialect == "chat":
        positive = (f"Create a {brief.get('dimensions')} image. Subject: {sections['subject']}. "
                    f"Composition: {sections['composition']}. Camera: {sections['camera']}. "
                    f"Lighting: {sections['lighting']}. Materials: {sections['materials']}. "
                    f"Style: {sections['style']}. Palette: {sections['palette']}. "
                    f"Deliver as {sections['output']}. Do not include: {negative}.")
    else:
        positive = ", ".join(sections[k] for k in ("subject", "composition", "camera", "lighting",
                                                   "materials", "style", "palette", "output") if sections.get(k))
    if seed_text:
        positive += f", {seed_text}"
    tokens = max(1, len(positive.split()) + len(negative.split()))
    return {
        "positive": positive,
        "negative": negative,
        "sections": sections,
        "controls": {"lens": lens_key, "lighting": light_key, "composition": comp_key,
                     "medium": media_key, "era": era_key, "palette": palette_hex[:5]},
        "token_estimate": tokens,
        "dialect": dialect,
    }


def op_prompt(ctx, a):
    brief = load_brief(a, ctx)
    core.enforce_policy(ctx, json.dumps(brief, ensure_ascii=False), "prompt-architecture")
    built = build_prompt(brief, ctx.config, seed_text=getattr(a, "emphasis", "") or "",
                         dialect=getattr(a, "dialect", "generic") or "generic")
    payload = {"brief": brief, "prompt": built, "generated_at": core.now_iso()}
    name = core.slugify(f"{brief['project']}-prompt")
    md = [f"# Generation prompt: {brief.get('title') or brief['project']}", "",
          "## Positive prompt", "", "```text", built["positive"], "```", "",
          "## Negative prompt", "", "```text", built["negative"], "```", "",
          "## Structured sections"]
    md += [f"- **{k}**: {v}" for k, v in built["sections"].items()]
    md += ["", "## Controls", "", "```json",
           json.dumps(built["controls"], indent=2), "```", "",
           f"Prompt length: {built['token_estimate']} words. Dialect: {built['dialect']}.", "",
           "Provider prompts are portable text: no model-specific syntax, weights or secret tokens are embedded."]
    outs = [core.emit_json(ctx, name, payload), core.emit_text(ctx, name, "md", "\n".join(md) + "\n")]
    ctx.audit("prompt.built", project=brief["project"], tokens=built["token_estimate"])
    return {"data": {"prompt": built["positive"], "negative": built["negative"],
                     "sections": built["sections"], "files": [core.summary_line(ctx, p) for p in outs]},
            "outputs": outs}


# --------------------------------------------------------------------------
# 3. Prompt variants + concept exploration
# --------------------------------------------------------------------------


VARIANT_AXES = {
    "composition": ["centred", "diagonal", "negative-space", "layered-depth", "flat-lay", "isolation"],
    "lens": ["ultra-wide", "wide", "normal", "portrait", "telephoto", "macro"],
    "lighting": ["natural-daylight", "golden-hour", "studio-soft", "studio-low-key", "dramatic-side", "neon-night"],
    "palette": ["warm-neutral", "cool-corporate", "vivid-editorial", "earthy-organic", "monochrome", "nocturne"],
    "medium": ["photographic", "editorial-illustration", "watercolour", "ink-line", "concept-art", "3d-render"],
    "era": ["contemporary", "mid-century", "art-deco", "1980s", "near-future", "pre-industrial"],
    "emphasis": ["", "human presence added", "macro detail emphasis", "scale emphasised by foreground anchor",
                 "atmospheric haze and depth", "graphic reduction to three values"],
}

VARIANT_RATIONALE = {
    "composition": "changes where the viewer looks first and how the frame breathes",
    "lens": "changes perspective, compression and how much context survives",
    "lighting": "changes mood, material read and shadow behaviour",
    "palette": "changes emotional temperature and brand fit",
    "medium": "changes production logic from photographic to illustrative",
    "era": "changes period cues without touching the subject",
    "emphasis": "adds a narrative or scale cue without changing the brief",
}


def op_variants(ctx, a):
    brief = load_brief(a, ctx)
    count = int(getattr(a, "count", 6) or 6)
    max_items = ctx.config.limit("max_batch_items", 500)
    if count < 2 or count > min(36, max_items):
        raise core.usage_error(f"--count must be between 2 and {min(36, max_items)}")
    axes = (getattr(a, "axes", None) or "composition,lens,lighting").split(",")
    axes = [ax.strip() for ax in axes if ax.strip() in VARIANT_AXES] or ["composition", "lens", "lighting"]
    seed = ctx.seed if ctx.seed is not None else 7
    rng = core.seeded_rng(seed, "variants")
    seen, variants = set(), []
    guard = 0
    while len(variants) < count and guard < count * 40:
        guard += 1
        axis = axes[len(variants) % len(axes)]
        choice = rng.choice(VARIANT_AXES[axis])
        key = (axis, choice)
        if key in seen:
            continue
        seen.add(key)
        override = {axis: choice} if axis != "emphasis" else {}
        built = build_prompt(brief, ctx.config, seed_text=choice if axis == "emphasis" else "",
                             dialect=getattr(a, "dialect", "generic") or "generic", variant=override)
        variants.append({
            "id": f"V{len(variants) + 1:02d}",
            "axis": axis,
            "change": choice or "baseline emphasis",
            "positive": built["positive"],
            "negative": built["negative"],
            "controls": built["controls"],
            "rationale": VARIANT_RATIONALE[axis],
            "risk": ("reduces subject legibility" if choice in ("isolation", "flat-lay", "monochrome") else
                     "may drift from brand palette" if axis == "palette" else
                     "higher generation cost / lower consistency" if axis in ("medium", "era") else "low"),
        })
    concepts = concept_directions(brief, ctx.config, rng, limit=min(4, count))
    payload = {"brief": brief, "variants": variants, "concepts": concepts, "axes": axes, "seed": seed}
    name = core.slugify(f"{brief['project']}-variants")
    md = [f"# Prompt variants: {brief.get('title') or brief['project']}", "",
          f"Axes varied: {', '.join(axes)}. Seed: {seed}. Count: {len(variants)}.", "",
          "## Concept directions (decide before producing)", ""]
    for c in concepts:
        md += [f"### {c['id']} — {c['name']}", f"- Hypothesis: {c['hypothesis']}",
               f"- Composition: {c['composition']}", f"- Risk: {c['risk']}", ""]
    md += ["## Variant prompts", ""]
    for v in variants:
        md += [f"### {v['id']} — {v['axis']}: {v['change']}", "",
               f"_{v['rationale']}; risk: {v['risk']}_", "", "```text", v["positive"], "```", ""]
    outs = [core.emit_json(ctx, name, payload), core.emit_text(ctx, name, "md", "\n".join(md) + "\n")]
    ctx.audit("variants.generated", count=len(variants), axes=axes)
    return {"data": {"variants": len(variants), "axes": axes, "concepts": concepts,
                     "files": [core.summary_line(ctx, p) for p in outs]}, "outputs": outs}


def concept_directions(brief, cfg, rng, limit=4):
    pp = prompt_presets(cfg)
    archetypes = [
        {"name": "Hero object", "composition": "single subject locked to the optical centre, negative space reserved for type",
         "hypothesis": "maximum recall when the asset must work at thumbnail size"},
        {"name": "Human moment", "composition": "subject in use, hands or face carrying the emotional read",
         "hypothesis": "raises trust and relatability for lifestyle and service messages"},
        {"name": "Environment story", "composition": "wide establishing frame, subject small but load-bearing",
         "hypothesis": "communicates context and scale; weaker at small sizes"},
        {"name": "Graphic reduction", "composition": "three-value poster logic, shapes over detail",
         "hypothesis": "fastest to adapt across ratios and print, loses material realism"},
        {"name": "Macro detail", "composition": "tight crop on texture and interface tolerance",
         "hypothesis": "signals craft and quality; needs a companion context shot"},
        {"name": "Systemic pattern", "composition": "repeating motif or grid derived from the brand mark",
         "hypothesis": "scales into backgrounds and merchandise without new photography"},
    ]
    picked = rng.sample(archetypes, min(limit, len(archetypes)))
    out = []
    for i, item in enumerate(picked):
        palette = rng.choice(list(pp["palettes"].keys()))
        out.append({
            "id": f"C{i + 1}",
            "name": item["name"],
            "composition": item["composition"],
            "hypothesis": item["hypothesis"],
            "palette": palette,
            "risk": "low" if i == 0 else rng.choice(["medium", "medium-high"]),
            "test_metric": "thumbnail legibility, first-glance subject identification, brand-palette match",
        })
    return out


# --------------------------------------------------------------------------
# 4. Image specification builder
# --------------------------------------------------------------------------


def op_spec(ctx, a):
    pp = prompt_presets(ctx.config)
    use = getattr(a, "use", None) or "web-hero"
    if use not in pp["output_uses"]:
        raise core.usage_error(f"Unknown --use '{use}'",
                               "Choose one of: " + ", ".join(sorted(pp["output_uses"])))
    preset = pp["output_uses"][use]
    size = core.parse_size(getattr(a, "size", None) or preset["sizes"][0])
    size = core.check_size(ctx.config, size)
    fmt = (getattr(a, "format", None) or preset["format"]).lower().lstrip(".")
    if fmt not in core.WRITE_FORMATS:
        raise core.usage_error(f"Unsupported --format '{fmt}'",
                               "Supported: " + ", ".join(sorted(core.WRITE_FORMATS)))
    transparent = bool(getattr(a, "transparent", False))
    if transparent and fmt in ("jpg", "jpeg", "pdf"):
        raise core.usage_error(f"'{fmt}' cannot store transparency.",
                              "Use png, webp or tiff for alpha, or drop --transparent.")
    dpi = int(getattr(a, "dpi", 0) or preset.get("dpi", core.dpi_default(ctx.config)))
    quality = int(getattr(a, "quality", 92) or 92)
    project = getattr(a, "project", None) or "project"
    naming_provider = ctx.config.naming
    filename = core.build_filename(
        {"project": project, "content": getattr(a, "name", None) or use, "dimensions": size,
         "version": int(getattr(a, "version", 1) or 1), "status": getattr(a, "status", None) or "draft"},
        fmt, naming_provider)
    color_mode = "RGBA" if transparent else "RGB"
    payload = {
        "output_use": use,
        "size": {"width": size[0], "height": size[1], "aspect": core.aspect_label(*size),
                 "megapixels": round(size[0] * size[1] / 1e6, 2)},
        "color": {"mode": color_mode, "icc": "sRGB" if fmt != "tiff" else "sRGB or printer profile",
                  "cmyk": "requires an external ICC profile; this tool writes RGB and flags print conversion"},
        "format": {"extension": fmt, "codec": core.WRITE_FORMATS[fmt], "quality": quality,
                   "lossless": fmt in ("png", "tiff", "bmp"),
                   "transparency_supported": fmt in ("png", "webp", "tiff", "gif", "avif")},
        "resolution": {"dpi": dpi, "note": "dpi is metadata only; pixel dimensions decide real detail"},
        "metadata_plan": ["title", "creator/tool", "provenance record", "synthetic disclosure when applicable",
                          "copyright/licence when known"],
        "naming": {"suggested": filename, "pattern": naming_provider.get("pattern"),
                   "note": "status values: draft, review, approved, final, superseded"},
        "downstream": {"use": use, "allowed_formats": ["png", "jpg", "webp", "tiff", "gif", "pdf"],
                       "max_file_kb": getattr(a, "max_kb", None), "notes": preset.get("note", "")},
        "companion_sizes": preset.get("sizes", []),
        "validation": [],
    }
    if transparent and fmt in ("png", "webp"):
        payload["validation"].append("alpha channel required; verify edge quality on a checkerboard")
    if use == "print-a4":
        payload["validation"].append("request a CMYK proof from the printer; RGB preview is not a proof")
    if size[0] * size[1] > 40e6:
        payload["validation"].append("large pixel count: confirm memory headroom on the target machine")
    payload["validation"].append("open the exported file in the target application before delivery")

    name = core.slugify(f"{project}-spec-{use}")
    md = [f"# Image specification — {use}", "",
          f"- **Pixels**: {size[0]}x{size[1]} ({payload['size']['aspect']}, {payload['size']['megapixels']} MP)",
          f"- **Colour**: {color_mode}, sRGB",
          f"- **Format**: {fmt.upper()} at quality {quality} ({'lossless' if payload['format']['lossless'] else 'lossy'})",
          f"- **DPI metadata**: {dpi}",
          f"- **Suggested filename**: `{filename}`",
          f"- **Companion sizes**: {', '.join(preset.get('sizes', []))}",
          "", "## Metadata plan"]
    md += [f"- {item}" for item in payload["metadata_plan"]]
    md += ["", "## Pre-delivery checks"]
    md += [f"- {item}" for item in payload["validation"]]
    outs = [core.emit_json(ctx, name, payload), core.emit_text(ctx, name, "md", "\n".join(md) + "\n")]
    ctx.audit("spec.built", use=use, size=size, format=fmt)
    return {"data": payload, "outputs": outs}


# --------------------------------------------------------------------------
# 5. Composition planner
# --------------------------------------------------------------------------

THIRDS = [(1 / 3, 1 / 3), (2 / 3, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 2 / 3)]
SAFE_ZONES = {
    "web-hero": {"type": "rect", "x": 0.08, "y": 0.18, "w": 0.36, "h": 0.5, "purpose": "headline block"},
    "social-square": {"type": "rect", "x": 0.08, "y": 0.08, "w": 0.84, "h": 0.84, "purpose": "safe margin"},
    "social-story": {"type": "rect", "x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6, "purpose": "centre-safe text"},
    "poster-a3": {"type": "rect", "x": 0.1, "y": 0.06, "w": 0.8, "h": 0.2, "purpose": "title band"},
    "product-listing": {"type": "rect", "x": 0.075, "y": 0.075, "w": 0.85, "h": 0.85, "purpose": "product envelope"},
    "app-icon": {"type": "circle", "cx": 0.5, "cy": 0.5, "r": 0.36, "purpose": "legible glyph circle"},
    "presentation": {"type": "rect", "x": 0.06, "y": 0.1, "w": 0.45, "h": 0.8, "purpose": "slide type column"},
}


def op_composition(ctx, a):
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or "1024x1024"))
    use = getattr(a, "use", None) or "web-hero"
    subject_count = int(getattr(a, "subjects", 1) or 1)
    if subject_count < 1 or subject_count > 50:
        raise core.usage_error("--subjects must be between 1 and 50")
    focal = getattr(a, "focal", None) or "upper-left-third"
    horizon = float(getattr(a, "horizon", 0.62) or 0.62)
    if not 0.0 <= horizon <= 1.0:
        raise core.usage_error("--horizon must be between 0 and 1")

    focal_map = {
        "upper-left-third": (1 / 3, 1 / 3), "upper-right-third": (2 / 3, 1 / 3),
        "lower-left-third": (1 / 3, 2 / 3), "lower-right-third": (2 / 3, 2 / 3),
        "centre": (0.5, 0.5), "left-mass": (0.3, 0.55), "right-mass": (0.7, 0.55),
    }
    if focal not in focal_map:
        raise core.usage_error(f"Unknown --focal '{focal}'", "Choose: " + ", ".join(sorted(focal_map)))
    fx, fy = focal_map[focal]
    zone = SAFE_ZONES.get(use, SAFE_ZONES["web-hero"])
    zone_rect = {k: round(zone.get(k, 0) * (size[0] if k in ("x", "w", "cx", "r") else size[1]), 1) for k in zone
                 if k in ("x", "y", "w", "h", "cx", "cy", "r")}
    focal_px = (round(fx * size[0]), round(fy * size[1]))
    subject_w = size[0] / max(1.6, math.sqrt(subject_count) * 1.5)
    subjects = []
    for i in range(subject_count):
        offset = (i - (subject_count - 1) / 2) * subject_w * 0.75
        cx = core.clamp(focal_px[0] + offset, subject_w / 2, size[0] - subject_w / 2)
        subjects.append({"id": f"S{i + 1}", "center": [round(cx), focal_px[1]],
                         "width": round(subject_w), "height": round(subject_w * 1.35)})
    warnings = []
    if zone["type"] == "rect":
        zx, zy = zone["x"], zone["y"]
        overlaps = [s for s in subjects
                    if abs(s["center"][0] / size[0] - (zx + zone["w"] / 2)) < (zone["w"] + s["width"] / size[0]) / 2
                    and abs(s["center"][1] / size[1] - (zy + zone["h"] / 2)) < (zone["h"] + s["height"] / size[1]) / 2]
        if overlaps:
            warnings.append("focal subject overlaps the type-safe zone; shift the subject or move the text block")
    if abs(fy - horizon) < 0.06:
        warnings.append("focal point sits on the horizon line: risk of a tangency cut through the subject")
    margin = 0.05
    edge_risk = [s["id"] for s in subjects
                 if s["center"][0] - s["width"] / 2 < size[0] * margin
                 or s["center"][0] + s["width"] / 2 > size[0] * (1 - margin)]
    if edge_risk:
        warnings.append(f"near-edge subjects risk crop damage: {', '.join(edge_risk)}")

    plan = {
        "size": {"width": size[0], "height": size[1], "aspect": core.aspect_label(*size)},
        "output_use": use,
        "grid": {"thirds": [[round(x * size[0]), round(y * size[1])] for x, y in THIRDS],
                 "horizon_y": round(horizon * size[1])},
        "focal_point": {"label": focal, "x": focal_px[0], "y": focal_px[1]},
        "subjects": subjects,
        "type_safe_zone": {"shape": zone["type"], "purpose": zone["purpose"], "pixels": zone_rect},
        "negative_space": {"region": "opposite the focal point",
                           "pixels": round(size[0] * size[1] * 0.28)},
        "depth_plan": ["foreground anchor at 0-25 percent of frame height",
                       "subject plane 35-70 percent",
                       "atmospheric background beyond 75 percent"],
        "crop_safety": {"keep_center_band": round(size[1] * 0.6), "protected_rect": zone_rect},
        "warnings": warnings,
    }
    svg = composition_svg(plan, size)
    name = core.slugify(f"{getattr(a, 'project', None) or 'composition'}-{use}-plan")
    outs = [core.emit_json(ctx, name, plan), core.emit_text(ctx, name, "svg", svg)]
    ctx.audit("composition.planned", use=use, subjects=subject_count)
    return {"data": plan, "outputs": outs}


def composition_svg(plan, size):
    w, h = size
    scale = 1.0
    if max(w, h) > 1400:
        scale = 1400 / max(w, h)
    vw, vh = round(w * scale), round(h * scale)

    def sx(x):
        return round(x * scale, 1)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}" height="{vh}" viewBox="0 0 {vw} {vh}">',
             f'<rect width="{vw}" height="{vh}" fill="#101418"/>']
    for i in range(4):
        x = sx(w * i / 3)
        y = sx(h * i / 3)
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{vh}" stroke="#2b3540" stroke-width="1"/>')
        parts.append(f'<line x1="0" y1="{y}" x2="{vw}" y2="{y}" stroke="#2b3540" stroke-width="1"/>')
    zone = plan["type_safe_zone"]
    if zone["shape"] == "rect":
        px = zone["pixels"]
        parts.append(f'<rect x="{sx(px["x"])}" y="{sx(px["y"])}" width="{sx(px["w"])}" height="{sx(px["h"])}" '
                     f'fill="#f2c14e" fill-opacity="0.12" stroke="#f2c14e" stroke-dasharray="6 4"/>')
        parts.append(f'<text x="{sx(px["x"]) + 8}" y="{sx(px["y"]) + 20}" fill="#f2c14e" '
                     f'font-family="sans-serif" font-size="13">{zone["purpose"]}</text>')
    elif zone["shape"] == "circle":
        p = zone["pixels"]
        parts.append(f'<circle cx="{sx(p["cx"])}" cy="{sx(p["cy"])}" r="{sx(p["r"])}" fill="none" '
                     f'stroke="#f2c14e" stroke-dasharray="6 4"/>')
    parts.append(f'<line x1="0" y1="{sx(plan["grid"]["horizon_y"])}" x2="{vw}" y2="{sx(plan["grid"]["horizon_y"])}" '
                 f'stroke="#4cc9f0" stroke-width="1.5"/>')
    parts.append(f'<text x="8" y="{sx(plan["grid"]["horizon_y"]) - 6}" fill="#4cc9f0" font-family="sans-serif" font-size="12">horizon</text>')
    for s in plan["subjects"]:
        cx, cy = s["center"]
        parts.append(f'<rect x="{sx(cx - s["width"] / 2)}" y="{sx(cy - s["height"] / 2)}" '
                     f'width="{sx(s["width"])}" height="{sx(s["height"])}" fill="#4cc9f0" fill-opacity="0.16" '
                     f'stroke="#4cc9f0" stroke-width="1.5"/>')
        parts.append(f'<text x="{sx(cx) - 10}" y="{sx(cy)}" fill="#e9f6fb" font-family="sans-serif" font-size="13">{s["id"]}</text>')
    fx, fy = plan["focal_point"]["x"], plan["focal_point"]["y"]
    parts.append(f'<circle cx="{sx(fx)}" cy="{sx(fy)}" r="7" fill="#ff5c5c"/>')
    parts.append(f'<text x="{sx(fx) + 12}" y="{sx(fy) + 4}" fill="#ff5c5c" font-family="sans-serif" '
                 f'font-size="12">{plan["focal_point"]["label"]}</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# 6. Art direction manager
# --------------------------------------------------------------------------


def op_direction(ctx, a):
    brief = load_brief(a, ctx)
    pp = prompt_presets(ctx.config)
    mode = getattr(a, "mode", None) or "create"
    if mode == "create":
        built = build_prompt(brief, ctx.config)
        palette_name = None
        for key, item in pp["palettes"].items():
            if item["colors"][2:4] and set(item["colors"][2:4]) & set(brief.get("palette") or []):
                palette_name = key
                break
        direction = {
            "version": 1,
            "project": brief["project"],
            "locked": {
                "subject": brief.get("subject"),
                "subject_type": brief.get("subject_type"),
                "medium": brief.get("style"),
                "lighting": built["controls"]["lighting"],
                "lens": built["controls"]["lens"],
                "composition": built["controls"]["composition"],
                "palette": (brief.get("palette") or pp["palettes"].get(palette_name or "warm-neutral")["colors"]),
                "dimensions": brief.get("dimensions"),
                "exclusions": brief.get("must_exclude", []),
            },
            "flexible": ["crop within safe band", "copy wording", "variant framing", "background detail density",
                         "secondary prop choice"],
            "tokens": {
                "contrast_target": "midtone-dominant, 8 percent clipped highlights maximum",
                "grain": "subtle, 3 percent maximum",
                "shadow_behaviour": "soft contact shadow, single dominant light direction",
                "text_treatment": "one display face, one body face, 4.5:1 minimum contrast",
            },
            "approval_owner": getattr(a, "owner", None) or "art director",
            "created_at": core.now_iso(),
        }
        payload = direction
        name = core.slugify(f"{brief['project']}-art-direction")
        md = [f"# Art direction: {brief['project']}", "", "## Locked decisions"]
        md += [f"- **{k}**: {v}" for k, v in direction["locked"].items()]
        md += ["", "## Flexible within the direction"]
        md += [f"- {item}" for item in direction["flexible"]]
        md += ["", "## Direction tokens"]
        md += [f"- **{k}**: {v}" for k, v in direction["tokens"].items()]
        md += ["", f"Approval owner: {direction['approval_owner']}"]
        outs = [core.emit_json(ctx, name, payload), core.emit_text(ctx, name, "md", "\n".join(md) + "\n")]
        ctx.audit("direction.created", project=brief["project"])
        return {"data": payload, "outputs": outs}

    # check mode: score a candidate description/prompt against a direction file
    direction_path = getattr(a, "direction", None)
    if not direction_path:
        raise core.usage_error("--mode check needs --direction PATH (a JSON file created by --mode create).")
    direction = core.read_json(core.resolve_input(direction_path), required=True)
    locked = direction.get("locked", {})
    candidate_text = (getattr(a, "candidate", None) or brief.get("subject") or "") + " " + json.dumps(brief)
    low = candidate_text.lower()
    checks = []
    palette = [h.lower() for h in locked.get("palette", [])]
    found = [h for h in palette if h.lower() in low]
    checks.append({"rule": "palette", "score": round(len(found) / max(1, len(palette)), 2),
                   "detail": f"{len(found)}/{len(palette)} locked colours referenced"})
    for key in ("medium", "lighting", "lens", "composition"):
        want = str(locked.get(key) or "").lower()
        checks.append({"rule": key, "score": 1.0 if want and want.replace("-", " ") in low.replace("-", " ") else 0.0,
                       "detail": f"expected '{locked.get(key)}'"})
    exclusions = locked.get("exclusions") or []
    violated = [e for e in exclusions if e.lower() in low]
    checks.append({"rule": "exclusions", "score": 0.0 if violated else 1.0,
                   "detail": "violated: " + "; ".join(violated) if violated else "no locked exclusion present"})
    dims = locked.get("dimensions")
    checks.append({"rule": "dimensions", "score": 1.0 if dims and str(brief.get("dimensions")) == str(dims) else 0.5,
                   "detail": f"expected {dims}, got {brief.get('dimensions')}"})
    overall = round(sum(c["score"] for c in checks) / len(checks), 3)
    verdict = "pass" if overall >= 0.8 else "review" if overall >= 0.5 else "fail"
    payload = {"direction_project": direction.get("project"), "overall_score": overall,
               "verdict": verdict, "checks": checks}
    name = core.slugify(f"{direction.get('project', 'direction')}-adherence")
    outs = [core.emit_json(ctx, name, payload)]
    ctx.audit("direction.checked", verdict=verdict, score=overall)
    return {"data": payload, "outputs": outs}


# --------------------------------------------------------------------------
# 7. Style guide builder (brand / world / illustration / photography)
# --------------------------------------------------------------------------


def op_styleguide(ctx, a):
    kind = getattr(a, "kind", None) or "brand"
    if kind not in ("brand", "world", "illustration", "photography"):
        raise core.usage_error("--kind must be brand, world, illustration or photography")
    pp = prompt_presets(ctx.config)
    brief = None
    if getattr(a, "brief", None) or getattr(a, "input", None):
        brief = load_brief(a, ctx)
    seeds = brief.get("palette") if brief and brief.get("palette") else None
    if getattr(a, "colors", None):
        seeds = [c.strip() for c in str(a.colors).split(",") if c.strip()]
    palette = seeds or pp["palettes"]["warm-neutral"]["colors"]
    roles = assign_palette_roles(palette)
    scale = {"display": 64, "h1": 44, "h2": 32, "h3": 24, "body": 17, "caption": 13}
    guide = {
        "kind": kind,
        "project": (brief or {}).get("project") or getattr(a, "project", None) or "untitled-project",
        "palette": roles,
        "type_scale_px": scale,
        "type_rules": ["one display face plus one body face", "no more than three weights in use",
                       "display tracking tight, body tracking normal", "minimum body size 13px at 1x"],
        "imagery_rules": imagery_rules(kind),
        "motifs": motif_library(kind),
        "do_dont": do_dont(kind),
        "accessibility": pp["accessibility"],
        "tokens": {
            "spacing": [4, 8, 12, 16, 24, 32, 48, 64],
            "radius_px": [0, 4, 8, 16, 999],
            "stroke_px": [1, 1.5, 2, 3],
            "grain_percent": 3,
        },
        "created_at": core.now_iso(),
    }
    name = core.slugify(f"{guide['project']}-{kind}-style-guide")
    md = [f"# {kind.title()} style guide — {guide['project']}", "", "## Palette"]
    md += [f"- **{role}**: {value}" for role, value in roles.items()]
    md += ["", "## Typography", f"Scale (px): {scale}", ""]
    md += [f"- {rule}" for rule in guide["type_rules"]]
    md += ["", "## Imagery rules"]
    md += [f"- {rule}" for rule in guide["imagery_rules"]]
    md += ["", "## Motifs"]
    md += [f"- {m['name']}: {m['description']}" for m in guide["motifs"]]
    md += ["", "## Do / do not"]
    md += [f"- **Do**: {d}" for d in guide["do_dont"]["do"]]
    md += [f"- **Do not**: {d}" for d in guide["do_dont"]["dont"]]
    outs = [core.emit_json(ctx, name, guide), core.emit_text(ctx, name, "md", "\n".join(md) + "\n"),
            core.emit_text(ctx, name, "svg", swatch_svg(roles))]
    ctx.audit("styleguide.created", kind=kind)
    return {"data": guide, "outputs": outs}


def assign_palette_roles(colors):
    hexes = [c if c.startswith("#") else "#" + c for c in colors][:6]
    while len(hexes) < 4:
        hexes.append(core.harmonize(hexes, 4)[-1])
    lum = sorted(hexes, key=lambda h: core.relative_luminance(core.hex_to_rgb(h)))
    return {
        "primary": hexes[0],
        "primary_dark": lum[0],
        "primary_light": lum[-1],
        "secondary": hexes[1] if len(hexes) > 1 else hexes[0],
        "accent": hexes[2] if len(hexes) > 2 else hexes[1],
        "neutral_dark": lum[1],
        "neutral_light": lum[-2],
        "surface": "#ffffff",
        "text_on_primary": "#ffffff" if core.contrast_ratio(core.hex_to_rgb(hexes[0]), (255, 255, 255)) >= 4.5 else "#111111",
    }


def imagery_rules(kind):
    base = ["subject occupies 40-70 percent of the frame unless the use is product-led",
            "single dominant light direction, no mixed colour temperature",
            "no text baked into imagery unless the layout reserves a type-safe zone"]
    extra = {
        "brand": ["imagery must sit beside the mark without competing for attention",
                  "product truth before mood: no invented features"],
        "world": ["every object must be explainable by the world's material and technology rules",
                  "repeat architectural and costume motifs across all images of the same culture"],
        "illustration": ["stroke weight stays within a 2:1 range across the series",
                         "shadow shapes are flat or hatched, never photographic blur"],
        "photography": ["depth of field motivated by subject scale", "no impossible reflections or floating shadows",
                        "skin and fabric retain texture at 100 percent"],
    }
    return base + extra.get(kind, [])


def motif_library(kind):
    common = [
        {"name": "primary arc", "description": "single sweeping curve echoing the mark's curvature"},
        {"name": "grid field", "description": "regular dot or line field at 8px rhythm for backgrounds"},
        {"name": "corner bracket", "description": "L-shaped frame element used to hold crops together"},
    ]
    special = {
        "brand": [{"name": "mark container", "description": "clear-space ring at 1x mark height"}],
        "world": [{"name": "faction glyph", "description": "three-stroke identifier system per culture"}],
        "illustration": [{"name": "hatch band", "description": "45-degree hatch strip used for shadow masses"}],
        "photography": [{"name": "prop pair", "description": "recurring two-object prop set for continuity"}],
    }
    return common + special.get(kind, [])


def do_dont(kind):
    return {
        "do": [f"apply the {kind} palette at full saturation for accents only",
               "keep one visual hierarchy per image",
               "reuse the motif set before inventing new decoration"],
        "dont": ["mix more than two type families", "stretch or recolour the master mark",
                 "introduce a new colour without a documented role"],
    }


def swatch_svg(roles):
    items = [(k, v) for k, v in roles.items() if str(v).startswith("#")]
    width = 120 * len(items)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="150" viewBox="0 0 {width} 150">',
             f'<rect width="{width}" height="150" fill="#111417"/>']
    for i, (role, hexv) in enumerate(items):
        x = i * 120
        parts.append(f'<rect x="{x + 10}" y="18" width="100" height="70" fill="{hexv}" stroke="#2b3540"/>')
        parts.append(f'<text x="{x + 10}" y="106" fill="#e8edf2" font-family="sans-serif" font-size="11">{role}</text>')
        parts.append(f'<text x="{x + 10}" y="124" fill="#8ea1b3" font-family="sans-serif" font-size="11">{hexv}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# 8. Reference-aware analysis
# --------------------------------------------------------------------------


def op_reference(ctx, a):
    paths = list(getattr(a, "refs", None) or [])
    if getattr(a, "input", None):
        paths.append(a.input)
    if not paths:
        raise core.usage_error("The 'reference' command needs --refs a.png[,b.png] or --input a.png.",
                               "Supply one or more reference images.")
    max_refs = 12
    if len(paths) > max_refs:
        raise core.usage_error(f"At most {max_refs} references per analysis (got {len(paths)})")
    analyses = []
    for raw in paths:
        for item in str(raw).split(","):
            item = item.strip()
            if not item:
                continue
            p = core.resolve_input(item)
            img = core.open_image(p, ctx)
            analyses.append(analyze_reference(img, p))
    aggregate = {
        "references": analyses,
        "palette": merge_palettes([r["palette"] for r in analyses], 6),
        "aspect": analyses[0]["aspect"],
        "transfer_parameters": build_transfer_params(analyses),
        "rights_notes": [
            "References are analysed for structure, palette and material response only.",
            "No identity, likeness or ownership claim is made about any depicted subject.",
            "Record the licence for every reference before publishing derived work.",
        ],
    }
    if any(r["identity_risk"] > 0.18 for r in analyses):
        aggregate["warnings"] = ["skin-tone coverage suggests a person may be present: treat the reference as identity-sensitive and confirm consent before any likeness edit."]
        ctx.note(aggregate["warnings"][0])
    name = core.slugify(f"{getattr(a, 'project', None) or 'reference'}-analysis")
    md = ["# Reference analysis", "",
          f"References analysed: {len(analyses)}", "", "## Per reference"]
    for r in analyses:
        md += [f"### {Path(r['file']).name}",
               f"- Size: {r['width']}x{r['height']} ({r['aspect']})",
               f"- Tone: mean {r['tone']['mean']}, dynamic range {r['tone']['dynamic_range']}",
               f"- Edge density: {r['edge_density']}, gradient share: {r['gradient_share']}",
               f"- Palette: {', '.join(c['hex'] for c in r['palette'])}",
               f"- Texture class: {r['texture_class']}",
               f"- Identity risk score: {r['identity_risk']}",
               ""]
    md += ["## Transfer parameters", "", "```json",
           json.dumps(aggregate["transfer_parameters"], indent=2), "```", "",
           "## Rights notes"] + [f"- {n}" for n in aggregate["rights_notes"]]
    if aggregate.get("warnings"):
        md += ["", "## Warnings"] + [f"- {w}" for w in aggregate["warnings"]]
    outs = [core.emit_json(ctx, name, aggregate), core.emit_text(ctx, name, "md", "\n".join(md) + "\n")]
    ctx.audit("reference.analysed", count=len(analyses))
    return {"data": aggregate, "outputs": outs}


def analyze_reference(img, path):
    palette = core.dominant_colors(img, 5)
    tone = core.tone_stats(img)
    edges = core.edge_density(img)
    skin = core.skin_ratio(img)
    grad = gradient_share(img)
    texture = ("photographic" if edges > 0.10 and tone["stddev"] > 35 else
               "illustrative-flat" if edges > 0.06 else "smooth-gradient")
    return {
        "file": core.rel_posix(path),
        "width": img.width,
        "height": img.height,
        "aspect": core.aspect_label(img.width, img.height),
        "palette": palette,
        "tone": tone,
        "edge_density": edges,
        "gradient_share": grad,
        "texture_class": texture,
        "identity_risk": skin,
        "sharpness": core.laplacian_variance(img),
        "channel_means": core.channel_means(img),
    }


def gradient_share(img):
    """Share of pixels sitting in locally smooth areas (proxy for gradient-heavy art)."""
    from PIL import ImageFilter, ImageStat
    small = img.convert("L")
    small.thumbnail((256, 256))
    edges = small.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist) or 1
    return round(1 - (sum(hist[24:]) / total), 4)


def merge_palettes(palettes, count):
    seen, merged = set(), []
    for pal in palettes:
        for entry in pal:
            key = entry["hex"].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged[:count]


def build_transfer_params(analyses):
    tones = [a["tone"] for a in analyses]
    edges = [a["edge_density"] for a in analyses]
    return {
        "palette": merge_palettes([a["palette"] for a in analyses], 5),
        "target_mean_luma": round(sum(t["mean"] for t in tones) / len(tones), 1),
        "target_stddev": round(sum(t["stddev"] for t in tones) / len(tones), 1),
        "grain": round(min(0.06, 0.02 + sum(edges) / len(edges) * 0.2), 3),
        "vignette": 0.22 if sum(t["mean"] for t in tones) / len(tones) < 110 else 0.12,
        "contrast_curve": ("lift shadows 3 percent, hold highlights"
                           if sum(t.get("black_clip_pct", 0) for t in tones) / len(tones) > 4
                           else "neutral"),
        "sharpness_target": round(sum(a["sharpness"] for a in analyses) / len(analyses), 1),
        "aspect": analyses[0]["aspect"],
    }


# --------------------------------------------------------------------------
# 9. Colour palette developer
# --------------------------------------------------------------------------


def rgb_to_cmyk_naive(rgb):
    r, g, b = (c / 255.0 for c in rgb[:3])
    k = 1 - max(r, g, b)
    if k >= 1:
        return [0, 0, 0, 100]
    c = (1 - r - k) / (1 - k)
    m = (1 - g - k) / (1 - k)
    y = (1 - b - k) / (1 - k)
    return [round(v * 100, 1) for v in (c, m, y, k)]


def op_palette(ctx, a):
    pp = prompt_presets(ctx.config)
    seeds = []
    if getattr(a, "colors", None):
        seeds = [c.strip() for c in str(a.colors).split(",") if c.strip()]
    if getattr(a, "input", None):
        img = core.open_image(core.resolve_input(a.input), ctx)
        seeds += [c["hex"] for c in core.dominant_colors(img, 5)]
    if not seeds:
        preset = getattr(a, "preset", None) or "warm-neutral"
        if preset not in pp["palettes"]:
            raise core.usage_error(f"Unknown --preset '{preset}'",
                                   "Choose: " + ", ".join(sorted(pp["palettes"])))
        seeds = pp["palettes"][preset]["colors"]
    for value in seeds:
        if not re.match(r"^#?[0-9a-fA-F]{6}$|^#?[0-9a-fA-F]{3}$", value):
            raise core.input_error(f"Invalid colour '{value}'", "Use #rrggbb hex values.")
    palette = [s if s.startswith("#") else "#" + s for s in seeds][:6]
    strategy = getattr(a, "strategy", None) or "analogous"
    if strategy not in ("analogous", "complementary", "triadic", "split", "monochrome"):
        raise core.usage_error("--strategy must be analogous, complementary, triadic, split or monochrome")
    derived = core.harmonize(palette, 5, strategy)
    roles = assign_palette_roles(palette + derived[1:])
    pairs = []
    for name, hexv in roles.items():
        if not str(hexv).startswith("#"):
            continue
        for text_hex in ("#ffffff", "#111111"):
            ratio = core.contrast_ratio(core.hex_to_rgb(hexv), core.hex_to_rgb(text_hex))
            pairs.append({"surface": name, "surface_hex": hexv, "text": text_hex,
                          "ratio": ratio,
                          "wcag_aa": ratio >= 4.5, "wcag_aa_large": ratio >= 3.0,
                          "wcag_aaa": ratio >= 7.0})
    accessible = [p for p in pairs if p["text"] == "#ffffff" and p["wcag_aa"]]
    payload = {
        "seeds": palette,
        "strategy": strategy,
        "derived": derived,
        "roles": roles,
        "print_equivalents": {name: rgb_to_cmyk_naive(core.hex_to_rgb(hexv))
                              for name, hexv in roles.items() if str(hexv).startswith("#")},
        "print_note": "CMYK values are naive conversions without an ICC profile. Always convert with the printer's profile and proof before production.",
        "contrast_pairs": pairs,
        "accessible_text_options": len(accessible),
        "recommendations": [
            "use the darkest role for body text and the lightest for surfaces",
            "limit accents to 10 percent of any layout",
            "pair colour with shape or label so meaning is never colour-only",
        ],
    }
    name = core.slugify(f"{getattr(a, 'project', None) or 'palette'}-{strategy}")
    md = [f"# Colour palette ({strategy})", "", "## Seeds", ", ".join(palette), "", "## Roles"]
    md += [f"- **{k}**: {v}" for k, v in roles.items()]
    md += ["", "## Contrast pairs (WCAG)"]
    for p in pairs:
        flag = "AA" if p["wcag_aa"] else ("AA-large only" if p["wcag_aa_large"] else "fail")
        md.append(f"- {p['surface_hex']} on {p['text']}: {p['ratio']}:1 — {flag}")
    md += ["", "## Print equivalents (naive CMYK)", "```json",
           json.dumps(payload["print_equivalents"], indent=2), "```", "", payload["print_note"]]
    outs = [core.emit_json(ctx, name, payload), core.emit_text(ctx, name, "md", "\n".join(md) + "\n"),
            core.emit_text(ctx, name, "svg", swatch_svg(roles))]
    ctx.audit("palette.developed", strategy=strategy, seeds=len(palette))
    return {"data": payload, "outputs": outs}
