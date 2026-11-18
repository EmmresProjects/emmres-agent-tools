"""Project, standards, planning and QA engine for the offline-3d-production Skill Tool.

Pure standard library. Covers:

* project scaffolding, naming conventions and name validation
* unit/axis/project-configuration standards and conversion
* license, consent and provenance auditing
* offline installation manifests and checksum verification
* capacity planning (VRAM / RAM / scratch disk / estimate) and render-job budgeting
* validation gates for geometry, UV, animation, licensing and packaging
* deterministic contact sheets, reports (Markdown/HTML) and integrity manifests

Every heavy scan is bounded by ``max_items``/``max_pairs`` style limits so a
limited workstation cannot be wedged by one command.
"""

from __future__ import annotations

import fnmatch
import hashlib
import html
import json
import math
import os
import re
import sys

core = sys.modules["o3d_core"]
geom = sys.modules["o3d_geom"]
tex = sys.modules["o3d_tex"]
anim = sys.modules["o3d_anim"]

SCAFFOLD_FOLDERS = [
    "00-admin",
    "01-concepts",
    "02-references",
    "03-models",
    "04-sculpts",
    "05-retopo",
    "06-uvs",
    "07-textures",
    "08-materials",
    "09-rigs",
    "10-animation",
    "11-simulation",
    "12-lighting",
    "13-renders",
    "14-reviews",
    "15-delivery",
    "99-archive",
]

NAME_PATTERN = r"^(?P<asset>[a-z0-9]+)_(?P<kind>[a-z0-9]+)_(?P<desc>[a-z0-9-]+)_(?:v(?P<version>\d{3}))$"
DEFAULT_NAMING = {
    "pattern": NAME_PATTERN,
    "separator": "_",
    "version_padding": 3,
    "max_length": 64,
    "kinds": ["model", "rig", "tex", "mat", "anim", "sim", "shot", "cache", "groom", "comp", "print"],
    "reserved": ["default", "untitled", "cube", "sphere", "lamp", "camera", "object", "mesh", "material", "node"],
    "illegal_chars": [" ", "\\\\", "/", ":", "*", "?", '"', "<", ">", "|", "#", "%", "&"],
}

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def finding(severity, code, message, hint=None, **extra):
    item = {"severity": severity, "code": code, "message": message}
    if hint:
        item["hint"] = hint
    item.update(extra)
    return item


def worst(findings):
    level = "info"
    for f in findings:
        if SEVERITY_ORDER[f["severity"]] > SEVERITY_ORDER[level]:
            level = f["severity"]
    return level


def count_by_severity(findings):
    out = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    for f in findings:
        out[f["severity"]] = out.get(f["severity"], 0) + 1
    return out


def gate(findings, fail_on="error"):
    """A gate passes when no finding is at or above ``fail_on``."""
    threshold = SEVERITY_ORDER[fail_on]
    blocking = [f for f in findings if SEVERITY_ORDER[f["severity"]] >= threshold]
    return {
        "gate": "pass" if not blocking else "fail",
        "fail_on": fail_on,
        "blocking": len(blocking),
        "blocking_codes": sorted({f["code"] for f in blocking}),
        "counts": count_by_severity(findings),
        "worst": worst(findings),
    }


# ---------------------------------------------------------------------------
# Scaffold and standards
# ---------------------------------------------------------------------------


def scaffold(root, project_name, folders=None, fps=24, units="m", up_axis="Z", write_config=True):
    folders = folders or SCAFFOLD_FOLDERS
    created = []
    for folder in folders:
        path = os.path.join(root, folder)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            created.append(folder)
        else:
            created.append(folder + " (existing)")
    config = {
        "schema": "o3d/project-config/1",
        "project_name": project_name,
        "created_utc": core.now_iso(),
        "fps": fps,
        "units": units,
        "up_axis": up_axis,
        "frame_start": 1,
        "resolution": [1920, 1080],
        "pixel_aspect": 1.0,
        "color_management": "ACEScg (linear) -> sRGB display",
        "working_space": "ACEScg",
        "output_paths": {
            "renders": "13-renders",
            "reviews": "14-reviews",
            "delivery": "15-delivery",
        },
        "cache_policy": {"keep_versions": 3, "scratch_folder": "11-simulation"},
        "handles_frames": 8,
        "naming": DEFAULT_NAMING,
        "delivery_targets": ["review-mp4", "glb-realtime", "obj-archive"],
    }
    config_path = None
    if write_config:
        config_path = os.path.join(root, "00-admin", "project-config.json")
        core.dump_json(config_path, config)
    return {
        "project_name": project_name,
        "root": os.path.abspath(root),
        "folders": created,
        "folder_count": len(folders),
        "project_config": "00-admin/project-config.json" if write_config else None,
        "config": config,
    }


def naming_standard(name="<skill-tool-name>", kinds=None, reserved=None, max_length=64):
    return {
        "schema": "o3d/naming-standard/1",
        "generated_utc": core.now_iso(),
        "pattern": NAME_PATTERN,
        "example": "hero_model_body_v001",
        "separator": "_",
        "version_padding": 3,
        "max_length": max_length,
        "kinds": kinds or DEFAULT_NAMING["kinds"],
        "reserved": reserved or DEFAULT_NAMING["reserved"],
        "illegal_chars": DEFAULT_NAMING["illegal_chars"],
        "rules": [
            "asset: lowercase a-z0-9 only, the show-approved short code",
            "kind: one of the declared kinds, lowercase",
            "desc: lowercase words joined by single hyphens, no version numbers",
            "version: v followed by exactly three digits, monotonically increasing",
            "no spaces, no double separators, no trailing separators",
            "bones and controls use kind 'ctrl'/'bone' with a side prefix (l_/r_/c_)",
            "UDIM tiles append .1001-.1999 to the texture file name, not the object name",
        ],
    }


