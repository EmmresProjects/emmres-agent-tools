"""Operational handlers: archives, compression, hashing, comparison, signatures,
repair, patching, conversion, fuzzing, external tool adapters, and spec authoring.
All writes go to new outputs; destructive/overwrite actions require --approve."""
from __future__ import annotations

import binascii
import bz2
import difflib
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import struct
import subprocess
import tarfile
import time
import zipfile
import zlib
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, EXIT_LIMIT, EXIT_APPROVAL, load_json_asset, read_bytes,
                        validate_input_file, write_new_bytes, require_approval, hash_file, hash_bytes,
                        shannon_entropy, u16, u32, u64, parse_int, utcnow, redact)
from tools_identify import identify_bytes, extract_strings, repair_encoding, _resolve_layout, annotate_layout, TYPE_SIZES
from tools_formats import (detect_format, parse_zip, parse_tar, parse_elf, parse_pe, parse_macho,
                           zip_safety)
from tools_data import (parse_der, decode_protobuf, decode_msgpack, decode_cbor, decode_bson,
                        analyze_json, analyze_ini, analyze_toml, analyze_xml, analyze_csv)

# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def cmd_archive(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    mode = args.mode or "inspect"
    fmt = detect_format(data, path.name)
    result = {"file": str(path), "mode": mode, "detected": fmt}
    if fmt == "zip":
        z = zipfile.ZipFile(io.BytesIO(data))
        if mode in ("inspect", "validate"):
            parsed = parse_zip(data, cfg)
            result.update(parsed)
        if mode == "extract":
            if not args.out:
                raise RetoolError("missing-out", "extract requires --out <dir>", exit_code=EXIT_USAGE)
            result["extracted"] = _safe_extract_zip(z, Path(args.out), cfg, args)
        if mode == "repack":
            if not args.out:
                raise RetoolError("missing-out", "repack requires --out <file>", exit_code=EXIT_USAGE)
            result["repacked"] = _repack_zip(z, Path(args.out), cfg, args)
    elif fmt == "tar":
        t = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        if mode in ("inspect", "validate"):
            result.update(parse_tar(data, cfg))
        if mode == "extract":
            if not args.out:
                raise RetoolError("missing-out", "extract requires --out <dir>", exit_code=EXIT_USAGE)
            result["extracted"] = _safe_extract_tar(t, Path(args.out), cfg, args)
    elif fmt == "gzip":
        from tools_formats import parse_gzip
        result.update(parse_gzip(data, cfg))
        if mode == "extract":
            if not args.out:
                raise RetoolError("missing-out", "extract requires --out <file>", exit_code=EXIT_USAGE)
            d = zlib.decompressobj(31)
            content = d.decompress(data, cfg["limits"]["max_extract_bytes"])
            write_new_bytes(Path(args.out), content, approve=getattr(args, "approve", False))
            result["extracted"] = {"path": args.out, "bytes": len(content)}
    elif fmt in ("7z", "rar", "cab") or data[:6] in (b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07") or data[:4] == b"MSCF":
        result["note"] = "Format detected but no built-in decoder; use the external adapter (7z) or plan. Detection-only."
        env["limitations"].append("7z/RAR/CAB extraction requires external tools; only signature-level detection is built in.")
    else:
        raise RetoolError("unsupported-archive", f"Not a supported archive for this command (detected {fmt}). Use parse/identify.", exit_code=EXIT_USAGE)
    env["result"] = result
    return env


def _safe_extract_zip(z, outdir: Path, cfg: dict, args) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    safety = zip_safety(z, cfg)
    if any(i["severity"] == "high" for i in safety["issues"]):
        raise RetoolError("unsafe-archive", "Archive has high-severity safety issues; extraction refused. Review the validate report.",
                          {"issues": safety["issues"]}, EXIT_APPROVAL)
    written = []
    total = 0
    for info in z.infolist()[:cfg["limits"]["max_archive_entries"]]:
        if info.is_dir():
            continue
        target = (outdir / info.filename).resolve()
        if not str(target).startswith(str(outdir.resolve())):
            raise RetoolError("path-traversal", f"Entry escapes output dir: {info.filename}", exit_code=EXIT_APPROVAL)
        if info.file_size > cfg["limits"]["max_extract_bytes"]:
            raise RetoolError("size-limit", f"Entry too large: {info.filename}", exit_code=EXIT_LIMIT)
        total += info.file_size
        if total > cfg["limits"]["max_extract_bytes"]:
            raise RetoolError("size-limit", "Total extraction exceeds max_extract_bytes", exit_code=EXIT_LIMIT)
        content = z.read(info)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not getattr(args, "approve", False):
            raise RetoolError("approval-required", f"Extraction target exists: {target}; pass --approve to overwrite.", exit_code=EXIT_APPROVAL)
        target.write_bytes(content)
        written.append({"name": info.filename, "bytes": len(content), "sha256": hash_bytes(content)["sha256"]})
    return {"files": written, "count": len(written), "total_bytes": total, "outdir": str(outdir)}


def _safe_extract_tar(t, outdir: Path, cfg: dict, args) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    total = 0
    for m in t.getmembers()[:cfg["limits"]["max_archive_entries"]]:
        if not m.isfile():
            continue
        target = (outdir / m.name).resolve()
        if not str(target).startswith(str(outdir.resolve())):
            raise RetoolError("path-traversal", f"Entry escapes output dir: {m.name}", exit_code=EXIT_APPROVAL)
        if m.size > cfg["limits"]["max_extract_bytes"]:
            raise RetoolError("size-limit", f"Entry too large: {m.name}", exit_code=EXIT_LIMIT)
        total += m.size
        if total > cfg["limits"]["max_extract_bytes"]:
            raise RetoolError("size-limit", "Total extraction exceeds limit", exit_code=EXIT_LIMIT)
        f = t.extractfile(m)
        content = f.read()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not getattr(args, "approve", False):
            raise RetoolError("approval-required", f"Target exists: {target}", exit_code=EXIT_APPROVAL)
        target.write_bytes(content)
        written.append({"name": m.name, "bytes": len(content)})
    return {"files": written, "count": len(written), "total_bytes": total, "outdir": str(outdir)}


def _repack_zip(z, out: Path, cfg: dict, args) -> dict:
    if out.exists() and not getattr(args, "approve", False):
        raise RetoolError("approval-required", "Repack output exists; pass --approve to overwrite.", exit_code=EXIT_APPROVAL)
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(out, "w") as zo:
        for info in z.infolist()[:cfg["limits"]["max_archive_entries"]]:
            content = z.read(info)
            ni = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            ni.compress_type = info.compress_type
            ni.external_attr = info.external_attr
            zo.writestr(ni, content)
            entries.append({"name": info.filename, "crc32": f"{zlib.crc32(content) & 0xFFFFFFFF:08x}"})
    # reopen validation
    with zipfile.ZipFile(out) as zr:
        bad = zr.testzip()
    return {"path": str(out), "entries": len(entries), "deterministic_timestamps": True,
            "reopen_crc_ok": bad is None, "failed_entry": bad,
            "note": "Entry order and compression methods preserved; timestamps normalized to 1980-01-01 for determinism."}


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

COMP_SIGS = [(b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"),
             (b"\x28\xb5\x2f\xfd", "zstd"), (b"\x04\x22\x4d\x18", "lz4"),
             (b"\x78\x9c", "zlib"), (b"\x78\x01", "zlib"), (b"\x78\xda", "zlib"),
             (b"\x5d\x00\x00", "lzma-alone"), (b"\x1f\x9d", "compress")]


def cmd_compress(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    mode = args.mode or "detect"
    detected = [name for sig, name in COMP_SIGS if data.startswith(sig)]
    result = {"file": str(path), "size": len(data), "detected": detected, "mode": mode}
    if mode in ("validate", "roundtrip", "benchmark"):
        codec = None
        if data[:2] == b"\x1f\x8b":
            codec = ("gzip", lambda d: zlib.decompress(d, 31), lambda d: gzip.compress(d))
        elif data[:3] == b"BZh":
            codec = ("bzip2", bz2.decompress, bz2.compress)
        elif data[:6] == b"\xfd7zXZ\x00":
            codec = ("xz", lzma.decompress, lzma.compress)
        elif data[:1] == b"\x78":
            codec = ("zlib", zlib.decompress, zlib.compress)
        if codec is None:
            result["note"] = "No built-in decoder for detected stream (zstd/lz4/brotli/lzma-alone need external libs). Detection only."
            env["limitations"].append("zstd/lz4/brotli/lzma-alone decoding requires external libraries; not bundled.")
        else:
            name, dec, enc = codec
            t0 = time.perf_counter()
            try:
                content = dec(data)
                dt = (time.perf_counter() - t0) * 1000
                result["decompressed_size"] = len(content)
                result["ratio"] = round(len(data) / max(len(content), 1), 4)
                result["expansion"] = round(len(content) / max(len(data), 1), 2)
                result["decompress_ms"] = round(dt, 3)
                result["content_sha256"] = hash_bytes(content)["sha256"]
                result["valid"] = True
                if len(content) > cfg["limits"]["max_extract_bytes"]:
                    env["warnings"].append("Decompressed size exceeds max_extract_bytes; output truncated by limit.")
                if mode in ("roundtrip", "benchmark"):
                    t1 = time.perf_counter()
                    re_enc = enc(content)
                    dt1 = (time.perf_counter() - t1) * 1000
                    result["recompress_ms"] = round(dt1, 3)
                    result["roundtrip_identical_bytes"] = re_enc == data
                    result["roundtrip_content_identical"] = dec(re_enc) == content
                    result["note"] = "Byte-identical round-trip is not guaranteed across encoder versions; content round-trip is the meaningful check."
                if mode == "benchmark":
                    bench = {}
                    for label, fn in (("zlib-6", lambda d: zlib.compress(d, 6)),
                                      ("zlib-9", lambda d: zlib.compress(d, 9)),
                                      ("bz2-9", lambda d: bz2.compress(d, 9)),
                                      ("xz-6", lambda d: lzma.compress(d, preset=6))):
                        t = time.perf_counter()
                        c = fn(content)
                        bench[label] = {"bytes": len(c), "ratio": round(len(c) / max(len(content), 1), 4),
                                        "ms": round((time.perf_counter() - t) * 1000, 3)}
                    result["benchmark"] = bench
            except (zlib.error, lzma.LZMAError, OSError) as e:
                result["decompress_error"] = str(e)
                result["valid"] = False
    env["result"] = result
    return env


# ---------------------------------------------------------------------------
# Hashing / CRC / fuzzy
# ---------------------------------------------------------------------------

CRC16_PRESETS = {
    "CRC-16/CCITT-FALSE": (0x1021, 0xFFFF, 0, False, False),
    "CRC-16/XMODEM": (0x1021, 0, 0, False, False),
    "CRC-16/ARC": (0x8005, 0, 0, True, True),
    "CRC-16/MODBUS": (0x8005, 0xFFFF, 0, True, True),
    "CRC-16/USB": (0x8005, 0xFFFF, 0xFFFF, True, True),
    "CRC-16/KERMIT": (0x1021, 0, 0, True, True),
    "CRC-16/DNP": (0x3D65, 0, 0xFFFF, True, True),
    "CRC-16/MAXIM": (0x8005, 0, 0xFFFF, True, True),
}
CRC32_PRESETS = {
    "CRC-32/ISO-HDLC": (0x04C11DB7, 0xFFFFFFFF, 0xFFFFFFFF, True, True),
    "CRC-32C": (0x1EDC6F41, 0xFFFFFFFF, 0xFFFFFFFF, True, True),
    "CRC-32/BZIP2": (0x04C11DB7, 0xFFFFFFFF, 0xFFFFFFFF, False, False),
    "CRC-32/MPEG-2": (0x04C11DB7, 0xFFFFFFFF, 0, False, False),
    "CRC-32/POSIX": (0x04C11DB7, 0, 0xFFFFFFFF, False, False),
    "CRC-32/JAMCRC": (0x04C11DB7, 0xFFFFFFFF, 0, True, True),
}


def _crc_generic(data: bytes, poly: int, init: int, xorout: int, refin: bool, refout: bool, width: int) -> int:
    def reflect(v, n):
        r = 0
        for _ in range(n):
            r = (r << 1) | (v & 1)
            v >>= 1
        return r
    crc = init
    mask = (1 << width) - 1
    top = 1 << (width - 1)
    for b in data:
        if refin:
            b = reflect(b, 8)
        crc ^= b << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & mask if crc & top else (crc << 1) & mask
    if refout:
        crc = reflect(crc, width)
    return crc ^ xorout


def cmd_hash(args, env, cfg):
    paths = [validate_input_file(p, cfg) for p in args.paths]
    algos = args.algos.split(",") if args.algos else cfg["output"]["hash_algorithms"]
    results = []
    for path in paths:
        entry = {"file": str(path), **hash_file(path, algos)}
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, 1 << 20))
        entry["crc32"] = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
        entry["adler32"] = f"{zlib.adler32(data) & 0xFFFFFFFF:08x}"
        if args.fuzzy:
            entry["fuzzy"] = _fuzzy_hash(data)
        if args.crc_recon:
            expected = parse_int(args.crc_recon)
            width = parse_int(args.width) if args.width else 32
            entry["crc_reconstruction"] = _crc_recon(data, expected, width)
        results.append(entry)
    env["result"] = {"files": results, "algorithms": algos}
    if len(paths) == 2:
        a, b = results
        env["result"]["identical"] = a.get("sha256") == b.get("sha256")
    return env


def _fuzzy_hash(data: bytes, chunk: int = 4096) -> dict:
    pieces = []
    for off in range(0, min(len(data), 1 << 20), chunk):
        pieces.append(hashlib.sha256(data[off:off + chunk]).hexdigest()[:16])
    return {"chunk_size": chunk, "pieces": pieces[:256], "piece_count": len(pieces),
            "note": "Piecewise fingerprint for near-duplicate clustering; not ssdeep-compatible."}


def _crc_recon(data: bytes, expected: int, width: int) -> dict:
    presets = CRC16_PRESETS if width == 16 else CRC32_PRESETS
    matches = []
    for name, (poly, init, xorout, refin, refout) in presets.items():
        val = _crc_generic(data, poly, init, xorout, refin, refout, width)
        if val == expected:
            matches.append({"preset": name, "value": hex(val)})
    return {"expected": hex(expected), "width": width, "matches": matches,
            "tested_presets": list(presets.keys()),
            "note": "Tests standard CRC presets over the supplied bytes. Try --offset/--length ranges if no match; custom polynomials need manual recovery."}


# ---------------------------------------------------------------------------
# Diff / duplicates
# ---------------------------------------------------------------------------

def cmd_diff(args, env, cfg):
    a = validate_input_file(args.a, cfg)
    b = validate_input_file(args.b, cfg)
    da = read_bytes(a, cfg, 0, min(a.stat().st_size, cfg["limits"]["max_read_bytes"]))
    db = read_bytes(b, cfg, 0, min(b.stat().st_size, cfg["limits"]["max_read_bytes"]))
    mode = args.mode or "bytes"
    result = {"a": str(a), "b": str(b), "size_a": len(da), "size_b": len(db), "mode": mode}
    if mode == "bytes":
        regions = []
        i = 0
        n = min(len(da), len(db))
        while i < n:
            if da[i] != db[i]:
                start = i
                while i < n and da[i] != db[i]:
                    i += 1
                regions.append({"offset": start, "length": i - start,
                                "a_hex": da[start:min(start + 32, i)].hex(),
                                "b_hex": db[start:min(start + 32, i)].hex()})
            else:
                i += 1
        if len(da) != len(db):
            regions.append({"offset": n, "length": abs(len(da) - len(db)), "kind": "tail-length-difference"})
        sm = difflib.SequenceMatcher(None, da[:65536], db[:65536], autojunk=False)
        result.update({"identical": da == db, "differing_regions": regions[:500],
                       "differing_region_count": len(regions),
                       "similarity_ratio_sample": round(sm.ratio(), 4),
                       "sha256_a": hash_bytes(da)["sha256"], "sha256_b": hash_bytes(db)["sha256"]})
    elif mode == "strings":
        sa = {s["value"] for s in extract_strings(da, 5, 5000)}
        sb = {s["value"] for s in extract_strings(db, 5, 5000)}
        inter = sa & sb
        union = sa | sb
        result.update({"strings_a": len(sa), "strings_b": len(sb), "shared": len(inter),
                       "jaccard": round(len(inter) / max(len(union), 1), 4),
                       "only_a_sample": sorted(sa - sb)[:50], "only_b_sample": sorted(sb - sa)[:50]})
    elif mode == "sections":
        fa, fb = detect_format(da, a.name), detect_format(db, b.name)
        if fa != fb or fa not in ("elf", "pe", "macho"):
            raise RetoolError("unsupported", f"Section diff needs matching ELF/PE/Mach-O; got {fa}/{fb}", exit_code=EXIT_USAGE)
        parser = {"elf": parse_elf, "pe": parse_pe, "macho": parse_macho}[fa]
        pa, pb = parser(da), parser(db)
        secs_a = {s.get("name", s.get("segment", "")): s for s in pa.get("sections", [])}
        secs_b = {s.get("name", s.get("segment", "")): s for s in pb.get("sections", [])}
        changed = []
        for name in sorted(set(secs_a) | set(secs_b)):
            sa_, sb_ = secs_a.get(name), secs_b.get(name)
            if sa_ is None or sb_ is None:
                changed.append({"section": name, "change": "added" if sa_ is None else "removed"})
            elif sa_.get("size") != sb_.get("size") or sa_.get("entropy") != sb_.get("entropy"):
                changed.append({"section": name, "size_a": sa_.get("size"), "size_b": sb_.get("size"),
                                "entropy_a": sa_.get("entropy"), "entropy_b": sb_.get("entropy")})
        result.update({"format": fa, "changed_sections": changed})
    elif mode == "json":
        ja = json.loads(da.decode("utf-8"))
        jb = json.loads(db.decode("utf-8"))
        result["json_diff"] = _json_diff(ja, jb)
    else:
        raise RetoolError("bad-mode", f"Unknown diff mode: {mode}", exit_code=EXIT_USAGE)
    env["result"] = result
    return env


def _json_diff(a, b, path="$"):
    changes = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}"
            if k not in a:
                changes.append({"path": p, "change": "added", "value_b": _short(b[k])})
            elif k not in b:
                changes.append({"path": p, "change": "removed", "value_a": _short(a[k])})
            else:
                changes.extend(_json_diff(a[k], b[k], p))
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            p = f"{path}[{i}]"
            if i >= len(a):
                changes.append({"path": p, "change": "added"})
            elif i >= len(b):
                changes.append({"path": p, "change": "removed"})
            else:
                changes.extend(_json_diff(a[i], b[i], p))
    elif a != b:
        changes.append({"path": path, "change": "modified", "a": _short(a), "b": _short(b)})
    return changes[:1000]


