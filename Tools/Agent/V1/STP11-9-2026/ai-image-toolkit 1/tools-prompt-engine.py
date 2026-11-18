#!/usr/bin/env python3
"""Prompt, blueprint and caption operations for ai-image-toolkit (standard library only).

Operations: plan.blueprint, prompt.build, prompt.negative, prompt.debug, prompt.variants,
prompt.weight_check, prompt.brief_translate, prompt.library, prompt.token_estimate,
prompt.binding_check, prompt.glossary, prompt.caption, prompt.eval_suite,
prompt.adherence, prompt.alt_text.

Token counts are approximations for planning only; exact counts depend on the tokenizer of the
model you run. Heuristic results are advisory and need human review.
"""
import hashlib
import importlib.util
import math
import random
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_common():
    name = "aiit_common"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _HERE / "tools-common.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


C = _load_common()
ToolError, P, op, write_json, slugify = C.ToolError, C.P, C.op, C.write_json, C.slugify
VOC = C.load_asset("asset-prompt-vocabulary.json")
TPL = C.load_asset("asset-plan-templates.json")

CLIP_LIMIT = 75  # usable tokens for SD1.5/SDXL-style CLIP encoders (77 minus BOS/EOS)
PRESETS = sorted(VOC["presets"].keys())
MOODS = sorted(VOC["mood_map"].keys())
VARIANT_DIMS = sorted(list(VOC["variant_dimensions"].keys()) + list(VOC["conversion_dimensions"].keys()))
EVAL_CATS = sorted(VOC["eval_templates"].keys())
GLOSSARY_TERMS = sorted(VOC["glossary"].keys())
NEG_FOCUS = sorted(VOC["artifact_negatives"].keys())
SLOTS = VOC["slot_order"]
STOP = set(VOC["stopwords"])


# ----------------------------------------------------------------- helpers

def approx_tokens(text):
    """Rough CLIP-style token estimate: about 5 characters per token plus punctuation."""
    words = re.findall(r"[A-Za-z0-9]+", text)
    punct = len(re.findall(r"[^\sA-Za-z0-9]", text))
    return sum(max(1, math.ceil(len(w) / 5)) for w in words) + punct


def _content_words(text):
    return [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in STOP]


def build_positive(slots, preset=None, mood=None):
    merged = {}
    if preset:
        merged.update(VOC["presets"][preset]["slots"])
    if mood:
        merged.update(VOC["mood_map"][mood])
    merged.update({k: v for k, v in slots.items() if v})
    parts = [merged[s] for s in SLOTS if merged.get(s)]
    return ", ".join(parts), merged


def _dedupe(seq):
    out = []
    for item in seq:
        if item not in out:
            out.append(item)
    return out


def _write_text(ctx, path_value, default_name, text):
    p = ctx.out_path(path_value, default_name)
    p.write_text(text, encoding="utf-8")
    ctx.record(p)
    return str(p)


# ----------------------------------------------------------------- plan.blueprint