def validate_names(names, standard, asset_code=None):
    findings = []
    pattern = re.compile(standard.get("pattern", NAME_PATTERN))
    reserved = {r.lower() for r in standard.get("reserved", [])}
    kinds = {k.lower() for k in standard.get("kinds", [])}
    max_len = int(standard.get("max_length", 64))
    seen = {}
    rename_map = {}
    for raw in names:
        name = str(raw).strip()
        if not name:
            findings.append(finding("error", "empty_name", "An empty name was supplied.", "Remove or name the datablock."))
            continue
        if len(name) > max_len:
            findings.append(finding("error", "name_too_long",
                                    "'{0}' is {1} chars (limit {2}).".format(name, len(name), max_len),
                                    "Shorten the description segment."))
        key = name.lower()
        seen.setdefault(key, []).append(name)
        if any(ch in name for ch in (" ", "\\\\", "/", ":", "*", "?", '"', "<", ">", "|", "#", "%", "&")):
            findings.append(finding("error", "illegal_character",
                                    "'{0}' contains a non-portable character.".format(name),
                                    "Use lowercase letters, digits, single hyphens and underscores."))
        if key in reserved:
            findings.append(finding("error", "reserved_default_name",
                                    "'{0}' is a default/reserved datablock name.".format(name),
                                    "Rename before any handoff or bake."))
        if "--" in name or "__" in name:
            findings.append(finding("warning", "double_separator",
                                    "'{0}' contains a doubled separator.".format(name)))
        if name.endswith("_") or name.endswith("-"):
            findings.append(finding("warning", "trailing_separator",
                                    "'{0}' ends with a separator.".format(name)))
        m = pattern.match(name)
        if not m:
            findings.append(finding("error", "pattern_mismatch",
                                    "'{0}' does not match the naming standard.".format(name),
                                    "Expected: {0} (e.g. hero_model_body_v001).".format(NAME_PATTERN)))
        else:
            groups = m.groupdict()
            if kinds and groups.get("kind") and groups["kind"] not in kinds:
                findings.append(finding("warning", "unknown_kind",
                                        "Kind '{0}' is not in the approved list.".format(groups["kind"]),
                                        "Approved kinds: " + ", ".join(sorted(kinds))))
            if asset_code and groups.get("asset") != asset_code:
                findings.append(finding("warning", "asset_code_mismatch",
                                        "Asset code '{0}' differs from project code '{1}'.".format(
                                            groups["asset"], asset_code)))
            version = groups.get("version")
            if version and (version == "000" or version.endswith("00") and version != "000"):
                findings.append(finding("info", "version_padding",
                                        "Version '{0}' uses padded digits as intended.".format(version)))
            safe = standard.get("separator", "_").join(
                [groups.get("asset", "asset"), groups.get("kind", "model"),
                 (groups.get("desc") or "unnamed"), "v001"]
            )
            if safe != name:
                rename_map[name] = safe
    for key, items in sorted(seen.items()):
        if len(items) > 1:
            findings.append(finding("error", "duplicate_name",
                                    "'{0}' is used {1} times (case-insensitive).".format(key, len(items)),
                                    "Suffix with a region or variant token."))
    return {
        "checked": len(names),
        "findings": findings,
        "rename_map": rename_map,
        **gate(findings, standard.get("fail_on", "error")),
    }


UNIT_TABLE = {
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mil": 0.0000254,
}
AXES = ("X", "Y", "Z")


def unit_standard(scene_unit="m", character_height=1.8, prop_scale=1.0, environment_scale=1.0,
                  gravity=9.81, cloth_scale=1.0, for_print=False):
    if scene_unit not in UNIT_TABLE:
        raise core.invalid("Unknown unit '{0}'.".format(scene_unit), "Use one of: " + ", ".join(sorted(UNIT_TABLE)))
    if character_height <= 0:
        raise core.invalid("character_height must be positive.")
    return {
        "schema": "o3d/unit-standard/1",
        "generated_utc": core.now_iso(),
        "scene_unit": scene_unit,
        "scene_unit_in_metres": UNIT_TABLE[scene_unit],
        "character_height_scene_units": round(character_height / UNIT_TABLE[scene_unit], 6),
        "prop_scale": prop_scale,
        "environment_scale": environment_scale,
        "gravity_m_per_s2": gravity,
        "gravity_scene_units_per_s2": round(gravity / UNIT_TABLE[scene_unit], 6),
        "cloth_scale": cloth_scale,
        "camera_clip_recommendation": {
            "near": round(0.01 / UNIT_TABLE[scene_unit], 6),
            "far": round(1000.0 / UNIT_TABLE[scene_unit], 3),
        },
        "print_note": "1 scene unit = 1 mm for print exports" if for_print else None,
        "rules": [
            "All assets import at 1.0 scale; anything else is a defect.",
            "Characters face +Y in Blender (Z-up) and +Z in glTF (Y-up) after conversion.",
            "Sim caches are scaled by cloth_scale; changing it invalidates caches.",
        ],
    }


def unit_convert(value, from_unit, to_unit):
    factor = geom.unit_factor(from_unit, to_unit)
    return {"value": value, "from": from_unit, "to": to_unit, "factor": round(factor, 9),
            "result": round(value * factor, 9)}


def axis_standard(up_axis="Z", forward_axis="Y", handedness="right", rotation_order="XYZ",
                  origin_policy="character feet at world origin"):
    if up_axis not in AXES or forward_axis.strip("-") not in AXES:
        raise core.invalid("Axes must be X, Y or Z (forward may be negative).")
    if up_axis == forward_axis.strip("-"):
        raise core.invalid("Up and forward axes must differ.", "Pick two different axes.")
    if handedness not in ("right", "left"):
        raise core.invalid("handedness must be 'right' or 'left'.")
    if rotation_order not in ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"):
        raise core.invalid("rotation_order must be a 3-letter permutation of XYZ.")
    return {
        "schema": "o3d/axis-standard/1",
        "generated_utc": core.now_iso(),
        "up_axis": up_axis,
        "forward_axis": forward_axis,
        "handedness": handedness,
        "rotation_order": rotation_order,
        "origin_policy": origin_policy,
        "transform_freeze": "apply scale/rotation on export, keep pivot at world origin",
        "conversions": {
            "to_z_up": {"Y_UP": "rotate -90 X", "Z_UP": "identity"},
            "to_y_up": {"Y_UP": "identity", "Z_UP": "rotate +90 X"},
        },
        "warnings": [
            "Never mix up-axis conventions between the rig and the animation clip.",
            "Foreign rigs with -Y forward need a 180-degree yaw before retargeting.",
        ],
    }


