"""Families: structure (DataModel trees), graph (dependencies and routes), schema (typed values), migration.

Loaded as rbx_structure by tools-engine.py. Each handler receives (data, capability, ctx) and returns
{"result", "findings", "artifacts", "warnings"}. Findings are checks: pass, fail, or review (operator evidence).
"""
import copy
import json
import math
import re

import rbx_core as core
from rbx_core import chk

FAMILY = "structure"
HANDLERS = {}
SERVICES = {"Workspace", "ReplicatedStorage", "ReplicatedFirst", "ServerStorage", "ServerScriptService", "StarterPlayer",
            "StarterGui", "StarterPack", "Lighting", "SoundService", "Players", "Teams", "TextChatService",
            "Chat", "HttpService", "MemoryStoreService", "DataStoreService", "MessagingService", "PhysicsService"}
REPLICATED = {"ReplicatedStorage", "ReplicatedFirst", "Workspace", "Lighting", "SoundService", "StarterPlayer", "StarterGui"}
SERVER_ONLY = {"ServerStorage", "ServerScriptService"}
CLIENT_ONLY = {"StarterPlayer", "StarterGui", "StarterPack"}
SERVER_SCRIPT = {"Script"}
CLIENT_SCRIPT = {"LocalScript"}
REMOTE_CLASSES = {"RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent"}
SECRET_NAME = re.compile(r"secret|token|api[_-]?key|password|credential", re.I)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
LUAU_RESERVED = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil", "not",
                 "or", "repeat", "return", "then", "true", "until", "while", "continue", "type", "export"}
BASEPART = {"Part", "MeshPart", "WedgePart", "CornerWedgePart", "TrussPart", "SpawnLocation", "Seat", "VehicleSeat"}
BASEPART_PROPS = {"Anchored", "CanCollide", "CanQuery", "CanTouch", "Transparency", "Size", "CFrame", "Position",
                  "Color", "Material", "Massless", "CollisionGroup", "CastShadow", "Locked", "Name", "Velocity"}
PROPERTY_NOTES = {
    "Anchored": "Anchored parts are not moved by physics. Changing it on a moving assembly can cause sudden motion; set it before the assembly is built.",
    "CanCollide": "Controls whether the part blocks characters and other parts. Turning it off is a common cause of fall-through when used on the floor.",
    "CanQuery": "Controls whether raycasts and spatial queries detect the part. Hit detection depends on it.",
    "CanTouch": "Controls whether Touched events fire. Disabling it stops interaction triggers.",
    "Transparency": "Visual only; the part still collides. Hidden geometry still costs render and physics time unless it is removed.",
    "Size": "Changes collision and render bounds. Sizes must stay within the engine's part limits (see tools-core studio config).",
    "CFrame": "Position and rotation. Replicated changes on anchored parts can cause visible snapping unless interpolated.",
    "Material": "Visual and footstep-sound hint only; it does not change physics friction by itself.",
    "Massless": "Excludes the part from assembly mass calculations; use only for non-structural visuals.",
    "CollisionGroup": "Filters which parts collide. Mis-assigned groups are a frequent cause of invisible walls.",
    "CastShadow": "Shadow cost scales with the number of casting parts; disable it for small decorations.",
    "Locked": "Prevents selection in Studio only; it has no runtime gameplay effect.",
    "Velocity": "Assigning velocity on a non-owned assembly is overridden by the network owner.",
    "Color": "Visual only.",
    "Name": "Instance names are used in paths; duplicate sibling names make path references ambiguous.",
    "Position": "Shortcut for the CFrame translation; same replication and snapping considerations as CFrame.",
}
SETTINGS_RECOMMENDED = {
    "Workspace.FilteringEnabled": True,
    "Workspace.StreamingEnabled": True,
    "HttpService.HttpEnabled": False,
    "TextChatService.ChatVersion": "TextChatService",
}


def out(result, findings, artifacts=None, warnings=None):
    return {"result": result, "findings": findings, "artifacts": artifacts or {}, "warnings": warnings or []}


def tree_nodes(tree):
    return list(core.walk_tree(tree))


def under(path, prefix_segments):
    parts = path.split("/")
    return parts[1:1 + len(prefix_segments)] == list(prefix_segments)


def service_of(path):
    parts = path.split("/")
    return parts[1] if len(parts) > 1 else None


def luau_name(value):
    return bool(IDENT.match(value)) and value not in LUAU_RESERVED


def lua_string(value):
    return core.lua(str(value))


# ---------------------------------------------------------------- structure