def op_blueprint(p, ctx):
    cap = ctx.cap
    tpl = TPL.get(str(cap["category_number"]))
    if not tpl:
        raise ToolError("runtime_error", f"no blueprint template for category {cap['category_number']}")
    default = TPL["default"]
    focus = p.get("focus") or "general"
    lines = [
        f"# {cap['name']}",
        "",
        f"- Category: {cap['category_number']}. {cap['category_name']}",
        f"- Capability number: {cap['number']} (id `{cap['id']}`)",
        f"- Toolkit operation: `{cap['operation']}`",
        f"- Execution mode: {cap['execution_mode']} | Approval: {cap['approval']} | Risk: {cap['risk']}",
        f"- Audience: {p['audience']} | Focus: {focus}",
        "",
        "## 1. Trigger definition",
        f"Use this Skill Tool when a request asks to: {cap['description']}",
        "Out of scope: requests that need a different category, an engine that is not configured, "
        "or actions on identifiable people without consent review.",
    ]
    if p.get("brief"):
        lines += ["", f"Brief supplied by the user: {p['brief']}"]
    lines += ["", "## 2. Inputs"] + [f"- {x}" for x in tpl["inputs"]]
    lines += ["", "## 3. Outputs"] + [f"- {x}" for x in tpl["outputs"]]
    lines += ["", "## 4. Tool routing",
              f"- This capability runs through `{cap['operation']}` in the toolkit.",
              f"- Command: `python3 tools-ai-image-toolkit.py run {cap['id']} --param ...`"]
    lines += [f"- {x}" for x in tpl["routing"]]
    lines += ["", "## 5. Workflow"] + [f"{i}. {x}" for i, x in enumerate(tpl["workflow"], 1)]
    if focus != "general":
        lines.append(f"{len(tpl['workflow']) + 1}. Apply the focus '{focus}' to every step above.")
    lines += ["", "## 6. Evaluation"] + [f"- {x}" for x in default["evaluation"]]
    lines += ["", "## 7. Governance"] + [f"- {x}" for x in default["governance"]]
    lines += ["", "## 8. Recovery"] + [f"- {x}" for x in default["recovery"]]
    lines.append("")
    text = "\n".join(lines)
    path = _write_text(ctx, p.get("output"), "blueprint.md", text)
    return {"blueprint_file": path, "sections": 8, "category": cap["category_name"], "focus": focus,
            "note": "Structured blueprint from the category template. It is not expert analysis; review it."}


# ----------------------------------------------------------------- prompt.build

def op_build(p, ctx):
    slots = {k: p.get(k) for k in SLOTS}
    preset = p.get("preset")
    if preset:
        pre = VOC["presets"][preset]
    else:
        pre = {}
    text, merged = build_positive(slots, preset, p.get("mood"))
    subject = p["subject"].strip()
    if not text:
        text = subject
    else:
        text = f"{subject}, {text}" if subject.lower() not in text.lower() else text
    neg = list(pre.get("negatives", []))
    people = bool(pre.get("needs_people_check"))
    if people:
        neg += VOC["artifact_negatives"]["anatomy"]
    neg += p.get("exclusions") or []
    negative = ", ".join(_dedupe([n.strip() for n in neg if n.strip()]))
    tokens = approx_tokens(text)
    warnings = []
    if tokens > CLIP_LIMIT:
        warnings.append(f"prompt is about {tokens} tokens; CLIP-style encoders truncate near {CLIP_LIMIT}. Shorten or split it.")
    if people:
        warnings.append("people preset: confirm consent and purpose with the policy ruleset 'likeness' before generating.")
    result = {"prompt": text, "negative_prompt": negative, "preset": preset, "mood": p.get("mood"),
              "slots": merged, "approx_tokens": tokens, "token_limit": CLIP_LIMIT}
    path = _write_text(ctx, p.get("output"), "prompt.json", _pretty(result))
    result.update({"prompt_file": path})
    return result | {"warnings_list": warnings}


def _pretty(obj):
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------- prompt.negative

def op_negative(p, ctx):
    focus = p.get("focus") or ["anatomy", "text", "quality"]
    bad = [f for f in focus if f not in VOC["artifact_negatives"]]
    if bad:
        raise ToolError("invalid_input", f"unknown focus {bad}; use one of {NEG_FOCUS}", field="focus")
    terms = []
    for f in focus:
        terms += VOC["artifact_negatives"][f]
    terms += p.get("extra") or []
    keep = [k.lower() for k in (p.get("keep") or [])]
    kept, removed = [], []
    for t in terms:
        tl = t.lower()
        if any(k and (k in tl) for k in keep):
            removed.append(t)
        elif t not in kept:
            kept.append(t)
    pos_words = set(_content_words(p["prompt"]))
    warnings = []
    for t in kept:
        overlap = pos_words & set(_content_words(t))
        if overlap:
            warnings.append(f"positive prompt uses '{', '.join(sorted(overlap))}', which the negative term '{t}' also targets.")
    if removed:
        warnings.append(f"kept these terms out of the negative prompt to protect wanted detail: {removed}")
    neg = ", ".join(kept)
    return {"negative_prompt": neg, "terms": kept, "removed_to_protect": removed,
            "approx_tokens": approx_tokens(neg), "warnings_list": warnings}


# ----------------------------------------------------------------- prompt.debug