def project_config(project_name, fps=24, resolution=(1920, 1080), handles=8, color_management="ACEScg",
                   naming=None, delivery_targets=None):
    if fps not in (12, 15, 16, 18, 20, 23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60):
        raise core.invalid(
            "Frame rate {0} is not a recognised production rate.".format(fps),
            "Use 23.976/24/25/29.97/30/48/50/59.94/60 or a documented custom rate.",
        )
    if len(resolution) != 2 or int(resolution[0]) <= 0 or int(resolution[1]) <= 0:
        raise core.invalid("resolution must be [width, height].")
    return {
        "schema": "o3d/project-config/1",
        "generated_utc": core.now_iso(),
        "project_name": project_name,
        "fps": fps,
        "resolution": [int(resolution[0]), int(resolution[1])],
        "aspect": round(int(resolution[0]) / float(resolution[1]), 6),
        "handles_frames": int(handles),
        "color_management": color_management,
        "naming": naming or DEFAULT_NAMING,
        "delivery_targets": delivery_targets or ["review-mp4", "glb-realtime"],
        "must_match": ["units", "up_axis", "fps", "color_management", "naming"],
    }


# ---------------------------------------------------------------------------
# Licenses / consent / provenance
# ---------------------------------------------------------------------------


def license_audit(assets, policy):
    """Check asset license metadata against a policy document.

    ``assets``: list of dicts with at least ``id``, ``license``, ``source``,
    ``redistribute`` (bool), ``model_training`` (bool), ``attribution`` (bool).
    ``policy``: dict from asset-license-policy.json.
    """
    allowed = {k.lower(): v for k, v in (policy.get("licenses") or {}).items()}
    findings = []
    for a in assets:
        aid = a.get("id") or "(unnamed)"
        lic = str(a.get("license") or "unknown").lower()
        if lic not in allowed:
            findings.append(finding("critical", "unknown_license",
                                    "'{0}' uses unrecognised license '{1}'.".format(aid, lic),
                                    "Add the license to the policy or replace the asset."))
            continue
        rule = allowed[lic]
        if rule.get("commercial_use") is False and policy.get("requires_commercial_use", True):
            findings.append(finding("critical", "no_commercial_use",
                                    "'{0}' license '{1}' forbids commercial use.".format(aid, lic)))
        if a.get("redistribute") and rule.get("redistribution") is False:
            findings.append(finding("error", "redistribution_forbidden",
                                    "'{0}' may not be redistributed under '{1}'.".format(aid, lic)))
        if a.get("model_training") and rule.get("ai_training") is False:
            findings.append(finding("critical", "ai_training_forbidden",
                                    "'{0}' forbids AI training under '{1}'.".format(aid, lic),
                                    "Exclude the asset from training datasets or switch the source."))
        if rule.get("attribution") and not a.get("attribution"):
            findings.append(finding("error", "missing_attribution",
                                    "'{0}' requires attribution but none is recorded.".format(aid)))
        if not a.get("source"):
            findings.append(finding("warning", "missing_source", "'{0}' has no recorded source URL/path.".format(aid)))
        if not a.get("provenance_sha256"):
            findings.append(finding("info", "missing_checksum",
                                    "'{0}' has no content hash; provenance cannot be proven.".format(aid),
                                    "Run the 'hash' command and record the digest."))
        if a.get("ai_generated") and not a.get("model_receipt"):
            findings.append(finding("warning", "missing_model_receipt",
                                    "'{0}' is AI-generated but no model receipt is recorded.".format(aid),
                                    "Run 'provenance-record' to capture model, version, seed and inputs."))
    return {
        "assets_checked": len(assets),
        "findings": findings,
        **gate(findings, policy.get("fail_on", "error")),
    }


def consent_audit(records, policy):
    findings = []
    required = policy.get("required_fields", ["performer_id", "consent_date", "usage_scope"])
    for rec in records:
        rid = rec.get("performer_id") or "(missing id)"
        for fld in required:
            if not rec.get(fld):
                findings.append(finding("critical", "missing_consent_field",
                                        "'{0}' is missing {1}.".format(rid, fld),
                                        "Obtain the signed consent record before release."))
        if rec.get("model_training") and not rec.get("training_permitted"):
            findings.append(finding("critical", "training_not_permitted",
                                    "'{0}' performance may not be used for model training.".format(rid)))
        if rec.get("territory") == "none":
            findings.append(finding("critical", "no_territory", "'{0}' has no granted territory.".format(rid)))
        exp = rec.get("expires")
        if exp and str(exp) < policy.get("today", "9999-12-31"):
            findings.append(finding("error", "consent_expired",
                                    "'{0}' consent expired on {1}.".format(rid, exp),
                                    "Renew consent before any further use."))
        if not rec.get("retention_policy"):
            findings.append(finding("warning", "missing_retention",
                                    "'{0}' has no retention policy.".format(rid)))
    return {
        "records_checked": len(records),
        "findings": findings,
        **gate(findings, policy.get("fail_on", "error")),
    }


