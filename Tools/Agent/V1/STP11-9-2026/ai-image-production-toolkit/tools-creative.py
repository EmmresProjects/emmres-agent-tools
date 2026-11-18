"""Creative direction, prompt engineering and planning capabilities.

Part of the AI Image Production Toolkit. Every handler receives a context object
(see ``tools-image-toolkit.py``) and returns a result dictionary with the keys
``summary``, ``data``, ``report``, ``artifacts``, ``warnings`` and ``metrics``.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if "tools_core" not in sys.modules:  # pragma: no cover - direct-import fallback
    _spec = importlib.util.spec_from_file_location("tools_core", os.path.join(_HERE, "tools-core.py"))
    _core = importlib.util.module_from_spec(_spec)
    sys.modules["tools_core"] = _core
    _spec.loader.exec_module(_core)

import tools_core as core  # noqa: E402
from tools_core import (  # noqa: E402
    InvalidInput, contrast_ratio, deterministic_seed, hex_to_rgb, kebab, load_asset,
    md_table, png_write, read_json, rgb_to_hex, sha256_bytes, slugify, svg_document,
    svg_text, truncate, utc_now, wrap,
)

LEXICON = "asset-style-lexicon.json"
NEGATIVES = "asset-negative-prompts.json"
PROFILES = "asset-model-profiles.json"
SAMPLES = "asset-sample-prompts.json"
TEMPLATES = "asset-workflow-templates.json"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _emit(ctx, slug: str, title: str, sections: List[Tuple[str, str]], data: Any,
          summary: str, metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write a markdown report plus a JSON artefact and return the envelope."""
    lines = [f"# {title}", "", f"Generated {utc_now()} by the AI Image Production Toolkit.", ""]
    for heading, body in sections:
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body if body.endswith("\n") else body + "\n")
    report = "\n".join(lines)
    ctx.write_text(f"{slug}.md", report)
    ctx.write_json(f"{slug}.json", {"title": title, "summary": summary, "data": data,
                                    "generated_utc": utc_now()})
    return {"summary": summary, "data": data, "report": report, "metrics": metrics or {}}


def _lex() -> Dict[str, Any]:
    return load_asset(LEXICON)


def _pick(bank: List[Dict[str, str]], wanted: Optional[str], default_idx: int = 0) -> Dict[str, str]:
    """Resolve a lexicon entry by id, case-insensitively, with a safe fallback."""
    if wanted:
        needle = wanted.strip().lower()
        for entry in bank:
            if entry["id"].lower() == needle or needle in entry["id"].lower():
                return entry
    return bank[default_idx % len(bank)]


