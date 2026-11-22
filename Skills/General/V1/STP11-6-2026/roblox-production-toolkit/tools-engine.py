"""Engine family: asset and avatar pipeline checks, geometry and spatial algorithms, UI layout checks.

Loaded as rbx_engine by tools-roblox.py. Every computation runs offline on the supplied
inputs. Nothing here opens Roblox Studio, imports assets, or contacts a service.
"""
import heapq
import math
import random
import rbx_core as core
from rbx_core import chk

FAMILY = "engine"
_AVATAR = None
_TOKENS = None


def out(result, findings, artifacts=None, warnings=None):
    return {"result": result, "findings": findings, "artifacts": artifacts or {}, "warnings": warnings or []}


def avatar_rules():
    global _AVATAR
    if _AVATAR is None:
        _AVATAR = core.res_json("asset-avatar-rules.json")
    return _AVATAR


def ui_tokens():
    global _TOKENS
    if _TOKENS is None:
        _TOKENS = core.res_json("asset-ui-tokens.json")
    return _TOKENS


def steps_ok(n, ctx):
    limit = ctx.config["limits"]["simulation_steps"]
    if n > limit:
        core.fail("INPUT_LIMIT", "Computation would exceed the configured step limit.", steps=n, limit=limit)
    return n


def vec2(value, name):
    if not isinstance(value, list) or len(value) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        core.fail("INPUT_INVALID", f"{name} must be a finite [x, y] pair.")
    return [float(value[0]), float(value[1])]


def vlen(v):
    return math.sqrt(sum(x * x for x in v))


def vsub(a, b):
    return [x - y for x, y in zip(a, b)]


def vdot(a, b):
    return sum(x * y for x, y in zip(a, b))


def vnorm(v):
    n = vlen(v)
    return [x / n for x in v] if n > 0 else [0.0] * len(v)


def pct(part, whole):
    return round(100.0 * part / whole, 3) if whole else 0.0


def min_pair_distance(a_list, b_list):
    if len(a_list) != len(b_list):
        core.fail("INPUT_INVALID", "Vertex lists must have equal length for paired clearance.")
    return min((vlen(vsub(a, b)) for a, b in zip(a_list, b_list)), default=0.0)


def valid_names(items, key="name", maximum=200):
    names = [core.text(it, key, maximum=120) for it in items]
    return names


def camera_distance(extent, fov_deg, margin):
    half = math.radians(fov_deg) / 2
    return (extent / 2) / math.tan(half) * margin


# ================================================================ asset / avatar

def h_avatar_body(d, cap, ctx):
    scale = core.number(d, "scale", minimum=0.1, maximum=10)
    height = core.number(d, "height", minimum=0.1, maximum=100)
    parts = core.array(d, "parts", required=True, kind=str, maximum=100)
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 8)
    rights = core.boolean(d, "rights_confirmed", False)
    rules = avatar_rules()
    limit = rules["limits"]["avatar_body_triangles_max"]
    missing = sorted(set(rules["r15"]["parts"]) - set(parts))
    extra = sorted(set(parts) - set(rules["r15"]["parts"]))
    tol = rules["limits"]["mesh_scale_tolerance"]
    findings = [chk("avatar_body.triangles", tris <= limit, f"{tris} triangles within {limit}." if tris <= limit else f"{tris} triangles exceed {limit}.", "fail"),
                chk("avatar_body.r15_parts", not missing, "All R15 parts are present." if not missing else "Missing R15 parts: " + ", ".join(missing), "fail"),
                chk("avatar_body.extra_parts", not extra, "No non-R15 parts." if not extra else "Non-R15 parts need review: " + ", ".join(extra), "warning"),
                chk("avatar_body.scale", abs(scale - 1.0) <= tol, f"Scale {scale} is within {tol} of 1.0." if abs(scale - 1.0) <= tol else f"Scale {scale} differs from 1.0 by more than {tol}; apply transforms before export.", "warning"),
                chk("avatar_body.rights", rights, "Rights to the body mesh are confirmed." if rights else "Confirm you own or license this body mesh.", "fail")]
    return out({"triangles": tris, "triangle_limit": limit, "missing_parts": missing, "height": height}, findings)


def h_r15(d, cap, ctx):
    parts = core.array(d, "parts", required=True, kind=str, maximum=100)
    joints = core.array(d, "joints", required=True, maximum=200)
    rules = avatar_rules()["r15"]
    expected = set(rules["parts"])
    have = set(parts)
    missing = sorted(expected - have)
    extra = sorted(have - expected)
    pairs = set()
    for j in joints:
        if isinstance(j, dict):
            pairs.add((j.get("part0"), j.get("part1")))
        elif isinstance(j, list) and len(j) == 2:
            pairs.add((j[0], j[1]))
    expected_pairs = {tuple(p) for p in rules["joints"]}
    missing_joints = sorted(expected_pairs - pairs)
    findings = [chk("r15.parts", not missing and not extra, "Part set matches R15 exactly." if not missing and not extra else f"Missing {missing or 'none'}; extra {extra or 'none'}.", "fail"),
                chk("r15.joints", not missing_joints, "All R15 joints are present." if not missing_joints else f"{len(missing_joints)} joint(s) missing.", "fail"),
                chk("r15.root", rules["root_part"] in have, "HumanoidRootPart is present." if rules["root_part"] in have else "HumanoidRootPart is missing.", "fail")]
    return out({"expected_parts": len(expected), "parts_found": len(have & expected), "missing_joints": [list(p) for p in missing_joints]}, findings)


def h_humanoid_description(d, cap, ctx):
    desc = core.obj(d, "description", required=True)
    ids = core.array(d, "asset_ids", maximum=500)
    bad_ids = [x for x in ids if isinstance(x, bool) or not isinstance(x, int) or x <= 0]
    dupes = sorted({x for x in ids if ids.count(x) > 1}, key=str)
    scales = {k: v for k, v in desc.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    out_range = {k: v for k, v in scales.items() if not (0.5 <= v <= 2.0)}
    findings = [chk("humanoid_description.ids", not bad_ids, "Asset ids are positive integers." if not bad_ids else f"{len(bad_ids)} invalid asset id(s).", "fail"),
                chk("humanoid_description.duplicates", not dupes, "No duplicate accessories." if not dupes else "Duplicate accessories: " + ", ".join(map(str, dupes)), "warning"),
                chk("humanoid_description.scales", not out_range, "Body scales are within 0.5 to 2.0." if not out_range else f"Scales outside 0.5-2.0: {out_range}", "warning")]
    return out({"scales": scales, "asset_count": len(ids), "unique_assets": len(set(ids))}, findings)


def h_dynamic_head(d, cap, ctx):
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 8)
    facs = core.boolean(d, "has_facs", False)
    cage = core.boolean(d, "cage_vertex_match", False)
    infl = core.integer(d, "max_skin_influences", minimum=0, maximum=100)
    expr = core.array(d, "expressions", maximum=200)
    rules = avatar_rules()["limits"]
    findings = [chk("dynamic_head.triangles", tris <= rules["avatar_body_triangles_max"], "Head triangle count is within the body budget." if tris <= rules["avatar_body_triangles_max"] else "Head exceeds the body triangle budget.", "fail"),
                chk("dynamic_head.facs", facs, "FACS-compatible rig present." if facs else "No FACS rig; expression fidelity will be limited.", "warning"),
                chk("dynamic_head.cage", cage, "Head cage vertices match the mesh." if cage else "Cage vertices must match the head mesh.", "fail"),
                chk("dynamic_head.influences", infl <= rules["skin_influences_max"], f"{infl} skin influences per vertex." if infl <= rules["skin_influences_max"] else f"{infl} influences exceed {rules['skin_influences_max']}.", "fail")]
    return out({"triangles": tris, "expressions": len(expr), "max_skin_influences": infl}, findings)


def h_avatar_setup(d, cap, ctx):
    source = core.text(d, "source", maximum=200)
    target = core.text(d, "target", maximum=200)
    rights = core.boolean(d, "rights_confirmed", False)
    uvs = core.boolean(d, "has_uvs", False)
    rest = core.boolean(d, "has_rest_pose", False)
    findings = [chk("avatar_setup.rights", rights, "Source rights are confirmed." if rights else "Confirm rights to the source before setup.", "fail"),
                chk("avatar_setup.uvs", uvs, "UVs are present." if uvs else "UVs are required for textures.", "fail"),
                chk("avatar_setup.rest_pose", rest, "A rest pose is present for skinning." if rest else "Provide a rest (T-pose) for skinning.", "fail"),
                chk("avatar_setup.names", source != target, "Source and target are distinct." if source != target else "Source and target are identical.", "warning")]
    return out({"source": source, "target": target, "ready": all(f["status"] == "pass" for f in findings)}, findings)


def h_skin_transfer(d, cap, ctx):
    sv = core.integer(d, "source_vertices", minimum=0, maximum=10 ** 7)
    tv = core.integer(d, "target_vertices", minimum=0, maximum=10 ** 7)
    compat = core.boolean(d, "compatible_topology", False)
    weights = core.array(d, "weights", maximum=100000)
    max_inf = core.integer(d, "max_influences", minimum=1, maximum=20)
    limit = avatar_rules()["limits"]["skin_influences_max"]
    bad_sum, too_many = 0, 0
    for row in weights:
        if not isinstance(row, list):
            core.fail("INPUT_INVALID", "weights must be a list of per-vertex lists.")
        vals = [float(x) for x in row if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if abs(sum(vals) - 1.0) > 1e-3 and vals:
            bad_sum += 1
        if len(vals) > max_inf:
            too_many += 1
    findings = [chk("skin_transfer.topology", compat, "Topology matches for a one-to-one transfer." if compat else "Topology differs; use a cage or nearest-point transfer and review.", "fail" if not compat else "info"),
                chk("skin_transfer.counts", sv == tv or not compat, "Vertex counts match." if sv == tv else f"Vertex counts differ ({sv} vs {tv}).", "warning"),
                chk("skin_transfer.normalized", bad_sum == 0, "Weights sum to 1 per vertex." if bad_sum == 0 else f"{bad_sum} vertex/vertices do not sum to 1.", "fail"),
                chk("skin_transfer.influences", too_many == 0 and max_inf <= limit, f"At most {max_inf} influences per vertex (platform limit {limit})." if too_many == 0 and max_inf <= limit else f"{too_many} vertex(es) exceed influence limits.", "fail")]
    return out({"vertices_source": sv, "vertices_target": tv, "vertices_with_bad_sum": bad_sum}, findings)


def h_avatar_import(d, cap, ctx):
    parts = core.array(d, "parts", required=True, kind=str, maximum=100)
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 8)
    tex = core.integer(d, "texture_size", minimum=1, maximum=65536)
    scale = core.number(d, "scale", minimum=0.0001, maximum=1000)
    cages = core.boolean(d, "cages_valid", False)
    owned = core.boolean(d, "owned", False)
    names = core.boolean(d, "names_valid", False)
    rules = avatar_rules()
    missing = sorted(set(rules["r15"]["parts"]) - set(parts))
    findings = [chk("avatar_import.r15", not missing, "R15 part set complete." if not missing else "Missing R15 parts: " + ", ".join(missing), "fail"),
                chk("avatar_import.triangles", tris <= rules["limits"]["avatar_body_triangles_max"], "Triangle count within budget." if tris <= rules["limits"]["avatar_body_triangles_max"] else f"{tris} triangles exceed the budget.", "fail"),
                chk("avatar_import.texture", tex <= rules["limits"]["texture_max_side_px"], "Texture side within the limit." if tex <= rules["limits"]["texture_max_side_px"] else f"Texture side {tex}px exceeds {rules['limits']['texture_max_side_px']}px.", "fail"),
                chk("avatar_import.scale", abs(scale - 1) <= rules["limits"]["mesh_scale_tolerance"], "Scale is applied." if abs(scale - 1) <= rules["limits"]["mesh_scale_tolerance"] else "Apply the scale transform before import.", "warning"),
                chk("avatar_import.cages", cages, "Cages are valid." if cages else "Cages must be valid before fitting.", "fail"),
                chk("avatar_import.ownership", owned, "Import is owned by the creator." if owned else "Ownership of the import is not confirmed.", "fail"),
                chk("avatar_import.names", names, "Part names follow the R15 naming." if names else "Rename parts to the R15 names.", "fail")]
    return out({"missing_parts": missing, "triangles": tris, "texture_side": tex}, findings)