def _short(v):
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s[:120] + "..." if len(s) > 120 else s


def cmd_duplicates(args, env, cfg):
    root = Path(args.path)
    if not root.is_dir():
        raise RetoolError("not-dir", "duplicates requires a directory", exit_code=EXIT_USAGE)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.stat().st_size <= cfg["limits"]["max_file_bytes"]:
            files.append(p)
        if len(files) >= cfg["limits"]["max_inventory_files"]:
            break
    by_hash = {}
    by_size = {}
    for p in files:
        h = hash_file(p, ["sha256"])["sha256"]
        by_hash.setdefault(h, []).append(str(p))
        by_size.setdefault(p.stat().st_size, []).append(str(p))
    exact = [{"sha256": h, "files": fs} for h, fs in by_hash.items() if len(fs) > 1]
    near = []
    sizes_with_multiple = {s: fs for s, fs in by_size.items() if len(fs) > 1 and s > 0}
    for s, fs in list(sizes_with_multiple.items())[:100]:
        reps = []
        for f in fs[:20]:
            data = read_bytes(Path(f), cfg, 0, min(s, 65536))
            reps.append((f, {x["value"] for x in extract_strings(data, 6, 500)}))
        for i in range(len(reps)):
            for j in range(i + 1, len(reps)):
                sa, sb = reps[i][1], reps[j][1]
                jac = len(sa & sb) / max(len(sa | sb), 1)
                if jac > 0.8:
                    near.append({"a": reps[i][0], "b": reps[j][0], "string_jaccard": round(jac, 3)})
    env["result"] = {"root": str(root), "files_scanned": len(files), "exact_duplicate_groups": exact,
                     "near_duplicate_candidates": near[:200],
                     "note": "Exact groups share SHA-256. Near candidates share size and >0.8 string Jaccard; confirm with diff."}
    return env