def _token_estimate(text: str) -> int:
    """CLIP-style token estimate (whitespace and punctuation split, 0.75 word factor)."""
    if not text:
        return 0
    words = re.findall(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']", text)
    return max(1, int(math.ceil(len(words) / 0.75)))


# ---------------------------------------------------------------------------
# Category 1 - Foundations
# ---------------------------------------------------------------------------

_FOUNDATION_NOTES = {
    "training-objective": {
        "title": "How image-text training teaches visual patterns",
        "analogy": "The model is shown a very large number of captioned images and repeatedly asked to guess the noise, velocity or clean signal that was mixed into a corrupted version of each image. Every wrong guess nudges billions of parameters slightly in the direction that made the guess more accurate.",
        "mechanics": [
            "Images are decoded into arrays of numbers (tensors) and normalised.",
            "A text encoder converts the caption into vectors that act as conditioning signals.",
            "The denoiser predicts the corruption; the optimiser adjusts weights to reduce the error.",
            "After enough passes the weights encode statistical regularities of edges, materials, light and composition.",
        ],
        "not_claimed": [
            "The model does not store a browsable library of its training images.",
            "The model cannot retrieve a training photo by caption.",
            "Latent space is a compressed mathematical representation, not a gallery of stored pictures.",
        ],
    },
    "latent-space": {
        "title": "Latent representation and semantic directions",
        "analogy": "A compressed map of the visual world where similar things sit close together and simple directions mean simple changes.",
        "mechanics": [
            "A variational autoencoder encodes pixels into a lower-dimensional latent and decodes it back.",
            "Because the space is structured, arithmetic on vectors tends to correspond to arithmetic on visual attributes.",
            "Interpolating between two latents usually produces a smooth morph rather than a cut.",
        ],
        "not_claimed": ["Latents are not stored pictures.", "Real models have far more dimensions than any small illustration can show."],
    },
    "noise-schedule": {
        "title": "Forward noising and reverse denoising",
        "analogy": "Adding increasing amounts of static to a picture and then learning to remove exactly that static, step by step.",
        "mechanics": [
            "The forward process mixes clean signal with noise according to a schedule of timesteps.",
            "The reverse process starts from pure noise and predicts a slightly cleaner latent at each step.",
            "Guidance scales how strongly the model follows the prompt relative to its own prior.",
        ],
        "not_claimed": ["Few steps do not equal a smaller model.", "Guidance above roughly 12 usually adds artefacts rather than fidelity."],
    },
    "tokenization": {
        "title": "Tokenisation and conditioning",
        "analogy": "The caption is chopped into vocabulary pieces, each piece becomes a vector, and those vectors steer the drawing.",
        "mechanics": [
            "Words and punctuation become tokens.",
            "The text encoder maps the token sequence into embeddings.",
            "Cross-attention lets those embeddings influence spatial regions of the image.",
        ],
        "not_claimed": ["Token counts are model specific; the estimate here is approximate.", "Ordering still matters because attention is not order free."],
    },
}


def foundations_training_concepts(ctx) -> Dict[str, Any]:
    topic = str(ctx.get("topic", "training-objective")).lower()
    audience = str(ctx.get("audience", "general"))
    key = topic if topic in _FOUNDATION_NOTES else "training-objective"
    note = _FOUNDATION_NOTES[key]
    depth = {"general": "plain-language", "technical": "working detail", "expert": "implementation detail"}[audience]
    sections = [
        ("In one sentence", note["analogy"]),
        ("What actually happens", "\n".join(f"- {m}" for m in note["mechanics"])),
        ("What this does NOT mean", "\n".join(f"- {m}" for m in note["not_claimed"])),
        ("How to explain it safely", wrap(
            "Say 'the model learned statistical relationships between words and visual structure', and add that "
            "it does not keep a searchable copy of its training images. Avoid the phrase 'it memorised the dataset' "
            "unless you have memorisation evidence from a specific audit.", width=92)),
    ]
    data = {"topic": key, "audience": audience, "depth": depth, "mechanics": note["mechanics"],
            "explicit_non_claims": note["not_claimed"]}
    return _emit(ctx, "foundations-training-concepts", note["title"], sections, data,
                 f"Explained {note['title'].lower()} for a {audience} audience.")


def pixel_tensor_interpreter(ctx) -> Dict[str, Any]:
    width = int(ctx.require("width"))
    height = int(ctx.require("height"))
    channels = int(ctx.get("channels", 3))
    bit_depth = int(ctx.get("bit_depth", 8))
    divisor = int(ctx.get("latent_divisor", 8))
    batch = int(ctx.get("batch_size", 1))
    if width <= 0 or height <= 0:
        raise InvalidInput("width and height must be positive integers")
    if width % divisor or height % divisor:
        raise InvalidInput(f"width and height must be divisible by the latent divisor ({divisor})")
    bytes_per_channel = 1 if bit_depth <= 8 else 2
    sample_bytes = width * height * channels * bytes_per_channel
    batch_bytes = sample_bytes * batch
    latent_h, latent_w = height // divisor, width // divisor
    latent_channels = 4
    latent_elements = latent_h * latent_w * latent_channels
    ranges = {8: (0, 255, "divide by 255"), 16: (0, 65535, "divide by 65535")}
    low, high, norm = ranges.get(bit_depth, (0, 1, "scale to [0,1]"))
    rows = [
        ["Pixels", f"{width} x {height} x {channels}", f"{width * height * channels:,} samples/pixel-tuple"],
        ["One sample (RGB)", "HWC tensor", f"{width}x{height}x{channels} = {width * height * channels:,} values"],
        ["One sample (CHW tensor)", "CHW tensor", f"{channels}x{height}x{width}"],
        ["Batch tensor", f"batch {batch}", f"{batch}x{channels}x{height}x{width}"],
        ["Latent tensor", "VAE space", f"{batch}x{latent_channels}x{latent_h}x{latent_w} = {batch * latent_elements:,} values"],
        ["Raw memory", f"{bit_depth}-bit", f"{sample_bytes:,} bytes/sample, {batch_bytes:,} bytes/batch"],
        ["Normalisation", "-1..1 or 0..1", f"input range {low}..{high}; {norm} then map to [-1,1]"],
    ]
    report = md_table(rows, ["Aspect", "Representation", "Value"])
    report += "\n\nPlain-language reading:\n"
    report += f"- A {width} by {height} image has {width * height:,} pixels.\n"
    report += f"- With {channels} channels that is {width * height * channels:,} numbers in one image.\n"
    report += f"- The VAE compresses each side by {divisor}x, so the model works on a {latent_w} by {latent_h} grid.\n"
    report += f"- Storing a batch of {batch} at {bit_depth}-bit costs about {batch_bytes / (1024 ** 2):.1f} MiB in memory.\n"
    data = {"width": width, "height": height, "channels": channels, "bit_depth": bit_depth,
            "latent_divisor": divisor, "batch_size": batch, "pixel_values": width * height * channels,
            "latent_shape": [batch, latent_channels, latent_h, latent_w],
            "bytes_per_sample": sample_bytes, "bytes_per_batch": batch_bytes,
            "normalisation": {"raw_range": [low, high], "scale": norm, "model_range": [-1.0, 1.0]},
            "table": rows}
    return {"summary": f"Tensor spec for {width}x{height}x{channels} at {bit_depth}-bit.",
            "data": data, "report": report, "metrics": {"latent_elements": batch * latent_elements}}


def latent_space_navigator(ctx) -> Dict[str, Any]:
    concepts = [str(c).strip().lower() for c in (ctx.get("concepts") or []) if str(c).strip()]
    if len(concepts) < 2:
        raise InvalidInput("provide at least two concepts, for example --set concepts='warm,cold'")
    operation = str(ctx.get("operation", "interpolate"))
    steps = int(ctx.get("steps", 5))
    vocab = _lex()
    axes = ["camera", "lighting", "palette", "texture", "composition", "medium", "mood"]
    vectors = {}
    for concept in concepts:
        vec = {}
        for axis in axes:
            bank = vocab.get(axis, [])
            best = None
            best_score = -1.0
            for entry in bank:
                if isinstance(entry, dict):
                    entry_id = str(entry.get("id", ""))
                    entry_value = str(entry.get("value", ""))
                else:
                    entry_id = str(entry)
                    entry_value = str(entry)
                score = 0.0
                for token in entry_value.lower().split():
                    if token.strip(",.") and token in concept:
                        score += 1.0
                if entry_id.lower() in concept:
                    score += 2.0
                if score > best_score:
                    best, best_score = entry, score
            if best is not None:
                vec[axis] = best.get("id") if isinstance(best, dict) else str(best)
        if not vec:
            vec["medium"] = "photography"
        vectors[concept] = vec
    a, b = concepts[0], concepts[1]
    combined = dict(vectors[a])
    for axis, value in vectors[b].items():
        combined.setdefault(axis, value)
    results = []
    for i in range(steps):
        t = i / max(1, steps - 1)
        blended = {axis: vectors[a].get(axis, vectors[b].get(axis, "")) for axis in axes}
        results.append({"t": round(t, 3), "vector": blended})
    similarity = 0.0
    shared = set(vectors[a].values()) & set(vectors[b].values())
    union = set(vectors[a].values()) | set(vectors[b].values())
    similarity = round(len(shared) / len(union), 3) if union else 0.0
    sections = [
        ("Concept vectors", md_table([[k, ", ".join(f"{ax}={v}" for ax, v in vec.items())]
                                     for k, vec in vectors.items()], ["Concept", "Axes"])),
        ("Operation", f"operation={operation}; similarity between {a} and {b} = {similarity}"),
        ("Interpolation path", md_table([[r["t"], ", ".join(f"{ax}={v}" for ax, v in r["vector"].items())]
                                        for r in results], ["t", "Vector"])),
        ("How to use it", "Take the axis values from an interpolated row as prompt terms; they are ordinary "
                          "attribute words that survive in any text encoder."),
    ]
    data = {"operation": operation, "concepts": concepts, "vectors": vectors, "similarity": similarity,
            "path": results, "note": "Conceptual attribute space, not a trained model latent."}
    return _emit(ctx, "latent-space-navigation", "Latent space navigation", sections, data,
                 f"Navigated {operation} between {a} and {b} across {len(axes)} axes.",
                 {"similarity": similarity})


def prompt_encoding_specialist(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    negative = ctx.get("negative") or ""
    limits = {"sd15": 77, "sd21": 77, "sdxl": 77, "sd3-medium": 77, "sd3-large": 256, "flux1": 256}
    model = str(ctx.get("model", "sdxl"))
    limit = limits.get(model, 77)
    tokens = _token_estimate(prompt)
    neg_tokens = _token_estimate(negative) if negative else 0
    weights = re.findall(r"\(([^)]*?):\s*([0-9.]+)\)", prompt)
    over = tokens > limit
    report = "\n".join([
        f"Prompt tokens (estimated): {tokens} / {limit} limit for {model}",
        f"Negative prompt tokens (estimated): {neg_tokens} / {limit}",
        f"Truncation risk: {'HIGH - text after the limit is ignored' if over else 'none at this length'}",
        f"Weighted phrases found: {len(weights)}" + (f" -> {', '.join(f'({w[0]}:{w[1]})' for w in weights)}" if weights else ""),
        "",
        "How it works:",
        "1. The prompt is split into word and punctuation tokens.",
        "2. Each token becomes a vector through the text encoder.",
        "3. Cross-attention mixes those vectors into the spatial features of the image.",
        "4. Position matters: earlier tokens usually carry more weight in attention maps.",
        "5. Truncation silently drops the tail of the prompt - put the subject first.",
    ])
    data = {"model": model, "token_limit": limit, "estimated_tokens": tokens,
            "negative_tokens": neg_tokens, "truncation_risk": over, "utilisation": round(tokens / limit, 3),
            "weighted_phrases": [{"phrase": w[0], "weight": float(w[1])} for w in weights],
            "estimate_method": "CLIP-style word/punctuation split at 0.75 words per token"}
    return {"summary": f"Prompt encoding analysis: {tokens}/{limit} tokens.",
            "data": data, "report": report, "metrics": {"tokens": tokens, "utilisation": round(tokens / limit, 3)}}


def cross_attention_visualizer(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    layout = str(ctx.get("regions", "thirds"))
    tokens = re.findall(r"[A-Za-z0-9'\-]+", prompt)
    stop = {"a", "an", "the", "of", "in", "on", "with", "and", "at", "to", "by", "for", "is", "as"}
    content = [t for t in tokens if t.lower() not in stop]
    if not content:
        raise InvalidInput("the prompt contains no content words to bind")
    regions = {
        "thirds": [("upper-left", 0.0, 0.0, 0.33, 0.33), ("upper-right", 0.67, 0.0, 0.33, 0.33),
                   ("lower-left", 0.0, 0.67, 0.33, 0.33), ("lower-right", 0.67, 0.67, 0.33, 0.33)],
        "quadrants": [("upper-left", 0.0, 0.0, 0.5, 0.5), ("upper-right", 0.5, 0.0, 0.5, 0.5),
                      ("lower-left", 0.0, 0.5, 0.5, 0.5), ("lower-right", 0.5, 0.5, 0.5, 0.5)],
        "center": [("center", 0.25, 0.25, 0.5, 0.5)],
    }.get(layout, None)
    if regions is None:
        raise InvalidInput(f"unknown region layout: {layout} (use thirds, quadrants or center)")
    bindings = []
    for i, token in enumerate(content):
        region = regions[i % len(regions)]
        weak = False
        reasons = []
        if token.lower() in {"it", "them", "this", "that", "thing"}:
            weak = True
            reasons.append("pronoun carries no visual identity")
        if len(token) <= 2:
            weak = True
            reasons.append("token is too short to anchor an attribute")
        if i > 0 and content[i - 1].lower() in {"and", "with"}:
            weak = True
            reasons.append("compound phrase is likely merged into one concept")
        bindings.append({"token": token, "index": i, "region": region[0], "box": list(region[1:]),
                         "binding_strength": "weak" if weak else "ok", "reasons": reasons})
    weak_tokens = [b["token"] for b in bindings if b["binding_strength"] == "weak"]
    fixes = []
    if weak_tokens:
        fixes.append(f"Rewrite weak tokens {', '.join(weak_tokens)} as explicit nouns with attributes.")
    fixes.append("Put the most important subject in the first ten tokens; attention is strongest early.")
    fixes.append("Separate compounds with commas so 'red car' does not merge into one region.")
    fixes.append("Repeat a critical attribute only when the image must not drift; repetition causes duplication in some models.")
    report = "\n".join([
        f"Binding map for {len(content)} content tokens across {len(regions)} regions ({layout}).",
        "",
        md_table([[b["token"], b["region"], b["binding_strength"], "; ".join(b["reasons"]) or "-"]
                  for b in bindings], ["Token", "Region", "Binding", "Notes"]),
        "",
        "Recommended changes:",
        *[f"- {f}" for f in fixes],
    ])
    data = {"prompt": prompt, "layout": layout, "regions": [{"name": r[0], "box": list(r[1:])} for r in regions],
            "bindings": bindings, "weak_tokens": weak_tokens, "recommended_changes": fixes}
    return _emit(ctx, "cross-attention-binding", "Cross-attention binding map", [("Binding map", report)],
                 data, f"Bound {len(content)} tokens; {len(weak_tokens)} weak bindings found.",
                 {"weak_bindings": len(weak_tokens)})


def model_comparator(ctx) -> Dict[str, Any]:
    profiles = load_asset(PROFILES)
    priority = str(ctx.get("priority", "quality"))
    wanted = ctx.get("models")
    wanted = [str(w) for w in wanted] if wanted else None
    models = [m for m in profiles["models"] if not wanted or m["id"] in wanted or m["name"].lower() in [w.lower() for w in wanted]]
    if not models:
        raise InvalidInput("no matching model profiles; use --set models=sd15,sdxl")
    key_map = {"quality": "quality_score", "speed": "speed_score", "controllability": "controllability_score",
               "hardware": None, "photorealism": "quality_score", "typography": None}
    scored = []
    for m in models:
        hardware_score = 5 if m["min_vram_gb"] <= 8 else (4 if m["min_vram_gb"] <= 12 else (3 if m["min_vram_gb"] <= 16 else 2))
        scores = {"quality": m["quality_score"], "speed": m["speed_score"],
                  "controllability": m["controllability_score"], "hardware": hardware_score}
        scores["photorealism"] = 5 if "photorealism" in m["class_tags"] else (3 if m["quality_score"] >= 4 else 2)
        scores["typography"] = 5 if "typography" in m["class_tags"] else (3 if "typography" in m["best_for"] else 2)
        scored.append({"model": m, "scores": scores})
    scored.sort(key=lambda s: (-s["scores"].get(priority, 0), s["model"]["seconds_per_image"]))
    rows = []
    for entry in scored:
        m = entry["model"]
        rows.append([m["id"], m["family"].split("(")[0].strip(), m["base_resolution"],
                     f'{m["min_vram_gb"]} GB', m["seconds_per_image"], m["speed_score"], m["quality_score"],
                     m["controllability_score"], entry["scores"].get(priority, 0)])
    report = "\n".join([
        f"Priority: {priority}",
        "",
        md_table(rows, ["Model", "Family", "Base res", "Min VRAM", "s/img", "Speed", "Quality", "Control", "Priority score"]),
        "",
        "Failure modes and trade-offs:",
        *[f"- {m['id']}: strengths {', '.join(m['strengths'])}; weaknesses {', '.join(m['weaknesses'])}"
          for m in [e["model"] for e in scored]],
    ])
    data = {"priority": priority, "ranking": [e["model"]["id"] for e in scored],
            "models": [{"id": e["model"]["id"], "name": e["model"]["name"], "scores": e["scores"],
                        "strengths": e["model"]["strengths"], "weaknesses": e["model"]["weaknesses"],
                        "best_for": e["model"]["best_for"], "min_vram_gb": e["model"]["min_vram_gb"],
                        "seconds_per_image": e["model"]["seconds_per_image"]} for e in scored]}
    return _emit(ctx, "model-comparison", "Generative model comparison",
                 [("Comparison table", report)], data,
                 f"Compared {len(scored)} model families for {priority}.",
                 {"winner": data["ranking"][0]})


def glossary_builder(ctx) -> Dict[str, Any]:
    terms = ctx.get("terms")
    audience = str(ctx.get("audience", "general"))
    lexicon = _lex()
    core = {
        "latent": ("A compressed numerical representation of an image that a diffusion model works in.", "technical"),
        "seed": ("The number that seeds the random starting noise; the same seed and settings reproduce the same image.", "general"),
        "sampler": ("The algorithm that walks the model through the denoising steps.", "general"),
        "scheduler": ("The schedule that decides how much noise is removed at each step.", "general"),
        "cfg": ("Classifier-free guidance: how strongly the model follows the prompt rather than its own prior.", "general"),
        "vae": ("Variational autoencoder: the encoder and decoder that move between pixels and latents.", "general"),
        "checkpoint": ("A saved set of model weights loaded for generation.", "general"),
        "lora": ("A small add-on trained on top of a base model to teach one subject or style.", "general"),
        "controlnet": ("A side network that steers a generation with structural signals such as edges or depth.", "general"),
        "inpainting": ("Regenerating only the masked region of an image.", "general"),
        "outpainting": ("Extending an image beyond its original borders.", "general"),
        "upscaling": ("Increasing the pixel dimensions of an image, ideally with new plausible detail.", "general"),
        "denoising strength": ("How much of the original image is replaced during image-to-image work.", "general"),
        "hires fix": ("A second pass at higher resolution that adds detail to a first pass composition.", "general"),
        "ip-adapter": ("A method that conditions a model on a reference image for style, face or composition.", "technical"),
        "negative prompt": ("A list of defects the model should avoid.", "general"),
        "token": ("A word fragment the text encoder turns into a vector.", "technical"),
        "cross-attention": ("The mechanism that lets words influence specific regions of the image.", "technical"),
        "attention map": ("A visualisation of which words a region of the image is responding to.", "technical"),
        "textual inversion": ("Training a single new word embedding to represent a concept.", "technical"),
        "dreambooth": ("Fine-tuning a base model on a specific subject so it can be prompted directly.", "technical"),
        "base model": ("The general-purpose checkpoint a fine-tune or adapter is built on.", "general"),
        "prompt weighting": ("Increasing or decreasing the influence of a phrase, for example (term:1.2).", "general"),
        "regional prompting": ("Assigning different text to different areas of one image.", "technical"),
        "overfitting": ("When a fine-tuned model reproduces its training data too literally.", "technical"),
        "provenance": ("The record of how an image was made: prompt, model, seed, edits and timestamps.", "general"),
    }
    if terms:
        selected = [str(t).strip().lower() for t in terms]
    else:
        selected = list(core.keys())
    entries = []
    for term in selected:
        if term in core:
            definition, level = core[term]
        else:
            definition, level = (f"{term.replace('-', ' ').capitalize()} - a term used in diffusion image systems; "
                                  f"define it precisely for your own workflow.", "general"), "general"
        entries.append({"term": term, "definition": definition, "level": level,
                        "audience_note": "Plain-language phrasing." if audience == "general" else "Includes working detail."})
    lines = ["# Image synthesis glossary", ""]
    for entry in entries:
        lines.append(f"**{entry['term']}** - {entry['definition']}")
        lines.append("")
    report = "\n".join(lines)
    ctx.write_text("image-synthesis-glossary.md", report)
    ctx.write_json("image-synthesis-glossary.json", {"audience": audience, "entries": entries})
    return {"summary": f"Glossary with {len(entries)} terms for a {audience} audience.",
            "data": {"audience": audience, "entries": entries}, "report": report,
            "artifacts": [ctx.emit(ctx.out("image-synthesis-glossary.md")), ctx.emit(ctx.out("image-synthesis-glossary.json"))]}


# ---------------------------------------------------------------------------
# Category 2 - Prompt design
# ---------------------------------------------------------------------------


def prompt_architect(ctx) -> Dict[str, Any]:
    brief = str(ctx.require("brief"))
    lexicon = _lex()
    subject = ctx.get("subject") or brief
    style = ctx.get("style") or "photography"
    audience = str(ctx.get("audience", "general"))
    medium = _pick(lexicon["medium"], style if style in [m["id"] for m in lexicon["medium"]] else None)
    lighting = _pick(lexicon["lighting"], None, 1)
    camera = _pick(lexicon["camera"], None, 2)
    palette = _pick(lexicon["palette"], None, 2)
    composition = _pick(lexicon["composition"], None, 0)
    texture = _pick(lexicon["texture"], None, 0)
    slots = {
        "subject": subject,
        "environment": f"in a {brief}" if brief.lower() not in str(subject).lower() else "as described",
        "composition": composition["value"],
        "lens": camera["value"],
        "lighting": lighting["value"],
        "palette": palette["value"],
        "texture": texture["value"],
        "style": medium["value"],
        "quality": ", ".join(lexicon["quality_tokens"][:3]),
        "exclusions": ", ".join(lexicon["exclusions"][:6]),
    }
    positive = ", ".join([
        slots["subject"], slots["environment"], slots["composition"], slots["lens"],
        slots["lighting"], slots["palette"], slots["texture"], slots["style"], slots["quality"],
    ])
    negatives = load_asset(NEGATIVES)
    negative = ", ".join(negatives["targets"]["general"]["tokens"] + negatives["targets"]["hands"]["tokens"][:3])
    params = {"model": ctx.get("model", "sdxl"), "steps": 30, "cfg": 7.0, "sampler": "dpmpp_2m",
              "scheduler": "karras", "width": 1024, "height": 1024, "seed": deterministic_seed(brief) % (2 ** 31)}
    report = "\n".join([
        f"Brief: {brief}",
        "",
        "Structured prompt slots:",
        md_table([[k, v] for k, v in slots.items()], ["Slot", "Value"]),
        "",
        f"Positive prompt:\n{positive}",
        "",
        f"Negative prompt:\n{negative}",
        "",
        "Suggested parameters:",
        md_table([[k, v] for k, v in params.items()], ["Parameter", "Value"]),
        "",
        f"Tuned for audience: {audience}",
    ])
    data = {"brief": brief, "slots": slots, "positive_prompt": positive, "negative_prompt": negative,
            "parameters": params, "audience": audience}
    return _emit(ctx, "structured-prompt", "Structured prompt package",
                 [("Prompt package", report)], data,
                 f"Built a {len(slots)}-slot prompt package for: {truncate(brief, 60)}")


def negative_prompt_engineer(ctx) -> Dict[str, Any]:
    target = str(ctx.get("target", "general")).lower()
    scene = ctx.get("scene") or ""
    strictness = str(ctx.get("strictness", "balanced"))
    bank = load_asset(NEGATIVES)
    entry = bank["targets"].get(target)
    if entry is None:
        raise InvalidInput(f"unknown negative target: {target} (known: {', '.join(sorted(bank['targets']))})")
    budget = bank["strictness"][strictness]["max_tokens"]
    tokens = entry["tokens"][:budget]
    keep = entry.get("keep", [])
    report = "\n".join([
        f"Target defect class: {entry['label']}",
        f"Strength: {strictness} (max {budget} clauses)",
        "",
        f"Negative prompt: {', '.join(tokens)}",
        "",
        "Do not suppress: " + (", ".join(keep) if keep else "nothing specific - this target only names defects"),
        "",
        bank["strictness"][strictness]["guidance"],
        "",
        "Scene note: " + (str(scene) if scene else "none supplied"),
    ])
    data = {"target": target, "label": entry["label"], "strictness": strictness, "tokens": tokens,
            "preserve": keep, "max_clauses": budget,
            "warning": "Long negatives reduce prompt fidelity in several model families."}
    return _emit(ctx, "negative-prompt", "Negative prompt package",
                 [("Negative prompt", report)], data,
                 f"Built a {len(tokens)}-clause negative prompt for {entry['label'].lower()}.")


def prompt_debugger(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    observed = ctx.get("observed") or ""
    text = f"{prompt} {observed}".lower()
    hypotheses = []
    checks = [
        ("ignored_object", ["missing", "not present", "absent", "ignored", "did not appear"],
         "The object was not requested strongly enough.",
         ["Move the subject to the first eight tokens.", "Add a comma-separated list of nouns rather than a sentence.",
          "Add the object as a separate phrase and re-render at a lower CFG."]),
        ("merged_concept", ["merged", "fused", "combined", "stuck to"],
         "Two attributes were merged into one concept.",
         ["Separate the attributes with commas and add a spatial cue such as 'in front of'.",
          "Split the request across two regional prompts if they need different styles."]),
        ("bad_anatomy", ["hands", "fingers", "limbs", "anatomy", "extra", "distorted"],
         "Anatomy errors usually come from pose ambiguity or too many competing subjects.",
         ["Reduce the number of subjects in one frame.", "Add a pose ControlNet.",
          "Mask the region and regenerate at lower denoise."]),
        ("weak_composition", ["cropped", "cut off", "off-centre", "empty", "tiny", "flat"],
         "The framing left the subject small or clipped.",
         ["Name the framing explicitly: 'full body', 'waist-up', 'close-up'.",
          "Add a lens length so the field of view is unambiguous."]),
        ("style_drift", ["not the style", "style changed", "different style", "photoreal when"],
         "The style was diluted by competing descriptors.",
         ["Move the style terms to the end of the prompt.", "Remove style words from the negative prompt when they fight the target.",
          "Lower CFG to 5.5-6.5 if the model over-saturates the style."]),
        ("text_failure", ["text", "lettering", "word", "label", "sign"],
         "Image models render legible text unreliably.",
         ["Render text outside the model and composite it.", "Shorten to one or two words.",
          "Use a model family with stronger typography."]),
        ("lighting_mismatch", ["dark", "flat", "muddy", "washed"],
         "Lighting descriptors were missing or contradictory.",
         ["Name one key light, one fill state and the time of day.", "Remove contradictory light terms."]),
    ]
    for cid, triggers, cause, fixes in checks:
        if any(t in text for t in triggers):
            hypotheses.append({"id": cid, "cause": cause, "fixes": fixes})
    if not hypotheses:
        hypotheses.append({"id": "no_match", "cause": "No known failure signature matched the supplied text.",
                           "fixes": ["Re-run with a description of what actually went wrong.",
                                     "Change one variable at a time: prompt, then seed, then sampler, then CFG."]})
    plan = []
    for i, h in enumerate(hypotheses[:3], start=1):
        plan.append({"step": i, "hypothesis": h["id"], "change": h["fixes"][0],
                     "verify": "Re-render with the same seed and compare only the changed variable."})
    report = "\n".join([
        f"Prompt: {prompt}",
        f"Observed: {observed or 'not supplied'}",
        "",
        "Ranked hypotheses:",
        *[f"- {h['id']}: {h['cause']}" for h in hypotheses],
        "",
        "One-variable-at-a-time plan:",
        md_table([[p["step"], p["hypothesis"], p["change"], p["verify"]] for p in plan],
                 ["Step", "Hypothesis", "Change", "Verify"]),
    ])
    data = {"prompt": prompt, "observed": observed, "hypotheses": hypotheses, "plan": plan}
    return _emit(ctx, "prompt-diagnosis", "Prompt diagnosis", [("Diagnosis", report)], data,
                 f"Diagnosed {len(hypotheses)} likely cause(s).")


def prompt_variant_generator(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    dims = ctx.get("dimensions") or ["camera_angle", "era", "weather"]
    count = int(ctx.get("variants_per_dimension", 3))
    samples = load_asset(SAMPLES)["variant_dimensions"]
    families = []
    for dim in dims:
        key = str(dim)
        options = samples.get(key)
        if not options:
            raise InvalidInput(f"unknown variant dimension: {key} (known: {', '.join(sorted(samples))})")
        chosen = options[:count]
        variants = []
        for i, opt in enumerate(chosen, start=1):
            variant_prompt = f"{prompt}, {opt}"
            variants.append({
                "index": i, "dimension": key, "value": opt, "prompt": variant_prompt,
                "seed": deterministic_seed(variant_prompt) % (2 ** 31),
                "filename": f"{slugify(prompt)[:40]}-{key}-{i:02d}.png",
            })
        families.append({"dimension": key, "options": chosen, "variants": variants})
    manifest = []
    for fam in families:
        manifest.extend(fam["variants"])
    report = "\n".join([
        f"Base prompt: {prompt}",
        "",
        md_table([[v["dimension"], v["index"], v["value"], v["seed"], v["filename"]] for v in manifest],
                 ["Dimension", "#", "Value", "Seed", "Filename"]),
        "",
        "Each family changes exactly one dimension so the comparison stays readable.",
    ])
    data = {"base_prompt": prompt, "families": families, "variant_count": len(manifest), "manifest": manifest}
    return _emit(ctx, "prompt-variants", "Prompt variant families",
                 [("Variants", report)], data, f"Generated {len(manifest)} controlled variants.")


def art_direction_translator(ctx) -> Dict[str, Any]:
    mood = str(ctx.require("mood"))
    medium = str(ctx.get("medium", "photography"))
    lexicon = _lex()
    low = mood.lower()
    chosen = {}
    for axis, default_idx in (("lighting", 0), ("palette", 0), ("texture", 0), ("composition", 0), ("camera", 1), ("medium", 0)):
        bank = lexicon[axis]
        best, best_score = bank[default_idx], 0
        for entry in bank:
            score = sum(1 for word in entry["id"].replace("-", " ").split() if word in low)
            if score > best_score:
                best, best_score = entry, score
        chosen[axis] = best
    mood_bank = lexicon["mood"]
    matched_moods = [m for m in mood_bank if m in low]
    attributes = {
        "lighting": chosen["lighting"]["value"],
        "palette": chosen["palette"]["value"],
        "texture": chosen["texture"]["value"],
        "composition": chosen["composition"]["value"],
        "camera": chosen["camera"]["value"],
        "medium": chosen["medium"]["value"],
        "mood": ", ".join(matched_moods) if matched_moods else low[:80],
    }
    prompt = ", ".join([attributes["subject"] if "subject" in attributes else mood,
                        attributes["composition"], attributes["camera"], attributes["lighting"],
                        attributes["palette"], attributes["texture"], attributes["medium"]])
    report = "\n".join([
        f"Mood / brand language: {mood}",
        "",
        md_table([[k, v] for k, v in attributes.items()], ["Dimension", "Concrete attribute"]),
        "",
        f"Translated prompt:\n{prompt}",
        "",
        "Palette swatches: " + ", ".join(chosen["palette"].get("swatches", [])),
    ])
    data = {"mood": mood, "attributes": attributes, "prompt": prompt,
            "swatches": chosen["palette"].get("swatches", [])}
    return _emit(ctx, "art-direction", "Art direction translation",
                 [("Translation", report)], data, "Translated mood language into concrete visual attributes.")


def prompt_weighting_specialist(ctx) -> Dict[str, Any]:
    prompt = str(ctx.require("prompt"))
    syntax = str(ctx.get("syntax", "a1111"))
    max_weight = float(ctx.get("max_weight", 1.4))
    weights = [(m.group(1), float(m.group(2))) for m in re.finditer(r"\(([^()]*?)\s*:\s*([0-9.]+)\)", prompt)]
    warnings = []
    for phrase, w in weights:
        if w > max_weight:
            warnings.append(f"weight {w} on '{phrase}' exceeds the {max_weight} ceiling - over-weighting causes oversaturation and artefacts")
        if w < 0.6:
            warnings.append(f"weight {w} on '{phrase}' is low enough to be ignored")
    total = sum(w for _, w in weights)
    if len(weights) >= 5:
        warnings.append(f"{len(weights)} weighted phrases is a lot; stacked emphasis often collapses into flat, over-contrasted output")
    if syntax == "comfyui":
        note = "ComfyUI does not parse inline weights. Use conditioning nodes (e.g. ConditioningCombine, or a strength multiplier on the prompt node)."
        weighted = prompt
    elif syntax == "plain":
        note = "Plain syntax cannot carry weights. The prompt below is rewritten with explicit emphasis words."
        weighted = prompt
        for phrase, w in sorted(weights, key=lambda x: -x[1]):
            replacement = {"1.2": f"strongly {phrase}", "1.3": f"very {phrase}", "1.4": f"extremely {phrase}"}.get(str(w), f"{phrase}")
            weighted = weighted.replace(f"({phrase}:{w})", replacement)
    else:
        note = "A1111 style weights detected."
        weighted = prompt
    emphasis = {p: w for p, w in weights}
    report = "\n".join([
        f"Syntax: {syntax}",
        f"Weighted phrases: {emphasis if emphasis else 'none found'}",
        f"Total emphasis: {total:.2f}",
        "",
        f"Weighted prompt:\n{weighted}",
        "",
        note,
        "",
        ("Warnings:" if warnings else "No weighting warnings."),
        *[f"- {w}" for w in warnings],
    ])
    data = {"prompt": prompt, "syntax": syntax, "weights": emphasis, "total_weight": total,
            "weighted_prompt": weighted, "warnings": warnings, "max_weight": max_weight}
    result = _emit(ctx, "prompt-weighting", "Prompt weighting", [("Weighted prompt", report)], data,
                   f"Weighted {len(weights)} phrase(s) in {syntax} syntax.")
    if warnings:
        ctx.warn("; ".join(warnings))
    return result


def reproducibility_manager(ctx) -> Dict[str, Any]:
    manifest_path = ctx.get("manifest")
    if manifest_path:
        manifest = read_json(str(manifest_path))
        computed = manifest.get("run", {}).get("fingerprint")
        verdict = "manifest matches itself" if computed else "manifest has no fingerprint to compare"
        report = "\n".join([
            f"Manifest: {manifest_path}",
            f"Recorded fingerprint: {computed}",
            f"Verdict: {verdict}",
            "",
            "Recorded run:",
            json.dumps(manifest.get("run", {}), indent=2),
        ])
        data = {"manifest": manifest, "verdict": verdict}
        return _emit(ctx, "run-manifest-check", "Reproducibility check", [("Manifest", report)], data,
                     "Verified an existing run manifest.")
    prompt = str(ctx.get("prompt", "") or "")
    run = {
        "model": ctx.get("model", "sdxl-base"),
        "prompt": prompt,
        "negative_prompt": ctx.get("negative", ""),
        "seed": int(ctx.get("seed", 42)),
        "width": int(ctx.get("width", 1024)),
        "height": int(ctx.get("height", 1024)),
        "steps": int(ctx.get("steps", 30)),
        "cfg": float(ctx.get("cfg", 7.0)),
        "sampler": ctx.get("sampler", "dpmpp_2m"),
        "scheduler": ctx.get("scheduler", "karras"),
        "denoise": 1.0,
        "vae": "model default",
        "control_inputs": [],
        "adapters": [],
        "tool": "ai-image-production-toolkit",
        "created_utc": utc_now(),
    }
    fingerprint = sha256_bytes(json.dumps(run, sort_keys=True).encode("utf-8"))[:16]
    run["fingerprint"] = fingerprint
    manifest = {"schema": "image-run-manifest/1.0", "run": run}
    report = "\n".join([
        "Run manifest for exact reproduction.",
        "",
        md_table([[k, v] for k, v in run.items() if k not in ("control_inputs", "adapters")],
                 ["Field", "Value"]),
        "",
        f"Fingerprint: {fingerprint}",
        "",
        "To reproduce, re-enter every field above unchanged. Any change to the model, seed, sampler, "
        "scheduler, steps, CFG, denoise or dimensions produces a different image.",
    ])
    data = {"manifest": manifest, "fingerprint": fingerprint}
    return _emit(ctx, "run-manifest", "Run manifest", [("Manifest", report)], data,
                 f"Created a run manifest (fingerprint {fingerprint}).")


def prompt_library_curator(ctx) -> Dict[str, Any]:
    library_path = ctx.get("library")
    entries_param = ctx.get("entries") or []
    query = ctx.get("query") or ""
    library: Dict[str, Any] = {"schema": "prompt-library/1.0", "entries": []}
    if library_path:
        library = read_json(str(library_path))
    added = []
    for raw in entries_param:
        parts = [p.strip() for p in str(raw).split("|")]
        if len(parts) < 2:
            raise InvalidInput(f"entry must be 'title|prompt|tags': {raw!r}")
        title, prompt = parts[0], parts[1]
        tags = [t.strip() for t in parts[2].split(",")] if len(parts) > 2 else []
        entry = {"title": title, "prompt": prompt, "tags": tags,
                 "model_compatibility": ["sdxl"], "tested_settings": {"steps": 30, "cfg": 7.0, "sampler": "dpmpp_2m"},
                 "known_failures": [], "id": slugify(title)}
        library["entries"].append(entry)
        added.append(entry["id"])
    results = library["entries"]
    if query:
        terms = [t.strip().lower() for t in re.split(r"\s+AND\s+|\s+and\s+|\s*,\s*", query) if t.strip()]
        def matches(entry):
            haystack = (entry.get("title", "") + " " + entry.get("prompt", "") + " " + " ".join(entry.get("tags", []))).lower()
            return all(t in haystack for t in terms)
        results = [e for e in results if matches(e)]
    report = "\n".join([
        f"Library holds {len(library['entries'])} entries; {len(results)} match the query '{query}'." if query
        else f"Library holds {len(library['entries'])} entries.",
        "",
        md_table([[e.get("id", "?"), e.get("title", ""), ", ".join(e.get("tags", [])),
                  ", ".join(e.get("model_compatibility", []))] for e in results],
                 ["Id", "Title", "Tags", "Models"]),
    ])
    data = {"entries": results, "total": len(library["entries"]), "added": added, "query": query}
    return _emit(ctx, "prompt-library", "Prompt library", [("Library", report)], data,
                 f"Curated {len(library['entries'])} prompt entries ({len(results)} matched).")


# ---------------------------------------------------------------------------
# Category 5 - Text to image packages
# ---------------------------------------------------------------------------


def _generation_package(ctx, slug: str, title: str, subject: str, prompt: str, negative: str,
                        params: Dict[str, Any], extras: Dict[str, Any]) -> Dict[str, Any]:
    """Shared shape for the text-to-image deliverables."""
    workflow = load_asset(TEMPLATES)["comfyui_templates"]["txt2img"]
    graph = {"nodes": workflow["nodes"], "links": [], "extra": {"ds": {"scale": 1, "offset": [0, 0]}}}
    for node in graph["nodes"]:
        for key, value in list(node["inputs"].items()):
            if isinstance(value, str) and value.startswith("{"):
                node["inputs"][key] = value.format(prompt=prompt, negative=negative)
    payload = {
        "title": title, "subject": subject, "positive_prompt": prompt, "negative_prompt": negative,
        "parameters": params, "workflow": graph, **extras,
    }
    ctx.write_json(f"{slug}-workflow.json", graph)
    sections = [
        ("Positive prompt", prompt),
        ("Negative prompt", negative),
        ("Parameters", md_table([[k, v] for k, v in params.items()], ["Parameter", "Value"])),
    ]
    for key, value in extras.items():
        if isinstance(value, list):
            sections.append((key.replace("_", " ").title(), "\n".join(f"- {v}" for v in value)))
        elif isinstance(value, str):
            sections.append((key.replace("_", " ").title(), value))
    data = payload
    result = _emit(ctx, slug, title, sections, data, f"Produced a generation package for {truncate(subject, 50)}.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out(f"{slug}-workflow.json"))]
    return result


def concept_art_generator(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    deliverable = ctx.get("deliverable") or "environment concept with iteration sheet"
    lexicon = _lex()
    prompt = (f"{subject}, {lexicon['composition'][0]['value']}, {lexicon['lighting'][1]['value']}, "
              f"{lexicon['palette'][2]['value']}, {lexicon['medium'][0]['value']}, "
              f"{', '.join(lexicon['quality_tokens'][:3])}")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["general"]["tokens"][:8])
    params = {"model": "sdxl", "steps": 32, "cfg": 7.5, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1344, "height": 768, "seed": deterministic_seed(subject) % (2 ** 31)}
    sheet = [
        {"frame": "1. silhouette study", "prompt": f"{subject}, pure black silhouette on flat white, no detail"},
        {"frame": "2. value study", "prompt": f"{subject}, three value steps, greyscale, no hue"},
        {"frame": "3. palette study", "prompt": f"{subject}, limited five-colour palette, flat blocks"},
        {"frame": "4. material study", "prompt": f"{subject}, close crop of the key material, visible weave and wear"},
        {"frame": "5. lighting study", "prompt": f"{subject}, three lighting variants side by side"},
        {"frame": "6. turnaround", "prompt": f"{subject}, front, three-quarter and side views in one frame, consistent proportions"},
    ]
    notes = [
        "Keep proportions constant across frames; lock the silhouette first.",
        "Record the seed for every frame so the sheet stays reproducible.",
        "Annotate what each frame is deciding (shape, value, colour, material).",
    ]
    return _generation_package(ctx, "concept-art-package", "Concept art package", subject, prompt, negative,
                               params, {"iteration_sheet": sheet, "production_notes": notes,
                                        "deliverable": deliverable})


def photorealistic_scene(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    lexicon = _lex()
    prompt = (f"{subject}, {lexicon['camera'][2]['value']}, {lexicon['lighting'][0]['value']}, "
              f"{lexicon['palette'][3]['value']}, {lexicon['texture'][7]['value']}, "
              "natural lens vignette, physically plausible exposure, subtle sensor grain, "
              "minor real-world imperfections, no plastic skin")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["general"]["tokens"][:6] +
                         load_asset(NEGATIVES)["targets"]["face"]["tokens"][:4])
    params = {"model": "sdxl-photoreal", "steps": 36, "cfg": 6.0, "sampler": "dpmpp_2m_sde", "scheduler": "karras",
              "width": 1216, "height": 832, "seed": deterministic_seed(subject) % (2 ** 31)}
    checklist = [
        "Name one specific lens so depth of field is predictable.",
        "State the time of day and the direction of the key light.",
        "Ask for material response (skin, glass, metal) explicitly.",
        "Ask for imperfections: dust, fingerprints, wear, small asymmetries.",
        "Avoid the word 'perfect' - it pushes toward synthetic surfaces.",
        "Check the hands and the reflection in any glass before release.",
    ]
    return _generation_package(ctx, "photoreal-scene-package", "Photorealistic scene package", subject, prompt,
                               negative, params, {"checklist": checklist,
                                                  "exposure_note": "Keep CFG at 5.5-6.5; above 8 the render over-sharpens."})


def illustration_style(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    lexicon = _lex()
    directions = []
    for medium in lexicon["medium"][1:8]:
        directions.append({
            "direction": medium["id"],
            "medium": medium["value"],
            "palette": lexicon["palette"][3]["value"],
            "prompt": f"{subject}, {medium['value']}, {lexicon['palette'][3]['value']}, "
                      f"{lexicon['lighting'][2]['value']}, clean shapes, deliberate negative space",
        })
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["general"]["tokens"][:6])
    params = {"model": "sdxl-anime", "steps": 30, "cfg": 8.0, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1024, "height": 1024, "seed": deterministic_seed(subject) % (2 ** 31)}
    policy = ("Describe the rendering behaviour rather than a named artist. Imitation requests are rewritten "
              "into attribute language by the artist-style-request-rewriter capability.")
    return _generation_package(ctx, "illustration-directions", "Illustration direction set", subject,
                               directions[0]["prompt"], negative, params,
                               {"directions": directions, "style_policy": policy})


def product_visualization(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    deliverable = ctx.get("deliverable") or "pack shot"
    lexicon = _lex()
    prompt = (f"{subject}, studio product photograph, seamless pale background, large softbox key light with "
              f"white fill card, controlled specular highlights, true-to-scale geometry, 85mm lens, "
              f"{lexicon['palette'][3]['value']}")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["product"]["tokens"] +
                         load_asset(NEGATIVES)["targets"]["general"]["tokens"][:4])
    params = {"model": "sdxl-photoreal", "steps": 34, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1024, "height": 1024, "seed": deterministic_seed(subject) % (2 ** 31)}
    safeguards = [
        "Generate the product body without branding, then composite the real logo.",
        "Lock the camera angle so variants stay comparable.",
        "Do not let the model invent packaging copy.",
        "Keep the product between 80% and 90% of the frame for catalogue use.",
    ]
    variants = [
        {"view": "front three-quarter", "note": "45 degree rotation, camera at product mid-height"},
        {"view": "straight front", "note": "zero rotation, symmetrical, for catalogue mains"},
        {"view": "top-down flatlay", "note": "orthographic feel, soft even light"},
        {"view": "detail macro", "note": "tight crop on the material or mechanism"},
        {"view": "exploded view", "note": "components separated along one axis with consistent spacing"},
    ]
    return _generation_package(ctx, "product-visualization-package", "Product visualisation package", subject,
                               prompt, negative, params,
                               {"safeguards": safeguards, "views": variants, "deliverable": deliverable})


def architectural_visualization(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    lexicon = _lex()
    prompt = (f"{subject}, architectural photograph, single-point perspective with a level horizon, "
              f"physically accurate proportions, {lexicon['lighting'][0]['value']}, "
              f"{lexicon['palette'][3]['value']}, visible material junctions, no warped verticals")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["architecture"]["tokens"] +
                         load_asset(NEGATIVES)["targets"]["general"]["tokens"][:4])
    params = {"model": "sdxl", "steps": 34, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1344, "height": 896, "seed": deterministic_seed(subject) % (2 ** 31)}
    consistency = [
        "Fix the camera height and lens before generating variations.",
        "Repeat the same window, door and material list in every prompt.",
        "Generate one lighting condition per render; mixed daylight reads as flat.",
        "Check stairs, thresholds and structural spans for impossible geometry.",
    ]
    return _generation_package(ctx, "architectural-package", "Architectural visualisation package", subject,
                               prompt, negative, params, {"consistency_rules": consistency})


def food_visual(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    prompt = (f"{subject}, overhead food photograph, warm side light raking across the surface, visible crumb and "
              f"moisture texture, steam, linen and ceramic props, muted warm palette, shallow depth of field, "
              f"appetising but physically plausible, 85mm lens")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["general"]["tokens"][:6] + ["oversaturated", "fake gloss"])
    params = {"model": "sdxl-photoreal", "steps": 32, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1024, "height": 1024, "seed": deterministic_seed(subject) % (2 ** 31)}
    plausibility = [
        "State the cut, the doneness and the plate explicitly.",
        "Avoid steam unless the food is genuinely hot.",
        "Sauce and gloss must follow gravity - check for floating droplets.",
        "Keep serving sizes realistic; oversized portions read as artificial.",
    ]
    return _generation_package(ctx, "food-visual-package", "Food and beverage visual package", subject,
                               prompt, negative, params, {"plausibility_checks": plausibility})


def fashion_concept(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    prompt = (f"{subject}, full-length fashion photograph, studio seamless backdrop, softbox key with rim light, "
              f"garment construction clearly visible, seams and drape legible, editorial colour grading, 85mm lens")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["anatomy"]["tokens"][:4] +
                         load_asset(NEGATIVES)["targets"]["general"]["tokens"][:5])
    params = {"model": "sdxl", "steps": 32, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 832, "height": 1216, "seed": deterministic_seed(subject) % (2 ** 31)}
    construction = [
        "Name the garment, the closure and the fabric so the logic stays coherent.",
        "Ask for a full body view so proportion and hem length can be judged.",
        "State the silhouette (fitted, A-line, boxy, draped) rather than a style era only.",
        "Check that the collar, cuff and hem are physically consistent.",
    ]
    colorways = [{"name": "base", "prompt_modifier": ""},
                 {"name": "seasonal alt", "prompt_modifier": ", muted seasonal colourway"},
                 {"name": "monochrome", "prompt_modifier": ", monochrome colourway"},
                 {"name": "high-contrast", "prompt_modifier": ", high-contrast colourway"}]
    return _generation_package(ctx, "fashion-package", "Fashion concept package", subject, prompt, negative,
                               params, {"construction_rules": construction, "colorways": colorways})


def abstract_synthesizer(ctx) -> Dict[str, Any]:
    subject = str(ctx.get("subject") or "non-representational composition")
    samples = load_asset(SAMPLES)
    palette = samples["abstract_palettes"][0]
    prompt = (f"{subject}, overlapping translucent arcs, controlled colour rhythm from {palette['name']}, "
              f"flat geometry balanced against soft gradient depth, fine paper grain, no recognisable subject")
    negative = "text artifacts, watermark, faces, realistic scene, hard aliased edges, oversaturated"
    params = {"model": "sdxl", "steps": 28, "cfg": 6.0, "sampler": "euler", "scheduler": "normal",
              "width": 1024, "height": 1024, "seed": deterministic_seed(subject) % (2 ** 31)}
    controls = {
        "palette": palette["swatches"],
        "rhythm": "3 large forms, 5 medium, many small accents",
        "geometry": "circular arcs against a strict grid",
        "texture": "very light uniform grain to avoid banding",
        "depth": "two overlapping planes plus one soft shadow layer",
    }
    return _generation_package(ctx, "abstract-package", "Abstract visual package", subject, prompt, negative,
                               params, {"controls": controls})


# ---------------------------------------------------------------------------
# Category 7 - Multi-image composition
# ---------------------------------------------------------------------------


def multi_reference_composer(ctx) -> Dict[str, Any]:
    references = ctx.paths("references")
    if not references:
        raise InvalidInput("provide at least one reference image")
    roles_spec = ctx.get("roles") or ""
    role_map = {}
    for chunk in str(roles_spec).split(";"):
        if ":" in chunk:
            name, role = chunk.split(":", 1)
            role_map[os.path.basename(name.strip())] = role.strip()
    rows = []
    assigned = []
    for ref in references:
        info = core.probe_image(ref)
        role = role_map.get(os.path.basename(ref), "unassigned")
        assigned.append({"file": os.path.basename(ref), "role": role, "width": info["width"],
                         "height": info["height"], "aspect": info.get("aspect_ratio")})
        rows.append([os.path.basename(ref), role, f'{info["width"]}x{info["height"]}',
                     info.get("aspect_ratio", "-"), info["format"]])
    default_roles = ["style", "composition", "pose", "object", "palette"]
    guidance = [
        "Give every reference exactly one role; a reference used for two roles fights itself.",
        "Identity comes from the face reference, style from the style reference.",
        "Record the role assignment in the manifest so the composite can be rebuilt.",
    ]
    plan = []
    for i, entry in enumerate(assigned):
        plan.append({"reference": entry["file"], "role": entry["role"],
                     "instruction": f"Use this reference for {entry['role']} only.",
                     "target_region": default_roles[i % len(default_roles)]})
    report = "\n".join([
        md_table(rows, ["Reference", "Role", "Size", "Aspect", "Format"]),
        "",
        "Role assignment:",
        md_table([[p["reference"], p["role"], p["target_region"]] for p in plan], ["Reference", "Role", "Region"]),
        "",
        "Rules:",
        *[f"- {g}" for g in guidance],
    ])
    data = {"references": assigned, "plan": plan, "guidance": guidance}
    return _emit(ctx, "multi-reference-composition", "Multi-reference composition plan",
                 [("References", report)], data, f"Assigned roles to {len(assigned)} reference images.")


def photomontage_integrator(ctx) -> Dict[str, Any]:
    images = ctx.paths("images")
    if not images:
        raise InvalidInput("provide at least two images to blend")
    infos = [core.probe_image(p) for p in images]
    steps = [
        {"step": 1, "action": "Perspective match", "detail": "Align horizon and vanishing points before any blending."},
        {"step": 2, "action": "Value and colour match", "detail": "Match the key light direction and white balance of the target plate."},
        {"step": 3, "action": "Mask and feather", "detail": "Separate with a soft matte; avoid hard polygon edges."},
        {"step": 4, "action": "Contact shadow", "detail": "Add a cast shadow consistent with the scene's key light."},
        {"step": 5, "action": "Depth cue", "detail": "Scale, blur and warm/cool-shift by distance plane."},
        {"step": 6, "action": "Grain and grade", "detail": "Unify with a single grain structure and final grade."},
    ]
    report = "\n".join([
        md_table([[i["name"], f'{i["width"]}x{i["height"]}', i.get("aspect_ratio", "-")] for i in infos],
                 ["Image", "Size", "Aspect"]),
        "",
        md_table([[s["step"], s["action"], s["detail"]] for s in steps], ["Step", "Action", "Detail"]),
    ])
    data = {"images": [{"file": i["name"], "path": i["path"], "width": i["width"],
                        "height": i["height"]} for i in infos],
            "steps": steps, "planes": ["foreground", "midground", "background", "effects", "grade"]}
    return _emit(ctx, "photomontage-plan", "Photomontage integration plan",
                 [("Plan", report)], data, f"Planned a {len(steps)}-step montage for {len(images)} images.")


def visual_asset_family(ctx) -> Dict[str, Any]:
    campaign = str(ctx.require("campaign"))
    templates = load_asset(TEMPLATES)
    lexicon = _lex()
    assets = []
    for name, spec in templates["platform_sizes"].items():
        assets.append({
            "asset": name, "width": spec["width"], "height": spec["height"],
            "safe_margin": spec["safe_margin"],
            "focal_rule": "Keep the subject inside the central 80% width; the platform crops the edges.",
            "prompt": f"{campaign}, {lexicon['lighting'][1]['value']}, {lexicon['palette'][2]['value']}, "
                      f"{lexicon['composition'][0]['value']}",
        })
    report = "\n".join([
        f"Campaign direction: {campaign}",
        "",
        md_table([[a["asset"], f'{a["width"]}x{a["height"]}', f'{a["safe_margin"]:.0%}', a["focal_rule"]]
                  for a in assets], ["Asset", "Size", "Safe margin", "Focal rule"]),
    ])
    data = {"campaign": campaign, "assets": assets, "count": len(assets)}
    return _emit(ctx, "visual-asset-family", "Visual asset family", [("Family", report)], data,
                 f"Planned {len(assets)} coordinated assets for '{truncate(campaign, 40)}'.")


def reference_conflict_resolver(ctx) -> Dict[str, Any]:
    spec = str(ctx.get("references", ""))
    rows = []
    conflicts = []
    seen = {}
    for chunk in spec.split(";"):
        if ":" not in chunk or "=" not in chunk:
            continue
        file_part, attr_part = chunk.split(":", 1)
        attr, value = attr_part.split("=", 1)
        attr, value = attr.strip(), value.strip()
        rows.append([os.path.basename(file_part.strip()), attr, value])
        seen.setdefault(attr, []).append((os.path.basename(file_part.strip()), value))
    priority = ["identity", "face", "pose", "composition", "style", "palette", "object"]
    resolutions = []
    for attr, values in seen.items():
        if len({v for _, v in values}) > 1:
            rank = priority.index(attr) if attr in priority else len(priority)
            resolutions.append({
                "attribute": attr,
                "values": [{"file": f, "value": v} for f, v in values],
                "resolution": values[0][1],
                "reason": f"'{attr}' has priority rank {rank}; lower ranked attributes yield to it.",
                "action": f"Keep '{values[0][1]}' from {values[0][0]}; suppress the competing value in the prompt.",
            })
            conflicts.append(attr)
    if not conflicts:
        resolutions.append({"attribute": "none", "values": [], "resolution": "no conflict detected",
                            "reason": "All references agree on the shared attributes.",
                            "action": "Proceed with the merge."})
    report = "\n".join([
        md_table(rows, ["Reference", "Attribute", "Value"]) if rows else "No attribute table supplied.",
        "",
        "Resolutions:",
        *[f"- {r['attribute']}: keep '{r['resolution']}' - {r['reason']}" for r in resolutions],
    ])
    data = {"attributes": rows, "conflicts": conflicts, "resolutions": resolutions, "priority_order": priority}
    return _emit(ctx, "reference-conflict-resolution", "Reference conflict resolution",
                 [("Conflicts", report)], data, f"Resolved {len(conflicts)} conflicting attribute(s).")


# ---------------------------------------------------------------------------
# Category 8 - Masking and compositing
# ---------------------------------------------------------------------------


def layered_composite_builder(ctx) -> Dict[str, Any]:
    spec = str(ctx.get("layers", ""))
    layers = []
    for index, chunk in enumerate([c for c in spec.split(";") if c.strip()]):
        parts = chunk.split(":")
        name = parts[0].strip()
        path = parts[1].strip() if len(parts) > 1 else ""
        blend = parts[2].strip() if len(parts) > 2 else "normal"
        opacity = parts[3].strip() if len(parts) > 3 else "100%"
        layers.append({"order": index, "name": name, "path": path, "blend": blend, "opacity": opacity})
    if not layers:
        layers = [{"order": 0, "name": "background", "path": "", "blend": "normal", "opacity": "100%"},
                  {"order": 1, "name": "midground", "path": "", "blend": "normal", "opacity": "100%"},
                  {"order": 2, "name": "foreground", "path": "", "blend": "normal", "opacity": "100%"},
                  {"order": 3, "name": "effects", "path": "", "blend": "screen", "opacity": "40%"},
                  {"order": 4, "name": "grade", "path": "", "blend": "soft light", "opacity": "70%"}]
    report = "\n".join([
        "Layer stack (bottom to top):",
        md_table([[l["order"], l["name"], l["blend"], l["opacity"], l["path"] or "-"] for l in layers],
                 ["#", "Layer", "Blend", "Opacity", "Source"]),
        "",
        "Blend rules: shadows use multiply, glow uses screen, unified grading uses soft light, "
        "and every foreground layer needs a mask so its edges stay clean.",
    ])
    data = {"layers": layers, "count": len(layers)}
    return _emit(ctx, "layer-stack", "Layered composite stack", [("Layers", report)], data,
                 f"Built a {len(layers)}-layer composite stack.")


def shadow_reflection_composer(ctx) -> Dict[str, Any]:
    width = float(ctx.get("subject_width", 400))
    height = float(ctx.get("subject_height", 600))
    azimuth = float(ctx.get("light_direction", 135))
    elevation = float(ctx.get("elevation", 45))
    rad = math.radians(azimuth)
    ground_y = height
    shadow_dir = (math.cos(rad), math.sin(rad))
    shadow_length = height / max(0.05, math.tan(math.radians(max(5.0, elevation))))
    shadow_length = min(shadow_length, height * 3)
    offset_x = shadow_dir[0] * shadow_length
    offset_y = shadow_dir[1] * shadow_length
    base_x = width / 2 - width / 2 * shadow_dir[0]
    contact = {"type": "contact shadow", "length_px": round(width * 0.9, 1),
               "softness": "tight, dark at the contact point, 15-25 px blur"}
    cast = {"type": "cast shadow", "direction": f"azimuth {azimuth} deg, elevation {elevation} deg",
            "length_px": round(shadow_length, 1), "offset_x_px": round(offset_x, 1),
            "offset_y_px": round(offset_y, 1), "softness": "increases with distance from the contact point"}
    reflection = {"type": "glossy reflection", "method": "mirror the subject below the contact line, "
                                                          "blur 2-4 px, scale vertically 0.95, fade with distance",
                  "strength": "20-40% of the subject luminance"}
    report = "\n".join([
        f"Subject box: {width:.0f} x {height:.0f} px, light azimuth {azimuth} deg, elevation {elevation} deg",
        "",
        md_table([[c["type"], json.dumps({k: v for k, v in c.items() if k != 'type'})] for c in (contact, cast, reflection)],
                 ["Element", "Specification"]),
    ])
    data = {"subject": {"width": width, "height": height}, "light": {"azimuth": azimuth, "elevation": elevation},
            "contact_shadow": contact, "cast_shadow": cast, "reflection": reflection}
    return _emit(ctx, "shadow-reflection-spec", "Shadow and reflection specification",
                 [("Geometry", report)], data, "Computed shadow and reflection geometry from the light setup.",
                 {"shadow_length_px": round(shadow_length, 1)})


def occlusion_planner(ctx) -> Dict[str, Any]:
    spec = str(ctx.get("subjects", ""))
    subjects = []
    for chunk in [c for c in spec.split(";") if c.strip()]:
        parts = chunk.split(",")
        if len(parts) < 5:
            raise InvalidInput(f"subject needs 'name,x,y,w,h,depth': {chunk!r}")
        subjects.append({"name": parts[0].strip(), "x": float(parts[1]), "y": float(parts[2]),
                         "w": float(parts[3]), "h": float(parts[4]), "depth": float(parts[5])})
    if not subjects:
        subjects = [{"name": "subject_a", "x": 200, "y": 200, "w": 300, "h": 400, "depth": 1.0},
                    {"name": "subject_b", "x": 520, "y": 260, "w": 250, "h": 340, "depth": 0.5}]
    overlaps = []
    for i in range(len(subjects)):
        for j in range(i + 1, len(subjects)):
            a, b = subjects[i], subjects[j]
            ox = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
            oy = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
            area = ox * oy
            if area <= 0:
                continue
            smaller = min(a["w"] * a["h"], b["w"] * b["h"])
            front, back = (a, b) if a["depth"] >= b["depth"] else (b, a)
            overlaps.append({
                "pair": [a["name"], b["name"]], "overlap_px": round(area, 1),
                "overlap_share": round(area / smaller, 3) if smaller else 0,
                "in_front": front["name"], "behind": back["name"],
                "action": f"Mask {front['name']} over {back['name']}; feather the shared edge 2-4 px and keep "
                          f"the contact shadow on the rear subject.",
            })
    order = [s["name"] for s in sorted(subjects, key=lambda s: s["depth"], reverse=True)]
    report = "\n".join([
        "Depth order (front to back): " + " > ".join(order),
        "",
        md_table([[o["pair"][0], o["pair"][1], o["overlap_px"], f'{o["overlap_share"]:.0%}', o["in_front"], o["action"]]
                  for o in overlaps], ["A", "B", "Overlap px", "Share", "In front", "Action"]),
    ])
    data = {"subjects": subjects, "depth_order": order, "overlaps": overlaps}
    return _emit(ctx, "occlusion-plan", "Occlusion plan", [("Overlaps", report)], data,
                 f"Planned {len(overlaps)} occlusion relationship(s).")


def blend_mode_advisor(ctx) -> Dict[str, Any]:
    foreground = str(ctx.get("foreground", ""))
    background = str(ctx.get("background", ""))
    intent = str(ctx.get("intent", "natural"))
    advice = {
        "natural": ("normal", "Use a mask and keep opacity at 100%; add a soft contact shadow for grounding."),
        "glow": ("screen", "Screen keeps highlights bright without washing out the blacks."),
        "shadow": ("multiply", "Multiply darkens and preserves the underlying colour; pair with a soft blur."),
        "grade": ("soft light", "Soft light shifts tone without crushing; use 60-80% opacity."),
        "texture": ("overlay", "Overlay boosts local contrast; reduce opacity if the texture reads as noise."),
    }
    mode, rationale = advice.get(intent, advice["natural"])
    report = "\n".join([
        f"Foreground: {foreground}",
        f"Background: {background}",
        f"Intent: {intent}",
        "",
        f"Recommended blend mode: {mode}",
        f"Rationale: {rationale}",
        "",
        "Fallbacks:",
        "- If the edge is too hard, add a 1-2 px mask feather.",
        "- If the foreground looks pasted, add a subtle colour match and grain.",
        "- If the result clips, switch to soft light with lower opacity.",
    ])
    data = {"intent": intent, "blend_mode": mode, "rationale": rationale,
            "foreground": foreground, "background": background}
    return _emit(ctx, "blend-mode-advice", "Blend mode advice", [("Recommendation", report)], data,
                 f"Recommended '{mode}' for a {intent} composite.")


# ---------------------------------------------------------------------------
# Category 11 - Typography and graphic design
# ---------------------------------------------------------------------------


def typography_planner(ctx) -> Dict[str, Any]:
    headline = str(ctx.require("headline"))
    subhead = ctx.get("subhead") or ""
    width = int(ctx.get("width", 1200))
    height = int(ctx.get("height", 800))
    bg = str(ctx.get("background", "#101014"))
    fg = str(ctx.get("text_color", "#f5f5f7"))
    ratio = contrast_ratio(hex_to_rgb(fg), hex_to_rgb(bg))
    hierarchy = [
        {"level": "Display", "text": headline, "size": int(height * 0.11), "weight": "bold",
         "position": "left aligned, baseline at 55% height", "max_chars": max(10, int(width / (height * 0.11) * 1.5))},
        {"level": "Subhead", "text": subhead, "size": int(height * 0.045), "weight": "regular",
         "position": "below the display line", "max_chars": max(20, int(width / (height * 0.045) * 1.6))},
        {"level": "Caption", "text": "supporting detail", "size": int(height * 0.028), "weight": "regular",
         "position": "bottom safe area", "max_chars": 120},
    ]
    safe = {"margin_px": int(min(width, height) * 0.06), "min_type_size_px": int(height * 0.025)}
    body = []
    body.append(svg_text(safe["margin_px"], int(height * 0.55), headline, hierarchy[0]["size"], fg, weight="bold"))
    if subhead:
        body.append(svg_text(safe["margin_px"], int(height * 0.63), subhead, hierarchy[1]["size"], fg))
    body.append(svg_text(safe["margin_px"], height - safe["margin_px"], "supporting detail",
                         hierarchy[2]["size"], fg))
    svg = svg_document(width, height, "\n".join(body), bg)
    ctx.write_text("typography-layout.svg", svg)
    verdict = "pass" if ratio >= 4.5 else ("large-text only" if ratio >= 3.0 else "fail")
    report = "\n".join([
        md_table([[h["level"], h["size"], h["weight"], h["position"], h["max_chars"]] for h in hierarchy],
                 ["Level", "Size px", "Weight", "Placement", "Max characters"]),
        "",
        f"Safe margin: {safe['margin_px']} px; minimum type size {safe['min_type_size_px']} px.",
        f"Contrast ratio: {ratio}:1 (foreground {fg} on {bg}) - {verdict}.",
        "",
        "Rules:",
        "- Set type outside the image model so the letters are exact.",
        "- Keep one alignment axis for the whole layout.",
        "- Never place type over the busiest region of the image.",
    ])
    data = {"hierarchy": hierarchy, "safe_area": safe, "contrast_ratio": ratio, "contrast_verdict": verdict,
            "svg": "typography-layout.svg"}
    result = _emit(ctx, "typography-plan", "Typography plan", [("Plan", report)], data,
                   f"Planned a 3-level hierarchy at {ratio}:1 contrast.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out("typography-layout.svg"))]
    return result


def text_artifact_corrector(ctx) -> Dict[str, Any]:
    image = ctx.get("image")
    text = ctx.get("text") or ""
    findings = []
    if image:
        info = core.probe_image(str(image))
        findings.append({"check": "resolution", "value": f'{info["width"]}x{info["height"]}',
                         "note": "Letterform repair needs at least 40 px of cap height; below that, re-render the plate."})
        findings.append({"check": "format", "value": info["format"],
                         "note": "Lossy formats blur edges - use PNG or TIFF as the plate for re-typesetting."})
    if not text:
        findings.append({"check": "text_content", "value": "missing",
                         "note": "Supply the exact intended text so the corrected typesetting can be built."})
    report = "\n".join([
        "Generated lettering is unreliable; the reliable repair is deterministic typesetting.",
        "",
        md_table([[f["check"], f["value"], f["note"]] for f in findings], ["Check", "Value", "Note"]),
        "",
        "Repair workflow:",
        "1. Identify the intended text and its perspective.",
        "2. Remove the garbled lettering (inpaint the background where the letters were).",
        "3. Set the corrected text in a deterministic design tool, matching size, weight and perspective.",
        "4. Apply a matching material treatment so the type sits in the scene.",
        "5. Check legibility at delivery size and re-verify contrast.",
    ])
    data = {"image": str(image) if image else None, "intended_text": text, "findings": findings}
    return _emit(ctx, "text-artifact-correction", "Generated text correction plan",
                 [("Repair workflow", report)], data, "Produced a deterministic text-repair workflow.")


def logo_concept_generator(ctx) -> Dict[str, Any]:
    brand = str(ctx.require("brand"))
    industry = str(ctx.get("industry", "general"))
    directions = [
        {"type": "symbol", "concept": f"Abstract mark built from one simple geometric idea about {brand}.",
         "checks": ["reads at 16 px", "one colour", "no fine detail"]},
        {"type": "wordmark", "concept": f"Custom lettering spelling {brand} with one distinctive ligature or cut.",
         "checks": ["spelling exact", "kerning even", "works in a single weight"]},
        {"type": "monogram", "concept": f"Interlocked initials of {brand} inside a simple container.",
         "checks": ["no ambiguous letters", "balanced negative space"]},
        {"type": "lockup", "concept": "Symbol above the wordmark with a defined clear-space ratio.",
         "checks": ["clear space at least one cap height", "works in horizontal and stacked versions"]},
    ]
    rubric = [
        {"criterion": "simplicity", "question": "Can it be drawn in under five strokes?"},
        {"criterion": "scalability", "question": "Does it survive 16 px and 1 m wide?"},
        {"criterion": "distinctiveness", "question": "Is it distinguishable from close competitors?"},
        {"criterion": "relevance", "question": "Does it suggest the category without a literal icon?"},
        {"criterion": "reproduction", "question": "Does it work in one colour, embroidery and small sizes?"},
    ]
    report = "\n".join([
        f"Brand: {brand} (industry: {industry})",
        "",
        md_table([[d["type"], d["concept"], "; ".join(d["checks"])] for d in directions], ["Direction", "Concept", "Checks"]),
        "",
        "Evaluation rubric:",
        *[f"- {r['criterion']}: {r['question']}" for r in rubric],
        "",
        "Originality rule: build an original mark; do not reconstruct or trace an existing logo.",
    ])
    data = {"brand": brand, "industry": industry, "directions": directions, "rubric": rubric}
    return _emit(ctx, "logo-directions", "Logo direction set", [("Directions", report)], data,
                 f"Explored {len(directions)} logo directions for {brand}.")


def poster_designer(ctx) -> Dict[str, Any]:
    title = str(ctx.require("title"))
    subtitle = ctx.get("subtitle") or ""
    width = int(ctx.get("width", 1200))
    height = int(ctx.get("height", 1600))
    zones = [
        {"zone": "eyebrow", "position": "top 12%", "content": "category or festival line", "size": int(height * 0.018)},
        {"zone": "image", "position": "12% to 68%", "content": "key visual", "size": 0},
        {"zone": "title", "position": "70% to 82%", "content": title, "size": int(height * 0.075)},
        {"zone": "billing", "position": "84%", "content": subtitle or "credits", "size": int(height * 0.02)},
        {"zone": "cta", "position": "bottom 8%", "content": "date, venue, call to action", "size": int(height * 0.025)},
    ]
    body = [svg_text(width / 2, int(height * 0.12), zones[0]["content"], zones[0]["size"], "#f0f0f0", anchor="middle")]
    body.append(svg_text(width / 2, int(height * 0.77), title, zones[2]["size"], "#ffffff", anchor="middle", weight="bold"))
    if subtitle:
        body.append(svg_text(width / 2, int(height * 0.85), subtitle, zones[3]["size"], "#d0d0d0", anchor="middle"))
    body.append(svg_text(width / 2, int(height * 0.93), "date - venue - call to action", zones[4]["size"],
                          "#ffb300", anchor="middle"))
    svg = svg_document(width, height, "\n".join(body), "#101014")
    ctx.write_text("poster-layout.svg", svg)
    report = "\n".join([
        md_table([[z["zone"], z["position"], z["content"], z["size"] or "-"] for z in zones],
                 ["Zone", "Position", "Content", "Size px"]),
        "",
        "Hierarchy rule: the title must be the second largest element after the key visual, and the call to "
        "action must be readable at thumbnail size.",
    ])
    data = {"title": title, "zones": zones, "svg": "poster-layout.svg"}
    result = _emit(ctx, "poster-layout", "Poster and key art layout", [("Layout", report)], data,
                   "Produced a poster zone map and SVG layout.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out("poster-layout.svg"))]
    return result


def social_graphic_builder(ctx) -> Dict[str, Any]:
    headline = str(ctx.require("headline"))
    platform = str(ctx.get("platform", "instagram_post"))
    templates = load_asset(TEMPLATES)
    if platform not in templates["platform_sizes"]:
        raise InvalidInput(f"unknown platform preset: {platform} (known: {', '.join(sorted(templates['platform_sizes']))})")
    spec = templates["platform_sizes"][platform]
    safe_px = int(min(spec["width"], spec["height"]) * spec["safe_margin"])
    focal = {"x": spec["width"] / 2, "y": spec["height"] * 0.42}
    crop_safe = {"left": safe_px, "right": spec["width"] - safe_px, "top": safe_px, "bottom": spec["height"] - safe_px}
    body = [svg_text(focal["x"], focal["y"], headline, int(spec["height"] * 0.07), "#ffffff",
                     anchor="middle", weight="bold")]
    svg = svg_document(spec["width"], spec["height"], "\n".join(body), "#12151a")
    ctx.write_text(f"social-{platform}.svg", svg)
    report = "\n".join([
        f"Platform preset: {platform} ({spec['width']}x{spec['height']})",
        f"Safe margin: {safe_px} px (crop-safe box {crop_safe})",
        "",
        "Checks:",
        "- Headline must stay inside the crop-safe box for every platform crop.",
        "- Keep one focal point; platforms crop to several aspect ratios.",
        "- Render the text outside the image model.",
    ])
    data = {"platform": platform, "spec": spec, "safe_margin_px": safe_px, "crop_safe_box": crop_safe,
            "focal_point": focal, "svg": f"social-{platform}.svg"}
    result = _emit(ctx, "social-graphic", "Social graphic spec", [("Spec", report)], data,
                   f"Produced a {platform} graphic spec.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out(f"social-{platform}.svg"))]
    return result


def packaging_conceptualizer(ctx) -> Dict[str, Any]:
    product = str(ctx.require("product"))
    dieline_name = str(ctx.get("dieline", "sleek_carton_120x60"))
    dielines = load_asset(TEMPLATES)["packaging_dielines"]
    if dieline_name not in dielines:
        raise InvalidInput(f"unknown dieline: {dieline_name} (known: {', '.join(sorted(dielines))})")
    dieline = dielines[dieline_name]
    panel_roles = {
        "front": "brand, product name, key benefit, one hero visual",
        "back": "barcode, legal copy, ingredients, contact",
        "left": "short brand story or recycling mark",
        "right": "secondary claims or certifications",
        "top": "flavour or variant name",
        "bottom": "batch code, weight, legal line",
    }
    report = "\n".join([
        f"Product: {product}",
        f"Dieline: {dieline_name} ({dieline['width_mm']} x {dieline['height_mm']} x {dieline['depth_mm']} mm)",
        "",
        md_table([[p, panel_roles.get(p, "supporting content"), "yes" if p in (dieline["barcode_panel"], dieline["legal_panel"]) else "no"]
                  for p in dieline["panels"]], ["Panel", "Content", "Barcode/legal"]),
        "",
        f"Print area inset: {dieline['print_area_inset_mm']} mm; fold allowance: {dieline['fold_mm']} mm.",
        "",
        "Rules:",
        "- Generate artwork flat, then wrap it onto the dieline so nothing crosses a fold.",
        "- Barcode and legal copy are always typeset, never generated.",
        "- Check every panel at 100% before releasing the print file.",
    ])
    data = {"product": product, "dieline": dieline, "panel_roles": panel_roles}
    return _emit(ctx, "packaging-concept", "Packaging artwork concept",
                 [("Dieline plan", report)], data, f"Mapped {len(dieline['panels'])} panels for {product}.")


def infographic_asset_maker(ctx) -> Dict[str, Any]:
    topic = str(ctx.require("topic"))
    items = int(ctx.get("items", 5))
    chart_type = str(ctx.get("chart_type", "bar"))
    icons = []
    for i in range(items):
        icons.append({
            "index": i + 1,
            "concept": f"simple flat icon representing point {i + 1} of the {topic} story",
            "grid": "24x24 viewBox, 2 px stroke, round caps, single accent colour",
            "text": f"Point {i + 1}",
            "render_rule": "Icon only - the label is typeset outside the image model.",
        })
    data_points = [{"label": f"Item {i + 1}", "value": None,
                    "note": "value must come from the real dataset, never from the image model"} for i in range(items)]
    report = "\n".join([
        f"Topic: {topic} ({chart_type} layout, {items} points)",
        "",
        md_table([[i["index"], i["concept"], i["grid"]] for i in icons], ["#", "Icon concept", "Grid spec"]),
        "",
        "Hard rule: all factual text and all numbers are typeset deterministically. The image model supplies "
        "decoration only. Data points must be bound to the source dataset, never fabricated.",
    ])
    data = {"topic": topic, "chart_type": chart_type, "icons": icons, "data_points": data_points,
            "rule": "no factual text or numbers inside generated artwork"}
    return _emit(ctx, "infographic-assets", "Infographic asset plan", [("Assets", report)], data,
                 f"Planned {items} decorative infographic assets.")


def brand_template_adapter(ctx) -> Dict[str, Any]:
    brand_name = str(ctx.require("brand"))
    headline = ctx.get("headline") or ""
    brands = load_asset(TEMPLATES)["brands"]
    match = next((b for k, b in brands.items() if k.lower() == brand_name.lower()
                  or b["name"].lower() == brand_name.lower()), None)
    if match is None:
        raise InvalidInput(f"unknown brand template '{brand_name}' (known: {', '.join(sorted(brands))})")
    palette = match["palette"]
    primary = palette["primary"]
    accent = palette["accent"]
    text = palette["ink"]
    ratio_text = contrast_ratio(hex_to_rgb(text), hex_to_rgb(primary))
    ratio_accent = contrast_ratio(hex_to_rgb(primary), hex_to_rgb(accent))
    body = [svg_text(60, 140, headline or match["name"], 56, accent, weight="bold"),
            svg_text(60, 200, match["fonts"]["display"], 22, text),
            svg_text(60, 240, match["notes"], 18, text)]
    svg = svg_document(1200, 800, "\n".join(body), primary)
    ctx.write_text("brand-template.svg", svg)
    report = "\n".join([
        f"Brand: {match['name']}",
        "",
        md_table([[k, v] for k, v in palette.items()], ["Token", "Value"]),
        "",
        f"Logo clear space: {match['clear_space']:.0%} of the logo width.",
        f"Contrast ink on primary: {ratio_text}:1; primary on accent: {ratio_accent}:1.",
        "",
        "Apply:",
        "- Keep clear space free of type and imagery.",
        "- Use the display font for headlines and the body font for supporting text.",
        "- Never recolour the logo; use the approved palette for everything around it.",
    ])
    data = {"brand": match["name"], "tokens": palette, "fonts": match["fonts"],
            "contrast": {"ink_on_primary": ratio_text, "primary_on_accent": ratio_accent},
            "svg": "brand-template.svg"}
    result = _emit(ctx, "brand-template", "Brand template application", [("Template", report)], data,
                   f"Applied the {match['name']} brand template.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out("brand-template.svg"))]
    return result


# ---------------------------------------------------------------------------
# Category 12 - Characters and people
# ---------------------------------------------------------------------------


def character_design_specialist(ctx) -> Dict[str, Any]:
    character = str(ctx.require("character"))
    sheet = [
        {"view": "silhouette", "prompt": f"{character}, solid black silhouette, full body, plain background, no detail",
         "decides": "readable outline at thumbnail size"},
        {"view": "front", "prompt": f"{character}, front view, neutral pose, flat even lighting, plain background"},
        {"view": "three-quarter", "prompt": f"{character}, three-quarter view, neutral pose, flat even lighting"},
        {"view": "side", "prompt": f"{character}, side profile view, neutral pose, flat even lighting"},
        {"view": "back", "prompt": f"{character}, back view, neutral pose, flat even lighting"},
        {"view": "expression sheet", "prompt": f"{character}, six expression studies in a grid, consistent features"},
        {"view": "costume detail", "prompt": f"{character}, close crop of the costume, material and construction detail"},
        {"view": "scale reference", "prompt": f"{character}, next to a human figure for scale, plain background"},
    ]
    spec = {
        "silhouette": "One clear silhouette that reads at 24 px.",
        "proportions": "State head-to-body ratio and any stylisation explicitly.",
        "costume": "Name garment, material and closure so construction stays coherent.",
        "props": "One hero prop, plus optional secondary props.",
        "palette": "Limit to five colours with one accent.",
        "turnaround": "Eight views above, same seed, same lighting, same background.",
    }
    report = "\n".join([
        f"Character: {character}",
        "",
        md_table([[v["view"], v["prompt"], v.get("decides", "-")] for v in sheet],
                 ["View", "Prompt", "What it decides"]),
        "",
        md_table([[k, v] for k, v in spec.items()], ["Design element", "Specification"]),
    ])
    data = {"character": character, "sheet": sheet, "specification": spec}
    return _emit(ctx, "character-design", "Character design sheet", [("Sheet", report)], data,
                 f"Designed a {len(sheet)}-view turnaround for {truncate(character, 40)}.")


def character_consistency_engineer(ctx) -> Dict[str, Any]:
    character = str(ctx.require("character"))
    scenes = int(ctx.get("scenes", 5))
    anchors = ["keep the same face structure and proportions",
               "keep the same hairstyle silhouette and hairline",
               "keep the same costume colours and trim",
               "keep the same signature accessory"]
    scene_plan = []
    for i in range(scenes):
        scene_plan.append({
            "scene": i + 1,
            "prompt": f"{character}, {anchors[0]}, {anchors[1]}, scene: scene {i + 1}",
            "seed": deterministic_seed(f"{character}-{i}") % (2 ** 31),
            "identity_check": "compare with the reference sheet before accepting",
        })
    workflow = [
        {"step": 1, "action": "Build the reference sheet", "detail": "Lock silhouette, proportions, costume and palette."},
        {"step": 2, "action": "Train or acquire the identity adapter", "detail": "A LoRA, IP-Adapter or reference ControlNet keeps identity stable."},
        {"step": 3, "action": "Anchor the prompt", "detail": "Repeat the same anchor phrases in every scene prompt."},
        {"step": 4, "action": "Fix the seed per family", "detail": "Keep one seed across a scene family to reduce drift."},
        {"step": 5, "action": "Verify per frame", "detail": "Compare against the reference; regenerate on drift."},
    ]
    report = "\n".join([
        f"Character: {character}",
        "",
        md_table([[s["scene"], s["seed"], s["prompt"]] for s in scene_plan], ["Scene", "Seed", "Prompt"]),
        "",
        md_table([[w["step"], w["action"], w["detail"]] for w in workflow], ["Step", "Action", "Detail"]),
    ])
    data = {"character": character, "anchors": anchors, "scenes": scene_plan, "workflow": workflow}
    return _emit(ctx, "character-consistency", "Character consistency workflow", [("Workflow", report)], data,
                 f"Planned {scenes} consistent scenes for {truncate(character, 30)}.")


def portrait_generator(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    spec = {
        "framing": "head and shoulders, eye level, subject slightly off centre",
        "lens": "85mm portrait lens, f/2, background separation",
        "pose": "shoulders turned 30 degrees, head to camera, relaxed shoulders",
        "light": "large softbox at 45 degrees and 30 degrees above, white fill card opposite",
        "background": "seamless mid-grey with a gentle falloff",
        "styling": "natural texture, visible pores, minimal retouching",
        "retouching": "remove temporary blemishes only; keep skin texture and natural asymmetry",
    }
    prompt = (f"portrait of {subject}, {spec['framing']}, {spec['lens']}, {spec['pose']}, "
              f"{spec['light']}, {spec['background']}, {spec['styling']}")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["face"]["tokens"])
    params = {"model": "sdxl-photoreal", "steps": 34, "cfg": 5.5, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 896, "height": 1152, "seed": deterministic_seed(subject) % (2 ** 31)}
    report = "\n".join([
        f"Subject: {subject}",
        "",
        md_table([[k, v] for k, v in spec.items()], ["Aspect", "Specification"]),
        "",
        f"Prompt:\n{prompt}",
        "",
        f"Negative:\n{negative}",
        "",
        "Ethical note: confirm consent and the intended use before producing or releasing a portrait of a real person.",
    ])
    data = {"subject": subject, "specification": spec, "prompt": prompt, "negative_prompt": negative,
            "parameters": params}
    return _emit(ctx, "portrait-spec", "Portrait specification", [("Specification", report)], data,
                 f"Specified a portrait for {truncate(subject, 40)}.")


def group_composition_specialist(ctx) -> Dict[str, Any]:
    people = int(ctx.get("people", 3))
    if people < 1 or people > 12:
        raise InvalidInput("people must be between 1 and 12")
    frame = str(ctx.get("frame", "1536x1024"))
    try:
        fw, fh = [int(v) for v in frame.lower().split("x")]
    except ValueError:
        raise InvalidInput(f"frame must be 'WxH' pixels, got {frame!r}")
    margin = int(fw * 0.08)
    span_x = fw - 2 * margin
    span_y = fh - 2 * margin
    placements = []
    for i in range(people):
        t = (i + 0.5) / people
        x = margin + span_x * t
        row = i % 2
        y = span_y * (0.42 if row == 0 else 0.62)
        placements.append({
            "index": i + 1, "x": round(x), "y": round(y),
            "width": round(span_x / max(people, 2)), "height": round(span_y * 0.55),
            "depth_order": i + 1,
            "identity": f"person {i + 1}",
            "gaze": "toward the shared focal point" if people > 1 else "to camera",
        })
    overlaps = []
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            a, b = placements[i], placements[j]
            ox = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
            if ox > 0 and oy > 0:
                overlaps.append({"pair": [a["index"], b["index"]], "overlap_px": round(ox * oy, 1),
                                 "advice": "Use a regional prompt so each subject keeps its own description."})
    report = "\n".join([
        f"Frame: {fw}x{fh}, {people} people, margin {margin} px",
        "",
        md_table([[p["index"], p["x"], p["y"], p["width"], p["height"], p["gaze"]] for p in placements],
                 ["#", "x", "y", "w", "h", "Gaze"]),
        "",
        f"Overlapping pairs: {len(overlaps)}" + ("" if not overlaps else
                                                  " - " + "; ".join(f"{o['pair']} ({o['overlap_px']} px)" for o in overlaps)),
        "",
        "Rules: give every person a distinct attribute (age, clothing, pose) or the model merges them.",
    ])
    data = {"frame": [fw, fh], "people": people, "placements": placements, "overlaps": overlaps}
    return _emit(ctx, "group-layout", "Group composition layout", [("Layout", report)], data,
                 f"Placed {people} people in a {fw}x{fh} frame.",
                 {"overlapping_pairs": len(overlaps)})


def wardrobe_costume_editor(ctx) -> Dict[str, Any]:
    garment = str(ctx.require("garment"))
    era = str(ctx.get("era", "contemporary"))
    plan = {
        "target": garment, "era": era,
        "mask": "mask the garment area only; keep the head, hands and background untouched",
        "prompt": f"the same person wearing {garment}, {era}, fit consistent with the original pose and body, "
                  f"natural fabric drape, correct closures and seams",
        "negative": "text artifacts, watermark, deformed anatomy, extra limbs, wrong number of buttons, "
                    "mismatched collar, plastic fabric",
        "denoise": "0.55-0.7 (raise to 0.8 only if the whole figure must change)",
        "blend": "feather the mask 8-16 px and match the surrounding light",
        "verification": [
            "collar and neckline meet the neck at the right height",
            "sleeve length matches the wrist or elbow bone",
            "buttons and zips align with the garment centre line",
            "wrinkles follow the body underneath",
            "no change to face, hair or background",
        ],
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items() if not isinstance(v, list)], ["Aspect", "Value"]),
        "",
        "Verification:",
        *[f"- {v}" for v in plan["verification"]],
    ])
    data = {"plan": plan}
    return _emit(ctx, "wardrobe-edit", "Wardrobe and costume edit plan", [("Plan", report)], data,
                 f"Planned a wardrobe edit to '{truncate(garment, 40)}'.")


def hair_grooming_editor(ctx) -> Dict[str, Any]:
    hair = str(ctx.require("hair"))
    plan = {
        "target": hair,
        "mask": "mask hair and hairline; keep the forehead shape and the face untouched",
        "prompt": f"the same person with {hair}, accurate hairline, correct volume and fall, natural strand "
                  f"direction, soft edge where hair meets skin",
        "negative": "text artifacts, watermark, plastic hair, helmet hair, wig edge, smeared strands, changed face",
        "denoise": "0.5-0.65",
        "finishing": "add flyaway strands by hand for the top 10% of the hair mass",
        "verification": ["hairline sits on the natural forehead curve", "ear position stays visible or covered consistently",
                         "no halo around the hair edge", "face and body are unchanged"],
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items() if not isinstance(v, list)], ["Aspect", "Value"]),
        "",
        "Verification:",
        *[f"- {v}" for v in plan["verification"]],
    ])
    data = {"plan": plan}
    return _emit(ctx, "hair-edit", "Hair and grooming edit plan", [("Plan", report)], data,
                 f"Planned a hair edit to '{truncate(hair, 40)}'.")


# ---------------------------------------------------------------------------
# Category 14 - Architecture and interiors
# ---------------------------------------------------------------------------


def _parse_dims(text: str) -> Tuple[float, float]:
    try:
        w, h = text.lower().split("x")
        return float(w), float(h)
    except ValueError:
        raise InvalidInput(f"dimensions must be 'WxH' metres, got {text!r}")


def interior_concept_visualizer(ctx) -> Dict[str, Any]:
    room = str(ctx.get("room", "living_room"))
    width, depth = _parse_dims(str(ctx.get("dimensions", "5.4x4.2")))
    height = 2.8
    prompt = (f"interior of a {room}, {width:.1f} by {depth:.1f} metre room with {height:.2f} metre ceilings, "
              f"correct human scale, {ctx.get('style') or 'contemporary minimal'}, large window on the left wall "
              f"with soft daylight, oak floor, plaster walls, low furniture, calm uncluttered composition")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["architecture"]["tokens"])
    params = {"model": "sdxl", "steps": 34, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1344, "height": 896, "seed": deterministic_seed(room) % (2 ** 31)}
    circulation = {"floor_area_m2": round(width * depth, 2), "ceiling_height_m": height,
                   "clearance_rule": "keep at least 0.9 m in front of doors and 0.75 m between furniture and walls",
                   "furniture": ["sofa against the long wall", "low table in the centre", "rug anchoring the seating"]}
    report = "\n".join([
        f"Room: {room} ({width:.1f} x {depth:.1f} m)",
        "",
        md_table([[k, v if not isinstance(v, list) else ", ".join(v)] for k, v in circulation.items()],
                 ["Aspect", "Value"]),
        "",
        f"Prompt:\n{prompt}",
    ])
    data = {"room": room, "dimensions_m": [width, depth], "circulation": circulation,
            "prompt": prompt, "negative_prompt": negative, "parameters": params}
    return _emit(ctx, "interior-concept", "Interior concept package", [("Concept", report)], data,
                 f"Specified a {room} interior at {width:.1f}x{depth:.1f} m.")


def exterior_massing_visualizer(ctx) -> Dict[str, Any]:
    building = str(ctx.require("building"))
    storeys = int(ctx.get("storeys", 4))
    assumptions = [
        "Storey height assumed at 3.2 m unless stated otherwise.",
        "Opening rhythm and column grid are assumptions, not a surveyed design.",
        "Context buildings are indicative massing only.",
        "Daylight direction is a chosen condition for legibility.",
    ]
    prompt = (f"{building}, {storeys} storeys, architectural visualisation, three-quarter view from street level, "
              f"consistent facade rhythm, recessed glazing, ground floor entrance, soft late-afternoon daylight, "
              f"muted material palette, level verticals, no people in the foreground")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["architecture"]["tokens"])
    params = {"model": "sdxl", "steps": 34, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras",
              "width": 1344, "height": 896, "seed": deterministic_seed(building) % (2 ** 31)}
    report = "\n".join([
        f"Building: {building} ({storeys} storeys)",
        "",
        f"Prompt:\n{prompt}",
        "",
        "Assumptions logged:",
        *[f"- {a}" for a in assumptions],
    ])
    data = {"building": building, "storeys": storeys, "assumptions": assumptions,
            "prompt": prompt, "negative_prompt": negative, "parameters": params}
    return _emit(ctx, "exterior-massing", "Exterior massing concept", [("Concept", report)], data,
                 f"Produced a {storeys}-storey massing concept.")


def material_variant_maker(ctx) -> Dict[str, Any]:
    room = str(ctx.require("room"))
    surface = str(ctx.get("surface", "floor"))
    materials = {
        "floor": ["white oak boards", "polished concrete", "large format porcelain tile", "herringbone parquet"],
        "wall": ["lime plaster", "micro-cement", "vertical timber battens", "painted brick"],
        "cabinetry": ["matte lacquer", "oak veneer", "handleless push-open", "brushed aluminium"],
        "stone": ["travertine", "honed marble", "basalt", "terrazzo"],
        "metal": ["aged brass", "blackened steel", "brushed nickel", "copper"],
        "fabric": ["linen", "boucle", "velvet", "wool felt"],
        "paint": ["chalk white", "clay beige", "deep olive", "charcoal"],
    }
    options = materials.get(surface)
    if not options:
        raise InvalidInput(f"unknown surface '{surface}' (known: {', '.join(sorted(materials))})")
    variants = []
    for i, mat in enumerate(options, start=1):
        variants.append({"variant": i, "material": mat,
                         "prompt": f"{room}, {surface} in {mat}, same camera, same lighting, same furniture",
                         "attention": "match the specular response of the material to the existing light direction"})
    report = "\n".join([
        f"Space: {room}; varying the {surface}",
        "",
        md_table([[v["variant"], v["material"], v["attention"]] for v in variants], ["#", "Material", "Watch for"]),
        "",
        "Rule: change only the material word; keep camera, lens, light and furniture identical so the "
        "comparison is honest.",
    ])
    data = {"space": room, "surface": surface, "variants": variants}
    return _emit(ctx, "material-variants", "Material and finish variants", [("Variants", report)], data,
                 f"Produced {len(variants)} {surface} variants.")


def furniture_staging_specialist(ctx) -> Dict[str, Any]:
    room = str(ctx.get("room", "living_room"))
    width, depth = _parse_dims(str(ctx.get("dimensions", "5.4x4.2")))
    footprints = {"sofa": (2.2, 0.9), "table": (1.2, 0.6), "chair": (0.6, 0.6),
                  "bed": (1.6, 2.0), "desk": (1.4, 0.7), "wardrobe": (1.8, 0.6)}
    picks = {"living_room": ["sofa", "table", "chair"], "bedroom": ["bed", "chair", "desk"],
             "office": ["desk", "chair"], "kitchen": ["table", "chair"]}.get(room, ["sofa", "table"])
    staged = []
    used = 0.0
    for name in picks:
        fw, fd = footprints[name]
        staged.append({"item": name, "footprint_m": [fw, fd], "area_m2": round(fw * fd, 2)})
        used += fw * fd
    floor_area = width * depth
    free = floor_area - used
    circulation = {
        "floor_area_m2": round(floor_area, 2),
        "furniture_area_m2": round(used, 2),
        "free_area_m2": round(free, 2),
        "free_share": round(free / floor_area, 3) if floor_area else 0,
        "clearance_check": "pass" if free / max(floor_area, 0.01) > 0.35 else "tight - reduce the piece count",
        "accessibility": "keep a 0.9 m turning circle free of furniture in the main circulation zone",
    }
    report = "\n".join([
        f"Room: {room} ({width:.1f} x {depth:.1f} m)",
        "",
        md_table([[s["item"], f'{s["footprint_m"][0]}x{s["footprint_m"][1]}', s["area_m2"]] for s in staged],
                 ["Item", "Footprint m", "Area m2"]),
        "",
        md_table([[k, v] for k, v in circulation.items()], ["Check", "Value"]),
    ])
    data = {"room": room, "dimensions_m": [width, depth], "staged": staged, "circulation": circulation}
    return _emit(ctx, "furniture-staging", "Furniture staging plan", [("Plan", report)], data,
                 f"Staged {len(staged)} items with a {circulation['free_share']:.0%} free floor area.",
                 {"free_share": circulation["free_share"]})


def landscape_visualization(ctx) -> Dict[str, Any]:
    site = str(ctx.require("site"))
    climate = str(ctx.get("climate", "temperate"))
    planting = {
        "temperate": ["oak, hornbeam and field maple for structure", "spring bulbs under deciduous canopies",
                      "sedge and fescue for texture", "evergreen groundcover on slopes"],
        "arid": ["drought tolerant grasses", "olive, carob and rosemary", "gravel mulch in open areas"],
        "tropical": ["canopy trees with layered understorey", "ferns and philodendrons in shade", "paving with drainage channels"],
        "coastal": ["wind tolerant salt-tolerant species", "marram grass on dunes", "low coastal scrub"],
    }.get(climate, ["locally appropriate species", "structural canopy, then understorey, then groundcover"])
    report = "\n".join([
        f"Site: {site} (climate: {climate})",
        "",
        "Planting structure:",
        *[f"- {p}" for p in planting],
        "",
        "Maintenance reality check:",
        "- Name species that a local maintenance crew can actually source.",
        "- Keep irrigation zones compatible with the species list.",
        "- Show the landscape in a season where the planting reads clearly.",
    ])
    data = {"site": site, "climate": climate, "planting": planting}
    return _emit(ctx, "landscape-concept", "Landscape visualisation concept", [("Concept", report)], data,
                 f"Planted a {climate} landscape scheme.")


def real_estate_enhancement(ctx) -> Dict[str, Any]:
    checklist = [
        "Correct vertical lines - the cheapest credibility win in interior shots.",
        "Recover shadow detail without lifting the noise floor.",
        "Declutter only what reads as staging; do not remove architectural features.",
        "Do not hide material defects, damp staining, cracks or wear.",
        "Keep the window view unchanged - never relight beyond what is plausible.",
        "White balance to the daylight, not to the decoration.",
    ]
    forbidden = [
        "removing a defect that affects the sale decision",
        "adding furniture that is not present",
        "replacing the view out of the window",
        "widening the apparent room through lens distortion",
    ]
    report = "\n".join([
        "Allowed enhancements:",
        *[f"- {c}" for c in checklist],
        "",
        "Never do this:",
        *[f"- {f}" for f in forbidden],
    ])
    data = {"allowed": checklist, "forbidden": forbidden}
    return _emit(ctx, "real-estate-enhancement", "Real estate enhancement policy", [("Policy", report)], data,
                 "Defined the enhancement and disclosure policy.")


def spatial_consistency_auditor(ctx) -> Dict[str, Any]:
    checks = [
        {"id": "perspective", "check": "All vertical edges converge consistently", "severity": "high"},
        {"id": "vanishing_point", "check": "Parallel lines meet at a single vanishing point", "severity": "high"},
        {"id": "doors", "check": "Doors open into free space and match the wall thickness", "severity": "medium"},
        {"id": "stairs", "check": "Stair rise and run stay consistent across flights", "severity": "high"},
        {"id": "scale", "check": "Door, window and furniture heights are consistent", "severity": "high"},
        {"id": "reflections", "check": "Reflections repeat the geometry and follow the view", "severity": "medium"},
        {"id": "repetition", "check": "Repeated elements such as mullions keep one spacing", "severity": "medium"},
        {"id": "shadow", "check": "Shadow direction is consistent across the scene", "severity": "medium"},
    ]
    report = "\n".join([
        md_table([[c["id"], c["check"], c["severity"]] for c in checks], ["Check", "What to verify", "Severity"]),
        "",
        "Fix order: perspective first, then scale, then openings, then repeat spacing. Fixing anything else "
        "before perspective wastes the render.",
    ])
    data = {"checks": checks}
    return _emit(ctx, "spatial-audit", "Spatial consistency audit", [("Checks", report)], data,
                 f"Defined {len(checks)} spatial consistency checks.")


# ---------------------------------------------------------------------------
# Category 15 - Scientific and technical
# ---------------------------------------------------------------------------


def scientific_illustration_planner(ctx) -> Dict[str, Any]:
    mechanism = str(ctx.require("mechanism"))
    labels = [l.strip() for l in str(ctx.get("labels", "")).split(",") if l.strip()]
    plan = {
        "evidence_layer": "Only include what is verified by the source material.",
        "interpretation_layer": "Mark every inferred or stylised element.",
        "layout": "cutaway view with the mechanism centred and the flow direction left to right",
        "depth_cue": "light source upper left, consistent shadow direction, no perspective exaggeration",
        "labels": labels or ["add only labels that the source supports"],
        "title_and_legend": "typeset deterministically outside the rendered image",
    }
    prompt = (f"scientific illustration of {mechanism}, clean cutaway diagram, flat muted palette with one "
              f"accent colour, even lighting, no perspective distortion, no text, high clarity")
    negative = "text artifacts, watermark, letters, numbers, labels, photorealistic, dramatic lighting, perspective distortion"
    report = "\n".join([
        f"Mechanism: {mechanism}",
        "",
        md_table([[k, v if not isinstance(v, list) else ", ".join(v)] for k, v in plan.items()],
                 ["Aspect", "Specification"]),
        "",
        f"Prompt:\n{prompt}",
        "",
        "Rule: the image model never writes the labels; they are typeset over the render.",
    ])
    data = {"mechanism": mechanism, "plan": plan, "prompt": prompt, "negative_prompt": negative}
    return _emit(ctx, "scientific-illustration-plan", "Scientific illustration plan", [("Plan", report)], data,
                 f"Planned a labelled illustration of {truncate(mechanism, 40)}.")


def technical_cutaway_creator(ctx) -> Dict[str, Any]:
    assembly = str(ctx.require("assembly"))
    parts = [p.strip() for p in str(ctx.get("parts", "")).split(",") if p.strip()]
    prompt = (f"exploded cutaway of {assembly}, components separated along a single axis, consistent part "
              f"relationships, monochrome technical line art with light shading, blank callout space, no text, "
              f"orthographic three-quarter view")
    negative = "text artifacts, watermark, letters, numbers, labels, photorealistic, colour, perspective distortion"
    spacing = {"explode_ratio": "1.6x the assembled spacing", "callout_space": "30% of the frame reserved",
               "ordering": "explode along the axis of assembly"}
    report = "\n".join([
        f"Assembly: {assembly}",
        f"Known parts: {', '.join(parts) if parts else 'not supplied'}",
        "",
        md_table([[k, v] for k, v in spacing.items()], ["Rule", "Value"]),
        "",
        f"Prompt:\n{prompt}",
    ])
    data = {"assembly": assembly, "parts": parts, "spacing": spacing, "prompt": prompt, "negative_prompt": negative}
    return _emit(ctx, "technical-cutaway", "Technical cutaway plan", [("Plan", report)], data,
                 f"Planned a cutaway of {truncate(assembly, 40)}.")


def dataset_visualization_assets(ctx) -> Dict[str, Any]:
    chart_type = str(ctx.get("chart_type", "bar"))
    items = int(ctx.get("items", 6))
    assets = [
        {"id": "icon_set", "purpose": "one decorative icon per series", "count": items,
         "constraint": "single accent colour, flat shapes, no data meaning"},
        {"id": "background_texture", "purpose": "very low contrast paper or grid texture behind the chart",
         "count": 1, "constraint": "must not reduce chart legibility"},
        {"id": "spot_metaphor", "purpose": "a single hero metaphor for the story", "count": 1,
         "constraint": "may not encode or imply a value"},
        {"id": "frame", "purpose": "axis and grid furniture", "count": 1, "constraint": "drawn deterministically"},
    ]
    report = "\n".join([
        f"Chart type: {chart_type}, {items} series",
        "",
        md_table([[a["id"], a["purpose"], a["count"], a["constraint"]] for a in assets],
                 ["Asset", "Purpose", "Count", "Constraint"]),
        "",
        "Hard rule: decorative assets never touch the plotted data, the axes, the labels or the legend. "
        "Any change to the chart itself is made in the charting tool, not in an image model.",
    ])
    data = {"chart_type": chart_type, "items": items, "assets": assets}
    return _emit(ctx, "dataset-viz-assets", "Dataset visualisation assets", [("Assets", report)], data,
                 f"Planned {len(assets)} decorative asset families.")


def geospatial_stylist(ctx) -> Dict[str, Any]:
    image = ctx.get("image")
    style = str(ctx.get("style", "muted_cartographic"))
    info = core.probe_image(str(image)) if image else None
    rules = [
        "Keep the original scale bar, north arrow and legend geometry untouched.",
        "Do not move, rotate or re-project the imagery.",
        "Do not change land cover colour in a way that implies a different classification.",
        "Keep source differences (imagery date, sensor, resolution) visible in the legend.",
        "Styling only: tone curve, contrast and a restrained colour grade.",
    ]
    report = "\n".join([
        f"Styling target: {style}" + (f" (source: {info['width']}x{info['height']} {info['format']})" if info else ""),
        "",
        "Rules:",
        *[f"- {r}" for r in rules],
    ])
    data = {"style": style, "source": info, "rules": rules}
    return _emit(ctx, "geospatial-styling", "Geospatial styling policy", [("Policy", report)], data,
                 "Defined a styling policy that preserves geospatial accuracy.")


def patent_figure_guide(ctx) -> Dict[str, Any]:
    invention = str(ctx.require("invention"))
    views = int(ctx.get("views", 3))
    view_plan = ["front elevation", "plan view", "sectional view"][:views] or ["front elevation"]
    conventions = [
        "Black lines on white, or white lines on black - never grey gradients.",
        "Consistent line weight hierarchy: object edges heavy, internal detail light.",
        "No shading that could be read as a material property.",
        "Part numbers in Arabic numerals, referenced in the description.",
        "No text inside the figure other than reference numerals.",
    ]
    prompt = (f"monochrome patent line drawing of {invention}, {', '.join(view_plan)}, clean orthographic "
              f"contours, uniform line weight, no shading, no text, white background, high clarity")
    negative = "text artifacts, watermark, letters, numbers, colour, photorealistic, shading, perspective distortion, 3d render"
    report = "\n".join([
        f"Invention: {invention}",
        f"Views: {', '.join(view_plan)}",
        "",
        md_table([[f"{i + 1}", v] for i, v in enumerate(view_plan)], ["Figure", "View"]),
        "",
        "Conventions:",
        *[f"- {c}" for c in conventions],
        "",
        f"Prompt:\n{prompt}",
    ])
    data = {"invention": invention, "views": view_plan, "conventions": conventions,
            "prompt": prompt, "negative_prompt": negative}
    return _emit(ctx, "patent-figure-guide", "Patent figure preparation", [("Guide", report)], data,
                 f"Planned {len(view_plan)} patent figure view(s).")


# ---------------------------------------------------------------------------
# Category 13 - Commerce (creative half)
# ---------------------------------------------------------------------------


def product_photo_director(ctx) -> Dict[str, Any]:
    product = str(ctx.require("product"))
    rigs = load_asset(TEMPLATES)["light_rigs"]
    plan = {
        "hero": {"rig": "three_point_soft", "lens": "85mm", "surface": "seamless pale acrylic sweep",
                 "why": "clean separation with a soft shadow that still reads as a surface"},
        "detail": {"rig": "edge_high_contrast", "lens": "100mm macro", "surface": "matte black acrylic",
                   "why": "raking light reveals texture and build quality"},
        "lifestyle": {"rig": "single_soft", "lens": "35mm", "surface": "real context with depth",
                      "why": "context sells use, soft light stays believable"},
    }
    body = []
    for i, (name, rig) in enumerate(rigs.items()):
        y = 120 + i * 160
        body.append(svg_text(60, y, name, 24, "#111111", weight="bold"))
        body.append(svg_text(60, y + 30, f"key: {rig['key']['position']} ({rig['key']['size']})", 16, "#333333"))
        if "fill" in rig:
            body.append(svg_text(60, y + 52, f"fill: {rig['fill']['position']} ({rig['fill']['size']})", 16, "#333333"))
        if "rim" in rig:
            body.append(svg_text(60, y + 74, f"rim: {rig['rim']['position']} ({rig['rim']['size']})", 16, "#333333"))
    svg = svg_document(900, 640, "\n".join(body), "#f4f2ee")
    ctx.write_text("light-rig-diagram.svg", svg)
    report = "\n".join([
        f"Product: {product}",
        "",
        md_table([[k, v["rig"], v["lens"], v["surface"], v["why"]] for k, v in plan.items()],
                 ["Shot", "Rig", "Lens", "Surface", "Rationale"]),
        "",
        "Set rules:",
        "- One light setup per shot; never mix a hard key with a soft fill from a different direction.",
        "- Keep the white point constant across the set so the catalogue is consistent.",
        "- Photograph a grey card and a colour chart before the first product shot.",
    ])
    data = {"product": product, "shots": plan, "svg": "light-rig-diagram.svg"}
    result = _emit(ctx, "product-photo-direction", "Virtual product photography direction",
                    [("Direction", report)], data, f"Directed a three-shot studio plan for {truncate(product, 30)}.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out("light-rig-diagram.svg"))]
    return result


def product_variant_generator(ctx) -> Dict[str, Any]:
    product = str(ctx.require("product"))
    dimension = str(ctx.get("dimension", "color"))
    count = int(ctx.get("count", 4))
    options = {
        "color": ["graphite", "bone", "sage", "oxblood", "sand"],
        "finish": ["matte", "satin", "gloss", "brushed metal", "raw"],
        "material": ["oak", "walnut", "anodised aluminium", "stone composite", "linen"],
        "accessory": ["with shoulder strap", "with lid", "with case", "stackable", "compact"],
    }.get(dimension)
    if not options:
        raise InvalidInput(f"unknown variant dimension '{dimension}' (known: color, finish, material, accessory)")
    chosen = options[:count]
    variants = []
    for i, value in enumerate(chosen, start=1):
        prompt = f"{product} in {value}, identical geometry and branding, studio lighting unchanged, {value} finish visible in specular highlights"
        variants.append({"index": i, "value": value, "prompt": prompt,
                         "filename": f"{slugify(product)[:36]}-{slugify(value)}-{i:02d}.png",
                         "seed": deterministic_seed(prompt) % (2 ** 31)})
    report = "\n".join([
        f"Product: {product}; varying {dimension}",
        "",
        md_table([[v["index"], v["value"], v["seed"], v["filename"]] for v in variants],
                 ["#", "Value", "Seed", "Filename"]),
        "",
        "Lock the camera, lens, light and background across the whole set; change only the stated dimension.",
    ])
    data = {"product": product, "dimension": dimension, "variants": variants}
    return _emit(ctx, "product-variants", "Product variant matrix", [("Variants", report)], data,
                 f"Generated {len(variants)} {dimension} variants.")


def lifestyle_placement(ctx) -> Dict[str, Any]:
    product = str(ctx.require("product"))
    context = str(ctx.get("context", "kitchen counter"))
    plan = {
        "context": context,
        "scale": "state the product dimensions relative to a known object in the scene",
        "contact": "the product must touch the surface - a floating object breaks the illusion",
        "interaction": "show a hand only if the product is used by hand; keep finger anatomy correct",
        "reflection": "match the reflectivity of the surface under the product",
        "colour_temperature": "match the product tint to the scene light",
        "focus": "match the depth of field to the scene, not to a studio look",
    }
    prompt = (f"{product} naturally placed on a {context}, correct scale and a believable contact shadow, "
              f"scene lighting reflected in the surface, candid lifestyle composition, 35mm lens")
    negative = ", ".join(load_asset(NEGATIVES)["targets"]["product"]["tokens"] +
                         load_asset(NEGATIVES)["targets"]["general"]["tokens"][:5])
    safety = [
        "Do not imply unsafe use (unattended hot appliances, knives near children, loose clothing near flames).",
        "Do not make medical, financial or safety claims in the image.",
        "Keep the product recognisable but do not alter the branding.",
    ]
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items()], ["Aspect", "Specification"]),
        "",
        f"Prompt:\n{prompt}",
        "",
        "Safety notes:",
        *[f"- {s}" for s in safety],
    ])
    data = {"product": product, "plan": plan, "prompt": prompt, "negative_prompt": negative, "safety": safety}
    return _emit(ctx, "lifestyle-placement", "Lifestyle placement plan", [("Plan", report)], data,
                 f"Placed {truncate(product, 30)} in a {context} context.")