_ISSUE_KEYS = [
    ("ignored", ["ignored", "missing", "absent", "not shown", "left out"]),
    ("merged", ["merged", "blend", "fused", "combined"]),
    ("anatomy", ["anatomy", "hand", "finger", "limb", "face", "eye", "pose"]),
    ("composition", ["composition", "crop", "framing", "cramped", "off-centre", "centre"]),
    ("style", ["style", "drift", "inconsistent look", "medium changed"]),
    ("text", ["text", "letter", "word", "typography", "lettering"]),
]


def op_debug(p, ctx):
    prompt = p["prompt"]
    observed = [o.lower() for o in (p.get("observed") or [])]
    findings = []
    tokens = approx_tokens(prompt)
    if tokens > CLIP_LIMIT:
        findings.append({"type": "length", "detail": f"about {tokens} tokens, above {CLIP_LIMIT}"})
    words = _content_words(prompt)
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    repeated = sorted([w for w, c in counts.items() if c >= 3])
    if repeated:
        findings.append({"type": "repetition", "detail": f"repeated terms: {repeated}"})
    lower = " " + re.sub(r"[^a-z ]", " ", prompt.lower()) + " "
    for a, b in VOC["conflict_pairs"]:
        if f" {a} " in lower and f" {b} " in lower:
            findings.append({"type": "conflict", "detail": f"conflicting terms '{a}' and '{b}'"})
    plan = []
    for key, words_list in _ISSUE_KEYS:
        hit = [o for o in observed if any(w in o for w in words_list)]
        if not hit:
            continue
        rule = VOC["debug_rules"].get(key)
        if not rule:
            continue
        item = {"issue": key, "observed": hit, "variable": rule["variable"], "change": rule["change"],
                "test_prompt": None}
        if key == "text":
            item["test_prompt"] = re.sub(r"\b(text|words?|letters?|lettering|typography)\b", "", prompt,
                                         flags=re.I).replace("  ", " ").strip(" ,")
        if key == "ignored":
            m = re.search(r"ignored[:\s]+([a-z ]+)", " ".join(hit))
            target = m.group(1).strip() if m else None
            if target and target in prompt.lower():
                idx = prompt.lower().index(target)
                phrase = prompt[idx: idx + len(target)]
                item["test_prompt"] = phrase + ", " + re.sub(re.escape(phrase), "", prompt, count=1, flags=re.I).strip(" ,")
        plan.append(item)
    if not observed and not findings:
        findings.append({"type": "info", "detail": "no issues supplied and no static problems found; supply observed issues for a targeted plan"})
    return {"static_findings": findings, "revision_plan": plan,
            "rule": "change one variable per test and keep the seed fixed"}


# ----------------------------------------------------------------- prompt.variants

def op_variants(p, ctx):
    mode = p.get("mode") or "standard"
    dim = p["dimension"]
    pool = VOC["variant_dimensions"] if mode == "standard" else VOC["conversion_dimensions"]
    if dim not in pool:
        raise ToolError("invalid_input", f"dimension '{dim}' is not valid for mode '{mode}'; choose from {sorted(pool)}",
                        field="dimension")
    values = p.get("values") or pool[dim]
    limit = ctx.limits["max_variants"]
    if len(values) > limit:
        raise ToolError("invalid_input", f"too many variants ({len(values)}); limit is {limit}", field="values")
    base = p["base_prompt"].strip().rstrip(",")
    seed = p["seed"]
    items = []
    for i, v in enumerate(values, 1):
        items.append({"id": f"{slugify(dim)}-{i:02d}", "dimension": dim, "value": v,
                      "prompt": f"{base}, {v}", "seed": seed})
    path = _write_text(ctx, p.get("output"), "variants.json", _pretty({"base_prompt": base, "variants": items}))
    return {"variant_count": len(items), "fixed_seed": seed, "variants": items, "variants_file": path,
            "rule": "Only the named dimension changes. Keep seed, model and settings identical across variants."}


# ----------------------------------------------------------------- prompt.weight_check

_WEIGHT_RE = re.compile(r"\(([^()]*?)(?::\s*([0-9]*\.?[0-9]+))?\)|\[([^\[\]]+)\]")