def provenance_record(path, model=None, version=None, seed=None, inputs=None, license_id=None,
                      operator=None, prompt_sha256=None, extra=None):
    record = {
        "schema": "o3d/provenance/1",
        "generated_utc": core.now_iso(),
        "output": os.path.basename(path),
        "output_sha256": core.sha256_file(path) if os.path.exists(path) else None,
        "output_bytes": os.path.getsize(path) if os.path.exists(path) else None,
        "model": model,
        "model_version": version,
        "seed": seed,
        "inputs": inputs or [],
        "license_id": license_id,
        "operator": operator,
        "prompt_sha256": prompt_sha256,
        "network_used": False,
        "environment": core.environment_info(),
    }
    if extra:
        record.update(extra)
    missing = [k for k in ("model", "model_version", "seed", "license_id") if record.get(k) in (None, "")]
    record["completeness"] = {
        "missing": missing,
        "complete": not missing,
        "note": "Offline generation is only reproducible when model, version, seed and inputs are all recorded.",
    }
    return record


# ---------------------------------------------------------------------------
# Offline install manifests and integrity
# ---------------------------------------------------------------------------


def build_install_manifest(entries, bundle_dir=None):
    findings = []
    files = []
    for e in entries:
        name = e.get("file")
        if not name:
            findings.append(finding("error", "manifest_missing_file", "An entry has no 'file'."))
            continue
        entry = {
            "file": name,
            "version": e.get("version"),
            "platform": e.get("platform"),
            "sha256": e.get("sha256"),
            "bytes": e.get("bytes"),
            "install": e.get("install"),
            "license": e.get("license"),
        }
        if bundle_dir:
            full = os.path.join(bundle_dir, name)
            if os.path.exists(full):
                entry["bytes"] = os.path.getsize(full)
                actual = core.sha256_file(full)
                if entry["sha256"] and entry["sha256"] != actual:
                    findings.append(finding("critical", "checksum_mismatch",
                                            "'{0}' does not match the recorded digest.".format(name),
                                            "Re-copy the installer; do not run it until verified."))
                entry["sha256"] = actual
                entry["verified"] = True
            else:
                entry["verified"] = False
                findings.append(finding("error", "installer_missing",
                                        "'{0}' is listed but not present in the bundle.".format(name),
                                        "Transfer the file to the air-gapped machine before install day."))
        if not entry["sha256"]:
            findings.append(finding("warning", "unverified_installer",
                                    "'{0}' has no checksum; integrity cannot be proven.".format(name)))
        if not entry["license"]:
            findings.append(finding("warning", "installer_no_license",
                                    "'{0}' has no recorded license.".format(name)))
        files.append(entry)
    return {
        "schema": "o3d/offline-install-manifest/1",
        "generated_utc": core.now_iso(),
        "bundle_dir": os.path.basename(bundle_dir) if bundle_dir else None,
        "files": files,
        "file_count": len(files),
        "findings": findings,
        **gate(findings, "error"),
    }


def hash_paths(paths, base_dir=None):
    results = []
    for p in paths:
        if not os.path.exists(p):
            results.append({"path": p, "exists": False})
            continue
        results.append({
            "path": os.path.relpath(p, base_dir) if base_dir else os.path.basename(p),
            "exists": True,
            "bytes": os.path.getsize(p),
            "sha256": core.sha256_file(p),
        })
    return {"generated_utc": core.now_iso(), "files": results, "count": len(results)}


def verify_manifest(manifest, base_dir):
    findings = []
    checked = 0
    for entry in manifest.get("files", []):
        name = entry.get("path") or entry.get("file")
        if not name:
            continue
        full = os.path.join(base_dir, name)
        checked += 1
        if not os.path.exists(full):
            findings.append(finding("critical", "missing_file", "{0} is missing.".format(name),
                                    "Restore from the archive or regenerate the deliverable."))
            continue
        actual = core.sha256_file(full)
        expected = entry.get("sha256")
        if expected and actual != expected:
            findings.append(finding("critical", "checksum_mismatch",
                                    "{0} changed since the manifest was written.".format(name),
                                    "Re-export the file, or update the manifest if the change is intended."))
        elif not expected:
            findings.append(finding("warning", "no_checksum", "{0} has no recorded digest.".format(name)))
    return {"checked": checked, "findings": findings, **gate(findings, manifest.get("fail_on", "error"))}


# ---------------------------------------------------------------------------
# Capacity planning
# ---------------------------------------------------------------------------


def vram_plan(triangles, textures=None, samples=256, resolution=(1920, 1080), subgroups=8,
              concurrent_apps_gb=0.0, overhead=1.35, device_gb=8.0, raytracing=True):
    """Estimate peak VRAM for a render/viewport session.

    The coefficients are deliberately conservative planning heuristics, in the
    spirit of production capacity sheets; they are not vendor measurements.
    """
    if triangles < 0:
        raise core.invalid("triangles must be >= 0.")
    mip_factor = 4.0 / 3.0
    tex = textures or []
    tex_bytes = 0
    tex_rows = []
    for t in tex:
        w, h = t.get("width", 2048), t.get("height", 2048)
        ch = t.get("channels", 3)
        depth = t.get("bytes_per_channel", 1)
        raw = w * h * ch * depth * mip_factor
        tex_bytes += raw
        tex_rows.append({"name": t.get("name", "texture"), "resolution": [w, h],
                         "mib": round(raw / geom_MB, 2) if (geom_MB := 1024 * 1024) else 0})
    # BVH / acceleration structure ~ 40 bytes/tri (BVH2) with raytracing
    accel = triangles * (40 if raytracing else 8)
    rw, rh = resolution
    buffers = rw * rh * 16 * samples * subgroups
    denoise = rw * rh * 4 * 6
    geometry = triangles * 96
    subtotal = geometry + accel + tex_bytes + buffers + denoise
    peak = subtotal * overhead + concurrent_apps_gb * 1024 * 1024 * 1024
    device_bytes = device_gb * 1024 * 1024 * 1024
    return {
        "assumptions": {
            "samples_per_pixel": samples, "resolution": [rw, rh], "subgroups": subgroups,
            "overhead_factor": overhead, "raytracing": raytracing,
            "note": "Planning heuristic, not a vendor measurement; validate with the GPU audit.",
        },
        "components_gib": {
            "geometry": round(geometry / 1024 ** 3, 3),
            "acceleration": round(accel / 1024 ** 3, 3),
            "textures": round(tex_bytes / 1024 ** 3, 3),
            "render_buffers": round(buffers / 1024 ** 3, 3),
            "denoise": round(denoise / 1024 ** 3, 3),
            "concurrent_apps": round(concurrent_apps_gb, 3),
        },
        "texture_breakdown": tex_rows,
        "peak_gib": round(peak / 1024 ** 3, 3),
        "device_gib": device_gb,
        "headroom_gib": round((device_bytes - peak) / 1024 ** 3, 3),
        "fits": peak <= device_bytes,
        "recommendation": (
            "Fits with headroom." if peak <= device_bytes * 0.8
            else "Tight: reduce samples, texture size or concurrent apps." if peak <= device_bytes
            else "Does not fit: lower resolution, samples or texture budget, or split the scene."
        ),
    }