def h_bootstrap(d, cap, ctx):
    name = core.text(d, "project_name", maximum=64)
    genre = core.text(d, "genre", maximum=40)
    studio = ctx.config["studio"]
    templates = core.load_json_resource(ctx.config["paths"]["place_templates"])
    known = sorted(templates["genres"].keys())
    folders = [
        ("ReplicatedStorage", "Shared"), ("ReplicatedStorage", "Remotes"), ("ReplicatedStorage", "Config"),
        ("ServerScriptService", "Services"), ("ServerStorage", "Templates"),
        ("StarterPlayer/StarterPlayerScripts", "Client"), ("Workspace", "Map"), ("Workspace", "Runtime"),
    ]
    tree = {"name": "game", "class": "DataModel", "children": []}
    for path, child in folders:
        parts = path.split("/")
        node = next(c for c in tree["children"] if c["name"] == parts[0]) if any(c["name"] == parts[0] for c in tree["children"]) else None
        if node is None:
            node = {"name": parts[0], "class": parts[0] if parts[0] in SERVICES else "Folder", "children": []}
            tree["children"].append(node)
        if len(parts) > 1:
            sub = next((c for c in node["children"] if c["name"] == parts[1]), None)
            if sub is None:
                sub = {"name": parts[1], "class": "Folder", "children": []}
                node["children"].append(sub)
            node = sub
        node["children"].append({"name": child, "class": "Folder", "children": []})
    config_module = {"project": project_slug(name), "genre": genre, "environment": "development",
                     "collision_groups": studio["collision_groups"], "tags": studio["tags"]}
    lines = ["--!strict", "-- Generated by tools-roblox.py bootstrap. Review before running in Studio (command bar or server script).",
             "local ServerScriptService = game:GetService(\"ServerScriptService\")", "local PhysicsService = game:GetService(\"PhysicsService\")",
             "local CollectionService = game:GetService(\"CollectionService\")", "",
             "-- Collision groups (idempotent)"]
    for group in studio["collision_groups"]:
        lines.append(f"if not PhysicsService:IsCollisionGroupRegistered({lua_string(group)}) then PhysicsService:RegisterCollisionGroup({lua_string(group)}) end")
    lines += ["", "local function ensureFolder(parent: Instance, name: string): Instance",
              "\tlocal existing = parent:FindFirstChild(name)", "\tif existing then return existing end",
              "\tlocal folder = Instance.new(\"Folder\")", "\tfolder.Name = name", "\tfolder.Parent = parent", "\treturn folder", "end", "", "-- Project folders (idempotent)"]
    for path, child in folders:
        parts = path.split("/")
        expr = f"game:GetService({lua_string(parts[0])})"
        for part in parts[1:]:
            expr = f"ensureFolder({expr}, {lua_string(part)})"
        lines.append(f"ensureFolder({expr}, {lua_string(child)})")
    lines += ["", "-- Starter config table for team development", "local Config = " + core.lua(config_module), "return Config", ""]
    findings = [
        chk("structure.project_name", bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{1,63}", name)),
            "Project name is a readable ASCII name of 2-64 characters." if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{1,63}", name) else "Project name must start with a letter and use ASCII letters, digits, spaces, hyphens or underscores."),
        chk("structure.genre", True if genre in known else None,
            f"Genre '{genre}' matches a preset." if genre in known else f"Genre '{genre}' has no preset; generic folders were used and genre systems need review."),
        chk("structure.collision_groups", len(set(studio["collision_groups"])) == len(studio["collision_groups"]),
            "Collision group names are unique in the configured list."),
        chk("structure.source_ownership", None, "Team ownership of each folder must be assigned with team_workflow; not inferred here."),
    ]
    return out({"project": config_module, "tree": tree, "folders": [f"{p}/{c}" for p, c in folders]},
               findings, {"Bootstrap.luau": "\n".join(lines), "project-tree.json": tree})


def project_slug(name):
    return core.safe_name(name, "project")


def h_hierarchy(d, cap, ctx):
    tree = core.obj(d, "tree", required=True)
    entries = tree_nodes(tree)
    findings, rows, counts = [], [], {}
    flagged = {"server_secrets": False, "client_modules": False, "remote_container": False}
    for entry in entries:
        node, path, cls = entry["node"], entry["path"], entry["class"]
        svc = service_of(path)
        counts[cls] = counts.get(cls, 0) + 1
        rows.append({"path": path, "class": cls})
        if cls == "Script" and svc in ("ReplicatedStorage", "ReplicatedFirst"):
            flagged["server_secrets"] = True
            findings.append(chk("hierarchy.server_secrets", False, f"Server Script at {path} is under replicated {svc}; move it to ServerScriptService.", "fail"))
        elif cls == "Script" and svc == "Workspace":
            findings.append(chk("hierarchy.server_secrets", None, f"Server Script at {path} sits in Workspace; confirm it holds no secrets."))
        if SECRET_NAME.search(node.get("name", "")) and svc in REPLICATED:
            flagged["server_secrets"] = True
            findings.append(chk("hierarchy.server_secrets", False, f"'{path}' name suggests a secret inside replicated container {svc}.", "fail"))
        if cls == "LocalScript" and svc in SERVER_ONLY:
            flagged["client_modules"] = True
            findings.append(chk("hierarchy.client_modules", False, f"LocalScript {path} is under server-only {svc} and never runs on clients.", "fail"))
        if cls == "ModuleScript" and svc in REPLICATED and svc not in ("ReplicatedStorage", "ReplicatedFirst"):
            findings.append(chk("hierarchy.client_modules", None, f"ModuleScript {path} is replicated via {svc}; confirm it holds no server-only logic."))
        if cls in REMOTE_CLASSES and svc != "ReplicatedStorage":
            flagged["remote_container"] = True
            findings.append(chk("hierarchy.remote_container", False, f"{cls} {path} should live in ReplicatedStorage so both sides resolve it.", "fail"))
        if path.count("/") > 12:
            findings.append(chk("hierarchy.depth", None, f"Path {path} is deeper than 12 levels."))
    top = [c["name"] for c in tree.get("children", [])]
    for name in top:
        if name not in SERVICES:
            findings.append(chk("hierarchy.services", None, f"Top-level '{name}' is not a known service name; verify it is a valid container."))
    for key, bad in flagged.items():
        if not bad:
            findings.append(chk(f"hierarchy.{key}", True, f"No {key.replace('_', ' ')} violation found in {len(entries)} instance(s)."))
    return out({"instance_count": len(entries), "classes": counts, "top_level": top, "entries": rows[:500]}, findings)


SCRIPT_CLASSES = {"Script", "LocalScript", "ModuleScript"}


PURPOSE_CONTAINER = {
    "template": ("ServerStorage/Templates", "server"), "remote": ("ReplicatedStorage/Remotes", "shared"),
    "shared": ("ReplicatedStorage/Shared", "shared"), "client": ("StarterPlayer/StarterPlayerScripts/Client", "client"),
    "effects": ("ReplicatedStorage/Effects", "shared"), "server": ("ServerScriptService/Services", "server"),
    "temporary": ("Workspace/Runtime", "temporary"), "npc": ("Workspace/NPCs", "shared"), "map": ("Workspace/Map", "shared"),
    "tool": ("StarterPack", "shared"), "asset": ("ReplicatedStorage/Assets", "shared"), "module": ("ReplicatedStorage/Shared", "shared"),
}


def h_containers(d, cap, ctx):
    objects = core.array(d, "objects", required=True, kind=dict, maximum=2000)
    names, findings, plan = set(), [], []
    for obj in objects:
        name = core.text(obj, "name", maximum=120)
        purpose = core.text(obj, "purpose", maximum=40)
        replication = core.text(obj, "replication", maximum=20)
        if name in names:
            findings.append(chk("containers.unique_names", False, f"Duplicate object name '{name}' makes references ambiguous.", "fail"))
        names.add(name)
        target = PURPOSE_CONTAINER.get(purpose.lower())
        if target is None:
            findings.append(chk("containers.lifetime", None, f"Purpose '{purpose}' has no taxonomy rule; assign a container manually."))
            plan.append({"name": name, "container": "unassigned", "replication": replication})
            continue
        container, expected = target
        if replication == "server" and container.startswith("ReplicatedStorage"):
            findings.append(chk("containers.replication", False, f"'{name}' is server-only but targets {container}.", "fail"))
        if replication == "client" and container.startswith(("ServerStorage", "ServerScriptService")):
            findings.append(chk("containers.replication", False, f"'{name}' is client-only but targets server container {container}.", "fail"))
        if purpose == "temporary" and not name.endswith(("Runtime", "Temp", "Effect")):
            findings.append(chk("containers.lifetime", None, f"Temporary object '{name}' should be cleaned up by its owning system."))
        plan.append({"name": name, "container": container, "replication": replication, "purpose": purpose})
    findings.append(chk("containers.unique_names", True, "Object names are unique."))
    return out({"plan": plan, "taxonomy": {k: v[0] for k, v in PURPOSE_CONTAINER.items()}}, findings,
               {"container-plan.json": plan})


def h_replication(d, cap, ctx):
    objects = core.array(d, "objects", required=True, kind=dict, maximum=2000)
    findings, rows = [], []
    for obj in objects:
        name = core.text(obj, "name", maximum=120)
        where = core.text(obj, "location", maximum=200, required=False) or ""
        kind = core.text(obj, "kind", maximum=40, required=False) or "unknown"
        svc = where.split("/")[0] if where else ""
        verdict = "review"
        reason = "Replication boundary needs a location to evaluate."
        if kind in ("secret", "server_state", "loot_table") and svc in REPLICATED:
            verdict, reason = "fail", "Server-only data is inside a replicated container."
        elif kind in ("secret", "server_state", "loot_table"):
            verdict, reason = "pass", "Stays in server-only storage."
        elif kind in ("ui", "camera", "input") and svc in ("StarterGui", "StarterPlayer"):
            verdict, reason = "pass", "Client-only state stays in client containers."
        elif kind == "shared_config" and svc == "ReplicatedStorage":
            verdict, reason = "pass", "Shared configuration replicates to both sides."
        if verdict == "fail":
            findings.append(chk(f"replication.{core.safe_name(name)}", False, f"{name}: {reason}", "fail"))
        elif verdict == "review":
            findings.append(chk(f"replication.{core.safe_name(name)}", None, f"{name}: {reason}"))
        rows.append({"name": name, "kind": kind, "location": where, "verdict": verdict, "reason": reason})
    findings.append(chk("replication.streaming", None, "Streaming regions depend on world_streaming planning (see geometry family)."))
    return out({"objects": rows}, findings, {"replication-boundaries.json": rows})


def h_properties(d, cap, ctx):
    cls = core.text(d, "class_name", maximum=60)
    props = core.obj(d, "properties", required=True)
    limits = ctx.config["studio"]
    findings, notes = [], {}
    for key, value in props.items():
        if key in PROPERTY_NOTES:
            notes[key] = PROPERTY_NOTES[key]
        if key == "Size" and isinstance(value, list):
            ok = len(value) == 3 and all(limits["part_size_min"] <= float(x) <= limits["part_size_max"] for x in value if isinstance(x, (int, float)))
            findings.append(chk("properties.size_limits", ok,
                                "Size components are within the configured part limits." if ok else
                                f"Size components must be between {limits['part_size_min']} and {limits['part_size_max']} studs."))
        if key == "Transparency" and not (isinstance(value, (int, float)) and 0 <= value <= 1):
            findings.append(chk("properties.values", False, "Transparency must be between 0 and 1.", "fail"))
    if cls in BASEPART:
        unknown = [k for k in props if k not in BASEPART_PROPS]
        findings.append(chk("properties.class_compatibility", None if unknown else True,
                            ("Properties not in the audited BasePart list: " + ", ".join(unknown) + "; verify against the API reference.") if unknown else f"All supplied properties are audited BasePart properties for {cls}."))
    else:
        findings.append(chk("properties.class_compatibility", None, f"{cls} is not a BasePart class in the audited table; property compatibility needs the API dump."))
    if props.get("Anchored") is False and cls in BASEPART:
        findings.append(chk("properties.physics_ownership", None, "Unanchored parts are simulated; check network ownership and assembly mass with the physics family."))
    findings.append(chk("properties.finite_values", True, "All numeric values are finite (validated by the JSON parser)."))
    return out({"class": cls, "properties": props, "consequences": notes}, findings)


def h_settings(d, cap, ctx):
    settings = core.obj(d, "settings", required=True)
    allow_http = core.boolean(d, "allow_http", False)
    rows, findings = [], []
    for key, recommended in SETTINGS_RECOMMENDED.items():
        if key not in settings:
            rows.append({"setting": key, "value": None, "recommended": recommended, "verdict": "review", "reason": "not supplied"})
            continue
        value = settings[key]
        if key == "HttpService.HttpEnabled":
            if value and not allow_http:
                verdict, reason = "fail", "HTTP is enabled but allow_http is false; enable only with an approved allowlist."
            elif value:
                verdict, reason = "review", "HTTP is enabled with allow_http; confirm the allowlist and approval record."
            else:
                verdict, reason = "pass", "HTTP is disabled."
        elif key == "Workspace.FilteringEnabled":
            verdict, reason = ("pass", "Filtering is enabled.") if value is True else ("fail", "FilteringEnabled must stay true for replicated changes.")
        elif key == "Workspace.StreamingEnabled":
            verdict, reason = ("pass", "Streaming is enabled.") if value is True else ("review", "Streaming is off; large worlds need the memory/streaming review.")
        else:
            verdict, reason = ("pass", "Matches the recommended value.") if value == recommended else ("review", f"Differs from recommended {recommended!r}.")
        rows.append({"setting": key, "value": value, "recommended": recommended, "verdict": verdict, "reason": reason})
        findings.append(chk("settings." + key.split(".")[-1].lower(), True if verdict == "pass" else (None if verdict == "review" else False), reason, "fail" if verdict == "fail" else "warning"))
    for key in settings:
        if key not in SETTINGS_RECOMMENDED:
            rows.append({"setting": key, "value": settings[key], "recommended": None, "verdict": "review", "reason": "not in the audited list"})
    return out({"rows": rows}, findings, {"settings-audit.json": rows})


def h_team_workflow(d, cap, ctx):
    team = core.array(d, "team", required=True, kind=dict, maximum=200)
    branch = core.text(d, "branch", maximum=80)
    findings, rows = [], []
    for area in team:
        name = core.text(area, "area", maximum=80)
        owner = core.text(area, "owner", maximum=80, required=False) or ""
        reviewer = core.text(area, "reviewer", maximum=80, required=False) or ""
        rows.append({"area": name, "owner": owner, "reviewer": reviewer})
        if not owner:
            findings.append(chk("team_workflow.owners", False, f"Area {name} has no owner.", "fail"))
        if not reviewer:
            findings.append(chk("team_workflow.reviewers", False, f"Area {name} has no reviewer.", "fail"))
        elif reviewer == owner:
            findings.append(chk("team_workflow.reviewers", None, f"Area {name}: owner and reviewer are the same team; add a second reviewer."))
    findings.append(chk("team_workflow.merge_rules", None, "Merge and review rules need the team's written policy; not inferred."))
    findings.append(chk("team_workflow.binary_policy", None, "Binary assets (.rbxl, .rbxm, textures) need a locking or Git LFS policy from the team."))
    lines = [f"# Ownership for branch `{branch}`", "", "| Area | Owner | Reviewer |", "|---|---|---|"]
    lines += [f"| {r['area']} | {r['owner']} | {r['reviewer']} |" for r in rows]
    lines += ["", "Confirm this map with each team before enforcing it in code review."]
    return out({"branch": branch, "areas": rows}, findings, {"team-ownership.md": "\n".join(lines) + "\n"})


def h_experience(d, cap, ctx):
    title = core.text(d, "title", maximum=50)
    description = core.text(d, "description", maximum=1000)
    devices = core.array(d, "devices", required=True, kind=str, maximum=10)
    locales = core.array(d, "locales", required=True, kind=str, maximum=50)
    private = core.boolean(d, "private_servers", False)
    valid_devices = {"Computer", "Phone", "Tablet", "Console", "VR"}
    findings = [
        chk("experience.truthful_metadata", not re.search(r"guaranteed|100%\s*safe|free robux|instant (?:money|robux)|best game ever", description, re.I),
            "Description contains no absolute or inducement claims." if not re.search(r"guaranteed|100%\s*safe|free robux|instant (?:money|robux)|best game ever", description, re.I) else "Description contains an absolute or inducement claim; rewrite it to describe actual gameplay.", "fail"),
        chk("experience.devices", all(x in valid_devices for x in devices) and bool(devices),
            "Devices use known names." if all(x in valid_devices for x in devices) and devices else "Devices must be a non-empty list of Computer, Phone, Tablet, Console or VR."),
        chk("experience.localization", all(re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", l) for l in locales) and "en" in locales,
            "Locale tags are well-formed and include en." if all(re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", l) for l in locales) and "en" in locales else "Locales must be well-formed BCP-47 tags and should include en."),
        chk("experience.rights", None, "Icon, thumbnail and title rights must be confirmed by the creator; not verified offline."),
    ]
    findings.append(chk("experience.private_servers", None if private else True,
                        "Private servers are enabled; confirm reserved-server safety rules." if private else "Private servers are disabled."))
    meta = {"title": title, "description": description, "devices": devices, "locales": locales, "private_servers": private, "title_length": len(title)}
    return out(meta, findings, {"experience-metadata.json": meta})


# ---------------------------------------------------------------- graph

def graph_from_nodes(nodes, edges):
    names = [n if isinstance(n, str) else n["id"] for n in nodes]
    pairs = []
    for e in edges:
        pairs.append((e["from"], e["to"]) if isinstance(e, dict) else (e[0], e[1]))
    return names, pairs


def h_wiring(d, cap, ctx):
    root = core.text(d, "root", maximum=120)
    objects = core.array(d, "objects", required=True, kind=dict, maximum=2000)
    refs = core.array(d, "references", maximum=5000, kind=dict)
    paths, findings, luau = set(), [], []
    class_allow = BASEPART | {"Model", "Folder", "Attachment", "Sound", "ParticleEmitter", "Script", "LocalScript", "ModuleScript",
                              "BillboardGui", "SurfaceGui", "ScreenGui", "Frame", "TextLabel", "ImageLabel", "ObjectValue",
                              "BoolValue", "NumberValue", "StringValue", "Humanoid", "RemoteEvent", "RemoteFunction", "SpawnLocation", "Configuration"}
    for obj in objects:
        path = core.text(obj, "path", maximum=200)
        cls = core.text(obj, "class_name", maximum=60)
        if path in paths:
            findings.append(chk("wiring.unique_paths", False, f"Duplicate path {path}.", "fail"))
        paths.add(path)
        if cls not in class_allow:
            findings.append(chk("wiring.classes", None, f"{cls} is outside the wiring allowlist; verify it is a valid creatable class."))
        for tag in obj.get("tags", []):
            if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", tag):
                findings.append(chk("wiring.tags", False, f"Tag {tag!r} on {path} is not a valid tag name.", "fail"))
        for key, val in (obj.get("attributes") or {}).items():
            if not isinstance(val, (str, int, float, bool)):
                findings.append(chk("wiring.attributes", False, f"Attribute {key} on {path} has an unsupported type.", "fail"))
    for ref in refs:
        src, dst = core.text(ref, "from", maximum=200), core.text(ref, "to", maximum=200)
        if src not in paths or dst not in paths:
            findings.append(chk("wiring.references", False, f"Reference {src} -> {dst} points outside the declared objects.", "fail"))
    names, pairs = graph_from_nodes(sorted(paths), [(r["from"], r["to"]) for r in refs if r.get("from") in paths and r.get("to") in paths])
    try:
        order = core.graph_order(names, pairs)
        if not any(f["id"] == "wiring.references" for f in findings):
            findings.append(chk("wiring.references", True, "Reference graph is acyclic and resolved."))
    except core.ToolError as exc:
        order = []
        findings.append(chk("wiring.references", False, exc.message, "fail"))
    by_path = {o["path"]: o for o in objects}
    luau.append("--!strict\n-- Generated insertion plan for " + root + ". Run as a server Script after review.\nlocal CollectionService = game:GetService(\"CollectionService\")\nlocal root = workspace")
    for path in order:
        obj = by_path[path]
        parts = path.split("/")
        luau.append(f"do\n\tlocal parent = root\n\tlocal inst = Instance.new({lua_string(obj['class_name'])})\n\tinst.Name = {lua_string(parts[-1])}")
        for tag in obj.get("tags", []):
            luau.append(f"\tCollectionService:AddTag(inst, {lua_string(tag)})")
        for key, val in (obj.get("attributes") or {}).items():
            luau.append(f"\tinst:SetAttribute({lua_string(key)}, {core.lua(val)})")
        luau.append(f"\tinst.Parent = parent\nend")
    result = {"insertion_order": order, "objects": len(objects), "references": len(refs)}
    if not any(f["status"] == "fail" for f in findings):
        findings.append(chk("wiring.classes_tags_attributes", True, "Paths, tags and attribute types validated; insertion order computed."))
    return out(result, findings, {"Wiring.luau": "\n".join(luau) + "\n"})


def h_references(d, cap, ctx):
    limits = ctx.config["limits"]
    refs = core.array(d, "references", required=True, kind=dict, maximum=500)
    available = set(core.array(d, "available", maximum=5000, kind=str))
    findings, rows, ok_timeouts = [], [], True
    for r in refs:
        name = core.text(r, "name", maximum=80)
        path = core.text(r, "path", maximum=300)
        timeout = core.number(r, "timeout", 5, 0.1, 30)
        resolved = path in available
        rows.append({"name": name, "path": path, "timeout": timeout, "resolved": resolved})
        if not resolved:
            findings.append(chk("references.missing_dependencies", False, f"{name} at {path} is not available; the server would wait {timeout}s and fail.", "fail"))
    findings.append(chk("references.timeouts", True, "All WaitForChild timeouts are bounded between 0.1 and 30 seconds."))
    findings.append(chk("references.streaming", None, "Streamed dependencies need a fallback path; confirm with streaming_audit when StreamingEnabled is true."))
    lines = ["--!strict", "-- Bounded resolver generated by tools-roblox.py. Review before use.", "local Resolved = {}"]
    for row in rows:
        root_svc = row["path"].split("/")[0]
        lines.append(f"Resolved[{lua_string(row['name'])}] = game:GetService({lua_string(root_svc)}):WaitForChild({lua_string('/'.join(row['path'].split('/')[1:]))}, {row['timeout']})" if "/" in row["path"] else f"Resolved[{lua_string(row['name'])}] = game:GetService({lua_string(root_svc)})")
    lines.append("return Resolved")
    return out({"root": d.get("root"), "resolutions": rows, "resolved": sum(r["resolved"] for r in rows), "missing": sum(not r["resolved"] for r in rows)},
               findings, {"ReferenceResolver.luau": "\n".join(lines) + "\n"})


def h_universe(d, cap, ctx):
    places = core.array(d, "places", required=True, kind=dict, maximum=200)
    routes = core.array(d, "routes", maximum=1000, kind=dict)
    home = core.text(d, "home", maximum=80)
    shared = core.text(d, "shared_store", maximum=80)
    ids = [core.text(p, "id", maximum=80) for p in places]
    kinds = {core.text(p, "id", maximum=80): core.text(p, "kind", maximum=20) for p in places}
    findings = []
    if home not in ids:
        findings.append(chk("universe.return_paths", False, f"Home place '{home}' is not in places.", "fail"))
    if not luau_name(shared):
        findings.append(chk("universe.shared_schema", False, "shared_store must be a valid identifier.", "fail"))
    adjacency = {i: [] for i in ids}
    for r in routes:
        a, b = core.text(r, "from", maximum=80), core.text(r, "to", maximum=80)
        if a not in adjacency or b not in adjacency:
            findings.append(chk("universe.place_ids", False, f"Route {a}->{b} references an unknown place.", "fail"))
            continue
        access = core.text(r, "access", maximum=20)
        if access not in ("always", "match", "party", "invite", "admin"):
            findings.append(chk("universe.access_rules", None, f"Route {a}->{b} uses access '{access}' which is not a known rule."))
        if kinds.get(b) == "reserved" and access == "always":
            findings.append(chk("universe.access_rules", False, f"Reserved place {b} is reachable with access 'always'.", "fail"))
        adjacency[a].append(b)
    seen, pending = set(), [home] if home in adjacency else []
    while pending:
        item = pending.pop()
        if item not in seen:
            seen.add(item)
            pending.extend(adjacency[item])
    unreachable = sorted(set(ids) - seen)
    findings.append(chk("universe.place_ids", not unreachable, "Every place is reachable from home." if not unreachable else f"Unreachable from home: {', '.join(unreachable)}", "fail"))
    no_return = []
    for pid in ids:
        if pid == home:
            continue
        back, pend, vis = False, [pid], set()
        while pend:
            cur = pend.pop()
            if cur == home:
                back = True
                break
            if cur in vis:
                continue
            vis.add(cur)
            pend.extend(adjacency[cur])
        if not back:
            no_return.append(pid)
    findings.append(chk("universe.return_paths", not no_return, "Every place has a graceful return path to home." if not no_return else f"No return path to home from: {', '.join(no_return)}", "fail"))
    findings.append(chk("universe.shared_schema", None, "Shared store schema versions must be enforced by data_migration tools; schema is not verified against live data."))
    graph = ["flowchart LR"] + [f"  {a}[{a}] --> {b}" for a in ids for b in adjacency[a]]
    result = {"places": ids, "routes": sum(len(v) for v in adjacency.values()), "reachable": sorted(seen), "unreachable": unreachable, "without_return": no_return}
    return out(result, findings, {"routes.mmd": "\n".join(graph) + "\n"})


CLASS_TABLE = {
    "Instance": None, "PVInstance": "Instance", "Model": "PVInstance", "BasePart": "PVInstance", "Part": "BasePart",
    "MeshPart": "BasePart", "WedgePart": "BasePart", "SpawnLocation": "Part", "Seat": "Part", "Folder": "Instance",
    "Humanoid": "Instance", "Attachment": "Instance", "LuaSourceContainer": "Instance", "BaseScript": "LuaSourceContainer",
    "Script": "BaseScript", "LocalScript": "BaseScript", "ModuleScript": "LuaSourceContainer", "RemoteEvent": "Instance",
    "RemoteFunction": "Instance", "Sound": "Instance", "ParticleEmitter": "Instance",
}


def h_api_lookup(d, cap, ctx):
    query = core.text(d, "query", maximum=120)
    dump = d.get("api_dump")
    findings = []
    if dump is None:
        source, classes = "built-in partial table", {k: v for k, v in CLASS_TABLE.items()}
        findings.append(chk("api_lookup.official_api", None,
                            "Only a partial built-in class table was used. Supply api_dump (exported API dump) for a complete lookup."))
    else:
        core.array({"x": dump}, "x", kind=dict, maximum=5000)
        source = "supplied api_dump"
        classes = {c["name"]: c.get("superclass") for c in dump if isinstance(c, dict) and "name" in c}
        findings.append(chk("api_lookup.official_api", True, f"Lookup used {len(classes)} classes from the supplied dump."))
    matches = sorted(k for k in classes if query.casefold() in k.casefold())
    chains = {}
    for m in matches[:50]:
        chain, cur, guard = [], m, 0
        while cur and guard < 50:
            chain.append(cur)
            cur = classes.get(cur)
            guard += 1
        chains[m] = chain
    findings.append(chk("api_lookup.inheritance", True if matches else None,
                        f"Found {len(matches)} class match(es) with inheritance chains." if matches else f"No class name contains '{query}' in the {source}."))
    sensitive = [m for m in matches if m in ("HttpService", "MarketplaceService", "DataStoreService", "MessagingService", "TeleportService")]
    findings.append(chk("api_lookup.security", None if sensitive else True,
                        ("Sensitive service(s) matched: " + ", ".join(sensitive) + ". Use server-only access and review data exposure.") if sensitive else "No security-sensitive service matched."))
    return out({"query": query, "source": source, "matches": matches[:50], "inheritance": chains}, findings)


def h_asset_dependencies(d, cap, ctx):
    assets = core.array(d, "assets", required=True, kind=dict, maximum=5000)
    used = set(core.array(d, "used", maximum=5000, kind=str))
    graph = {}
    ids = set()
    findings = []
    for a in assets:
        aid = core.text(a, "id", maximum=120)
        if aid in ids:
            findings.append(chk("asset_dependencies.unique_ids", False, f"Duplicate asset id {aid}.", "fail"))
        ids.add(aid)
        graph[aid] = [x for x in a.get("depends_on", []) if isinstance(x, str)]
    missing = sorted({dep for deps in graph.values() for dep in deps if dep not in ids})
    findings.append(chk("asset_dependencies.references", not missing, "All declared dependencies exist." if not missing else "Missing dependencies: " + ", ".join(missing), "fail"))
    unused = sorted(ids - used)
    findings.append(chk("asset_dependencies.unused", None if unused else True, ("Unused assets (candidates for removal after review): " + ", ".join(unused[:50])) if unused else "Every asset is used."))
    missing_used = sorted(used - ids)
    findings.append(chk("asset_dependencies.used_resolved", not missing_used, "Every used asset is declared." if not missing_used else "Used but undeclared: " + ", ".join(missing_used), "fail"))
    try:
        order = core.graph_order(sorted(ids), [(a, b) for a, deps in graph.items() for b in deps if b in ids])
        findings.append(chk("asset_dependencies.cycles", True, "Dependency graph is acyclic."))
    except core.ToolError as exc:
        order = []
        findings.append(chk("asset_dependencies.cycles", False, exc.message, "fail"))
    return out({"load_order": order, "unused": unused, "missing": missing}, findings, {"asset-dependencies.json": {"graph": graph, "load_order": order}})


def h_architecture_diagram(d, cap, ctx):
    nodes = core.array(d, "nodes", required=True, kind=dict, maximum=200)
    edges = core.array(d, "edges", required=True, kind=dict, maximum=500)
    ids = {core.text(n, "id", maximum=60): core.text(n, "trust", maximum=20, required=False) or "unknown" for n in nodes}
    findings = []
    for e in edges:
        a, b = core.text(e, "from", maximum=60), core.text(e, "to", maximum=60)
        if a not in ids or b not in ids:
            findings.append(chk("architecture_diagram.edges", False, f"Edge {a}->{b} references an unknown node.", "fail"))
            continue
        if not e.get("label"):
            findings.append(chk("architecture_diagram.legend", None, f"Edge {a}->{b} has no label."))
        if ids[a] == "client" and ids[b] == "data":
            findings.append(chk("architecture_diagram.trust_boundary", False, f"Client {a} talks directly to data {b}; route through a server.", "fail"))
    layers = {}
    indeg = {n: 0 for n in ids}
    adj = {n: [] for n in ids}
    for e in edges:
        if e.get("from") in ids and e.get("to") in ids:
            adj[e["from"]].append(e["to"])
            indeg[e["to"]] += 1
    frontier = [n for n in ids if indeg[n] == 0]
    depth = {n: 0 for n in frontier}
    for n in frontier:
        pend = [n]
        while pend:
            cur = pend.pop(0)
            for nxt in adj[cur]:
                depth[nxt] = max(depth.get(nxt, 0), depth[cur] + 1)
                if depth[nxt] < 50:
                    pend.append(nxt)
    for n in ids:
        layers.setdefault(depth.get(n, 0), []).append(n)
    colors = {"client": "#cfe8ff", "server": "#d9f7d9", "data": "#fff1cc", "external": "#f3d9f7", "studio": "#eeeeee", "unknown": "#ffffff"}
    width, height, pad = 180, 56, 40
    positions = {}
    for level in sorted(layers):
        for i, n in enumerate(sorted(layers[level])):
            positions[n] = (pad + level * (width + pad), pad + i * (height + pad))
    W = max(x for x, _ in positions.values()) + width + pad
    H = max(y for _, y in positions.values()) + height + pad
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L9,3 L0,6 z" fill="#333"/></marker></defs>']
    for e in edges:
        if e.get("from") in positions and e.get("to") in positions:
            x1, y1 = positions[e["from"]]
            x2, y2 = positions[e["to"]]
            label = str(e.get("label", "")).replace("&", "and").replace("<", "").replace(">", "")[:60]
            svg.append(f'<line x1="{x1 + width}" y1="{y1 + height // 2}" x2="{x2}" y2="{y2 + height // 2}" stroke="#333" marker-end="url(#arrow)"/>')
            svg.append(f'<text x="{(x1 + x2) // 2 + width // 2}" y="{min(y1, y2) + height // 2 - 6}" font-size="11" text-anchor="middle">{label}</text>')
    for n, (x, y) in positions.items():
        t = ids[n]
        svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="6" fill="{colors.get(t, "#ffffff")}" stroke="#333"/>')
        svg.append(f'<text x="{x + width // 2}" y="{y + 24}" font-size="13" text-anchor="middle">{n}</text>')
        svg.append(f'<text x="{x + width // 2}" y="{y + 42}" font-size="10" text-anchor="middle" fill="#555">trust: {t}</text>')
    svg.append("</svg>")
    mermaid = ["flowchart LR"] + [f'  {n}["{n} ({ids[n]})"]' for n in ids] + [f'  {e["from"]} -->|{str(e.get("label", "")).replace(chr(34), "")}| {e["to"]}' for e in edges if e.get("from") in ids and e.get("to") in ids]
    findings.append(chk("architecture_diagram.trust_boundary", True if not any(f["id"] == "architecture_diagram.trust_boundary" for f in findings) else False,
                        "No client-to-data edge exists."))
    return out({"nodes": len(ids), "edges": len(edges), "layers": {str(k): v for k, v in layers.items()}}, findings,
               {"architecture.svg": "\n".join(svg) + "\n", "architecture.mmd": "\n".join(mermaid) + "\n"})


def h_packages(d, cap, ctx):
    packages = core.array(d, "packages", required=True, kind=dict, maximum=500)
    config_ext = core.boolean(d, "configuration_external", False)
    graph, owners, findings = {}, {}, []
    for p in packages:
        name = core.text(p, "name", maximum=80)
        graph[name] = [x for x in p.get("depends_on", []) if isinstance(x, str)]
        owners[name] = p.get("owner")
        if not owners[name]:
            findings.append(chk("packages.ownership", False, f"Package {name} has no owner.", "fail"))
    missing = sorted({x for deps in graph.values() for x in deps if x not in graph})
    findings.append(chk("packages.dependencies", not missing, "All package dependencies are declared." if not missing else "Undeclared dependencies: " + ", ".join(missing), "fail"))
    try:
        order = core.graph_order(sorted(graph), [(a, b) for a, deps in graph.items() for b in deps if b in graph])
        findings.append(chk("packages.layering", True, "Package dependency graph is acyclic."))
    except core.ToolError as exc:
        order = []
        findings.append(chk("packages.layering", False, exc.message, "fail"))
    findings.append(chk("packages.configuration", True if config_ext else False,
                        "Configuration is kept outside packages." if config_ext else "Packages embed configuration; move it to external configuration.", "warning"))
    return out({"load_order": order, "owners": owners}, findings)


def h_impact(d, cap, ctx):
    nodes = core.array(d, "nodes", required=True, kind=dict, maximum=2000)
    edges = core.array(d, "edges", required=True, maximum=5000)
    changed = core.array(d, "changed", required=True, kind=str, maximum=500)
    types = {core.text(n, "id", maximum=80): core.text(n, "type", maximum=40, required=False) or "unknown" for n in nodes}
    pairs = []
    for e in edges:
        a, b = (e["from"], e["to"]) if isinstance(e, dict) else (e[0], e[1])
        if a in types and b in types:
            pairs.append((b, a))  # reverse: dependency -> dependent
    affected = set()
    for c in changed:
        if c not in types:
            continue
        affected.add(c)
        affected.update(core.closure(pairs, c))
    affected_list = sorted(affected - set(changed))
    by_type = {}
    for a in affected_list:
        by_type.setdefault(types[a], []).append(a)
    findings = [chk("impact.unknown_changes", all(c in types for c in changed), "All changed nodes exist." if all(c in types for c in changed) else "Some changed nodes are unknown.", "fail")]
    sensitive = [t for t in ("remote", "data_schema", "place") if by_type.get(t)]
    findings.append(chk("impact.schema_and_remotes", None if sensitive else True,
                        ("Affected contract types: " + ", ".join(sensitive) + "; schedule compatibility review and migration.") if sensitive else "No remote, data schema or place is affected."))
    return out({"changed": changed, "affected": affected_list, "by_type": by_type}, findings)


def h_dependency_injection(d, cap, ctx):
    services = core.array(d, "services", required=True, kind=dict, maximum=300)
    graph, findings = {}, []
    for s in services:
        name = core.text(s, "name", maximum=80)
        graph[name] = [x for x in s.get("depends_on", []) if isinstance(x, str)]
    missing = sorted({x for deps in graph.values() for x in deps if x not in graph})
    findings.append(chk("dependency_injection.dependencies", not missing, "All dependencies are registered." if not missing else "Unregistered dependencies: " + ", ".join(missing), "fail"))
    try:
        order = core.graph_order(sorted(graph), [(a, b) for a, deps in graph.items() for b in deps if b in graph])
        findings.append(chk("dependency_injection.startup_order", True, "Startup order is deterministic and acyclic."))
    except core.ToolError as exc:
        order = []
        findings.append(chk("dependency_injection.startup_order", False, exc.message, "fail"))
    lines = ["--!strict", "-- Composition root generated by tools-roblox.py. Services are constructed in this order.", "local Container = {}"]
    for name in order:
        deps = ", ".join(f"Container[{lua_string(x)}]" for x in graph[name])
        lines.append(f"Container[{lua_string(name)}] = require(script.Parent.{name})({deps})")
    lines.append("return Container")
    findings.append(chk("dependency_injection.test_doubles", None, "Test doubles must be supplied by the test harness; they are not generated here."))
    return out({"startup_order": order, "services": len(graph)}, findings, {"Composition.luau": "\n".join(lines) + "\n"})


def h_checkpoints(d, cap, ctx):
    checkpoints = core.array(d, "checkpoints", required=True, maximum=500)
    touches = core.array(d, "touches", required=True, maximum=5000)
    max_jump = core.number(d, "max_jump", 10, 0.1, 500)
    inv = core.number(d, "invulnerability_seconds", 1, 0, 60)
    ids, pos = [], {}
    for c in checkpoints:
        if isinstance(c, dict):
            cid = core.text(c, "id", maximum=80)
            p = core.vector(c.get("position"), 3)
        else:
            cid = core.text({"v": c}, "v", maximum=80)
            p = None
        ids.append(cid)
        if p is not None:
            pos[cid] = p
    findings = []
    gaps = []
    for a, b in zip(ids, ids[1:]):
        if a in pos and b in pos:
            dist = sum((x - y) ** 2 for x, y in zip(pos[a], pos[b])) ** 0.5
            if dist > max_jump:
                gaps.append({"from": a, "to": b, "distance": round(dist, 3)})
    findings.append(chk("checkpoints.reachability", not gaps,
                        "Consecutive checkpoints are within max_jump." if not gaps else f"{len(gaps)} gap(s) exceed max_jump {max_jump}.", "fail" if gaps else "warning"))
    unknown = [t for t in touches if t not in ids]
    findings.append(chk("checkpoints.unknown_touches", not unknown, "All touches reference declared checkpoints." if not unknown else "Unknown checkpoints: " + ", ".join(map(str, unknown[:10])), "fail"))
    order_ok, last = True, -1
    for t in touches:
        if t in ids:
            idx = ids.index(t)
            if idx < last:
                order_ok = False
            last = max(last, idx)
    findings.append(chk("checkpoints.respawn", order_ok, "Checkpoint touches never move backwards." if order_ok else "Touches move backwards; respawn state must be restored from the latest checkpoint.", "warning"))
    findings.append(chk("checkpoints.anti_abuse", None, "Server must validate each touch against the player's last checkpoint; confirm in the server validation review."))
    return out({"checkpoints": ids, "gaps": gaps, "invulnerability_seconds": inv}, findings, {"checkpoints.json": {"ids": ids, "gaps": gaps}})


def h_quests(d, cap, ctx):
    quests = core.array(d, "quests", required=True, kind=dict, maximum=1000)
    progress = core.obj(d, "progress")
    completed = set(core.array(d, "completed", maximum=1000, kind=str))
    graph = {}
    for q in quests:
        graph[core.text(q, "id", maximum=80)] = [x for x in q.get("requires", []) if isinstance(x, str)]
    findings = []
    missing = sorted({x for deps in graph.values() for x in deps if x not in graph})
    findings.append(chk("quests.prerequisites", not missing, "All prerequisites exist." if not missing else "Missing prerequisites: " + ", ".join(missing), "fail"))
    try:
        order = core.graph_order(sorted(graph), [(a, b) for a, deps in graph.items() for b in deps if b in graph])
        findings.append(chk("quests.branching", True, "Quest prerequisites form an acyclic graph."))
    except core.ToolError as exc:
        order = []
        findings.append(chk("quests.branching", False, exc.message, "fail"))
    invalid = sorted(q for q in completed if any(p not in completed for p in graph.get(q, [])))
    findings.append(chk("quests.progress", not invalid, "Completed quests respect prerequisites." if not invalid else "Completed without prerequisites: " + ", ".join(invalid), "fail"))
    available = sorted(q for q in graph if q not in completed and all(p in completed for p in graph[q]))
    tracked = sorted(q for q in progress if q not in graph)
    findings.append(chk("quests.persistence", None if tracked else True, ("Progress for unknown quests: " + ", ".join(tracked)) if tracked else "All tracked progress maps to known quests."))
    findings.append(chk("quests.localization", None, "Quest titles and objectives need localization keys; not verified here."))
    return out({"available": available, "completed": sorted(completed), "order": order}, findings)


def h_crafting(d, cap, ctx):
    recipe = core.obj(d, "recipe", required=True)
    inventory = core.obj(d, "inventory")
    station = core.text(d, "station", maximum=80, required=False)
    findings = []
    ingredients = recipe.get("ingredients", {})
    if not isinstance(ingredients, dict) or not ingredients:
        findings.append(chk("crafting.recipe", False, "Recipe needs an ingredients object.", "fail"))
        ingredients = {}
    lacking = {k: v - inventory.get(k, 0) for k, v in ingredients.items() if inventory.get(k, 0) < v}
    needs_station = recipe.get("station")
    station_ok = not needs_station or needs_station == station
    craftable = not lacking and station_ok
    findings.append(chk("crafting.ingredients", not lacking, "Inventory has every ingredient." if not lacking else "Missing: " + ", ".join(f"{k} x{v}" for k, v in lacking.items()), "warning"))
    findings.append(chk("crafting.station", station_ok, "Station requirement met." if station_ok else f"Requires station {needs_station}.", "warning"))
    findings.append(chk("crafting.server_outputs", None, "The server must consume ingredients and grant the output in one transaction."))
    return out({"recipe": recipe.get("id"), "craftable": craftable, "lacking": lacking, "station_ok": station_ok}, findings)


def h_content_browser(d, cap, ctx):
    assets = core.array(d, "assets", required=True, kind=dict, maximum=5000)
    query = core.obj(d, "query")
    findings, rows = [], []
    paths = {core.text(a, "path", maximum=300) for a in assets}
    for a in assets:
        if (query.get("type") and a.get("type") != query["type"]) or (query.get("text") and query["text"].casefold() not in a["path"].casefold()):
            continue
        rows.append({"path": a["path"], "type": a.get("type"), "dependencies": a.get("deps", [])})
    broken = sorted({dep for a in assets for dep in a.get("deps", []) if dep not in paths})
    findings.append(chk("content_browser.dependencies", not broken, "All dependencies are indexed." if not broken else "Unindexed dependencies: " + ", ".join(broken[:20]), "warning"))
    findings.append(chk("content_browser.ownership", None, "Ownership comes from asset-dependency records; not inferred from path names."))
    return out({"matches": rows, "count": len(rows)}, findings)


def h_ownership(d, cap, ctx):
    paths = core.obj(d, "paths", required=True)
    owners = core.obj(d, "owners", required=True)
    incident = core.text(d, "incident_owner", maximum=80)
    findings, rows = [], {}
    for path, owner in paths.items():
        rows[path] = owner
    unowned = []
    for path in paths:
        if paths[path] not in owners:
            unowned.append(path)
    findings.append(chk("ownership.assignments", not unowned, "Every path maps to a declared owner." if not unowned else "Owners not declared: " + ", ".join(unowned), "fail"))
    findings.append(chk("ownership.incident", incident in owners, "Incident owner is declared." if incident in owners else "Incident owner is not declared.", "fail"))
    roles = {k: v for k, v in owners.items()}
    return out({"assignments": rows, "owners": roles, "incident_owner": incident}, findings)


def h_policy_impact(d, cap, ctx):
    title = core.text(d, "policy_title", maximum=200)
    source = core.text(d, "source", maximum=500)
    effective = core.text(d, "effective_date", maximum=20)
    affected = core.array(d, "affected", required=True, kind=dict, maximum=200)
    verified = core.boolean(d, "verified", False)
    findings = [chk("policy_impact.source", bool(re.match(r"^https://", source)) or len(source) > 0,
                    "Source evidence supplied; verify the official page before acting." if source else "A source is required.", "fail"),
                chk("policy_impact.verified", verified, "Operator has verified the policy text." if verified else "Policy text is not verified; treat this impact map as a draft.", "warning")]
    rows = []
    for a in affected:
        rows.append({"system": core.text(a, "system", maximum=120), "touches": a.get("touches", []), "owner": a.get("owner")})
    findings.append(chk("policy_impact.owners", None if any(not r["owner"] for r in rows) else True,
                        "Some affected systems have no owner." if any(not r["owner"] for r in rows) else "Every affected system has an owner."))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective):
        findings.append(chk("policy_impact.effective_date", False, "effective_date must be YYYY-MM-DD.", "fail"))
    return out({"policy": title, "effective_date": effective, "impacts": rows}, findings,
               {"policy-impact.json": {"policy": title, "effective_date": effective, "impacts": rows, "verified": verified}})


def h_workflow_cli(d, cap, ctx):
    steps = core.array(d, "steps", required=True, kind=dict, maximum=200)
    require = core.boolean(d, "require_approval", True)
    env = core.text(d, "environment", maximum=40)
    ids = [core.text(s, "id", maximum=60) for s in steps]
    graph = {core.text(s, "id", maximum=60): [x for x in s.get("needs", []) if isinstance(x, str)] for s in steps}
    findings = []
    missing = sorted({x for deps in graph.values() for x in deps if x not in graph})
    findings.append(chk("validation_pipeline.dependencies", not missing, "All step dependencies exist." if not missing else "Missing steps: " + ", ".join(missing), "fail"))
    try:
        order = core.graph_order(sorted(graph), [(a, b) for a, deps in graph.items() for b in deps if b in graph])
    except core.ToolError as exc:
        order = []
        findings.append(chk("validation_pipeline.order", False, exc.message, "fail"))
    risky = [s for s in steps if re.search(r"publish|deploy|delete|revoke|purchase", str(s.get("command", "")), re.I)]
    unapproved = [s["id"] for s in risky if not s.get("approval") and require]
    findings.append(chk("validation_pipeline.approvals", not unapproved, "Every risky step requires approval." if not unapproved else "Risky steps lack approval: " + ", ".join(unapproved), "fail"))
    findings.append(chk("validation_pipeline.environment", env in ("development", "staging", "canary", "production"),
                        f"Environment {env} is a known channel." if env in ("development", "staging", "canary", "production") else "Environment must be development, staging, canary or production.", "fail"))
    plan = {"environment": env, "order": order, "steps": [{"id": s["id"], "command": s.get("command"), "approval": bool(s.get("approval")), "risky": s in risky} for s in steps]}
    findings.append(chk("validation_pipeline.execution", None, "Commands are listed for review and are not executed by this tool."))
    return out(plan, findings, {"workflow-plan.json": plan})


def h_validation_pipeline(d, cap, ctx):
    project = core.obj(d, "project", required=True)
    required = core.array(d, "required_checks", required=True, kind=str, maximum=100)
    checks = project.get("checks", {})
    findings, rows = [], []
    for name in required:
        status = checks.get(name)
        rows.append({"check": name, "status": status or "not_run"})
        if status is None:
            findings.append(chk(f"validation_pipeline.{core.safe_name(name)}", None, f"Check '{name}' has no recorded result; run it before release."))
        elif status is True or status == "pass":
            findings.append(chk(f"validation_pipeline.{core.safe_name(name)}", True, f"Check '{name}' passed."))
        else:
            findings.append(chk(f"validation_pipeline.{core.safe_name(name)}", False, f"Check '{name}' did not pass.", "fail"))
    return out({"checks": rows}, findings)


def h_rojo(d, cap, ctx):
    name = core.text(d, "name", maximum=80)
    source_dirs = core.array(d, "source_dirs", required=True, kind=str, maximum=50)
    mapping = {"src/shared": "ReplicatedStorage/Shared", "src/server": "ServerScriptService/Server", "src/client": "StarterPlayer/StarterPlayerScripts/Client", "src/server-storage": "ServerStorage"}
    findings = [chk("rojo.paths", all(not s.startswith(("/", "..")) and ".." not in s for s in source_dirs), "Source directories are relative and contain no traversal.", "fail")]
    rows = []
    for s in source_dirs:
        rows.append({"source": s, "container": mapping.get(s.rstrip("/"), "unmapped")})
    unmapped = [r["source"] for r in rows if r["container"] == "unmapped"]
    findings.append(chk("rojo.mapping", None if unmapped else True, ("Unmapped directories: " + ", ".join(unmapped)) if unmapped else "All directories map to known containers."))
    project = {"name": name, "tree": {"$className": "DataModel", "ReplicatedStorage": {"$path": "src/shared"}, "ServerScriptService": {"$path": "src/server"}}}
    findings.append(chk("rojo.generated_files", None, "Generated Roblox files must not be hand-edited; keep the project file as the single mapping source."))
    return out({"name": name, "mapping": rows}, findings, {"default.project.json": project})


def h_git_workflow(d, cap, ctx):
    branch = core.text(d, "branch", maximum=80)
    reviewers = core.array(d, "reviewers", required=True, kind=str, maximum=50)
    fmt = core.text(d, "source_format", maximum=40)
    binary = core.text(d, "binary_asset_policy", maximum=120)
    prefix = core.text(d, "release_prefix", maximum=20)
    findings = [chk("git_workflow.reviews", len(reviewers) >= 2, "At least two reviewers are required." if len(reviewers) >= 2 else "Only one reviewer; add a second for critical areas.", "warning"),
                chk("git_workflow.binary_assets", None if "lfs" not in binary.lower() and "lock" not in binary.lower() else True, "Binary policy names LFS or locking." if ("lfs" in binary.lower() or "lock" in binary.lower()) else "Binary asset handling must use LFS or file locking; state the policy explicitly."),
                chk("git_workflow.tags", bool(re.fullmatch(r"v?", prefix)) or prefix.startswith("v"), "Release tags use a stable prefix."),
                chk("git_workflow.branch", branch not in ("", "HEAD"), "Protected branch name is set.")]
    text = f"# Git workflow\n\n- Protected branch: `{branch}`\n- Reviewers: {', '.join(reviewers)}\n- Source format: {fmt}\n- Binary policy: {binary}\n- Release tag prefix: `{prefix}`\n\n## Rules\n\n1. Work on feature branches; open a pull request into `{branch}`.\n2. Require the reviewers above before merge.\n3. Keep place files (.rbxl/.rbxlx) as binary artifacts under the binary policy.\n4. Tag releases as `{prefix}<version>`.\n"
    return out({"branch": branch, "reviewers": reviewers, "source_format": fmt}, findings, {"git-workflow.md": text})


# ---------------------------------------------------------------- schema

ALLOWED_FIELD_TYPES = {"number", "string", "boolean", "integer", "Vector3", "Color3", "CFrame", "Instance", "table"}
TYPE_CHECK = {
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "Vector3": lambda v: isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v),
    "Color3": lambda v: isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in v),
    "CFrame": lambda v: isinstance(v, list) and len(v) == 12,
    "Instance": lambda v: isinstance(v, str),
    "table": lambda v: isinstance(v, (dict, list)),
}


def h_attributes(d, cap, ctx):
    schema = core.obj(d, "schema", required=True)
    values = core.obj(d, "values", required=True)
    findings, normalized, rows = [], {}, []
    for name, spec in schema.items():
        if not luau_name(name):
            findings.append(chk("attributes.names", False, f"Attribute name {name} is not a Luau identifier.", "fail"))
        typ = spec.get("type")
        if typ not in ALLOWED_FIELD_TYPES:
            findings.append(chk("attributes.types", False, f"Attribute {name} has unsupported type {typ}.", "fail"))
            continue
        if "default" in spec:
            if not TYPE_CHECK[typ](spec["default"]):
                findings.append(chk("attributes.defaults", False, f"Default for {name} does not match {typ}.", "fail"))
        if name in values:
            value = values[name]
            if not TYPE_CHECK[typ](value):
                findings.append(chk("attributes.types", False, f"Value for {name} is not a {typ}.", "fail"))
                continue
            if "minimum" in spec and value < spec["minimum"]:
                findings.append(chk("attributes.ranges", False, f"{name} is below minimum {spec['minimum']}.", "fail"))
            if "maximum" in spec and value > spec["maximum"]:
                findings.append(chk("attributes.ranges", False, f"{name} is above maximum {spec['maximum']}.", "fail"))
            if "enum" in spec and value not in spec["enum"]:
                findings.append(chk("attributes.types", False, f"{name} is not an allowed value.", "fail"))
            normalized[name] = value
        elif "default" in spec:
            normalized[name] = spec["default"]
        else:
            findings.append(chk("attributes.defaults", None, f"{name} has no value and no default."))
        rows.append({"name": name, "type": typ})
    for name in values:
        if name not in schema:
            findings.append(chk("attributes.unknown", False, f"Value {name} is not in the schema.", "fail"))
    findings.append(chk("attributes.version", None if "__version" not in schema else True,
                        "Add a __version attribute for migrations." if "__version" not in schema else "Schema declares a version attribute."))
    luau = ["--!strict", "-- Attribute validator generated by tools-roblox.py. Review before use.", "local AttributeSchema = {}", ""]
    for name, spec in schema.items():
        if spec.get("type") in ALLOWED_FIELD_TYPES:
            typ = spec["type"]
            lua_type = {"integer": "number", "table": "any", "Vector3": "Vector3", "Color3": "Color3", "CFrame": "CFrame", "Instance": "Instance"}.get(typ, typ)
            luau.append(f"AttributeSchema[{lua_string(name)}] = {{ type = {lua_string(lua_type)}, minimum = {core.lua(spec.get('minimum'))}, maximum = {core.lua(spec.get('maximum'))} }}")
    luau += ["", "function AttributeSchema.validate(instance: Instance): (boolean, string?)", "\tfor name, rule in AttributeSchema do", "\t\tlocal value = instance:GetAttribute(name)",
             "\t\tif value == nil then", "\t\t\tcontinue", "\t\tend", "\t\tif rule.type == \"Vector3\" and typeof(value) ~= \"Vector3\" then return false, name end",
             "\t\tif rule.type == \"Color3\" and typeof(value) ~= \"Color3\" then return false, name end",
             "\t\tif rule.type == \"number\" and typeof(value) ~= \"number\" then return false, name end",
             "\t\tif rule.type == \"string\" and typeof(value) ~= \"string\" then return false, name end",
             "\t\tif rule.type == \"boolean\" and typeof(value) ~= \"boolean\" then return false, name end",
             "\t\tif rule.minimum and value < rule.minimum then return false, name end",
             "\t\tif rule.maximum and value > rule.maximum then return false, name end", "\tend", "\treturn true, nil", "end", "", "return AttributeSchema", ""]
    return out({"normalized": normalized, "fields": rows}, findings, {"AttributeSchema.luau": "\n".join(luau)})


def h_remote_contract(d, cap, ctx):
    name = core.text(d, "name", maximum=80)
    direction = core.text(d, "direction", maximum=30)
    version = core.integer(d, "version", 1, 1, 1000)
    fields = core.obj(d, "fields", required=True)
    rate = core.number(d, "rate", 1, 0, 1000)
    burst = core.number(d, "burst", 1, 0, 1000)
    auth = core.text(d, "authorization", maximum=300, required=False) or ""
    findings = [
        chk("remote_contract.name", luau_name(name) and name.endswith(("Event", "Request", "Function", "Remote")) or luau_name(name),
            "Remote name is a valid identifier." if luau_name(name) else "Remote name must be a valid identifier.", "fail"),
        chk("remote_contract.direction", direction in ("client_to_server", "server_to_client"), "Direction is explicit." if direction in ("client_to_server", "server_to_client") else "Direction must be client_to_server or server_to_client.", "fail"),
        chk("remote_contract.fields", all(t in ALLOWED_FIELD_TYPES for t in fields.values()), "All payload field types are supported." if all(t in ALLOWED_FIELD_TYPES for t in fields.values()) else "A field type is unsupported.", "fail"),
        chk("remote_contract.rate", rate <= burst or burst == 0 or rate > 0, "Rate and burst are configured." if rate > 0 else "A rate of zero blocks all requests.", "warning"),
    ]
    if direction == "client_to_server":
        findings.append(chk("remote_contract.authorization", bool(auth), "Authorization rule is stated for a client request." if auth else "Client-to-server remotes must state an authorization rule.", "fail"))
    findings.append(chk("remote_contract.error_behavior", None, "Error replies must not reveal server internals; confirm the response contract."))
    findings.append(chk("remote_contract.versioning", True, f"Contract version is {version}."))
    typedef = [f"export type {name}Payload = {{"]
    for f, t in fields.items():
        typedef.append(f"\t{f}: {({'integer': 'number', 'table': 'any'}).get(t, t)},")
    typedef += ["}", "", f"local {name}Contract = {{ name = {lua_string(name)}, version = {version}, direction = {lua_string(direction)}, rate = {rate}, burst = {burst} }}",
                f"function {name}Contract.validate(payload: any): boolean", "\tif typeof(payload) ~= \"table\" then return false end"]
    for f, t in fields.items():
        check = {"number": "number", "integer": "number", "string": "string", "boolean": "boolean", "Vector3": "Vector3", "Color3": "Color3", "CFrame": "CFrame", "Instance": "Instance", "table": "table"}[t]
        typedef.append(f"\tif typeof(payload[{lua_string(f)}]) ~= {lua_string(check)} then return false end")
    typedef += ["\treturn true", "end", "", f"return {name}Contract", ""]
    return out({"name": name, "direction": direction, "version": version, "fields": fields, "rate": rate, "burst": burst, "authorization": auth},
               findings, {f"{name}Contract.luau": "--!strict\n-- Generated contract with a payload validator. Reject invalid payloads before any state change.\n" + "\n".join(typedef) + "\n"})


def h_protocol(d, cap, ctx):
    current = core.integer(d, "current_version", 1, 1, 10000)
    payload = core.obj(d, "payload", required=True)
    migrations = core.array(d, "migrations", required=True, kind=dict, maximum=200)
    version = payload.get("version")
    fields = dict(payload.get("fields", {}))
    findings = []
    if not isinstance(version, int) or isinstance(version, bool):
        findings.append(chk("protocol.version", False, "Payload version must be an integer.", "fail"))
        return out({"accepted": False}, findings)
    if version > current:
        findings.append(chk("protocol.newer_versions", False, f"Payload v{version} is newer than server v{current}; reject it.", "fail"))
        return out({"accepted": False, "version": version}, findings)
    steps, v = [], version
    by_from = {m["from"]: m for m in migrations}
    while v < current:
        m = by_from.get(v)
        if m is None:
            findings.append(chk("protocol.migration_path", False, f"No migration from v{v}.", "fail"))
            return out({"accepted": False, "version": version}, findings)
        for old, new in m.get("rename", {}).items():
            if old in fields:
                fields[new] = fields.pop(old)
        for k, val in m.get("defaults", {}).items():
            fields.setdefault(k, val)
        steps.append(f"v{v}->v{m['to']}")
        v = m["to"]
    findings.append(chk("protocol.migration_path", True, "Migration path reaches the current version." if version < current else "Payload is already the current version."))
    findings.append(chk("protocol.backward_compat", None, "Old clients must keep working during rollout; confirm the deprecation window."))
    return out({"accepted": True, "from_version": version, "to_version": current, "steps": steps, "fields": fields}, findings)


def h_profile_schema(d, cap, ctx):
    defaults = core.obj(d, "defaults", required=True)
    profile = core.obj(d, "profile", required=True)
    version = core.integer(d, "version", 1, 0, 10000)
    current = defaults.get("version")
    findings = []
    if current is None:
        findings.append(chk("profile.version_field", False, "defaults must include a version field.", "fail"))
    elif version > current:
        findings.append(chk("profile.newer_schema", False, f"Profile schema v{version} is newer than the code's v{current}; refuse to load.", "fail"))
    normalized = copy.deepcopy(defaults)
    for key, val in profile.items():
        if key not in defaults:
            findings.append(chk("profile.unknown_fields", None, f"Unknown field {key} is kept without being trusted; review it for removal."))
            normalized[key] = val
            continue
        expected = defaults[key]
        if type(expected) is not type(val) and not (isinstance(expected, (int, float)) and isinstance(val, (int, float)) and not isinstance(val, bool)):
            findings.append(chk("profile.types", False, f"Field {key} has type {type(val).__name__}; default is {type(expected).__name__}.", "fail"))
            normalized[key] = expected
        else:
            normalized[key] = val
    findings.append(chk("profile.defaults", True, "Missing fields are filled from defaults."))
    findings.append(chk("profile.version_field", True if current is not None else False, "Schema version is present in defaults."))
    lines = ["--!strict", "-- Profile defaults and version. Migrations run in a server-owned transform; see migration tools.", "local Schema = {}", f"Schema.Version = {core.lua(current)}", f"Schema.Defaults = {core.lua(defaults)}", "return Schema", ""]
    return out({"normalized": normalized, "version": version, "current_version": current}, findings, {"ProfileSchema.luau": "\n".join(lines)})


def h_event_taxonomy(d, cap, ctx):
    events = core.array(d, "events", required=True, kind=dict, maximum=500)
    forbidden = set(core.array(d, "forbidden_fields", maximum=200, kind=str))
    findings, names, rows = [], set(), []
    for e in events:
        name = core.text(e, "name", maximum=80)
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*", name):
            findings.append(chk("event_taxonomy.names", False, f"Event {name} must be lower_snake_case with optional dots.", "fail"))
        if name in names:
            findings.append(chk("event_taxonomy.names", False, f"Duplicate event {name}.", "fail"))
        names.add(name)
        props = e.get("properties", {})
        bad = [p for p in props if p in forbidden or re.search(r"email|phone|birth|address|ip_?address|password", p, re.I)]
        if bad:
            findings.append(chk("event_taxonomy.privacy", False, f"Event {name} collects forbidden fields: {', '.join(bad)}.", "fail"))
        rows.append({"event": name, "properties": sorted(props.keys())})
    findings.append(chk("event_taxonomy.funnels", None, "Funnel definitions must reference these event names; verify with the funnel analysis capability."))
    md = ["# Analytics event taxonomy", "", "| Event | Properties |", "|---|---|"] + [f"| `{r['event']}` | {', '.join(r['properties'])} |" for r in rows]
    return out({"events": rows, "forbidden_fields": sorted(forbidden)}, findings, {"event-taxonomy.md": "\n".join(md) + "\n"})


def h_remote_config(d, cap, ctx):
    schema = core.obj(d, "schema", required=True)
    values = core.obj(d, "values", required=True)
    version = core.integer(d, "version", 1, 0, 100000)
    previous = core.obj(d, "previous")
    prev_version = previous.get("version", 0) if isinstance(previous, dict) else 0
    findings, normalized, changes = [], {}, []
    for key, spec in schema.items():
        if key not in values:
            if "default" in spec:
                normalized[key] = spec["default"]
            else:
                findings.append(chk("remote_config.missing", False, f"{key} has no value and no default.", "fail"))
            continue
        value = values[key]
        typ = spec.get("type")
        if typ in TYPE_CHECK and not TYPE_CHECK[typ](value):
            findings.append(chk("remote_config.types", False, f"{key} is not a {typ}.", "fail"))
            continue
        if "minimum" in spec and value < spec["minimum"]:
            findings.append(chk("remote_config.ranges", False, f"{key} is below {spec['minimum']}.", "fail"))
            continue
        if "maximum" in spec and value > spec["maximum"]:
            findings.append(chk("remote_config.ranges", False, f"{key} is above {spec['maximum']}.", "fail"))
            continue
        normalized[key] = value
    for key in values:
        if key not in schema:
            findings.append(chk("remote_config.unknown", False, f"Unknown config key {key}.", "fail"))
    for key in sorted(set(normalized) | set(previous) - {"version"}):
        if normalized.get(key) != previous.get(key):
            changes.append({"key": key, "from": previous.get(key), "to": normalized.get(key)})
    findings.append(chk("remote_config.version", version > prev_version, f"Version {version} is newer than {prev_version}." if version > prev_version else f"Version {version} must be greater than {prev_version}.", "fail"))
    findings.append(chk("remote_config.rollback", None if changes else True, "Keep the previous version available for rollback." if changes else "No value changed."))
    return out({"version": version, "normalized": normalized, "changes": changes}, findings, {"remote-config.json": {"version": version, "values": normalized}})


def h_catalog_maintenance(d, cap, ctx):
    catalog = copy.deepcopy(core.obj(d, "catalog", required=True))
    ops = core.array(d, "operations", required=True, kind=dict, maximum=500)
    caps = catalog.setdefault("capabilities", [])
    by_id = {c["id"]: c for c in caps}
    findings, applied = [], []
    for op in ops:
        kind = core.text(op, "op", maximum=20)
        cid = core.text(op, "id", maximum=120, required=False) or ""
        if kind == "add":
            if cid in by_id or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cid):
                findings.append(chk("catalog_maintenance.unique_ids", False, f"Cannot add {cid}: id is duplicate or not kebab-case.", "fail"))
                continue
            entry = {"id": cid, "name": core.text(op, "name", maximum=160), "description": core.text(op, "description", maximum=2000), "tags": op.get("tags", []), "deprecated": False}
            caps.append(entry)
            by_id[cid] = entry
            applied.append({"op": "add", "id": cid})
        elif kind == "deprecate":
            if cid not in by_id:
                findings.append(chk("catalog_maintenance.references", False, f"Cannot deprecate unknown {cid}.", "fail"))
                continue
            by_id[cid]["deprecated"] = True
            applied.append({"op": "deprecate", "id": cid, "replacement": op.get("replacement")})
        elif kind == "tag":
            if cid not in by_id:
                findings.append(chk("catalog_maintenance.references", False, f"Cannot tag unknown {cid}.", "fail"))
                continue
            by_id[cid].setdefault("tags", [])
            for t in op.get("tags", []):
                if t not in by_id[cid]["tags"]:
                    by_id[cid]["tags"].append(t)
            applied.append({"op": "tag", "id": cid})
        elif kind == "merge":
            into = core.text(op, "into", maximum=120)
            if cid not in by_id or into not in by_id:
                findings.append(chk("catalog_maintenance.references", False, f"Merge {cid} into {into} needs both ids.", "fail"))
                continue
            by_id[cid]["deprecated"] = True
            by_id[cid]["merged_into"] = into
            applied.append({"op": "merge", "id": cid, "into": into})
        else:
            findings.append(chk("catalog_maintenance.operations", False, f"Unknown operation {kind}.", "fail"))
    ids = [c["id"] for c in caps]
    findings.append(chk("catalog_maintenance.unique_ids", len(ids) == len(set(ids)), "Capability ids are unique." if len(ids) == len(set(ids)) else "Duplicate ids remain.", "fail"))
    empty = [c["id"] for c in caps if not str(c.get("description", "")).strip()]
    findings.append(chk("catalog_maintenance.descriptions", not empty, "Every capability has a description." if not empty else "Empty descriptions: " + ", ".join(empty[:20]), "fail"))
    return out({"applied": applied, "count": len(caps), "version": catalog.get("version")}, findings, {"catalog.updated.json": catalog})