def op_weight_check(p, ctx):
    text = p["prompt"]
    depth = max_depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == ")":
            depth -= 1
    warnings = []
    if depth != 0:
        warnings.append("unbalanced parentheses; check each '(' has a matching ')'")
    terms = []
    for m in _WEIGHT_RE.finditer(text):
        if m.group(3) is not None:
            terms.append({"text": m.group(3).strip(), "weight": 0.9, "form": "[]"})
        else:
            w = float(m.group(2)) if m.group(2) else 1.1
            terms.append({"text": m.group(1).strip(), "weight": w, "form": "()"})
    breaks = len(re.findall(r"\bBREAK\b", text))
    for t in terms:
        if t["weight"] > 1.5:
            warnings.append(f"'{t['text']}' weighted {t['weight']}; above 1.5 often causes artifacts")
        if t["weight"] < 0.5:
            warnings.append(f"'{t['text']}' weighted {t['weight']}; very low weights may be ignored")
    if len(terms) >= 3:
        warnings.append(f"{len(terms)} weighted terms; too many emphasis marks can destabilise the image")
    if max_depth > 2:
        warnings.append(f"nesting depth {max_depth}; keep nesting to 2 or fewer")
    pieces, last = [], 0
    for m in _WEIGHT_RE.finditer(text):
        pieces.append(text[last:m.start()])
        if m.group(3) is not None:
            body, w = m.group(3).strip(), 0.9
        else:
            body, w = m.group(1).strip(), (float(m.group(2)) if m.group(2) else 1.1)
        clamped = min(1.4, max(0.6, w))
        pieces.append(body if abs(clamped - 1.0) < 1e-9 else f"({body}:{clamped:.2f})")
        last = m.end()
    pieces.append(text[last:])
    norm = "".join(pieces)
    return {"terms": terms, "break_count": breaks, "max_nesting": max_depth,
            "normalized_prompt": norm, "warnings_list": warnings,
            "note": "Weights are clamped to 0.6-1.4 in the normalized prompt. Syntax support depends on the interface."}


# ----------------------------------------------------------------- prompt.brief_translate

def op_brief_translate(p, ctx):
    brief = p["brief"]
    words = re.findall(r"[a-z]+", brief.lower())
    slots, matched, mood_hits = {}, [], []
    for w in words:
        if w in VOC["mood_map"]:
            mood_hits.append(w)
            for k, v in VOC["mood_map"][w].items():
                slots.setdefault(k, v)
            matched.append(w)
        if w in VOC["keyword_map"]:
            for k, v in VOC["keyword_map"][w].items():
                slots.setdefault(k, v)
            matched.append(w)
    first = " ".join(brief.split()[:12]).strip(" .,")
    slots.setdefault("subject", first)
    text, merged = build_positive(slots)
    unmatched = [w for w in words if w not in matched and w not in STOP and len(w) > 2]
    instructions = [f"Set {k} to '{v}'" for k, v in merged.items()]
    return {"slots": merged, "suggested_prompt": text, "matched_keywords": sorted(set(matched)),
            "mood_terms": mood_hits, "unmatched_content_words": len(unmatched),
            "instructions": instructions,
            "warnings_list": ["Mapping is keyword-based. Review every slot before generating."]}


# ----------------------------------------------------------------- prompt.library

def _load_library(path):
    import json
    if not Path(path).exists():
        return {"version": 1, "entries": []}
    try:
        data = C.read_json(path)
    except json.JSONDecodeError as e:
        raise ToolError("invalid_input", f"library file is corrupt: {e.msg}", field="library",
                        hint="Restore from a backup; the file was not modified.")
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ToolError("invalid_input", "library file must contain an 'entries' list", field="library")
    return data


