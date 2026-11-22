#!/usr/bin/env python3
"""Core engine for the powerful-extractor-compressor Skill Tool.

Loaded by ``tools-pec.py``; not a standalone entry point. Implements:

* path normalization and the zip-slip / link / device-name defenses
* the preflight gate (counts, expansion, ratio, depth, encryption, capacity)
* :class:`ExtractionJob` — staged, checkpointed, resumable, manifested extraction
* nested-archive recursion with cycle protection and lineage
* organization by content type, collision-safe renames
* verification: manifest hash audit, native test, round-trip compare
* scanning: secrets, file-type mismatch, metadata sanitization
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
PecError = R.PecError
EXIT_USAGE, EXIT_POLICY, EXIT_INTEGRITY = R.EXIT_USAGE, R.EXIT_POLICY, R.EXIT_INTEGRITY
EXIT_PARTIAL, EXIT_IO, EXIT_DEPENDENCY = R.EXIT_PARTIAL, R.EXIT_IO, R.EXIT_DEPENDENCY

if "pec_formats" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_formats", os.path.join(HERE, "tools-pec-formats.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_formats"] = _mod
    _spec.loader.exec_module(_mod)
F = sys.modules["pec_formats"]

WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
DRIVE_RE = re.compile(r"^[A-Za-z]:")

TEXT_LIMIT_FOR_SCAN = 2 << 20


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------
def normalize_entry_path(raw: str, config, depth: int = 0) -> tuple[str, list[str]]:
    """Normalize an archive entry name into a safe relative POSIX path.

    Raises :class:`PecError` (exit 3) for traversal, absolute, or drive paths.
    Returns ``(normalized, warnings)``.
    """
    warnings: list[str] = []
    if not raw or raw.strip() in {"", ".", "./"}:
        return "", warnings
    name = raw.replace("\\", "/")
    if "\x00" in name:
        raise PecError(f"entry name contains a NUL byte: {raw!r}", EXIT_POLICY, "path")
    if DRIVE_RE.match(name) or name.startswith("//"):
        raise PecError(f"entry has a drive/UNC path: {raw!r}", EXIT_POLICY, "path")
    if name.startswith("/"):
        if not config.get("policy.allow_absolute_paths", False):
            raise PecError(f"entry has an absolute path: {raw!r}", EXIT_POLICY, "path")
        name = name.lstrip("/")
    parts: list[str] = []
    for part in name.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise PecError(f"entry escapes the extraction root via '..': {raw!r}", EXIT_POLICY, "traversal")
        parts.append(part)
    if not parts:
        return "", warnings
    norm = unicodedata.normalize("NFC", "/".join(parts))
    cleaned: list[str] = []
    max_name = int(config.get("policy.max_name_length", 200))
    for part in norm.split("/"):
        new = INVALID_CHARS.sub("_", part).rstrip(" .")
        if new != part:
            warnings.append(f"sanitized path component {part!r} -> {new!r}")
        if not new:
            new = "_"
        stem = new.split(".")[0].lower()
        if stem in WINDOWS_RESERVED:
            new = f"_{new}"
            warnings.append(f"reserved device name rewritten: {part!r} -> {new!r}")
        if len(new) > max_name:
            root, ext = os.path.splitext(new)
            keep = max(1, max_name - len(ext) - 9)
            digest = hashlib.sha256(new.encode("utf-8", "replace")).hexdigest()[:8]
            new = f"{root[:keep]}~{digest}{ext}"
            warnings.append(f"component truncated: {part!r} -> {new!r}")
        cleaned.append(new)
    result = "/".join(cleaned)
    if len(result) > int(config.get("limits.max_path_length", 255)):
        raise PecError(f"normalized path exceeds limits.max_path_length: {result[:120]}...",
                       EXIT_POLICY, "path-length")
    return result, warnings


def is_within(root: str, candidate: str) -> bool:
    root_abs = os.path.abspath(root)
    cand_abs = os.path.abspath(candidate)
    try:
        return os.path.commonpath([root_abs, cand_abs]) == root_abs
    except ValueError:
        return False


def resolve_output_root(dest: str, config, create: bool = True) -> str:
    """Validate the destination against protected and allowed roots."""
    dest_abs = os.path.abspath(dest)
    protected = [os.path.abspath(p) for p in config.get("policy.protected_roots", []) or [] if p]
    for root in protected:
        if dest_abs == root or is_within(root, dest_abs):
            raise PecError(f"destination {dest_abs} is inside a protected root ({root})",
                           EXIT_POLICY, "protected-root")
    allowed = [p for p in (config.get("policy.allowed_output_roots", []) or []) if p]
    if allowed:
        roots = [os.path.abspath(p) for p in allowed]
        if not any(dest_abs == r or is_within(r, dest_abs) for r in roots):
            raise PecError(
                f"destination {dest_abs} is outside policy.allowed_output_roots {roots}",
                EXIT_POLICY, "allowed-roots")
    if create:
        try:
            os.makedirs(dest_abs, exist_ok=True)
        except OSError as exc:
            raise PecError(f"cannot create destination {dest_abs}: {exc}", EXIT_IO) from exc
    return dest_abs


def safe_join(root: str, rel: str) -> str:
    candidate = os.path.normpath(os.path.join(root, *rel.split("/")))
    if not is_within(root, candidate):
        raise PecError(f"refusing to write outside the extraction root: {rel!r}",
                       EXIT_POLICY, "traversal")
    return candidate


# --------------------------------------------------------------------------
# Selection filters
# --------------------------------------------------------------------------
@dataclass
class Selector:
    include: list[str] = field(default_factory=list)      # glob patterns
    exclude: list[str] = field(default_factory=list)
    regex: str = ""
    extensions: list[str] = field(default_factory=list)
    min_size: int | None = None
    max_size: int | None = None
    since: float | None = None
    until: float | None = None
    index_range: tuple[int, int] | None = None          # 1-based inclusive
    limit: int | None = None                            # max matching entries

    def matches(self, entry, index: int) -> bool:
        name = entry.name.replace("\\", "/")
        if self.include and not any(_glob(p, name) for p in self.include):
            return False
        if self.exclude and any(_glob(p, name) for p in self.exclude):
            return False
        if self.regex and not re.search(self.regex, name):
            return False
        if self.extensions:
            ext = os.path.splitext(name)[1].lower()
            if ext not in {e if e.startswith(".") else "." + e for e in self.extensions}:
                return False
        if self.min_size is not None and entry.size < self.min_size:
            return False
        if self.max_size is not None and entry.size > self.max_size:
            return False
        if self.since is not None and (entry.mtime is None or entry.mtime < self.since):
            return False
        if self.until is not None and (entry.mtime is None or entry.mtime > self.until):
            return False
        if self.index_range:
            low, high = self.index_range
            if not (low <= index <= high):
                return False
        return True


def _glob(pattern: str, name: str) -> bool:
    if fnmatch.fnmatch(name, pattern):
        return True
    if pattern.startswith("*") and fnmatch.fnmatch("/" + name, pattern):
        return True
    return fnmatch.fnmatch(os.path.basename(name), pattern)


def parse_date(text: str) -> float:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    try:
        return float(text)
    except ValueError as exc:
        raise PecError(f"cannot parse date {text!r}; use YYYY-MM-DD", EXIT_USAGE) from exc


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------
def preflight(path: str, config, password: str | None = None) -> dict:
    """Inspect an archive and estimate resources without writing anything."""
    volumes = F.collect_volumes(path)
    report: dict = {
        "source": os.path.abspath(path),
        "volumes": {k: v for k, v in volumes.items() if k != "volumes"},
        "volume_paths": volumes.get("volumes", []),
        "checks": [],
        "warnings": [],
        "blocking": [],
    }
    if volumes.get("multipart") and not volumes.get("complete"):
        report["blocking"].append({
            "check": "volumes", "detail": f"missing volume numbers: {volumes.get('missing')}",
        })
    try:
        entries, meta = F.list_entries(path, config, password)
    except PecError as exc:
        report["error"] = exc.as_dict()
        report["blocking"].append({"check": "open", "detail": exc.message})
        return report
    report["type"] = meta
    entries_only = [e for e in entries if not e.is_dir]
    total = sum(e.size for e in entries_only)
    compressed = sum(e.compressed_size for e in entries_only) or os.path.getsize(path)
    limits = config.limits
    report["entries"] = len(entries)
    report["files"] = len(entries_only)
    report["directories"] = len(entries) - len(entries_only)
    report["expanded_bytes"] = total
    report["expanded_human"] = R.human_bytes(total)
    report["compressed_bytes"] = compressed
    report["compressed_human"] = R.human_bytes(compressed)
    report["ratio"] = round(total / compressed, 3) if compressed else 0.0
    report["encrypted_entries"] = sum(1 for e in entries if e.encrypted)
    report["symlinks"] = sum(1 for e in entries if e.link_target and e.link_target.startswith("symlink"))
    report["hardlinks"] = sum(1 for e in entries if e.link_target and e.link_target.startswith("hardlink"))
    report["max_entry_bytes"] = max((e.size for e in entries_only), default=0)
    report["largest"] = sorted(entries_only, key=lambda e: e.size, reverse=True)[:5]
    report["largest"] = [{"name": e.name, "size": e.size} for e in report["largest"]]
    report["estimated_temp_bytes"] = total
    depth_guard = int(limits.get("max_depth", 5))

    def check(name: str, ok: bool, detail: str) -> None:
        rec = {"check": name, "ok": ok, "detail": detail}
        report["checks"].append(rec)
        if not ok:
            report["blocking"].append(rec)

    check("entry-count", len(entries) <= int(limits.get("max_entries", 200000)),
          f"{len(entries)} entries vs limit {limits.get('max_entries')}")
    check("expanded-size", total <= int(limits.get("max_total_bytes", 21 << 30)),
          f"{R.human_bytes(total)} vs limit {R.human_bytes(limits.get('max_total_bytes'))}")
    check("expansion-ratio", report["ratio"] <= float(limits.get("max_ratio", 200)),
          f"ratio {report['ratio']} vs limit {limits.get('max_ratio')}")
    biggest = report["max_entry_bytes"]
    check("largest-entry", biggest <= int(limits.get("max_entry_bytes", 8 << 30)),
          f"{R.human_bytes(biggest)} vs limit {R.human_bytes(limits.get('max_entry_bytes'))}")
    check("nesting-budget", depth_guard >= 0, f"nesting depth allowed: {depth_guard}")

    if report["encrypted_entries"]:
        report["warnings"].append(
            f"{report['encrypted_entries']} encrypted entries; supply a password via "
            f"--password-env or --password-file")
    traversal: list[str] = []
    for entry in entries:
        name = entry.name.replace("\\", "/")
        if name.startswith("/") or DRIVE_RE.match(name) or ".." in name.split("/"):
            traversal.append(entry.name)
    if traversal:
        report["warnings"].append(f"{len(traversal)} entries contain traversal/absolute paths and will be blocked")
        report["traversal_samples"] = traversal[:10]
    junk = [e.name for e in entries if any(j in e.name for j in (config.get("policy.metadata_junk") or []))]
    if junk:
        report["warnings"].append(f"{len(junk)} entries look like platform metadata (can be dropped with --strip-metadata)")
    if volumes.get("multipart"):
        report["assembly_required"] = volumes.get("kind") in {"numeric-chunks"}
    usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)) or ".")
    report["disk"] = {"free_bytes": usage.free, "free_human": R.human_bytes(usage.free)}
    needed = total * (2 if config.get("extraction.publish", "atomic") == "atomic" else 1)
    report["needs_free_bytes"] = needed
    if needed > usage.free:
        report["warnings"].append(
            f"estimated {R.human_bytes(needed)} needed but only {R.human_bytes(usage.free)} free on this volume")
    report["safe_to_extract"] = not report["blocking"]
    return report


# --------------------------------------------------------------------------
# Rate / progress
# --------------------------------------------------------------------------
class Progress:
    def __init__(self, job_id: str, total_entries: int, total_bytes: int, echo: bool = True):
        self.job_id = job_id
        self.total_entries = max(1, total_entries)
        self.total_bytes = max(1, total_bytes)
        self.done_entries = 0
        self.done_bytes = 0
        self.started = time.monotonic()
        self.echo = echo
        self.last_emit = 0.0

    def advance(self, name: str, nbytes: int, force: bool = False) -> None:
        self.done_entries += 1
        self.done_bytes += nbytes
        now = time.monotonic()
        if self.echo and (force or now - self.last_emit >= 1.0):
            self.last_emit = now
            elapsed = max(1e-6, now - self.started)
            rate = self.done_bytes / elapsed
            remaining = max(0, self.total_bytes - self.done_bytes)
            eta = remaining / rate if rate > 0 else 0
            sys.stderr.write(
                f"  [{self.done_entries}/{self.total_entries}] {name[:60]:<60} "
                f"{R.human_bytes(self.done_bytes)}/{R.human_bytes(self.total_bytes)} "
                f"{R.human_bytes(int(rate))}/s eta {int(eta)}s\n")
            sys.stderr.flush()

    def summary(self) -> dict:
        elapsed = max(1e-6, time.monotonic() - self.started)
        return {
            "entries": self.done_entries,
            "bytes": self.done_bytes,
            "seconds": round(elapsed, 3),
            "throughput_mbps": round((self.done_bytes / (1 << 20)) / elapsed, 2),
        }


# --------------------------------------------------------------------------
# Checkpoints
# --------------------------------------------------------------------------
class Checkpoint:
    """Durable per-entry progress so a job can resume after failure."""

    def __init__(self, state_dir: str, key: str, enabled: bool = True):
        self.path = os.path.join(state_dir, "checkpoints", f"{key}.jsonl")
        self.enabled = enabled
        self.completed: dict[str, dict] = {}
        if enabled and os.path.exists(self.path):
            for rec in R.read_jsonl(self.path):
                if rec.get("name"):
                    self.completed[rec["name"]] = rec

    def record(self, name: str, path: str, size: int, digest: str, rel: str = "") -> None:
        if not self.enabled:
            return
        rec = {"ts": R.utcnow(), "name": name, "path": path, "size": size, "sha256": digest,
               "rel": rel or name, "schema": R.CHECKPOINT_SCHEMA}
        self.completed[name] = rec
        R.append_jsonl(self.path, rec)

    def already_done(self, name: str, dest_path: str, final_root: str | None = None) -> bool:
        rec = self.completed.get(name)
        if not rec:
            return False
        candidates = [dest_path]
        rel = rec.get("rel") or rec.get("name")
        if final_root and rel:
            candidates.append(safe_join(final_root, rel))
        for candidate in candidates:
            if not os.path.exists(candidate):
                continue
            try:
                if os.path.getsize(candidate) == rec.get("size"):
                    return True
            except OSError:
                continue
        return False

    def clear(self) -> None:
        self.completed = {}
        if os.path.exists(self.path):
            os.unlink(self.path)


# --------------------------------------------------------------------------
# Extraction job
# --------------------------------------------------------------------------
@dataclass
class ExtractOptions:
    destination: str
    selector: Selector = field(default_factory=Selector)
    nest: bool = False
    max_depth: int = 0
    quarantine: bool = False
    strip_metadata: bool = False
    preserve_times: bool = True
    organize: str = "none"
    publish: str = "atomic"
    checkpoint: bool = True
    resume: bool = False
    password: str | None = None
    scan: bool = True
    assemble: bool = True
    overwrite: str = "never"
    verify_hashes: bool = True
    job_key: str = ""


class ExtractionJob:
    """Extract one archive into a destination with safety, checkpoints and manifest."""

    def __init__(self, source: str, options: ExtractOptions, config, logger, cancellation=None,
                 gate=None, job_id: str = "", depth: int = 0, parent: dict | None = None):
        self.source = os.path.abspath(source)
        self.options = options
        self.config = config
        self.log = logger
        self.cancel = cancellation or R.Cancellation().install()
        self.gate = gate or R.ApprovalGate(config)
        self.job_id = job_id or R.new_job_id()
        self.depth = depth
        self.parent = parent or {}
        self.state_dir = logger.state_dir
        self.stage_root = ""
        self.final_root = ""
        self.manifest_entries: list[dict] = []
        self.errors: list[dict] = []
        self.warnings: list[str] = []
        self.skips: list[dict] = []
        self.renames: list[dict] = []
        self.scan_findings: list[dict] = []
        self.nested_results: list[dict] = []
        self.bytes_written = 0
        self._direct = False
        self.started = time.monotonic()

    # -- public ---------------------------------------------------------
    def run(self) -> dict:
        final = resolve_output_root(self.options.destination, self.config, create=False)
        if self.options.publish == "direct":
            os.makedirs(final, exist_ok=True)
            stage = final
            self._direct = True
        else:
            stage = R.temp_workdir(self.state_dir, self.job_id, "stage")
            self._direct = False
        key = self.options.job_key or hashlib.sha256(
            f"{self.source}|{final}|{self.job_id}".encode()).hexdigest()[:16]
        ckpt = Checkpoint(self.state_dir, key, enabled=self.options.checkpoint)
        if not self.options.resume and ckpt.completed:
            ckpt.clear()
        self.final_root = final
        self.stage_root = stage

        sources = [self.source]
        if self.options.assemble:
            volumes = F.collect_volumes(self.source)
            if volumes.get("multipart"):
                if not volumes.get("complete"):
                    raise PecError(
                        f"multi-volume set is incomplete; missing volume numbers "
                        f"{volumes.get('missing')}", EXIT_INTEGRITY, "volumes",
                        detail=volumes)
                if volumes.get("kind") == "numeric-chunks":
                    merged = os.path.join(stage, "_assembled_" + os.path.basename(self.source))
                    self.log.info("volumes.assemble", f"assembling {volumes['count']} volumes")
                    info = F.assemble_volumes(volumes["volumes"], merged)
                    self.warnings.append(
                        f"assembled {volumes['count']} volumes into one container ({info['detected']})")
                    sources = [merged]
                elif volumes.get("kind") == "zip-spanned":
                    self.warnings.append(
                        "zip-spanned sets (.z01/.z02 + .zip) can be read directly by zipfile; no assembly needed")

        pre = preflight(sources[0], self.config, self.options.password)
        if pre.get("blocking"):
            raise PecError(
                f"preflight blocked extraction of {os.path.basename(self.source)}: "
                f"{pre['blocking'][0].get('detail')}", EXIT_POLICY, "preflight", detail=pre["blocking"])
        self.warnings.extend(pre.get("warnings", []))

        entries, meta = F.list_entries(sources[0], self.config, self.options.password)
        kind = meta["opened_as"]
        file_entries = [e for e in entries if not e.is_dir]
        selected: list[tuple[int, object]] = []
        for idx, entry in enumerate(entries, start=1):
            if entry.is_dir:
                continue
            if self.options.selector.matches(entry, idx):
                selected.append((idx, entry))
        if self.options.selector.limit:
            selected = selected[: self.options.selector.limit]
        if not selected:
            self.warnings.append("no entries matched the selection filters")

        total_bytes = sum(e.size for _, e in selected)
        progress = Progress(self.job_id, len(selected), total_bytes, echo=not self.config.get("quiet", False))
        self.log.info("extract.start", f"{len(selected)} entries selected from {kind}",
                      entries=len(entries), selected=len(selected), bytes=total_bytes, kind=kind)

        max_depth = self.options.max_depth or int(self.config.get("limits.max_depth", 0))
        nested_seen: set[str] = {self.source}

        for index, entry in selected:
            self.cancel.check()
            try:
                self._extract_entry(kind, entry, sources[0], ckpt, progress, nested_seen,
                                    max_depth, index)
            except PecError as exc:
                if exc.code == R.EXIT_CANCELLED:
                    raise
                if exc.code in {EXIT_POLICY} and exc.kind in {"traversal", "bomb"}:
                    self.errors.append({"name": entry.name, "kind": exc.kind, "message": exc.message})
                    self.log.warn("extract.blocked", exc.message, entry=entry.name, kind=exc.kind)
                else:
                    self.errors.append({"name": entry.name, "kind": exc.kind, "message": exc.message})
                    self.log.error("extract.entry-failed", exc.message, entry=entry.name)
            except OSError as exc:
                self.errors.append({"name": entry.name, "kind": "io", "message": str(exc)})
                self.log.error("extract.entry-failed", str(exc), entry=entry.name)

        if self.options.scan and self.config.get("scanner.enable_secret_scan", True):
            findings = scan_tree(self.stage_root, self.config)
            self.scan_findings.extend(findings)
            gate_mode = str(self.config.get("policy.secret_gate", "warn"))
            if findings and gate_mode == "block":
                raise PecError(
                    f"secret scan found {len(findings)} findings and policy.secret_gate=block",
                    EXIT_POLICY, "secret", detail=findings[:20])

        if self.options.organize == "by-content-type":
            self._organize_stage()

        if self.options.strip_metadata:
            self._strip_metadata()

        if self.errors and not self.manifest_entries and not self.skips:
            status = "failed"
        elif self.errors:
            status = "partial"
        else:
            status = "completed"
        manifest = self._build_manifest(pre, meta, status, sources[0])
        manifest_path = os.path.join(stage, "pec-manifest.json")
        R.write_json_atomic(manifest_path, manifest)
        self._write_human_manifest(stage, manifest)

        published = self._publish(stage)
        self.log.info("extract.done", f"published to {published}", status=status)
        elapsed = max(1e-6, time.monotonic() - self.started)
        return {
            "source": self.source,
            "destination": published,
            "container": kind,
            "status": status,
            "entries_total": len(entries),
            "entries_selected": len(selected),
            "entries_written": len(self.manifest_entries),
            "entries_skipped": len(self.skips),
            "entries_failed": len(self.errors),
            "bytes_written": self.bytes_written,
            "bytes_written_human": R.human_bytes(self.bytes_written),
            "seconds": round(elapsed, 3),
            "throughput_mbps": round((self.bytes_written / (1 << 20)) / elapsed, 3),
            "manifest": os.path.join(published, "pec-manifest.json"),
            "progress": progress.summary(),
            "warnings": self.warnings,
            "skips": self.skips[:50],
            "renames": self.renames[:100],
            "errors": self.errors,
            "scan_findings": self.scan_findings[:100],
            "scan_finding_count": len(self.scan_findings),
            "nested": self.nested_results,
            "fingerprint": R.compute_fingerprint([self.source],
                                                 {"selector": vars(self.options.selector),
                                                  "nest": self.options.nest}),
        }

    # -- internals ------------------------------------------------------
    def _extract_entry(self, kind, entry, container_path, ckpt, progress, nested_seen,
                       max_depth, index) -> None:
        rel, warnings = normalize_entry_path(entry.name, self.config)
        for warning in warnings:
            self.warnings.append(warning)
        if not rel:
            return
        dest_path = safe_join(self.stage_root, rel)
        if ckpt.already_done(entry.name, dest_path, self.final_root):
            self.skips.append({"name": entry.name, "reason": "already completed (checkpoint)"})
            progress.advance(rel, entry.size)
            return
        if entry.link_target:
            self._handle_link(entry, rel, dest_path)
            return
        os.makedirs(os.path.dirname(dest_path) or self.stage_root, exist_ok=True)
        existing = os.path.exists(dest_path)
        if existing and ckpt.already_done(entry.name, dest_path, self.final_root):
            self.skips.append({"name": entry.name, "reason": "already present (verified)"})
            progress.advance(rel, entry.size)
            return
        if existing:
            action = self.options.overwrite
            if action == "never":
                raise PecError(f"refusing to overwrite existing file: {rel}",
                               EXIT_POLICY, "overwrite")
            if action == "suffix":
                dest_path = _unique_path(dest_path)
                self.renames.append({"original": rel, "written": os.path.relpath(dest_path, self.stage_root),
                                     "reason": "collision"})
                rel = os.path.relpath(dest_path, self.stage_root).replace(os.sep, "/")
            elif action == "replace":
                self.gate.require("overwrite", detail=f"replacing {rel}")
        result = self._stream_entry(container_path, kind, entry, dest_path)
        try:
            if self.options.preserve_times and entry.mtime:
                os.utime(dest_path, (entry.mtime, entry.mtime))
            mode = 0o600 if self.options.quarantine else 0o644
            if self.options.quarantine:
                mode = int(str(self.config.get("policy.quarantine_permissions", "0500")), 8)
            elif self.config.get("policy.strip_executable_bits", True):
                mode = 0o644
            os.chmod(dest_path, mode)
        except OSError:
            pass
        self.bytes_written += result["bytes"]
        self.manifest_entries.append({
            "path": rel,
            "source_entry": entry.name,
            "size": result["bytes"],
            "sha256": result["sha256"],
            "mtime": entry.mtime,
            "mode": oct(os.stat(dest_path).st_mode & 0o777) if os.path.exists(dest_path) else None,
            "method": entry.extra.get("method") if isinstance(entry.extra, dict) else None,
            "container": os.path.basename(container_path),
            "depth": self.depth,
            "parent": self.parent.get("source_entry"),
        })
        ckpt.record(entry.name, dest_path, result["bytes"], result["sha256"], rel=rel)
        progress.advance(rel, result["bytes"])

        if self.options.nest and self.depth < max_depth:
            nested = self._maybe_nest(dest_path, rel, nested_seen, max_depth)
            if nested:
                self.nested_results.append(nested)

    def _stream_entry(self, container_path: str, kind: str, entry, dest_path: str) -> dict:
        chunk = int(self.config.get("limits.io_chunk_bytes", 1 << 20))
        limit = int(self.config.get("limits.max_entry_bytes", 8 << 30))
        digest = hashlib.sha256()
        written = 0
        started = time.monotonic()
        with open(dest_path, "wb") as out:
            try:
                for data in read_entry_bytes(container_path, kind, entry, self.config,
                                             self.options.password, chunk, limit):
                    written += len(data)
                    if written > limit:
                        raise PecError(
                            f"entry {entry.name!r} exceeded the per-entry byte limit ({limit} bytes)",
                            EXIT_POLICY, "bomb")
                    out.write(data)
                    digest.update(data)
            except PecError:
                out.close()
                try:
                    os.unlink(dest_path)
                except OSError:
                    pass
                raise
            out.flush()
            os.fsync(out.fileno())
        elapsed = max(1e-6, time.monotonic() - started)
        return {"path": dest_path, "bytes": written, "sha256": digest.hexdigest(),
                "seconds": round(elapsed, 4),
                "throughput_mbps": round((written / (1 << 20)) / elapsed, 2)}

    def _handle_link(self, entry, rel: str, dest_path: str) -> None:
        target = entry.link_target or ""
        is_symlink = target.startswith("symlink->")
        actual = target.split("->", 1)[1] if "->" in target else target
        allow = (self.config.get("policy.allow_symlinks", False) and is_symlink) or \
                (self.config.get("policy.allow_hardlinks", False) and not is_symlink)
        resolved = os.path.normpath(os.path.join(os.path.dirname(dest_path), actual))
        escapes = os.path.isabs(actual) or not is_within(self.stage_root, resolved)
        if not allow or escapes:
            self.skips.append({"name": entry.name, "reason": "link blocked",
                               "detail": "policy forbids links" if not allow else "target escapes root",
                               "target": actual})
            self.warnings.append(f"blocked link entry {entry.name!r} -> {actual!r}")
            return
        os.makedirs(os.path.dirname(dest_path) or self.stage_root, exist_ok=True)
        if is_symlink:
            os.symlink(actual, dest_path)
        else:
            try:
                os.link(os.path.join(self.stage_root, actual), dest_path)
            except OSError as exc:
                self.skips.append({"name": entry.name, "reason": "hardlink failed", "detail": str(exc)})
                return
        self.manifest_entries.append({"path": rel, "source_entry": entry.name, "size": 0,
                                      "sha256": "", "link": target, "container": os.path.basename(self.source)})

    def _maybe_nest(self, file_path: str, rel: str, nested_seen: set, max_depth: int) -> dict | None:
        try:
            info = F.detect_file(file_path)
        except PecError:
            return None
        stream_kinds = {"gzip", "bzip2", "xz", "zstd", "lz4"}
        if info["id"] not in F.ARCHIVE_KINDS and info["id"] not in stream_kinds:
            return None
        if info["id"] in stream_kinds and not F._is_tar_stream(file_path) and info["id"] != "zstd":
            self.warnings.append(
                f"nested {rel!r} is a single compressed stream; use `create --format` or `list` instead of nesting")
            return None
        digest = _file_sha256(file_path)
        if digest in nested_seen:
            self.warnings.append(f"cycle protection: {rel!r} duplicates an already processed archive")
            return {"source": rel, "skipped": "cycle"}
        nested_seen.add(digest)
        inner_dest = os.path.splitext(file_path)[0] + "__unpacked"
        internal = self.config.copy()
        internal.data.setdefault("policy", {})["allowed_output_roots"] = []
        internal.data.setdefault("policy", {})["protected_roots"] = []
        job = ExtractionJob(
            file_path,
            ExtractOptions(
                destination=inner_dest,
                selector=Selector(),
                nest=True,
                max_depth=max_depth,
                quarantine=self.options.quarantine,
                scan=self.options.scan,
                publish="direct",
                checkpoint=False,
                password=self.options.password,
                overwrite="suffix",
                job_key="",
            ),
            internal, self.log, self.cancel, self.gate, job_id=self.job_id,
            depth=self.depth + 1, parent={"source_entry": rel},
        )
        try:
            result = job.run()
            self.log.info("nest.extracted", f"{rel} -> {result['entries_written']} entries",
                          parent=rel, depth=self.depth + 1)
            result["parent_entry"] = rel
            result["digest"] = digest
            return result
        except PecError as exc:
            self.warnings.append(f"nested extraction of {rel!r} failed: {exc.message}")
            return {"source": rel, "error": exc.message, "depth": self.depth + 1}

    def _organize_stage(self) -> None:
        mapping = R.load_asset(str(self.config.get("content_types_file", "asset-content-types.json")))
        moved = 0
        for entry in list(self.manifest_entries):
            src = os.path.join(self.stage_root, *entry["path"].split("/"))
            if not os.path.isfile(src):
                continue
            category = classify_path(entry["path"], mapping)
            target_dir = os.path.join(self.stage_root, category)
            os.makedirs(target_dir, exist_ok=True)
            target = _unique_path(os.path.join(target_dir, os.path.basename(src)))
            try:
                shutil.move(src, target)
                moved += 1
                entry["path"] = os.path.relpath(target, self.stage_root).replace(os.sep, "/")
                entry["category"] = category
            except OSError as exc:
                self.warnings.append(f"could not organize {entry['path']}: {exc}")
        self.log.info("organize.by-content-type", f"moved {moved} files")

    def _strip_metadata(self) -> None:
        junk = self.config.get("policy.metadata_junk", []) or []
        removed = 0
        for dirpath, dirnames, filenames in os.walk(self.stage_root):
            for name in list(filenames):
                rel = os.path.relpath(os.path.join(dirpath, name), self.stage_root).replace(os.sep, "/")
                if any(j.rstrip("/") in rel.split("/") or j in rel for j in junk):
                    if name in {"pec-manifest.json", "pec-manifest.md"}:
                        continue
                    try:
                        os.unlink(os.path.join(dirpath, name))
                        removed += 1
                    except OSError:
                        pass
        if removed:
            self.warnings.append(f"strip-metadata removed {removed} platform metadata files")
            self.log.info("metadata.strip", f"removed {removed} junk files")

    def _build_manifest(self, pre: dict, meta: dict, status: str, container: str) -> dict:
        return {
            "schema": R.MANIFEST_SCHEMA,
            "skill": R.SKILL_NAME,
            "version": R.VERSION,
            "job_id": self.job_id,
            "created_at": R.utcnow(),
            "status": status,
            "source": os.path.abspath(container),
            "source_fingerprint": R.compute_fingerprint([container], {"depth": self.depth}),
            "container_type": meta.get("id"),
            "destination": self.final_root,
            "counts": {
                "entries": len(self.manifest_entries),
                "skipped": len(self.skips),
                "failed": len(self.errors),
                "bytes": self.bytes_written,
            },
            "preflight": {
                "entries": pre.get("entries"),
                "expanded_bytes": pre.get("expanded_bytes"),
                "ratio": pre.get("ratio"),
            },
            "entries": self.manifest_entries,
            "skips": self.skips,
            "errors": self.errors,
            "warnings": self.warnings,
            "scan_findings": self.scan_findings,
            "lineage": {
                "schema": R.LINEAGE_SCHEMA,
                "depth": self.depth,
                "parent_entry": self.parent.get("source_entry"),
                "chain": [*self.parent.get("chain", []), os.path.basename(container)],
            },
        }

    def _write_human_manifest(self, stage: str, manifest: dict) -> None:
        counts = manifest["counts"]
        lines = [
            f"# Extraction manifest — {os.path.basename(manifest['source'])}",
            "",
            f"- Job: `{manifest['job_id']}`",
            f"- Created: {manifest['created_at']}",
            f"- Status: **{manifest['status']}**",
            f"- Container: {manifest['container_type']}",
            f"- Entries written: {counts['entries']}",
            f"- Skipped: {counts['skipped']} · Failed: {counts['failed']}",
            f"- Bytes written: {R.human_bytes(counts['bytes'])}",
            "",
            "## Largest files",
            "",
        ]
        top = sorted(manifest["entries"], key=lambda e: e["size"], reverse=True)[:20]
        for item in top:
            lines.append(f"- `{item['path']}` — {R.human_bytes(item['size'])}")
        if manifest["errors"]:
            lines += ["", "## Failures", ""]
            for err in manifest["errors"][:50]:
                lines.append(f"- `{err.get('name')}` — {err.get('message')}")
        if manifest["warnings"]:
            lines += ["", "## Warnings", ""]
            for warn in manifest["warnings"][:50]:
                lines.append(f"- {warn}")
        with open(os.path.join(stage, "pec-manifest.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")

    def _publish(self, stage: str) -> str:
        final = self.final_root
        if getattr(self, "_direct", False) or self.options.publish == "direct":
            return final
        existing = os.path.exists(final) and os.listdir(final)
        if existing:
            if self.config.get("policy.overwrite") == "replace":
                self.gate.require("overwrite", detail=f"replacing destination {final}")
                shutil.rmtree(final)
            else:
                final = _unique_path(final)
                self.warnings.append(f"destination already existed; published to {final}")
        os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
        try:
            os.replace(stage, final)
        except OSError:
            # different filesystems: copy then remove
            shutil.copytree(stage, final, dirs_exist_ok=True)
            shutil.rmtree(stage, ignore_errors=True)
        return final


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    for i in range(2, 10_000):
        candidate = f"{root}~{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
    raise PecError(f"cannot find a free name for {path}", EXIT_IO, "collision")


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_entry_bytes(container_path: str, kind: str, entry, config, password: str | None = None,
                     chunk: int = 1 << 20, max_bytes: int | None = None):
    """Yield entry bytes for any container backend, including 7z/RAR that need a temp file."""
    import io as _io
    if kind in {"7z", "rar"}:
        limit = max_bytes if max_bytes is not None else int(config.get("limits.max_entry_bytes", 8 << 30))
        produced = 0
        for data in F.iter_entry_bytes(container_path, kind, entry, config, password, chunk):
            produced += len(data)
            if produced > limit:
                raise PecError(f"entry {entry.name!r} exceeded the per-entry byte limit",
                               EXIT_POLICY, "bomb")
            yield data
        return
    yield from F.iter_entry_bytes(container_path, kind, entry, config, password, chunk)


def classify_path(path: str, mapping: dict) -> str:
    ext = os.path.splitext(path)[1].lower()
    for category, spec in mapping.get("categories", {}).items():
        if ext in {e.lower() for e in spec.get("extensions", [])}:
            return category
    return mapping.get("fallback", "unknown")


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
def hash_file(path: str, algorithm: str = "sha256") -> dict:
    try:
        h = hashlib.new(algorithm)
    except ValueError as exc:
        raise PecError(f"unsupported hash algorithm {algorithm!r}", EXIT_USAGE) from exc
    size = 0
    started = time.monotonic()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return {"path": path, "algorithm": algorithm, "digest": h.hexdigest(), "bytes": size,
            "seconds": round(time.monotonic() - started, 3)}


def verify_manifest(manifest_path: str, root: str | None = None, config=None,
                    sample: int | None = None) -> dict:
    manifest = R.read_json(manifest_path, None)
    if manifest is None:
        raise PecError(f"manifest not found: {manifest_path}", EXIT_USAGE)
    if manifest.get("schema") != R.MANIFEST_SCHEMA:
        raise PecError(f"unexpected manifest schema {manifest.get('schema')!r}", EXIT_USAGE)
    root = os.path.abspath(root or manifest.get("destination") or os.getcwd())
    entries = manifest.get("entries", [])
    if sample:
        entries = entries[:sample]
    checked = matched = 0
    mismatches: list[dict] = []
    missing: list[str] = []
    started = time.monotonic()
    for entry in entries:
        if entry.get("link"):
            continue
        target = safe_join(root, entry["path"])
        checked += 1
        if not os.path.exists(target):
            missing.append(entry["path"])
            continue
        info = hash_file(target, "sha256")
        if info["digest"] == entry.get("sha256") and info["bytes"] == entry.get("size"):
            matched += 1
        else:
            mismatches.append({"path": entry["path"], "expected": entry.get("sha256"),
                               "actual": info["digest"], "expected_bytes": entry.get("size"),
                               "actual_bytes": info["bytes"]})
    extra = [os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
             for dp, _, fs in os.walk(root) for f in fs
             if f not in {"pec-manifest.json", "pec-manifest.md"}]
    known = {e["path"] for e in manifest.get("entries", [])}
    unexpected = [p for p in extra if p not in known]
    ok = not mismatches and not missing
    return {
        "manifest": os.path.abspath(manifest_path),
        "root": root,
        "entries_checked": checked,
        "matched": matched,
        "missing": missing[:100],
        "mismatched": mismatches[:100],
        "unexpected": unexpected[:100],
        "ok": ok,
        "seconds": round(time.monotonic() - started, 3),
    }


def round_trip_check(archive: str, config, workdir: str, password: str | None = None,
                     keep: bool = False) -> dict:
    """Extract into an isolated scratch root, re-hash every file, then clean up."""
    source_listing = preflight(archive, config, password)
    if source_listing.get("blocking"):
        raise PecError("preflight blocked round-trip check", EXIT_POLICY, "preflight",
                       detail=source_listing["blocking"])
    scratch_parent = os.path.join(config.state_dir(), "tmp")
    os.makedirs(scratch_parent, exist_ok=True)
    import tempfile
    scratch = tempfile.mkdtemp(prefix="roundtrip-", dir=scratch_parent)
    internal = config.copy()
    internal.data.setdefault("policy", {})["allowed_output_roots"] = []
    internal.data.setdefault("policy", {})["protected_roots"] = []
    try:
        job = ExtractionJob(archive, ExtractOptions(destination=os.path.join(scratch, "content"),
                                                    publish="direct", checkpoint=False, scan=False,
                                                    password=password, overwrite="suffix"),
                            internal, R.Logger(scratch, "roundtrip", echo=False),
                            R.Cancellation().install(), R.ApprovalGate(internal, granted=["overwrite"]))
        result = job.run()
        manifest = R.read_json(result["manifest"], {})
        entries = manifest.get("entries", [])
        mismatches = []
        for entry in entries:
            target = safe_join(result["destination"], entry["path"])
            if not os.path.exists(target):
                mismatches.append({"path": entry["path"], "manifest": entry.get("sha256"),
                                   "recheck": "missing"})
                continue
            info = hash_file(target, "sha256")
            if info["digest"] != entry.get("sha256"):
                mismatches.append({"path": entry["path"], "manifest": entry.get("sha256"),
                                   "recheck": info["digest"]})
        archived = []
        try:
            listed, meta = F.list_entries(archive, config, password)
            stream_kind = meta["opened_as"]
            for item in listed:
                if item.is_dir or item.link_target:
                    continue
                digest = hashlib.sha256()
                for data in read_entry_bytes(archive, stream_kind, item, config, password):
                    digest.update(data)
                archived.append({"path": item.name, "sha256": digest.hexdigest()})
        except PecError as exc:
            archived = [{"error": exc.message}]
        archive_hashes = {a["path"]: a["sha256"] for a in archived if a.get("sha256")}
        for entry in entries:
            source_name = entry.get("source_entry")
            if source_name in archive_hashes and archive_hashes[source_name] != entry.get("sha256"):
                mismatches.append({"path": entry["path"], "manifest": entry.get("sha256"),
                                   "archive": archive_hashes[source_name],
                                   "kind": "archive-recheck"})
        return {
            "archive": os.path.abspath(archive),
            "entries": len(entries),
            "mismatches": mismatches,
            "ok": not mismatches,
            "preflight_ratio": source_listing.get("ratio"),
            "scratch_kept": keep,
            "scratch": scratch if keep else None,
            "entries_extracted": result["entries_written"],
            "archive_entries_rehashed": len(archive_hashes),
        }
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------
def scan_tree(root: str, config) -> list[dict]:
    """Scan extracted text-like files for secrets and PII patterns."""
    patterns = R.load_asset(str(config.get("scanner.secret_patterns_file", "asset-secret-patterns.json")))
    enabled = [p for p in patterns.get("patterns", []) if p.get("enabled", True)]
    allowed_ext = {e.lower() for e in config.get("scanner.scan_text_extensions", [])}
    findings: list[dict] = []
    cap = int(config.get("scanner.max_findings", 500))
    min_conf = str(config.get("scanner.min_confidence", "medium"))
    rank = {"low": 0, "medium": 1, "high": 2}
    threshold = rank.get(min_conf, 1)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if len(findings) >= cap:
                return findings
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if name.startswith("pec-manifest"):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in allowed_ext:
                continue
            if os.path.getsize(path) > TEXT_LIMIT_FOR_SCAN:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for pattern in enabled:
                if rank.get(pattern.get("confidence", "medium"), 1) < threshold:
                    continue
                try:
                    regex = re.compile(pattern["regex"], re.MULTILINE)
                except re.error:
                    continue
                for match in regex.finditer(text):
                    sample = match.group(0)
                    findings.append({
                        "file": rel,
                        "rule": pattern["id"],
                        "label": pattern.get("label", pattern["id"]),
                        "severity": pattern.get("severity", "medium"),
                        "confidence": pattern.get("confidence", "medium"),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "preview": _redact(sample),
                    })
                    if len(findings) >= cap:
                        return findings
    return findings


def _redact(value: str) -> str:
    if len(value) <= 8:
        return value[:2] + "*" * max(0, len(value) - 2)
    return f"{value[:4]}…{value[-2:]} ({len(value)} chars)"


def scan_mismatches(root: str, config, limit: int = 500) -> list[dict]:
    """Flag files whose signature contradicts their extension (disguised content)."""
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if len(out) >= limit:
                return out
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                info = F.detect_file(path)
            except PecError:
                continue
            if info["id"] == "unknown":
                continue
            ext = info["extension"]
            expected = F.MAGIC_EXT_HINT.get(info["id"], [])
            if ext and ext not in expected and not (
                info.get("zip_family") and info["id"].startswith("zip")):
                out.append({"path": rel, "detected": info["id"], "extension": ext,
                            "kind": info["kind"], "notes": info.get("notes", "")})
    return out


def sanitize_metadata(root: str, config, drop_junk: bool = True, epoch: str | None = None) -> dict:
    removed: list[str] = []
    touched = 0
    junk = config.get("policy.metadata_junk", []) or []
    stamp = None
    if epoch:
        try:
            stamp = datetime.strptime(epoch, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        except ValueError as exc:
            raise PecError(f"--epoch must be YYYY-MM-DD: {exc}", EXIT_USAGE) from exc
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if name in {"pec-manifest.json", "pec-manifest.md"}:
                continue
            if drop_junk and any(j.rstrip("/") in rel.split("/") or j in rel for j in junk):
                try:
                    os.unlink(path)
                    removed.append(rel)
                    continue
                except OSError:
                    pass
            if stamp is not None:
                try:
                    os.utime(path, (stamp, stamp))
                    touched += 1
                except OSError:
                    pass
        if drop_junk:
            for name in list(dirnames):
                full = os.path.join(dirpath, name)
                if any(j.rstrip("/") == name or j.rstrip("/").endswith(name) for j in junk if "/" in j):
                    if not os.listdir(full):
                        try:
                            os.rmdir(full)
                        except OSError:
                            pass
    return {"root": os.path.abspath(root), "removed": removed, "removed_count": len(removed),
            "timestamps_set": touched, "epoch": epoch}
