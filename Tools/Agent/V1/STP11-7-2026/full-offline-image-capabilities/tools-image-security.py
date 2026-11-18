#!/usr/bin/env python3
"""Model and asset integrity, security screening, quarantine and rights audit.

Role: everything that must happen *before* an untrusted model file, archive or
asset is allowed anywhere near a runtime. Builds and verifies SHA-256
manifests, classifies model components, screens for pickle/executable risk,
archive bombs, path traversal, symlinks and embedded secrets, quarantines
findings behind an approval gate, and audits licences and redistribution
rights.

This module never unpickles, never extracts an archive to disk and never
contacts the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import struct
import sys
import tarfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_spec = importlib.util.spec_from_file_location("foic_core", os.path.join(_HERE, "tools-image-core.py"))
core = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("foic_core", core)
_spec.loader.exec_module(core)

ToolError = core.ToolError
LOG = core.LOG

SECRET_PATTERNS = [
    ("aws-access-key-id", re.compile(r"(?i)\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9]{24,}\b")),
    ("generic-api-key", re.compile(r"(?i)\b(api[_-]?key|apikey|secret[_-]?key|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}")),
    ("private-key-block", re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP)? ?PRIVATE KEY-----")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{24,}")),
    ("connection-string", re.compile(r"(?i)\b[a-z]+://[^\s:@/]+:[^\s:@/]+@")),
]

TEXT_EXTENSIONS = {
    "json", "yaml", "yml", "txt", "md", "ini", "cfg", "conf", "toml", "py", "sh",
    "env", "csv", "xml", "html", "rst", "log",
}

ARCHIVE_EXTENSIONS = {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "whl"}

LICENSE_SPDX_MAP = [
    (re.compile(r"(?i)\bapache\s*license\b|\bapache-?2\.0\b"), "apache-2.0"),
    (re.compile(r"(?i)\bmit\s+license\b|\bmit\b(?=[^\w]|$)"), "mit"),
    (re.compile(r"(?i)\bbsd-?2\b|\bsimplified bsd\b"), "bsd-2-clause"),
    (re.compile(r"(?i)\bbsd-?3\b|\bnew bsd\b|\bmodified bsd\b|\bbsd\b"), "bsd-3-clause"),
    (re.compile(r"(?i)\bstable.?diffusion\b.*\bopenrail\b|\bopenrail\+\+-m\b"), "openrail++-m"),
    (re.compile(r"(?i)\bopenrail\+\+-m\b"), "openrail++-m"),
    (re.compile(r"(?i)\bopenrail\b|\bcreative\s*ml\b"), "creativeml-openrail-m"),
    (re.compile(r"(?i)\bstability\s*ai\s*community\b"), "stability-ai-community"),
    (re.compile(r"(?i)\bcc0\b|\bpublic\s*domain\b"), "cc0-1.0"),
    (re.compile(r"(?i)\bcc[ -]?by[ -]?nc[ -]?sa\b"), "cc-by-nc-sa-4.0"),
    (re.compile(r"(?i)\bcc[ -]?by[ -]?nc\b"), "cc-by-nc-4.0"),
    (re.compile(r"(?i)\bcc[ -]?by[ -]?sa\b"), "cc-by-sa-4.0"),
    (re.compile(r"(?i)\bcc[ -]?by\b"), "cc-by-4.0"),
    (re.compile(r"(?i)\bs-?lab\b"), "s-lab-1.0"),
    (re.compile(r"(?i)\bnon-?commercial\b"), "non-commercial-unspecified"),
    (re.compile(r"(?i)\ball rights reserved\b|\bproprietary\b"), "all-rights-reserved"),
]


def load_catalog(cfg, path=None):
    path = path or os.path.join(core.TOOL_DIR, core.DEFAULT_MODEL_CATALOG_ASSET)
    return core.read_json(path)


def load_license_policy(path=None):
    path = path or os.path.join(core.TOOL_DIR, core.DEFAULT_LICENSE_POLICY_ASSET)
    return core.read_json(path)


# --------------------------------------------------------------------------
# component classification and manifests
# --------------------------------------------------------------------------
def classify_component(filename, catalog):
    name = filename.lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    base = os.path.basename(name)
    for kind, spec in catalog["component_kinds"].items():
        if ext in spec["extensions"]:
            if kind in ("checkpoint", "unet") and any(
                tok in base for tok in ("lora", "control", "upscal", "gfpgan", "codeformer", "sam_", "embedding")
            ):
                continue
            if kind == "vae" and any(tok in base for tok in ("control", "lora")):
                continue
            return kind, spec["preferred_format"]
    for kind, tokens in (
        ("lora", ("lora",)),
        ("controlnet", ("controlnet", "control_", "control-")),
        ("upscaler", ("realesrgan", "esrgan", "upscal", "swinir", "4x_")),
        ("face_restorer", ("gfpgan", "codeformer", "restore")),
        ("segmenter", ("sam_", "segment", "matting", "u2net", "modnet")),
        ("embedding", ("embedding", "textual_inversion", "ti-")),
    ):
        if any(tok in base for tok in tokens):
            return kind, catalog["component_kinds"].get(kind, {}).get("preferred_format", "safetensors")
    if ext in ("json", "yaml", "yml"):
        return "config", "json"
    if ext in ("txt", "md", "license"):
        return "license", "txt"
    if ext in ("safetensors", "pt", "pth", "bin", "ckpt"):
        return "checkpoint", "safetensors"
    return "other", ext or None


def build_manifest(root, cfg, catalog=None, deadline=None, skip_dirs=None):
    root = os.path.abspath(root)
    if not os.path.exists(root):
        raise ToolError("manifest root does not exist: %s" % root, code="not-found")
    catalog = catalog or load_catalog(cfg)
    files = []
    total = 0
    for path in core.iter_files(root, skip_dirs=skip_dirs):
        if ".foic-tmp" in path:
            continue
        if deadline:
            deadline.check("building manifest")
        rel = os.path.relpath(path, root).replace("\\", "/")
        digest, size = core.sha256_file(path, deadline=deadline)
        kind, preferred = classify_component(rel, catalog)
        files.append(
            {
                "path": rel,
                "size": size,
                "sha256": digest,
                "kind": kind,
                "preferred_format": preferred,
                "extension": os.path.splitext(rel)[1].lstrip("."),
            }
        )
        total += size
    return {
        "schema": "foic.manifest/1",
        "generated_at": core.iso_now(),
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "root": root,
        "file_count": len(files),
        "total_bytes": total,
        "total_bytes_human": core.human_bytes(total),
        "files": sorted(files, key=lambda f: f["path"]),
    }


def verify_manifest(root, manifest, cfg, deadline=None):
    root = os.path.abspath(root)
    expected = {f["path"]: f for f in manifest.get("files", [])}
    actual = {}
    for path in core.iter_files(root):
        if deadline:
            deadline.check("verifying manifest")
        rel = os.path.relpath(path, root).replace("\\", "/")
        actual[rel] = path
    missing, mismatched, ok = [], [], []
    for rel, entry in expected.items():
        if rel not in actual:
            missing.append({"path": rel, "expected_sha256": entry["sha256"]})
            continue
        digest, size = core.sha256_file(actual[rel], deadline=deadline)
        if digest != entry["sha256"]:
            mismatched.append(
                {"path": rel, "expected_sha256": entry["sha256"], "actual_sha256": digest,
                 "expected_size": entry["size"], "actual_size": size}
            )
        else:
            ok.append(rel)
    extra = sorted(set(actual) - set(expected))
    return {
        "schema": "foic.manifest-verify/1",
        "generated_at": core.iso_now(),
        "root": root,
        "manifest_generated_at": manifest.get("generated_at"),
        "verified": len(ok),
        "missing": sorted(missing, key=lambda x: x["path"]),
        "mismatched": sorted(mismatched, key=lambda x: x["path"]),
        "extra": [{"path": p} for p in extra],
        "status": "ok" if not missing and not mismatched else "drift",
    }


# --------------------------------------------------------------------------
# screening
# --------------------------------------------------------------------------
def _sniff_magic(head):
    if head[:2] == b"PK\x03\x04":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:3] == b"BZh":
        return "bzip2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if head[:4] == b"7z\xbc\xaf":
        return "7z"
    if head[:2] in (b"\x80\x02", b"\x80\x03", b"\x80\x04", b"\x80\x05"):
        return "pickle"
    if head[:2] == b"\x7fELF":
        return "elf"
    if head[:2] == b"MZ":
        return "pe"
    if len(head) >= 8:
        try:
            (slen,) = struct.unpack("<Q", head[:8])
            if 0 < slen < (200 << 20):
                body = head[8 : 8 + min(slen, 4096)]
                if body.lstrip()[:1] == b"{":
                    return "safetensors"
        except struct.error:
            pass
    return "unknown"


def _pickle_markers(head):
    markers = []
    for proto, label in ((2, "protocol 2"), (3, "protocol 3"), (4, "protocol 4"), (5, "protocol 5")):
        if head[:2] == bytes((0x80, proto)):
            markers.append(label)
    if re.search(rb"(?s)c(__main__|builtins|posix|nt|subprocess|os)\n", head[:8192]):
        markers.append("imports executable module")
    if b"REDUCE" in head[:8192] or b"\x52\x71\x00" in head[:8192]:
        markers.append("REDUCE opcode")
    return markers


def _screen_archive(path, cfg, ext):
    findings = []
    max_bytes = int(cfg.get("security.max_archive_uncompressed_bytes") or 0)
    max_ratio = float(cfg.get("security.max_archive_ratio") or 0)
    max_entries = int(cfg.get("security.max_archive_entries") or 0)
    total_uncompressed = 0
    entries = 0
    nested = []
    suspicious = []
    if ext in ("zip", "whl"):
        try:
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    entries += 1
                    total_uncompressed += info.file_size
                    if entries > max_entries:
                        break
                    name = info.filename
                    if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                        suspicious.append("path traversal entry: %s" % name)
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        if not cfg.get("security.allow_symlinks_in_archives"):
                            suspicious.append("symlink entry: %s" % name)
                    if name.lower().rsplit(".", 1)[-1:] and name.lower().rsplit(".", 1)[-1] in ARCHIVE_EXTENSIONS:
                        nested.append(name)
        except zipfile.BadZipFile as exc:
            findings.append({"id": "archive-corrupt", "severity": "error", "message": "unreadable zip: %s" % exc})
    elif ext in ("tar", "tgz", "gz", "bz2", "xz"):
        mode = "r:*"
        try:
            with tarfile.open(path, mode) as tf:
                for member in tf.getmembers():
                    entries += 1
                    total_uncompressed += member.size
                    if entries > max_entries:
                        break
                    if member.name.startswith("/") or ".." in member.name.replace("\\", "/").split("/"):
                        suspicious.append("path traversal entry: %s" % member.name)
                    if member.issym() and not cfg.get("security.allow_symlinks_in_archives"):
                        suspicious.append("symlink entry: %s" % member.name)
                    if member.name.lower().rsplit(".", 1)[-1:] and member.name.lower().rsplit(".", 1)[-1] in ARCHIVE_EXTENSIONS:
                        nested.append(member.name)
        except tarfile.TarError:
            if ext in ("gz", "tgz", "bz2", "xz"):
                with open(path, "rb") as fh:
                    fh.seek(max(0, os.path.getsize(path) - 4))
                    tail = fh.read(4)
                if len(tail) == 4:
                    total_uncompressed = struct.unpack("<I", tail)[0]
                    entries = 1
                findings.append(
                    {"id": "archive-single-file", "severity": "info",
                     "message": "single-file compressed stream; uncompressed size read from trailer"}
                )
            else:
                findings.append({"id": "archive-corrupt", "severity": "error", "message": "unreadable tar archive"})
    if max_entries and entries > max_entries:
        findings.append(
            {"id": "archive-entries", "severity": "error",
             "message": "entry count %d exceeds limit %d" % (entries, max_entries)}
        )
    if max_bytes and total_uncompressed > max_bytes:
        findings.append(
            {"id": "archive-bomb", "severity": "error",
             "message": "uncompressed size %s exceeds limit %s"
             % (core.human_bytes(total_uncompressed), core.human_bytes(max_bytes))}
        )
    compressed_size = max(1, os.path.getsize(path))
    ratio = total_uncompressed / float(compressed_size)
    if max_ratio and ratio > max_ratio and total_uncompressed > (50 << 20):
        findings.append(
            {"id": "archive-ratio", "severity": "error",
             "message": "compression ratio %.0f:1 exceeds limit %.0f:1 (possible archive bomb)" % (ratio, max_ratio)}
        )
    if nested and not cfg.get("security.allow_nested_archives"):
        findings.append(
            {"id": "nested-archive", "severity": "warn",
             "message": "%d nested archive(s) present, e.g. %s" % (len(nested), nested[0])}
        )
    for item in suspicious[:20]:
        findings.append({"id": "archive-entry-unsafe", "severity": "error", "message": item})
    metrics = {
        "entries": entries,
        "uncompressed_bytes": total_uncompressed,
        "compressed_bytes": compressed_size,
        "ratio": round(ratio, 2),
    }
    if not findings:
        findings.append(
            {"id": "archive-clean", "severity": "info",
             "message": "%d entries, %s uncompressed, ratio %.1f:1" % (entries, core.human_bytes(total_uncompressed), ratio)}
        )
    return findings, metrics


def _screen_secrets(path, head, full_text=None):
    findings = []
    text = full_text
    if text is None:
        try:
            text = head.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    for label, pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            snippet = match.group(0)
            masked = snippet[:6] + "..." + ("*" * max(0, len(snippet) - 10))
            findings.append(
                {"id": "secret-%s" % label, "severity": "error",
                 "message": "possible %s in file (matched %r)" % (label.replace("-", " "), masked)}
            )
    return findings


def screen_file(path, cfg, catalog=None):
    catalog = catalog or load_catalog(cfg)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ToolError("not a file: %s" % path, code="not-found")
    size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    findings = []
    metrics = {"size": size, "extension": ext}
    with open(path, "rb") as fh:
        head = fh.read(65536)
    magic = _sniff_magic(head)
    metrics["magic"] = magic
    kind, preferred = classify_component(os.path.basename(path), catalog)
    metrics["component_kind"] = kind
    metrics["preferred_format"] = preferred

    blocked = cfg.get("security.blocked_extensions") or []
    if ext in blocked:
        findings.append(
            {"id": "blocked-executable", "severity": "error",
             "message": "extension .%s is on the blocked executable list" % ext}
        )
    if magic in ("elf", "pe"):
        findings.append(
            {"id": "executable-payload", "severity": "error",
             "message": "file contains an ELF/PE executable payload; never load it as a model"}
        )

    pickle_exts = cfg.get("security.pickle_risk_extensions") or []
    if ext in pickle_exts:
        if magic == "safetensors":
            findings.append(
                {"id": "format-mismatch", "severity": "warn",
                 "message": "extension .%s but content is SafeTensors; rename to .safetensors" % ext}
            )
        elif magic == "zip":
            findings.append(
                {"id": "pickle-risk", "severity": "warn",
                 "message": "zip-wrapped torch checkpoint: torch.load() still executes pickle unless weights_only=True"}
            )
        elif magic == "pickle":
            markers = _pickle_markers(head) or ["raw pickle stream"]
            findings.append(
                {"id": "pickle-risk", "severity": "error",
                 "message": "raw pickle stream (%s); converting to SafeTensors is required before loading" % ", ".join(markers)}
            )
        else:
            findings.append(
                {"id": "pickle-risk", "severity": "warn",
                 "message": "format .%s can carry pickle payloads; prefer SafeTensors" % ext}
            )
    if ext == "safetensors" and magic == "safetensors":
        findings.append({"id": "format-ok", "severity": "info", "message": "SafeTensors container with JSON header"})
        meta = safetensors_metadata(path)
        if meta:
            metrics["safetensors_metadata_keys"] = sorted(meta.keys())[:20]
            lic = meta.get("license") or meta.get("license_name")
            if lic:
                metrics["license_hint"] = lic
    elif ext == "safetensors":
        findings.append(
            {"id": "format-mismatch", "severity": "error",
             "message": "file claims .safetensors but the header is not a SafeTensors JSON header (magic=%s)" % magic}
        )

    if ext in ARCHIVE_EXTENSIONS or magic in ("zip", "gzip", "bzip2", "xz", "7z"):
        archive_findings, archive_metrics = _screen_archive(path, cfg, ext)
        findings.extend(archive_findings)
        metrics.update(archive_metrics)

    if ext in TEXT_EXTENSIONS:
        if cfg.get("security.secret_scan", True):
            with open(path, "rb") as fh:
                text = fh.read(min(size, 2 << 20)).decode("utf-8", errors="replace")
            findings.extend(_screen_secrets(path, head, text))

    if not findings:
        findings.append({"id": "clean", "severity": "info", "message": "no screening rule matched"})
    statuses = [f["severity"] for f in findings]
    if "error" in statuses:
        status = "fail"
    elif "warn" in statuses:
        status = "warn"
    else:
        status = "pass"
    return {
        "path": path,
        "filename": os.path.basename(path),
        "size": size,
        "size_human": core.human_bytes(size),
        "status": status,
        "findings": findings,
        "metrics": metrics,
    }


def screen_tree(root, cfg, catalog=None, deadline=None, skip_dirs=None):
    catalog = catalog or load_catalog(cfg)
    results = []
    for path in core.iter_files(root, skip_dirs=skip_dirs):
        if deadline:
            deadline.check("screening")
        try:
            results.append(screen_file(path, cfg, catalog))
        except ToolError as exc:
            results.append(
                {"path": path, "filename": os.path.basename(path), "status": "error",
                 "findings": [{"id": "screen-error", "severity": "error", "message": str(exc)}], "metrics": {}}
            )
    summary = {
        "scanned": len(results),
        "failed": sum(1 for r in results if r["status"] == "fail"),
        "warned": sum(1 for r in results if r["status"] == "warn"),
        "passed": sum(1 for r in results if r["status"] == "pass"),
        "errored": sum(1 for r in results if r["status"] == "error"),
    }
    summary["verdict"] = "fail" if summary["failed"] else ("warn" if summary["warned"] else "pass")
    return {
        "schema": "foic.screen/1",
        "generated_at": core.iso_now(),
        "root": os.path.abspath(root),
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "settings": {
            "prefer_safetensors": cfg.get("security.prefer_safetensors"),
            "max_archive_uncompressed_bytes": cfg.get("security.max_archive_uncompressed_bytes"),
            "max_archive_ratio": cfg.get("security.max_archive_ratio"),
            "secret_scan": cfg.get("security.secret_scan"),
        },
        "summary": summary,
        "results": results,
    }


def safetensors_metadata(path):
    """Read the SafeTensors JSON header without loading tensor data."""
    try:
        with open(path, "rb") as fh:
            (slen,) = struct.unpack("<Q", fh.read(8))
            if slen <= 0 or slen > (200 << 20):
                return None
            raw = fh.read(slen)
        if len(raw) < slen:
            return None
        data = json.loads(raw.decode("utf-8"))
        return data.get("__metadata__", {}) or None
    except Exception:
        return None


# --------------------------------------------------------------------------
# quarantine
# --------------------------------------------------------------------------
def quarantine_file(path, cfg, quarantine_dir, approved=False, reason="security screening"):
    core.require_approval("quarantine", cfg, approved, "moving a file out of the working tree")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ToolError("not a file: %s" % path, code="not-found")
    qdir = os.path.abspath(quarantine_dir)
    core.ensure_dir(qdir)
    records = os.path.join(qdir, "records")
    core.ensure_dir(records)
    rel = os.path.basename(path)
    stamp = core.iso_now().replace(":", "").replace("-", "")
    target = os.path.join(qdir, "%s.%s.quarantined" % (rel, stamp))
    digest, size = core.sha256_file(path)
    os.replace(path, target)
    record = {
        "schema": "foic.quarantine/1",
        "recorded_at": core.iso_now(),
        "original_path": path,
        "quarantined_path": target,
        "sha256": digest,
        "size": size,
        "reason": reason,
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
    }
    record_path = os.path.join(records, "%s.%s.json" % (rel, stamp))
    core.write_json(record_path, record, overwrite=True)
    return record


def list_quarantine(quarantine_dir):
    qdir = os.path.abspath(quarantine_dir)
    records = os.path.join(qdir, "records")
    out = []
    if os.path.isdir(records):
        for name in sorted(os.listdir(records)):
            if name.endswith(".json"):
                try:
                    out.append(core.read_json(os.path.join(records, name)))
                except ToolError:
                    continue
    return out


def restore_quarantine(record_path, cfg, approved=False):
    core.require_approval("restore", cfg, approved, "returning a quarantined file to its original path")
    record = core.read_json(record_path)
    src = record["quarantined_path"]
    dst = record["original_path"]
    if not os.path.isfile(src):
        raise ToolError("quarantined file is missing: %s" % src, code="not-found")
    if os.path.exists(dst):
        raise ToolError(
            "refusing to restore: original path already occupied: %s" % dst, code="would-overwrite"
        )
    core.ensure_dir(os.path.dirname(dst) or ".")
    os.replace(src, dst)
    os.remove(record_path)
    return {"restored": dst, "record_removed": record_path, "sha256": record["sha256"]}


# --------------------------------------------------------------------------
# licence and rights audit
# --------------------------------------------------------------------------
def _read_license_text(path, limit=262144):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def detect_license(text):
    for pattern, spdx in LICENSE_SPDX_MAP:
        if pattern.search(text):
            return spdx
    return None


def license_audit(root, cfg, policy=None, catalog=None, deadline=None):
    policy = policy or load_license_policy()
    catalog = catalog or load_catalog(cfg)
    root = os.path.abspath(root)
    if not os.path.exists(root):
        raise ToolError("audit root does not exist: %s" % root, code="not-found")
    allow = set(policy["classification"]["allow"])
    review = set(policy["classification"]["review"])
    deny = set(policy["classification"]["deny"])
    sidecar_names = set(n.lower() for n in policy["detection"]["sidecar_filenames"])

    components = []
    for path in core.iter_files(root):
        if deadline:
            deadline.check("auditing licences")
        name = os.path.basename(path)
        lower = name.lower()
        ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
        evidence = None
        detected = None
        if lower in sidecar_names or lower.endswith(".license.txt") or lower.endswith(".licence.txt"):
            evidence = path
            detected = detect_license(_read_license_text(path)) or "unspecified"
        elif ext in ("json", "yaml", "yml"):
            try:
                data = core.read_json(path)
            except ToolError:
                data = None
            if isinstance(data, dict):
                for field in policy["detection"]["json_fields"]:
                    value = data.get(field)
                    if isinstance(value, str):
                        evidence = path
                        detected = detect_license(value) or value.lower()
                        break
                    if isinstance(value, dict):
                        evidence = path
                        detected = detect_license(json.dumps(value)) or "custom"
                        break
        elif ext == "safetensors":
            meta = safetensors_metadata(path) or {}
            for key in policy["detection"]["safetensors_metadata_keys"]:
                key = key.replace("__metadata__.", "")
                if key in meta:
                    evidence = path
                    detected = detect_license(str(meta[key])) or str(meta[key]).lower()
                    break
        if evidence is None:
            continue
        components.append({"path": os.path.relpath(path, root).replace("\\", "/"), "evidence": evidence, "license": detected})

    # directory-level sidecars: a LICENSE at the root covers the whole folder
    for entry in sorted(os.listdir(root)):
        full = os.path.join(root, entry)
        if os.path.isfile(full) and entry.lower() in sidecar_names:
            components.append(
                {"path": ".", "evidence": full, "license": detect_license(_read_license_text(full)) or "unspecified"}
            )

    if not components:
        components.append({"path": ".", "evidence": None, "license": "unknown"})

    out_components = []
    blockers = []
    for comp in components:
        lic = comp["license"] or "unknown"
        if lic in deny:
            classification = "deny"
        elif lic in allow:
            classification = "allow"
        else:
            classification = "review"
        redistribution = policy["redistribution"].get(lic, "unknown")
        template = policy["attribution_templates"].get(lic) or policy["attribution_templates"]["unknown"]
        attribution = template.format(name=os.path.basename(comp["path"]), year="(see license file)", holder="(see license file)", url="(see license file)")
        if classification in ("deny",) or redistribution in ("block", "restricted"):
            blockers.append({"path": comp["path"], "license": lic, "classification": classification, "redistribution": redistribution})
        out_components.append(
            {
                "path": comp["path"],
                "evidence": os.path.relpath(comp["evidence"], root) if comp["evidence"] else None,
                "license": lic,
                "classification": classification,
                "redistribution": redistribution,
                "attribution": attribution,
            }
        )

    catalog_hints = {}
    for cid, comp in catalog.get("components", {}).items():
        lic = comp.get("license")
        if lic:
            catalog_hints[cid] = {
                "license": lic,
                "classification": "allow" if lic in allow else ("deny" if lic in deny else "review"),
                "redistribution": policy["redistribution"].get(lic, "unknown"),
            }

    return {
        "schema": "foic.license-audit/1",
        "generated_at": core.iso_now(),
        "root": root,
        "tool": "%s %s" % (core.TOOL_NAME, core.TOOL_VERSION),
        "mode": policy.get("mode", "audit-only"),
        "summary": {
            "components": len(out_components),
            "allow": sum(1 for c in out_components if c["classification"] == "allow"),
            "review": sum(1 for c in out_components if c["classification"] == "review"),
            "deny": sum(1 for c in out_components if c["classification"] == "deny"),
            "blockers": len(blockers),
            "release_rights_gate": "blocked" if blockers else "clear",
        },
        "components": out_components,
        "blockers": blockers,
        "catalog_hints": catalog_hints,
        "rules": policy.get("rules", []),
    }