def ram_plan(scene_triangles, sculpt_millions=0.0, caches_gb=(0, 0, 0), cache_versions=3,
             bake_resolution=(4096, 4096, 16), comp_layers=12, system_gb=32.0):
    """Estimate resident RAM for sculpting, simulation caches and baking."""
    sculpt_polys = sculpt_millions * 1_000_000
    sculpt_bytes = sculpt_polys * 120  # positions+normals+temporaries per polygon
    scene_bytes = scene_triangles * 220
    sim_bytes = sum(caches_gb) * 1024 ** 3
    bw, bh, bch = bake_resolution
    bake_bytes = bw * bh * bch * 12
    comp_bytes = comp_layers * 1920 * 1080 * 16 * 2
    total = (sculpt_bytes + scene_bytes + sim_bytes + bake_bytes + comp_bytes) * cache_versions * 0.6
    system = system_gb * 1024 ** 3
    return {
        "components_gib": {
            "sculpt": round(sculpt_bytes / 1024 ** 3, 2),
            "scene": round(scene_bytes / 1024 ** 3, 2),
            "simulation_caches": round(sim_bytes / 1024 ** 3, 2),
            "baking": round(bake_bytes / 1024 ** 3, 2),
            "compositing": round(comp_bytes / 1024 ** 3, 2),
        },
        "peak_gib": round(total / 1024 ** 3, 2),
        "system_gib": system_gb,
        "headroom_gib": round((system - total) / 1024 ** 3, 2),
        "fits": total <= system,
        "recommendation": (
            "Sufficient." if total <= system * 0.75
            else "Prefer more RAM or render in layers." if total <= system
            else "Exceeds memory: split the scene, bake in tiles, or render on a farm."
        ),
        "note": "Coefficients are conservative planning values; measure with the workload profiler before lock.",
    }


def disk_plan(exr_frames=240, exr_mb=24.0, cache_scene_mb=800.0, cache_versions=3,
              bakes=8, bake_mb=48.0, proxies_mb=1200.0, autosave_mb=200.0, safety=0.35,
              available_gb=500.0):
    render = exr_frames * exr_mb
    caches = cache_scene_mb * cache_versions
    bake_total = bakes * bake_mb
    raw = render + caches + bake_total + proxies_mb + autosave_mb * 10
    with_margin = raw * (1 + safety)
    return {
        "components_mb": {
            "render_frames": round(render, 1),
            "simulation_caches": round(caches, 1),
            "bakes": round(bake_total, 1),
            "proxies": round(proxies_mb, 1),
            "autosaves": round(autosave_mb * 10, 1),
        },
        "required_mb": round(with_margin, 1),
        "required_gb": round(with_margin / 1024.0, 2),
        "available_gb": available_gb,
        "fits": with_margin / 1024.0 <= available_gb,
        "cleanup_rules": [
            "Delete reviewed proxy caches older than the last two retained versions.",
            "Keep autosaves for 7 days, then archive to 99-archive.",
            "Purge /tmp scratch after every successful delivery package.",
        ],
        "recommendation": ("Sufficient scratch space." if with_margin / 1024.0 <= available_gb * 0.7
                           else "Tight; clean caches before the next render."
                           if with_margin / 1024.0 <= available_gb
                           else "Insufficient: move caches to another volume or lower cache_versions."),
    }


def render_job_plan(frames, seconds_per_frame, machines=1, threads_per_machine=8, retries=2,
                    upload_minutes=0.0, target_hours=None):
    if frames <= 0:
        raise core.invalid("frames must be positive.")
    if seconds_per_frame <= 0:
        raise core.invalid("seconds_per_frame must be positive.")
    per_machine_frames = math.ceil(frames / float(max(1, machines)))
    render_seconds = per_machine_frames * seconds_per_frame * (1 + 0.08 * retries)
    wall = render_seconds / 60.0 + upload_minutes
    return {
        "frames": frames,
        "machines": machines,
        "frames_per_machine": per_machine_frames,
        "seconds_per_frame": seconds_per_frame,
        "retries_factor": round(1 + 0.08 * retries, 3),
        "estimated_wall_minutes": round(wall, 1),
        "estimated_wall_hours": round(wall / 60.0, 2),
        "threads_per_machine": threads_per_machine,
        "target_hours": target_hours,
        "meets_target": (wall / 60.0 <= target_hours) if target_hours else None,
        "recommendation": (
            "Schedule as a single batch." if wall <= 120 else
            "Split into chunks with per-chunk checkpoints; resume on failure."
        ),
        "chunk_size_recommendation": max(1, min(frames, int(math.ceil(frames / max(1, machines * 4))))),
    }


# ---------------------------------------------------------------------------
# Hardware audit
# ---------------------------------------------------------------------------


