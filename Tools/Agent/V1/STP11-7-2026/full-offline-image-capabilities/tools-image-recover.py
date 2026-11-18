#!/usr/bin/env python3
"""Recovery, repair and cleanup for offline image workflows.

Role: salvage truncated or partly corrupt PNG files by recovering every
complete scanline and re-writing a valid container, rebuild integrity
manifests after repair, restore quarantined files behind an approval gate,
and clean bounded tool temp artifacts.

Recovery is non-destructive: the damaged file is never modified in place.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_spec = importlib.util.spec_from_file_location("foic_core", os.path.join(_HERE, "tools-image-core.py"))
core = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("foic_core", core)
_spec.loader.exec_module(core)

_spec2 = importlib.util.spec_from_file_location("foic_security", os.path.join(_HERE, "tools-image-security.py"))
security = importlib.util.module_from_spec(_spec2)
sys.modules.setdefault("foic_security", security)
_spec2.loader.exec_module(security)

ToolError = core.ToolError
LOG = core.LOG

TEMP_PATTERNS = [re.compile(r"\.foic-tmp$"), re.compile(r"^foic-tmp-"), re.compile(r"^\.foic-cache$")]


def salvage_png(src, dst, cfg, overwrite=False, pad=True, deadline=None):
    """Recover every complete scanline from a damaged PNG and re-encode it."""
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        raise ToolError("not a file: %s" % src, code="not-found")
    with open(src, "rb") as fh:
        data = fh.read()
    if core.sniff_format(data) != "PNG":
        raise ToolError(
            "salvage currently supports PNG only (detected %s)" % core.sniff_format(data),
            code="unsupported-image",
        )
    try:
        img = core.decode_png_bytes(data, want_pixels=True, allow_partial=True)
    except core.UnsupportedImage as exc:
        raise ToolError("cannot salvage %s: %s" % (src, exc), code="unrecoverable")

    expected_h = None
    for ctype, payload, _whole in core._png_chunks(data):
        if ctype == b"IHDR" and len(payload) >= 13:
            import struct

            expected_h = struct.unpack(">II", payload[:8])[1]
            break
    rows = img.height
    padded = 0
    pixels = img.pixels
    if expected_h and rows < expected_h and pad:
        stride = img.width * img.channels
        padded = expected_h - rows
        pixels = pixels + bytes(stride * padded)
        rows = expected_h
    if deadline:
        deadline.check("salvaging PNG")
    core.save_image(dst, core.ImageData(img.width, rows, img.mode, pixels, "PNG", img.backend),
                    cfg, fmt="png", overwrite=overwrite)
    recovered_ratio = (rows - padded) / float(expected_h or rows)
    return {
        "schema": "foic.recover/1",
        "source": src,
        "output": os.path.abspath(dst),
        "mode": "png-salvage",
        "expected_height": expected_h,
        "rows_recovered": (rows - padded),
        "rows_padded": padded,
        "width": img.width,
        "output_height": rows,
        "recovery_ratio": round(recovered_ratio, 4),
        "status": "full" if padded == 0 and not img.truncated else "partial",
        "sha256": core.sha256_file(dst)[0],
        "bytes": os.path.getsize(dst),
        "note": "padded rows are black and mark unrecoverable data" if padded else
                "all rows recovered; container rebuilt and CRC-validated",
    }


def rebuild_manifest(root, out_path, cfg, overwrite=False, deadline=None):
    manifest = security.build_manifest(root, cfg, deadline=deadline)
    core.write_json(out_path, manifest, overwrite=overwrite)
    return {
        "schema": "foic.recover-manifest/1",
        "output": os.path.abspath(out_path),
        "root": os.path.abspath(root),
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "sha256": core.sha256_file(out_path)[0],
    }


def restore(record_path, cfg, approved=False):
    return security.restore_quarantine(record_path, cfg, approved=approved)


def cleanup(dir_path, cfg, approve=False, older_than_hours=0, dry_run=False, deadline=None):
    """Delete only tool-generated temp artifacts inside dir_path."""
    if approve:
        core.require_approval("delete", cfg, True, "removing tool temp files under %s" % dir_path)
    else:
        dry_run = True  # without --approve the run is always a dry run
    root = os.path.abspath(dir_path)
    if not os.path.isdir(root):
        raise ToolError("not a directory: %s" % root, code="not-found")
    cutoff = time.time() - (float(older_than_hours) * 3600.0) if older_than_hours else None
    removed = []
    kept = []
    bytes_freed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if deadline:
                deadline.check("cleaning")
            if any(p.search(name) for p in TEMP_PATTERNS):
                try:
                    stat = os.stat(path)
                    if cutoff and stat.st_mtime > cutoff:
                        kept.append({"path": path, "reason": "newer than cutoff"})
                        continue
                    if dry_run:
                        removed.append({"path": path, "bytes": stat.st_size, "dry_run": True})
                        bytes_freed += stat.st_size
                    else:
                        os.remove(path)
                        removed.append({"path": path, "bytes": stat.st_size})
                        bytes_freed += stat.st_size
                except OSError as exc:
                    kept.append({"path": path, "reason": str(exc)})
            else:
                kept.append({"path": path, "reason": "not a tool temp artifact"})
        for name in dirnames:
            path = os.path.join(dirpath, name)
            if any(p.search(name) for p in TEMP_PATTERNS):
                try:
                    if not os.listdir(path):
                        if dry_run:
                            removed.append({"path": path, "bytes": 0, "dry_run": True, "type": "dir"})
                        else:
                            os.rmdir(path)
                            removed.append({"path": path, "bytes": 0, "type": "dir"})
                except OSError:
                    pass
    return {
        "schema": "foic.cleanup/1",
        "root": root,
        "dry_run": bool(dry_run),
        "removed": removed,
        "removed_count": len(removed),
        "kept_count": len(kept),
        "bytes_freed": bytes_freed,
        "kept_sample": kept[:20],
        "policy": "only files matching tool temp patterns (*.foic-tmp, foic-tmp-*, .foic-cache) are removed",
    }