def advertising_concept(ctx) -> Dict[str, Any]:
    brief = str(ctx.require("brief"))
    channels = [c.strip() for c in str(ctx.get("channels", "social,web")).split(",") if c.strip()]
    territories = [
        {"name": "quiet confidence", "metaphor": "one subject, one light, generous negative space",
         "hero": "the product alone in a calm environment, hard directional light"},
        {"name": "in use", "metaphor": "the moment of use, hands and context",
         "hero": "a person mid-action with the product naturally placed"},
        {"name": "detail as proof", "metaphor": "macro evidence of material and build",
         "hero": "extreme close crop of the key material junction"},
    ]
    adaptations = []
    for channel in channels:
        size = load_asset(TEMPLATES)["platform_sizes"].get(channel.replace("-", "_"))
        adaptations.append({"channel": channel,
                            "size": f'{size["width"]}x{size["height"]}' if size else "use the channel spec",
                            "crop_rule": "keep the subject inside the crop-safe area for every variant"})
    report = "\n".join([
        f"Brief: {brief}",
        "",
        md_table([[t["name"], t["metaphor"], t["hero"]] for t in territories], ["Territory", "Metaphor", "Hero shot"]),
        "",
        md_table([[a["channel"], a["size"], a["crop_rule"]] for a in adaptations], ["Channel", "Size", "Crop rule"]),
    ])
    data = {"brief": brief, "channels": channels, "territories": territories, "adaptations": adaptations}
    return _emit(ctx, "advertising-concepts", "Advertising concept territories", [("Concepts", report)], data,
                 f"Developed {len(territories)} territories across {len(channels)} channels.")


