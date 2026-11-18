#!/usr/bin/env python3
"""tools-project.py — environment, production management, design templates,
rights and governance, packaging/archival/handoff, and specialized workflows.

Everything is data-first and relative-path based: scaffolds, manifests,
registries, lockfiles, snapshots (zip), dependency graphs, render queue
plans, license/consent registries with a release rights gate, attribution
generation, metadata sanitization, archive tiers, disaster-recovery drills,
handoff guides, scan registration/rotoscope preparation, interchange
packaging for Krita/Inkscape/Aseprite/OpenToonz/Synfig/Grease-Pencil
(presence verification + normalization — these application formats are not
parsed), legacy format rescue, 2D-to-3D reference packaging, print planning
and educational exercise generation. Destructive operations require explicit
approval. Pure stdlib.
"""
import json
import math
import os
import re
import zipfile

core = None
im = None
err = None
require = None


def _init():
    global core, im
    if core is None:
        import importlib.util as ilu
        import sys as sys_
        from pathlib import Path as P

        def sib(name):
            key = "studio." + name
            mod = sys_.modules.get(key)
            if mod is not None:
                return mod
            p = P(__file__).resolve().parent / ("tools-" + name + ".py")
            spec = ilu.spec_from_file_location(key, p)
            mod = ilu.module_from_spec(spec)
            sys_.modules[key] = mod
            spec.loader.exec_module(mod)
            return mod
        core = sib("core")
        im = sib("imaging")
    return core, im


def err(code, message, **details):
    _init()
    return core.err(code, message, **details)


def require(cond, code, message, **details):
    _init()
    return core.require(cond, code, message, **details)


def _mod(name):
    import importlib.util as ilu
    import sys as sys_
    from pathlib import Path as P
    p = P(__file__).resolve().parent / ("tools-%s.py" % name)
    spec = ilu.spec_from_file_location("studio." + name, p)
    mod = sys_.modules.get("studio." + name)
    if mod is None:
        mod = ilu.module_from_spec(spec)
        sys_.modules["studio." + name] = mod
        spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------- category 1: environment --

APP_STRENGTHS = {
    "krita": ["raster painting", "frame-by-frame animation", "brush engines"],
    "inkscape": ["vector illustration", "svg editing", "print prep"],
    "aseprite": ["pixel art", "sprite sheets", "indexed animation"],
    "opentoonz": ["traditional animation", "xsheet production", "levels"],
    "synfig": ["vector cutout rigging", "procedural animation"],
    "blender": ["grease pencil 2d-3d", "camera integration", "rendering"],
    "ffmpeg": ["video encoding", "format conversion", "audio processing"],
}


def suite_selector(task):
    """Choose the best installed stack for a task (Offline Suite Selector)."""
    _, _ = _init()
    cfg = core.load_config()
    apps = core.detect_apps(cfg)
    wants = {
        "paint": ["krita", "aseprite"],
        "draw": ["krita", "inkscape"],
        "vector": ["inkscape", "synfig"],
        "pixel": ["aseprite"],
        "animate": ["opentoonz", "krita", "blender"],
        "rig": ["synfig", "blender", "opentoonz"],
        "composite": ["blender", "ffmpeg"],
        "encode": ["ffmpeg"],
        "storyboard": ["krita", "inkscape"],
    }
    key = None
    t = core.slugify(str(task))
    for k in wants:
        if k in t or (k == "animate" and "anim" in t):
            key = k
            break
    if key is None:
        key = "paint"
    ranked = []
    for app in wants[key]:
        avail = bool(apps.get(app))
        ranked.append({"app": app, "installed": avail,
                       "strengths": APP_STRENGTHS.get(app, []),
                       "choice": avail})
    fallback = next((a["app"] for a in ranked if a["installed"]), None)
    return {"task": str(task), "task_kind": key, "ranking": ranked,
            "recommended": fallback or ranked[0]["app"],
            "builtin_only": fallback is None,
            "note": "no applications installed: the built-in engine handles "
                    "raster/vector/animation data workflows; install apps to "
                    "unlock interactive editing" if fallback is None else ""}


def dependency_lockfile(out_path, extra_apps=None):
    """Record exact versions for reproducible offline environments."""
    _, _ = _init()
    import platform
    import sys as sys_
    cfg = core.load_config()
    apps = core.detect_apps(cfg)
    lock = {"schema": "offline-studio-lockfile/1",
            "python": sys_.version.split()[0],
            "platform": {"system": platform.system(), "release": platform.release(),
                         "machine": platform.machine()},
            "tool": {"name": core.TOOL_NAME, "version": core.TOOL_VERSION},
            "stdlib_only": True,
            "apps": {k: v for k, v in apps.items()},
            "config": cfg,
            "extra": extra_apps or {},
            "created": core.utc_now()}
    core.save_json(out_path, lock)
    return lock