def op_library(p, ctx):
    lib_path = Path(p.get("library") or (ctx.output_dir / "prompt-library.json"))
    action = p["action"]
    lib = _load_library(lib_path) if lib_path.exists() else {"version": 1, "entries": []}
    entries = lib["entries"]
    if action == "list":
        return {"library": str(lib_path), "count": len(entries),
                "entries": [{"id": e["id"], "tags": e.get("tags", []), "model": e.get("model")} for e in entries]}
    if action == "get":
        hit = [e for e in entries if e["id"] == p.get("id")]
        if not hit:
            raise ToolError("not_found", f"no library entry with id '{p.get('id')}'", field="id")
        return {"entry": hit[0]}
    if action == "search":
        q = (p.get("query") or "").lower()
        if not q:
            raise ToolError("invalid_input", "search needs 'query'", field="query")
        hits = [e for e in entries if q in e["text"].lower() or any(q in t.lower() for t in e.get("tags", []))]
        return {"matches": len(hits), "entries": hits}
    if action == "add":
        text = (p.get("text") or "").strip()
        if not text:
            raise ToolError("invalid_input", "add needs 'text'", field="text")
        if len(text) > ctx.limits["max_text_chars"]:
            raise ToolError("invalid_input", "prompt text exceeds max_text_chars", field="text")
        eid = p.get("id") or "p-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        if any(e["id"] == eid for e in entries):
            raise ToolError("invalid_input", f"id '{eid}' already exists", field="id")
        entry = {"id": eid, "text": text, "tags": p.get("tags") or [], "model": p.get("model"),
                 "tested_settings": p.get("tested_settings") or {}, "known_failures": p.get("known_failures") or []}
        entries.append(entry)
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(lib_path, lib)
        ctx.record(lib_path)
        return {"added": eid, "count": len(entries), "library": str(lib_path)}
    if action == "remove":
        if not p.get("id"):
            raise ToolError("invalid_input", "remove needs 'id'", field="id")
        if not ctx.approve:
            raise ToolError("approval_required", f"removing library entry '{p['id']}' is destructive",
                            hint="Re-run with --approve after confirming the entry should be deleted.")
        before = len(entries)
        lib["entries"] = [e for e in entries if e["id"] != p["id"]]
        if len(lib["entries"]) == before:
            raise ToolError("not_found", f"no library entry with id '{p['id']}'", field="id")
        write_json(lib_path, lib)
        ctx.record(lib_path)
        return {"removed": p["id"], "count": len(lib["entries"]), "library": str(lib_path)}
    raise ToolError("invalid_input", f"unknown action {action}", field="action")


# ----------------------------------------------------------------- prompt.token_estimate

def op_token_estimate(p, ctx):
    if p.get("text"):
        text = p["text"]
    elif p.get("text_file"):
        text = C.read_text_file(p["text_file"], "text_file")
    else:
        raise ToolError("invalid_input", "provide 'text' or 'text_file'", field="text")
    if len(text) > ctx.limits["max_text_chars"]:
        raise ToolError("invalid_input", "text exceeds max_text_chars", field="text")
    segments = [s.strip() for s in re.split(r"\bBREAK\b", text) if s.strip()]
    per = [{"segment": i + 1, "approx_tokens": approx_tokens(s), "over_limit": approx_tokens(s) > CLIP_LIMIT}
           for i, s in enumerate(segments)]
    total = approx_tokens(text)
    return {"approx_total_tokens": total, "limit_per_segment": CLIP_LIMIT, "segments": per,
            "status": "over_limit" if any(s["over_limit"] for s in per) else "ok",
            "note": "Approximate. Use the tokenizer of your target model for exact counts."}


# ----------------------------------------------------------------- prompt.binding_check

def op_binding_check(p, ctx):
    text = p["prompt"].lower()
    clauses = [c for c in re.split(r",|;|\band\b", text) if c.strip()]
    attrs = set(VOC["binding_attributes"])
    results = []
    for ci, clause in enumerate(clauses):
        words = re.findall(r"[a-z]+", clause)
        nouns_here = [i for i, w in enumerate(words) if w not in attrs and w not in STOP and len(w) > 2]
        for i, w in enumerate(words):
            if w not in attrs:
                continue
            if i + 1 < len(words) and words[i + 1] not in attrs and words[i + 1] not in STOP:
                results.append({"attribute": w, "bound_to": words[i + 1], "clause": ci + 1, "status": "strong"})
                continue
            near = [j for j in nouns_here if abs(j - i) <= 3]
            if near:
                j = min(near, key=lambda x: abs(x - i))
                results.append({"attribute": w, "bound_to": words[j], "clause": ci + 1, "status": "ok"})
            else:
                results.append({"attribute": w, "bound_to": None, "clause": ci + 1, "status": "weak",
                                "advice": "move the attribute directly before its noun or separate it with a comma"})
    weak = [r for r in results if r["status"] == "weak"]
    return {"bindings": results, "weak_count": len(weak), "total_attributes": len(results),
            "warnings_list": [f"weak binding for '{r['attribute']}'" for r in weak]}