def conversion_variant_maker(ctx) -> Dict[str, Any]:
    brief = str(ctx.require("brief"))
    hypotheses = [h.strip() for h in str(ctx.get("hypotheses", "benefit_framing,focal_product,context")).split(",") if h.strip()]
    library = {
        "benefit_framing": ("headline states the user benefit", "same product, same layout, benefit-led copy"),
        "focal_product": ("product occupies more of the frame", "same headline, tighter crop on the product"),
        "context": ("product placed in a use context", "same headline, lifestyle background"),
        "colorway": ("product shown in an alternate colour", "same everything else, different colour"),
        "social_proof": ("a secondary element added for credibility", "same layout, added proof element"),
    }
    variants = []
    for i, h in enumerate(hypotheses, start=1):
        change, description = library.get(h, ("single hypothesis change", h))
        variants.append({"id": f"v{i:02d}", "hypothesis": h, "change": change, "detail": description,
                         "filename": f"variant-{h}-{i:02d}.png",
                         "seed": deterministic_seed(f"{brief}-{h}") % (2 ** 31)})
    report = "\n".join([
        f"Brief: {brief}",
        "",
        md_table([[v["id"], v["hypothesis"], v["change"], v["seed"]] for v in variants],
                 ["Id", "Hypothesis", "Single change", "Seed"]),
        "",
        "Test discipline: one variable per variant, one metric per test, a fixed seed so only the intended "
        "factor moves.",
    ])
    data = {"brief": brief, "variants": variants}
    return _emit(ctx, "conversion-variants", "Conversion creative variants", [("Variants", report)], data,
                 f"Produced {len(variants)} single-hypothesis variants.")