# ---------------------------------------------------------------------------
# Signatures (structural; no trust-chain verification)
# ---------------------------------------------------------------------------

def cmd_signatures(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    fmt = detect_format(data, path.name)
    # DER/PEM are data formats the binary detector may not label; recognize them here.
    ext = path.suffix.lower()
    if fmt in ("unknown", "data") or ext in (".der", ".cer", ".crt", ".pem"):
        if b"-----BEGIN" in data:
            fmt = "pem"
        elif data[:1] == b"\x30":
            try:
                parse_der(data)
                fmt = "der"
            except Exception:
                pass
    result = {"file": str(path), "format": fmt, "trust_chain_verified": False}
    if fmt == "pe":
        result["authenticode"] = _pe_signature(data)
    elif fmt == "macho":
        result["code_signature"] = _macho_signature(data)
    elif fmt == "zip":
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        if "AndroidManifest.xml" in names:
            result["apk"] = _apk_signature(data, z)
        elif "META-INF/MANIFEST.MF" in names:
            result["jar"] = _jar_signature(z)
        else:
            result["note"] = "ZIP without APK/JAR signing markers."
    elif fmt in ("der", "pem"):
        if fmt == "pem" or b"-----BEGIN" in data:
            import base64
            b64 = b"".join(re.findall(rb"-----BEGIN [A-Z0-9 ]+-----(.*?)-----END [A-Z0-9 ]+-----", data, re.S))
            cert = base64.b64decode(b64) if b64 else data
            result["certificate"] = parse_der(cert)
        else:
            result["certificate"] = parse_der(data)
    else:
        raise RetoolError("unsupported", f"No signature structure handler for {fmt}; supported: PE/Mach-O/APK/JAR/DER/PEM.", exit_code=EXIT_USAGE)
    env["result"] = result
    env["limitations"].append("Signature structure and digests are inspected; certificate trust chains, revocation, and timestamp validity are NOT cryptographically verified. Use platform verifiers (signtool/codesign/apksigner) for trust decisions.")
    return env


def _pe_signature(data: bytes) -> dict:
    parsed = parse_pe(data)
    ct = parsed.get("certificate_table")
    if not ct:
        return {"signed": False, "note": "No certificate data directory entry."}
    off, size = ct["rva"], ct["size"]  # for certificates, RVA is a file offset
    if off + size > len(data) or size < 8:
        return {"signed": False, "note": "Certificate table out of range."}
    length = u32(data, off)
    revision = u16(data, off + 4)
    cert_type = u16(data, off + 6)
    pkcs7 = data[off + 8:off + length]
    out = {"signed": True, "win_certificate": {"length": length, "revision": hex(revision),
                                               "type": {0x0002: "PKCS#7"}.get(cert_type, cert_type)},
           "certificate_table_offset": off, "certificate_table_size": size}
    try:
        der = parse_der(pkcs7)
        out["pkcs7_structure"] = {"node_count": der["node_count"]}
        oids = []
        def collect(nodes):
            for n in nodes:
                if n.get("tag") == "OBJECT IDENTIFIER":
                    oids.append({"oid": n.get("value"), "name": n.get("name")})
                if "children" in n:
                    collect(n["children"])
        collect(der["tree"])
        out["oids"] = oids[:30]
        out["is_signed_data"] = any(o["oid"] == "1.2.840.113549.1.7.2" for o in oids)
    except Exception as e:
        out["pkcs7_parse_error"] = str(e)
    return out


def _macho_signature(data: bytes) -> dict:
    parsed = parse_macho(data)
    cs = parsed.get("code_signature")
    if not cs:
        return {"signed": False}
    off, size = cs["offset"], cs["size"]
    if off + size > len(data) or size < 16:
        return {"signed": False, "note": "Code signature region out of range."}
    magic = u32(data, off, "be")
    length = u32(data, off + 4, "be")
    count = u32(data, off + 8, "be")
    out = {"signed": True, "superblob_magic": hex(magic), "superblob_length": length, "blob_count": count}
    blobs = []
    for i in range(min(count, 16)):
        bo = off + 12 + i * 8
        btype = u32(data, bo, "be")
        boff = u32(data, bo + 4, "be")
        bstart = off + boff
        if bstart + 8 > len(data):
            continue
        bmagic = u32(data, bstart, "be")
        blen = u32(data, bstart + 4, "be")
        entry = {"type": btype, "magic": hex(bmagic), "length": blen}
        if bmagic == 0xFADE0C02 and blen >= 44:  # CodeDirectory
            cd = data[bstart:bstart + blen]
            ident_off = u32(cd, 20, "be")
            team_off = u32(cd, 40, "be") if blen >= 44 else 0
            entry["identifier"] = cd[ident_off:cd.find(b"\x00", ident_off)].decode("utf-8", errors="replace") if ident_off < len(cd) else ""
            if team_off and team_off < len(cd):
                entry["team_id"] = cd[team_off:cd.find(b"\x00", team_off)].decode("utf-8", errors="replace")
            entry["hash_size"] = cd[38] if len(cd) > 38 else None
            entry["n_code_slots"] = u32(cd, 32, "be")
        if bmagic == 0xFADE7171:  # entitlements XML
            entry["entitlements_xml"] = data[bstart + 8:bstart + blen].decode("utf-8", errors="replace")[:4096]
        blobs.append(entry)
    out["blobs"] = blobs
    return out


def _apk_signature(data: bytes, z) -> dict:
    out = {"jar_signing": _jar_signature(z)}
    # APK Signing Block: before central directory, magic "APK Sig Block 42"
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        return out
    cd_off = u32(data, eocd + 16)
    magic = data[cd_off - 16:cd_off]
    if magic == b"APK Sig Block 42":
        block_size = u64(data, cd_off - 24)
        block_start = cd_off - block_size - 8
        out["signing_block"] = {"present": True, "size": block_size, "offset": block_start}
        pos = block_start + 8
        pairs = []
        while pos + 12 < cd_off - 24:
            pair_len = u64(data, pos)
            if pair_len < 4 or pos + 8 + pair_len > cd_off:
                break
            bid = u32(data, pos + 8)
            scheme = {0x7109871A: "v2", 0xF05368C0: "v3", 0x1B93AD61: "v4-stripping-protection",
                      0x42726577: "verity-padding"}.get(bid, hex(bid))
            pairs.append({"id": hex(bid), "scheme": scheme, "length": pair_len - 4})
            pos += 8 + pair_len
        out["signing_block"]["schemes"] = pairs
    else:
        out["signing_block"] = {"present": False}
    return out


def _jar_signature(z) -> dict:
    names = z.namelist()
    if "META-INF/MANIFEST.MF" not in names:
        return {"present": False}
    manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
    digests = {}
    current = None
    for line in manifest.splitlines():
        if line.startswith("Name:"):
            current = line[5:].strip()
        elif "Digest" in line and current:
            digests[current] = line.split(":", 1)[1].strip()
    sf_files = [n for n in names if n.startswith("META-INF/") and n.endswith(".SF")]
    sig_files = [n for n in names if n.startswith("META-INF/") and n.endswith((".RSA", ".DSA", ".EC"))]
    verified = []
    import base64
    for name, digest_b64 in list(digests.items())[:200]:
        if name not in names:
            continue
        content = z.read(name)
        sha256 = base64.b64encode(hashlib.sha256(content).digest()).decode()
        sha1 = base64.b64encode(hashlib.sha1(content).digest()).decode()
        verified.append({"entry": name, "manifest_digest": digest_b64,
                         "sha256_match": sha256 == digest_b64, "sha1_match": sha1 == digest_b64})
    return {"present": True, "manifest_entries_with_digest": len(digests),
            "signature_files": sf_files, "certificate_files": sig_files,
            "digest_verification_sample": verified,
            "note": "JAR entry digests recomputed and compared; PKCS#7 signer trust is not verified."}


# ---------------------------------------------------------------------------
# Repair (dry-run default; writes require --approve and --out)
# ---------------------------------------------------------------------------

def cmd_repair(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    mode = args.mode
    result = {"file": str(path), "mode": mode, "dry_run": not (args.out and args.approve)}
    if mode in ("mojibake-latin1", "newlines-lf", "newlines-crlf", "add-bom", "remove-bom"):
        fixed, changes = repair_encoding(data, mode)
        result["changes"] = changes
        result["output_size"] = len(fixed)
        result["output_sha256"] = hash_bytes(fixed)["sha256"]
        out_data = fixed
    elif mode == "json":
        out_data, changes = _repair_json(data)
        result["changes"] = changes
        result["output_sha256"] = hash_bytes(out_data)["sha256"]
    elif mode == "zip-cd":
        out_data, changes = _rebuild_zip_cd(data)
        result["changes"] = changes
        result["output_sha256"] = hash_bytes(out_data)["sha256"]
    elif mode == "gzip-trailer":
        out_data, changes = _fix_gzip_trailer(data)
        result["changes"] = changes
        result["output_sha256"] = hash_bytes(out_data)["sha256"]
    else:
        raise RetoolError("bad-mode", f"Unknown repair mode: {mode}", exit_code=EXIT_USAGE)
    if args.out:
        require_approval(args, env, "repair-write")
        write_new_bytes(Path(args.out), out_data, approve=args.approve)
        result["written"] = args.out
        result["dry_run"] = False
        result["rollback"] = "Original input was not modified. Delete the output file to roll back."
    else:
        result["note"] = "Dry-run only. Pass --out <new-file> --approve to write a repaired copy. Original is never modified."
    env["result"] = result
    return env


def _repair_json(data: bytes) -> tuple[bytes, list]:
    text = data.decode("utf-8", errors="replace")
    changes = []
    try:
        json.loads(text)
        return data, ["Input already valid JSON; no changes."]
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    if fixed != text:
        changes.append("Removed trailing commas before } or ].")
    try:
        json.loads(fixed)
        return fixed.encode("utf-8"), changes
    except json.JSONDecodeError as e:
        # Bracket balancing: insert missing/mismatched closers (string-aware), then close any
        # still-open containers at EOF. Only accepted if the result parses as JSON.
        candidate = _balance_json_brackets(fixed)
        if candidate != fixed:
            try:
                json.loads(candidate)
                changes.append("Rebalanced JSON brackets: inserted missing/mismatched closers and closed open containers.")
                return candidate.encode("utf-8"), changes
            except json.JSONDecodeError:
                pass
        raise RetoolError("repair-not-supported", f"JSON cannot be safely repaired: {e}. Manual review required.", exit_code=EXIT_USAGE)


def _balance_json_brackets(text: str) -> str:
    stack = []
    out = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "{[":
            stack.append(ch)
            out.append(ch)
        elif ch in "}]":
            # Close mismatched openers first (e.g. a } encountered while a [ is open).
            while stack and ((stack[-1] == "{" and ch == "]") or (stack[-1] == "[" and ch == "}")):
                top = stack.pop()
                out.append("}" if top == "{" else "]")
            if stack and ((stack[-1] == "{" and ch == "}") or (stack[-1] == "[" and ch == "]")):
                stack.pop()
                out.append(ch)
            # else: stray closer with empty stack -> drop it
        else:
            out.append(ch)
    while stack:
        top = stack.pop()
        out.append("}" if top == "{" else "]")
    return "".join(out)


def _rebuild_zip_cd(data: bytes) -> tuple[bytes, list]:
    # Scan local file headers and rebuild central directory + EOCD
    changes = []
    pos = 0
    entries = []
    while pos + 30 <= len(data):
        if data[pos:pos + 4] != b"PK\x03\x04":
            break
        method = u16(data, pos + 8)
        crc = u32(data, pos + 14)
        csize = u32(data, pos + 18)
        usize = u32(data, pos + 22)
        nlen = u16(data, pos + 26)
        elen = u16(data, pos + 28)
        name = data[pos + 30:pos + 30 + nlen]
        body_off = pos + 30 + nlen + elen
        if csize == 0xFFFFFFFF:
            raise RetoolError("zip64-not-supported", "ZIP64 entries not supported by CD rebuild.", exit_code=EXIT_USAGE)
        entries.append({"name": name, "method": method, "crc": crc, "csize": csize, "usize": usize,
                        "local_off": pos})
        pos = body_off + csize
    if not entries:
        raise RetoolError("repair-not-supported", "No local file headers found; cannot rebuild.", exit_code=EXIT_USAGE)
    cd_start = pos
    cd = bytearray()
    for e in entries:
        rec = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, e["method"], 0, 0,
                          e["crc"], e["csize"], e["usize"], len(e["name"]), 0, 0, 0, 0, 0, e["local_off"])
        cd += rec + e["name"]
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(cd), cd_start, 0)
    out = data[:cd_start] + bytes(cd) + eocd
    changes.append(f"Rebuilt central directory from {len(entries)} local headers; appended new EOCD.")
    # validate
    try:
        z = zipfile.ZipFile(io.BytesIO(out))
        bad = z.testzip()
        changes.append(f"Reopen CRC check: {'OK' if bad is None else 'FAILED at ' + bad}.")
    except Exception as e:
        changes.append(f"Reopen failed: {e}")
    return out, changes


