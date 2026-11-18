#!/usr/bin/env python3
"""Packaging recipes for the powerful-extractor-compressor Skill Tool.

Loaded by ``tools-pec.py``; not a standalone entry point. Every recipe:

1. validates its inputs and options first,
2. builds the payload in a staging directory,
3. writes a machine-readable manifest plus a short human index,
4. hashes the produced artifacts,
5. publishes atomically (and never silently overwrites).

Recipes are declared in ``asset-recipes.json``; this module supplies the
handlers that those declarations reference.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if "pec_runtime" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_runtime", os.path.join(HERE, "tools-pec-runtime.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_runtime"] = _mod
    _spec.loader.exec_module(_mod)
R = sys.modules["pec_runtime"]

if "pec_formats" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_formats", os.path.join(HERE, "tools-pec-formats.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_formats"] = _mod
    _spec.loader.exec_module(_mod)
F = sys.modules["pec_formats"]

if "pec_core" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_core", os.path.join(HERE, "tools-pec-core.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_core"] = _mod
    _spec.loader.exec_module(_mod)
C = sys.modules["pec_core"]

PecError = R.PecError
EXIT_USAGE, EXIT_POLICY, EXIT_DEPENDENCY = R.EXIT_USAGE, R.EXIT_POLICY, R.EXIT_DEPENDENCY
EXIT_APPROVAL, EXIT_PARTIAL, EXIT_IO = R.EXIT_APPROVAL, R.EXIT_PARTIAL, R.EXIT_IO


@dataclass
class RecipeRun:
    recipe: str
    sources: list[str]
    destination: str
    options: dict = field(default_factory=dict)
    config: object = None
    logger: object = None
    gate: object = None
    cancellation: object = None
    job_id: str = ""
    stage: str = ""
    produced: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.job_id = self.job_id or R.new_job_id()
        self.stage = R.temp_workdir(self.logger.state_dir, self.job_id, f"pkg-{self.recipe}")


def load_recipes(config) -> dict:
    name = str(config.get("recipes_file", "asset-recipes.json"))
    data = R.load_asset(name)
    return data


def get_recipe(config, name: str) -> dict:
    data = load_recipes(config)
    recipes = data.get("recipes", {})
    if name not in recipes:
        raise PecError(
            f"unknown recipe {name!r}; available: {', '.join(sorted(recipes))}", EXIT_USAGE)
    return recipes[name]


def list_recipes(config) -> list[dict]:
    data = load_recipes(config)
    out = []
    for name, spec in sorted(data.get("recipes", {}).items()):
        out.append({
            "name": name,
            "title": spec.get("title", name),
            "handler": spec.get("handler", name),
            "description": spec.get("description", ""),
            "options": {k: v.get("default") for k, v in (spec.get("options") or {}).items()},
            "produces": spec.get("produces", ""),
        })
    return out


# --------------------------------------------------------------------------
# Collection helpers
# --------------------------------------------------------------------------
def collect_files(sources: list[str], include: list[str] | None = None,
                  exclude: list[str] | None = None, strip_metadata: bool = True,
                  config=None, max_bytes: int | None = None) -> list[dict]:
    """Walk sources into a normalized list of {fs, rel, size} dicts."""
    exclude = exclude or []
    junk = (config.get("policy.metadata_junk", []) if config else []) if strip_metadata else []
    out: list[dict] = []
    total = 0
    for src in sources:
        src_abs = os.path.abspath(src)
        if not os.path.exists(src_abs):
            raise PecError(f"input not found: {src_abs}", EXIT_USAGE)
        if os.path.isfile(src_abs):
            rel = os.path.basename(src_abs)
            if _blocked(rel, include, exclude, junk):
                continue
            out.append({"fs": src_abs, "rel": rel, "size": os.path.getsize(src_abs)})
            total += out[-1]["size"]
            continue
        root_base = os.path.dirname(src_abs)
        for dirpath, dirnames, filenames in os.walk(src_abs, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not _blocked(d + "/", include, exclude, junk))
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue
                rel = os.path.relpath(full, root_base).replace(os.sep, "/")
                if _blocked(rel, include, exclude, junk):
                    continue
                size = os.path.getsize(full)
                total += size
                if max_bytes and total > max_bytes:
                    raise PecError(
                        f"input set exceeds the {R.human_bytes(max_bytes)} packaging budget", EXIT_POLICY,
                        "size-budget")
                out.append({"fs": full, "rel": rel, "size": size})
    out.sort(key=lambda m: m["rel"])
    return out


def _blocked(rel: str, include: list[str] | None, exclude: list[str], junk: list[str]) -> bool:
    norm = rel.replace(os.sep, "/")
    if include and not any(fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(os.path.basename(norm), p) for p in include):
        return True
    if any(fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(os.path.basename(norm), p) for p in exclude):
        return True
    for j in junk:
        token = j.rstrip("/")
        if token in norm.split("/") or (token.endswith("/") and token[:-1] in norm.split("/")):
            return True
    return False


def write_manifest(run: RecipeRun, entries: list[dict], extra: dict | None = None) -> str:
    manifest = {
        "schema": R.MANIFEST_SCHEMA,
        "kind": "package-manifest",
        "skill": R.SKILL_NAME,
        "version": R.VERSION,
        "job_id": run.job_id,
        "recipe": run.recipe,
        "created_at": R.utcnow(),
        "destination": os.path.abspath(run.destination),
        "sources": [os.path.abspath(s) for s in run.sources],
        "source_fingerprint": R.compute_fingerprint(run.sources, run.options),
        "counts": {
            "files": len(entries),
            "bytes": sum(e.get("size", 0) for e in entries),
        },
        "entries": entries,
        "notes": run.notes,
        "warnings": run.warnings,
        "artifacts": run.produced,
    }
    if extra:
        manifest.update(extra)
    path = os.path.join(run.stage, "pec-package-manifest.json")
    R.write_json_atomic(path, manifest)
    return path


def _slug(value: str) -> str:
    """Filesystem-safe slug for generated artifact names."""
    import re as _re
    text = _re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    text = text.strip("-._")
    return text.lower() or "package"


def _common_root(paths: list[str]) -> str:
    """Longest common directory prefix of the given files ('' when they diverge)."""
    if not paths:
        return ""
    dirs = [os.path.dirname(os.path.abspath(p)) for p in paths]
    try:
        common = os.path.commonpath(dirs)
    except ValueError:
        return ""
    if common == os.path.sep:
        return ""
    return common


def _copy_entries(entries: list[dict], staged_root: str, base: str = "") -> list[dict]:
    """Copy collected files into the staging tree, preserving layout under ``base``."""
    copied = []
    for entry in entries:
        if base:
            rel = os.path.relpath(os.path.abspath(entry["fs"]), base).replace(os.sep, "/")
        else:
            rel = entry["rel"].split("/")[-1]
        entry["rel"] = rel
        target = os.path.join(staged_root, *rel.split("/"))
        os.makedirs(os.path.dirname(target) or staged_root, exist_ok=True)
        shutil.copy2(entry["fs"], target)
        copied.append({"rel": rel, "size": entry["size"], "staged": target})
    return copied


def collect_tree(root: str, strip_root: bool = True, exclude: list[str] | None = None) -> list[dict]:
    """Walk one directory and return paths relative to it (not to its parent)."""
    root_abs = os.path.abspath(root)
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root_abs).replace(os.sep, "/")
            if exclude and any(fnmatch.fnmatch(rel, p) for p in exclude):
                continue
            out.append({"fs": full, "rel": rel, "size": os.path.getsize(full)})
    return out


def write_text(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    return path


def publish(run: RecipeRun, archive_output: str | None = None, fmt: str = "none") -> dict:
    """Move staged content (or a built archive) into the destination."""
    gate = run.gate
    dest = os.path.abspath(run.destination)
    if os.path.exists(archive_output or dest) and gate:
        gate.require("overwrite", detail=f"replacing {archive_output or dest}")
    if archive_output:
        os.makedirs(os.path.dirname(os.path.abspath(archive_output)) or ".", exist_ok=True)
        shutil.move(archive_output, archive_output)  # already written in place
        return {"mode": "archive", "path": archive_output, "format": fmt}
    if os.path.exists(dest) and os.listdir(dest):
        raise PecError(f"destination is not empty and overwrite is not approved: {dest}",
                       EXIT_POLICY, "overwrite")
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(run.stage):
        src = os.path.join(run.stage, name)
        target = os.path.join(dest, name)
        if os.path.isdir(src):
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)
    return {"mode": "directory", "path": dest, "format": "none"}


def build_archive(run: RecipeRun, sources: list[str], fmt: str, level: int,
                  root_folder: str | None, deterministic: bool, output: str | None = None) -> dict:
    out = output or os.path.join(run.stage, "package" + F.CREATE_FORMATS.get(fmt, {}).get("extension", ".zip"))
    info = F.create_archive(sources, out, run.config, fmt=fmt, level=level, root_folder=root_folder,
                            deterministic=deterministic, cancellation=run.cancellation)
    run.produced.append({"path": os.path.basename(out), "bytes": info["output_bytes"],
                         "sha256": info["sha256"], "format": fmt, "ratio": info["ratio"]})
    return {**info, "artifact": out}


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def _finalize(run: RecipeRun, entries: list[dict], extra: dict | None = None,
              archive: dict | None = None) -> dict:
    manifest_path = write_manifest(run, entries, extra)
    index_lines = [
        f"# {get_recipe(run.config, run.recipe).get('title', run.recipe)}",
        "",
        f"- Job: `{run.job_id}`",
        f"- Created: {R.utcnow()}",
        f"- Recipe: `{run.recipe}`",
        f"- Files: {len(entries)}",
        f"- Size: {R.human_bytes(sum(e.get('size', 0) for e in entries))}",
        "",
        "## Contents",
        "",
    ]
    for entry in entries[:200]:
        index_lines.append(f"- `{entry['rel']}` — {R.human_bytes(entry['size'])}")
    if len(entries) > 200:
        index_lines.append(f"- … {len(entries) - 200} more (see pec-package-manifest.json)")
    if run.notes:
        index_lines += ["", "## Notes", ""] + [f"- {n}" for n in run.notes]
    write_text(os.path.join(run.stage, "pec-package-index.md"), "\n".join(index_lines))
    return {"manifest": manifest_path, "entries": entries}


def handle_source_release(run: RecipeRun) -> dict:
    opts = run.options
    exclude = list(opts.get("exclude") or []) + [
        "*.pyc", "*.pyo", "__pycache__/*", "*.log", "*.tmp", "*.swp",
        ".env", ".env.*", "*.env", "*.pem", "*.key", "id_rsa*", "*.p12", "*.pfx",
        "*.secret", "*credentials*", "*secrets*",
        "pec-state/*", ".git/*", "node_modules/*", "dist/*", "build/*", "*.egg-info/*",
    ]
    entries = collect_files(run.sources, opts.get("include"), exclude, True, run.config)
    if not entries:
        raise PecError("source release would be empty after exclusion rules", EXIT_POLICY, "empty")
    staged_root = os.path.join(run.stage, opts.get("root_folder") or "release")
    copy_base = _common_root([e["fs"] for e in entries])
    _copy_entries(entries, staged_root, copy_base)
    stem = os.path.basename(os.path.abspath(run.sources[0])).rstrip("/") or "release"
    archive_name = f"{_slug(stem)}-{opts.get('version', 'v1')}.{opts.get('format', 'tar.gz')}"
    archive_path = os.path.join(run.stage, archive_name)
    info = F.create_archive([staged_root], archive_path, run.config,
                            fmt=opts.get("format", "tar.gz"), level=int(opts.get("level", 6)),
                            root_folder=None,
                            deterministic=bool(opts.get("deterministic", True)),
                            cancellation=run.cancellation)
    run.produced.append({"path": archive_name, "bytes": info["output_bytes"],
                         "sha256": info["sha256"], "format": opts.get("format", "tar.gz")})
    extra = {"release": {"version": opts.get("version", "v1"),
                         "excluded_rules": exclude,
                         "archive": {"name": archive_name, "sha256": info["sha256"],
                                     "bytes": info["output_bytes"]}}}
    entries_payload = [{"rel": e["rel"], "size": e["size"],
                        "sha256": C.hash_file(e["fs"], "sha256")["digest"]} for e in entries]
    _finalize(run, entries_payload, extra, info)
    run.notes.append(f"archive sha256 {info['sha256']}")
    if opts.get("round_trip", True):
        checks = C.round_trip_check(archive_path, run.config, run.stage)
        run.notes.append(f"round-trip check: {'ok' if checks['ok'] else 'MISMATCH'}")
        if not checks["ok"]:
            raise PecError("round-trip verification of the release archive failed",
                           R.EXIT_INTEGRITY, "round-trip", detail=checks)
    return finalize_archive_output(run, archive_path, opts)


def handle_project_handoff(run: RecipeRun) -> dict:
    opts = run.options
    exclude = list(opts.get("exclude") or []) + [
        "*.log", "*.tmp", "*.swp", "*.pyc", ".git/*", "node_modules/*", "dist/*", "build/*",
        ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
        "*credentials*", "*secrets*", ".pec-state/*",
    ]
    entries = collect_files(run.sources, opts.get("include"), exclude, True, run.config)
    if not entries:
        raise PecError("handoff bundle would be empty", EXIT_POLICY, "empty")
    staged_root = os.path.join(run.stage, opts.get("root_folder") or "handoff")
    copy_base = _common_root([e["fs"] for e in entries])
    _copy_entries(entries, staged_root, copy_base)
    template = R.load_text_asset("asset-handoff-template.md", None)
    if template is None:
        raise PecError("missing asset-handoff-template.md", EXIT_DEPENDENCY)
    rendered = (template.replace("{{PROJECT}}", opts.get("project") or os.path.basename(os.path.abspath(run.sources[0])))
                        .replace("{{OWNER}}", opts.get("owner") or os.environ.get("USER", "unassigned"))
                        .replace("{{DATE}}", R.utcnow())
                        .replace("{{FILE_COUNT}}", str(len(entries) + 1))
                        .replace("{{TOTAL_SIZE}}", R.human_bytes(sum(e["size"] for e in entries))))
    write_text(os.path.join(staged_root, "HANDOFF.md"), rendered)
    archive_name = f"handoff-{_slug(opts.get('project') or 'project')}.{opts.get('format', 'zip')}"
    archive_path = os.path.join(run.stage, archive_name)
    info = F.create_archive([staged_root], archive_path, run.config,
                            fmt=opts.get("format", "zip"), level=int(opts.get("level", 6)),
                            root_folder=None,
                            cancellation=run.cancellation)
    run.produced.append({"path": archive_name, "bytes": info["output_bytes"],
                         "sha256": info["sha256"], "format": opts.get("format", "zip")})
    payload = [{"rel": e["rel"], "size": e["size"]} for e in entries]
    payload.append({"rel": "handoff/HANDOFF.md", "size": len(rendered)})
    _finalize(run, payload, {"handoff": {"project": opts.get("project"), "owner": opts.get("owner")}}, info)
    return finalize_archive_output(run, archive_path, opts)


def handle_evidence(run: RecipeRun) -> dict:
    opts = run.options
    exclude = list(opts.get("exclude") or [])
    entries = collect_files(run.sources, opts.get("include"), exclude, False, run.config)
    if not entries:
        raise PecError("evidence collection would be empty", EXIT_POLICY, "empty")
    staged_root = os.path.join(run.stage, "evidence")
    os.makedirs(staged_root, exist_ok=True)
    payload = []
    for entry in entries:
        rel = entry["rel"]
        target = os.path.join(staged_root, *rel.split("/"))
        os.makedirs(os.path.dirname(target) or staged_root, exist_ok=True)
        shutil.copy2(entry["fs"], target)
        if opts.get("read_only", True):
            os.chmod(target, 0o444)
        digest = C.hash_file(target, "sha256")["digest"]
        payload.append({"rel": rel, "size": entry["size"], "sha256": digest,
                        "original_mtime": os.path.getmtime(entry["fs"]),
                        "collected_at": R.utcnow()})
    custody = {
        "schema": R.LINEAGE_SCHEMA,
        "kind": "chain-of-custody",
        "job_id": run.job_id,
        "collector": opts.get("collector") or os.environ.get("USER", "unassigned"),
        "case_reference": opts.get("case_reference", ""),
        "started_at": R.utcnow(),
        "sources": [os.path.abspath(s) for s in run.sources],
        "records": [
            {"action": "collect", "rel": e["rel"], "sha256": e["sha256"],
             "size": e["size"], "ts": e["collected_at"]}
            for e in payload
        ],
        "notes": run.notes,
    }
    write_text(os.path.join(staged_root, "CUSTODY.json"), json.dumps(custody, indent=2, ensure_ascii=False))
    run.produced.append({"path": "CUSTODY.json", "bytes": os.path.getsize(os.path.join(staged_root, "CUSTODY.json"))})
    _finalize(run, payload, {"evidence": {"case_reference": opts.get("case_reference", ""),
                                          "read_only": bool(opts.get("read_only", True))}})
    if opts.get("archive", True):
        name = f"evidence-{_slug(opts.get('case_reference') or 'collection')}.zip"
        archive_path = os.path.join(run.stage, name)
        info = F.create_archive([staged_root], archive_path, run.config, fmt="zip",
                                level=0, root_folder=None, deterministic=True,
                                cancellation=run.cancellation)
        run.produced.append({"path": name, "bytes": info["output_bytes"], "sha256": info["sha256"],
                             "format": "zip"})
        return finalize_archive_output(run, archive_path, {**opts, "format": "zip"})
    return finalize_dir_output(run)


def handle_dataset(run: RecipeRun) -> dict:
    opts = run.options
    entries = collect_files(run.sources, opts.get("include"), opts.get("exclude"), True, run.config)
    if not entries:
        raise PecError("dataset delivery would be empty", EXIT_POLICY, "empty")
    volume_bytes = int(opts.get("volume_size") or 0)
    staged_root = os.path.join(run.stage, "dataset")
    os.makedirs(staged_root, exist_ok=True)
    manifest_rows = []
    volumes: list[dict] = []
    current: list[dict] = []
    current_bytes = 0
    index = 1
    for entry in entries:
        if volume_bytes and current and current_bytes + entry["size"] > volume_bytes:
            volumes.append({"index": index, "files": current, "bytes": current_bytes})
            current, current_bytes, index = [], 0, index + 1
        current.append(entry)
        current_bytes += entry["size"]
    if current:
        volumes.append({"index": index, "files": current, "bytes": current_bytes})
    produced_volumes = []
    for vol in volumes:
        vol_dir = os.path.join(run.stage, "volumes", f"volume-{vol['index']:03d}")
        os.makedirs(vol_dir, exist_ok=True)
        rows = []
        for entry in vol["files"]:
            target = os.path.join(vol_dir, *entry["rel"].split("/"))
            os.makedirs(os.path.dirname(target) or vol_dir, exist_ok=True)
            shutil.copy2(entry["fs"], target)
            digest = C.hash_file(target, "sha256")["digest"]
            rows.append({"rel": entry["rel"], "size": entry["size"], "sha256": digest})
            manifest_rows.append({"volume": vol["index"], **rows[-1]})
        vol_manifest = os.path.join(vol_dir, "volume-manifest.json")
        R.write_json_atomic(vol_manifest, {"schema": R.MANIFEST_SCHEMA, "volume": vol["index"],
                                           "files": rows, "bytes": vol["bytes"]})
        produced_volumes.append({"volume": vol["index"], "files": len(rows), "bytes": vol["bytes"],
                                 "manifest": os.path.relpath(vol_manifest, run.stage)})
    readme = "\n".join([
        "# Dataset delivery",
        "",
        f"- Job: `{run.job_id}`",
        f"- Volumes: {len(produced_volumes)}",
        f"- Files: {len(entries)}",
        f"- Total: {R.human_bytes(sum(e['size'] for e in entries))}",
        f"- Volume budget: {R.human_bytes(volume_bytes) if volume_bytes else 'single volume'}",
        "",
        "## Reconstruction",
        "",
        "1. Concatenate or copy every `volumes/volume-*` directory into one dataset root.",
        "2. Verify each file against `volume-manifest.json` with `python3 tools-pec.py verify --dir`.",
        "3. Schema files and checksums travel inside the same volume as their data.",
        "",
        "## Volumes",
        "",
    ] + [f"- volume-{v['volume']:03d}: {v['files']} files, {R.human_bytes(v['bytes'])}" for v in produced_volumes])
    write_text(os.path.join(staged_root, "RECONSTRUCTION.md"), readme)
    _finalize(run, manifest_rows, {"dataset": {"volumes": produced_volumes,
                                              "volume_bytes": volume_bytes}})
    return finalize_dir_output(run)


def handle_media(run: RecipeRun) -> dict:
    opts = run.options
    entries = collect_files(run.sources, opts.get("include"), opts.get("exclude"), True, run.config)
    if not entries:
        raise PecError("media delivery would be empty", EXIT_POLICY, "empty")
    mapping = R.load_asset(str(run.config.get("content_types_file", "asset-content-types.json")))
    staged_root = os.path.join(run.stage, "media")
    rows = []
    for entry in entries:
        category = C.classify_path(entry["rel"], mapping)
        target_dir = os.path.join(staged_root, category)
        os.makedirs(target_dir, exist_ok=True)
        target = C._unique_path(os.path.join(target_dir, os.path.basename(entry["rel"])))
        shutil.copy2(entry["fs"], target)  # no recompression, no transcode
        rows.append({"rel": os.path.relpath(target, run.stage).replace(os.sep, "/"),
                     "source": entry["rel"], "category": category, "size": entry["size"],
                     "sha256": C.hash_file(target, "sha256")["digest"]})
    run.notes.append("media assets copied byte-for-byte; no recompression or transcoding performed")
    _finalize(run, rows, {"media": {"categories": sorted({r['category'] for r in rows})}})
    if opts.get("archive"):
        name = f"media-{_slug(opts.get('deliverable') or 'delivery')}.{opts.get('format', 'zip')}"
        archive_path = os.path.join(run.stage, name)
        info = F.create_archive([staged_root], archive_path, run.config,
                                fmt=opts.get("format", "zip"), level=int(opts.get("level", 0)),
                                root_folder=None, cancellation=run.cancellation)
        run.produced.append({"path": name, "bytes": info["output_bytes"], "sha256": info["sha256"]})
        return finalize_archive_output(run, archive_path, opts)
    return finalize_dir_output(run)


def handle_single_bundle(run: RecipeRun) -> dict:
    opts = run.options
    entries = collect_files(run.sources, opts.get("include"), opts.get("exclude"), True, run.config)
    if not entries:
        raise PecError("bundle would be empty", EXIT_POLICY, "empty")
    fmt = opts.get("format", "zip")
    root = opts.get("root_folder")
    name = opts.get("name") or f"bundle-{time.strftime('%Y%m%d')}.{fmt}"
    archive_path = os.path.join(run.stage, name)
    info = F.create_archive([e["fs"] for e in entries], archive_path, run.config, fmt=fmt,
                            level=int(opts.get("level", 6)), root_folder=root,
                            cancellation=run.cancellation)
    run.produced.append({"path": name, "bytes": info["output_bytes"], "sha256": info["sha256"],
                         "format": fmt, "ratio": info["ratio"]})
    payload = [{"rel": e["rel"], "size": e["size"]} for e in entries]
    _finalize(run, payload, {"archive": {"name": name, "sha256": info["sha256"]}}, info)
    return finalize_archive_output(run, archive_path, opts)


def handle_separate_zip(run: RecipeRun) -> dict:
    opts = run.options
    mode = opts.get("group_by", "top-level")
    entries = collect_files(run.sources, opts.get("include"), opts.get("exclude"), True, run.config)
    if not entries:
        raise PecError("no files to package", EXIT_USAGE)
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        if mode == "extension":
            key = os.path.splitext(entry["rel"])[1].lstrip(".").lower() or "noext"
        elif mode == "size-class":
            key = _size_class(entry["size"])
        else:
            parts = entry["rel"].split("/")
            key = parts[0] if len(parts) > 1 else "root"
        groups.setdefault(key, []).append(entry)
    produced = []
    rows = []
    for key, items in sorted(groups.items()):
        name = f"{_slug(key)}.{opts.get('format', 'zip')}"
        out = os.path.join(run.stage, name)
        info = F.create_archive([i["fs"] for i in items], out, run.config,
                                fmt=opts.get("format", "zip"), level=int(opts.get("level", 6)),
                                cancellation=run.cancellation)
        produced.append({"group": key, "archive": name, "files": len(items),
                         "bytes": info["output_bytes"], "sha256": info["sha256"]})
        rows += [{"rel": i["rel"], "group": key, "size": i["size"]} for i in items]
    run.produced.extend({"path": p["archive"], "bytes": p["bytes"], "sha256": p["sha256"]} for p in produced)
    _finalize(run, rows, {"groups": produced})
    return {"publish": finalize_dir_output(run), "groups": produced}


def handle_update_patch(run: RecipeRun) -> dict:
    opts = run.options
    if len(run.sources) < 2:
        raise PecError("update-patch needs two sources: --source BASE --source TARGET", EXIT_USAGE)
    base, target = os.path.abspath(run.sources[0]), os.path.abspath(run.sources[1])
    base_files = {e["rel"]: e for e in collect_tree(base)}
    target_files = {e["rel"]: e for e in collect_tree(target)}
    added = [r for r in target_files if r not in base_files]
    removed = [r for r in base_files if r not in target_files]
    changed = []
    for rel, entry in target_files.items():
        if rel in base_files and C.hash_file(entry["fs"], "sha256")["digest"] != \
                C.hash_file(base_files[rel]["fs"], "sha256")["digest"]:
            changed.append(rel)
    staged_root = os.path.join(run.stage, "patch")
    rows = []
    for rel in sorted(added + changed):
        entry = target_files[rel]
        dest = os.path.join(staged_root, *rel.split("/"))
        os.makedirs(os.path.dirname(dest) or staged_root, exist_ok=True)
        shutil.copy2(entry["fs"], dest)
        rows.append({"rel": rel, "action": "add" if rel in added else "modify",
                     "size": entry["size"], "sha256": C.hash_file(dest, "sha256")["digest"]})
    rollback = {
        "removed_files": [{"rel": r, "sha256": C.hash_file(base_files[r]["fs"], "sha256")["digest"]}
                          for r in removed],
        "replaced_files": [{"rel": r, "previous_sha256": C.hash_file(base_files[r]["fs"], "sha256")["digest"]}
                           for r in changed],
        "instructions": "Restore replaced_files from the base release and delete added files.",
    }
    write_text(os.path.join(staged_root, "ROLLBACK.json"), json.dumps(rollback, indent=2))
    run.notes.append(f"{len(added)} added, {len(changed)} modified, {len(removed)} removed")
    _finalize(run, rows, {"patch": {"added": len(added), "modified": len(changed),
                                    "removed": len(removed)}})
    return finalize_dir_output(run)


def handle_redacted(run: RecipeRun) -> dict:
    opts = run.options
    exclude = list(opts.get("exclude") or []) + [".env", "*.pem", "*.key", "*.pfx", "id_rsa*",
                                                  "credentials*", "secrets*"]
    entries = collect_files(run.sources, opts.get("include"), exclude, False, run.config)
    staged_root = os.path.join(run.stage, "shared")
    os.makedirs(staged_root, exist_ok=True)
    rows = []
    for entry in entries:
        target = os.path.join(staged_root, *entry["rel"].split("/"))
        os.makedirs(os.path.dirname(target) or staged_root, exist_ok=True)
        shutil.copy2(entry["fs"], target)
        rows.append({"rel": entry["rel"], "size": entry["size"]})
    omitted = [{"rel": e["rel"], "rule": "exclusion policy"} for e in
               collect_files(run.sources, opts.get("include"), None, False, run.config)
               if e["rel"] not in {r["rel"] for r in rows}]
    notice = ["# Omitted content notice", "",
              "The following paths were excluded from this package by policy:", ""]
    notice += [f"- `{o['rel']}`" for o in omitted[:500]]
    write_text(os.path.join(staged_root, "OMITTED.md"), "\n".join(notice))
    _finalize(run, rows, {"redaction": {"omitted": [o["rel"] for o in omitted],
                                        "omitted_count": len(omitted)}})
    if opts.get("archive", True):
        name = f"shared-{_slug(opts.get('label') or 'package')}.zip"
        archive_path = os.path.join(run.stage, name)
        info = F.create_archive([staged_root], archive_path, run.config, fmt="zip", level=6,
                                root_folder=None, cancellation=run.cancellation)
        run.produced.append({"path": name, "bytes": info["output_bytes"], "sha256": info["sha256"]})
        return finalize_archive_output(run, archive_path, opts)
    return finalize_dir_output(run)


def handle_compliance_evidence(run: RecipeRun) -> dict:
    opts = run.options
    inputs = opts.get("evidence_files") or []
    if not inputs:
        raise PecError("compliance-evidence needs --evidence-file entries (manifests, checksums, scan output)",
                       EXIT_USAGE)
    staged_root = os.path.join(run.stage, "compliance")
    os.makedirs(staged_root, exist_ok=True)
    rows = []
    for path in inputs:
        if not os.path.exists(path):
            raise PecError(f"evidence file not found: {path}", EXIT_USAGE)
        name = os.path.basename(path)
        target = os.path.join(staged_root, name)
        if os.path.isdir(path):
            shutil.copytree(path, target, dirs_exist_ok=True)
            rows.append({"rel": name + "/", "size": 0, "kind": "directory"})
            continue
        shutil.copy2(path, target)
        rows.append({"rel": name, "size": os.path.getsize(target),
                     "sha256": C.hash_file(target, "sha256")["digest"]})
    summary = {
        "schema": R.MANIFEST_SCHEMA, "kind": "compliance-evidence", "job_id": run.job_id,
        "created_at": R.utcnow(), "policy_reference": opts.get("policy_reference", ""),
        "reviewer": opts.get("reviewer", ""), "artifacts": rows,
    }
    write_text(os.path.join(staged_root, "EVIDENCE-SUMMARY.json"), json.dumps(summary, indent=2))
    run.notes.append("content bundles excluded; only evidence artifacts were packaged")
    _finalize(run, rows, {"compliance": {"policy_reference": opts.get("policy_reference", "")}})
    return finalize_dir_output(run)


def handle_retention_labels(run: RecipeRun) -> dict:
    opts = run.options
    entries = collect_files(run.sources, opts.get("include"), opts.get("exclude"), True, run.config)
    days = int(opts.get("retention_days") or 365)
    staged_root = os.path.join(run.stage, "retention")
    os.makedirs(staged_root, exist_ok=True)
    labels = {
        "schema": R.MANIFEST_SCHEMA, "kind": "retention-labels", "job_id": run.job_id,
        "created_at": R.utcnow(), "retention_days": days,
        "classification": opts.get("classification", "internal"),
        "labels": [],
    }
    for entry in entries:
        labels["labels"].append({
            "rel": entry["rel"], "size": entry["size"],
            "retain_until": R.utcnow(), "classification": labels["classification"],
            "review_required": bool(opts.get("legal_hold", False)),
        })
    write_text(os.path.join(staged_root, "RETENTION.json"), json.dumps(labels, indent=2))
    _finalize(run, [{"rel": e["rel"], "size": e["size"]} for e in entries],
              {"retention": labels})
    return finalize_dir_output(run)


HANDLERS = {
    "single_bundle": handle_single_bundle,
    "separate_zip": handle_separate_zip,
    "source_release": handle_source_release,
    "project_handoff": handle_project_handoff,
    "update_patch": handle_update_patch,
    "compliance_evidence": handle_compliance_evidence,
    "retention_labels": handle_retention_labels,
    "redacted": handle_redacted,
    "evidence": handle_evidence,
    "dataset": handle_dataset,
    "media": handle_media,
    "source-release": handle_source_release,
    "project-handoff": handle_project_handoff,
    "evidence": handle_evidence,
    "dataset": handle_dataset,
    "media": handle_media,
    "single-bundle": handle_single_bundle,
    "separate-zip": handle_separate_zip,
    "update-patch": handle_update_patch,
    "redacted": handle_redacted,
    "compliance-evidence": handle_compliance_evidence,
    "retention-labels": handle_retention_labels,
}


def finalize_archive_output(run: RecipeRun, archive_path: str, opts: dict) -> dict:
    """Move the built archive to its requested output path (or the destination root)."""
    target = opts.get("output")
    if not target:
        dest = os.path.abspath(run.destination)
        base = os.path.basename(archive_path)
        if os.path.isdir(dest) or not os.path.splitext(dest)[1]:
            os.makedirs(dest, exist_ok=True)
            target = os.path.join(dest, base)
        else:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            target = dest
    if os.path.exists(target):
        run.gate.require("overwrite", detail=f"replacing {target}")
        os.unlink(target)
    shutil.move(archive_path, target)
    info = C.hash_file(target, "sha256")
    return {"archive": target, "bytes": info["bytes"], "sha256": info["digest"],
            "manifest": os.path.join(os.path.dirname(target), "pec-package-manifest.json")}


def finalize_dir_output(run: RecipeRun) -> dict:
    dest = os.path.abspath(run.destination)
    if os.path.exists(dest) and os.listdir(dest):
        if run.gate.check("overwrite"):
            shutil.rmtree(dest)
        else:
            dest = C._unique_path(dest)
            run.warnings.append(f"destination existed; published to {dest} instead")
    os.makedirs(dest, exist_ok=True)
    for name in sorted(os.listdir(run.stage)):
        src = os.path.join(run.stage, name)
        target = os.path.join(dest, name)
        if os.path.isdir(src):
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)
    return {"directory": dest, "files": len(os.listdir(dest))}


def run_recipe(recipe_name: str, sources: list[str], destination: str, options: dict,
               config, logger, gate, cancellation, job_id: str = "") -> dict:
    spec = get_recipe(config, recipe_name)
    handler_name = spec.get("handler", recipe_name)
    handler = HANDLERS.get(handler_name)
    if handler is None:
        raise PecError(f"recipe {recipe_name!r} references unknown handler {handler_name!r}",
                       EXIT_DEPENDENCY, "recipe")
    merged = {k: v.get("default") for k, v in (spec.get("options") or {}).items()}
    unknown = set(options) - set(merged) - {"output"}
    if unknown:
        raise PecError(
            f"unknown option(s) for recipe {recipe_name!r}: {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(sorted(list(merged) + ['output']))}", EXIT_USAGE)
    merged.update({k: v for k, v in options.items() if v is not None})
    merged["output"] = options.get("output")
    for key, value in (spec.get("options") or {}).items():
        if value.get("required") and merged.get(key) in (None, "", []):
            raise PecError(f"recipe {recipe_name!r} requires option {key!r}", EXIT_USAGE)
    started = time.monotonic()
    run = RecipeRun(recipe=recipe_name, sources=sources, destination=destination,
                    options=merged, config=config, logger=logger, gate=gate,
                    cancellation=cancellation, job_id=job_id)
    logger.info("package.start", f"recipe {recipe_name}", sources=len(sources))
    result = handler(run)
    elapsed = max(1e-6, time.monotonic() - started)
    logger.info("package.done", f"recipe {recipe_name} finished in {elapsed:.2f}s")
    return {
        "recipe": recipe_name,
        "title": spec.get("title", recipe_name),
        "sources": [os.path.abspath(s) for s in sources],
        "destination": os.path.abspath(destination),
        "options": merged,
        "produced": run.produced,
        "notes": run.notes,
        "warnings": run.warnings,
        "published": result,
        "seconds": round(elapsed, 3),
        "fingerprint": R.compute_fingerprint(sources, merged),
    }