def hardware_audit(requirements, reported):
    findings = []
    checks = []
    for key, need in sorted(requirements.items()):
        have = reported.get(key)
        entry = {"resource": key, "required": need, "reported": have}
        if have is None:
            entry["status"] = "unknown"
            findings.append(finding("warning", "hardware_unknown",
                                    "{0} was not reported.".format(key),
                                    "Record the value in the hardware profile JSON."))
        elif isinstance(need, (int, float)) and isinstance(have, (int, float)):
            entry["status"] = "pass" if have >= need else "fail"
            if have < need:
                findings.append(finding("error", "hardware_below_requirement",
                                        "{0}: have {1}, need {2}.".format(key, have, need),
                                        "Upgrade the workstation or reduce the project requirement."))
        else:
            entry["status"] = "pass" if str(have).lower() == str(need).lower() else "fail"
            if entry["status"] == "fail":
                findings.append(finding("warning", "hardware_mismatch",
                                        "{0}: have {1}, project requires {2}.".format(key, have, need)))
        checks.append(entry)
    return {"checks": checks, "findings": findings, **gate(findings, "error")}


# ---------------------------------------------------------------------------
# Gate: geometry / uv / animation / package
# ---------------------------------------------------------------------------


def geometry_gate(mesh, profile):
    findings = []
    bounds = mesh.bounds()
    dims = mesh.dimensions()
    tris = mesh.tri_count
    max_tris = profile.get("max_triangles", 500000)
    if tris > max_tris:
        findings.append(finding("error", "triangle_budget_exceeded",
                                "{0} triangles above the {1} budget.".format(tris, max_tris),
                                "Decimate with: mesh decimate --target-tris {0}".format(max_tris)))
    if tris == 0:
        findings.append(finding("critical", "empty_mesh", "Mesh contains no faces."))
    edges = geom.edge_map(mesh)
    boundary_edges = sum(1 for u in edges.values() if len(u) == 1)
    nonmanifold = sum(1 for u in edges.values() if len(u) > 2)
    if nonmanifold:
        findings.append(finding("error", "nonmanifold_edges",
                                "{0} edges are shared by more than two faces.".format(nonmanifold),
                                "Run: mesh repair"))
    density = [sum(1 for u in edges.values() if len(u) == 1)]
    if profile.get("require_closed", False) and boundary_edges:
        findings.append(finding("error", "open_boundary",
                                "{0} boundary edges found; a closed mesh is required.".format(boundary_edges),
                                "Run: mesh repair --fill"))
    degenerate = geom.degenerate_faces(mesh)
    if degenerate:
        findings.append(finding("warning", "degenerate_faces",
                                "{0} degenerate triangles found.".format(len(degenerate)),
                                "Run: mesh repair"))
    vol = mesh.signed_volume()
    if vol < 0 and profile.get("require_closed", False):
        findings.append(finding("warning", "inverted_winding",
                                "Signed volume is negative; normals point inward.",
                                "Run: mesh repair (unify winding)"))
    scale_expect = profile.get("expected_height_m")
    up_axis = profile.get("up_axis", "Z")
    height = dims[{"X": 0, "Y": 1, "Z": 2}[up_axis]]
    if scale_expect:
        lo, hi = scale_expect * 0.5, scale_expect * 2.0
        if not (lo <= height <= hi):
            findings.append(finding("error", "scale_defect",
                                    "Height along {0} is {1:.3f} m; expected {2} m +/- 50%.".format(
                                        up_axis, height, scale_expect),
                                    "Scale the mesh and re-apply transforms before export."))
    asym = geom.symmetry_report(mesh, profile.get("symmetry_axis", "X"), profile.get("symmetry_tolerance", 0.002))
    if profile.get("require_symmetry") and asym["matched_ratio"] < 0.98:
        findings.append(finding("error", "symmetry_defect",
                                "Only {0:.1%} of vertices have a mirror partner.".format(asym["matched_ratio"]),
                                "Run: mesh symmetry --repair"))
    quality = geom.face_quality(mesh)
    if quality["min_angle_degrees"] is not None and quality["min_angle_degrees"] < profile.get("min_angle_degrees", 5.0):
        findings.append(finding("warning", "sliver_triangles",
                                "Minimum triangle angle is {0:.2f} degrees.".format(quality["min_angle_degrees"]),
                                "Retopologise the flagged region or weld loose detail."))
    valence = geom.valence_stats(mesh)
    if valence["loose_vertices"]:
        findings.append(finding("warning", "loose_vertices",
                                "{0} vertices are not used by any face.".format(valence["loose_vertices"]),
                                "Run: mesh repair"))
    si = geom.self_intersections(mesh, max_pairs=int(profile.get("max_pairs", 200000)))
    if si["hits"]:
        findings.append(finding("warning", "self_intersections",
                                "{0} triangle pairs intersect.".format(si["hits"]),
                                "Fix with sculpt smooth/clay brushes or boolean cleanup.",
                                truncated=si["truncated"]))
    if profile.get("check_uv", False):
        try:
            uvr = geom.uv_report(mesh, texel_resolution=int(profile.get("texture_resolution", 2048)))
            if uvr["flipped_uv_triangles"]:
                findings.append(finding("error", "flipped_uv",
                                        "{0} UV triangles are mirrored.".format(uvr["flipped_uv_triangles"]),
                                        "Recalculate UVs or flip the island."))
            limit = profile.get("max_uv_stretch_ratio", 8.0)
            if uvr["stretch_ratio_p95_over_p05"] and uvr["stretch_ratio_p95_over_p05"] > limit:
                findings.append(finding("warning", "uv_stretch",
                                        "UV stretch ratio {0} exceeds {1}.".format(
                                            uvr["stretch_ratio_p95_over_p05"], limit),
                                        "Relax/repack UVs or split the island."))
            if profile.get("texel_density") and uvr["texel_density_px_per_m"] < profile["texel_density"] * 0.8:
                findings.append(finding("warning", "low_texel_density",
                                        "Texel density {0:.0f} px/m is below the {1} target.".format(
                                            uvr["texel_density_px_per_m"], profile["texel_density"])))
        except core.O3DError as exc:
            findings.append(finding("error", "uv_missing", exc.message, exc.hint))
    report = {
        "mesh": mesh.name,
        "triangles": tris,
        "vertices": len(mesh.verts),
        "bounds": [[round(c, 4) for c in bounds[0]], [round(c, 4) for c in bounds[1]]],
        "dimensions": [round(d, 4) for d in dims],
        "surface_area": round(mesh.surface_area(), 6),
        "volume": round(vol, 6),
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold,
        "degenerate_faces": len(degenerate),
        "quality": quality,
        "valence": valence,
        "symmetry": asym,
        "self_intersections": si,
        "findings": findings,
    }
    report.update(gate(findings, profile.get("fail_on", "error")))
    return report