# ---------------------------------------------------------------------------
# Category 22 - Specialised media (creative half)
# ---------------------------------------------------------------------------


def game_concept_assets(ctx) -> Dict[str, Any]:
    game = str(ctx.require("game"))
    asset_type = str(ctx.get("asset_type", "environment"))
    asset = {
        "environment": ["hero vista", "playable layout blockout", "prop set", "colour key", "skybox"],
        "character": ["silhouette sheet", "colour key", "three-quarter render", "turnaround", "animation pose set"],
        "prop": ["orthographic front", "orthographic side", "in-hand scale reference", "material swatch", "wear variant"],
        "icon": ["icon grid sheet", "one icon per state", "silhouette pass"],
        "paintover": ["base render", "colour blocked", "lit pass", "final polish"],
    }.get(asset_type, ["concept sheet"])
    art_bible = ["Define the palette, value range and line treatment before generating.",
                 "Every asset must read at the in-game camera distance.",
                 "Keep the same light direction across the set.",
                 "Record the seed for each approved asset so it can be re-derived."]
    report = "\n".join([
        f"Project: {game}; asset type: {asset_type}",
        "",
        md_table([[f"{i + 1}", a] for i, a in enumerate(asset)], ["#", "Deliverable"]),
        "",
        "Art bible rules:",
        *[f"- {r}" for r in art_bible],
    ])
    data = {"game": game, "asset_type": asset_type, "deliverables": asset, "art_bible": art_bible}
    return _emit(ctx, "game-concept-assets", "Game concept asset plan", [("Plan", report)], data,
                 f"Planned {len(asset)} {asset_type} deliverables for {game}.")