def h_environments(d, cap, ctx):
    envs = core.obj(d, "environments", required=True)
    shared = core.array(d, "shared_secret_refs", maximum=200, kind=str)
    findings, rows, seen_ids = [], [], {}
    for name, cfg in envs.items():
        if not isinstance(cfg, dict):
            findings.append(chk("environments.shape", False, f"{name} must be an object.", "fail"))
            continue
        for k, v in cfg.items():
            if isinstance(v, str) and re.search(r"(?i)(api[_-]?key|secret|token|password)", k) and not v.startswith("env:"):
                findings.append(chk("environments.secrets", False, f"{name}.{k} holds a literal secret-like value; use an env: reference instead.", "fail"))
        uid = cfg.get("universe_id")
        if uid is not None:
            if uid in seen_ids:
                findings.append(chk("environments.identifiers", False, f"{name} and {seen_ids[uid]} share universe_id {uid}; staging must not write to production data.", "fail"))
            seen_ids[uid] = name
        rows.append({"environment": name, "features": sorted(cfg.get("flags", {}).keys()) if isinstance(cfg.get("flags"), dict) else []})
    if "production" not in envs:
        findings.append(chk("environments.production", None, "No production environment declared; intentional?"))
    findings.append(chk("environments.secret_refs", None if shared else True, "Shared secret references are listed; verify each exists in the platform secret store." if shared else "No shared secret references."))
    return out({"environments": rows, "shared_secret_refs": shared}, findings)