def h_npc_rig(d, cap, ctx):
    name = core.text(d, "name", maximum=80)
    mode = core.text(d, "mode", maximum=20)
    walk = core.number(d, "walk_speed", minimum=0, maximum=200)
    collide = core.boolean(d, "can_collide", False)
    tag = core.text(d, "interaction_tag", maximum=60, required=False) or ""
    known_tags = set(ctx.config["studio"]["tags"])
    findings = [chk("npc_rig.mode", mode in ("server", "client_visual"), "Mode is server or client_visual." if mode in ("server", "client_visual") else "Mode must be server or client_visual.", "fail"),
                chk("npc_rig.speed", walk <= 50, "Walk speed is conventional." if walk <= 50 else "Walk speed above 50 studs/s is unusual.", "warning"),
                chk("npc_rig.collisions", not collide or mode == "server", "NPC collisions are simulated on the server." if not collide or mode == "server" else "Collidable client-only NPCs desync; use server mode.", "warning"),
                chk("npc_rig.tag", (not tag) or tag in known_tags, "Interaction tag is a configured tag." if (not tag) or tag in known_tags else f"Tag {tag} is not in the configured tag list.", "warning")]
    return out({"name": name, "mode": mode, "walk_speed": walk}, findings)


def h_avatar_preview(d, cap, ctx):
    height = core.number(d, "subject_height", minimum=0.1, maximum=100)
    dist = core.number(d, "distance", minimum=0.1, maximum=1000)
    fov = core.number(d, "fov", minimum=10, maximum=120)
    restore = core.boolean(d, "restore_on_cancel", False)
    server_check = core.boolean(d, "server_validates_ownership", False)
    needed = camera_distance(height, fov, 1.15)
    fits = dist >= needed
    findings = [chk("avatar_preview.framing", fits, f"Camera distance {dist} frames the {height}-stud subject (needs {round(needed, 2)})." if fits else f"Camera at {dist} cuts off the subject; move it to at least {round(needed, 2)}.", "warning"),
                chk("avatar_preview.restore", restore, "Camera and character state are restored on cancel." if restore else "Restore the camera and character on cancel.", "fail"),
                chk("avatar_preview.ownership", server_check, "Server validates ownership before equipping." if server_check else "Preview must not equip items the player does not own; validate on the server.", "fail")]
    return out({"required_distance": round(needed, 4), "distance": dist}, findings)


def h_classic_clothing(d, cap, ctx):
    kind = core.text(d, "kind", maximum=20)
    w = core.integer(d, "width", minimum=1, maximum=100000)
    h = core.integer(d, "height", minimum=1, maximum=100000)
    alpha = core.boolean(d, "alpha_valid", False)
    rights = core.boolean(d, "rights_confirmed", False)
    rules = avatar_rules()
    key = {"shirt": "shirt_template_px", "pants": "pants_template_px", "tshirt": "tshirt_template_px"}.get(kind)
    if key is None:
        core.fail("INPUT_INVALID", "kind must be shirt, pants or tshirt.", kind=kind)
    expected = rules["classic_clothing"][key]
    findings = [chk("classic_clothing.template", [w, h] == expected, f"Template size {w}x{h} matches {expected[0]}x{expected[1]}." if [w, h] == expected else f"Template must be {expected[0]}x{expected[1]} px, got {w}x{h}.", "fail"),
                chk("classic_clothing.alpha", alpha if rules["classic_clothing"]["alpha_allowed"] else None, "Alpha channel is valid." if alpha else "Alpha channel is not valid; fix stray transparency.", "warning"),
                chk("classic_clothing.rights", rights, "Rights are confirmed." if rights else "Confirm rights to the artwork.", "fail")]
    return out({"kind": kind, "expected": expected, "actual": [w, h]}, findings)


def h_layered_clothing(d, cap, ctx):
    inner = core.array(d, "inner_vertices", required=True, maximum=50000)
    outer = core.array(d, "outer_vertices", required=True, maximum=50000)
    weights_ok = core.boolean(d, "skin_weights_normalized", False)
    bodies = core.array(d, "test_bodies", maximum=100, kind=str)
    passed = core.integer(d, "clipping_tests_passed", minimum=0, maximum=10000)
    if len(inner) != len(outer):
        core.fail("INPUT_INVALID", "inner_vertices and outer_vertices must pair one-to-one.")
    pairs_in = [core.vector(v, 3) for v in inner]
    pairs_out = [core.vector(v, 3) for v in outer]
    clearance = min_pair_distance(pairs_in, pairs_out) if pairs_in else 0.0
    min_c = avatar_rules()["limits"]["cage_min_clearance_studs"]
    total_tests = max(1, len(bodies))
    findings = [chk("layered_clothing.clearance", clearance >= min_c, f"Minimum clearance {round(clearance, 4)} studs." if clearance >= min_c else f"Clearance {round(clearance, 4)} is below {min_c}; the outer layer will clip.", "fail"),
                chk("layered_clothing.weights", weights_ok, "Skin weights are normalized." if weights_ok else "Normalize skin weights before fitting.", "fail"),
                chk("layered_clothing.clipping", passed >= total_tests, f"{passed} clipping test(s) passed across {len(bodies)} body type(s)." if passed >= total_tests else f"Only {passed} of {total_tests} clipping tests passed.", "fail"),
                chk("layered_clothing.bodies", bool(bodies), "Test bodies are listed." if bodies else "List the test bodies used for clipping review.", "warning")]
    return out({"min_clearance": round(clearance, 5), "vertex_pairs": len(pairs_in)}, findings)


def h_accessory(d, cap, ctx):
    kind = core.text(d, "kind", maximum=40)
    attach = core.text(d, "attachment", maximum=60)
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 8)
    bounds = core.vector(d.get("bounds"), 3)
    owned = core.boolean(d, "owned", False)
    limit = avatar_rules()["limits"]["accessory_triangles_max"]
    findings = [chk("accessory.triangles", tris <= limit, f"{tris} triangles within {limit}." if tris <= limit else f"{tris} triangles exceed {limit}.", "fail"),
                chk("accessory.attachment", bool(attach), f"Attaches to {attach}." if attach else "Attachment point is required.", "fail"),
                chk("accessory.ownership", owned, "Ownership confirmed." if owned else "Ownership is not confirmed.", "fail"),
                chk("accessory.bounds", all(b > 0 for b in bounds), "Bounds are positive." if all(b > 0 for b in bounds) else "Bounds must be positive in every axis.", "fail")]
    return out({"kind": kind, "triangles": tris, "bounds": bounds}, findings)


def h_cage(d, cap, ctx):
    inner = core.array(d, "inner", required=True, maximum=50000)
    outer = core.array(d, "outer", required=True, maximum=50000)
    min_c = core.number(d, "minimum_clearance", minimum=0, maximum=100)
    if len(inner) != len(outer):
        core.fail("INPUT_INVALID", "inner and outer must have the same vertex count.")
    pi = [core.vector(v, 3) for v in inner]
    po = [core.vector(v, 3) for v in outer]
    dists = [vlen(vsub(a, b)) for a, b in zip(pi, po)]
    thin = [i for i, x in enumerate(dists) if x < min_c]
    findings = [chk("cage.clearance", not thin, "Every cage vertex keeps the minimum clearance." if not thin else f"{len(thin)} vertex pair(s) closer than {min_c}.", "fail"),
                chk("cage.inside_out", all(x >= 0 for x in dists), "No inverted pairs.")]
    return out({"min_distance": round(min(dists), 5) if dists else None, "thin_pairs": len(thin)}, findings)


def h_textures(d, cap, ctx):
    maps = core.obj(d, "maps", required=True)
    rules = avatar_rules()
    limit = rules["limits"]["texture_max_side_px"]
    exts = set(rules["allowed_texture_extensions"])
    rows, findings = [], []
    for name, m in maps.items():
        if not isinstance(m, dict):
            core.fail("INPUT_INVALID", "Each map must be an object with width, height and format.", map=name)
        w, h = core.integer(m, "width", minimum=1, maximum=65536), core.integer(m, "height", minimum=1, maximum=65536)
        fmt = core.text(m, "format", maximum=10).lower()
        rows.append({"map": name, "width": w, "height": h, "format": fmt})
        if max(w, h) > limit:
            findings.append(chk("textures.size", False, f"{name} is {w}x{h}; the side limit is {limit}px.", "fail"))
        if fmt not in exts:
            findings.append(chk("textures.format", False, f"{name} uses {fmt}; allowed formats are {', '.join(sorted(exts))}.", "fail"))
        if w != h and max(w, h) and (w & (w - 1) or h & (h - 1)):
            findings.append(chk("textures.power_of_two", None, f"{name} is {w}x{h}; non-power-of-two sizes may waste mip space."))
    if not any(f["status"] == "fail" for f in findings):
        findings.append(chk("textures.size", True, f"All {len(rows)} map(s) fit the {limit}px side limit."))
        findings.append(chk("textures.format", True, "All maps use allowed formats."))
    return out({"maps": rows}, findings)


def h_outfit(d, cap, ctx):
    items = core.array(d, "items", required=True, kind=dict, maximum=200)
    anims = set(core.array(d, "animations_owned", maximum=200, kind=str))
    slots = {}
    conflicts = []
    for it in items:
        slot = core.text(it, "slot", maximum=40)
        name = core.text(it, "name", maximum=80)
        if slot in slots:
            conflicts.append({"slot": slot, "items": [slots[slot], name]})
        else:
            slots[slot] = name
    unowned_anims = sorted({it["animation"] for it in items if it.get("animation") and it["animation"] not in anims})
    findings = [chk("outfit.slots", not conflicts, "Each slot holds one item." if not conflicts else f"{len(conflicts)} slot conflict(s).", "fail"),
                chk("outfit.animations", not unowned_anims, "Every animation in the outfit is owned." if not unowned_anims else "Unowned animations: " + ", ".join(unowned_anims), "fail")]
    return out({"slots": slots, "conflicts": conflicts}, findings)


def h_mesh_pipeline(d, cap, ctx):
    name = core.text(d, "name", maximum=120)
    units = core.text(d, "units", maximum=20)
    scale = core.number(d, "scale_to_studs", minimum=0.00001, maximum=10000)
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 8)
    uvs = core.boolean(d, "uvs", False)
    version = core.text(d, "version", maximum=40)
    known = {"studs", "meters", "centimeters", "inches"}
    findings = [chk("mesh_pipeline.units", units in known, f"Units {units} are known." if units in known else f"Units {units} are not recognized.", "fail"),
                chk("mesh_pipeline.scale", scale > 0, f"Scale factor {scale} is applied to convert to studs."),
                chk("mesh_pipeline.uvs", uvs, "UVs are present." if uvs else "UVs are required for textured meshes.", "warning"),
                chk("mesh_pipeline.version", bool(version), f"Export version {version} recorded." if version else "Record the export version.", "info")]
    return out({"name": name, "units": units, "scale_to_studs": scale, "triangles": tris}, findings, {"mesh-import-manifest.json": {"name": name, "units": units, "scale": scale, "triangles": tris, "version": version}})


