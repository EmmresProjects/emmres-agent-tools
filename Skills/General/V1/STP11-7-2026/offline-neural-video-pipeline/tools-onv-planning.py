"""Capability resolution, model routing, budget estimation and plan building.

Loaded by ``tools-onv.py`` (path-based import). All functions are pure with
respect to the filesystem except ``write_plan``.

Public surface
--------------
Catalog            normalised catalog index (28 categories / 1235 concepts)
Registry           model routing registry
resolve_capability(query)                 -> resolution dict
route_model(resolution, spec, registry)   -> routing dict
estimate_budget(...)                      -> budget dict
build_plan(spec, resolution, routing, budget, ctx) -> plan dict
"""

from __future__ import annotations

import math
import re
from pathlib import Path

NEURAL_OPS = ("generate", "transform", "animate", "interpolate", "restore", "edit")
LOCAL_OPS = ("audio", "caption", "color", "encode", "validate", "govern", "operate")
ALL_OPS = NEURAL_OPS + LOCAL_OPS


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------
class Catalog:
    def __init__(self, doc, common):
        self.common = common
        if not isinstance(doc, dict) or "categories" not in doc:
            common.fail("E_INPUT", "capability catalog is malformed (missing 'categories')")
        self.doc = doc
        self.version = doc.get("catalog_version")
        self.source = doc.get("source")
        self.operations = doc.get("operations", {})
        self.categories = doc["categories"]
        self.concepts = []
        self.by_slug = {}
        self.by_id = {}
        self.by_category = {}
        for category in self.categories:
            self.by_category[category["slug"]] = category
            for concept in category["concepts"]:
                enriched = dict(concept)
                enriched["category_number"] = category["number"]
                enriched["category_title"] = category["title"]
                enriched["category_slug"] = category["slug"]
                self.concepts.append(enriched)
                self.by_slug[concept["slug"]] = enriched
                self.by_id[concept["id"]] = enriched

    # -- lookups ---------------------------------------------------------
    def resolve(self, query):
        common = self.common
        text = str(query or "").strip()
        if not text:
            common.fail("E_SPEC", "capability is empty")
        lowered = text.lower()
        slug = slugify(text)

        for candidate in (text, slug):
            if candidate in self.by_id:
                return self._concept_resolution(self.by_id[candidate], text)
        for candidate in (text, slug):
            if candidate in self.by_slug:
                return self._concept_resolution(self.by_slug[candidate], text)
        for candidate in (text, slug):
            if candidate in self.by_category:
                return self._category_resolution(self.by_category[candidate], text)
        if lowered in ALL_OPS:
            return {
                "kind": "operation",
                "query": text,
                "op": lowered,
                "label": self.operations.get(lowered, {}).get("label", lowered),
                "name": "%s operation" % lowered,
                "description": "Operation-level routing resolved from the catalog operation taxonomy.",
                "category_title": None,
                "category_slug": None,
                "candidates": [],
            }
        matches = self.search(text, limit=6)
        if matches:
            common.fail(
                "E_SPEC",
                "capability '%s' is ambiguous or unknown; closest catalog matches are listed in details" % text,
                candidates=[{"slug": m["slug"], "name": m["name"], "category": m["category_title"]} for m in matches],
            )
        common.fail(
            "E_SPEC",
            "capability '%s' not found in the catalog; run 'tools-onv.py capabilities --search <text>'" % text,
        )

    def _concept_resolution(self, concept, query):
        op = concept.get("op", "operate")
        return {
            "kind": "concept",
            "query": query,
            "id": concept.get("id"),
            "slug": concept["slug"],
            "name": concept["name"],
            "description": concept.get("description", ""),
            "op": op,
            "label": self.operations.get(op, {}).get("label", op),
            "category_title": concept.get("category_title"),
            "category_slug": concept.get("category_slug"),
            "category_number": concept.get("category_number"),
            "candidates": [],
        }

    def _category_resolution(self, category, query):
        op = category.get("default_op", "operate")
        return {
            "kind": "category",
            "query": query,
            "slug": category["slug"],
            "name": category["title"],
            "description": "Category umbrella: %d catalog concepts routed through the '%s' operation family."
            % (category.get("concept_count", len(category["concepts"])), op),
            "op": op,
            "label": self.operations.get(op, {}).get("label", op),
            "category_title": category["title"],
            "category_slug": category["slug"],
            "category_number": category["number"],
            "candidates": [],
        }

    def search(self, text, limit=20):
        needle = str(text or "").lower().strip()
        if not needle:
            return []
        words = [w for w in re.split(r"[^a-z0-9]+", needle) if len(w) > 2]
        scored = []
        for concept in self.concepts:
            haystack = (concept["name"] + " " + concept["slug"]).lower()
            score = 0
            if needle in haystack:
                score += 6
            score += sum(2 for word in words if word in haystack)
            if concept.get("op") == needle:
                score += 1
            if score:
                scored.append((score, concept))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [item for _score, item in scored[:limit]]

    def summary(self):
        return {
            "catalog_version": self.version,
            "source": self.source,
            "category_count": len(self.categories),
            "concept_count": len(self.concepts),
            "operation_count": len(self.operations),
            "categories": [
                {
                    "number": c["number"],
                    "slug": c["slug"],
                    "title": c["title"],
                    "concept_count": c.get("concept_count", len(c["concepts"])),
                    "default_op": c.get("default_op"),
                }
                for c in self.categories
            ],
            "operations": [
                {"op": op, "label": meta.get("label", op), "stage": meta.get("stage")}
                for op, meta in sorted(self.operations.items())
            ],
        }