def h_channels(d, cap, ctx):
    envs = core.array(d, "environments", required=True, kind=str, maximum=10)
    src, dst = core.text(d, "from", maximum=40), core.text(d, "to", maximum=40)
    tests = core.boolean(d, "tests_passed", False)
    order = ["development", "staging", "canary", "production"]
    findings = [chk("channels.known", all(e in order for e in envs), "Channels use known names." if all(e in order for e in envs) else "Channels must be development, staging, canary or production.", "fail")]
    if src in order and dst in order:
        adjacent = order.index(dst) - order.index(src) == 1
        findings.append(chk("channels.promotion_path", adjacent, "Promotion moves one channel forward." if adjacent else "Promotion must move one channel forward at a time.", "fail"))
    else:
        findings.append(chk("channels.promotion_path", False, "Unknown from/to channel.", "fail"))
    findings.append(chk("channels.tests", tests, "Tests passed for this promotion." if tests else "Promotion blocked until tests pass.", "fail"))
    if dst == "production":
        findings.append(chk("channels.production_approval", None, "Production promotion needs explicit human approval; this tool never publishes."))
    decision = "promote" if all(f["status"] == "pass" for f in findings) else ("review" if dst == "production" else "hold")
    return out({"from": src, "to": dst, "decision": decision}, findings)