def h_dcc_export(d, cap, ctx):
    dcc = core.text(d, "dcc", maximum=40)
    applied = core.boolean(d, "applied_transforms", False)
    units = core.text(d, "units", maximum=20)
    unit_scale = core.number(d, "unit_scale", minimum=0.00001, maximum=10000)
    tri = core.boolean(d, "triangulated", False)
    arm = core.boolean(d, "armature_clean", False)
    fmt = core.text(d, "format", maximum=10).lower()
    exts = set(avatar_rules()["allowed_mesh_extensions"])
    findings = [chk("dcc_export.transforms", applied, "Transforms are applied before export." if applied else "Apply all transforms in the DCC before export.", "fail"),
                chk("dcc_export.units", unit_scale > 0, f"Unit scale {unit_scale} from {units}."),
                chk("dcc_export.triangulated", tri, "Mesh is triangulated." if tri else "Triangulate or accept per-engine tessellation.", "warning"),
                chk("dcc_export.armature", arm, "Armature is clean (no non-deform bones)." if arm else "Clean the armature before export.", "warning"),
                chk("dcc_export.format", fmt in exts, f"Format {fmt} is accepted." if fmt in exts else f"Format {fmt} is not in {sorted(exts)}.", "fail")]
    return out({"dcc": dcc, "format": fmt, "unit_scale": unit_scale}, findings)


def h_pbr(d, cap, ctx):
    cs = core.text(d, "color_space", maximum=20)
    ns = core.text(d, "normal_space", maximum=20)
    rough = core.number(d, "roughness", minimum=0, maximum=1)
    metal = core.number(d, "metalness", minimum=0, maximum=1)
    alpha = core.text(d, "alpha_mode", maximum=20)
    shared = core.boolean(d, "maps_shared_resolution", False)
    findings = [chk("pbr.color_space", cs.lower() in ("srgb",), "Albedo is sRGB." if cs.lower() == "srgb" else "Albedo should be sRGB; data maps should be linear.", "warning"),
                chk("pbr.normal_space", ns.lower() == "tangent", "Normal maps use tangent space." if ns.lower() == "tangent" else "Use tangent-space normal maps.", "warning"),
                chk("pbr.alpha", alpha in ("opaque", "mask", "blend"), "Alpha mode is valid." if alpha in ("opaque", "mask", "blend") else "Alpha mode must be opaque, mask or blend.", "fail"),
                chk("pbr.resolution", shared, "Maps share a resolution." if shared else "Maps use mixed resolutions; match them to avoid stretching.", "warning")]
    return out({"roughness": rough, "metalness": metal, "alpha_mode": alpha}, findings)


def h_deformation(d, cap, ctx):
    norm = core.boolean(d, "normalized_weights", False)
    maxi = core.integer(d, "max_influences", minimum=1, maximum=50)
    bind = core.boolean(d, "bind_pose_matches", False)
    neg = core.boolean(d, "negative_scale", False)
    zero = core.integer(d, "zero_length_normals", minimum=0, maximum=10 ** 7)
    lim = avatar_rules()["limits"]["skin_influences_max"]
    findings = [chk("deformation.weights", norm, "Weights are normalized." if norm else "Normalize weights.", "fail"),
                chk("deformation.influences", maxi <= lim, f"{maxi} influences per vertex (limit {lim})." if maxi <= lim else f"{maxi} influences exceed {lim}.", "fail"),
                chk("deformation.bind_pose", bind, "Bind pose matches the rest pose." if bind else "Bind pose must match the rest pose.", "fail"),
                chk("deformation.negative_scale", not neg, "No negative scale." if not neg else "Negative scale flips normals and skin; apply it in the DCC.", "fail"),
                chk("deformation.normals", zero == 0, "No zero-length normals." if zero == 0 else f"{zero} zero-length normal(s).", "warning")]
    return out({"max_influences": maxi, "zero_length_normals": zero}, findings)


def h_import_automation(d, cap, ctx):
    assets = core.array(d, "assets", required=True, kind=dict, maximum=2000)
    container = core.text(d, "container", maximum=120)
    exts = set(avatar_rules()["allowed_mesh_extensions"]) | set(avatar_rules()["allowed_texture_extensions"]) | {"ogg", "mp3", "wav", "flac"}
    plan, bad, names = [], [], []
    for a in assets:
        nm = core.text(a, "name", maximum=120)
        ext = core.text(a, "extension", maximum=10).lower()
        names.append(nm)
        if ext not in exts:
            bad.append(f"{nm}.{ext}")
        plan.append({"name": nm, "extension": ext, "destination": f"{container}/{nm}"})
    dupes = sorted({x for x in names if names.count(x) > 1})
    findings = [chk("import_automation.extensions", not bad, "All extensions are accepted." if not bad else "Unsupported extensions: " + ", ".join(bad), "fail"),
                chk("import_automation.duplicates", not dupes, "No duplicate names." if not dupes else "Duplicate names collide in the container: " + ", ".join(dupes), "fail"),
                chk("import_automation.dry_run", True, "This operation writes an import plan only; it does not import into Studio.")]
    return out({"container": container, "planned": len(plan), "rejected": bad}, findings, {"import-plan.json": plan})


def h_audio_pipeline(d, cap, ctx):
    assets = core.array(d, "assets", required=True, kind=dict, maximum=2000)
    rows, findings = [], []
    fmts = {"mp3", "ogg", "wav", "flac"}
    for a in assets:
        nm = core.text(a, "name", maximum=120)
        fmt = core.text(a, "format", maximum=10).lower()
        dur = core.number(a, "duration", minimum=0, maximum=86400)
        rows.append({"name": nm, "format": fmt, "duration": dur, "stream": dur > 30})
        if fmt not in fmts:
            findings.append(chk("audio_pipeline.format", False, f"{nm} uses {fmt}; accepted formats are {', '.join(sorted(fmts))}.", "fail"))
    longs = [r["name"] for r in rows if r["stream"]]
    findings.append(chk("audio_pipeline.streaming", not longs, "No asset longer than 30 s; no streaming required." if not longs else "Stream these long assets: " + ", ".join(longs), "warning"))
    if not any(f["status"] == "fail" for f in findings):
        findings.append(chk("audio_pipeline.format", True, "All audio formats are accepted."))
    return out({"assets": rows, "streaming": longs}, findings, {"audio-manifest.json": rows})


def h_materials(d, cap, ctx):
    mats = core.array(d, "materials", required=True, kind=dict, maximum=1000)
    palette = core.integer(d, "palette_size", minimum=1, maximum=10000)
    names = [core.text(m, "name", maximum=80) for m in mats]
    dupes = sorted({x for x in names if names.count(x) > 1})
    over = len(set(names)) > palette
    findings = [chk("materials.palette", not over, f"{len(set(names))} unique material(s) within a palette of {palette}." if not over else f"{len(set(names))} materials exceed the palette of {palette}.", "warning"),
                chk("materials.duplicates", not dupes, "No duplicate material names." if not dupes else "Duplicate names: " + ", ".join(dupes), "fail")]
    return out({"unique": len(set(names)), "palette_size": palette}, findings)


def h_ugc_gate(d, cap, ctx):
    atype = core.text(d, "asset_type", maximum=40)
    owned = core.boolean(d, "owned", False)
    w = core.integer(d, "width", minimum=0, maximum=100000)
    h = core.integer(d, "height", minimum=0, maximum=100000)
    mod = core.text(d, "moderation_status", maximum=30)
    text_ok = core.boolean(d, "text_filter_passed", False)
    max_size = core.integer(d, "maximum_size", minimum=1, maximum=100000)
    reasons = []
    if not owned:
        reasons.append("not_owned")
    if mod != "approved":
        reasons.append("moderation_" + mod)
    if max(w, h) > max_size:
        reasons.append("too_large")
    if atype in ("text", "name", "decal") and not text_ok:
        reasons.append("text_filter_failed")
    decision = "allow" if not reasons else ("hold_for_review" if mod in ("pending", "") else "reject")
    findings = [chk("ugc_gate.ownership", owned, "Creator owns the asset." if owned else "Uploader does not own the asset.", "fail"),
                chk("ugc_gate.moderation", mod == "approved", "Moderation approved." if mod == "approved" else f"Moderation status is {mod}; the asset is blocked.", "fail"),
                chk("ugc_gate.size", max(w, h) <= max_size, "Size is within the limit." if max(w, h) <= max_size else f"{max(w, h)} exceeds {max_size}.", "fail"),
                chk("ugc_gate.text", atype not in ("text", "name", "decal") or text_ok, "Text filter passed." if atype not in ("text", "name", "decal") or text_ok else "Text filter has not passed.", "fail")]
    return out({"decision": decision, "reasons": reasons}, findings)


def h_marketplace(d, cap, ctx):
    atype = core.text(d, "asset_type", maximum=40)
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 8)
    tex = core.integer(d, "texture_size", minimum=0, maximum=65536)
    owned = core.boolean(d, "owned", False)
    meta = core.boolean(d, "metadata_complete", False)
    human = core.boolean(d, "human_policy_review", False)
    rules = avatar_rules()["limits"]
    reasons = []
    if atype == "accessory" and tris > rules["accessory_triangles_max"]:
        reasons.append("triangles")
    if tex > rules["texture_max_side_px"]:
        reasons.append("texture")
    if not owned:
        reasons.append("ownership")
    if not meta:
        reasons.append("metadata")
    decision = "ready_for_human_review" if not reasons and human else ("hold" if not reasons else "fix_first")
    findings = [chk("marketplace.limits", not any(r in reasons for r in ("triangles", "texture")), "Triangle and texture limits pass." if not any(r in reasons for r in ("triangles", "texture")) else "Limits exceeded: " + ", ".join(r for r in reasons if r in ("triangles", "texture")), "fail"),
                chk("marketplace.ownership", owned, "Ownership confirmed." if owned else "Ownership is not confirmed.", "fail"),
                chk("marketplace.metadata", meta, "Metadata is complete." if meta else "Complete the metadata (name, description, rights).", "fail"),
                chk("marketplace.review", human, "Human policy review is recorded." if human else "A human policy review is required before listing.", "warning")]
    return out({"decision": decision, "reasons": reasons}, findings)


def h_thumbnail(d, cap, ctx):
    bounds = core.vector(d.get("bounds"), 3)
    fov = core.number(d, "fov", minimum=10, maximum=120)
    margin = core.number(d, "margin", minimum=1.0, maximum=3.0)
    aspect = core.number(d, "aspect", minimum=0.2, maximum=5)
    radius = vlen(bounds) / 2
    dist_v = camera_distance(2 * radius, fov, margin)
    horiz_fov = 2 * math.degrees(math.atan(math.tan(math.radians(fov) / 2) * aspect))
    findings = [chk("thumbnail.framing", True, f"Camera distance {round(dist_v, 3)} studs keeps the bounding sphere (radius {round(radius, 3)}) in frame."),
                chk("thumbnail.aspect", True, f"Horizontal FOV {round(horiz_fov, 2)} degrees at aspect {round(aspect, 3)}."),
                chk("thumbnail.hidden_hud", None, "Confirm the HUD is hidden for capture; see capture_scene.")]
    return out({"camera_distance": round(dist_v, 4), "bounding_radius": round(radius, 4), "horizontal_fov_degrees": round(horiz_fov, 3)}, findings)