# ----------------------------------------------------------------- prompt.glossary

def op_glossary(p, ctx):
    terms = p.get("terms") or GLOSSARY_TERMS
    bad = [t for t in terms if t not in VOC["glossary"]]
    if bad:
        raise ToolError("invalid_input", f"unknown term(s) {bad}", field="terms",
                        hint=f"Valid terms: {', '.join(GLOSSARY_TERMS)}")
    aud = p["audience"]
    entries = [{"term": t, "definition": VOC["glossary"][t][aud]} for t in terms]
    md = [f"# Image synthesis glossary ({aud})", ""] + [f"**{e['term']}** - {e['definition']}" for e in entries]
    path = _write_text(ctx, p.get("output"), "glossary.md", "\n\n".join(md) + "\n")
    return {"audience": aud, "entries": entries, "glossary_file": path}


# ----------------------------------------------------------------- prompt.caption

_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def op_caption(p, ctx):
    folder = Path(p["images"])
    tags_map = {}
    if p.get("tags_file"):
        import json
        tags_map = C.read_json(p["tags_file"])
    dropout = p["dropout"]
    if dropout > VOC["caption_defaults"]["dropout_max"]:
        raise ToolError("invalid_input", f"dropout must be <= {VOC['caption_defaults']['dropout_max']}", field="dropout")
    images = sorted([f for f in folder.iterdir() if f.suffix.lower() in _IMG_EXT and f.is_file()])
    if not images:
        raise ToolError("invalid_input", "no supported images (png, jpg, jpeg, webp) in folder", field="images")
    if len(images) > ctx.limits["max_batch_items"]:
        raise ToolError("invalid_input", f"{len(images)} images exceeds max_batch_items", field="images")
    out_dir = ctx.cap_dir / "captions"
    out_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for img in images:
        raw = tags_map.get(img.name)
        if raw is None:
            side = img.with_suffix(".txt")
            raw = side.read_text(encoding="utf-8") if side.exists() else None
        if raw is None:
            skipped.append(img.name)
            continue
        tags = [t.strip() for t in (raw if isinstance(raw, list) else str(raw).split(",")) if t.strip()]
        h = int(hashlib.sha256(img.name.encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(p["seed"] + h)
        kept = [t for t in tags if rng.random() >= dropout]
        caption = ", ".join(([p["trigger"]] if p.get("trigger") else []) + kept)
        target = out_dir / (img.stem + ".txt")
        target.write_text(caption + "\n", encoding="utf-8")
        ctx.record(target)
        written.append({"image": img.name, "caption_file": str(target), "tags_used": len(kept), "tags_dropped": len(tags) - len(kept)})
    warnings = []
    if skipped:
        warnings.append(f"no tags for {len(skipped)} image(s); add them to tags_file or a same-name .txt file")
    return {"captioned": len(written), "skipped": skipped, "captions": written,
            "note": "Captions are written to the output folder only; source images and tag files are never modified."}


# ----------------------------------------------------------------- prompt.eval_suite

def op_eval_suite(p, ctx):
    cats = p.get("categories") or EVAL_CATS
    bad = [c for c in cats if c not in VOC["eval_templates"]]
    if bad:
        raise ToolError("invalid_input", f"unknown categories {bad}", field="categories", hint=f"Use: {EVAL_CATS}")
    per = p["per_category"]
    total = len(p["subjects"]) * len(cats) * per
    if total > ctx.limits["max_variants"] * 4:
        raise ToolError("invalid_input", f"suite of {total} prompts is above the configured limit", field="subjects")
    suite, idx = [], 0
    for subj in p["subjects"]:
        for cat in cats:
            for k in range(per):
                idx += 1
                suite.append({"id": f"eval-{idx:03d}", "category": cat, "subject": subj,
                              "prompt": VOC["eval_templates"][cat].format(subject=subj), "seed": p["seed"] + idx})
    path = _write_text(ctx, p.get("output"), "eval-suite.json", _pretty({"prompts": suite, "fixed_seed_base": p["seed"]}))
    return {"prompt_count": len(suite), "categories": cats, "suite_file": path,
            "note": "Use the same seeds, model and settings for every checkpoint you compare."}


# ----------------------------------------------------------------- prompt.adherence

_EXCL = ("no ", "without ")


def op_adherence(p, ctx):
    reqs = p["requirements"]
    obs = [o.lower() for o in p["observations"]]
    if not reqs:
        raise ToolError("invalid_input", "requirements list is empty", field="requirements")
    results = []
    for r in reqs:
        rl = r.lower().strip()
        excl = rl.startswith(_EXCL)
        core = re.sub(r"^(no |without )", "", rl)
        kws = _content_words(core)
        if not kws:
            results.append({"requirement": r, "status": "unverifiable", "evidence": None})
            continue
        need = max(1, math.ceil(0.6 * len(kws)))
        hit = None
        for i, o in enumerate(obs):
            if sum(1 for k in kws if k in o) >= need:
                hit = i
                break
        if excl:
            status = "pass" if hit is None else "fail"
        else:
            status = "pass" if hit is not None else "fail"
        results.append({"requirement": r, "status": status, "evidence": hit, "keywords": kws})
    scored = [x for x in results if x["status"] in ("pass", "fail")]
    rate = round(sum(1 for x in scored if x["status"] == "pass") / len(scored), 3) if scored else None
    return {"results": results, "pass_rate": rate,
            "note": "Keyword matching against text observations. Supply observations written by a reviewer or a vision model."}


# ----------------------------------------------------------------- prompt.alt_text

def op_alt_text(p, ctx):
    purpose = p["purpose"]
    if purpose == "decorative":
        return {"alt_text": "", "extended_description": None,
                "note": "Decorative images use empty alt text so screen readers skip them."}
    facts = [f.strip() for f in (p.get("facts") or []) if f and f.strip()]
    if not facts:
        raise ToolError("invalid_input", "provide 'facts': the visible content and its purpose", field="facts")
    text = ", ".join(facts)
    for pref in VOC["alt_text_strip_prefixes"]:
        if text.lower().startswith(pref):
            text = text[len(pref):]
    text = text[:1].upper() + text[1:]
    limit = p["max_length"]
    warnings = []
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        warnings.append(f"alt text truncated to {limit} characters; move detail into the extended description")
        text = cut.rstrip(",;:")
    if re.search(r"\b(image|photo|picture|graphic) of\b", text.lower()):
        warnings.append("remove 'image of' or 'photo of'; assistive tech already announces the image")
    extended = None
    if purpose in ("complex", "functional") or len(", ".join(facts)) > limit:
        extended = ". ".join(f.rstrip(".") for f in facts) + "."
        if p.get("context"):
            extended += " Context: " + p["context"].strip().rstrip(".") + "."
    return {"alt_text": text, "alt_length": len(text), "extended_description": extended,
            "purpose": purpose, "warnings_list": warnings,
            "note": "Written only from the facts you supply. Check that every fact is visible in the image."}


# ----------------------------------------------------------------- registry

OPS = {
    "plan.blueprint": op(op_blueprint, [
        P("brief", "str", help="Optional user brief to include in the blueprint"),
        P("audience", "choice", default="practitioner", choices=["beginner", "practitioner", "technical"]),
        P("focus", "str", help="Optional focus label appended to the workflow"),
        P("output", "outfile", help="Markdown output path (default: blueprint.md in the capability folder)"),
    ], "Write the 8-section skill blueprint for this capability"),
    "prompt.build": op(op_build, [
        P("subject", "str", True, help="Main subject of the image"),
        P("environment", "str"), P("composition", "str"), P("lens", "str"), P("lighting", "str"),
        P("palette", "str"), P("texture", "str"), P("style", "str"), P("quality", "str"),
        P("exclusions", "list", help="Comma list of things to avoid (added to negative prompt)"),
        P("preset", "choice", choices=PRESETS, help="Slot defaults and negatives for a category"),
        P("mood", "choice", choices=MOODS),
        P("output", "outfile"),
    ], "Build a structured prompt and negative prompt from slots and presets"),
    "prompt.negative": op(op_negative, [
        P("prompt", "str", True), P("focus", "list", help="Comma list from: " + ", ".join(NEG_FOCUS)),
        P("keep", "list", help="Terms that must not be suppressed"),
        P("extra", "list", help="Extra negative terms"),
    ], "Build concise negative constraints that protect wanted detail"),
    "prompt.debug": op(op_debug, [
        P("prompt", "str", True), P("observed", "list", help="Observed issues, e.g. 'ignored: dog', 'bad hands'"),
    ], "Diagnose prompt problems and plan single-variable revisions"),
    "prompt.variants": op(op_variants, [
        P("base_prompt", "str", True), P("dimension", "choice", True, choices=VARIANT_DIMS),
        P("mode", "choice", default="standard", choices=["standard", "conversion"]),
        P("values", "list", help="Override the default values for the dimension"),
        P("seed", "int", default=42, min=0, max=2**31 - 1), P("output", "outfile"),
    ], "Create a prompt family that changes only one dimension"),
    "prompt.weight_check": op(op_weight_check, [P("prompt", "str", True)],
                              "Parse emphasis syntax, flag risky weights and normalise them"),
    "prompt.brief_translate": op(op_brief_translate, [P("brief", "str", True)],
                                 "Map a creative brief to prompt slots (keyword-based)"),
    "prompt.library": op(op_library, [
        P("action", "choice", True, choices=["list", "add", "get", "remove", "search"]),
        P("library", "outfile", help="Library JSON path (default: output-dir/prompt-library.json)"),
        P("id", "str"), P("text", "str"), P("tags", "list"), P("model", "str"),
        P("tested_settings", "json"), P("known_failures", "list"), P("query", "str"),
    ], "Store, search and curate reusable prompts (remove needs --approve)"),
    "prompt.token_estimate": op(op_token_estimate, [
        P("text", "str"), P("text_file", "file"),
    ], "Estimate prompt tokens per BREAK segment against the CLIP-style limit"),
    "prompt.binding_check": op(op_binding_check, [P("prompt", "str", True)],
                               "Find attribute-noun pairs that may bind to the wrong object"),
    "prompt.glossary": op(op_glossary, [
        P("terms", "list", help="Comma list; default all terms"),
        P("audience", "choice", default="practitioner", choices=["beginner", "practitioner", "technical"]),
        P("output", "outfile"),
    ], "Write an audience-aware glossary of image synthesis terms"),
    "prompt.caption": op(op_caption, [
        P("images", "dir", True, help="Folder of training images"),
        P("trigger", "str", help="Trigger word placed first in every caption"),
        P("tags_file", "file", help="JSON object: image filename -> tag list or string"),
        P("dropout", "float", default=0.1, min=0, max=0.5),
        P("seed", "int", default=0, min=0, max=2**31 - 1),
    ], "Create consistent captions with trigger words and deterministic tag dropout"),
    "prompt.eval_suite": op(op_eval_suite, [
        P("subjects", "list", True, help="Comma list of subjects"),
        P("categories", "list", help="Comma list from: " + ", ".join(EVAL_CATS)),
        P("per_category", "int", default=1, min=1, max=10),
        P("seed", "int", default=7, min=0, max=2**31 - 1), P("output", "outfile"),
    ], "Generate a fixed evaluation prompt suite with fixed seeds"),
    "prompt.adherence": op(op_adherence, [
        P("requirements", "json", True, help="JSON list of requirement strings"),
        P("observations", "json", True, help="JSON list of observation strings written by a reviewer"),
    ], "Check each requested element against reviewer observations"),
    "prompt.alt_text": op(op_alt_text, [
        P("purpose", "choice", default="informative", choices=["informative", "decorative", "functional", "complex"]),
        P("facts", "json", help="JSON list of visible facts"),
        P("context", "str"),
        P("max_length", "int", default=125, min=40, max=300),
    ], "Draft alt text and extended description from supplied facts"),
}