def h_server_validation(d, cap, ctx):
    findings = []
    value = d.get("value")
    schema = core.obj(d, "schema", required=True)
    ranges = core.obj(d, "ranges")
    typ = schema.get("type")
    ok_type = typ in TYPE_CHECK and TYPE_CHECK[typ](value)
    findings.append(chk("server_validation.type", ok_type, "Value matches the expected type." if ok_type else "Value has the wrong type; reject.", "fail"))
    if ok_type and isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = ranges.get("min", schema.get("minimum", -float("inf"))), ranges.get("max", schema.get("maximum", float("inf")))
        in_range = lo <= value <= hi
        findings.append(chk("server_validation.range", in_range, "Value is within the allowed range." if in_range else f"Value {value} is outside [{lo}, {hi}].", "fail"))
    findings.append(chk("server_validation.ownership", bool(d.get("owner_matches")), "Requester owns the target." if d.get("owner_matches") else "Requester does not own the target; reject.", "fail"))
    dist, maxd = d.get("distance"), d.get("max_distance")
    if dist is not None and maxd is not None:
        findings.append(chk("server_validation.proximity", dist <= maxd, "Within interaction distance." if dist <= maxd else f"Distance {dist} exceeds {maxd}.", "fail"))
    allowed = d.get("allowed_states", [])
    state = d.get("state")
    findings.append(chk("server_validation.state", state in allowed if allowed else None, "State allows this action." if (state in allowed) else "Action is not allowed in the current state.", "fail"))
    findings.append(chk("server_validation.entitlement", bool(d.get("entitled", True)), "Entitlement check passed." if d.get("entitled", True) else "Entitlement missing; reject.", "fail"))
    accepted = all(f["status"] != "fail" for f in findings) and all(f["status"] != "review" for f in findings)
    return out({"accepted": accepted, "decision": "accept" if accepted else "reject"}, findings)