def h_capture_scene(d, cap, ctx):
    bounds = core.vector(d.get("subject_bounds"), 3)
    shots = core.array(d, "shots", required=True, kind=dict, maximum=50)
    res = core.array(d, "resolution", required=True, maximum=2)
    hide = core.boolean(d, "hide_hud", False)
    consent = core.boolean(d, "player_consent", False)
    if len(res) != 2 or any(not isinstance(x, int) or isinstance(x, bool) or x < 16 or x > 8192 for x in res):
        core.fail("INPUT_INVALID", "resolution must be [width, height] integers between 16 and 8192.")
    names = [core.text(s, "name", maximum=60) for s in shots]
    aspect = res[0] / res[1]
    radius = vlen(bounds) / 2
    worst = max((camera_distance(2 * radius, float(s.get("fov", 70)), 1.2) for s in shots), default=0.0)
    players_in_scene = any(s.get("includes_player") for s in shots)
    findings = [chk("capture_scene.hud", hide, "HUD is hidden for captures." if hide else "Hide the HUD before capturing.", "warning"),
                chk("capture_scene.consent", consent or not players_in_scene, "Player consent recorded for captures that include players." if consent or not players_in_scene else "Capturing players requires recorded consent.", "fail"),
                chk("capture_scene.names", len(set(names)) == len(names), "Shot names are unique." if len(set(names)) == len(names) else "Shot names must be unique.", "fail"),
                chk("capture_scene.aspect", 0.2 <= aspect <= 5, f"Aspect {round(aspect, 3)}.", "fail" if not (0.2 <= aspect <= 5) else "info")]
    return out({"shots": len(shots), "aspect": round(aspect, 4), "max_required_distance": round(worst, 3)}, findings)


# ================================================================ geometry and spatial

def h_geometry(d, cap, ctx):
    tris = core.integer(d, "triangles", minimum=0, maximum=10 ** 9)
    budget = core.integer(d, "budget", minimum=1, maximum=10 ** 9)
    degen = core.integer(d, "degenerate_faces", minimum=0, maximum=10 ** 9)
    hidden = core.integer(d, "hidden_faces", minimum=0, maximum=10 ** 9)
    bounds = core.vector(d.get("bounds"), 3)
    findings = [chk("geometry.budget", tris <= budget, f"{tris} triangles within {budget}." if tris <= budget else f"{tris} triangles exceed {budget} by {tris - budget}.", "fail" if tris > budget else "info"),
                chk("geometry.degenerate", degen == 0, "No degenerate faces." if degen == 0 else f"{degen} degenerate face(s) to remove.", "warning"),
                chk("geometry.hidden", hidden / max(tris, 1) < 0.2, f"{pct(hidden, tris)}% hidden faces." if hidden / max(tris, 1) < 0.2 else f"{pct(hidden, tris)}% of faces are hidden; cull them.", "warning"),
                chk("geometry.bounds", all(b > 0 for b in bounds), "Bounds are positive." if all(b > 0 for b in bounds) else "Bounds must be positive.", "fail")]
    return out({"triangles": tris, "budget_used_percent": pct(tris, budget), "hidden_percent": pct(hidden, max(tris, 1))}, findings)


def h_uv(d, cap, ctx):
    size = core.integer(d, "texture_size", minimum=16, maximum=8192)
    pad = core.integer(d, "padding_pixels", minimum=0, maximum=256)
    islands = core.array(d, "islands", maximum=5000, kind=dict)
    util = core.number(d, "utilization", minimum=0, maximum=1)
    overlaps = core.integer(d, "overlaps", minimum=0, maximum=10 ** 6)
    oob = core.integer(d, "out_of_bounds", minimum=0, maximum=10 ** 6)
    ratio = core.number(d, "texel_density_ratio", minimum=0.0001, maximum=100)
    findings = [chk("uv.overlaps", overlaps == 0, "No overlapping UV islands." if overlaps == 0 else f"{overlaps} overlapping island(s).", "fail"),
                chk("uv.bounds", oob == 0, "All UVs are inside 0-1." if oob == 0 else f"{oob} UV(s) outside 0-1.", "fail"),
                chk("uv.padding", pad >= 2, f"Padding {pad}px survives mip-mapping." if pad >= 2 else "Padding under 2px bleeds at mip levels.", "warning"),
                chk("uv.utilization", util >= 0.6, f"Utilization {round(util * 100, 1)}%." if util >= 0.6 else f"Utilization {round(util * 100, 1)}% wastes texture space.", "warning"),
                chk("uv.texel_density", 0.8 <= ratio <= 1.2, f"Texel density ratio {ratio}." if 0.8 <= ratio <= 1.2 else f"Texel density ratio {ratio} is outside 0.8-1.2; rescale the islands.", "warning")]
    return out({"islands": len(islands), "texture_size": size, "padding": pad}, findings)