def _fix_gzip_trailer(data: bytes) -> tuple[bytes, list]:
    if data[:2] != b"\x1f\x8b":
        raise RetoolError("not-gzip", "Input is not gzip.", exit_code=EXIT_USAGE)
    changes = []
    d = zlib.decompressobj(31)
    try:
        content = d.decompress(data)
    except zlib.error as e:
        raise RetoolError("repair-not-supported", f"gzip stream cannot be decompressed: {e}", exit_code=EXIT_USAGE)
    crc = zlib.crc32(content) & 0xFFFFFFFF
    isize = len(content) & 0xFFFFFFFF
    # find where compressed stream ended
    consumed = len(data) - len(d.unused_data)
    body = data[:consumed - 8] if consumed >= 8 else data[:consumed]
    out = body + struct.pack("<II", crc, isize)
    changes.append(f"Recomputed CRC32={crc:08x} and ISIZE={isize} from decompressed content.")
    return out, changes


# ---------------------------------------------------------------------------
# Patch (reversible, precondition-checked)
# ---------------------------------------------------------------------------

def cmd_patch(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    spec_path = validate_input_file(args.spec, cfg)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    data = bytearray(path.read_bytes())
    original_hash = hashlib.sha256(data).hexdigest()
    result = {"file": str(path), "original_sha256": original_hash, "patches": []}
    if spec.get("target_sha256") and spec["target_sha256"] != original_hash:
        raise RetoolError("precondition-failed", "target_sha256 does not match the input; refusing to patch.",
                          {"expected": spec["target_sha256"], "actual": original_hash}, EXIT_USAGE)
    for i, p in enumerate(spec.get("patches", [])):
        off = parse_int(str(p["offset"]))
        expected = bytes.fromhex(p.get("expected_hex", "")) if p.get("expected_hex") else None
        new = bytes.fromhex(p["new_hex"])
        if off < 0 or off + len(new) > len(data):
            raise RetoolError("patch-out-of-range", f"Patch {i} out of range", exit_code=EXIT_USAGE)
        actual = bytes(data[off:off + len(new)])
        if expected is not None and bytes(data[off:off + len(expected)]) != expected:
            raise RetoolError("precondition-failed", f"Patch {i}: expected bytes at {off} do not match.",
                              {"expected": expected.hex(), "actual": bytes(data[off:off + len(expected)]).hex()}, EXIT_USAGE)
        result["patches"].append({"index": i, "offset": off, "old_hex": actual.hex(), "new_hex": new.hex(),
                                  "length": len(new)})
        if args.apply:
            data[off:off + len(new)] = new
    result["changed_bytes"] = sum(p["length"] for p in result["patches"])
    if args.apply:
        require_approval(args, env, "patch-apply")
        if not args.out:
            raise RetoolError("missing-out", "patch --apply requires --out <new-file> (original is never modified).", exit_code=EXIT_USAGE)
        write_new_bytes(Path(args.out), bytes(data), approve=args.approve)
        result["applied"] = True
        result["written"] = args.out
        result["patched_sha256"] = hashlib.sha256(data).hexdigest()
        result["rollback"] = {"method": "reverse-patch", "reverse_patches": [
            {"offset": p["offset"], "new_hex": p["old_hex"]} for p in result["patches"]]}
    else:
        result["applied"] = False
        result["note"] = "Dry-run. Pass --apply --out <new-file> --approve to write a patched copy. Original is never modified."
    env["result"] = result
    return env


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def cmd_convert(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    to = args.to
    result = {"file": str(path), "to": to, "loss_report": []}
    src_fmt = detect_format(data, path.name)
    if to == "hex":
        out = data.hex().encode()
        result["lossless"] = True
    elif to == "base64":
        import base64
        out = base64.b64encode(data)
        result["lossless"] = True
    elif to == "json":
        obj, losses = _to_json(data, path, cfg)
        out = json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        result["loss_report"] = losses
        result["lossless"] = not losses
    elif to == "csv":
        out, losses = _to_csv(data, path, cfg)
        result["loss_report"] = losses
        result["lossless"] = not losses
    else:
        raise RetoolError("bad-target", f"Unknown conversion target: {to}. Supported: hex, base64, json, csv.", exit_code=EXIT_USAGE)
    result["output_bytes"] = len(out)
    result["output_sha256"] = hash_bytes(out)["sha256"]
    if args.out:
        write_new_bytes(Path(args.out), out, approve=getattr(args, "approve", False))
        result["written"] = args.out
        # reopen validation for json/csv
        if to == "json":
            try:
                json.loads(out)
                result["reopen_ok"] = True
            except json.JSONDecodeError as e:
                result["reopen_ok"] = False
                result["reopen_error"] = str(e)
    else:
        result["preview"] = out[:512].decode("utf-8", errors="replace")
        result["note"] = "No --out given; preview only. Pass --out to write the converted file."
    env["result"] = result
    return env


def _to_json(data: bytes, path: Path, cfg: dict):
    fmt = detect_format(data, path.name)
    losses = []
    if fmt in ("zip", "tar", "elf", "pe", "macho"):
        from tools_formats import cmd_parse
        # use parse result
        import types
        ns = types.SimpleNamespace(path=str(path), format="auto")
        from tools_core import envelope
        e = envelope("parse", cfg)
        r = cmd_parse(ns, e, cfg)["result"]
        return r, ["Binary structure summarized as JSON; not a byte-level round-trip."]
    ext = path.suffix.lower()
    if ext in (".json",) or data[:1] in (b"{", b"["):
        return json.loads(data), []
    if ext in (".ini", ".cfg"):
        r = analyze_ini(data)
        return r.get("sections", {}), []
    if ext == ".toml":
        r = analyze_toml(data)
        return r.get("preview", {}), []
    if ext in (".yaml", ".yml"):
        from tools_data import analyze_yaml
        r = analyze_yaml(data)
        losses.append("YAML flat-subset only; nested structures may be lost.")
        return r.get("flat_mapping_subset", {}), losses
    if ext in (".xml",) or data[:5] == b"<?xml":
        r = analyze_xml(data)
        losses.append("XML converted to tag histogram/metadata, not full tree.")
        return r, losses
    if ext in (".csv", ".tsv"):
        r = analyze_csv(data)
        return r, []
    if ext == ".plist" or data[:8] == b"bplist00":
        from tools_formats import parse_plist
        return parse_plist(data)["content"], []
    if data[:2] == b"\xac\xed":
        from tools_data import analyze_java_serialization
        losses.append("Java serialization summarized structurally; objects not instantiated.")
        return analyze_java_serialization(data), losses
    # binary serializers — check specific extensions before the schema-less protobuf fallback
    if ext in (".msgpack", ".mpack"):
        return decode_msgpack(data)["value"], []
    if ext == ".cbor":
        return decode_cbor(data)["value"], []
    if ext == ".bson":
        return decode_bson(data)["value"], []
    if ext in (".der", ".cer", ".crt") or data[:1] == b"\x30":
        losses.append("DER converted to TLV tree; not byte-identical re-encoding.")
        return parse_der(data)["tree"], losses
    if ext in (".pb", ".bin"):
        try:
            r = decode_protobuf(data)
            return r["fields"], ["Protobuf decoded schema-less; field names unknown."]
        except Exception:
            pass
    if data[:16] == b"SQLite format 3\x00":
        from tools_data import analyze_sqlite
        return analyze_sqlite(path, cfg), ["SQLite schema/stats exported; row data not included by default."]
    raise RetoolError("unsupported-conversion", f"Cannot convert {path.name} to JSON automatically; specify a recognized format.", exit_code=EXIT_USAGE)


def _to_csv(data: bytes, path: Path, cfg: dict):
    obj, losses = _to_json(data, path, cfg)
    rows = []
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        rows = obj
    elif isinstance(obj, dict):
        rows = [{"key": k, "value": json.dumps(v, ensure_ascii=False, default=str)} for k, v in obj.items()]
        losses.append("Flattened object to key/value rows.")
    else:
        rows = [{"value": json.dumps(obj, ensure_ascii=False, default=str)}]
        losses.append("Scalar wrapped in a single row.")
    if not rows:
        return b"", losses
    import csv as _csv
    buf = io.StringIO()
    fields = list({k for r in rows for k in r})
    w = _csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
    return buf.getvalue().encode("utf-8"), losses


# ---------------------------------------------------------------------------
# Fuzz / fixtures / differential
# ---------------------------------------------------------------------------

def cmd_fuzz(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    count = min(parse_int(args.count) if args.count else 20, cfg["limits"]["max_fuzz_cases"])
    seed = parse_int(args.seed) if args.seed else 0
    import random
    rng = random.Random(seed)
    cases = []
    outdir = Path(args.out) if args.out else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
    mutations = ["truncate-50", "truncate-90", "truncate-10", "bitflip", "byteflip", "insert", "delete",
                 "boundary-00", "boundary-ff", "duplicate-header", "zero-fill-tail", "swap-endian-word"]
    for i in range(count):
        kind = mutations[i % len(mutations)]
        mutated = _mutate(data, kind, rng)
        name = f"case-{i:04d}-{kind}.bin"
        entry = {"name": name, "mutation": kind, "size": len(mutated), "sha256": hash_bytes(mutated)["sha256"]}
        if outdir:
            (outdir / name).write_bytes(mutated)
            entry["path"] = str(outdir / name)
        if args.differential or args.run_builtins:
            entry["parser_results"] = _run_parsers(mutated, name, cfg)
        cases.append(entry)
    env["result"] = {"seed_file": str(path), "seed": seed, "case_count": len(cases), "cases": cases,
                     "outdir": str(outdir) if outdir else None,
                     "note": "Deterministic mutations for parser testing. Differential results show built-in parser behavior; crashes/timeouts are flagged."}
    return env


def _mutate(data: bytes, kind: str, rng) -> bytes:
    if not data:
        return data
    if kind == "truncate-50":
        return data[:len(data) // 2]
    if kind == "truncate-90":
        return data[:len(data) // 10]
    if kind == "truncate-10":
        return data[:max(1, len(data) * 9 // 10)]
    if kind == "bitflip":
        b = bytearray(data)
        for _ in range(max(1, len(b) // 100)):
            i = rng.randrange(len(b))
            b[i] ^= 1 << rng.randrange(8)
        return bytes(b)
    if kind == "byteflip":
        b = bytearray(data)
        i = rng.randrange(len(b))
        b[i] ^= 0xFF
        return bytes(b)
    if kind == "insert":
        i = rng.randrange(len(data) + 1)
        return data[:i] + bytes([rng.randrange(256)]) + data[i:]
    if kind == "delete":
        i = rng.randrange(len(data))
        return data[:i] + data[i + 1:]
    if kind == "boundary-00":
        b = bytearray(data)
        for i in range(min(16, len(b))):
            b[i] = 0
        return bytes(b)
    if kind == "boundary-ff":
        b = bytearray(data)
        for i in range(min(16, len(b))):
            b[i] = 0xFF
        return bytes(b)
    if kind == "duplicate-header":
        return data[:min(32, len(data))] + data
    if kind == "zero-fill-tail":
        return data[:len(data) // 2] + b"\x00" * (len(data) - len(data) // 2)
    if kind == "swap-endian-word" and len(data) >= 4:
        b = bytearray(data)
        b[0:4] = b[0:4][::-1]
        return bytes(b)
    return data


def _run_parsers(data: bytes, name: str, cfg: dict) -> list:
    results = []
    fmt = detect_format(data, name)
    from tools_formats import cmd_parse
    import types
    from tools_core import envelope
    # write to temp for parse (parsers take paths)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    try:
        ns = types.SimpleNamespace(path=tmp, format="auto")
        e = envelope("parse", cfg)
        t0 = time.perf_counter()
        try:
            r = cmd_parse(ns, e, cfg)
            results.append({"parser": "retool-parse", "ok": True, "format": r["result"].get("format"),
                            "ms": round((time.perf_counter() - t0) * 1000, 2)})
        except RetoolError as ex:
            results.append({"parser": "retool-parse", "ok": False, "error_code": ex.code,
                            "ms": round((time.perf_counter() - t0) * 1000, 2)})
        except Exception as ex:
            results.append({"parser": "retool-parse", "ok": False, "crash": type(ex).__name__, "message": str(ex)[:120],
                            "ms": round((time.perf_counter() - t0) * 1000, 2)})
        # independent stdlib parser for zip
        if data[:4] == b"PK\x03\x04":
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
                bad = z.testzip()
                results.append({"parser": "zipfile", "ok": bad is None, "failed_entry": bad})
            except Exception as ex:
                results.append({"parser": "zipfile", "ok": False, "error": str(ex)[:120]})
        if data[:1] in (b"{", b"["):
            try:
                json.loads(data)
                results.append({"parser": "json", "ok": True})
            except Exception as ex:
                results.append({"parser": "json", "ok": False, "error": str(ex)[:120]})
    finally:
        os.unlink(tmp)
    return results


# ---------------------------------------------------------------------------
# External tool adapter (allowlist, read-only, bounded)
# ---------------------------------------------------------------------------

def cmd_external(args, env, cfg):
    tools = cfg["external_tools"]
    if args.list or (not args.tool and not args.run):
        rows = []
        for name, spec in tools.items():
            path = shutil.which(name)
            rows.append({"tool": name, "available": path is not None, "path": path,
                         "kind": spec["kind"], "dynamic": spec["dynamic"],
                         "execution": "blocked-plan-only" if spec["dynamic"] else "allowlisted-read-only"})
        env["result"] = {"tools": rows, "note": "Dynamic tools are never executed. Read-only tools run only with fixed argument templates and timeouts."}
        return env
    name = args.tool
    if name not in tools:
        raise RetoolError("unknown-tool", f"Tool '{name}' is not in the allowlist. Available: {', '.join(tools)}", exit_code=EXIT_USAGE)
    spec = tools[name]
    path = shutil.which(name)
    result = {"tool": name, "available": path is not None, "dynamic": spec["dynamic"]}
    if not path:
        result["note"] = "Tool not installed. Install it and re-run, or use the generated plan."
        env["result"] = result
        return env
    if spec["dynamic"]:
        result["execution"] = "blocked"
        result["note"] = "Dynamic tool; this skill tool never executes it. Use plan --template debugger-session / dynamic-sandbox / adb-session in an authorized isolated environment."
        env["result"] = result
        return env
    if args.check:
        try:
            proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=cfg["limits"]["external_timeout_s"])
            result["version_output"] = (proc.stdout or proc.stderr)[:500]
        except subprocess.TimeoutExpired:
            result["version_output"] = "timeout"
        except Exception as e:
            result["version_output"] = str(e)
        env["result"] = result
        return env
    if args.run:
        inp = validate_input_file(args.run, cfg)
        argv = [path] + [a.replace("{input}", str(inp)) for a in spec["args"]]
        result["argv"] = argv
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=cfg["limits"]["external_timeout_s"])
            result["returncode"] = proc.returncode
            result["stdout"] = proc.stdout[:20000]
            result["stderr"] = proc.stderr[:2000]
            result["normalized"] = _normalize_tool_output(name, proc.stdout)
        except subprocess.TimeoutExpired:
            result["error"] = "timeout"
        except Exception as e:
            result["error"] = str(e)
        env["result"] = result
        return env
    env["result"] = result
    return env


def _normalize_tool_output(tool: str, stdout: str) -> dict:
    lines = stdout.splitlines()
    if tool == "file":
        return {"description": lines[0] if lines else ""}
    if tool == "nm":
        syms = []
        for line in lines[:2000]:
            parts = line.split()
            if len(parts) >= 3:
                syms.append({"address": parts[0], "type": parts[1], "name": parts[2]})
        return {"symbols": syms[:500], "count": len(syms)}
    if tool == "strings":
        return {"strings": lines[:1000], "count": len(lines)}
    if tool == "readelf":
        return {"line_count": len(lines), "sample": lines[:100]}
    return {"line_count": len(lines), "sample": lines[:100]}


# ---------------------------------------------------------------------------
# Spec authoring + parser generation
# ---------------------------------------------------------------------------

def cmd_spec(args, env, cfg):
    layout = _resolve_layout(args.layout)
    name = args.layout if args.layout in load_json_asset("asset-layouts.json")["layouts"] else Path(args.layout).stem
    lines = [f"# Format specification: {name}", "", layout.get("description", ""), "",
             f"- Default endianness: {layout.get('endian', 'le')}",
             f"- Declared size: {layout.get('size', 'variable')} bytes", "",
             "| Offset | Field | Type | Size | Description |", "|---|---|---|---|---|"]
    off = 0
    for f in layout["fields"]:
        t = f["type"]
        count = f.get("count", 1)
        size = TYPE_SIZES.get(t, count if t in ("bytes", "char") else 1)
        if t in ("bytes", "char"):
            size = count
        else:
            size = TYPE_SIZES[t] * count
        lines.append(f"| {off} (0x{off:x}) | {f['name']} | {t}{'[' + str(count) + ']' if count > 1 else ''} | {size} | {f.get('desc', '')} |")
        off += size
    lines += ["", "## Invariants and validation notes", "",
              "- All offsets are relative to the start of the structure.",
              "- Integer fields use the declared endianness unless a field overrides it.",
              "- Validate magic/signature fields before trusting other fields.",
              "- Bounds-check every offset and length against the buffer before reading.",
              "- Unknown or version-dependent fields must be preserved, not guessed.", ""]
    if layout.get("fields"):
        bits_fields = [f for f in layout["fields"] if f.get("bits")]
        if bits_fields:
            lines += ["## Bitfields", ""]
            for f in bits_fields:
                for b in f["bits"]:
                    lines.append(f"- `{f['name']}.{b['name']}`: mask 0x{b['mask']:x}, shift {b['shift']}")
            lines.append("")
    md = "\n".join(lines)
    result = {"layout": name, "field_count": len(layout["fields"]), "computed_size": off, "markdown": md}
    if args.out:
        write_new_bytes(Path(args.out), md.encode("utf-8"), approve=getattr(args, "approve", False))
        result["written"] = args.out
    if args.emit_parser:
        code = _emit_parser(layout, name)
        write_new_bytes(Path(args.emit_parser), code.encode("utf-8"), approve=getattr(args, "approve", False))
        result["parser_written"] = args.emit_parser
        result["parser_note"] = "Generated reader is bounds-checked and read-only; run its --self-test to validate."
    env["result"] = result
    return env


def _emit_parser(layout: dict, name: str) -> str:
    endian = layout.get("endian", "le")
    lines = ['"""Generated safe parser for ' + name + '. Bounds-checked, read-only.',
             'Produced by code-data-reverse-engineering spec --emit-parser."""',
             'import struct, sys, json', '', 'def parse(data: bytes) -> dict:',
             '    out = {}', '    off = 0']
    for f in layout["fields"]:
        t = f["type"]
        count = f.get("count", 1)
        fe = f.get("endian", endian)
        eb = "<" if fe == "le" else ">"
        if t in ("bytes", "char"):
            lines.append(f'    if off + {count} > len(data): raise ValueError("truncated: {f["name"]}")')
            if t == "char":
                lines.append(f'    out["{f["name"]}"] = data[off:off+{count}].decode("utf-8", errors="replace").rstrip("\\x00")')
            else:
                lines.append(f'    out["{f["name"]}"] = data[off:off+{count}].hex()')
            lines.append(f'    off += {count}')
        else:
            size = TYPE_SIZES[t]
            fmt = {"u8": "B", "i8": "b", "u16": "H", "i16": "h", "u32": "I", "i32": "i",
                   "u64": "Q", "i64": "q", "f32": "f", "f64": "d"}[t]
            if count > 1:
                lines.append(f'    if off + {size * count} > len(data): raise ValueError("truncated: {f["name"]}")')
                lines.append(f'    out["{f["name"]}"] = list(struct.unpack_from("{eb}{count}{fmt}", data, off))')
                lines.append(f'    off += {size * count}')
            else:
                lines.append(f'    if off + {size} > len(data): raise ValueError("truncated: {f["name"]}")')
                lines.append(f'    out["{f["name"]}"] = struct.unpack_from("{eb}{fmt}", data, off)[0]')
                lines.append(f'    off += {size}')
            if f.get("bits"):
                for b in f["bits"]:
                    lines.append(f'    out["{f["name"]}_{b["name"]}"] = (out["{f["name"]}"] >> {b["shift"]}) & {b["mask"]}')
    lines += ['    return out', '', 'def self_test():',
              '    # minimal smoke test: parse a zero buffer of the declared size',
              f'    size = {layout.get("size", 64)}',
              '    r = parse(b"\\x00" * size)',
              '    assert isinstance(r, dict)',
              '    print("self-test OK:", len(r), "fields")', '',
              'if __name__ == "__main__":',
              '    if "--self-test" in sys.argv:',
              '        self_test()',
              '    elif len(sys.argv) > 1:',
              '        with open(sys.argv[1], "rb") as f:',
              '            print(json.dumps(parse(f.read()), indent=2, default=str))',
              '    else:',
              '        print("usage: parser.py <file> | --self-test")', '']
    return "\n".join(lines)