def h_regression(d, cap, ctx):
    cases = core.array(d, "cases", required=True, kind=dict, maximum=2000)
    ids = [core.text(c, "id", maximum=80) for c in cases]
    findings = [chk("regression.unique_ids", len(ids) == len(set(ids)), "Regression ids are unique." if len(ids) == len(set(ids)) else "Duplicate regression ids.", "fail")]
    missing = [c["id"] for c in cases if "expected" not in c or not c.get("bug")]
    findings.append(chk("regression.expected", not missing, "Every case states the bug and expected result." if not missing else "Cases missing bug or expected: " + ", ".join(missing[:10]), "fail"))
    registry = [{"id": c["id"], "bug": c.get("bug"), "input": c.get("input"), "expected": c.get("expected"), "test_file": c.get("test_file")} for c in cases]
    return out({"cases": len(cases), "registry": registry}, findings, {"regression-registry.json": registry})


def h_edge_cases(d, cap, ctx):
    name = core.text(d, "name", maximum=80)
    typ = core.text(d, "type", maximum=20)
    required = core.boolean(d, "required", False)
    lo, hi = d.get("minimum"), d.get("maximum")
    cases = []
    if typ == "number":
        base = [0, -1, 1]
        for v in ([lo, hi] if lo is not None and hi is not None else []):
            base += [v, v - 1 if isinstance(v, (int, float)) else v, v + 1 if isinstance(v, (int, float)) else v]
        cases = [{"value": x, "expect": "valid" if (lo is None or x >= lo) and (hi is None or x <= hi) else "invalid"} for x in dict.fromkeys(base)]
    elif typ == "string":
        cases = [{"value": "", "expect": "invalid" if required else "valid"}, {"value": "a" * 300, "expect": "review"}, {"value": "  spaced  ", "expect": "valid"}, {"value": "\u00e9\u00e8\u00e0", "expect": "valid"}]
    else:
        core.text({"t": typ}, "t", maximum=16)
        raise core.ToolError("INPUT_INVALID", "type must be number or string.", {"type": typ}, status=2)
    if required:
        cases.append({"value": None, "expect": "invalid"})
    findings = [chk("edge_cases.coverage", len(cases) >= 3, f"Generated {len(cases)} edge cases for {name}."),
                chk("edge_cases.nulls", None if not required else True, "Missing required values are included as invalid cases." if required else "Field is optional; null handling needs a decision.")]
    return out({"name": name, "cases": cases}, findings, {"edge-cases.json": {"name": name, "cases": cases}})


def h_package_update(d, cap, ctx):
    old, new = core.text(d, "old_version", maximum=40), core.text(d, "new_version", maximum=40)
    old_api, new_api = core.obj(d, "old_api"), core.obj(d, "new_api")
    desc = core.array(d, "modified_descendants", maximum=2000, kind=str)
    rollback = core.text(d, "rollback_version", maximum=40, required=False) or ""
    removed = sorted(k for k in old_api if k not in new_api)
    changed = sorted(k for k in old_api if k in new_api and old_api[k] != new_api[k])
    added = sorted(k for k in new_api if k not in old_api)
    findings = [chk("package_update.breaking_api", None if removed or changed else True,
                    f"Breaking API changes: removed {removed or 'none'}, changed {changed or 'none'}." if removed or changed else "No removed or changed API members.")]
    findings.append(chk("package_update.modified_descendants", None if desc else True,
                        "Modified descendants will be overwritten or conflict; review each one before updating." if desc else "No locally modified descendants reported."))
    findings.append(chk("package_update.rollback", bool(rollback) and rollback != new, "A rollback version is recorded." if rollback and rollback != new else "Record a known-good rollback version.", "fail"))
    notes = [f"# Package {old} -> {new}", "", f"- Removed API: {', '.join(removed) or 'none'}", f"- Changed API: {', '.join(changed) or 'none'}", f"- Added API: {', '.join(added) or 'none'}", f"- Modified descendants: {', '.join(desc) or 'none'}", f"- Rollback version: {rollback or 'not set'}", ""]
    return out({"removed": removed, "changed": changed, "added": added, "modified_descendants": desc, "rollback_version": rollback}, findings, {"package-update.md": "\n".join(notes)})