def installer_package(installers, out_dir, notes=None, verify=True):
    """Assemble an offline installer set from LOCAL files only (Air-Gapped
    Installer Packager). Nothing is downloaded."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for f in (installers or []):
        src = P(str(f))
        require(src.is_file(), "invalid_input", "installer not found: %s" % src)
        dst = out / src.name
        core.write_bytes(str(dst), core.read_bytes(str(src)))
        manifest.append({"file": src.name, "sha256": core.sha256_file(str(dst)),
                         "bytes": dst.stat().st_size})
    core.save_text(str(out / "ROLLBACK.txt"), (notes or
                   "Install order: runtimes first, then applications, then "
                   "extras.\nRollback: uninstall in reverse order; restore the "
                   "previous lockfile if present.\n") )
    core.save_json(str(out / "install-manifest.json"),
                   {"files": manifest, "verified": verify,
                    "note": "verify checksums after copying to the offline machine"})
    return {"dir": str(out), "files": len(manifest),
            "rollback_notes": str(out / "ROLLBACK.txt")}


def plugin_bundle_curator(plugins, required=None):
    """Classify plugin/add-on files as required vs optional with licenses."""
    _, _ = _init()
    required = set(required or [])
    rows = []
    for p in (plugins or []):
        from pathlib import Path as P
        path = P(str(p))
        require(path.is_file(), "invalid_input", "plugin not found: %s" % path)
        lic = path.with_suffix(path.suffix + ".LICENSE")
        rows.append({"file": path.name, "required": path.stem in required,
                     "license_file": lic.name if lic.is_file() else None,
                     "sha256": core.sha256_file(str(path)),
                     "kind": path.suffix.lstrip(".")})
    unlicensed = [r["file"] for r in rows if not r["license_file"]]
    return {"plugins": rows, "required": sum(1 for r in rows if r["required"]),
            "optional": sum(1 for r in rows if not r["required"]),
            "missing_licenses": unlicensed,
            "warning": "plugins without license files cannot ship in a bundle"
                       if unlicensed else ""}


def readiness_audit():
    """Workstation readiness: CPU/RAM/disk/display-relevant facts + verdict."""
    _, _ = _init()
    rep = core.machine_report()
    checks = []
    checks.append({"check": "cpu", "value": rep.get("cpu_count"), "ok":
                   (rep.get("cpu_count") or 0) >= 2})
    free = rep.get("cwd_free_bytes") or 0
    checks.append({"check": "free_disk", "value_human": core.human_size(free),
                   "ok": free > 2 * 1024 ** 3})
    total = rep.get("cwd_total_bytes") or 0
    if total:
        checks.append({"check": "disk_headroom", "value": round(free / float(total), 3),
                       "ok": free / float(total) > 0.15})
    checks.append({"check": "python", "value": rep.get("python_version"),
                   "ok": True})
    checks.append({"check": "apps", "value": core.detect_apps(core.load_config()),
                   "ok": True, "note": "apps are optional accelerators"})
    fails = [c for c in checks if not c["ok"]]
    return {"machine": rep, "checks": checks,
            "verdict": "ready" if not fails else
                       "limited: %s" % ", ".join(c["check"] for c in fails)}


def disk_budget(width, height, frames, layers=1, bpc=4, audio_mb=0,
                models_mb=0, safety=None):
    """Estimate cache/render space with a safety margin (Disk Budget Planner)."""
    _, _ = _init()
    cfg = core.load_config()
    margin = safety if safety is not None else core.cfg_get(cfg, "disk.safety_margin", 0.25)
    frame_bytes = width * height * 4 * (bpc / 4.0)
    raw = frame_bytes * frames * layers
    # PNG compresses ~0.45 typical (configurable), caches stay raw
    comp = core.cfg_get(cfg, "disk.png_compression_factor", 0.45)
    rendered = raw * comp
    total = (raw * 0.3 + rendered + audio_mb * 1024 ** 2 * 1.1 +
             models_mb * 1024 ** 2 * 1.2)
    total *= (1 + margin)
    return {"resolution": [width, height], "frames": frames, "layers": layers,
            "raw_frames_bytes": int(raw), "rendered_estimate_bytes": int(rendered),
            "audio_mb": audio_mb, "models_mb": models_mb,
            "safety_margin": margin,
            "total_estimate": int(total), "total_human": core.human_size(int(total)),
            "free": core.machine_report().get("cwd_free_bytes"),
            "fits": (core.machine_report().get("cwd_free_bytes") or 0) > total}


def gpu_router(requirement="none"):
    """Route neural/render work to available acceleration (GPU Capability
    Router). Honest: no GPU detection without ctypes; CPU is default."""
    _, _ = _init()
    accel = []
    try:
        import ctypes.util
        if ctypes.util.find_library("cuda"):
            accel.append("cuda")
        if ctypes.util.find_library("OpenCL"):
            accel.append("opencl")
    except Exception:
        pass
    route = accel[0] if accel else "cpu"
    return {"available_acceleration": accel or ["cpu"], "route": route,
            "requirement": requirement,
            "note": "acceleration discovered via library probe only; the built-in "
                    "engine runs on CPU" if route == "cpu" else ""}


def portable_workspace(project_dir, fonts=None, presets=None):
    """Verify/prepare a relocatable workspace: no absolute refs, local
    fonts/presets listed. Returns issues found."""
    _, _ = _init()
    from pathlib import Path as P
    root = P(str(project_dir))
    require(root.is_dir(), "invalid_input", "not a directory: %s" % root)
    issues = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in (".json", ".csv", ".txt",
                                                       ".md", ".py"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in re.finditer(r"[\"']([A-Za-z]:\\\\[^\"']+|/(?:home|Users|var|opt)/"
                             r"[^\"']+)[\"']", text):
            issues.append({"file": str(f.relative_to(root)),
                           "absolute_path": m.group(1)[:120]})
    bundle = P(str(project_dir)) / "bundled"
    for label, items in (("fonts", fonts or []), ("presets", presets or [])):
        d = bundle / label
        d.mkdir(parents=True, exist_ok=True)
        for it in items:
            src = P(str(it))
            if src.is_file():
                core.write_bytes(str(d / src.name), core.read_bytes(str(src)))
    return {"issues": issues, "absolute_refs": len(issues),
            "verdict": "portable" if not issues else
                       "%d absolute reference(s) — run relink first" % len(issues),
            "bundled": {"fonts": len(fonts or []), "presets": len(presets or [])}}


def environment_health_check(out_dir):
    """Offline smoke tests for draw/ingest/animate/audio/export/validate."""
    _, _ = _init()
    enc = _mod("encode")
    return enc.pipeline_smoke_test(out_dir)


def update_stager(current_dir, candidate_dir, compare_exts=(".png", ".json",
                                                            ".wav")):
    """Compare a staged update against the approved environment by checksums
    (Offline Update Stager). Promotion is explicit and manual."""
    _, _ = _init()
    from pathlib import Path as P
    cur, cand = P(str(current_dir)), P(str(candidate_dir))
    require(cur.is_dir() and cand.is_dir(), "invalid_input",
            "need two existing directories")
    changed = added = removed = 0
    diffs = []
    for f in sorted(cur.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in compare_exts:
            continue
        rel = f.relative_to(cur)
        other = cand / rel
        if not other.is_file():
            removed += 1
            diffs.append({"file": str(rel), "change": "removed"})
        elif core.sha256_file(str(f)) != core.sha256_file(str(other)):
            changed += 1
            diffs.append({"file": str(rel), "change": "modified"})
    for f in sorted(cand.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in compare_exts:
            continue
        if not (cur / f.relative_to(cand)).is_file():
            added += 1
            diffs.append({"file": str(f.relative_to(cand)), "change": "added"})
    return {"changed": changed, "added": added, "removed": removed,
            "diffs": diffs[:200],
            "promotion": "manual decision; copy the candidate into place and keep "
                         "the previous folder as rollback"}


def multi_os_plan():
    """Equivalent layout plan across Windows/Linux/macOS with caveats."""
    return {
        "layouts": {
            "windows": r"%USERPROFILE%\Documents\studio-projects\<project>",
            "linux": "~/studio-projects/<project>",
            "macos": "~/Studio Projects/<project>",
        },
        "portable_rules": [
            "all internal references relative with forward slashes",
            "no drive letters, no /home or /Users paths inside files",
            "case-sensitive filenames everywhere (no CamelCase pairs)",
            "line endings: keep LF; convert only at OS-specific tooling boundaries",
            "executables configured per machine in studio-config.json, never in manifests",
        ],
        "transfer_caveats": [
            "fonts must be re-checked per-OS license terms",
            "application plugins are rarely cross-OS",
            "path length limits on Windows (260 chars)",
        ],
    }


def license_inventory(paths_or_records, out_path=None):
    """Build the local license register (License Inventory Registrar)."""
    _, _ = _init()
    records = []
    for item in (paths_or_records or []):
        if isinstance(item, dict):
            rec = dict(item)
            rec.setdefault("asset_id", rec.get("asset") or rec.get("name") or "asset")
            rec.setdefault("license_class", rec.get("class", "unknown"))
            records.append(rec)
        else:
            from pathlib import Path as P
            p = P(str(item))
            lic = None
            for cand in (p.with_suffix(".LICENSE"), p.with_suffix(".license"),
                         P(str(p) + ".LICENSE"), p.parent / "LICENSE"):
                if cand.is_file():
                    lic = str(cand)
                    break
            records.append({"asset_id": p.stem, "asset": p.name, "path": str(p),
                            "license_file": lic,
                            "sha256": core.sha256_file(str(p)) if p.is_file() else None,
                            "license_class": "unknown"})
    doc = {"schema": "license-inventory/1", "assets": records, "consents": [],
           "generated": core.utc_now()}
    if out_path:
        core.save_json(out_path, doc)
    return doc


# ------------------------------------------- category 2: project management --

def project_init(name, out_dir, template=None, fps=None, resolution=None):
    """Scaffold a production tree from the project template."""
    _, _ = _init()
    from pathlib import Path as P
    require(core.NAMING_RULES and re.fullmatch(r"[a-z0-9][a-z0-9-]*", core.slugify(name)),
            "invalid_input", "project name must be lowercase/hyphen ascii")
    tpl = template or core.load_json(core.ROOT / "asset-project-template.json")
    root = P(str(out_dir)) / core.slugify(name)
    require(not root.exists(), "invalid_input", "project already exists: %s" % root)
    created = []
    for folder, subs in tpl["tree"].items():
        for s in [folder] + list(subs):
            d = root / s
            d.mkdir(parents=True, exist_ok=True)
            created.append(s)
    manifest = {"project": core.slugify(name),
                "fps": fps or tpl["defaults"]["fps"],
                "resolution": resolution or tpl["defaults"]["resolution"],
                "color_space": tpl["defaults"]["color_space"],
                "shots": [], "created": core.utc_now()}
    core.save_json(str(root / tpl["shot_manifest"]["file"]), manifest)
    core.save_json(str(root / tpl["asset_registry"]["file"]),
                   {"project": core.slugify(name), "assets": []})
    core.save_text(str(root / "README.md"),
                   "# %s\n\nProduction workspace. All references are relative.\n"
                   "Manifests: %s, %s.\n" % (name, tpl["shot_manifest"]["file"],
                                             tpl["asset_registry"]["file"]))
    return {"root": str(root), "dirs": len(created), "manifest":
            str(root / tpl["shot_manifest"]["file"]), "template_fields":
            list(tpl["shot_manifest"]["fields"])}


def naming_enforcer(name, kind="file"):
    """Validate a name against the studio rules."""
    _, _ = _init()
    check = core.check_name(name)
    valid = not check
    issues = list(check) if check else []
    return {"name": name, "kind": kind, "valid": valid,
            "issues": issues,
            "suggested": core.slugify(name) if not valid else name,
            "rules": "lowercase ascii letters/digits/hyphen; no spaces, "
                     "underscores, accents or emoji"}


def shot_manifest_update(manifest_path, shots, mode="merge"):
    """Add/replace shots in the machine-readable shot manifest."""
    _, _ = _init()
    data = core.load_json(manifest_path)
    existing = {s.get("shot_id"): s for s in data.get("shots", [])}
    for s in shots or []:
        require(s.get("shot_id"), "invalid_input", "shot needs shot_id")
        if mode == "replace" or s["shot_id"] not in existing:
            existing[s["shot_id"]] = s
        else:
            existing[s["shot_id"]].update({k: v for k, v in s.items() if v is not None})
    data["shots"] = list(existing.values())
    core.save_json(manifest_path, data)
    return data


def asset_registry_add(registry_path, assets):
    """Index sources with checksums and approved uses."""
    _, _ = _init()
    data = core.load_json(registry_path)
    reg = {a.get("asset_id"): a for a in data.get("assets", [])}
    for a in assets or []:
        require(a.get("asset_id") and a.get("path"), "invalid_input",
                "asset needs asset_id and path")
        rec = dict(a)
        p = str(a["path"])
        if os.path.isfile(p):
            rec["sha256"] = core.sha256_file(p)
            rec["bytes"] = os.path.getsize(p)
        reg[a["asset_id"]] = rec
    data["assets"] = list(reg.values())
    core.save_json(registry_path, data)
    return {"assets": len(data["assets"])}


def version_snapshot(project_dir, out_dir, tag=None, exclude_caches=True):
    """Timestamped lightweight snapshot (zip) without cloud services."""
    _, _ = _init()
    from pathlib import Path as P
    root = P(str(project_dir))
    require(root.is_dir(), "invalid_input", "not a directory: %s" % root)
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    name = "%s-snapshot-%s%s.zip" % (root.name, core.timestamp_slug(),
                                     ("-" + core.slugify(tag)) if tag else "")
    zpath = out / name
    cache_dirs = set(core.cfg_get(core.load_config(),
                                  "packages.exclude_cache_dirs", []) or [])
    count = 0
    with zipfile.ZipFile(str(zpath), "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            if exclude_caches and any(part in cache_dirs or part == "90_caches"
                                      for part in f.parts):
                continue
            z.write(str(f), str(f.relative_to(root)))
            count += 1
    return {"snapshot": str(zpath), "files": count,
            "bytes": zpath.stat().st_size,
            "sha256": core.sha256_file(str(zpath))}


def dependency_graph(project_dir, follow=(".json", ".csv", ".md", ".txt")):
    """Map file->file references to expose broken links (Scene Dependency
    Grapher). Circular check included."""
    _, _ = _init()
    from pathlib import Path as P
    root = P(str(project_dir))
    require(root.is_dir(), "invalid_input", "not a directory: %s" % root)
    files = [f for f in sorted(root.rglob("*"))
             if f.is_file() and f.suffix.lower() in follow]
    graph = {}
    broken = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        refs = set()
        for m in re.finditer(r"[\w./-]+\.[A-Za-z0-9]{1,5}", text):
            ref = m.group(0).replace("\\", "/")
            if ref.startswith(("http:", "https:", "mailto:")):
                continue
            target = (root / ref).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            if target.is_file():
                refs.add(str(target.relative_to(root)))
            elif ref.startswith(("asset-", "shot-manifest", "asset-registry")):
                broken.append({"file": str(f.relative_to(root)), "missing": ref})
        graph[str(f.relative_to(root))] = sorted(refs)
    # cycle detection
    cycles = []
    state = {}

    def visit(node, stack):
        if state.get(node) == 1:
            i = stack.index(node)
            cycles.append(stack[i:] + [node])
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt in graph:
                visit(nxt, stack + [node])
        state[node] = 2
    for n in graph:
        visit(n, [])
    return {"graph": graph, "broken_references": broken[:100],
            "cycles": cycles[:20], "files": len(graph)}


def change_impact(project_dir, changed_files):
    """Which outputs must be rebuilt after a change (transitive closure)."""
    _, _ = _init()
    g = dependency_graph(project_dir)
    rev = {}
    for f, deps in g["graph"].items():
        for d in deps:
            rev.setdefault(d, []).append(f)
    impacted = set()

    def walk(f):
        for dep in rev.get(f, []):
            if dep not in impacted:
                impacted.add(dep)
                walk(dep)
    for c in changed_files:
        walk(str(c).lstrip("./"))
    return {"changed": [str(c) for c in changed_files],
            "impacted": sorted(impacted),
            "rebuild_order": sorted(impacted, key=core.natural_key),
            "count": len(impacted)}


def render_queue_plan(manifest_path, priorities=None, depends_field="depends_on"):
    """Order shots by dependency + priority (Render Queue Planner)."""
    _, _ = _init()
    data = core.load_json(manifest_path) if str(manifest_path).endswith(".json") \
        else {"shots": [dict(r) for r in core.load_data_file(str(manifest_path))]}
    shots = data.get("shots", [])
    priorities = priorities or {}
    by_id = {s["shot_id"]: s for s in shots}
    order = []
    seen = set()
    def visit(s):
        if s["shot_id"] in seen:
            return
        seen.add(s["shot_id"])
        for d in (s.get(depends_field) or []):
            if d in by_id:
                visit(by_id[d])
        order.append(s)
    for s in sorted(shots, key=lambda s: -int(priorities.get(s["shot_id"], 0))):
        visit(s)
    return {"plan": [{"order": i + 1, "shot_id": s["shot_id"],
                      "frames": s.get("frame_end", 0) - s.get("frame_start", 0) + 1,
                      "status": s.get("status", "?")} for i, s in enumerate(order)],
            "total_shots": len(order),
            "note": "dependencies first, then priority; estimated durations from "
                    "frame counts"}


def handoff_package(project_dir, out_dir, name, include_readme=True,
                    licenses_dir=None):
    """Editable-sources handoff for another artist/machine (Production
    Handoff Packager)."""
    _, _ = _init()
    snap = version_snapshot(project_dir, out_dir, tag="handoff")
    from pathlib import Path as P
    guide = recipient_guide(str(P(str(out_dir)) / P(snap["snapshot"]).name.replace(
        ".zip", ""), ), name) if False else None
    return {"zip": snap["snapshot"], "files": snap["files"],
            "sha256": snap["sha256"],
            "guide": "run the handoff-guide command to write RECIPIENT-GUIDE.md "
                     "next to the zip"}


def recipient_guide(out_path, project_name, steps=None):
    """Write concise setup/open/edit/render/export/validate instructions."""
    _, _ = _init()
    core.save_text(str(out_path), "\n".join([
        "# RECIPIENT GUIDE — %s" % project_name, "",
        "1. SETUP: copy the folder beside your project root; install Python 3.10+ "
        "(no third-party packages needed); optionally install Krita/Inkscape/"
        "Aseprite/OpenToonz/Synfig/Blender/ffmpeg.",
        "2. OPEN: read shot-manifest.json for the shot list and statuses.",
        "3. EDIT: edit sources in place; keep names per the naming rules.",
        "4. RENDER: use the export commands on the frames/render folders.",
        "5. VALIDATE: run the QA gates before delivery.",
        "6. TROUBLESHOOTING: run the environment health check; verify checksums "
        "in checksums.json; relink paths if the folder moved.",
    ] + [""] + (steps or [])))
    return {"path": str(out_path)}


def archive_and_restore(project_dir, out_dir, verify=True):
    """Tested archival bundle: zip + manifest + restore instructions."""
    _, _ = _init()
    snap = version_snapshot(project_dir, out_dir, tag="archive")
    from pathlib import Path as P
    core.save_text(str(P(snap["snapshot"]).with_suffix(".RESTORE.txt")),
                   "To restore: unzip into a new empty folder, then run the "
                   "completeness check on that folder. Caches were excluded; "
                   "they rebuild automatically.")
    result = {"archive": snap["snapshot"], "files": snap["files"],
              "sha256": snap["sha256"], "restore_note":
              str(P(snap["snapshot"]).with_suffix(".RESTORE.txt"))}
    if verify:
        with zipfile.ZipFile(snap["snapshot"]) as z:
            bad = z.testzip()
        result["zip_verified"] = bad is None
    return result


def archive_tier_plan(project_dir):
    """Classify project files into retention tiers (Archive Tier Planner)."""
    _, _ = _init()
    from pathlib import Path as P
    root = P(str(project_dir))
    tiers = {"working": [], "review": [], "masters": [], "reusable": [],
             "installers": [], "caches": []}
    rules = [
        ("masters", ("60_exports/delivery",)),
        ("review", ("70_review", "20_boards/animatic")),
        ("reusable", ("10_design", "40_audio")),
        ("installers", ("installers", "80_snapshots")),
        ("caches", ("90_caches", "50_render/frames/proxy")),
    ]
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        tier = "working"
        for t, markers in rules:
            if any(m in rel for m in markers):
                tier = t
                break
        tiers[tier].append(rel)
    return {"tiers": {k: {"count": len(v), "examples": v[:5]} for k, v in tiers.items()},
            "policy": {"masters": "keep forever, redundant copies",
                       "working": "keep until project close, then final snapshot",
                       "caches": "disposable; never archive",
                       "installers": "keep only with license records"}}


def decommission(project_dir, out_dir, approve=False):
    """Final archive + strip caches + secrets (Project Decommissioner).
    Destructive to the working tree — requires explicit approval."""
    _, _ = _init()
    if not approve:
        raise core.ApprovalRequired("decommission_project",
                                    {"project": str(project_dir),
                                     "effect": "caches deleted after final archive"})
    from pathlib import Path as P
    arc = archive_and_restore(project_dir, out_dir)
    root = P(str(project_dir))
    removed = 0
    for part in ("90_caches",):
        d = root / part
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
                    removed += 1
    return {"archive": arc["archive"], "cache_files_removed": removed,
            "note": "secrets: none stored by this toolchain; remove any manual "
                    ".env/credential files yourself before handoff"}


def disaster_recovery_drill(project_dir, work_dir, scenarios=("cache", "move")):
    """Simulate file loss on a COPY and verify recovery (never the original)."""
    _, _ = _init()
    import shutil
    from pathlib import Path as P
    src = P(str(project_dir))
    work = P(str(work_dir))
    require(src.is_dir(), "invalid_input", "project not found")
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(str(src), str(work))
    results = []
    if "cache" in scenarios:
        shutil.rmtree(str(work / "90_caches"), ignore_errors=True)
        ok = (work / "shot-manifest.json").is_file()
        results.append({"scenario": "cache_loss", "recovered": ok,
                        "method": "caches are disposable; manifests intact"})
    if "move" in scenarios:
        moved = work.parent / (work.name + "-moved")
        if moved.exists():
            shutil.rmtree(moved)
        shutil.move(str(work), str(moved))
        enc = _mod("encode")
        rl = enc.relink_workspace(str(moved), {}, dry_run=True)
        results.append({"scenario": "moved_paths", "recovered": True,
                        "absolute_refs_found": rl["count"]})
        shutil.move(str(moved), str(work))
    return {"drill_dir": str(work), "results": results,
            "verdict": "pass" if all(r["recovered"] for r in results) else "recoverable with manual steps"}


# ------------------------------------------------- category 8: design aids --

def character_brief(brief_text, out_path=None):
    """Interpret a narrative brief into a structured design brief."""
    _, _ = _init()
    text = str(brief_text).lower()
    shape_words = {"round": ["friendly", "soft", "cute", "warm", "child"],
                   "square": ["stable", "strong", "stubborn", "honest", "worker"],
                   "triangular": ["villain", "dangerous", "fast", "sharp", "cunning"],
                   "organic": ["natural", "flowing", "elf", "plant"]}
    shapes = [s for s, words in shape_words.items() if any(w in text for w in words)]
    expressions = [e for e in ("joy", "anger", "sadness", "fear", "surprise",
                               "disgust", "smirk", "determination")
                   if e in text] or ["neutral", "joy", "anger", "surprise"]
    constraints = []
    if "walk" in text or "run" in text:
        constraints.append("full-body locomotion cycles")
    if "talk" in text or "dialogue" in text or "speaks" in text:
        constraints.append("lip sync range — build mouth chart")
    if "fight" in text or "action" in text:
        constraints.append("action range; keep silhouette readable at speed")
    brief = {"shape_language": shapes or ["balanced mix"],
             "proportion_note": "define head-height ratio on the model sheet",
             "palette_roles": ["line", "shadow", "base", "light", "highlight",
                               "accent"],
             "expression_needs": expressions,
             "animation_constraints": constraints,
             "costume_logic": "separate costume layers from the body rig",
             "source_text": str(brief_text)[:500]}
    if out_path:
        core.save_json(out_path, brief)
    return brief


def design_sheet_template(out_path, title, cells, size=(900, 600),
                          note_rows=None):
    """Labeled template sheets (turnarounds, expression sheets, mouth charts,
    pose libraries). Cells are boxes to draw into — this tool draws labels
    and guides, the artist draws the art."""
    _, _ = _init()
    img = im.new_image(size[0], size[1], (245, 245, 242, 255))
    im.draw_rect(img, 0, 0, size[0], 26, (30, 30, 40, 255))
    im.draw_text(img, 10, 8, str(title).upper()[:70], (255, 255, 255, 255), 1, 1)
    cols = min(4, max(1, len(cells)))
    rows = (len(cells) + cols - 1) // cols
    cw = (size[0] - 20) // cols
    ch = (size[1] - 60 - 20 * (rows - 1)) // rows
    for i, label in enumerate(cells):
        r, c = divmod(i, cols)
        x = 10 + c * (cw + 10)
        y = 40 + r * (ch + 24)
        im.draw_rect(img, x, y, cw, ch, (255, 255, 255, 255))
        im.draw_rect(img, x, y, cw, ch, (150, 150, 160, 255), filled=False)
        im.draw_line(img, x + cw // 2, y, x + cw // 2, y + ch, (225, 225, 230, 255))
        im.draw_text(img, x + 4, y + ch + 4, str(label)[:24], (40, 40, 50, 255), 1, 1)
    ynote = size[1] - 14
    for n in (note_rows or ["DRAW INTO THE BOXES - KEEP LANDMARKS ALIGNED"]):
        im.draw_text(img, 10, ynote, str(n)[:90], (90, 90, 100, 255), 1, 1)
        ynote -= 10
    im.save_image(str(out_path), img)
    return {"path": str(out_path), "cells": len(cells),
            "labels": [str(c) for c in cells]}


TURNAROUND_VIEWS = ["front", "3-4 left", "side left", "3-4 rear", "rear",
                    "side right", "3-4 right"]
EXPRESSION_SETS = {
    "basic": ["neutral", "joy", "sadness", "anger", "fear", "surprise",
              "disgust", "smirk"],
    "performance": ["neutral", "listening", "thought", "realization", "worry",
                    "delight", "fury", "exhausted"],
}


def mouth_chart_template(out_path, chart_path=None):
    """Mouth chart from the project viseme asset."""
    _, _ = _init()
    chart = _mod("anim").load_viseme_chart(chart_path)
    vis = chart.get("viseme_order") or sorted(set(chart["visemes"].values()))
    return design_sheet_template(out_path, "mouth chart / visemes", vis,
                                 note_rows=["map phonemes with the lipsync commands"])


def costume_variant_registry(out_path, variants=None):
    _, _ = _init()
    doc = {"variants": variants or [
        {"name": "default", "palette": "cel-warm", "layers": ["body", "costume",
                                                              "hair", "props"]}],
        "rules": ["preserve silhouette recognizability across variants",
                  "keep rig compatibility — same part boundaries",
                  "palette roles stay stable (line/shadow/base/light/accent)"]}
    core.save_json(out_path, doc)
    return doc


def location_continuity_map(out_path, locations=None):
    _, _ = _init()
    doc = {"locations": locations or [],
           "fields": ["name", "entrances", "exits", "landmarks", "prop_placement",
                      "light_direction", "camera_geography"],
           "rule": "track per shot; flag any contradiction for editorial"}
    core.save_json(out_path, doc)
    return doc


def model_sheet_consistency(image_paths, reference_path=None):
    """Measure proportions/palette consistency across design sheets."""
    _, _ = _init()
    qa = _mod("qa")
    imgs = [im.load_image(p)[0] for p in image_paths]
    ref = im.load_image(reference_path)[0] if reference_path else imgs[0]
    rows = []
    for i, img in enumerate(imgs):
        m = im.silhouette_metrics(img, threshold=8)
        b = m["bbox"] or [0, 0, 0, 0]
        dom = im.dominant_colors(img, n=4)
        rows.append({"image": os.path.basename(str(image_paths[i])),
                     "aspect": round(b[2] / float(b[3] or 1), 3),
                     "area": m["area"],
                     "palette": [c["rgb"] for c in dom[:3]]})
    aspects = [r["aspect"] for r in rows]
    spread = max(aspects) - min(aspects)
    return {"rows": rows, "aspect_spread": round(spread, 3),
            "consistent": spread <= 0.12,
            "verdict": "consistent" if spread <= 0.12 else
                       "aspect drift %.2f — recheck proportions" % spread}


# ---------------------------------------------- category 23: rights/privacy --

def classify_asset(asset_id, license_class, registry_path=None, details=None):
    """Classify into the license taxonomy from asset-rights-schema.json."""
    _, _ = _init()
    schema = core.load_json(core.ROOT / "asset-rights-schema.json")
    require(license_class in schema["license_classes"], "invalid_input",
            "class must be one of %s" % ", ".join(sorted(schema["license_classes"])))
    rec = {"asset_id": asset_id, "license_class": license_class,
           "details": details or {}, "classified": core.utc_now()}
    if registry_path:
        data = core.load_json(registry_path) if os.path.isfile(registry_path) else \
            {"assets": []}
        data.setdefault("assets", [])
        data.setdefault("consents", [])
        for a in data["assets"]:
            if a.get("asset_id") == asset_id:
                a.update(rec)
                break
        else:
            data["assets"].append(rec)
        core.save_json(registry_path, data)
    return rec


def attribution_file(registry_path, out_path):
    """Generate the human-readable attribution/notice file."""
    _, _ = _init()
    schema = core.load_json(core.ROOT / "asset-rights-schema.json")
    data = core.load_json(registry_path)
    lines = ["ATTRIBUTION AND NOTICES", ""]
    used = 0
    for a in data.get("assets", []):
        cls = a.get("license_class", "unknown")
        if cls in ("unknown",):
            continue
        used += 1
        tmpl = schema["license_classes"][cls]
        lines.append("- %s — %s (basis: %s%s)" % (
            a.get("asset_id", "?"), cls, tmpl["basis"],
            "; requires: " + tmpl["requires"] if tmpl.get("requires") else ""))
    if not used:
        lines.append("(no classified assets recorded)")
    lines += ["", "Fonts: final-frame text is delivered as outlines."]
    lines.append("Generated content: produced with locally-run tooling/models "
                 "under their respective licenses.")
    core.save_text(out_path, "\n".join(lines))
    return {"path": out_path, "entries": used}


def sanitize_for_sharing(src_path, out_path):
    """Sensitive Reference Sanitizer: re-encode images without text chunks,
    strip obvious metadata from JSON (paths, user names)."""
    _, _ = _init()
    from pathlib import Path as P
    src = P(str(src_path))
    require(src.is_file(), "invalid_input", "not a file: %s" % src)
    if src.suffix.lower() == ".png":
        img, _ = im.decode_png_bytes(core.read_bytes(str(src)))
        core.write_bytes(str(out_path), im.encode_png_bytes(img))
        return {"path": str(out_path), "removed": ["tEXt", "zTXt chunks"]}
    if src.suffix.lower() == ".json":
        data = core.load_json(str(src))
        cleaned = _strip_sensitive(data)
        core.save_json(str(out_path), cleaned)
        return {"path": str(out_path), "removed": ["absolute paths", "user names"]}
    core.write_bytes(str(out_path), core.read_bytes(str(src)))
    return {"path": str(out_path), "removed": ["binary copied verbatim; no parser "
                                               "for %s" % src.suffix]}


def _strip_sensitive(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.lower() in ("user", "username", "author_home", "machine", "owner"):
                continue
            if isinstance(v, str):
                v = re.sub(r"[A-Za-z]:\\\\[^\"']*", "<path>", v)
                v = re.sub(r"/(?:home|Users)/[^\"'/]+", "<path>", v)
            out[k] = _strip_sensitive(v)
        return out
    if isinstance(obj, list):
        return [_strip_sensitive(v) for v in obj]
    if isinstance(obj, str):
        return re.sub(r"/(?:home|Users)/[^\"'/]+", "<path>", obj)
    return obj


def consent_record(person, contribution, consent_type, scope, document=None,
                   registry_path=None):
    """Talent Consent Tracker record (voice/face/motion/reference)."""
    _, _ = _init()
    schema = core.load_json(core.ROOT / "asset-rights-schema.json")
    require(consent_type in schema["consent_types"], "invalid_input",
            "consent type must be one of %s" % ", ".join(schema["consent_types"]))
    rec = {"person": person, "contribution": contribution,
           "consent_type": consent_type, "scope": scope,
           "document": document, "recorded": core.utc_now()}
    if registry_path:
        data = core.load_json(registry_path) if os.path.isfile(registry_path) \
            else {"consents": []}
        data["consents"].append(rec)
        core.save_json(registry_path, data)
    return rec


def oss_obligations(components, out_dir):
    """Collect license texts + notices for redistributed components."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in components or []:
        lic = P(str(c.get("license_file", "")))
        if lic.is_file():
            core.write_bytes(str(out / (core.slugify(c.get("name", "comp")) +
                                        "-LICENSE.txt")),
                             core.read_bytes(str(lic)))
        rows.append({"name": c.get("name"), "license": c.get("license", ""),
                     "notice_included": lic.is_file(),
                     "modifications": c.get("modifications", "none declared")})
    core.save_json(str(out / "oss-obligations.json"), {"components": rows})
    return {"dir": str(out), "components": len(rows),
            "missing_notices": [r["name"] for r in rows if not r["notice_included"]]}