def animation_gate(curves, fps, profile):
    """Check per-curve keys and cross-curve continuity for export.

    ``curves``: dict name -> list of (frame, value).
    """
    findings = []
    start = profile.get("frame_start", 1)
    end = profile.get("frame_end")
    jitter_limit = profile.get("max_jitter_per_frame", 0.25)
    summary = {}
    for name, keys in sorted(curves.items()):
        if not keys:
            findings.append(finding("error", "empty_curve", "'{0}' has no keys.".format(name)))
            continue
        stats = anim.curve_stats(keys, fps)
        summary[name] = stats
        if stats["start_frame"] > start:
            findings.append(finding("warning", "late_start",
                                    "'{0}' starts at frame {1} (shot starts {2}).".format(
                                        name, stats["start_frame"], start)))
        if end and stats["end_frame"] < end:
            findings.append(finding("warning", "early_end",
                                    "'{0}' ends at frame {1} (shot ends {2}).".format(name, stats["end_frame"], end)))
        worst_jump = 0.0
        ordered = sorted(keys)
        for i in range(1, len(ordered)):
            df = ordered[i][0] - ordered[i - 1][0]
            if df > 0:
                worst_jump = max(worst_jump, abs(ordered[i][1] - ordered[i - 1][1]) / df)
        if worst_jump > jitter_limit:
            findings.append(finding("warning", "jitter_or_pop",
                                    "'{0}' changes {1:.3f} per frame (limit {2}).".format(
                                        name, worst_jump, jitter_limit),
                                    "Inspect for pops; run: anim reduce then re-check.",
                                    curve=name))
    if profile.get("loop") and curves:
        mismatched = []
        for name, keys in curves.items():
            ordered = sorted(keys)
            if len(ordered) >= 2 and abs(ordered[-1][1] - ordered[0][1]) > profile.get("loop_tolerance", 0.001):
                mismatched.append(name)
        if mismatched:
            findings.append(finding("warning", "loop_seam",
                                    "Loop endpoints differ for: {0}".format(", ".join(mismatched)),
                                    "Run: anim loop to resample a continuous cycle."))
    report = {"fps": fps, "curves": summary, "findings": findings}
    report.update(gate(findings, profile.get("fail_on", "warning")))
    return report


def package_gate(paths, base_dir, profile):
    findings = []
    files = []
    total = 0
    for p in paths:
        rel = os.path.relpath(p, base_dir)
        if not os.path.exists(p):
            findings.append(finding("critical", "missing_deliverable", "{0} is missing.".format(rel)))
            continue
        size = os.path.getsize(p)
        total += size
        entry = {"path": rel.replace(os.sep, "/"), "bytes": size, "sha256": core.sha256_file(p)}
        files.append(entry)
        if size == 0:
            findings.append(finding("critical", "empty_file", "{0} is zero bytes.".format(rel)))
        if b"\\x00" * 16 in core.read_bytes(p, max_mb=profile.get("scan_mb", 8))[:1048576] and rel.endswith((".json", ".md", ".txt", ".obj", ".mtl", ".usda", ".vtt", ".srt")):
            findings.append(finding("warning", "binary_in_text_file", "{0} contains binary bytes.".format(rel)))
    allowed = profile.get("allowed_extensions")
    if allowed:
        for entry in files:
            ext = os.path.splitext(entry["path"])[1].lower()
            if ext not in allowed:
                findings.append(finding("warning", "unexpected_extension",
                                        "{0} is not in the allowed delivery extensions.".format(entry["path"])))
    if profile.get("max_total_mb") and total / (1024 * 1024) > profile["max_total_mb"]:
        findings.append(finding("error", "package_too_large",
                                "Package is {0:.1f} MB, above the {1} MB limit.".format(
                                    total / (1024 * 1024), profile["max_total_mb"]),
                                "Split the delivery or compress the textures."))
    if profile.get("require_readme") and not any(f["path"].lower().endswith((".md", ".txt")) for f in files):
        findings.append(finding("error", "missing_readme",
                                "No README/handoff text file found in the package.",
                                "Add the handoff template filled by 'standards handoff'."))
    return {"files": files, "file_count": len(files), "total_bytes": total,
            "findings": findings, **gate(findings, profile.get("fail_on", "error"))}


# ---------------------------------------------------------------------------
# Reports and contact sheets
# ---------------------------------------------------------------------------


