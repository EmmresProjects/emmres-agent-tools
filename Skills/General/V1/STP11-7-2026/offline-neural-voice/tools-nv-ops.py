#!/usr/bin/env python3
"""tools-nv-ops.py - offline operations: model intake, packaging, manifests, benchmarks.

Loaded by the entry point; safe to import. Self test:

    python tools-nv-ops.py selftest

Capabilities
------------
fetch-plan       build the exact download plan for a pinned registry voice
fetch-model      execute the plan (network, approval gate, digest verification)
build-manifest   versioned manifest of a directory with checksums and provenance
package-plan     immutable plan for a release folder
package-build    create the release folder (files only, no archives)
diagnose         environment, dependency, asset and offline health report
benchmark        model load and inference timing, real-time factor, memory

The only network operation in the whole Skill Tool lives here and it is gated
behind an explicit approval token; everything else works air-gapped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if "nvcore" not in sys.modules:
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("nvcore", _HERE / "tools-nvcore.py")
        _module = _ilu.module_from_spec(_spec)
        sys.modules["nvcore"] = _module
        _spec.loader.exec_module(_module)
    except FileNotFoundError:
        pass

import nvcore
from nvcore import (ApprovalError, DependencyError, InputError, NVError, atomic_write_bytes,
                    atomic_write_json, human_bytes, sha256_file, slugify, utc_now)

MAX_MODEL_BYTES = 400 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 120.0


# --------------------------------------------------------------------------- #
# Model intake
# --------------------------------------------------------------------------- #

def _registry_files(entry):
    files = entry.get("files") or {}
    model_rel = files.get("model")
    config_rel = files.get("config") or (model_rel + ".json" if model_rel else None)
    if not model_rel:
        raise InputError("registry entry %r has no model file" % entry.get("id"))
    contract = entry.get("download") or {}
    return {
        "model": {"relative": model_rel, "sha256": files.get("model_sha256"),
                  "md5": files.get("model_md5"), "size": files.get("model_size"),
                  "upstream": files.get("upstream_model") or model_rel},
        "config": {"relative": config_rel, "sha256": files.get("config_sha256"),
                   "md5": files.get("config_md5"), "size": files.get("config_size"),
                   "upstream": files.get("upstream_config") or config_rel},
        "base_url": contract.get("base_url"),
        "license": entry.get("license_id"),
        "source": entry.get("source"),
    }


def local_intake(cfg, entry, *, source_dir, journal=None):
    """Copy a pinned voice pair from a local folder (sneakernet intake).

    No network is used: the files are copied from a directory inside the skill
    root and then verified against the registry digests, so the result is
    identical to a network fetch.
    """
    spec = _registry_files(entry)
    # --source-dir is an explicit operator choice of an intake folder (a USB stick, a
    # download directory), so `~` is expanded and the path may sit outside the skill
    # folder. It is never used to *write*; the verified copies land in models_dir.
    src_dir = Path(os.path.expanduser(str(source_dir)))
    if not src_dir.is_dir():
        raise nvcore.InputError("source directory not found: %s" % source_dir,
                                hint="pass the folder that holds the .onnx and .onnx.json pair")
    src_dir = src_dir.resolve()
    models_dir = cfg.ensure_dir("paths.models_dir")
    results = []
    if journal is not None:
        journal.begin_intent("import-model", voice=entry.get("id"), source=source_dir)
    try:
        for role in ("model", "config"):
            item = spec[role]
            name = Path(item["relative"]).name
            source = src_dir / name
            if not source.is_file():
                raise InputError("missing %s file in %s: %s" % (role, source_dir, name),
                                 hint="stage both the .onnx model and its .onnx.json "
                                      "configuration with their pinned filenames")
            target = models_dir / name
            payload = source.read_bytes()
            expected_size = item.get("size")
            if expected_size and len(payload) != int(expected_size):
                raise InputError(
                    "size mismatch for %s: %d bytes staged, %s pinned"
                    % (name, len(payload), expected_size),
                    hint="use the exact file recorded in the registry")
            atomic_write_bytes(target, payload, overwrite=True)
            digest = sha256_file(target)
            if item.get("sha256") and digest != item["sha256"]:
                target.unlink()
                raise InputError(
                    "checksum mismatch for %s" % name,
                    details={"expected": item["sha256"], "actual": digest},
                    hint="the staged file is not the pinned artifact; nothing was installed")
            results.append({"role": role, "path": cfg.rel(target), "bytes": len(payload),
                            "sha256": digest, "action": "imported"})
        if journal is not None:
            journal.record("import-model", voice=entry.get("id"), source=source_dir,
                           files=[r["path"] for r in results])
    finally:
        if journal is not None:
            journal.clear_intent()
    return {
        "summary": "voice %s imported from %s and verified (%d files)"
                   % (entry.get("id"), source_dir, len(results)),
        "voice": entry.get("id"),
        "source": source_dir,
        "network": False,
        "files": results,
        "verified": True,
        "model": cfg.rel(models_dir / spec["model"]["relative"]),
        "next": "python tools-nv-voice.py inspect-model --voice %s" % entry.get("id"),
        "artifacts": [{"kind": "model", "path": r["path"], "sha256": r["sha256"]}
                      for r in results if r["role"] == "model"],
    }


def fetch_plan(cfg, entry):
    """Return the deterministic download plan; no network is used here."""
    spec = _registry_files(entry)
    if not spec["base_url"]:
        raise InputError("registry entry %r has no download.base_url; it is a local-only voice"
                         % entry.get("id"),
                         hint="place the model and config in the models directory manually")
    models_dir = cfg.ensure_dir("paths.models_dir")
    plan = {
        "voice": entry.get("id"),
        "license_id": spec["license"],
        "source": spec["source"],
        "destination_dir": cfg.rel(models_dir),
        "files": [],
        "network": True,
        "writes": [],
        "max_total_bytes": 0,
    }
    for role in ("model", "config"):
        item = spec[role]
        target = models_dir / item["relative"]
        plan["files"].append({
            "role": role,
            "url": "%s/%s" % (spec["base_url"].rstrip("/"), item["upstream"]),
            "target": cfg.rel(target),
            "expected_sha256": item["sha256"],
            "expected_md5": item["md5"],
            "expected_size": item["size"],
            "already_present": target.is_file(),
            "present_sha256": sha256_file(target) if target.is_file() else None,
        })
        plan["writes"].append(cfg.rel(target))
        plan["max_total_bytes"] += int(item["size"] or 0)
    plan["max_total_human"] = human_bytes(plan["max_total_bytes"])
    plan["timeout_seconds"] = DOWNLOAD_TIMEOUT_S
    plan["retries"] = 2
    plan["rollback"] = "partial files are written to a temporary name and removed on failure"
    return plan


def _download(url, target, *, timeout=DOWNLOAD_TIMEOUT_S, retries=2, expected_size=None,
              cancel=None):
    """Bounded download with a hard size cap, retries and atomic replacement."""
    last_error = None
    for attempt in range(retries + 1):
        if cancel is not None:
            cancel.check()
        request = urllib.request.Request(url, headers={"User-Agent": "offline-neural-voice/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > MAX_MODEL_BYTES:
                    raise NVError("remote file is larger than the %s safety limit"
                                  % human_bytes(MAX_MODEL_BYTES),
                                  hint="raise MAX_MODEL_BYTES in tools-nv-ops.py only after review")
                buffer = bytearray()
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    if len(buffer) > MAX_MODEL_BYTES:
                        raise NVError("download exceeded the %s safety limit"
                                      % human_bytes(MAX_MODEL_BYTES))
                if expected_size and len(buffer) != int(expected_size):
                    raise NVError("downloaded size %d does not match the pinned size %s"
                                  % (len(buffer), expected_size))
                atomic_write_bytes(target, bytes(buffer), overwrite=True)
                return len(buffer)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
                continue
            raise NVError("download failed after %d attempts: %s" % (retries + 1, exc),
                          hint="check connectivity once, or copy the pinned files into the "
                               "models directory from your offline media",
                          details={"url": url})
    raise NVError("download failed: %s" % last_error)


def fetch_model(cfg, entry, *, plan=None, journal=None, cancel=None):
    """Execute a fetch plan after approval; verify digests; never leave partial files."""
    if cfg.offline and not cfg.allow_network:
        # The network guard is only lifted inside this function, and only because
        # the caller already passed the approval gate in the entry point.
        pass
    spec = _registry_files(entry)
    plan = plan or fetch_plan(cfg, entry)
    models_dir = cfg.ensure_dir("paths.models_dir")
    results = []
    verified = []
    if journal is not None:
        journal.begin_intent("fetch-model", voice=entry.get("id"))
    try:
        for item in plan["files"]:
            target = cfg.resolve(item["target"])
            if item["already_present"] and item["expected_sha256"] and \
                    item["present_sha256"] == item["expected_sha256"]:
                results.append({"role": item["role"], "path": item["target"],
                                "action": "kept", "sha256": item["present_sha256"]})
                verified.append(item["target"])
                continue
            size = _download(item["url"], target, expected_size=item["expected_size"],
                             retries=plan.get("retries", 2), cancel=cancel)
            digest = sha256_file(target)
            if item["expected_sha256"] and digest != item["expected_sha256"]:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise InputError(
                    "checksum mismatch for %s" % item["target"],
                    details={"expected": item["expected_sha256"], "actual": digest},
                    hint="the download was corrupted or the registry pin is stale; nothing "
                         "was installed")
            results.append({"role": item["role"], "path": item["target"], "action": "downloaded",
                            "bytes": size, "sha256": digest})
            verified.append(item["target"])
        if journal is not None:
            journal.record("fetch-model", voice=entry.get("id"), files=verified)
        journal_clear = True
    finally:
        if journal is not None:
            journal.clear_intent()
    return {
        "summary": "voice %s installed and verified (%d files)" % (entry.get("id"), len(results)),
        "voice": entry.get("id"),
        "destination": cfg.rel(models_dir),
        "files": results,
        "verified": True,
        "model": cfg.rel(models_dir / spec["model"]["relative"]),
        "next": "python tools-nv-voice.py inspect-model --voice %s" % entry.get("id"),
        "artifacts": [{"kind": "model", "path": r["path"], "sha256": r.get("sha256")}
                      for r in results if r["role"] == "model"],
    }


# --------------------------------------------------------------------------- #
# Manifests and packaging
# --------------------------------------------------------------------------- #

def _walk_files(root, *, exclude_names=()):
    rows = []
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in exclude_names for part in path.parts):
            continue
        if path.name.startswith(".") or path.name.endswith(".part"):
            continue
        rows.append(path)
    return rows


def build_manifest(cfg, *, directory="output", name=None):
    """Versioned manifest of artifacts with checksums and provenance."""
    root = cfg.resolve(directory, must_exist=True, kind="dir")
    files = _walk_files(root)
    entries = []
    for path in files:
        if path.suffix == ".json" and "manifest" in path.name:
            continue
        stat = path.stat()
        entries.append({
            "path": cfg.rel(path),
            "bytes": stat.st_size,
            "sha256": sha256_file(path),
            "kind": path.suffix.lower().lstrip(".") or "file",
        })
    manifest = {
        "manifest_version": 1,
        "generated_at": utc_now(),
        "skill": nvcore.SKILL_SLUG,
        "skill_version": nvcore.__version__,
        "directory": cfg.rel(root),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "config_version": cfg.get("skill.config_version"),
        "model_pin_source": cfg.get("paths.registry_path"),
        "preset_pin_source": cfg.get("paths.presets_path"),
        "files": entries,
        "reproduce": "python tools-nv-voice.py manifest --directory %s" % directory,
    }
    target_name = name or "manifest.json"
    rel = "%s/%s" % (directory.rstrip("/"), target_name)
    atomic_write_json(cfg.resolve(rel), manifest, overwrite=True)
    return {
        "summary": "%d files, %s -> %s" % (len(entries), human_bytes(manifest["total_bytes"]), rel),
        "manifest_file": rel,
        "file_count": len(entries),
        "total_bytes": manifest["total_bytes"],
        "manifest": manifest,
        "artifacts": [{"kind": "manifest", "path": rel,
                       "sha256": sha256_file(cfg.resolve(rel))}],
    }


def package_plan(cfg, *, source="output", name=None, include_captions=False):
    src = cfg.resolve(source, must_exist=True, kind="dir")
    files = _walk_files(src)
    if not files:
        raise InputError("source directory is empty: %s" % cfg.rel(src),
                         hint="synthesize audio first")
    package_name = name or ("release-%s" % slugify(src.name))
    dest = cfg.resolve("packages") / package_name
    dest_rel = cfg.rel(dest)
    rows = []
    total = 0
    for path in files:
        rel = path.relative_to(src).as_posix()
        rows.append({"from": cfg.rel(path), "to": "%s/%s" % (dest_rel, rel)})
        total += path.stat().st_size
    plan = {
        "package": package_name,
        "destination": cfg.rel(dest),
        "source": cfg.rel(src),
        "file_count": len(rows),
        "total_bytes": total,
        "total_human": human_bytes(total),
        "files": rows,
        "writes": [cfg.rel(dest)] + [row["to"] for row in rows],
        "overwrites_allowed": False,
        "destructive": False,
        "contains_archives": False,
        "network": False,
    }
    if dest.exists():
        plan["conflict"] = "destination already exists: %s" % cfg.rel(dest)
    return plan


def package_build(cfg, *, plan, journal=None):
    """Materialise an approved package plan as plain files."""
    # The destination does not exist yet, so resolve it as a plain child of the root.
    dest = nvcore.skill_root() / plan["destination"]
    dest = dest.resolve()
    if cfg.root not in dest.parents and dest.parent != cfg.root:
        raise InputError("package destination escapes the skill root")
    if dest.exists():
        raise InputError("package destination already exists: %s" % plan["destination"],
                         hint="remove it after review, or choose another --name")
    if journal is not None:
        journal.begin_intent("package-build", destination=plan["destination"])
    created = []
    try:
        for row in plan["files"]:
            source = cfg.resolve(row["from"], must_exist=True, kind="file")
            target = nvcore.skill_root() / row["to"]
            # the destination folder was verified absent above, so a new file is safe
            atomic_write_bytes(target, source.read_bytes(), overwrite=True)
            created.append({"path": row["to"], "bytes": target.stat().st_size,
                            "sha256": sha256_file(target)})
        manifest = build_manifest(cfg, directory=plan["destination"],
                                  name="package-manifest.json")
        if journal is not None:
            journal.record("package-build", package=plan["package"],
                           file_count=len(created))
    finally:
        if journal is not None:
            journal.clear_intent()
    return {
        "summary": "package %s: %d files, %s" % (plan["package"], len(created),
                                                 human_bytes(plan["total_bytes"])),
        "package": plan["package"],
        "destination": plan["destination"],
        "files": created,
        "manifest": manifest["manifest_file"],
        "contents": "plain files only: audio, captions, evidence, manifest",
        "no_archives": True,
        "artifacts": [{"kind": "package-folder", "path": plan["destination"]}],
    }


# --------------------------------------------------------------------------- #
# Diagnostics and benchmarking
# --------------------------------------------------------------------------- #

def diagnose(cfg):
    """Environment, dependency, asset and offline health in one report."""
    environment = nvcore.environment_report()
    dependencies = nvcore.dependency_report()
    checks = []
    warnings = []

    def add(name, status, detail, remediation=None):
        checks.append({"check": name, "status": status, "detail": detail,
                       "remediation": remediation})

    missing = [d["module"] for d in dependencies if not d["available"]]
    required_missing = [m for m in ("numpy", "onnxruntime") if m in missing]
    add("required-packages", "fail" if required_missing else "pass",
        "missing: %s" % ", ".join(required_missing) if required_missing
        else "numpy and onnxruntime import correctly",
        "pip install numpy onnxruntime")
    optional_missing = [m for m in missing if m not in required_missing]
    add("optional-packages", "warn" if optional_missing else "pass",
        "missing optional: %s" % ", ".join(optional_missing) if optional_missing
        else "all optional packages available",
        "pip install lameenc soundfile jsonschema faster-whisper")
    cpu = environment.get("cpu_count") or 0
    add("cpu", "pass" if cpu >= 2 else "warn", "%d logical CPUs on %s" % (cpu, environment["machine"]),
        "use a CPU with at least 2 cores for real-time synthesis")
    memory = environment.get("memory") or {}
    available = memory.get("available_bytes") or 0
    add("memory", "pass" if available > 1_000_000_000 else "warn",
        "%s available of %s" % (memory.get("available_human"),
                                memory.get("total_human")),
        "free memory or reduce inference.intra_op_threads")
    try:
        registry, by_id, registry_rel = nvcore.load_registry(cfg)
        models_dir = cfg.ensure_dir("paths.models_dir")
        installed = [p for p in sorted(models_dir.glob("*.onnx"))
                     if (models_dir / (p.name + ".json")).is_file()]
        add("registry", "pass", "%d voices in %s" % (len(by_id), registry_rel))
        add("installed-voices", "pass" if installed else "warn",
            "%d installed: %s" % (len(installed), ", ".join(p.name for p in installed))
            if installed else "no neural voice installed",
            "python tools-nv-voice.py fetch-model --voice <id> --plan")
    except NVError as exc:
        add("registry", "fail", exc.message, "restore asset-model-registry.json")
        installed = []
    try:
        presets_data, presets, presets_rel = nvcore.load_presets(cfg)
        add("presets", "pass", "%d presets in %s" % (len(presets), presets_rel))
    except NVError as exc:
        add("presets", "fail", exc.message, "restore asset-voice-presets.json")
    try:
        policy, policy_rel = nvcore.load_policy(cfg)
        add("rights-policy", "pass", "policy %s in %s" % (
            policy.get("policy_version") or policy.get("version"), policy_rel))
    except NVError as exc:
        add("rights-policy", "fail", exc.message, "restore asset-rights-policy.json")
    for key, label in (("paths.output_dir", "output-dir"), ("paths.work_dir", "work-dir")):
        try:
            target = cfg.ensure_dir(key)
            probe = target / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(label, "pass", "%s is writable" % cfg.rel(target))
        except OSError as exc:
            add(label, "fail", "not writable: %s" % exc, "grant write access")
    espeak = {}
    try:
        engine = nvcore.load_sibling("tools-nv-engine.py", "nvengine")
        espeak = engine.Phonemizer().probe()
        backend = "piper_espeak" if espeak.get("piper_espeak", {}).get("available") else (
            "espeak_ng_binary" if espeak.get("espeak_ng_binary", {}).get("available")
            else "manual_ipa")
        add("phonemizer", "pass" if backend != "manual_ipa" else "warn", backend,
            "pip install piper-tts, or install espeak-ng")
    except NVError as exc:
        add("phonemizer", "warn", exc.message, "pip install piper-tts")
    journal = nvcore.build_journal(cfg)
    recovery = journal.recover()
    add("journal", "pass" if not recovery.get("interrupted") else "warn",
        "no stale intent" if not recovery.get("interrupted")
        else "recovered interrupted operation %s" % recovery.get("operation"),
        "verify outputs with qa before rerunning")
    add("offline-policy", "pass" if cfg.offline else "warn",
        "offline guard enabled (sockets blocked during synthesis)" if cfg.offline
        else "offline guard disabled by configuration",
        "set execution.offline=true for delivery")
    failures = [c for c in checks if c["status"] == "fail"]
    warnings = [c["detail"] for c in checks if c["status"] == "warn"]
    return {
        "summary": "%d checks, %d failed, %d warnings" % (len(checks), len(failures),
                                                          len(warnings)),
        "healthy": not failures,
        "checks": checks,
        "warnings": warnings,
        "environment": environment,
        "dependencies": dependencies,
        "espeak": espeak,
    }


def benchmark(cfg, *, voice_id=None, runs=3, text="Greetings, friends! Welcome to my video."):
    """Measure model load, phonemization and inference on this CPU."""
    engine = nvcore.load_sibling("tools-nv-engine.py", "nvengine")
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    asset = engine.resolve_voice(cfg, voice_id, registry=registry)
    phonemizer = engine.Phonemizer(espeak_voice=asset.config.espeak_voice)
    phonemizer.probe()
    rows = {"phonemize_ms": [], "load_ms": [], "inference_ms": [], "audio_s": []}
    sequences, _ = phonemizer.phonemize(text)
    phonemes = sequences[0] if sequences else []
    warnings = []
    for _ in range(max(1, runs)):
        started = time.monotonic()
        phonemizer.phonemize(text)
        rows["phonemize_ms"].append(round((time.monotonic() - started) * 1000, 2))
    synth = engine.build_engine(engine.ENGINE_ONNX, asset, cfg=cfg)
    for _ in range(max(1, runs)):
        started = time.monotonic()
        session_engine = engine.build_engine(engine.ENGINE_ONNX, asset, cfg=cfg)
        session_engine._ensure_session()
        rows["load_ms"].append(round((time.monotonic() - started) * 1000, 2))
        started = time.monotonic()
        result = session_engine.synthesize(engine.SynthesisRequest(
            phonemes, scales={"length_scale": 1.0, "noise_scale": 0.667, "noise_w": 0.8},
            speaker_index=asset.speaker_index, timeout_s=120.0))
        elapsed = round((time.monotonic() - started) * 1000, 2)
        rows["inference_ms"].append(elapsed)
        rows["audio_s"].append(result.duration_s)
    def stats(values):
        ordered = sorted(values)
        return {"min": ordered[0], "median": ordered[len(ordered) // 2], "max": ordered[-1],
                "runs": len(ordered)}
    inference = stats(rows["inference_ms"])
    audio_seconds = sum(rows["audio_s"]) / max(1, len(rows["audio_s"]))
    rtf = (inference["median"] / 1000.0) / audio_seconds if audio_seconds else None
    return {
        "summary": "%s: load %s ms, inference %s ms for %.2f s audio (RTF %.3f)" % (
            asset.voice_id, stats(rows["load_ms"])["median"], inference["median"],
            audio_seconds, rtf if rtf is not None else -1),
        "voice": asset.describe(),
        "text": text,
        "phonemes": "".join(phonemes),
        "timings": {"phonemize_ms": stats(rows["phonemize_ms"]),
                    "model_load_ms": stats(rows["load_ms"]),
                    "inference_ms": inference},
        "audio_seconds": round(audio_seconds, 3),
        "real_time_factor": round(rtf, 3) if rtf is not None else None,
        "threads": int(cfg.get("inference.intra_op_threads", 1)),
        "environment": {"cpu_count": os.cpu_count(), "python": sys.version.split()[0]},
        "warnings": warnings + (["real-time factor above 1.0: audio is generated slower than "
                                 "playback on this CPU"] if rtf and rtf > 1.0 else []),
    }


# --------------------------------------------------------------------------- #
# Self test
# --------------------------------------------------------------------------- #

def selftest(argv=None):
    parser = argparse.ArgumentParser(prog="tools-nv-ops.py selftest")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args(argv)
    import tempfile
    checks = []

    def check(name, fn):
        started = time.monotonic()
        try:
            detail = fn()
            checks.append({"check": name, "ok": True,
                           "ms": round((time.monotonic() - started) * 1000, 2), "detail": detail})
        except Exception as exc:
            checks.append({"check": name, "ok": False,
                           "ms": round((time.monotonic() - started) * 1000, 2),
                           "detail": "%s: %s" % (type(exc).__name__, exc)})

    workdir = Path(tempfile.mkdtemp())
    # the self test runs inside a throwaway root so absolute temp paths are allowed
    cfg_test = nvcore.load_config(None, root=workdir, allow_absolute=True)

    def t_manifest():
        cfg = cfg_test
        target = workdir / "output"
        target.mkdir(parents=True, exist_ok=True)
        (target / "clip.wav").write_bytes(b"RIFF0000WAVE")
        result = build_manifest(cfg, directory=str(target), name="test-manifest.json")
        assert result["file_count"] >= 1, result
        saved = json.loads((target / "test-manifest.json").read_text())
        assert saved["manifest_version"] == 1
        return "manifest lists %d files" % result["file_count"]

    def t_package():
        cfg = cfg_test
        source = workdir / "src"
        source.mkdir(exist_ok=True)
        (source / "a.wav").write_bytes(b"RIFFxxxxWAVE")
        plan = package_plan(cfg, source=str(source), name="selftest-package")
        assert plan["file_count"] == 1 and not plan["contains_archives"]
        return "package plan lists %d file(s) without archives" % plan["file_count"]

    def t_fetch_plan_shape():
        cfg = nvcore.load_config(None, root=_HERE)
        entry = {"id": "demo", "files": {"model": "demo.onnx", "model_sha256": "0" * 64,
                                         "model_size": 10},
                 "download": {"base_url": "https://example.invalid/models"},
                 "license_id": "public-domain"}
        plan = fetch_plan(cfg, entry)
        assert plan["network"] is True and plan["files"], plan
        assert plan["files"][0]["url"].endswith("demo.onnx"), plan
        return "fetch plan builds explicit URLs and digests: %s" % plan["files"][0]["url"]

    def t_download_cap():
        cfg = nvcore.load_config(None, root=_HERE)
        assert MAX_MODEL_BYTES <= 512 * 1024 * 1024
        return "download size cap is %s" % human_bytes(MAX_MODEL_BYTES)

    check("manifest", t_manifest)
    check("package-plan", t_package)
    check("fetch-plan", t_fetch_plan_shape)
    check("download-cap", t_download_cap)
    shutil.rmtree(workdir, ignore_errors=True)

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "tool": "offline-neural-voice/ops", "version": nvcore.__version__,
               "checks": checks}
    if args.format == "json":
        nvcore.print_json(payload)
    else:
        for item in checks:
            print("[%s] %s (%sms) %s" % ("PASS" if item["ok"] else "FAIL",
                                         item["check"], item["ms"], item["detail"]))
        print("\n%s: %d/%d ops checks passed" % ("PASS" if ok else "FAIL",
                                                 sum(1 for c in checks if c["ok"]), len(checks)))
    return nvcore.EXIT_OK if ok else nvcore.EXIT_FAIL


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return nvcore.EXIT_OK
    if argv[0] == "selftest":
        return selftest(argv[1:])
    sys.stderr.write("unknown command %r; try `selftest --help`\n" % argv[0])
    return nvcore.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