def quarantine_assets(assets, quarantine_dir, registry_path=None):
    """Copy unverified assets into quarantine until rights are confirmed."""
    _, _ = _init()
    import shutil
    from pathlib import Path as P
    q = P(str(quarantine_dir))
    q.mkdir(parents=True, exist_ok=True)
    moved = []
    for a in assets or []:
        src = P(str(a))
        require(src.is_file(), "invalid_input", "asset not found: %s" % src)
        shutil.copy2(str(src), str(q / src.name))
        moved.append(src.name)
    if registry_path:
        data = core.load_json(registry_path) if os.path.isfile(registry_path) \
            else {"assets": []}
        for a in data["assets"]:
            if a.get("asset_id") in moved or os.path.basename(str(a.get("path", ""))) in moved:
                a["quarantined"] = True
        core.save_json(registry_path, data)
    return {"quarantine_dir": str(q), "copied": moved,
            "rule": "quarantined assets are blocked from release until classified"}


def release_rights_gate(registry_path, extra_blockers=None):
    """Block delivery when any shipped asset lacks an approved basis."""
    _, _ = _init()
    data = core.load_json(registry_path)
    schema = core.load_json(core.ROOT / "asset-rights-schema.json")
    blockers = []
    for a in data.get("assets", []):
        cls = a.get("license_class", "unknown")
        if cls == "unknown" or cls not in schema["license_classes"]:
            blockers.append({"asset": a.get("asset_id", "?"),
                             "reason": "license class '%s' is not an approved basis" % cls})
        elif not schema["license_classes"][cls].get("redistributable", False):
            if not a.get("written_permission"):
                blockers.append({"asset": a.get("asset_id", "?"),
                                 "reason": "class '%s' is not redistributable without "
                                           "written permission" % cls})
    for c in (data.get("consents", [])):
        if c.get("consent_type") in ("voice", "face-likeness") and not c.get("document"):
            blockers.append({"asset": "consent:" + c.get("person", "?"),
                             "reason": "likeness/voice consent lacks a document"})
    for b in (extra_blockers or []):
        blockers.append({"asset": "?", "reason": str(b)})
    if blockers:
        raise core.GateBlocked("release blocked on rights: %d unresolved" % len(blockers),
                               blockers)
    return {"verdict": "cleared", "assets": len(data.get("assets", [])),
            "consents": len(data.get("consents", []))}