# ---------------------------------------------------------------- migration

def h_migration(d, cap, ctx):
    records = core.array(d, "records", required=True, kind=dict, maximum=ctx.config["limits"]["batch_capabilities"] * 100)
    from_v = core.integer(d, "from_version", 1, 0, 10000)
    to_v = core.integer(d, "to_version", 2, 0, 10000)
    defaults = core.obj(d, "defaults", required=True)
    fail_keys = set(core.array(d, "fail_keys", maximum=1000, kind=str))
    checkpoint = core.obj(d, "checkpoint")
    findings, migrated, backups, status = [], [], [], {}
    if to_v <= from_v:
        findings.append(chk("migration.direction", False, "to_version must be greater than from_version.", "fail"))
    done = {k for k, v in checkpoint.items() if v == "done"}
    for rec in sorted(records, key=lambda r: str(r.get("key"))):
        ctx.check()
        key = str(rec.get("key"))
        data = rec.get("data", {})
        if key in done:
            status[key] = "skipped_done"
            continue
        if not isinstance(data, dict):
            status[key] = "failed_invalid"
            findings.append(chk("migration.records", False, f"Record {key} is not an object.", "fail"))
            continue
        version = data.get("version", from_v)
        if isinstance(version, int) and version > to_v:
            status[key] = "rejected_newer"
            findings.append(chk("migration.newer_versions", False, f"Record {key} is at v{version}, newer than target v{to_v}.", "fail"))
            continue
        if key in fail_keys:
            status[key] = "failed_retryable"
            findings.append(chk("migration.partial_failures", None, f"Record {key} failed; it stays unchanged and is retried on resume."))
            continue
        backups.append({"key": key, "data": copy.deepcopy(data)})
        new = copy.deepcopy(data)
        for k, val in defaults.items():
            new.setdefault(k, val)
        new["version"] = to_v
        migrated.append({"key": key, "data": new})
        status[key] = "migrated"
    failed = [k for k, v in status.items() if v.startswith("failed")]
    findings.append(chk("migration.backup", True, f"Backups hold {len(backups)} original record(s) for rollback."))
    findings.append(chk("migration.resumable", True, "Checkpoint records every key; rerun with the checkpoint to resume."))
    findings.append(chk("migration.partial_failures", not failed, "All selected records migrated." if not failed else f"{len(failed)} record(s) need retry: {', '.join(failed[:10])}", "warning"))
    checkpoint_out = {**checkpoint, **{k: ("done" if v == "migrated" or v == "skipped_done" else v) for k, v in status.items()}}
    summary = {"total": len(records), "migrated": sum(v == "migrated" for v in status.values()), "skipped_done": sum(v == "skipped_done" for v in status.values()), "failed": len(failed), "rejected": sum(v == "rejected_newer" for v in status.values())}
    return out({"summary": summary, "status": status}, findings,
               {"migrated-records.json": migrated, "backup-records.json": backups, "checkpoint.json": checkpoint_out,
                "migration-report.json": {"dry_run": True, "summary": summary}})


def h_rollback(d, cap, ctx):
    current = core.text(d, "current", maximum=60)
    known_good = core.text(d, "known_good", maximum=60)
    schema = core.integer(d, "schema_version", 1, 0, 10000)
    kg_schema = core.integer(d, "known_good_schema", 1, 0, 10000)
    backup = core.boolean(d, "backup_verified", False)
    config_backup = core.boolean(d, "config_backup", False)
    findings = [chk("rollback.target", known_good != current, "Known-good target differs from current." if known_good != current else "Known-good equals current; nothing to roll back to.", "fail"),
                chk("rollback.schema", kg_schema >= schema, "Known-good code can read the current data schema." if kg_schema >= schema else "Rollback would read newer data than it understands; restore the data backup first.", "fail"),
                chk("rollback.backup", backup, "Data backup is verified." if backup else "Verify the data backup before rollback.", "fail"),
                chk("rollback.config", config_backup, "Config backup exists." if config_backup else "Back up configuration before rollback.", "warning")]
    steps = ["Freeze new writes or enable maintenance mode", "Verify backup checksum and restore point", f"Deploy known-good version {known_good}", "Run the deployment verification checks", "Re-open traffic gradually"]
    decision = "blocked" if any(f["status"] == "fail" for f in findings) else ("review" if any(f["status"] == "review" for f in findings) else "ready")
    return out({"decision": decision, "steps": steps}, findings, {"rollback-plan.md": "# Rollback plan\n\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) + f"\n\nDecision: {decision}. Human approval is required before executing any step.\n"})



# ---------------------------------------------------------------- tags, clones, audits, diff, batch edit, map validation

TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
SCRIPT_CLASSES = {"Script", "LocalScript", "ModuleScript"}
STATEFUL_CLASSES = {"Humanoid", "Sound", "Animator", "ObjectValue", "IntValue", "NumberValue", "BoolValue", "StringValue"}
EFFECT_CLASSES = {"ParticleEmitter", "Trail", "Beam", "PointLight", "SpotLight", "SurfaceLight", "Fire", "Smoke", "Sparkles"}
PHYSICAL_PROPS = {"Anchored", "CanCollide", "CanQuery", "CanTouch", "Size", "Position", "Material", "Transparency", "Color"}
EDIT_PROP_TYPES = {"Anchored": "bool", "CanCollide": "bool", "CanQuery": "bool", "CanTouch": "bool", "Transparency": "unit",
                   "Size": "vec3", "Position": "vec3", "Material": "str", "Color": "color", "Enabled": "bool"}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _vec3(v):
    return isinstance(v, list) and len(v) == 3 and all(_is_num(x) for x in v)


def _edit_value_ok(name, value):
    kind = EDIT_PROP_TYPES.get(name)
    if kind is None:
        return None
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "unit":
        return _is_num(value) and 0 <= value <= 1
    if kind == "vec3":
        return _vec3(value) and all(x >= 0 for x in value) if name == "Size" else _vec3(value)
    if kind == "color":
        return isinstance(value, str) and bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value))
    if kind == "str":
        return isinstance(value, str) and 0 < len(value) <= 64
    return None


