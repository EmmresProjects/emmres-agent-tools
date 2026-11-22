#!/usr/bin/env python3
"""tools-oaa-files.py -- filesystem, artifact and model-provider boundary operations.

Loaded by `tools-oaa.py`. Registers:

  fs.read          read a scoped file (workspace confined, size limited)
  fs.write         write a scoped file with policy check (no path traversal)
  fs.delete        delete a scoped file or empty directory (approval required)
  fs.list          list a scoped directory
  fs.hash          compute a checksum for a file
  artifact.write   write an artifact with checksum and provenance record
  artifact.list    list recorded artifacts with checksums
  provider.infer   call the configured local model provider through the adapter

This module is the only place that touches the host filesystem outside the state
database. Every path is resolved through the workspace scope, so no operation can
escape the workspace root, follow `..`, or write outside the declared layout.
"""
from __future__ import annotations

import json
import os
import shutil

import importlib.util as _ilu
import sys as _sys

# Load the shared kernel exactly once per process so every module sees the same
# exception types, configuration objects and audit chain instance.
_KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools-oaa-kernel.py")
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel", _KERNEL_PATH)
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)

TEXT_EXTENSIONS = {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".yaml",
                   ".yml", ".html", ".xml", ".log", ".py", ".toml", ".ini"}
MAX_WRITE_BYTES = 20 * 1024 * 1024


def _summarize(obj, limit=400):
    text = K.canonical_json(obj) if not isinstance(obj, str) else obj
    return text[:limit]


