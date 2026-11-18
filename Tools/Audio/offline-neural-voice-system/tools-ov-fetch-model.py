#!/usr/bin/env python3
"""Acquire the bundled Piper voice model (one-time, optional, network-gated).

The 63 MB voice model is NOT inside the ZIP (delivery-size constraint). Everything
else — code, engine wheels, docs, checksums — is fully offline.

Deterministic acquisition (pinned, verified):
  source URL : https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
  size       : 63201294 bytes
  sha256     : 5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f
  license    : repository rhasspy/piper-voices tagged license:mit (see dependencies-notices.md)

Air-gapped install (no network at all): copy the model file to this machine, then
  python3 tools-ov-fetch-model.py --from-file /path/to/en_US-lessac-medium.onnx --yes

Network install (one-time, ~63 MB download):
  python3 tools-ov-fetch-model.py --yes

Both modes verify size + SHA-256 before installing. Without --yes the network
is never touched; an interactive terminal is asked for confirmation instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
DEST = SKILL_ROOT / "asset-voice-lessac-medium.onnx"
MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
MODEL_SIZE = 63201294
MODEL_SHA256 = "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def existing_status() -> str:
    if not DEST.is_file():
        return "missing"
    if DEST.stat().st_size == MODEL_SIZE and sha256_file(DEST) == MODEL_SHA256:
        return "valid"
    return "corrupt"


def install_from(source_stream, total, workdir: Path, label: str = "downloading") -> int:
    tmp = workdir / (DEST.name + ".part")
    try:
        with open(tmp, "wb") as fh:
            done = 0
            while True:
                block = source_stream.read(CHUNK) if hasattr(source_stream, "read") else source_stream.read(CHUNK)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {label}... {pct:3d}% ({done // (1 << 20)} MiB)", end="", file=sys.stderr)
        if total:
            print("", file=sys.stderr)
        actual_size = tmp.stat().st_size
        actual_sha = sha256_file(tmp)
        if actual_size != MODEL_SIZE:
            print(f"FATAL: size mismatch: expected {MODEL_SIZE}, got {actual_size}", file=sys.stderr)
            tmp.unlink()
            return 8
        if actual_sha != MODEL_SHA256:
            print(f"FATAL: sha256 mismatch: expected {MODEL_SHA256}, got {actual_sha}", file=sys.stderr)
            tmp.unlink()
            return 8
        os.replace(tmp, DEST)
        print(json.dumps({"ok": True, "model": str(DEST), "sha256": actual_sha,
                          "size": actual_size, "source": "verified"}))
        return 0
    except Exception as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="approve the one-time download/install (network) or local copy")
    parser.add_argument("--from-file", metavar="PATH", help="install from a local file instead of the network (air-gapped use)")
    parser.add_argument("--force", action="store_true", help="reinstall even if an identical model is already present")
    parser.add_argument("--check", action="store_true", help="only report model status; no network, no writes")
    args = parser.parse_args()

    status = existing_status()
    if args.check:
        print(json.dumps({"status": status, "path": str(DEST), "expected_sha256": MODEL_SHA256}))
        return 0 if status == "valid" else 8
    if status == "valid" and not args.force:
        print(json.dumps({"ok": True, "model": str(DEST), "status": "already-valid",
                          "note": "use --force to reinstall"}))
        return 0

    if not args.yes:
        if sys.stdin.isatty():
            prompt = ("Download 63 MB from huggingface.co now? [y/N] " if not args.from_file
                      else f"Install {args.from_file} as the voice model? [y/N] ")
            if input(prompt).strip().lower() not in ("y", "yes"):
                print("Aborted; nothing was downloaded or written.", file=sys.stderr)
                return 4
        else:
            print("FATAL: refusing to use the network without approval; re-run with --yes (or --from-file for air-gapped install).", file=sys.stderr)
            return 4

    if args.from_file:
        src = Path(args.from_file).expanduser()
        if not src.is_file():
            print(f"FATAL: file not found: {src}", file=sys.stderr)
            return 5
        with open(src, "rb") as fh:
            return install_from(fh, src.stat().st_size, SKILL_ROOT, label="installing")

    print(f"downloading {MODEL_URL}", file=sys.stderr)
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=600) as resp:  # noqa: S310 (fixed pinned https URL)
            return install_from(resp, MODEL_SIZE, SKILL_ROOT, label="downloading")
    except Exception as exc:
        print(f"FATAL: download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Air-gapped alternative: copy the model manually and use --from-file.", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