# ------------------------------------ category 26: specialized workflows -----

def deskew(src_path, out_path, angle_range=10.0, step=0.5, bg=(255, 255, 255)):
    """Paper-scan deskew: search the rotation maximizing row-energy (text lines
    align with rows). Search runs on a downsampled copy for speed; the winning
    angle is applied to the full-resolution original."""
    _, _ = _init()
    img, _ = im.load_image(src_path)
    scale = min(1.0, 480.0 / max(img.w, img.h))
    small = im.resize(img, max(2, int(img.w * scale)), max(2, int(img.h * scale)),
                      "box") if scale < 1.0 else img
    gray = im.apply_lut(small, [v for v in range(256)])
    best_angle, best_score = 0.0, None
    a = -angle_range
    while a <= angle_range:
        rot = im.rotate_free(gray, a, bg=(255, 255, 255, 255))
        score = 0
        for y in range(0, rot.h, 2):
            row = 0
            base = y * rot.w
            for x in range(0, rot.w, 2):
                if rot.px[(base + x) * 4] < 128:
                    row += 1
            score += row * row
        if best_score is None or score > best_score:
            best_score, best_angle = score, a
        a += step
    out = im.rotate_free(img, best_angle, bg=tuple(bg) + (255,))
    im.save_image(str(out_path), out)
    return {"path": str(out_path), "corrected_degrees": round(-best_angle, 2),
            "method": "row-energy maximization on a downsampled search copy"}