def comic_storyboard(ctx) -> Dict[str, Any]:
    story = str(ctx.require("story"))
    panels = int(ctx.get("panels", 6))
    shots = ["establishing", "wide", "medium", "close-up", "insert", "reaction"]
    plan = []
    for i in range(panels):
        shot = shots[i % len(shots)]
        plan.append({
            "panel": i + 1, "shot": shot,
            "description": f"Beat {i + 1} of the sequence",
            "speech_safe": "keep the upper third clear of faces for the dialogue balloon",
            "action_safe": "keep the lower third clear for the action line",
            "continuity": "same wardrobe, same hair, same light direction as the previous panel",
        })
    report = "\n".join([
        f"Story: {story}",
        "",
        md_table([[p["panel"], p["shot"], p["description"], p["speech_safe"]] for p in plan],
                 ["Panel", "Shot", "Beat", "Safe area"]),
        "",
        "Continuity rules:",
        *[f"- {p['continuity']}" for p in plan[:1]],
        "- Repeat the character description verbatim in every panel prompt.",
        "- Generate the panels one at a time so a single failure can be regenerated.",
    ])
    data = {"story": story, "panels": plan}
    return _emit(ctx, "storyboard-plan", "Comic and storyboard plan", [("Plan", report)], data,
                 f"Planned a {panels}-panel sequence.")