def _register(registry):
    reg = registry

    # ---------------------------------------------------------------- fs.read
    @reg.op("fs.read", "Read a scoped text file.",
            params=["path", "encoding"], group="files",
            examples=['{"path": "notes.txt"}'])
    def fs_read(ctx, params):
        if not params.get("path"):
            raise K.InputError("fs.read requires params.path")
        path = ctx.workspace.scope.resolve(params["path"], must_exist=True)
        if os.path.isdir(path):
            raise K.InputError("path is a directory", path=params["path"])
        max_bytes = int(K.get_path(ctx.cfg, "limits.max_input_bytes", 5000000))
        text = K.read_text(path, max_bytes=max_bytes)
        return {"path": ctx.workspace.rel(path), "bytes": os.path.getsize(path),
                "sha256": K.sha256_bytes(text), "text": text}

    # ---------------------------------------------------------------- fs.write
    @reg.op("fs.write", "Write a scoped text or JSON file.",
            params=["path", "text", "json", "overwrite"], group="files",
            mutating=True, examples=['{"path": "notes.txt", "text": "hello"}'])
    def fs_write(ctx, params):
        if not params.get("path"):
            raise K.InputError("fs.write requires params.path")
        path = ctx.workspace.scope.resolve(params["path"])
        if os.path.isdir(path):
            raise K.InputError("path is a directory", path=params["path"])
        if params.get("json") is not None:
            payload = json.dumps(params["json"], indent=2, ensure_ascii=False) + "\n"
        elif params.get("text") is not None:
            payload = str(params["text"])
        else:
            raise K.InputError("fs.write requires params.text or params.json")
        if len(payload.encode("utf-8")) > MAX_WRITE_BYTES:
            raise K.BudgetError("write exceeds the artifact size ceiling",
                                bytes=len(payload), max_bytes=MAX_WRITE_BYTES)
        if os.path.exists(path) and params.get("overwrite") is False:
            raise K.StateError("file exists and overwrite is false",
                               path=ctx.workspace.rel(path))
        ctx.guard("fs.write", approval_token=params.get("approval_token"))
        K.ensure_parent(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        ctx.emit("fs.write", action="fs.write", target=ctx.workspace.rel(path),
                 bytes=len(payload))
        return {"path": ctx.workspace.rel(path), "bytes": len(payload),
                "sha256": K.sha256_bytes(payload),
                "parent": ctx.workspace.rel(os.path.dirname(path))}

    # ---------------------------------------------------------------- fs.delete
    @reg.op("fs.delete",
            "Delete a scoped file or empty directory. Requires approval.",
            params=["path", "recursive"], group="files", mutating=True,
            approval_action="fs.delete",
            examples=['{"path": "artifacts/old-report.json"}'])
    def fs_delete(ctx, params):
        if not params.get("path"):
            raise K.InputError("fs.delete requires params.path")
        path = ctx.workspace.scope.resolve(params["path"])
        if path == ctx.workspace.root:
            raise K.PolicyError("refusing to delete the workspace root")
        if not os.path.exists(path):
            raise K.NotFoundError("path does not exist", path=params["path"])
        if os.path.isdir(path) and not params.get("recursive"):
            if os.listdir(path):
                raise K.InputError("directory is not empty; pass recursive=true "
                                   "after approval", path=params["path"])
        ctx.guard("fs.delete", approval_token=params.get("approval_token"))
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed = "directory"
        else:
            os.remove(path)
            removed = "file"
        ctx.emit("fs.delete", action="fs.delete", target=ctx.workspace.rel(path),
                 kind=removed)
        return {"path": ctx.workspace.rel(path), "deleted": True, "kind": removed}

    # ---------------------------------------------------------------- fs.list
    @reg.op("fs.list", "List a scoped directory.",
            params=["path", "recursive"], group="files",
            examples=['{"path": "artifacts"}'])
    def fs_list(ctx, params):
        path = ctx.workspace.scope.resolve(params.get("path", "."), must_exist=True)
        if not os.path.isdir(path):
            raise K.InputError("path is not a directory", path=params.get("path"))
        recursive = bool(params.get("recursive"))
        entries = []
        if recursive:
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                for name in sorted(filenames):
                    full = os.path.join(dirpath, name)
                    entries.append({"path": ctx.workspace.rel(full),
                                    "bytes": os.path.getsize(full)})
        else:
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                entries.append({"path": ctx.workspace.rel(full),
                                "kind": "dir" if os.path.isdir(full) else "file",
                                "bytes": 0 if os.path.isdir(full)
                                else os.path.getsize(full)})
        return {"root": ctx.workspace.rel(path), "count": len(entries),
                "entries": entries[:500]}

    # ---------------------------------------------------------------- fs.hash
    @reg.op("fs.hash", "Compute the SHA-256 checksum of a scoped file.",
            params=["path"], group="files", examples=['{"path": "artifact.bin"}'])
    def fs_hash(ctx, params):
        if not params.get("path"):
            raise K.InputError("fs.hash requires params.path")
        path = ctx.workspace.scope.resolve(params["path"], must_exist=True)
        if os.path.isdir(path):
            raise K.InputError("path is a directory", path=params["path"])
        return {"path": ctx.workspace.rel(path), "sha256": K.sha256_file(path),
                "bytes": os.path.getsize(path)}

    # ---------------------------------------------------------------- artifact.write
    @reg.op("artifact.write",
            "Write an artifact plus a provenance record with checksum and lineage.",
            params=["name", "text", "json", "lineage", "overwrite"], group="files",
            mutating=True,
            examples=['{"name": "report.json", "json": {"ok": true}}'])
    def artifact_write(ctx, params):
        name = params.get("name")
        if not name:
            raise K.InputError("artifact.write requires params.name")
        if "/" in name or "\\" in name or name.startswith("."):
            raise K.InputError("artifact name must be a plain file name", name=name)
        path = os.path.join(ctx.workspace.artifacts_dir, name)
        if os.path.exists(path) and params.get("overwrite") is False:
            raise K.StateError("artifact exists and overwrite is false", name=name)
        sub = fs_write(ctx, {"path": ctx.workspace.rel(path),
                             "text": params.get("text"),
                             "json": params.get("json"),
                             "approval_token": params.get("approval_token")})
        manifest_path = os.path.join(ctx.workspace.artifacts_dir, "artifact-manifest.json")
        manifest = {}
        if os.path.isfile(manifest_path):
            manifest = K.read_json(manifest_path)
        entry = {
            "name": name,
            "path": ctx.workspace.rel(path),
            "sha256": sub["sha256"],
            "bytes": sub["bytes"],
            "created_at": K.now_iso(),
            "correlation_id": ctx.correlation_id,
            "run_id": ctx.run_id,
            "lineage": params.get("lineage") or {},
            "provider": ctx.provider.info(),
        }
        manifest[name] = entry
        K.write_json(manifest_path, manifest)
        ctx.emit("artifact.write", action="artifact.write", target=name,
                 sha256=entry["sha256"])
        return entry

    @reg.op("artifact.list", "List recorded artifacts and their checksums.",
            params=[], group="files", examples=['{}'])
    def artifact_list(ctx, params):
        manifest_path = os.path.join(ctx.workspace.artifacts_dir,
                                     "artifact-manifest.json")
        if not os.path.isfile(manifest_path):
            return {"count": 0, "artifacts": []}
        manifest = K.read_json(manifest_path)
        rows = sorted(manifest.values(), key=lambda e: e.get("created_at", ""))
        return {"count": len(rows), "artifacts": rows}

    # ---------------------------------------------------------------- provider.infer
    @reg.op("provider.infer",
            "Run the configured local model provider with bounded input and provenance.",
            params=["prompt", "evidence", "max_chars"], group="provider",
            examples=['{"prompt": "What is the release window?", "evidence": '
                      '[{"source": "a.md", "text": "The window is Saturday."}]}'])
    def provider_infer(ctx, params):
        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise K.InputError("provider.infer requires params.prompt")
        if len(prompt) > 20000:
            raise K.BudgetError("prompt exceeds the 20000 character ceiling",
                                chars=len(prompt))
        evidence = params.get("evidence") or []
        if not isinstance(evidence, list):
            raise K.InputError("evidence must be a list of {source, text} objects")
        max_chars = int(params.get("max_chars", 800))
        ctx.check_cancel()
        outcome = K.retry_call(
            lambda attempt: ctx.provider.generate(prompt, evidence=evidence,
                                                 max_chars=max_chars),
            attempts=int(K.get_path(ctx.cfg, "limits.max_retries", 3)),
            base=float(K.get_path(ctx.cfg, "limits.retry_base_seconds", 0.05)),
            max_delay=float(K.get_path(ctx.cfg, "limits.retry_max_seconds", 5.0)),
            jitter=float(K.get_path(ctx.cfg, "limits.retry_jitter", 0.25)),
        )
        response = outcome["result"]
        response["attempts"] = outcome["attempts"]
        response["model"] = ctx.provider.info().get("model")
        response["correlation_id"] = ctx.correlation_id
        ctx.emit("provider.infer", action="provider.infer",
                 provider=response.get("provider"),
                 abstained=bool(response.get("abstained")))
        return response


def register(registry):
    _register(registry)
    return registry