def slugify(text):
    text = str(text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
class Registry:
    def __init__(self, doc, common):
        self.common = common
        if not isinstance(doc, dict) or not isinstance(doc.get("models"), list):
            common.fail("E_INPUT", "model registry is malformed (missing 'models' list)")
        self.doc = doc
        self.version = doc.get("registry_version")
        self.models = doc["models"]
        self.by_id = {m["id"]: m for m in self.models if m.get("id")}
        self.proxy_filters = doc.get("proxy_filters", {})

    def get(self, model_id):
        if model_id not in self.by_id:
            self.common.fail(
                "E_SPEC",
                "model_id '%s' is not in the registry" % model_id,
                available=sorted(self.by_id),
            )
        return self.by_id[model_id]

    def candidates(self, op, hardware=None):
        out = []
        for model in self.models:
            if op in model.get("ops", []):
                out.append(model)
        if hardware and hardware.get("vram_gb") is not None:
            affordable = [m for m in out if (m.get("residency", {}).get("vram_min_gb") or 0) <= hardware["vram_gb"]]
            if affordable:
                return affordable
        return out

    def route(self, op, spec, hardware):
        """Pick a model entry for ``op``; returns a routing dict."""
        requested = (spec.get("models") or {}).get("model_id")
        if requested:
            model = self.get(requested)
            if op not in model.get("ops", []) and op not in LOCAL_OPS:
                self.common.fail(
                    "E_SPEC",
                    "model '%s' does not declare operation '%s'" % (requested, op),
                    model_ops=model.get("ops", []),
                )
            return self._routing(model, op, reason="explicit spec.models.model_id")

        if op in LOCAL_OPS:
            return {
                "model_id": None,
                "model": None,
                "op": op,
                "reason": "operation '%s' runs locally through ffmpeg/internal stages and needs no neural model" % op,
                "requires_neural_backend": False,
                "license": None,
                "proxy_equivalent": self.proxy_filters.get(op),
            }

        candidates = self.candidates(op, hardware)
        if not candidates:
            self.common.fail(
                "E_SPEC",
                "no registered model supports operation '%s'; add one to the registry asset or pass --registry" % op,
                operation=op,
            )
        defaults = [m for m in candidates if m.get("default_for_ops") and op in m["default_for_ops"]]
        pool = defaults or candidates

        def sort_key(model):
            residency = model.get("residency", {})
            return (
                0 if model.get("redistributable", False) else 1,
                residency.get("vram_min_gb") or 0,
                model.get("id"),
            )

        pool = sorted(pool, key=sort_key)
        chosen = pool[0]
        reason = "lowest declared VRAM among %d candidate models for op '%s'" % (len(pool), op)
        if hardware and hardware.get("vram_gb") is not None:
            fits = [m for m in pool if (m.get("residency", {}).get("vram_min_gb") or 0) <= hardware["vram_gb"]]
            if fits:
                chosen = fits[0]
                reason = "fits detected %.1f GB VRAM budget (of %d candidates)" % (hardware["vram_gb"], len(pool))
            else:
                reason = (
                    "no candidate fits detected %.1f GB VRAM; smallest candidate selected and a hardware warning raised"
                    % hardware["vram_gb"]
                )
        return self._routing(chosen, op, reason=reason)

    def _routing(self, model, op, reason):
        license_info = model.get("license") or {}
        return {
            "model_id": model["id"],
            "model": {
                "id": model["id"],
                "label": model.get("label", model["id"]),
                "family": model.get("family"),
                "ops": model.get("ops", []),
                "weights": model.get("weights", {}),
                "residency": model.get("residency", {}),
                "backend": model.get("backend", {}),
                "limits": model.get("limits", {}),
                "notes": model.get("notes"),
            },
            "op": op,
            "reason": reason,
            "requires_neural_backend": True,
            "license": license_info,
            "proxy_equivalent": self.proxy_filters.get(op),
        }


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------
def resolve_preset(presets_doc, name, op=None, common=None):
    presets = (presets_doc or {}).get("presets", {})
    if name not in presets:
        available = sorted(presets)
        if common is not None:
            common.fail("E_SPEC", "preset '%s' is not defined" % name, available=available)
        raise ValueError("preset '%s' is not defined in the presets asset" % name)
    preset = presets[name]
    merged = dict(preset)
    if op and op in (presets_doc.get("op_overrides") or {}):
        merged = _merge(merged, presets_doc["op_overrides"][op])
    return merged


def _merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# budget estimation
# ---------------------------------------------------------------------------
def estimate_budget(spec, routing, preset, registry, mode=None):
    params = spec.get("params") or {}
    width = int(params.get("width") or preset.get("width") or 640)
    height = int(params.get("height") or preset.get("height") or 360)
    fps = int(params.get("fps") or preset.get("fps") or 24)
    duration = float(params.get("duration_s") or preset.get("duration_s") or 3.0)
    frames = int(params.get("frames") or round(duration * fps))
    max_frames = int(spec["limits"]["max_frames"])
    warnings = []
    if frames > max_frames:
        warnings.append(
            "requested %d frames exceeds spec.limits.max_frames=%d; the plan clamps to %d" % (frames, max_frames, max_frames)
        )
        frames = max_frames
        duration = frames / float(fps)

    model = routing.get("model") or {}
    residency = model.get("residency") or {}
    ref = residency.get("vram_reference") or {"width": 1024, "height": 576, "frames": 49}
    ref_pixels = max(1, ref.get("width", 1024) * ref.get("height", 576))
    ref_frames = max(1, ref.get("frames", 49))
    pixels = max(1, width * height)
    vram_min = residency.get("vram_min_gb")

    proxy_mode = (mode or spec.get("mode")) == "proxy"
    if routing.get("requires_neural_backend") and vram_min is not None and not proxy_mode:
        scale = (float(pixels) * frames) / (ref_pixels * ref_frames)
        vram_est = float(vram_min) * max(1.0, scale) ** 0.85
        vram_peak = max(float(vram_min), float(residency.get("vram_recommended_gb") or vram_min))
        ram_est = float(residency.get("ram_min_gb") or 8) + max(0.0, vram_est - float(vram_min)) * 0.5
    else:
        vram_est = 0.0
        vram_peak = 0.0
        ram_est = float(preset.get("local_ram_gb") or 2)

    weights_gb = 0.0
    if routing.get("requires_neural_backend"):
        weights_gb = float((model.get("weights") or {}).get("approx_size_gb") or 0.0)
    declared_files = (spec.get("models") or {}).get("files") or []
    disk_models_gb = weights_gb if not declared_files else 0.0
    for entry in declared_files:
        if isinstance(entry, dict) and entry.get("size_gb"):
            disk_models_gb += float(entry["size_gb"])

    bitrate_kbps = float(preset.get("target_bitrate_kbps") or 4000)
    out_mb = (bitrate_kbps * duration) / 8.0 / 1024.0 * 1.15 + 8.0
    temp_mb = out_mb * 2.6 + (frames * width * height * 3 / 1024.0 / 1024.0) * 0.15
    max_output_mb = int(spec["limits"]["max_output_mb"])
    if out_mb > max_output_mb:
        warnings.append(
            "estimated output %.0f MB exceeds spec.limits.max_output_mb=%d" % (out_mb, max_output_mb)
        )

    if routing.get("requires_neural_backend") and not proxy_mode:
        throughput = float((model.get("limits") or {}).get("frames_per_second") or preset.get("assumed_fps_throughput") or 0.6)
        quality_factor = float(preset.get("quality_factor") or 1.0)
        neural_s = (frames / max(0.05, throughput)) * quality_factor
    else:
        neural_s = 0.0
    encode_s = max(1.5, frames / max(1.0, float(preset.get("encode_fps") or 120.0)))
    validate_s = 3.0 + frames / 240.0
    overhead_s = 4.0
    total_s = neural_s + encode_s + validate_s + overhead_s
    backend_timeout = int((spec.get("backend") or {}).get("timeout_s") or 900)

    return {
        "resolution": {"width": width, "height": height, "aspect": round(width / float(height), 4)},
        "fps": fps,
        "duration_s": round(duration, 3),
        "frames": frames,
        "steps": params.get("steps") or preset.get("steps"),
        "seed": params.get("seed"),
        "vram_gb_estimate": round(vram_est, 2),
        "vram_gb_recommended": round(vram_peak, 2),
        "ram_gb_estimate": round(ram_est, 2),
        "disk": {
            "models_gb": round(disk_models_gb, 2),
            "output_mb": round(out_mb, 1),
            "scratch_mb": round(temp_mb, 1),
            "total_free_required_gb": round((disk_models_gb * 0 + out_mb + temp_mb) / 1024.0 + 0.25, 2),
        },
        "time": {
            "neural_s": round(neural_s, 1),
            "encode_s": round(encode_s, 1),
            "validate_s": round(validate_s, 1),
            "overhead_s": overhead_s,
            "total_s": round(total_s, 1),
            "backend_timeout_s": backend_timeout,
            "job_timeout_s": int(spec["limits"]["timeout_s"]),
        },
        "within_job_timeout": total_s <= float(spec["limits"]["timeout_s"]),
        "requires_neural_backend": bool(routing.get("requires_neural_backend")) and not proxy_mode,
        "runtime_note": "proxy mode: no GPU residency or neural latency is required" if proxy_mode else None,
        "method": "deterministic heuristic: VRAM scales with (pixels*frames)/(reference)**, time from declared frames_per_second",
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# stage graph
# ---------------------------------------------------------------------------
def build_stages(spec, resolution, routing, budget, preset, registry, policy):
    """Return the ordered stage list for a job."""
    op = resolution["op"]
    stages = []

    def add(stage_id, title, executor, timeout, params=None, approval=False, needs=None, produces=None):
        stages.append(
            {
                "id": stage_id,
                "title": title,
                "op": (params or {}).get("op", op),
                "executor": executor,
                "timeout_s": int(timeout),
                "retries": int(spec["backend"]["retries"]) if executor == "backend" else 0,
                "approval_required": bool(approval),
                "depends_on": needs or [],
                "produces": produces or [],
                "params": params or {},
            }
        )

    add("preflight", "Environment, hardware, model-file and rights preflight", "internal", 300)
    add("prepare-inputs", "Probe and normalise declared inputs", "ffmpeg-prepare", 300, needs=["preflight"])

    proxy = registry.proxy_filters.get(op) or {}
    if op in NEURAL_OPS:
        mode = spec["mode"]
        if mode == "validate-only":
            executor = "internal"
        elif mode == "proxy":
            executor = "ffmpeg-proxy"
        elif mode == "backend":
            executor = "backend"
        else:  # auto
            executor = "backend" if (spec.get("backend") or {}).get("command") else "ffmpeg-proxy"
        add(
            "neural-%s" % op,
            "%s stage (%s)" % (op, routing.get("model_id") or "no model"),
            executor,
            spec["backend"]["timeout_s"] if executor == "backend" else 600,
            params={
                "op": op,
                "proxy_filter": proxy.get("filter"),
                "proxy_requires_input": proxy.get("requires_input", True),
                "proxy_label": proxy.get("label"),
                "model_id": routing.get("model_id"),
            },
            approval=(executor == "backend"),
            needs=["prepare-inputs"],
            produces=["neural-output"],
        )

    audio_cfg = spec.get("audio")
    if op == "audio" or audio_cfg:
        add(
            "audio",
            "Audio mix, loudness normalisation and dialogue path",
            "ffmpeg-audio",
            max(120, budget["frames"]),
            params={
                "op": "audio",
                "mode": ((audio_cfg or {}).get("mode") if isinstance(audio_cfg, dict) else None) or "normalize",
                "sources": (audio_cfg or {}).get("sources") if isinstance(audio_cfg, dict) else None,
            },
            needs=["neural-%s" % op] if op in NEURAL_OPS else ["prepare-inputs"],
            produces=["audio-output"],
        )

    if spec.get("compose") and isinstance(spec["compose"], dict) and spec["compose"].get("clips"):
        add(
            "compose",
            "Assemble clips into the programme timeline",
            "ffmpeg-compose",
            600,
            params={"op": "operate", "clips": spec["compose"]["clips"], "mode": spec["compose"].get("mode", "concat")},
            needs=["prepare-inputs"],
            produces=["composed"],
        )

    if op == "color" or preset.get("always_finish"):
        add(
            "color",
            "Finishing pass (contrast, saturation, level clamp)",
            "ffmpeg-colour",
            300,
            params={"op": "color", "filter": registry.proxy_filters.get("color", {}).get("filter")},
            needs=[s["id"] for s in stages if s["produces"] and s["id"] != "preflight"][-1:],
            produces=["graded"],
        )

    captions_cfg = spec.get("captions")
    if op == "caption" or captions_cfg:
        add(
            "caption",
            "Caption / subtitle generation and burn-in",
            "ffmpeg-caption",
            600,
            params={"op": "caption", "mode": "burn" if isinstance(captions_cfg, dict) else "sidecar",
                    "srt": (captions_cfg or {}).get("srt") if isinstance(captions_cfg, dict) else None},
            needs=["prepare-inputs"],
            produces=["captioned"],
        )

    add(
        "encode",
        "Encode to the delivery container and codec set",
        "ffmpeg-encode",
        600,
        params={
            "op": "encode",
            "container": spec["delivery"]["container"],
            "video_codec": spec["delivery"]["video_codec"],
            "audio_codec": spec["delivery"]["audio_codec"],
            "crf": preset.get("crf", 20),
            "x264_preset": preset.get("x264_preset", "medium"),
            "audio_bitrate_kbps": preset.get("audio_bitrate_kbps", 160),
            "loudness_lufs": preset.get("loudness_lufs"),
        },
        needs=[s["id"] for s in stages if s["produces"]][-1:],
        produces=["delivery"],
    )
    add(
        "validate",
        "Media validation suite against the validation policy",
        "internal",
        300,
        params={"op": "validate", "fail_on": (policy.get("fail_mode") or "error")},
        needs=["encode"],
        produces=["validation-report"],
    )
    add(
        "package",
        "Provenance, disclosure and delivery packaging",
        "internal",
        300,
        params={"op": "govern"},
        approval=bool(((policy.get("approvals") or {}).get("require_for") or []) and
                      "package" in (policy.get("approvals") or {}).get("require_for", [])),
        needs=["validate"],
        produces=["delivery-package"],
    )
    return stages


def build_plan(spec, resolution, routing, budget, stages, ctx, warnings=None):
    common = ctx.common
    approvals = sorted({s["id"] for s in stages if s["approval_required"]})
    governance_required = bool((spec.get("rights") or {}).get("require_consent"))
    plan = {
        "plan_version": "1.0",
        "tool": common.TOOL_NAME,
        "tool_version": common.TOOL_VERSION,
        "created_at": common.utcnow(),
        "job_id": spec["job_id"],
        "title": spec.get("title") or spec["job_id"],
        "spec_path": spec.get("spec_path"),
        "spec_sha256": spec.get("spec_sha256"),
        "capability": resolution,
        "operation": resolution["op"],
        "mode": spec["mode"],
        "preset": spec["preset"],
        "routing": routing,
        "budget": budget,
        "stages": stages,
        "approvals_required": approvals,
        "governance": {
            "consent_required": governance_required,
            "subject_ids": (spec.get("rights") or {}).get("subject_ids") or [],
            "ledger": (spec.get("rights") or {}).get("ledger"),
            "disclosure_required": bool((spec.get("rights") or {}).get("disclosure_required")),
            "allowed_use": (spec.get("rights") or {}).get("allowed_use"),
        },
        "warnings": list(warnings or []) + list(budget.get("warnings") or []),
        "limits": spec["limits"],
        "paths": {
            "base_dir": spec.get("base_dir"),
            "models_root": spec["models"]["root"],
            "output_dir": spec["output"]["dir"],
        },
    }
    return plan


def describe_concept_text(resolution, width=98):
    lines = []
    if resolution["kind"] == "concept":
        lines.append("%s  [%s]" % (resolution["name"], resolution.get("id")))
        lines.append("slug      : %s" % resolution["slug"])
        lines.append("category  : %s. %s" % (resolution.get("category_number"), resolution.get("category_title")))
    elif resolution["kind"] == "category":
        lines.append("%s  [category]" % resolution["name"])
        lines.append("slug      : %s" % resolution["slug"])
    else:
        lines.append("%s operation" % resolution["op"])
    lines.append("operation : %s (%s)" % (resolution["op"], resolution.get("label")))
    if resolution.get("description"):
        lines.append("summary   : %s" % wrap(resolution["description"], width))
    return lines


def wrap(text, width=98, indent="            "):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines)


def write_plan(plan, path, common):
    return common.json_write(path, plan)