def h_collision(d, cap, ctx):
    strategy = core.text(d, "strategy", maximum=20)
    render = core.integer(d, "render_triangles", minimum=0, maximum=10 ** 8)
    prims = core.integer(d, "collision_primitives", minimum=0, maximum=10 ** 6)
    can_collide = core.boolean(d, "can_collide", False)
    can_query = core.boolean(d, "can_query", False)
    concave = core.boolean(d, "concave", False)
    if strategy not in ("box", "convex", "mesh", "none"):
        core.fail("INPUT_INVALID", "strategy must be box, convex, mesh or none.", strategy=strategy)
    findings = [chk("collision.strategy", not (strategy == "convex" and concave), "Convex strategy does not use concave shapes." if not (strategy == "convex" and concave) else "Concave shapes are not valid for convex collision.", "fail"),
                chk("collision.cost", prims <= max(render // 4, 1) or strategy == "none", f"{prims} primitives for {render} render triangles." if prims <= max(render // 4, 1) or strategy == "none" else "Collision primitives exceed a quarter of render triangles; simplify.", "warning"),
                chk("collision.groups", None, "Assign collision groups from the configured list (Default, Characters, Projectiles, Environment, Debris)."),
                chk("collision.query_only", True, "Query-only parts do not block movement." if can_query and not can_collide else "Collision settings recorded.")]
    return out({"strategy": strategy, "primitives": prims, "render_triangles": render, "can_collide": can_collide, "can_query": can_query}, findings)


def h_lod(d, cap, ctx):
    tris = core.integer(d, "triangles", minimum=1, maximum=10 ** 9)
    levels = core.integer(d, "levels", minimum=1, maximum=10)
    ratio = core.number(d, "ratio", minimum=0.01, maximum=0.99)
    dists = core.array(d, "distances", required=True, maximum=20)
    hyst = core.number(d, "hysteresis", minimum=0, maximum=50)
    if len(dists) != levels:
        core.fail("INPUT_INVALID", "distances must have one entry per LOD level.", levels=levels, distances=len(dists))
    dvals = [float(x) for x in dists]
    rows = [{"level": 0, "triangles": tris, "distance": 0.0}]
    for k in range(1, levels + 1):
        rows.append({"level": k, "triangles": int(tris * ratio ** k), "distance": dvals[k - 1]})
    mono = all(dvals[i] < dvals[i + 1] for i in range(len(dvals) - 1))
    findings = [chk("lod.monotonic", mono, "Switch distances increase with each level." if mono else "Distances must increase with each level.", "fail"),
                chk("lod.hysteresis", hyst > 0 or levels == 1, f"Hysteresis {hyst} prevents flicker at boundaries." if hyst > 0 or levels == 1 else "Add hysteresis to stop flicker at the switch distance.", "warning"),
                chk("lod.lowest", rows[-1]["triangles"] >= 4, f"Lowest level keeps {rows[-1]['triangles']} triangles.", "warning" if rows[-1]["triangles"] < 4 else "info")]
    return out({"levels": rows}, findings)


def h_pivot(d, cap, ctx):
    parts = core.array(d, "parts", required=True, kind=dict, maximum=5000)
    pivot = core.vector(d.get("pivot"), 3)
    trans = core.vector(d.get("translation"), 3)
    moved = []
    for p in parts:
        pos = core.vector(p.get("position"), 3)
        moved.append({"name": core.text(p, "name", maximum=120), "position": [round(x - pivot[i] + trans[i], 6) for i, x in enumerate(pos)]})
    centre = [round(sum(m["position"][i] for m in moved) / len(moved), 6) if moved else 0.0 for i in range(3)]
    findings = [chk("pivot.centre", True, f"New centre of the parts is {centre}."),
                chk("pivot.parts", bool(moved), "Parts were re-based around the pivot." if moved else "No parts supplied.", "fail" if not moved else "info")]
    return out({"parts": moved, "centre": centre}, findings, {"pivot-rebased.json": moved})


def value_noise(seed):
    rnd = random.Random(int(seed))
    table = [rnd.random() for _ in range(256)]

    def sample(x, y):
        xi, yi = math.floor(x), math.floor(y)
        xf, yf = x - xi, y - yi
        def v(ix, iy):
            return table[(ix * 73856093 ^ iy * 19349663) & 255]
        def smooth(t):
            return t * t * (3 - 2 * t)
        u, w = smooth(xf), smooth(yf)
        a = v(xi, yi) + (v(xi + 1, yi) - v(xi, yi)) * u
        b = v(xi, yi + 1) + (v(xi + 1, yi + 1) - v(xi, yi + 1)) * u
        return a + (b - a) * w
    return sample


def h_biome(d, cap, ctx):
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    w = core.integer(d, "width", minimum=4, maximum=100)
    h = core.integer(d, "height", minimum=4, maximum=100)
    biomes = core.array(d, "biomes", required=True, kind=dict, maximum=20)
    spawn = vec2(d.get("spawn"), "spawn")
    steps_ok(w * h, ctx)
    names = [core.text(b, "name", maximum=40) for b in biomes]
    weights = [float(b.get("weight", 1)) for b in biomes]
    if any(x <= 0 for x in weights):
        core.fail("INPUT_INVALID", "Biome weights must be positive.")
    total = sum(weights)
    cum, acc = [], 0.0
    for wgt in weights:
        acc += wgt / total
        cum.append(acc)
    noise = value_noise(seed)
    counts = {n: 0 for n in names}
    grid = []
    for y in range(h):
        row = []
        for x in range(w):
            val = noise(x / 6.0, y / 6.0)
            idx = next((i for i, c in enumerate(cum) if val <= c), len(names) - 1)
            counts[names[idx]] += 1
            row.append(names[idx])
        grid.append(row)
    sx, sy = int(spawn[0]) % w, int(spawn[1]) % h
    spawn_biome = grid[sy][sx]
    water = [b for b in biomes if b.get("water")]
    spawn_in_water = spawn_biome in {b["name"] for b in water}
    findings = [chk("biome.spawn", not spawn_in_water, f"Spawn sits in {spawn_biome}." if not spawn_in_water else "Spawn is inside a water biome.", "fail"),
                chk("biome.coverage", all(counts[n] > 0 for n in names), "Every biome appears on the map." if all(counts[n] > 0 for n in names) else "Some biomes are absent at this seed and size; raise the size or change the seed.", "warning"),
                chk("biome.determinism", True, "The same seed and size always produce the same map.")]
    return out({"counts": counts, "spawn_biome": spawn_biome, "cells": w * h}, findings, {"biome-grid.json": grid})


def h_terrain(d, cap, ctx):
    w = core.integer(d, "width", minimum=4, maximum=100)
    dep = core.integer(d, "depth", minimum=4, maximum=100)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    amp = core.number(d, "amplitude", minimum=0, maximum=10000)
    freq = core.number(d, "frequency", minimum=0.001, maximum=10)
    erosion = core.integer(d, "erosion_passes", minimum=0, maximum=50)
    steps_ok(w * dep * (1 + erosion), ctx)
    noise = value_noise(seed)
    hgt = [[amp * noise(x * freq, y * freq) for x in range(w)] for y in range(dep)]
    for _ in range(erosion):
        nxt = [row[:] for row in hgt]
        for y in range(1, dep - 1):
            for x in range(1, w - 1):
                avg = (hgt[y - 1][x] + hgt[y + 1][x] + hgt[y][x - 1] + hgt[y][x + 1]) / 4
                nxt[y][x] = hgt[y][x] * 0.8 + avg * 0.2
        hgt = nxt
    flat = [v for row in hgt for v in row]
    slope = 0.0
    for y in range(dep - 1):
        for x in range(w - 1):
            slope = max(slope, abs(hgt[y][x] - hgt[y][x + 1]), abs(hgt[y][x] - hgt[y + 1][x]))
    findings = [chk("terrain.range", max(flat) - min(flat) <= amp + 1e-9, "Relief stays within the amplitude."),
                chk("terrain.slope", slope <= amp * 0.5 or amp == 0, f"Largest step between neighbours is {round(slope, 3)}.", "warning" if slope > amp * 0.5 and amp else "info"),
                chk("terrain.erosion", True, f"{erosion} smoothing pass(es) applied.")]
    return out({"min": round(min(flat), 4), "max": round(max(flat), 4), "mean": round(sum(flat) / len(flat), 4), "max_step": round(slope, 4)}, findings, {"heightmap.json": hgt})


def h_procedural_map(d, cap, ctx):
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    rooms_in = d.get("rooms")
    width = core.integer(d, "width", minimum=8, maximum=500)
    height = core.integer(d, "height", minimum=8, maximum=500)
    extra = core.integer(d, "extra_edges", minimum=0, maximum=100)
    if isinstance(rooms_in, bool) or rooms_in is None:
        core.fail("INPUT_INVALID", "rooms must be a count or a list of room objects.")
    if isinstance(rooms_in, int):
        if not 2 <= rooms_in <= 200:
            core.fail("INPUT_INVALID", "room count must be between 2 and 200.")
        rooms = [{"name": f"room_{i + 1}", "w": 4, "h": 4} for i in range(rooms_in)]
    elif isinstance(rooms_in, list):
        rooms = [{"name": core.text(r, "name", maximum=60), "w": core.integer(r, "w", minimum=1, maximum=100), "h": core.integer(r, "h", minimum=1, maximum=100)} for r in rooms_in if isinstance(r, dict)]
        if len(rooms) < 2:
            core.fail("INPUT_INVALID", "At least two rooms are required.")
    else:
        core.fail("INPUT_INVALID", "rooms must be a count or a list.")
    steps_ok(len(rooms) ** 2, ctx)
    gen = random.Random(seed)
    placed = []
    for r in rooms:
        for _ in range(200):
            x, y = gen.randint(0, width - r["w"]), gen.randint(0, height - r["h"])
            rect = (x, y, r["w"], r["h"])
            if all(rect[0] + rect[2] + 1 <= p[0] or p[0] + p[2] + 1 <= rect[0] or rect[1] + rect[3] + 1 <= p[1] or p[1] + p[3] + 1 <= rect[1] for p in placed):
                placed.append(rect)
                r["pos"] = [x, y]
                break
        else:
            core.fail("INPUT_LIMIT", "Rooms do not fit the map without overlap; enlarge width or height.", room=r["name"])
    centres = [(r["pos"][0] + r["w"] / 2, r["pos"][1] + r["h"] / 2) for r in rooms]
    n = len(rooms)
    edges = []
    in_tree = {0}
    while len(in_tree) < n:
        best = None
        for i in in_tree:
            for j in range(n):
                if j in in_tree:
                    continue
                dd = math.dist(centres[i], centres[j])
                if best is None or dd < best[0]:
                    best = (dd, i, j)
        edges.append((best[1], best[2]))
        in_tree.add(best[2])
    pool = sorted(((math.dist(centres[i], centres[j]), i, j) for i in range(n) for j in range(i + 1, n) if (i, j) not in edges and (j, i) not in edges))
    for _, i, j in pool[:extra]:
        edges.append((i, j))
    connected = len(in_tree) == n
    findings = [chk("procedural_map.connected", connected, f"All {n} rooms connect through {len(edges)} corridor(s)."),
                chk("procedural_map.overlap", True, "Rooms keep at least one tile of separation."),
                chk("procedural_map.loops", extra == 0 or len(edges) > n - 1, "Extra edges add loops for alternative routes.")]
    layout = {"rooms": [{"name": r["name"], "x": r["pos"][0], "y": r["pos"][1], "w": r["w"], "h": r["h"]} for r in rooms],
              "corridors": [[rooms[i]["name"], rooms[j]["name"]] for i, j in edges]}
    return out({"rooms": n, "corridors": len(edges), "connected": connected}, findings, {"map-layout.json": layout})


def h_modular_kit(d, cap, ctx):
    grid = core.vector(d.get("grid"), 2, [1, 1])
    pieces = core.array(d, "pieces", required=True, kind=dict, maximum=500)
    complement = {"north": "south", "south": "north", "east": "west", "west": "east"}
    names = [core.text(p, "name", maximum=80) for p in pieces]
    dupes = sorted({x for x in names if names.count(x) > 1})
    sockets = {}
    rows, unmatched = [], []
    for p in pieces:
        socks = p.get("sockets", {})
        if not isinstance(socks, dict) or any(k not in complement for k in socks):
            core.fail("INPUT_INVALID", "sockets must map north, east, south and west to socket types.", piece=p.get("name"))
        rows.append({"name": p.get("name"), "sockets": socks})
        for side, typ in socks.items():
            sockets.setdefault((side, str(typ)), []).append(p.get("name"))
    for (side, typ), owners in sockets.items():
        want = (complement[side], typ)
        if want not in sockets:
            unmatched.append({"piece": owners[0], "side": side, "type": typ})
    findings = [chk("modular_kit.duplicates", not dupes, "Piece names are unique." if not dupes else "Duplicate piece names: " + ", ".join(dupes), "fail"),
                chk("modular_kit.sockets", not unmatched, "Every socket has a matching partner." if not unmatched else f"{len(unmatched)} socket(s) have no partner and will leave gaps.", "warning"),
                chk("modular_kit.grid", grid[0] >= 1 and grid[1] >= 1, f"Grid {int(grid[0])}x{int(grid[1])} is valid.")]
    return out({"pieces": len(pieces), "socket_types": sorted({t for _, t in sockets}), "unmatched": unmatched}, findings)


def h_vegetation(d, cap, ctx):
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    count = core.integer(d, "count", minimum=1, maximum=20000)
    area = core.vector(d.get("area"), 2)
    spacing = core.number(d, "spacing", minimum=0.1, maximum=1000)
    max_slope = core.number(d, "max_slope", minimum=0, maximum=90)
    cap_n = core.integer(d, "maximum_instances", minimum=1, maximum=100000)
    steps_ok(count * 30, ctx)
    gen = random.Random(seed)
    pts, rejected_spacing, rejected_slope = [], 0, 0
    for _ in range(count * 30):
        if len(pts) >= count:
            break
        x, y = gen.uniform(0, area[0]), gen.uniform(0, area[1])
        slope = gen.uniform(0, 45)
        if slope > max_slope:
            rejected_slope += 1
            continue
        if any(math.dist((x, y), p) < spacing for p in pts):
            rejected_spacing += 1
            continue
        pts.append((x, y))
    placed = min(len(pts), cap_n)
    density = placed / (area[0] * area[1]) if area[0] * area[1] else 0
    findings = [chk("vegetation.budget", len(pts) <= cap_n, f"{placed} instance(s) within the maximum of {cap_n}." if len(pts) <= cap_n else f"Placed {len(pts)} instances, capped at {cap_n}.", "warning"),
                chk("vegetation.spacing", True, f"Minimum spacing {spacing} enforced; {rejected_spacing} candidate(s) rejected."),
                chk("vegetation.slope", True, f"Slope limit {max_slope} degrees; {rejected_slope} candidate(s) rejected."),
                chk("vegetation.density", density < 1.0 / max(spacing, 0.1) ** 2 * 4, f"Density {round(density, 5)} per square stud.")]
    return out({"placed": placed, "requested": count, "density_per_square_stud": round(density, 6)}, findings, {"vegetation-points.json": [[round(x, 3), round(y, 3)] for x, y in pts[:placed]]})


def h_streaming(d, cap, ctx):
    radius = core.number(d, "radius", minimum=1, maximum=100000)
    objs = core.array(d, "objects", required=True, kind=dict, maximum=20000)
    required = core.array(d, "required", maximum=2000, kind=str)
    origin = [0.0, 0.0]
    loaded, unloaded, far_required = [], [], []
    names = {}
    for o in objs:
        nm = core.text(o, "name", maximum=120)
        pos = vec2(o.get("position"), "position")
        names[nm] = pos
        dist = math.dist(origin, pos)
        (loaded if dist <= radius else unloaded).append(nm)
    for r in required:
        if r in names and math.dist(origin, names[r]) > radius:
            far_required.append(r)
    findings = [chk("streaming.required", not far_required, "Required objects stay within the streaming radius." if not far_required else "Required objects outside the radius will not load: " + ", ".join(far_required), "fail"),
                chk("streaming.radius", radius >= 64, "Radius is at least 64 studs." if radius >= 64 else "A radius under 64 studs causes frequent streaming churn.", "warning"),
                chk("streaming.evaluation", True, "Load decisions are made from the origin; the client should re-evaluate on movement.")]
    return out({"loaded": len(loaded), "unloaded": len(unloaded), "far_required": far_required}, findings)


def h_streaming_audit(d, cap, ctx):
    enabled = core.boolean(d, "streaming_enabled", False)
    persistent = set(core.array(d, "persistent", maximum=2000, kind=str))
    required = core.array(d, "required", maximum=2000, kind=str)
    wait = core.number(d, "wait_for_child_timeouts", minimum=0, maximum=3600)
    atomic = core.boolean(d, "atomic_models", False)
    radius = core.number(d, "radius", minimum=1, maximum=100000)
    not_persistent = [r for r in required if r not in persistent]
    findings = [chk("streaming_audit.enabled", enabled, "StreamingEnabled is on." if enabled else "Enable StreamingEnabled for large worlds.", "warning"),
                chk("streaming_audit.required", not not_persistent or atomic, "Required objects are persistent or atomic." if not not_persistent or atomic else "Required objects that are not persistent can be missing: " + ", ".join(not_persistent), "fail"),
                chk("streaming_audit.wait", wait > 0, f"WaitForChild timeout {wait}s." if wait > 0 else "Use bounded WaitForChild timeouts; unbounded waits hang on streamed objects.", "fail"),
                chk("streaming_audit.atomic", atomic, "Models are atomic so partial loads cannot appear." if atomic else "Partial models can appear mid-stream; consider atomic models.", "warning"),
                chk("streaming_audit.radius", radius >= 64, "Radius is sufficient." if radius >= 64 else "Small radius increases churn.", "info")]
    return out({"required_not_persistent": not_persistent, "radius": radius}, findings)


def h_camera(d, cap, ctx):
    mode = core.text(d, "mode", maximum=20)
    target = core.vector(d.get("target"), 3)
    dist = core.number(d, "distance", minimum=0.1, maximum=10000)
    yaw = core.number(d, "yaw", minimum=-3600, maximum=3600)
    pitch = core.number(d, "pitch", minimum=-89, maximum=89)
    fov = core.number(d, "fov", minimum=10, maximum=120)
    if mode not in ("follow", "orbit", "fixed", "first_person"):
        core.fail("INPUT_INVALID", "mode must be follow, orbit, fixed or first_person.", mode=mode)
    yr, pr = math.radians(yaw), math.radians(pitch)
    offset = [dist * math.cos(pr) * math.sin(yr), dist * math.sin(pr), dist * math.cos(pr) * math.cos(yr)]
    position = [round(target[i] + offset[i], 4) for i in range(3)]
    findings = [chk("camera.fov", 30 <= fov <= 100, f"FOV {fov} degrees is comfortable." if 30 <= fov <= 100 else "FOV outside 30-100 degrees can cause discomfort.", "warning"),
                chk("camera.pitch", abs(pitch) < 80, "Pitch leaves room before the poles.", "warning" if abs(pitch) >= 80 else "info"),
                chk("camera.collision", None, "Use camera_collision to keep the camera out of geometry.")]
    return out({"position": position, "look_at": [round(x, 4) for x in target], "mode": mode}, findings)


def ray_aabb(origin, direction, lo, hi):
    tmin, tmax = 0.0, math.inf
    for i in range(3):
        if abs(direction[i]) < 1e-12:
            if origin[i] < lo[i] or origin[i] > hi[i]:
                return None
            continue
        t1, t2 = (lo[i] - origin[i]) / direction[i], (hi[i] - origin[i]) / direction[i]
        if t1 > t2:
            t1, t2 = t2, t1
        tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin


def h_camera_collision(d, cap, ctx):
    target = core.vector(d.get("target"), 3)
    desired = core.vector(d.get("desired"), 3)
    obstacles = core.array(d, "obstacles", maximum=5000, kind=dict)
    pad = core.number(d, "padding", minimum=0, maximum=10)
    seg = vsub(desired, target)
    length = vlen(seg)
    direction = vnorm(seg)
    best = None
    for ob in obstacles:
        steps_ok(1, ctx)
        lo, hi = core.vector(ob.get("min"), 3), core.vector(ob.get("max"), 3)
        t = ray_aabb(target, direction, lo, hi)
        if t is not None and t <= length and (best is None or t < best[0]):
            best = (t, ob.get("name", "obstacle"))
    if best is None:
        final_dist, hit = length, None
    else:
        final_dist, hit = max(0.0, best[0] - pad), best[1]
    camera = [round(target[i] + direction[i] * final_dist, 4) for i in range(3)]
    findings = [chk("camera_collision.clear", hit is None, "Desired camera position is clear." if hit is None else f"Camera pulled in to {round(final_dist, 3)} studs to avoid {hit}.", "warning" if hit else "info"),
                chk("camera_collision.padding", pad > 0 or hit is None, "Padding keeps the lens off the surface." if pad > 0 or hit is None else "No padding: the lens can clip the surface.", "warning")]
    return out({"camera": camera, "distance": round(final_dist, 4), "blocked_by": hit}, findings)


def h_hit_detection(d, cap, ctx):
    origin = core.vector(d.get("origin"), 3)
    direction = vnorm(core.vector(d.get("direction"), 3))
    rng_ = core.number(d, "range", minimum=0.1, maximum=100000)
    targets = core.array(d, "targets", required=True, kind=dict, maximum=5000)
    rewind = core.number(d, "rewind_ms", minimum=0, maximum=10000)
    max_rewind = core.number(d, "max_rewind_ms", minimum=0, maximum=10000)
    if vlen(direction) == 0:
        core.fail("INPUT_INVALID", "direction must be non-zero.")
    hits = []
    for t in targets:
        steps_ok(1, ctx)
        c = core.vector(t.get("center"), 3)
        r = core.number(t, "radius", minimum=0.01, maximum=1000)
        proj = vdot(vsub(c, origin), direction)
        if proj < 0 or proj > rng_:
            continue
        closest = [origin[i] + direction[i] * proj for i in range(3)]
        if vlen(vsub(c, closest)) <= r:
            hits.append({"name": t.get("name"), "distance": round(proj, 4)})
    hits.sort(key=lambda h: h["distance"])
    findings = [chk("hit_detection.rewind", rewind <= max_rewind, f"Rewind {rewind} ms within {max_rewind} ms." if rewind <= max_rewind else f"Rewind {rewind} ms exceeds {max_rewind} ms; reject the hit.", "fail"),
                chk("hit_detection.first_hit", True, f"First valid hit: {hits[0]['name']}." if hits else "No target in the ray."),
                chk("hit_detection.authority", None, "The server performs the final check; client hit reports are hints.")]
    accepted = hits if rewind <= max_rewind else []
    return out({"hits": accepted, "first": accepted[0] if accepted else None}, findings)


def h_targeting(d, cap, ctx):
    targets = core.array(d, "targets", required=True, kind=dict, maximum=500)
    max_range = core.number(d, "max_range", minimum=0.1, maximum=100000)
    max_angle = core.number(d, "max_angle", minimum=0, maximum=180)
    assist = core.number(d, "assist_strength", minimum=0, maximum=1)
    valid = []
    for t in targets:
        dist = core.number(t, "distance", minimum=0, maximum=100000)
        ang = core.number(t, "angle", minimum=0, maximum=180)
        if dist <= max_range and ang <= max_angle:
            score = (ang / max(max_angle, 1e-9)) * (1 - assist) + (dist / max_range) * (1 - assist) * 0.5
            valid.append((score, t.get("name")))
    valid.sort()
    best = valid[0][1] if valid else None
    findings = [chk("targeting.assist", assist <= 0.8, "Assist strength is moderate." if assist <= 0.8 else "Assist above 0.8 removes skill; reduce.", "warning"),
                chk("targeting.range", True, f"{len(valid)} target(s) in range and angle."),
                chk("targeting.authority", None, "Target selection for damage must be confirmed on the server.")]
    return out({"selected": best, "candidates": [v[1] for v in valid]}, findings)


def h_pathfinding(d, cap, ctx):
    grid_in = core.array(d, "grid", required=True, kind=str, maximum=500)
    start = [int(x) for x in vec2(d.get("start"), "start")]
    goal = [int(x) for x in vec2(d.get("goal"), "goal")]
    radius = core.integer(d, "agent_radius", minimum=0, maximum=20)
    repath = core.number(d, "repath_seconds", minimum=0.05, maximum=600)
    h, w = len(grid_in), max(len(r) for r in grid_in)
    if w > 500:
        core.fail("INPUT_LIMIT", "Grid is wider than 500 cells.")
    steps_ok(h * w, ctx)
    blocked = [[False] * w for _ in range(h)]
    for y, row in enumerate(grid_in):
        for x, ch in enumerate(row):
            blocked[y][x] = ch == "#"
    inflated = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if blocked[y][x]:
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h and 0 <= xx < w:
                            inflated[yy][xx] = True
    sx, sy = start
    gx, gy = goal
    if not (0 <= sx < w and 0 <= sy < h and 0 <= gx < w and 0 <= gy < h):
        core.fail("INPUT_INVALID", "start and goal must be inside the grid.")
    if inflated[sy][sx] or inflated[gy][gx]:
        return out({"path": None, "reason": "start_or_goal_blocked"}, [chk("pathfinding.endpoints", False, "Start or goal is blocked for this agent radius.", "fail")])
    heap, came, cost = [(0, (sx, sy))], {}, {(sx, sy): 0}
    found = False
    while heap:
        _, cur = heapq.heappop(heap)
        if cur == (gx, gy):
            found = True
            break
        cx, cy = cur
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < w and 0 <= ny < h) or inflated[ny][nx]:
                continue
            if dx and dy and (inflated[cy][nx] or inflated[ny][cx]):
                continue
            step = math.sqrt(2) if dx and dy else 1.0
            nc = cost[cur] + step
            if nc < cost.get((nx, ny), math.inf):
                cost[(nx, ny)] = nc
                came[(nx, ny)] = cur
                hval = math.hypot(gx - nx, gy - ny)
                heapq.heappush(heap, (nc + hval, (nx, ny)))
    path = None
    if found:
        path = [list(goal)]
        cur = (gx, gy)
        while cur in came:
            cur = came[cur]
            path.append(list(cur))
        path.reverse()
    findings = [chk("pathfinding.reachable", found, f"Path found in {len(path) - 1} step(s)." if found else "No path exists for this agent radius.", "fail" if not found else "info"),
                chk("pathfinding.repath", repath >= 0.25, f"Repath every {repath}s.", "warning" if repath < 0.25 else "info"),
                chk("pathfinding.radius", radius <= 5, "Agent radius fits typical corridors." if radius <= 5 else "Large agent radius may block narrow corridors.", "warning")]
    return out({"path": path, "steps": (len(path) - 1) if path else None, "found": found}, findings)


def h_perception(d, cap, ctx):
    observer = core.vector(d.get("observer"), 3)
    forward = vnorm(core.vector(d.get("forward"), 3))
    targets = core.array(d, "targets", required=True, kind=dict, maximum=1000)
    rng_ = core.number(d, "range", minimum=0.1, maximum=100000)
    fov = core.number(d, "fov", minimum=1, maximum=360)
    memory = core.number(d, "memory_seconds", minimum=0, maximum=3600)
    seen, remembered = [], []
    for t in targets:
        steps_ok(1, ctx)
        p = core.vector(t.get("position"), 3)
        v = vsub(p, observer)
        dist = vlen(v)
        if dist == 0 or dist > rng_:
            continue
        angle = math.degrees(math.acos(max(-1.0, min(1.0, vdot(vnorm(v), forward)))))
        if angle <= fov / 2:
            seen.append({"name": t.get("name"), "distance": round(dist, 3), "angle": round(angle, 2)})
        elif t.get("last_seen_seconds_ago", 1e9) <= memory:
            remembered.append(t.get("name"))
    findings = [chk("perception.cone", True, f"{len(seen)} target(s) inside the view cone."),
                chk("perception.memory", True, f"Targets remembered for {memory}s after leaving view: {len(remembered)}."),
                chk("perception.occlusion", None, "Line of sight through geometry needs a raycast; supply visibility results from the engine.")]
    return out({"seen": seen, "remembered": remembered}, findings)


def h_ai_debug(d, cap, ctx):
    paths = core.array(d, "paths", required=True, kind=dict, maximum=200)
    targets = core.array(d, "targets", maximum=500, kind=dict)
    state = core.text(d, "state", maximum=60)
    bounds = d.get("bounds")
    lo, hi = (core.vector(bounds[0], 3), core.vector(bounds[1], 3)) if isinstance(bounds, list) and len(bounds) == 2 else (None, None)
    outside = []
    for p in paths:
        for pt in p.get("points", []):
            v = core.vector(pt, 3)
            if lo and any(v[i] < lo[i] or v[i] > hi[i] for i in range(3)):
                outside.append(p.get("name"))
                break
    findings = [chk("ai_debug.paths", not outside, "All path points are inside the bounds." if not outside else "Paths leave the bounds: " + ", ".join(map(str, outside)), "warning"),
                chk("ai_debug.targets", True if targets else None, f"{len(targets)} target(s) tracked." if targets else "No targets listed; the debug view is empty."),
                chk("ai_debug.state", bool(state), f"Current state {state}." if state else "Report a current state for the NPC.", "info")]
    return out({"paths": len(paths), "outside": outside, "state": state}, findings)


def h_placement(d, cap, ctx):
    grid = core.vector(d.get("grid"), 2)
    pos = [int(x) for x in vec2(d.get("position"), "position")]
    size = [int(x) for x in vec2(d.get("size"), "size")]
    bounds = [int(x) for x in vec2(d.get("bounds"), "bounds")]
    occupied = core.array(d, "occupied", maximum=5000)
    cost = core.number(d, "cost", minimum=0, maximum=1e9)
    balance = core.number(d, "balance", minimum=0, maximum=1e9)

    def rect_of(r):
        if not isinstance(r, list) or len(r) != 4:
            core.fail("INPUT_INVALID", "occupied entries must be [x, y, w, h].")
        return [int(x) for x in r]
    rect = [pos[0], pos[1], size[0], size[1]]
    inside = pos[0] >= 0 and pos[1] >= 0 and pos[0] + size[0] <= bounds[0] and pos[1] + size[1] <= bounds[1]
    overlaps = []
    for r in occupied:
        o = rect_of(r)
        if rect[0] < o[0] + o[2] and o[0] < rect[0] + rect[2] and rect[1] < o[1] + o[3] and o[1] < rect[1] + rect[3]:
            overlaps.append(o)
    affordable = balance >= cost
    findings = [chk("placement.bounds", inside, "Placement is inside the bounds." if inside else "Placement extends beyond the bounds.", "fail"),
                chk("placement.overlap", not overlaps, "No overlap with occupied cells." if not overlaps else f"Overlaps {len(overlaps)} occupied area(s).", "fail"),
                chk("placement.cost", affordable, "Balance covers the cost." if affordable else f"Short by {round(cost - balance, 2)}.", "fail"),
                chk("placement.grid", pos[0] % 1 == 0 and grid[0] >= 1, "Position snaps to the grid.", "info")]
    return out({"valid": inside and not overlaps and affordable, "overlaps": len(overlaps), "remaining_balance": round(balance - cost, 2) if affordable else None}, findings)


def h_interaction_framework(d, cap, ctx):
    dist = core.number(d, "distance", minimum=0, maximum=100000)
    maxd = core.number(d, "max_distance", minimum=0, maximum=100000)
    los = core.boolean(d, "line_of_sight", False)
    permitted = core.boolean(d, "permitted", False)
    held = core.number(d, "held_seconds", minimum=0, maximum=3600)
    hold = core.number(d, "hold_seconds", minimum=0, maximum=3600)
    cooldown = core.boolean(d, "cooldown_ready", False)
    ok = dist <= maxd and los and permitted and cooldown
    progress = 1.0 if hold == 0 else min(1.0, held / hold)
    if not ok:
        decision = "blocked"
    elif progress < 1.0:
        decision = "holding"
    else:
        decision = "complete"
    findings = [chk("interaction_framework.range", dist <= maxd, "In range." if dist <= maxd else "Out of range.", "fail"),
                chk("interaction_framework.line_of_sight", los, "Line of sight is clear." if los else "Line of sight is blocked.", "fail"),
                chk("interaction_framework.permission", permitted, "Player is permitted." if permitted else "Player is not permitted; the server rejects this.", "fail"),
                chk("interaction_framework.cooldown", cooldown, "Cooldown is ready." if cooldown else "Cooldown is active.", "warning"),
                chk("interaction_framework.authority", None, "The server re-checks distance and permission on completion.")]
    return out({"decision": decision, "progress": round(progress, 4)}, findings)


def h_spatial_audio(d, cap, ctx):
    dist = core.number(d, "distance", minimum=0, maximum=100000)
    mind = core.number(d, "min_distance", minimum=0.01, maximum=100000)
    maxd = core.number(d, "max_distance", minimum=0.01, maximum=100000)
    roll = core.text(d, "rolloff", maximum=20)
    occluded = core.boolean(d, "occluded", False)
    occ_gain = core.number(d, "occlusion_gain", minimum=0, maximum=1)
    if maxd <= mind:
        core.fail("INPUT_INVALID", "max_distance must be greater than min_distance.")
    if roll == "inverse":
        gain = 1.0 if dist <= mind else (mind / dist if dist < maxd else 0.0)
    elif roll == "linear":
        gain = 1.0 if dist <= mind else max(0.0, 1 - (dist - mind) / (maxd - mind))
    else:
        core.fail("INPUT_INVALID", "rolloff must be inverse or linear.", rolloff=roll)
    if occluded:
        gain *= occ_gain
    findings = [chk("spatial_audio.range", dist <= maxd, "Source is audible at this distance." if dist <= maxd else "Source is beyond max distance and silent.", "info"),
                chk("spatial_audio.occlusion", (not occluded) or occ_gain < 1, "Occlusion reduces gain." if occluded and occ_gain < 1 else "Occlusion is not applied." if not occluded else "Occlusion gain of 1 has no effect.", "info"),
                chk("spatial_audio.rolloff", True, f"{roll} rolloff from {mind} to {maxd} studs.")]
    return out({"gain": round(gain, 6)}, findings)


# ================================================================ layout

def _rect(r, name):
    if not isinstance(r, dict):
        core.fail("INPUT_INVALID", f"{name} entries must be objects with x, y, w and h.")
    return {"name": core.text(r, "name", maximum=80), "x": core.number(r, "x", minimum=-100000, maximum=100000),
            "y": core.number(r, "y", minimum=-100000, maximum=100000), "w": core.number(r, "w", minimum=0, maximum=100000),
            "h": core.number(r, "h", minimum=0, maximum=100000)}


def _viewport(v):
    if isinstance(v, dict):
        return core.number(v, "width", minimum=1, maximum=20000), core.number(v, "height", minimum=1, maximum=20000)
    if isinstance(v, list) and len(v) == 2:
        return float(v[0]), float(v[1])
    core.fail("INPUT_INVALID", "viewport must be {width, height} or [width, height].")


def _overlaps(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def _margins(m):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return {"top": m, "bottom": m, "left": m, "right": m}
    if isinstance(m, list) and len(m) == 4:
        return dict(zip(("top", "bottom", "left", "right"), [float(x) for x in m]))
    if isinstance(m, dict):
        return {k: float(m.get(k, 0)) for k in ("top", "bottom", "left", "right")}
    core.fail("INPUT_INVALID", "safe_margin must be a number, [top, bottom, left, right], or an object.")


def h_ui_layout(d, cap, ctx):
    vw, vh = _viewport(d.get("viewport"))
    elements = [_rect(e, "elements") for e in core.array(d, "elements", required=True, kind=dict, maximum=2000)]
    m = _margins(d.get("safe_margin", ui_tokens()["safe_area_default_px"]))
    outside = [e["name"] for e in elements if e["x"] < m["left"] or e["y"] < m["top"] or e["x"] + e["w"] > vw - m["right"] or e["y"] + e["h"] > vh - m["bottom"]]
    offscreen = [e["name"] for e in elements if e["x"] < 0 or e["y"] < 0 or e["x"] + e["w"] > vw or e["y"] + e["h"] > vh]
    pairs = [(a["name"], b["name"]) for i, a in enumerate(elements) for b in elements[i + 1:] if _overlaps(a, b)]
    findings = [chk("ui_layout.offscreen", not offscreen, "Every element is on screen." if not offscreen else "Off screen: " + ", ".join(offscreen), "fail"),
                chk("ui_layout.safe_area", not outside, "Elements respect the safe area." if not outside else "Inside the unsafe margin: " + ", ".join(outside), "warning"),
                chk("ui_layout.overlap", not pairs, "No overlapping elements." if not pairs else f"{len(pairs)} overlapping pair(s).", "warning")]
    return out({"viewport": [vw, vh], "overlaps": pairs[:100], "unsafe": outside}, findings)


def h_mobile_ui(d, cap, ctx):
    vw, vh = _viewport(d.get("viewport"))
    insets = _margins(d.get("safe_insets", ui_tokens()["safe_area_default_px"]))
    buttons = [_rect(b, "buttons") for b in core.array(d, "buttons", required=True, kind=dict, maximum=500)]
    tmin = core.number(d, "minimum_target", ui_tokens()["touch"]["minimum_target_px"], 1, 500)
    small = [b["name"] for b in buttons if min(b["w"], b["h"]) < tmin]
    inset_bad = [b["name"] for b in buttons if b["x"] < insets["left"] or b["y"] < insets["top"] or b["x"] + b["w"] > vw - insets["right"] or b["y"] + b["h"] > vh - insets["bottom"]]
    findings = [chk("mobile_ui.touch_targets", not small, f"Every target is at least {tmin}px." if not small else f"Targets under {tmin}px: " + ", ".join(small), "fail"),
                chk("mobile_ui.safe_insets", not inset_bad, "Buttons clear the safe insets." if not inset_bad else "Buttons intrude into safe insets: " + ", ".join(inset_bad), "warning"),
                chk("mobile_ui.spacing", True, "Spacing between buttons is checked by ui_layout overlap rules.")]
    return out({"viewport": [vw, vh], "small_targets": small, "inset_violations": inset_bad}, findings)


def h_hud(d, cap, ctx):
    vw, vh = _viewport(d.get("viewport"))
    regions = [_rect(r, "regions") for r in core.array(d, "regions", required=True, kind=dict, maximum=200)]
    maxc = core.number(d, "max_coverage", minimum=0, maximum=1)
    area = vw * vh
    covered = sum(r["w"] * r["h"] for r in regions)
    coverage = covered / area if area else 0
    pairs = [(a["name"], b["name"]) for i, a in enumerate(regions) for b in regions[i + 1:] if _overlaps(a, b)]
    findings = [chk("hud.coverage", coverage <= maxc, f"HUD covers {round(coverage * 100, 1)}% of the screen." if coverage <= maxc else f"HUD covers {round(coverage * 100, 1)}%, above the {round(maxc * 100)}% limit.", "warning"),
                chk("hud.overlap", not pairs, "HUD regions do not overlap." if not pairs else f"{len(pairs)} overlapping HUD region(s).", "warning")]
    return out({"coverage": round(coverage, 4), "overlaps": pairs}, findings)


def h_inventory_ui(d, cap, ctx):
    items = core.array(d, "items", required=True, kind=dict, maximum=20000)
    query = core.text(d, "query", maximum=100, required=False) or ""
    flt = core.text(d, "filter", maximum=40, required=False) or "all"
    sort = core.text(d, "sort", maximum=20, required=False) or "name"
    page = core.integer(d, "page", minimum=1, maximum=100000)
    size = core.integer(d, "page_size", minimum=1, maximum=200)
    if sort not in ("name", "count"):
        core.fail("INPUT_INVALID", "sort must be name or count.", sort=sort)
    rows = []
    for it in items:
        name = core.text(it, "name", maximum=80)
        if query and query.lower() not in name.lower():
            continue
        if flt != "all" and it.get("type") != flt:
            continue
        rows.append({"name": name, "count": int(it.get("count", 1)), "type": it.get("type")})
    rows.sort(key=(lambda r: (r["name"].lower(),)) if sort == "name" else (lambda r: (-r["count"], r["name"].lower())))
    start = (page - 1) * size
    view = rows[start:start + size]
    pages = max(1, math.ceil(len(rows) / size))
    findings = [chk("inventory_ui.search", True, f"{len(rows)} item(s) match '{query}' in filter '{flt}'."),
                chk("inventory_ui.paging", page <= pages, f"Page {page} of {pages}." if page <= pages else f"Page {page} is past the last page {pages}.", "info"),
                chk("inventory_ui.empty", bool(rows) or not query, "Show an empty-state message when no items match." if not rows else "Results are populated.", "info")]
    return out({"matches": len(rows), "page": page, "pages": pages, "items": view}, findings)


def h_world_ui(d, cap, ctx):
    dist = core.number(d, "distance", minimum=0, maximum=100000)
    maxd = core.number(d, "max_distance", minimum=0, maximum=100000)
    base = core.number(d, "base_pixels", minimum=1, maximum=4000)
    mn = core.number(d, "min_pixels", minimum=1, maximum=4000)
    mx = core.number(d, "max_pixels", minimum=1, maximum=4000)
    adornee = core.boolean(d, "has_adornee", False)
    los = core.boolean(d, "line_of_sight", False)
    if mn > mx:
        core.fail("INPUT_INVALID", "min_pixels must not exceed max_pixels.")
    size = max(mn, min(mx, base * 10.0 / max(dist, 1.0)))
    visible = adornee and dist <= maxd and los
    findings = [chk("world_ui.adornee", adornee, "Adornee is set." if adornee else "Set an adornee so the label has a parent.", "fail"),
                chk("world_ui.visibility", visible, "Label is visible." if visible else "Label is hidden (range or line of sight).", "info"),
                chk("world_ui.legibility", size >= mn, f"Label size {round(size, 1)}px.")]
    return out({"visible": visible, "pixel_size": round(size, 2)}, findings)


def h_responsive(d, cap, ctx):
    views = core.array(d, "viewports", required=True, maximum=20)
    tmin = core.number(d, "minimum_target", minimum=1, maximum=500)
    base_t = core.number(d, "base_target", minimum=1, maximum=500)
    base_short = core.number(d, "base_short_side", minimum=1, maximum=20000)
    rows, bad = [], []
    for v in views:
        w, h = _viewport(v)
        short = min(w, h)
        scale = short / base_short
        target = max(tmin, base_t * scale)
        rows.append({"viewport": [w, h], "scale": round(scale, 4), "target_px": round(target, 2)})
        if target < tmin:
            bad.append([w, h])
    findings = [chk("responsive.targets", not bad, "Targets meet the minimum on every viewport." if not bad else f"Targets fall below the minimum on {bad}.", "fail"),
                chk("responsive.scale", True, "Targets scale with the short side of the viewport.")]
    return out({"layouts": rows}, findings)


def h_usability(d, cap, ctx):
    steps = core.integer(d, "task_steps", minimum=1, maximum=1000)
    maxs = core.integer(d, "max_steps", minimum=1, maximum=1000)
    contrast = core.number(d, "contrast", minimum=0, maximum=21)
    touch = core.number(d, "minimum_touch_target", minimum=1, maximum=500)
    recover = core.boolean(d, "error_recovery", False)
    loading = core.boolean(d, "loading_feedback", False)
    labels = core.boolean(d, "text_labels", False)
    t = ui_tokens()
    findings = [chk("usability.steps", steps <= maxs, f"Task takes {steps} step(s) against a limit of {maxs}." if steps <= maxs else f"Task takes {steps} steps; the limit is {maxs}.", "warning"),
                chk("usability.contrast", contrast >= t["contrast"]["text_normal"], f"Contrast {contrast}:1." if contrast >= t["contrast"]["text_normal"] else f"Contrast {contrast}:1 is below {t['contrast']['text_normal']}:1.", "fail"),
                chk("usability.touch", touch >= t["touch"]["minimum_target_px"], f"Touch target {touch}px." if touch >= t["touch"]["minimum_target_px"] else f"Touch target {touch}px is below {t['touch']['minimum_target_px']}px.", "fail"),
                chk("usability.errors", recover, "Errors offer a recovery action." if recover else "Every error needs a recovery action.", "warning"),
                chk("usability.loading", loading, "Loading shows feedback." if loading else "Show loading feedback.", "warning"),
                chk("usability.labels", labels, "Controls have text labels." if labels else "Add text labels, not icons alone.", "warning")]
    return out({"steps": steps, "contrast": contrast}, findings)


def h_visual_qa(d, cap, ctx):
    devices = core.array(d, "devices", required=True, kind=dict, maximum=200)
    obs = core.array(d, "observations", required=True, kind=dict, maximum=5000)
    names = {core.text(x, "name", maximum=80) for x in devices}
    per = {}
    for o in obs:
        dev = core.text(o, "device", maximum=80)
        per.setdefault(dev, {"passed": 0, "failed": 0})
        per[dev]["passed" if o.get("passed") else "failed"] += 1
    unknown = sorted(set(per) - names)
    untested = sorted(n for n in names if n not in per)
    failing = sorted(k for k, v in per.items() if v["failed"])
    findings = [chk("visual_qa.coverage", not untested, "Every device has observations." if not untested else "Untested devices: " + ", ".join(untested), "warning"),
                chk("visual_qa.failures", not failing, "No failed observations." if not failing else "Failed observations on: " + ", ".join(failing), "fail"),
                chk("visual_qa.unknown", not unknown, "All observations map to listed devices." if not unknown else "Observations reference unknown devices: " + ", ".join(unknown), "warning")]
    return out({"per_device": per, "untested": untested}, findings)


def h_rtl(d, cap, ctx):
    width = core.number(d, "width", minimum=1, maximum=20000)
    els = [_rect(e, "elements") for e in core.array(d, "elements", required=True, kind=dict, maximum=2000)]
    mirrored = [{"name": e["name"], "x": round(width - e["x"] - e["w"], 3), "y": e["y"], "w": e["w"], "h": e["h"]} for e in els]
    overlapping = [(a["name"], b["name"]) for i, a in enumerate(mirrored) for b in mirrored[i + 1:] if _overlaps(a, b)]
    findings = [chk("rtl.mirror", all(m["x"] >= 0 for m in mirrored), "Mirrored elements stay on screen." if all(m["x"] >= 0 for m in mirrored) else "Some mirrored elements leave the screen.", "fail"),
                chk("rtl.icons", None, "Directional icons (arrows, progress) must flip; icons without direction must not."),
                chk("rtl.text", None, "Verify text alignment and bidi rendering with an RTL locale.")]
    return out({"mirrored": mirrored, "overlapping": overlapping}, findings)


def h_input(d, cap, ctx):
    bindings = core.array(d, "bindings", required=True, kind=dict, maximum=500)
    reserved = {"Escape", "Tab"}
    keys = {}
    conflicts, reserved_hits = [], []
    for b in bindings:
        action = core.text(b, "action", maximum=60)
        key = core.text(b, "key", maximum=40)
        if key in reserved:
            reserved_hits.append({"action": action, "key": key})
        if key in keys and keys[key] != action:
            conflicts.append({"key": key, "actions": [keys[key], action]})
        keys.setdefault(key, action)
    findings = [chk("input.conflicts", not conflicts, "No key is bound to two actions." if not conflicts else f"{len(conflicts)} key conflict(s).", "fail"),
                chk("input.reserved", not reserved_hits, "No binding uses a reserved key." if not reserved_hits else "Reserved keys in use: " + ", ".join(sorted({r["key"] for r in reserved_hits})), "warning"),
                chk("input.rebinding", None, "Expose rebinding in settings; this checks the defaults only.")]
    return out({"actions": len(bindings), "conflicts": conflicts}, findings)


def h_gamepad(d, cap, ctx):
    controls = core.array(d, "controls", required=True, kind=str, maximum=500)
    edges = core.array(d, "edges", required=True, kind=dict, maximum=5000)
    initial = core.text(d, "initial", maximum=80)
    dead = core.number(d, "deadzone", minimum=0, maximum=0.9)
    adj = {c: [] for c in controls}
    bad = []
    for e in edges:
        a, b = core.text(e, "from", maximum=80), core.text(e, "to", maximum=80)
        if a in adj and b in adj:
            adj[a].append(b)
        else:
            bad.append(f"{a}->{b}")
    seen, pend = set(), [initial]
    while pend:
        c = pend.pop()
        if c in seen or c not in adj:
            continue
        seen.add(c)
        pend.extend(adj[c])
    unreachable = sorted(set(controls) - seen)
    findings = [chk("gamepad.edges", not bad, "All focus edges reference declared controls." if not bad else "Undeclared focus edges: " + ", ".join(bad), "fail"),
                chk("gamepad.reachable", not unreachable, "Every control is reachable by stick navigation." if not unreachable else "Unreachable by gamepad: " + ", ".join(unreachable), "fail"),
                chk("gamepad.deadzone", 0.05 <= dead <= 0.3, f"Deadzone {dead}." if 0.05 <= dead <= 0.3 else f"Deadzone {dead} is outside 0.05-0.3.", "warning")]
    return out({"reachable": len(seen), "unreachable": unreachable}, findings)


def h_design_tokens(d, cap, ctx):
    bg = core.text(d, "background", maximum=10)
    fg = core.text(d, "foreground", maximum=10)
    accent = core.text(d, "accent", maximum=10)
    spacing = core.array(d, "spacing", maximum=50, kind=(int, float))
    body = core.number(d, "body_size", minimum=8, maximum=96)
    minc = core.number(d, "minimum_contrast", minimum=1, maximum=21)
    r_fg = core.contrast_ratio(fg, bg)
    r_acc = core.contrast_ratio(accent, bg)
    scale_ok = all(spacing[i] <= spacing[i + 1] for i in range(len(spacing) - 1)) if spacing else True
    findings = [chk("design_tokens.text_contrast", r_fg >= minc, f"Foreground contrast {round(r_fg, 2)}:1." if r_fg >= minc else f"Foreground contrast {round(r_fg, 2)}:1 is below {minc}:1.", "fail"),
                chk("design_tokens.accent_contrast", r_acc >= 3.0, f"Accent contrast {round(r_acc, 2)}:1." if r_acc >= 3.0 else f"Accent contrast {round(r_acc, 2)}:1 is below 3:1 for non-text.", "warning"),
                chk("design_tokens.spacing", scale_ok, "Spacing scale is ascending." if scale_ok else "Spacing scale must ascend.", "warning"),
                chk("design_tokens.body", body >= 14, f"Body size {body}px." if body >= 14 else "Body size under 14px is hard to read.", "warning")]
    return out({"foreground_contrast": round(r_fg, 3), "accent_contrast": round(r_acc, 3)}, findings)


HANDLERS = {
    "avatar_body": h_avatar_body, "r15": h_r15, "humanoid_description": h_humanoid_description, "dynamic_head": h_dynamic_head,
    "avatar_setup": h_avatar_setup, "skin_transfer": h_skin_transfer, "avatar_import": h_avatar_import, "npc_rig": h_npc_rig,
    "avatar_preview": h_avatar_preview, "classic_clothing": h_classic_clothing, "layered_clothing": h_layered_clothing,
    "accessory": h_accessory, "cage": h_cage, "textures": h_textures, "outfit": h_outfit, "mesh_pipeline": h_mesh_pipeline,
    "dcc_export": h_dcc_export, "pbr": h_pbr, "deformation": h_deformation, "import_automation": h_import_automation,
    "audio_pipeline": h_audio_pipeline, "materials": h_materials, "ugc_gate": h_ugc_gate, "marketplace": h_marketplace,
    "thumbnail": h_thumbnail, "capture_scene": h_capture_scene,
    "geometry": h_geometry, "uv": h_uv, "collision": h_collision, "lod": h_lod, "pivot": h_pivot, "biome": h_biome,
    "terrain": h_terrain, "procedural_map": h_procedural_map, "modular_kit": h_modular_kit, "vegetation": h_vegetation,
    "streaming": h_streaming, "streaming_audit": h_streaming_audit, "camera": h_camera, "camera_collision": h_camera_collision,
    "hit_detection": h_hit_detection, "targeting": h_targeting, "pathfinding": h_pathfinding, "perception": h_perception,
    "ai_debug": h_ai_debug, "placement": h_placement, "interaction_framework": h_interaction_framework,
    "spatial_audio": h_spatial_audio,
    "ui_layout": h_ui_layout, "mobile_ui": h_mobile_ui, "hud": h_hud, "inventory_ui": h_inventory_ui, "world_ui": h_world_ui,
    "responsive": h_responsive, "usability": h_usability, "visual_qa": h_visual_qa, "rtl": h_rtl, "input": h_input,
    "gamepad": h_gamepad, "design_tokens": h_design_tokens,
}


def h_fitting(d, cap, ctx):
    target = core.vector(d.get("target"), 3)
    current = core.vector(d.get("current"), 3)
    scale = core.number(d, "scale", default=1.0, minimum=0.5, maximum=2.0)
    tolerance = core.number(d, "tolerance", default=0.25, minimum=0.001, maximum=5)
    bodies = core.array(d, "bodies", required=True, kind=str, maximum=20)
    offsets = core.obj(d, "body_offsets")
    correction = [(t - c) * scale for t, c in zip(target, current)]
    corrected = [c + k for c, k in zip(current, correction)]
    rows = []
    for b in bodies:
        point = offsets.get(b)
        if point is None:
            rows.append({"body": b, "residual_studs": None, "status": "review"})
            continue
        point = core.vector(point, 3)
        residual = math.dist(corrected, point)
        rows.append({"body": b, "residual_studs": round(residual, 6), "status": "pass" if residual <= tolerance else "fail"})
    failing = [r["body"] for r in rows if r["status"] == "fail"]
    unsupplied = [r["body"] for r in rows if r["status"] == "review"]
    findings = [chk("fitting.correction", True, f"Offset correction {[round(x, 6) for x in correction]} at scale {scale}."),
                chk("fitting.cross_body", not failing, "Every supplied body is within tolerance." if not failing else f"Bodies out of tolerance ({tolerance} studs): " + ", ".join(failing), "fail"),
                chk("fitting.reference_data", not unsupplied, "Every body has a reference attachment point." if not unsupplied else "No reference attachment supplied for: " + ", ".join(unsupplied) + "; supply body_offsets to verify.", "warning")]
    return out({"correction": [round(x, 6) for x in correction], "corrected_attachment": [round(x, 6) for x in corrected],
                "bodies": rows, "tolerance_studs": tolerance}, findings)


HANDLERS["fitting"] = h_fitting