def _luau_value(name, value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_num(value):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if _vec3(value):
        return f"Vector3.new({value[0]}, {value[1]}, {value[2]})"
    if name == "Color":
        return f'Color3.fromHex("{value}")'
    return json.dumps(value)


def h_tags(d, cap, ctx):
    tags = core.array(d, "tags", required=True, kind=str, maximum=200)
    classes = core.obj(d, "classes")
    known = set(ctx.config["studio"]["tags"])
    bad = [t for t in tags if not TAG_RE.match(t)]
    dupes = sorted({t for t in tags if tags.count(t) > 1})
    bad_classes = {t: c for t, c in classes.items() if not isinstance(c, str) or not IDENT.match(c)}
    valid = list(dict.fromkeys(t for t in tags if TAG_RE.match(t)))
    lines = ["-- Generated by tools-roblox.py (tags). Not compiled or executed by this tool.",
             'local CollectionService = game:GetService("CollectionService")', "",
             "local connections: { RBXScriptConnection } = {}", "",
             "local function subscribe(tag: string, className: string, onAdded: (Instance) -> (), onRemoved: (Instance) -> ())",
             "\tlocal function accept(inst: Instance)", "\t\tif inst:IsA(className) then", "\t\t\tonAdded(inst)", "\t\tend", "\tend",
             "\tfor _, inst in CollectionService:GetTagged(tag) do", "\t\ttask.spawn(accept, inst)", "\tend",
             "\ttable.insert(connections, CollectionService:GetInstanceAddedSignal(tag):Connect(accept))",
             "\ttable.insert(connections, CollectionService:GetInstanceRemovedSignal(tag):Connect(onRemoved))", "end", "",
             "local function cleanup()", "\tfor _, connection in connections do", "\t\tconnection:Disconnect()", "\tend",
             "\ttable.clear(connections)", "end", ""]
    for t in valid:
        cls = classes.get(t, "Instance")
        lines += [f'subscribe("{t}", "{cls}", function(inst: Instance)', "\t-- Initialize the tagged instance here.", "end, function(inst: Instance)",
                  "\t-- Clean up state for the removed instance here.", "end)", ""]
    lines += ["return { subscribe = subscribe, cleanup = cleanup }", ""]
    findings = [chk("tags.names", not bad, "Every tag name is a valid identifier." if not bad else "Invalid tag names: " + ", ".join(bad[:10]), "fail"),
                chk("tags.duplicates", not dupes, "Tags are unique." if not dupes else "Duplicate tags: " + ", ".join(dupes), "warning"),
                chk("tags.classes", not bad_classes, "Class filters are valid identifiers." if not bad_classes else "Invalid class filters: " + ", ".join(bad_classes), "fail"),
                chk("tags.config", all(t in known for t in valid), "All tags are in the configured tag list." if all(t in known for t in valid) else "Tags not in the configured list: " + ", ".join(t for t in valid if t not in known), "info"),
                chk("tags.cleanup", True, "Generated module disconnects every signal in cleanup().")]
    artifacts = {"tag-subscriptions.luau": "\n".join(lines)} if not bad and not bad_classes else {}
    return out({"tags": valid, "subscriptions": len(valid)}, findings, artifacts)


def h_clone_audit(d, cap, ctx):
    template = core.obj(d, "template", required=True)
    clones = core.integer(d, "clones", minimum=1, maximum=10000)
    nodes = tree_nodes(template)
    root = nodes[0]["path"]
    paths = {n["path"] for n in nodes}
    scripts = [n["path"] for n in nodes if n["class"] in SCRIPT_CLASSES]
    not_archivable = [n["path"] for n in nodes if n["node"].get("archivable") is False]
    stale = []
    for n in nodes:
        if n["class"] == "ObjectValue":
            target = n["node"].get("value")
            if isinstance(target, str) and target:
                if target.startswith(root + "/") and target not in paths:
                    stale.append(f"{n['path']} -> {target} (missing in template)")
                elif not target.startswith(root + "/") and target != root:
                    stale.append(f"{n['path']} -> {target} (points outside the template; clones keep the original)")
    stateful = [n["path"] for n in nodes if n["node"].get("stateful") is True]
    root_ok = template.get("archivable", True) is not False
    findings = [chk("clone.archivable_root", root_ok, "Template root is Archivable, so Clone() works." if root_ok else "Template root has Archivable = false; Clone() will not copy it.", "fail"),
                chk("clone.archivable_children", not not_archivable, "All template descendants are Archivable." if not not_archivable else f"{len(not_archivable)} descendant(s) are not Archivable and will be missing from clones: " + ", ".join(not_archivable[:5]), "warning"),
                chk("clone.scripts", not scripts or clones == 1, "No scripts are duplicated." if not scripts or clones == 1 else f"{len(scripts)} script(s) would be duplicated {clones} times; keep logic in one ModuleScript and clone only the visual model.", "warning"),
                chk("clone.references", not stale, "No stale object references." if not stale else "Stale references: " + "; ".join(stale[:5]), "fail"),
                chk("clone.stateful", not stateful, "No stateful objects are marked for cloning." if not stateful else "Stateful objects are cloned by default; reset them per clone: " + ", ".join(stateful[:5]), "warning")]
    return out({"template_instances": len(nodes), "clones": clones, "instances_after_cloning": len(nodes) * clones,
                "scripts_per_clone": len(scripts), "script_instances_total": len(scripts) * clones}, findings)


def h_instance_audit(d, cap, ctx):
    tree = core.obj(d, "tree", required=True)
    budget = core.integer(d, "budget", default=5000, minimum=1, maximum=10000000)
    nodes = tree_nodes(tree)
    counts = {}
    for n in nodes:
        counts[n["class"]] = counts.get(n["class"], 0) + 1
    groups = {}
    for n in nodes:
        parent = n["path"].rsplit("/", 1)[0] if "/" in n["path"] else ""
        props = n["node"].get("properties", {})
        if n["class"] in BASEPART or n["class"] in EFFECT_CLASSES:
            key = (parent, n["class"], core.canonical(props))
            groups[key] = groups.get(key, 0) + 1
    duplicates = [f"{k[0]} {k[1]} x{v}" for k, v in groups.items() if v > 1]
    hidden = [n["path"] for n in nodes if n["class"] in BASEPART and n["node"].get("properties", {}).get("Transparency") == 1]
    stale_effects = [n["path"] for n in nodes if n["class"] in EFFECT_CLASSES and n["node"].get("properties", {}).get("Enabled") is False]
    temporary = [n["path"] for n in nodes if "Temporary" in (n["node"].get("tags") or [])]
    total = len(nodes)
    findings = [chk("instance.budget", total <= budget, f"{total} instances within the budget of {budget}." if total <= budget else f"{total} instances exceed the budget of {budget}.", "fail"),
                chk("instance.duplicates", not duplicates, "No duplicated decorations under the same parent." if not duplicates else f"{len(duplicates)} duplicated decoration group(s), e.g. {duplicates[0]}.", "warning"),
                chk("instance.hidden", not hidden, "No fully transparent parts." if not hidden else f"{len(hidden)} fully transparent part(s) still cost memory and physics: " + ", ".join(hidden[:5]), "warning"),
                chk("instance.stale_effects", not stale_effects, "No disabled effects left in the tree." if not stale_effects else f"{len(stale_effects)} disabled effect(s) remain: " + ", ".join(stale_effects[:5]), "warning"),
                chk("instance.runtime", not temporary, "No runtime-temporary instances are stored in the tree." if not temporary else f"{len(temporary)} instance(s) tagged Temporary are stored in the tree; create them at runtime and destroy them.", "info")]
    return out({"total": total, "budget": budget, "counts_by_class": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                "duplicate_groups": len(duplicates), "hidden_parts": len(hidden), "stale_effects": len(stale_effects)}, findings)


def h_tree_diff(d, cap, ctx):
    before = core.obj(d, "before", required=True)
    after = core.obj(d, "after", required=True)
    renames = core.obj(d, "renames")
    for old, new in renames.items():
        if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
            core.fail("INPUT_INVALID", "renames must map non-empty names to non-empty names.")
    v_before = core.integer(d, "schema_version_before", default=1, minimum=1, maximum=10000)
    v_after = core.integer(d, "schema_version_after", default=1, minimum=1, maximum=10000)

    def remap(path):
        return "/".join(renames.get(seg, seg) for seg in path.split("/"))

    before_nodes = {remap(n["path"]): n["node"] for n in tree_nodes(before)}
    after_nodes = {n["path"]: n["node"] for n in tree_nodes(after)}
    removed = sorted(set(before_nodes) - set(after_nodes))
    added = sorted(set(after_nodes) - set(before_nodes))
    changed = []
    for p in sorted(set(before_nodes) & set(after_nodes)):
        b, a = before_nodes[p], after_nodes[p]
        diffs = {}
        if b.get("class") != a.get("class"):
            diffs["class"] = [b.get("class"), a.get("class")]
        bp, ap = b.get("properties", {}), a.get("properties", {})
        for k in sorted(set(bp) | set(ap)):
            if bp.get(k) != ap.get(k):
                diffs[k] = [bp.get(k), ap.get(k)]
        if diffs:
            changed.append({"path": p, "changes": diffs})
    schema_delta = v_after - v_before
    steps = [f"Back up the current tree before any change." if (removed or schema_delta or changed) else "No migration steps are required."]
    steps += [f"Rename {old} to {new} (instances keep their children)." for old, new in renames.items()]
    steps += [f"Review removal of {p} and relocate or delete it after backup." for p in removed]
    steps += [f"Set changed properties on {c['path']}: {', '.join(c['changes'])}." for c in changed]
    if schema_delta > 0:
        steps.append(f"Run the schema migration from version {v_before} to {v_after}.")
    findings = [chk("tree_diff.removals", not removed, "No objects are removed." if not removed else f"{len(removed)} object(s) would be removed; requires a backup and explicit approval.", "fail"),
                chk("tree_diff.schema", schema_delta >= 0, f"Schema moves from {v_before} to {v_after}." if schema_delta >= 0 else f"Schema would downgrade from {v_before} to {v_after}; downgrades are blocked.", "fail"),
                chk("tree_diff.changes", not changed, "No property changes." if not changed else f"{len(changed)} object(s) have changed properties.", "info"),
                chk("tree_diff.backup", True, "A backup is required before applying this migration." if removed or schema_delta or changed else "No backup needed for an identical tree.")]
    plan = {"renames": renames, "removed": removed, "added": added, "changed": changed, "schema": [v_before, v_after], "steps": steps,
            "backup_required": bool(removed or schema_delta or changed), "approval_required": bool(removed or schema_delta < 0)}
    return out({"removed": len(removed), "added": len(added), "changed": len(changed), "schema_delta": schema_delta,
                "backup_required": plan["backup_required"]}, findings, {"migration-plan.json": plan})


def h_batch_edit(d, cap, ctx):
    tree = core.obj(d, "tree", required=True)
    edits = core.array(d, "edits", required=True, kind=dict, maximum=500)
    max_sel = core.integer(d, "max_selection", default=100, minimum=1, maximum=5000)
    index = {n["path"]: n for n in tree_nodes(tree)}
    working = copy.deepcopy(tree)
    wnodes = {n["path"]: n["node"] for n in tree_nodes(working)}
    errors, review, undo, luau, touched = [], [], [], [], set()
    if len(edits) > max_sel:
        core.fail("INPUT_LIMIT", "Selection exceeds max_selection.", selected=len(edits), limit=max_sel)
    for e in edits:
        path = core.text(e, "path", maximum=500)
        if path not in index:
            errors.append(f"{path}: not found in the tree")
            continue
        cls = index[path]["class"]
        original = index[path]["node"]
        node = wnodes[path]
        touched.add(path)
        props = e.get("properties", {})
        if not isinstance(props, dict):
            core.fail("INPUT_INVALID", "edit.properties must be an object.", path=path)
        undo_props = {}
        for k, v in props.items():
            if k in PHYSICAL_PROPS and cls not in BASEPART:
                errors.append(f"{path}: {k} is not valid on class {cls}")
                continue
            ok = _edit_value_ok(k, v)
            if ok is None:
                review.append(f"{path}.{k}: property is not in the validated set; not applied")
                continue
            if not ok:
                errors.append(f"{path}.{k}: value {v!r} is invalid")
                continue
            undo_props[k] = original.get("properties", {}).get(k)
            node.setdefault("properties", {})[k] = v
            luau.append(("prop", path, k, v))
        new_tags = e.get("tags", [])
        if not isinstance(new_tags, list) or not all(isinstance(t, str) for t in new_tags):
            core.fail("INPUT_INVALID", "edit.tags must be an array of strings.", path=path)
        for t in new_tags:
            if not TAG_RE.match(t):
                errors.append(f"{path}: tag {t!r} is not a valid identifier")
                continue
            if t not in node.setdefault("tags", []):
                node["tags"].append(t)
                luau.append(("tag", path, t, None))
        undo.append({"path": path, "properties": undo_props, "tags": list(original.get("tags", []))})
    lua = ['-- Generated by tools-roblox.py (batch_edit). Paths are relative to Workspace. Not compiled or executed by this tool.',
           'local ChangeHistoryService = game:GetService("ChangeHistoryService")', 'local CollectionService = game:GetService("CollectionService")', '',
           'local function find(path: string): Instance?', '\tlocal current: Instance? = workspace',
           '\tfor segment in string.gmatch(path, "[^/]+") do', '\t\tif current == nil then', '\t\t\treturn nil', '\t\tend',
           '\t\tcurrent = current:FindFirstChild(segment)', '\tend', '\treturn current', 'end', '',
           'ChangeHistoryService:SetWaypoint("Before batch edit")']
    for kind, path, a, b in luau:
        if kind == "prop":
            name = a
            value = _luau_value(name, b)
            if name == "Material":
                lua.append(f'do local t = find({json.dumps(path)}) if t then t.Material = Enum.Material[{json.dumps(b)}] end end')
            else:
                lua.append(f'do local t = find({json.dumps(path)}) if t then t.{name} = {value} end end')
        else:
            lua.append(f'do local t = find({json.dumps(path)}) if t then CollectionService:AddTag(t, {json.dumps(a)}) end end')
    lua += ['ChangeHistoryService:SetWaypoint("After batch edit")', '']
    applied = len(touched) if not errors else 0
    findings = [chk("batch_edit.targets", not any("not found" in x for x in errors), "All edit paths exist in the tree." if not any("not found" in x for x in errors) else "Some edit paths are missing from the tree.", "fail"),
                chk("batch_edit.values", not any("invalid" in x or "not valid on class" in x or "not a valid identifier" in x for x in errors), "Every edit value passes validation." if not any("invalid" in x or "not valid on class" in x or "not a valid identifier" in x for x in errors) else f"{len(errors)} edit error(s); see result.errors.", "fail"),
                chk("batch_edit.review", not review, "All edited properties are validated." if not review else f"{len(review)} property edit(s) need manual review.", "warning"),
                chk("batch_edit.reversible", True, f"{len(undo)} undo record(s) written.")]
    return out({"edits": len(edits), "applied_to_preview": applied, "errors": errors[:50], "review": review[:50],
                "touched_paths": sorted(touched)}, findings,
               {"batch-edit-undo.json": undo, "batch-edit-commands.luau": "\n".join(lua)})


def h_map_validation(d, cap, ctx):
    tree = core.obj(d, "tree", required=True)
    required = set(core.array(d, "required_tags", maximum=50, kind=str))
    budget = core.integer(d, "part_budget", default=5000, minimum=1, maximum=10000000)
    studio = ctx.config["studio"]
    max_size, min_size = studio["part_size_max"], studio["part_size_min"]
    nodes = tree_nodes(tree)
    spawns = [n for n in nodes if n["class"] == "SpawnLocation"]
    parts = [n for n in nodes if n["class"] in BASEPART]
    seen_tags, bad_tags = set(), []
    for n in nodes:
        for t in core.array(n["node"], "tags", maximum=50, kind=str):
            seen_tags.add(t)
            if not TAG_RE.match(t):
                bad_tags.append(f"{n['path']}:{t}")
    missing = sorted(required - seen_tags)
    oversize = []
    for p in parts:
        size = p["node"].get("properties", {}).get("Size")
        if _vec3(size) and any(s > max_size for s in size):
            oversize.append(p["path"])
    blockers = []
    for p in parts:
        pr = p["node"].get("properties", {})
        if pr.get("CanCollide", True) is False or pr.get("Transparency") == 1 or not _vec3(pr.get("Position")) or not _vec3(pr.get("Size")):
            continue
        blockers.append((p["path"], pr["Position"], pr["Size"]))
    blocked, unknown_spawns = [], []
    for s in spawns:
        sp = s["node"].get("properties", {})
        if not (_vec3(sp.get("Position")) and _vec3(sp.get("Size"))):
            unknown_spawns.append(s["path"])
            continue
        px, py, pz = sp["Position"]
        sx, sy, sz = sp["Size"]
        base = py - sy / 2
        for bpath, (bx, by, bz), (bsx, bsy, bsz) in blockers:
            if bpath == s["path"]:
                continue
            if abs(bx - px) < (bsx + sx) / 2 and abs(bz - pz) < (bsz + sz) / 2 and by - bsy / 2 < base + 8 and by + bsy / 2 > base:
                blocked.append(f"{s['path']} blocked by {bpath}")
                break
    unsafe = []
    for n in nodes:
        if n["class"] in SCRIPT_CLASSES:
            src = n["node"].get("source", "")
            if isinstance(src, str) and ("loadstring" in src or re.search(r"require\(\s*\d{5,}", src)):
                unsafe.append(n["path"])
    findings = [chk("map.spawn", bool(spawns), f"{len(spawns)} spawn location(s)." if spawns else "No SpawnLocation exists; players cannot join.", "fail"),
                chk("map.spawn_clear", not blocked and not unknown_spawns, "Spawns have clear airspace." if not blocked and not unknown_spawns else ("Blocked spawns: " + "; ".join(blocked[:5]) if blocked else "Spawn geometry is missing Position or Size: " + ", ".join(unknown_spawns)), "fail"),
                chk("map.required_tags", not missing, "All required tags are present." if not missing else "Missing required tags: " + ", ".join(missing), "fail"),
                chk("map.tag_names", not bad_tags, "All tags are valid identifiers." if not bad_tags else "Invalid tags: " + ", ".join(bad_tags[:5]), "fail"),
                chk("map.bounds", not oversize, f"No part exceeds {max_size} studs on any axis." if not oversize else f"{len(oversize)} part(s) exceed {max_size} studs: " + ", ".join(oversize[:5]), "fail"),
                chk("map.scripts", not unsafe, "No script uses loadstring or a numeric require." if not unsafe else "Unsafe scripts: " + ", ".join(unsafe[:5]), "fail"),
                chk("map.budget", len(parts) <= budget, f"{len(parts)} parts within budget {budget}." if len(parts) <= budget else f"{len(parts)} parts exceed budget {budget}.", "warning")]
    return out({"spawns": len(spawns), "parts": len(parts), "missing_tags": missing, "blocked_spawns": blocked, "oversize_parts": oversize[:50],
                "unsafe_scripts": unsafe, "min_part_studs": min_size, "max_part_studs": max_size}, findings)


HANDLERS.update({
    "bootstrap": h_bootstrap, "hierarchy": h_hierarchy, "containers": h_containers, "replication": h_replication,
    "properties": h_properties, "settings": h_settings, "team_workflow": h_team_workflow, "experience": h_experience,
    "wiring": h_wiring, "references": h_references, "universe": h_universe, "api_lookup": h_api_lookup,
    "asset_dependencies": h_asset_dependencies, "architecture_diagram": h_architecture_diagram, "packages": h_packages,
    "impact": h_impact, "dependency_injection": h_dependency_injection, "checkpoints": h_checkpoints, "quests": h_quests,
    "crafting": h_crafting, "content_browser": h_content_browser, "ownership": h_ownership, "policy_impact": h_policy_impact,
    "validation_pipeline": h_validation_pipeline, "workflow_cli": h_workflow_cli, "rojo": h_rojo, "git_workflow": h_git_workflow,
    "attributes": h_attributes, "remote_contract": h_remote_contract, "protocol": h_protocol, "profile_schema": h_profile_schema,
    "event_taxonomy": h_event_taxonomy, "remote_config": h_remote_config, "catalog_maintenance": h_catalog_maintenance,
    "environments": h_environments, "channels": h_channels, "server_validation": h_server_validation,
    "regression": h_regression, "edge_cases": h_edge_cases, "package_update": h_package_update,
    "migration": h_migration, "rollback": h_rollback,
    "tags": h_tags, "clone_audit": h_clone_audit, "instance_audit": h_instance_audit, "tree_diff": h_tree_diff,
    "batch_edit": h_batch_edit, "map_validation": h_map_validation,
})