def report_markdown(title, summary, sections, generated_by=None):
    lines = ["# {0}".format(title), ""]
    lines.append("_Generated {0} by {1} (offline, no network used)._".format(
        core.now_iso(), generated_by or "{0} {1}".format(core.TOOL_NAME, core.VERSION)))
    lines.append("")
    if summary:
        lines.append("| Field | Value |")
        lines.append("| --- | --- |")
        for k, v in summary.items():
            lines.append("| {0} | {1} |".format(k, v))
        lines.append("")
    for sec in sections:
        lines.append("## {0}".format(sec.get("heading", "Section")))
        lines.append("")
        if sec.get("body"):
            lines.append(str(sec["body"]))
            lines.append("")
        if sec.get("table"):
            cols = sec["table"].get("columns", [])
            rows = sec["table"].get("rows", [])
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for row in rows:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
            lines.append("")
        if sec.get("bullets"):
            for b in sec["bullets"]:
                lines.append("- {0}".format(b))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_html(title, summary, sections):
    esc = html.escape
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<title>{0}</title>".format(esc(title)),
        "<style>body{font-family:system-ui,sans-serif;margin:32px;color:#1b1f24;background:#fbfbfa}"
        "h1{font-size:22px}table{border-collapse:collapse;margin:12px 0}"
        "td,th{border:1px solid #d5d7d9;padding:6px 10px;font-size:13px;text-align:left}"
        "th{background:#eef1f3}code{background:#eef1f3;padding:1px 4px;border-radius:3px}"
        ".sev-error{color:#b3261e;font-weight:600}.sev-warning{color:#8a5a00}"
        ".sev-critical{color:#7c1d13;font-weight:700}.sev-info{color:#3b5bdb}"
        "footer{margin-top:24px;font-size:12px;color:#6b7280}</style></head><body>",
        "<h1>{0}</h1>".format(esc(title)),
    ]
    if summary:
        parts.append("<table>")
        for k, v in summary.items():
            parts.append("<tr><th>{0}</th><td>{1}</td></tr>".format(esc(str(k)), esc(str(v))))
        parts.append("</table>")
    for sec in sections:
        parts.append("<h2>{0}</h2>".format(esc(sec.get("heading", "Section"))))
        if sec.get("body"):
            parts.append("<p>{0}</p>".format(esc(str(sec["body"]))))
        if sec.get("table"):
            cols = sec["table"].get("columns", [])
            rows = sec["table"].get("rows", [])
            parts.append("<table><tr>" + "".join("<th>{0}</th>".format(esc(str(c))) for c in cols) + "</tr>")
            for row in rows:
                parts.append("<tr>")
                for c in row:
                    cell = esc(str(c))
                    for sev in ("critical", "error", "warning", "info"):
                        if cell.startswith(sev + ":"):
                            cell = "<span class='sev-{0}'>{1}</span>".format(sev, cell)
                            break
                    parts.append("<td>{0}</td>".format(cell))
                parts.append("</tr>")
            parts.append("</table>")
        if sec.get("bullets"):
            parts.append("<ul>" + "".join("<li>{0}</li>".format(esc(str(b))) for b in sec["bullets"]) + "</ul>")
    parts.append("<footer>Offline report &middot; {0} {1} &middot; {2}</footer>".format(
        esc(core.TOOL_NAME), esc(core.VERSION), esc(core.now_iso())))
    parts.append("</body></html>")
    return "\n".join(parts)


def contact_sheet(items, columns=4, cell=128, label_height=16, background=(24, 26, 30)):
    """Render a labelled contact sheet PNG from image files.

    ``items``: list of dicts ``{"label": str, "path": str}``. Uses nearest-fit
    scaling so thumbnails stay sharp for texel inspection.
    """
    tex.require_numpy()
    import numpy as _np
    if not items:
        raise core.invalid("Contact sheet needs at least one image.")
    columns = max(1, min(int(columns), len(items)))
    rows = int(math.ceil(len(items) / float(columns)))
    width = columns * cell
    height = rows * (cell + label_height)
    canvas = _np.zeros((height, width, 3), dtype=_np.uint8)
    canvas[:, :] = background
    # 5x7 bitmap font for labels (subset: A-Z0-9-_.)
    for idx, item in enumerate(items):
        col = idx % columns
        row = idx // columns
        x0 = col * cell
        y0 = row * (cell + label_height)
        img = tex.Image.load(item["path"])
        thumb = img.to_rgb().resize(cell, cell)
        arr = thumb.array
        canvas[y0 : y0 + arr.shape[0], x0 : x0 + arr.shape[1], :] = arr
        draw_text(canvas, x0 + 2, y0 + cell + 2, str(item.get("label", ""))[: int(cell / 6)], (225, 228, 232))
    return tex.Image(canvas)


FONT_5X7 = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00110", "00110"],
    ":": ["00000", "00110", "00110", "00000", "00110", "00110", "00000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def draw_text(canvas, x, y, text, color=(240, 240, 240), scale=1):
    import numpy as _np
    cx = x
    for ch in str(text).upper():
        glyph = FONT_5X7.get(ch, FONT_5X7["-" if ch not in FONT_5X7 else ch])
        for gy, rowbits in enumerate(glyph):
            for gx, bit in enumerate(rowbits):
                if bit == "1":
                    py = y + gy * scale
                    px = cx + gx * scale
                    if 0 <= py < canvas.shape[0] - scale and 0 <= px < canvas.shape[1] - scale:
                        canvas[py : py + scale, px : px + scale, :] = color
        cx += 6 * scale
    return canvas


# ---------------------------------------------------------------------------
# Deterministic fixture generation (used by self-test and demos)
# ---------------------------------------------------------------------------


def make_fixture_meshes(out_dir):
    import os as _os
    core.ensure_dir(out_dir)
    made = {}
    cube = geom.make_grid(4, 1.0)
    core.dump_json(_os.path.join(out_dir, "fixture-cube.json"), {
        "name": "cube", "verts": cube.verts, "faces": cube.faces})
    geom.write_obj(cube, _os.path.join(out_dir, "fixture-cube.obj"))
    made["cube"] = "fixture-cube.obj"
    sphere = geom.make_uv_sphere(32, 16, 0.5)
    geom.write_obj(sphere, _os.path.join(out_dir, "fixture-sphere.obj"))
    made["sphere"] = "fixture-sphere.obj"
    open_mesh = cube.clone()
    open_mesh.faces = open_mesh.faces[:-40]
    open_mesh.invalidate()
    geom.write_obj(open_mesh, _os.path.join(out_dir, "fixture-open.obj"))
    made["open"] = "fixture-open.obj"
    geom.write_stl_binary(sphere, _os.path.join(out_dir, "fixture-sphere.stl"))
    made["sphere_stl"] = "fixture-sphere.stl"
    geom.write_glb(cube, _os.path.join(out_dir, "fixture-cube.glb"))
    made["cube_glb"] = "fixture-cube.glb"
    with open(_os.path.join(out_dir, "fixture-capture.bvh"), "w", encoding="utf-8") as fh:
        fh.write(anim.synthetic_bvh(frames=48, fps=24))
    made["bvh"] = "fixture-capture.bvh"
    return made