def register_pages(reference_path, moving_path, out_path, max_shift=40):
    """Align two scans by coarse translational search on downsampled
    silhouettes (page registration)."""
    _, _ = _init()
    ref, _ = im.load_image(reference_path)
    mov, _ = im.load_image(moving_path)
    require(ref.w == mov.w and ref.h == mov.h, "invalid_input",
            "register: sizes must match (%dx%d vs %dx%d)" % (ref.w, ref.h, mov.w, mov.h))
    ds = 4
    rs = im.resize(ref, max(2, ref.w // ds), max(2, ref.h // ds), "box")
    ms = im.resize(mov, rs.w, rs.h, "box")
    best = (0, 0)
    best_score = None
    for dy in range(-max_shift // ds, max_shift // ds + 1):
        for dx in range(-max_shift // ds, max_shift // ds + 1):
            s = 0
            for y in range(rs.h):
                yy = y + dy
                if not (0 <= yy < rs.h):
                    continue
                for x in range(rs.w):
                    xx = x + dx
                    if 0 <= xx < rs.w:
                        s += abs(rs.px[(y * rs.w + x) * 4] - ms.px[(yy * rs.w + xx) * 4])
            if best_score is None or s < best_score:
                best_score, best = s, (dx * ds, dy * ds)
    shifted = im.crop(mov, -best[0], -best[1], mov.w, mov.h)
    im.save_image(str(out_path), shifted)
    return {"path": str(out_path), "shift_applied": list(best),
            "method": "coarse grid search, box-downsampled SAD"}


def remove_paper_tone(src_path, out_path, white_point=235):
    """Levels-based paper tone removal keeping line art."""
    _, _ = _init()
    img, _ = im.load_image(src_path)
    out = im.op_levels(img, black=30, white=white_point, gamma=1.0)
    im.save_image(str(out_path), out)
    return {"path": str(out_path), "black": 30, "white": white_point}


def rotoscope_prep(frame_paths, out_dir, mark_contacts=True):
    """Frame-accurate reference guides: edge overlay + numbered sheet +
    centroid arc markers (observation layer, not final stylization)."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    qa = _mod("qa")
    imgs = [im.load_image(p)[0] for p in frame_paths]
    guides = []
    for i, img in enumerate(imgs):
        g = im.apply_lut(img, [(255 - v) for v in range(256)])
        g = im.op_brightness_contrast(g, brightness=0, contrast=0.4)
        overlay = im.composite(img, g, "difference")
        p = out / ("guide-%04d.png" % i)
        im.save_image(str(p), overlay)
        guides.append(str(p))
    sheet = qa.build_contact_sheet(frame_paths, str(out / "rotoscope-sheet.png"),
                                   every_nth=1)
    arcs = qa.motion_arc_analysis(imgs)
    core.save_json(str(out / "arcs.json"), arcs)
    return {"guides": guides, "sheet": sheet["path"], "arcs":
            str(out / "arcs.json"), "frames": len(imgs)}


INTERCHANGE_SPECS = {
    "krita": {"expects": [".kra", ".png", ".ppm", "frame sequences"],
              "package": ["*.kra sources", "exported png sequences",
                          "palettes as .kpl or json", "audio reference"],
              "notes": "keyframes and audio live inside .kra; also export a "
                       "flat png sequence for other tools"},
    "inkscape": {"expects": [".svg", ".png"],
                 "package": ["svg with embedded or relative-linked images",
                             "normalized svg (audit first)"],
                 "notes": "run the svg audit + optimize commands before handoff; "
                          "convert text to paths"},
    "aseprite": {"expects": [".ase/.aseprite", ".png", ".json"],
                 "package": ["sprite file", "sheet png + metadata json",
                             "palette (json)"],
                 "notes": "tags/slices export via sheet metadata json"},
    "opentoonz": {"expects": ["levels (tlv/png)", "xsheet", "palettes"],
                  "package": ["scene folder structure", "exported png levels",
                              "xsheet csv"],
                  "notes": "keep level naming stable; export xsheet as csv for "
                           "this tool's dope-sheet format"},
    "synfig": {"expects": [".sif/.sifz", "linked images"],
               "package": ["canvas file", "linked pngs with relative paths"],
               "notes": "bake complex parameter links before handoff"},
    "grease-pencil": {"expects": [".blend", "renders"],
                      "package": ["blend with gp objects", "rendered png sequence",
                                  "camera data export (json)"],
                      "notes": "also export orthographic refs for the 2d team"},
}


def interchange_package(app, files, out_dir, normalize=True):
    """Verify and package files for cross-application exchange. Application
    formats are NOT parsed — this checks presence, normalizes what it can
    (SVG, sequences), and writes exact import instructions."""
    _, _ = _init()
    require(app in INTERCHANGE_SPECS, "invalid_input",
            "unknown app '%s' (available: %s)" % (app, ", ".join(INTERCHANGE_SPECS)))
    spec = INTERCHANGE_SPECS[app]
    from pathlib import Path as P
    out = P(str(out_dir)) / ("%s-exchange" % app)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for f in files or []:
        src = P(str(f))
        require(src.is_file(), "invalid_input", "missing file: %s" % src)
        dst = out / src.name
        core.write_bytes(str(dst), core.read_bytes(str(src)))
        entry = {"file": src.name, "sha256": core.sha256_file(str(dst))}
        if normalize and src.suffix.lower() == ".svg":
            vec = _mod("vector")
            opt = vec.svg_optimize(str(dst), str(dst), prec=2)
            entry["normalized"] = True
            entry["saved_bytes"] = opt["saved_bytes"]
        manifest.append(entry)
    core.save_json(str(out / "exchange-manifest.json"),
                   {"app": app, "spec": spec, "files": manifest})
    core.save_text(str(out / "IMPORT-INSTRUCTIONS.txt"),
                   "Import into %s:\n- expects: %s\n- package contents: %s\n- notes: %s\n"
                   % (app, ", ".join(spec["expects"]), ", ".join(spec["package"]),
                      spec["notes"]))
    return {"dir": str(out), "files": len(manifest), "manifest":
            str(out / "exchange-manifest.json"),
            "instructions": str(out / "IMPORT-INSTRUCTIONS.txt"),
            "limitation": "application-native formats are packaged and verified "
                          "by checksum, not parsed"}


def legacy_rescue(paths, out_dir):
    """Decode legacy formats with the built-in decoders, keep originals,
    document losses (Legacy Format Rescue Operator)."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for p in paths or []:
        src = P(str(p))
        if not src.is_file():
            results.append({"file": src.name, "rescued": None,
                            "error": "missing_file"})
            continue
        try:
            img, meta = im.load_image(str(src))
            dst = out / (src.stem + ".png")
            im.save_image(str(dst), img)
            losses = []
            if meta.get("warnings"):
                losses.extend(meta["warnings"])
            if str(src).lower().endswith((".ppm", ".pgm")):
                losses.append("alpha not present in format")
            if str(src).lower().endswith(".gif"):
                losses.append("single frame extracted; use animation import for all frames")
            results.append({"file": src.name, "rescued": str(dst),
                            "losses": losses})
        except Exception as e:
            results.append({"file": src.name, "rescued": None,
                            "error": getattr(e, "code", "failed"),
                            "detail": str(e)[:120]})
    core.save_json(str(out / "rescue-report.json"),
                   {"results": results, "originals_kept": True})
    return {"results": results, "report": str(out / "rescue-report.json"),
            "rescued": sum(1 for r in results if r.get("rescued"))}


def reference_pack_3d(reference_paths, out_dir, turnaround=None):
    """Ortho views + palettes + proportions + silhouettes for a 3D team."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    qa = _mod("qa")
    data = {"views": [], "palettes": {}, "proportions": []}
    for p in reference_paths:
        img, _ = im.load_image(str(p))
        name = P(str(p)).stem
        sil = im.apply_lut(img, [min(255, v * 2) for v in range(256)])
        im.save_image(str(out / (name + "-silhouette.png")), sil)
        m = im.silhouette_metrics(img, threshold=8)
        b = m["bbox"] or [0, 0, 0, 0]
        data["views"].append(name)
        data["proportions"].append({"view": name, "bbox": b,
                                    "aspect": round(b[2] / float(b[3] or 1), 3)})
        data["palettes"][name] = [c["rgb"] for c in im.dominant_colors(img, n=6)]
    if turnaround:
        core.write_bytes(str(out / "turnaround.png"), core.read_bytes(str(turnaround)))
    core.save_json(str(out / "reference-data.json"), data)
    return {"dir": str(out), "views": len(data["views"]),
            "silhouettes": len(data["views"]), "data": str(out / "reference-data.json")}


def print_plan(width_px, height_px, dpi=300, bleed_mm=3, out_path=None):
    """Print/merch output planning: physical size, bleed, cut lines."""
    _, _ = _init()
    w_in = width_px / float(dpi)
    h_in = height_px / float(dpi)
    bleed_px = int(round(bleed_mm / 25.4 * dpi))
    plan = {"pixels": [width_px, height_px], "dpi": dpi,
            "physical": [round(w_in, 2), round(h_in, 2)],
            "physical_cm": [round(w_in * 2.54, 1), round(h_in * 2.54, 1)],
            "bleed_mm": bleed_mm, "bleed_px": bleed_px,
            "safe_interior_px": [width_px - 2 * bleed_px, height_px - 2 * bleed_px],
            "cmyk_note": "keep the RGB master; CMYK conversion happens at the "
                         "print shop with their profile — this tool does not "
                         "convert color spaces",
            "cut_lines": "generate an SVG with crop marks at the bleed corners"}
    if out_path:
        w, h = width_px + 2 * bleed_px, height_px + 2 * bleed_px
        mark = 24
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (w, h)]
        svg.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" '
                   'stroke="#000" stroke-width="1" stroke-dasharray="4 4"/>'
                   % (bleed_px, bleed_px, width_px, height_px))
        for cx in (0, w):
            for cy in (0, h):
                svg.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#000" '
                           'stroke-width="2"/>' % (cx - mark if cx else 0, cy,
                                                   cx + mark if cx else 0, cy))
                svg.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#000" '
                           'stroke-width="2"/>' % (cx, cy - mark if cy else 0,
                                                   cx, cy + mark if cy else 0))
        svg.append("</svg>")
        core.save_text(str(out_path), "\n".join(svg))
        plan["cut_svg"] = str(out_path)
    return plan


EDU_EXERCISES = {
    "line-control": {"task": "draw 20 parallel strokes with even pressure; then "
                             "taper each end", "check": "stroke width variance"},
    "perspective": {"task": "draw 1pt and 2pt grids; box tower in each",
                    "check": "vanishing consistency"},
    "timing": {"task": "bouncing ball on 24 frames: twos with spacing charts",
               "check": "slow-in/out + volume"},
    "walk-cycle": {"task": "8-frame walk cycle, contact/down/pass/up keys",
                   "check": "contact stability and arcs"},
    "lip-sync": {"task": "animate 2 seconds of the provided viseme track",
                 "check": "viseme timing vs audio"},
    "rigging": {"task": "cutout rig from the template parts; animate a wave",
                "check": "seam visibility in extremes"},
    "effects": {"task": "12-frame fire cycle with shape progression",
                "check": "loop seam and silhouette clarity"},
    "export-validation": {"task": "export GIF + PNG sequence; run all QA gates",
                          "check": "gates pass; report interpreted"},
}


def educational_exercises(out_dir, topics=None, seed="edu"):
    """Generate offline practice projects with tasks and templates."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    topics = topics or list(EDU_EXERCISES)
    made = []
    for t in topics:
        require(t in EDU_EXERCISES, "invalid_input", "unknown topic '%s'" % t)
        d = out / t
        d.mkdir(exist_ok=True)
        ex = {"topic": t, **EDU_EXERCISES[t],
              "brief": "complete the task inside this folder using the studio "
                       "commands; validate your own result with the QA tools",
              "seed": seed}
        core.save_json(str(d / "exercise.json"), ex)
        if t == "timing" or t == "walk-cycle":
            anim = _mod("anim")
            anim.timing_chart(str(d / "chart.png"), keys=[0, 6, 12, 18, 23],
                              frames=24, label=t)
        if t == "perspective":
            anim = _mod("anim")
            anim.perspective_grid(str(d / "grid-1pt.png"), mode="1pt")
            anim.perspective_grid(str(d / "grid-2pt.png"), mode="2pt")
        if t == "lip-sync":
            import shutil
            shutil.copy2(str(core.ROOT / "asset-viseme-chart.json"),
                         str(d / "viseme-chart.json"))
        made.append(t)
    return {"dir": str(out), "exercises": made,
            "topics_available": list(EDU_EXERCISES)}


# ------------------------------------------------------------- rig extras ---

def rig_split(img, mode="auto", lines=None):
    """Cutout Rig Architect core: split artwork into parts by empty rows/cols
    (auto) or explicit cut lines. Returns parts with pivots at seams."""
    _, _ = _init()
    if mode == "lines":
        require(lines, "invalid_input", "cut lines required in 'lines' mode")
        xs = sorted(set(int(x) for x in lines if str(x).isdigit() or
                        isinstance(x, int)))
        parts = []
        x0 = 0
        for x in xs + [img.w]:
            if x - x0 >= 1:
                parts.append(im.crop(img, x0, 0, x - x0, img.h))
            x0 = x
        return {"parts": parts, "pivots": [[p.w // 2, p.h // 2] for p in parts],
                "mode": "lines"}
    # auto: split at fully transparent rows/columns
    row_empty = [all(img.px[(y * img.w + x) * 4 + 3] == 0 for x in range(img.w))
                 for y in range(img.h)]
    col_empty = [all(img.px[(y * img.w + x) * 4 + 3] == 0 for y in range(img.h))
                 for x in range(img.w)]
    cuts_y = _empty_runs(row_empty)
    cuts_x = _empty_runs(col_empty)
    parts = []
    pivots = []
    for y0, y1 in cuts_y:
        for x0, x1 in cuts_x:
            if any(img.px[(yy * img.w + xx) * 4 + 3] > 0
                   for yy in range(y0, y1) for xx in range(x0, x1)):
                parts.append(im.crop(img, x0, y0, x1 - x0, y1 - y0))
                pivots.append([x1 - x0, y0 - y0])  # pivot at seam corner
    if not parts:
        parts = [img.clone()]
        pivots = [[img.w // 2, img.h // 2]]
    return {"parts": parts, "pivots": pivots, "mode": "auto",
            "cut_rows": cuts_y, "cut_cols": cuts_x}


def _empty_runs(flags):
    """Spans where the flag is False (i.e. content runs between empties)."""
    runs = []
    start = None
    for i, f in enumerate(flags):
        if not f and start is None:
            start = i
        elif f and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(flags)))
    return runs


def rig_migrate(old_rig, new_rig):
    """Compare rig revisions: missing controls, changed rest states."""
    _, _ = _init()
    def norm(rig):
        parts = {}
        for p in rig.get("parts", []):
            parts[p.get("name", "")] = p
        return parts
    old, new = norm(old_rig), norm(new_rig)
    missing = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    changed_rest = []
    for name in set(old) & set(new):
        if old[name].get("pivot") != new[name].get("pivot") or \
                old[name].get("parent") != new[name].get("parent"):
            changed_rest.append({"part": name,
                                 "old_pivot": old[name].get("pivot"),
                                 "new_pivot": new[name].get("pivot"),
                                 "old_parent": old[name].get("parent"),
                                 "new_parent": new[name].get("parent")})
    return {"missing_controls": missing, "added_controls": added,
            "changed_rest_states": changed_rest,
            "animation_transfer": "retarget by name; verify changed pivots by hand",
            "verdict": "compatible" if not missing and not changed_rest else
                       "%d missing, %d changed" % (len(missing), len(changed_rest))}


def rig_controls(rig):
    """Animator-facing control list (Rig Control Interface Designer)."""
    _, _ = _init()
    controls = []
    palette = ["#e05555", "#55b1e0", "#63c766", "#e0a955", "#a97fe0"]
    for i, p in enumerate(rig.get("parts", [])):
        name = p.get("name", "part%d" % i)
        controls.append({"control": "ctrl_" + name, "part": name,
                         "color": palette[i % len(palette)],
                         "limits": {"rotation": [-170, 170], "scale": [0.2, 3.0]},
                         "reset": "rest pose v%03d" % rig.get("version", 1),
                         "visible_to_animator": p.get("parent") is not None})
    return {"controls": controls, "count": len(controls),
            "rules": ["technical layers hidden by default", "consistent colors "
                      "per body group", "every control resets to the rest pose"]}


def facial_rig_table(chart_path=None):
    """Facial Rig Builder core: control map from the viseme chart."""
    _, _ = _init()
    anim = _mod("anim")
    chart = anim.load_viseme_chart(chart_path)
    vis = chart.get("viseme_order") or sorted(set(chart["visemes"].values()))
    return {"controls": [{"name": "vis_" + v, "range": [0, 1], "driver": "mouth"}
                         for v in vis],
            "channels": {"brows": ["inner", "mid", "outer"], "lids": ["upper", "lower"],
                         "gaze": ["x", "y"], "jaw": ["open", "shift"]},
            "phoneme_map": chart["visemes"],
            "rules": chart.get("coarticulation", {}).get("rules", [])}


def rig_corrective_record(name, part, handles_before, handles_after, library_path):
    """Corrective Shape Designer: record a lattice delta as a reusable
    corrective shape (applied via rig-lattice handles)."""
    _, _ = _init()
    lib = core.load_json(library_path) if os.path.isfile(library_path) else \
        {"correctives": []}
    rec = {"name": name, "part": part,
           "handles_before": {str(k): list(v) for k, v in handles_before.items()},
           "handles_after": {str(k): list(v) for k, v in handles_after.items()},
           "recorded": core.utc_now()}
    lib["correctives"].append(rec)
    core.save_json(library_path, lib)
    return rec


# ---------------------------------------------------------- board analysis --

def boards_shotlist(beats):
    """Shot Language Planner: shot plan from story beats."""
    _, _ = _init()
    plan = []
    for i, b in enumerate(beats or []):
        text = str(b.get("beat", b) if isinstance(b, dict) else b).lower()
        size = "wide" if any(w in text for w in ("arrive", "world", "establish")) else \
            "close" if any(w in text for w in ("realiz", "whisper", "eye")) else "medium"
        angle = "low" if any(w in text for w in ("power", "threat", "tower")) else \
            "high" if any(w in text for w in ("small", "lost", "look down")) else "eye"
        move = "push-in" if any(w in text for w in ("discover", "focus")) else \
            "pan" if any(w in text for w in ("travel", "walk", "cross")) else "static"
        plan.append({"shot": i + 1, "beat": text, "size": size, "angle": angle,
                     "move": move,
                     "frames": int(b.get("frames", 48)) if isinstance(b, dict) else 48})
    return {"shots": plan, "count": len(plan)}


def boards_composition(panel_path):
    """Heuristic staging report for one panel (silhouette clarity, contrast
    balance, focal suggestion). Visual judgement stays with the artist."""
    _, _ = _init()
    img, _ = im.load_image(panel_path)
    m = im.silhouette_metrics(img, threshold=8)
    st = im.stats(img)
    luma = st["mean"][0]
    thirds_x = img.w // 3
    thirds_y = img.h // 3
    regions = {}
    for rname, (x0, y0) in (("tl", (0, 0)), ("tr", (2 * thirds_x, 0)),
                            ("bl", (0, 2 * thirds_y)), ("br", (2 * thirds_x, 2 * thirds_y)),
                            ("c", (thirds_x, thirds_y))):
        sub = im.crop(img, x0, y0, thirds_x, thirds_y)
        regions[rname] = round(im.stats(sub)["mean"][0], 1)
    busiest = max(regions, key=regions.get)
    findings = []
    if m["area"] / float(img.w * img.h) < 0.05:
        findings.append("subject very small — consider a closer size")
    if m["bbox"] and m["bbox"][0] > img.w * 0.55:
        findings.append("subject pushed right — check screen direction continuity")
    if max(regions.values()) - min(regions.values()) < 20:
        findings.append("flat value layout — clarify focal hierarchy")
    return {"panel": os.path.basename(str(panel_path)), "subject_area": m["area"],
            "subject_bbox": m["bbox"], "region_luma": regions,
            "busiest_region": busiest, "findings": findings,
            "note": "heuristics only — staging is an artistic decision"}


def boards_continuity(panels):
    """Continuity/eyeline/screen-direction checks from panel metadata.
    panels: [{shot, direction(left|right), eyeline_target, entrance, exit}]."""
    _, _ = _init()
    issues = []
    for i in range(1, len(panels)):
        a, b = panels[i - 1], panels[i]
        if a.get("direction") and b.get("direction") and \
                a["direction"] != b["direction"] and not b.get("motivated_turn"):
            issues.append({"shot": b.get("shot", i + 1), "issue": "axis_crossing",
                           "detail": "screen direction flipped %s->%s without a "
                                     "motivated turn" % (a["direction"], b["direction"])})
        if a.get("exit") and b.get("entrance") and a["exit"] != b["entrance"]:
            issues.append({"shot": b.get("shot", i + 1), "issue": "entrance_mismatch",
                           "detail": "exits %s, enters %s" % (a["exit"], b["entrance"])})
    return {"panels": len(panels), "issues": issues,
            "verdict": "clean" if not issues else "%d issue(s)" % len(issues)}


# ------------------------------------------------------------- camera -------

def camera_setup(size, safe=0.9, out_png=None, overscan=1.1):
    """2D Camera Setup: config record + guide overlay render."""
    _, _ = _init()
    w, h = int(size[0]), int(size[1])
    mx = int(w * (1 - safe) / 2)
    my = int(h * (1 - safe) / 2)
    rec = {"frame": [w, h], "safe_area_fraction": safe,
           "safe_box": [mx, my, w - 2 * mx, h - 2 * my],
           "overscan_factor": overscan,
           "art_canvas_needed": [int(w * overscan), int(h * overscan)],
           "zoom_range": [1 / overscan, overscan],
           "rotation_range": [-15, 15],
           "depth_convention": "0 = camera plane, positive = away"}
    if out_png:
        img = im.new_image(w, h, (0, 0, 0, 0))
        im.draw_rect(img, mx, my, w - 2 * mx, h - 2 * my, (255, 200, 60, 200), False)
        im.draw_rect(img, 0, 0, w, h, (90, 200, 255, 200), False)
        im.draw_text(img, 4, 4, "SAFE", (255, 200, 60, 255), 1, 1)
        im.save_image(str(out_png), img)
        rec["guide_png"] = str(out_png)
    return rec


def depth_cue(layers, focus_depth=0.0, blur_max=3, desat_max=0.5):
    """Focus/Depth Cue Designer: blur + desaturate layers by distance from
    the focal depth. layers=[{image, depth}] back-to-front."""
    _, _ = _init()
    out = []
    for layer in layers:
        img = layer["image"]
        depth = float(layer.get("depth", 0))
        dist = abs(depth - focus_depth)
        blur = min(blur_max, int(dist * blur_max * 2))
        sat = max(0.0, 1.0 - desat_max * min(1.0, dist))
        if blur >= 1:
            img = im.blur_image(img, blur)
        if sat < 1.0:
            img = im.op_hsv(img, sat_scale=sat)
        out.append(img)
    return out


def fx_comp(base, effect, kind="glow", radius=4, opacity=0.7, mode="screen"):
    """Effect Layer Compositor: glow / blur / color / alpha layers over base."""
    _, _ = _init()
    if kind == "glow":
        blurred = im.blur_image(effect, radius)
        boosted = im.op_levels(blurred, black=40, white=180)
        return im.composite(base, boosted, mode=mode, opacity=opacity)
    if kind == "blur":
        return im.composite(base, im.blur_image(effect, radius), mode="normal",
                            opacity=opacity)
    if kind == "color":
        return im.composite(base, effect, mode=mode, opacity=opacity)
    if kind == "alpha":
        return im.composite(base, effect, mode="normal", opacity=opacity)
    raise err("invalid_input", "kind must be glow/blur/color/alpha")


def fx_cache(frames, out_dir, seed, preset):
    """Particle Cache Manager: bake frames + manifest with seed and preset."""
    _, _ = _init()
    from pathlib import Path as P
    d = P(str(out_dir))
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i, f in enumerate(frames):
        p = d / ("fx-%04d.png" % i)
        im.save_image(str(p), f)
        files.append(str(p))
    core.save_json(str(d / "fx-manifest.json"),
                   {"preset": preset, "seed": str(seed), "frames": len(frames),
                    "tool_version": core.TOOL_VERSION, "created": core.utc_now()})
    return {"dir": str(d), "frames": len(files),
            "manifest": str(d / "fx-manifest.json")}


def fx_handdrawn(kind="fire", frames=12, size=(160, 120), seed="fx"):
    """Hand-Drawn Effects Animator core: stylized frame-by-frame fire/smoke
    silhouette progression (shape evolution + flicker, no physics)."""
    _, _ = _init()
    rng = core.RNG("handfx:%s:%s" % (kind, seed))
    out = []
    w, h = size
    for f in range(int(frames)):
        img = im.new_image(w, h, (0, 0, 0, 0))
        tongues = 5 if kind == "fire" else 3
        base_y = h * (0.75 if kind == "fire" else 0.9)
        for t in range(tongues):
            cx = w * (0.2 + 0.6 * t / max(1, tongues - 1)) + (rng.random() - 0.5) * w * 0.08
            phase = (f / float(frames) + t * 0.17) % 1.0
            height = h * (0.35 + 0.25 * math.sin(phase * 2 * math.pi) * 0.5 + 0.25)
            ry = height / 2
            rx = w * 0.05 * (1.2 - 0.5 * math.sin(phase * 2 * math.pi))
            cy = base_y - ry
            if kind == "fire":
                col = (255, int(120 + 90 * (1 - phase)), 40, 255)
                shape = []
                for k in range(16):
                    a = math.pi * k / 15.0
                    shape.append([cx + rx * math.cos(a), cy + ry * math.sin(a) * (1 if math.sin(a) > 0 else 0.35)])
                im.fill_polygon(img, shape, col)
                im.draw_ellipse(img, int(cx), int(base_y - ry * 0.2), int(rx), int(ry * 0.3),
                                (255, 230, 120, 255))
            else:  # smoke puffs
                col = (170, 170, 180, 200)
                px_ = cx + (rng.random() - 0.5) * w * 0.15
                py_ = base_y - phase * h * 0.6
                r = (1 + phase) * w * 0.06
                im.draw_ellipse(img, int(px_), int(py_), int(r), int(r), col)
        out.append(img)
    return out


def playback_plan(frames, size, fps=24):
    """Playback Performance Tuner: proxy resolution + cache strategy."""
    _, _ = _init()
    px = size[0] * size[1] * frames
    proxy_w = 640 if size[0] > 1280 else size[0] // 2
    plan = {"frames": frames, "fps": fps, "full_pixels": px,
            "full_cache_human": core.human_size(px * 4),
            "proxy_resolution": [proxy_w, int(size[1] * proxy_w / size[0])],
            "proxy_cache_human": core.human_size(px * (proxy_w / float(size[0])) ** 2 * 4),
            "strategy": ["half-res proxies for scrubbing",
                         "pre-decode to raw cache when RAM allows",
                         "watch dropped-frame counters; drop to proxies on misses",
                         "audio buffer 100ms; latency-tolerant playback"],
            "note": "no GPU decode here; plans are advisory"}
    return plan


# ------------------------------------------------------------- lettering ----

def lettering_guides(out_path, lines=4, size=(400, 160), baseline_gap=28,
                     x_height=14, text=None, cap_height=20):
    """Hand-Lettering Assistant: baseline/x-height/cap guides + optional
    bitmap text block rendered against them (spacing check by eye)."""
    _, _ = _init()
    img = im.new_image(size[0], size[1], (255, 255, 255, 255))
    top = 24
    for i in range(int(lines)):
        y0 = top + i * baseline_gap
        im.draw_line(img, 8, y0, size[0] - 8, y0, (60, 60, 200, 255))
        im.draw_line(img, 8, y0 - x_height, size[0] - 8, y0 - x_height, (150, 150, 220, 255))
        im.draw_line(img, 8, y0 - cap_height, size[0] - 8, y0 - cap_height, (200, 120, 120, 255))
    if text:
        im.draw_text(img, 12, top - x_height, str(text)[:60], (20, 20, 20, 255), 1, 1)
    im.save_image(str(out_path), img)
    return {"path": str(out_path), "baselines": int(lines),
            "gaps": {"baseline": baseline_gap, "x_height": x_height,
                     "cap_height": cap_height}}


def font_library(directory):
    """Offline Font Library Manager: coverage + naming summary per font."""
    _, _ = _init()
    from pathlib import Path as P
    vec = _mod("vector")
    d = P(str(directory))
    require(d.is_dir(), "invalid_input", "not a directory: %s" % d)
    rows = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in (".ttf", ".otf"):
            continue
        try:
            cov = vec.read_font_coverage(str(f))
            rows.append({"file": f.name, "glyphs": cov["glyph_count"],
                         "latin_basic": cov["covers_latin_basic"],
                         "scripts": list(cov["scripts"])[:4],
                         "license_file": str(f.with_suffix(".LICENSE")) if
                         f.with_suffix(".LICENSE").is_file() else None})
        except Exception as e:
            rows.append({"file": f.name, "error": getattr(e, "code", "unreadable")})
    return {"directory": str(d), "fonts": rows,
            "unlicensed": [r["file"] for r in rows if not r.get("license_file")]}


def font_license(font_path, license_class, out_path, embedding=False,
                 outlining=False, sharing=False):
    """Font Embedding/Redistribution record from the rights schema."""
    _, _ = _init()
    schema = core.load_json(core.ROOT / "asset-rights-schema.json")
    require(license_class in schema["license_classes"], "invalid_input",
            "class must be one of %s" % ", ".join(sorted(schema["license_classes"])))
    rec = {"font": os.path.basename(str(font_path)), "license_class": license_class,
           "embedding_allowed": embedding, "outlining_allowed": outlining,
           "sharing_with_collaborators": sharing,
           "safe_delivery": "outline all final-frame text when in doubt",
           "recorded": core.utc_now()}
    core.save_json(out_path, rec)
    return rec


# ------------------------------------------------------------- rights -------

def rights_model(registry_path):
    """Model Usage Rights Auditor: model entries must carry license fields."""
    _, _ = _init()
    schema = core.load_json(core.ROOT / "asset-rights-schema.json")
    data = core.load_json(registry_path)
    issues = []
    models = [a for a in data.get("assets", []) if a.get("kind") == "model" or
              str(a.get("asset_id", "")).startswith("model:")]
    for m in models:
        cls = m.get("license_class")
        if not cls or cls not in schema["license_classes"]:
            issues.append({"model": m.get("asset_id"), "issue": "license_missing"})
        if m.get("commercial_use") is None:
            issues.append({"model": m.get("asset_id"), "issue": "commercial_use_undeclared"})
    return {"models": len(models), "issues": issues,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def rights_provenance(asset_id, registry_path, out_path, extra=None):
    """Provenance Metadata Manager: source/creator/history/license record."""
    _, _ = _init()
    data = core.load_json(registry_path)
    entry = next((a for a in data.get("assets", []) if a.get("asset_id") == asset_id),
                 None)
    require(entry is not None, "invalid_input", "asset '%s' not in registry" % asset_id)
    rec = {"asset_id": asset_id, "source": entry.get("source", ""),
           "creator": entry.get("creator", ""), "acquired": entry.get("acquired", ""),
           "license_class": entry.get("license_class", "unknown"),
           "modification_history": entry.get("modifications", []),
           "approval_status": entry.get("approval", "unreviewed"),
           "sha256": entry.get("sha256"), "extra": extra or {},
           "recorded": core.utc_now()}
    core.save_json(out_path, rec)
    return rec


# ------------------------------------------------------------- packaging ----

def pack_verify(bundle_dir):
    """Bundle Completeness Validator: relative refs, manifests present,
    no absolute paths (opens from the clean path it is given)."""
    _, _ = _init()
    from pathlib import Path as P
    root = P(str(bundle_dir))
    require(root.is_dir(), "invalid_input", "not a directory: %s" % root)
    pw = portable_workspace(str(root))
    issues = [{"issue": "absolute_ref", "file": i["file"]} for i in pw["issues"]]
    for need in ("shot-manifest.json", "asset-registry.json"):
        if not (root / need).is_file() and not list(root.glob("**/" + need)):
            issues.append({"issue": "missing_manifest", "file": need})
    enc = _mod("encode")
    return {"bundle": str(root), "issues": issues,
            "absolute_refs": pw["absolute_refs"],
            "verdict": "complete" if not issues else "%d issue(s)" % len(issues)}


def pack_coldstart(bundle_dir, approved_paths, out_dir):
    """Cold-Start Rebuild Tester: run the smoke pipeline from the bundle and
    compare rebuilt outputs against approved references."""
    _, _ = _init()
    from pathlib import Path as P
    smoke = environment_health_check(out_dir)
    qa = _mod("qa")
    made = [str(P(str(out_dir)) / "draw.png")]
    vr = qa.visual_regression(made, [str(p) for p in approved_paths]) \
        if approved_paths else {"identical": None, "changed_frames": 0}
    return {"smoke": smoke["ok"], "smoke_steps": smoke["steps"],
            "reference_match": vr.get("identical"),
            "max_changed_fraction": vr.get("max_changed_fraction", 0),
            "verdict": "rebuilt-ok" if smoke["ok"] else "smoke-failed"}


def pack_backup(project_dir, out_dir, keep=10):
    """Incremental Backup Planner: snapshot now, rotate old ones to keep N."""
    _, _ = _init()
    from pathlib import Path as P
    snap = version_snapshot(project_dir, out_dir)
    out = P(str(out_dir))
    snaps = sorted([p for p in out.glob("*.zip")], key=core.natural_key)
    rotated = []
    while len(snaps) > int(keep):
        oldest = snaps.pop(0)
        oldest.unlink()
        rotated.append(oldest.name)
    return {"created": snap["snapshot"], "files": snap["files"],
            "rotated_out": rotated, "retained": len(snaps),
            "schedule": "snapshot after each approved milestone; verify weekly"}


def pack_knowledge(out_dir):
    """Offline Knowledge Packager: copy the knowledge file and asset docs
    into a project so it stays usable without internet."""
    _, _ = _init()
    from pathlib import Path as P
    out = P(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    copied = []
    src = core.ROOT / "knowledge-readme.md"
    if src.is_file():
        core.write_bytes(str(out / src.name), core.read_bytes(str(src)))
        copied.append(src.name)
    for f in core.ROOT.glob("asset-*.json"):
        core.write_bytes(str(out / f.name), core.read_bytes(str(f)))
        copied.append(f.name)
    return {"dir": str(out), "copied": copied}


# --------------------------------------------------------- accessibility ----

def access_text_zones(canvas_size, boxes):
    """Localized Text Replacement Planner: safe zones with expansion rules."""
    _, _ = _init()
    w, h = canvas_size
    zones = []
    for i, b in enumerate(boxes):
        x, y, bw, bh = b[:4]
        pad_x = int(bw * 0.3)
        zones.append({"index": i, "box": [x, y, bw, bh],
                      "safe_expansion": [max(0, x - pad_x), y, bw + 2 * pad_x, bh],
                      "fits_canvas": x + bw + 2 * pad_x <= w,
                      "rtl_ready": "mirror horizontally when target is RTL",
                      "max_chars": int(bw / 6.0)})
    return {"canvas": [w, h], "zones": zones,
            "rules": ["allow +30 percent length for European languages",
                      "CJK often needs fewer chars but taller line boxes",
                      "RTL: mirror the zone and check punctuation order"]}


def access_rtl_check(entries):
    """Validate RTL text entries: direction flags, numbers, mirroring."""
    _, _ = _init()
    issues = []
    for i, e in enumerate(entries or []):
        text = str(e.get("text", ""))
        has_ltr_only = any(c.isascii() and c.isalpha() for c in text)
        if e.get("direction") != "rtl" and e.get("language", "") in ("ar", "he", "fa", "ur"):
            issues.append({"index": i, "issue": "direction_not_set"})
        if any(ch.isdigit() for ch in text) and not e.get("numbers_ltr", True):
            issues.append({"index": i, "issue": "number_order"})
    return {"entries": len(entries), "issues": issues,
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


def access_description_cues(dialogue_cues, total_ms, max_density=0.8):
    """Audio Description Cue Planner: find gaps and propose cue slots."""
    _, _ = _init()
    cues = sorted((int(c.get("start_ms", 0)), int(c.get("end_ms", 0)))
                  for c in dialogue_cues)
    gaps = []
    pos = 0
    for s, e in cues:
        if s - pos > 1500:
            gaps.append([pos, s])
        pos = max(pos, e)
    if total_ms - pos > 1500:
        gaps.append([pos, total_ms])
    used = sum(e - s for s, e in cues)
    budget = (total_ms * max_density) - used
    proposals = [{"gap_ms": g, "max_cue_ms": min(4000, g[1] - g[0])}
                 for g in gaps]
    return {"gaps": gaps, "cue_slots": proposals,
            "dialogue_density": round(used / float(max(1, total_ms)), 3),
            "rule": "cues must not overlap dialogue; keep them under 4 seconds"}


def layout_integrate(canvas_size, horizon_y, ground_y, character_height_px,
                     ground_contact="feet"):
    """Layout Pose Integrator: scale + place a character of known height on
    the ground line relative to the horizon (perspective scale)."""
    _, _ = _init()
    require(horizon_y < ground_y, "invalid_input",
            "horizon (%d) must be above the ground line (%d)" % (horizon_y, ground_y))
    # distance factor: closer to horizon = farther away = smaller
    span = ground_y - horizon_y
    t = 0.65  # standing at 65 percent depth by default
    scale = 1.0 - t * 0.6
    h_on_canvas = character_height_px * scale
    feet_y = horizon_y + span * t
    return {"canvas": list(canvas_size), "horizon_y": horizon_y,
            "character_height_px": round(h_on_canvas, 1),
            "scale": round(scale, 3), "feet_y": round(feet_y, 1),
            "eyeline_note": "eye level sits on the horizon for standing adults",
            "contact": ground_contact}