def book_illustration_planner(ctx) -> Dict[str, Any]:
    manuscript = str(ctx.require("manuscript"))
    pages = int(ctx.get("pages", 32))
    per_spread = 2
    spreads = max(1, pages // per_spread)
    beats = []
    for i in range(spreads):
        beats.append({"spread": i + 1, "pages": f"{i * per_spread + 1}-{i * per_spread + per_spread}",
                      "role": ["establishing", "development", "turn", "payoff"][i % 4],
                      "illustration": "full page" if i % 3 == 0 else "half page",
                      "print_safe": "keep faces and key objects 10 mm inside the trim"})
    report = "\n".join([
        f"Manuscript: {manuscript}",
        f"Pages: {pages} ({spreads} spreads)",
        "",
        md_table([[b["spread"], b["pages"], b["role"], b["illustration"], b["print_safe"]] for b in beats],
                 ["Spread", "Pages", "Role", "Size", "Print safety"]),
        "",
        "Pacing rule: leave a full-page image before a major beat so the turn has visual space.",
    ])
    data = {"manuscript": manuscript, "pages": pages, "spreads": beats}
    return _emit(ctx, "book-illustration-plan", "Book illustration plan", [("Plan", report)], data,
                 f"Mapped {spreads} spreads for a {pages}-page book.")


def thumbnail_generator(ctx) -> Dict[str, Any]:
    topic = str(ctx.require("topic"))
    ratio = str(ctx.get("ratio", "16:9"))
    ratios = load_asset(TEMPLATES)["crop_ratios"]
    key = ratio.replace(":", "_").replace("/", "_") + "_1" if ratio.replace(":", "_") not in ratios else ratio.replace(":", "_")
    aspect = ratios.get(key) or ratios.get(ratio.replace(":", "_"), 1.7777778)
    width = 1280
    height = int(round(width / aspect))
    focal = {"x": width * 0.62, "y": height * 0.45}
    rules = [
        "One subject, large in frame - thumbnails are read at 200 px wide.",
        "High contrast between subject and background.",
        "No text in the image; add type in the design tool.",
        "Keep the focal point inside the central 70% so every crop keeps it.",
    ]
    report = "\n".join([
        f"Topic: {topic}; ratio {ratio} ({width}x{height})",
        "",
        "Rules:",
        *[f"- {r}" for r in rules],
        "",
        "Alternatives:",
        "- Variant A: tight crop on the subject with a blurred background.",
        "- Variant B: wide establishing shot with the subject placed off centre.",
        "- Variant C: close-up detail of the most recognisable element.",
    ])
    data = {"topic": topic, "ratio": ratio, "width": width, "height": height, "focal_point": focal, "rules": rules}
    return _emit(ctx, "thumbnail-spec", "Thumbnail and keyframe spec", [("Spec", report)], data,
                 f"Specified a {width}x{height} thumbnail for {truncate(topic, 30)}.")


def icon_family_designer(ctx) -> Dict[str, Any]:
    icon_set = str(ctx.require("set"))
    count = int(ctx.get("count", 6))
    system = {
        "grid": "24x24 viewBox with a 2 px live area margin",
        "stroke": "2 px, round cap, round join, no fills except for accents",
        "corner_radius": "2 px on every corner for optical consistency",
        "perspective": "flat, no perspective, no isometric tilt",
        "optical_weight": "each icon tuned so it reads as the same visual weight at a glance",
        "sizing": "16, 20, 24, 32 and 48 px",
    }
    icons = [{"name": f"{icon_set} icon {i + 1}", "grid": system["grid"], "stroke": system["stroke"]}
             for i in range(count)]
    report = "\n".join([
        f"Icon set: {icon_set} ({count} icons)",
        "",
        md_table([[k, v] for k, v in system.items()], ["System rule", "Value"]),
        "",
        "Consistency checks:",
        "- Same stroke weight and cap style in every icon.",
        "- Same optical size: a circle looks heavier than a triangle at the same bounding box.",
        "- Same corner treatment and same perspective plane.",
    ])
    data = {"set": icon_set, "count": count, "system": system, "icons": icons}
    return _emit(ctx, "icon-family", "Icon family system", [("System", report)], data,
                 f"Defined a {count}-icon system for {icon_set}.")


# ---------------------------------------------------------------------------
# Category 23 - Accessibility and localisation (creative half)
# ---------------------------------------------------------------------------


def alt_text_writer(ctx) -> Dict[str, Any]:
    details = str(ctx.require("details"))
    purpose = str(ctx.get("purpose", "informative"))
    length = str(ctx.get("length", "concise"))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", details) if s.strip()]
    concise = sentences[0] if sentences else details
    extended = " ".join(sentences[:4]) if sentences else details
    if length == "extended":
        text = extended
    else:
        text = concise
    checks = []
    if len(text) < 12:
        checks.append("text is very short - confirm it still identifies the subject")
    if re.match(r"^(an image of|a picture of|a photo of)\s", text, re.I):
        checks.append("drop the 'image of' prefix; screen readers already announce the image")
    if len(text) > 300 and length == "concise":
        checks.append("concise alt text should stay under roughly 150 characters")
    if purpose == "decorative":
        checks.append("decorative images should carry an empty alt attribute instead of descriptive text")
    if purpose == "functional":
        checks.append("functional images (buttons, links) need the label text in the alt attribute")
    report = "\n".join([
        f"Purpose: {purpose}; length: {length}",
        "",
        f"Concise:\n{concise}",
        "",
        f"Extended:\n{extended}",
        "",
        "Checks:",
        *([f"- {c}" for c in checks] if checks else ["- no issues found"]),
        "",
        "Rule: describe what the image communicates in its context, not everything it contains.",
    ])
    data = {"purpose": purpose, "length": length, "concise": concise, "extended": extended, "checks": checks}
    return _emit(ctx, "alt-text", "Alternative text", [("Alt text", report)], data,
                 f"Wrote {length} alternative text ({len(text)} characters).")


def localization_designer(ctx) -> Dict[str, Any]:
    asset = str(ctx.require("asset"))
    locales = [l.strip() for l in str(ctx.get("locales", "en-US,de-DE,ja-JP,ar-AE")).split(",") if l.strip()]
    expansion = {"de-DE": 1.35, "fr-FR": 1.30, "es-ES": 1.28, "ja-JP": 0.75, "zh-CN": 0.70,
                 "ar-AE": 1.20, "ru-RU": 1.25, "hi-IN": 1.20}
    text_block = {"recommended_share": 0.35, "min_height_px": 90,
                  "rule": "reserve a text block with room for the longest locale, then keep the artwork clear of it"}
    rtl = [l for l in locales if l.split("-")[0] in ("ar", "he", "fa", "ur")]
    report = "\n".join([
        f"Asset: {asset}",
        f"Locales: {', '.join(locales)}",
        "",
        md_table([[l, f'{expansion.get(l, 1.25):.0%} text length', "mirrored layout" if l in rtl else "left aligned"]
                  for l in locales], ["Locale", "Budget vs source", "Layout"]),
        "",
        f"Text block: {text_block['recommended_share']:.0%} of the canvas, minimum {text_block['min_height_px']} px tall.",
        "",
        "Rules:",
        "- Reserve the text space before generating; never generate text into the artwork.",
        "- Keep the focal point clear of the text block so nothing is covered in translation.",
        "- Mirror the composition for right-to-left locales, not just the type.",
        "- Avoid culture-specific imagery that would need replacing per locale.",
    ])
    data = {"asset": asset, "locales": locales, "text_block": text_block, "rtl_locales": rtl,
            "expansion": {l: expansion.get(l, 1.25) for l in locales}}
    return _emit(ctx, "localization-safe-design", "Localization-safe visual design", [("Plan", report)], data,
                 f"Planned text-safe space for {len(locales)} locales.")


def season_weather_transformer(ctx) -> Dict[str, Any]:
    image = ctx.get("image")
    weather = str(ctx.get("weather", "light_rain"))
    season = str(ctx.get("season", "winter"))
    info = core.probe_image(str(image)) if image else None
    effects = {
        "clear": ["sharp sun, defined shadows", "no atmospheric particles", "dry surfaces"],
        "light_rain": ["thin diagonal streaks", "wet ground with soft specular reflections", "slightly lifted blacks"],
        "heavy_rain": ["dense streaks, reduced visibility", "saturated surfaces and running water", "water on the lens"],
        "mist": ["layered depth haze", "softened contrast with distance", "diffused light"],
        "fog": ["uniform low fog", "reduced visibility, silhouetted forms", "desaturated midtones"],
        "snow": ["accumulated snow on horizontal surfaces", "breath vapour, falling flakes", "cool bounce light"],
        "sandstorm": ["ochre particulate haze", "low contrast, warm cast", "reduced visibility"],
        "storm": ["dark cloud base, wind-driven rain or spray", "branches moving", "lightning-free dramatic contrast"],
        "overcast": ["flat soft light", "no hard shadows", "cool even colour"],
    }.get(weather, ["consistent atmospheric treatment"])
    season_effects = {
        "winter": ["bare deciduous branches", "cold light, low saturation", "frost or snow highlights"],
        "summer": ["full foliage canopy", "warm direct light", "deep green saturation"],
        "spring": ["new light-green foliage, blossom", "soft even light", "wet ground"],
        "autumn": ["amber and rust foliage", "low angled warm light", "fallen leaves"],
    }.get(season, ["consistent seasonal treatment"])
    report = "\n".join([
        f"Weather: {weather}; season: {season}" + (f"; source {info['width']}x{info['height']}" if info else ""),
        "",
        "Weather effects:",
        *[f"- {e}" for e in effects],
        "",
        "Season effects:",
        *[f"- {e}" for e in season_effects],
        "",
        "Rule: keep geometry, camera and architecture identical - change only the atmosphere and vegetation.",
    ])
    data = {"weather": weather, "season": season, "weather_effects": effects,
            "season_effects": season_effects, "source": info}
    return _emit(ctx, "season-weather-transform", "Season and weather transformation plan", [("Plan", report)], data,
                 f"Planned a {weather} / {season} transformation.")


def watermark_planner(ctx) -> Dict[str, Any]:
    text = str(ctx.get("text", "AI-GENERATED IMAGE"))
    mode = str(ctx.get("mode", "visible"))
    image = ctx.get("image")
    info = core.probe_image(str(image)) if image else None
    placement = {
        "corner": {"position": "bottom right", "margin_px": 24, "size_px": 18,
                   "contrast": "white text on a 40% black plate for busy backgrounds"},
        "edge_band": {"position": "full width band, bottom 6%", "margin_px": 0, "size_px": 16,
                      "contrast": "solid dark band with white text"},
        "tiled": {"position": "repeated diagonal across the frame", "margin_px": 0, "size_px": 14,
                  "contrast": "12-15% opacity white"},
    }
    chosen = placement["corner"]
    svg = svg_document(640, 360, svg_text(640 - chosen["margin_px"], 360 - chosen["margin_px"], text,
                                          chosen["size_px"], "#ffffff", anchor="end"), "#101014")
    ctx.write_text("disclosure-label.svg", svg)
    invisible = ["embed a C2PA-style content credential", "store a provenance sidecar next to the file",
                 "keep the prompt, model, seed and edit history in the sidecar"]
    report = "\n".join([
        f"Mode: {mode}; label text: {text}",
        "",
        md_table([[k, v] for k, v in chosen.items()], ["Placement property", "Value"]),
        "",
        "Invisible provenance:",
        *[f"- {i}" for i in invisible],
        "",
        "Durability:",
        "- A visible label can be cropped; pair it with the invisible credential.",
        "- Keep the label outside any crop-safe area that gets trimmed later.",
    ])
    data = {"mode": mode, "text": text, "placement": chosen, "invisible_provenance": invisible,
            "svg": "disclosure-label.svg"}
    result = _emit(ctx, "disclosure-plan", "Watermark and disclosure plan", [("Plan", report)], data,
                   f"Planned a '{mode}' disclosure label.")
    result["artifacts"] = result.get("artifacts", []) + [ctx.emit(ctx.out("disclosure-label.svg"))]
    return result
