"""Identification, triage, encoding, numeric, XOR and carving handlers."""
from __future__ import annotations

import base64
import binascii
import math
import re
import struct
import unicodedata
import zlib
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, EXIT_LIMIT, load_json_asset, read_bytes,
                        validate_input_file, shannon_entropy, entropy_windows, hexdump,
                        find_all, hash_bytes, u16, u32, u64, parse_int, cstring, align_up)

EXT_FAMILY = {
    ".exe": "executable", ".dll": "executable", ".so": "executable", ".o": "executable",
    ".elf": "executable", ".dylib": "executable", ".bin": "executable",
    ".zip": "archive", ".jar": "archive", ".apk": "archive", ".aab": "archive", ".ipa": "archive",
    ".docx": "archive", ".xlsx": "archive", ".pptx": "archive", ".odt": "archive", ".epub": "archive",
    ".tar": "archive", ".gz": "compression", ".bz2": "compression", ".xz": "compression",
    ".7z": "archive", ".rar": "archive", ".cab": "archive",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".bmp": "image",
    ".pdf": "document", ".doc": "document", ".xls": "document", ".rtf": "document",
    ".sqlite": "database", ".db": "database", ".sqlite3": "database",
    ".pcap": "network", ".pcapng": "network",
    ".class": "bytecode", ".dex": "bytecode", ".wasm": "bytecode",
    ".json": "text", ".xml": "text", ".txt": "text", ".csv": "text", ".md": "text",
    ".py": "text", ".js": "text", ".c": "text", ".h": "text", ".java": "text",
}

B64_RE = re.compile(rb"[A-Za-z0-9+/]{24,}={0,2}")
HEX_RE = re.compile(rb"(?:[0-9a-fA-F]{2}){12,}")


def _sig_db():
    return load_json_asset("asset-signatures.json")["signatures"]


def identify_bytes(data: bytes, name: str = "") -> dict:
    sigs = _sig_db()
    matches = []
    for s in sigs:
        off = s["offset"]
        raw = bytes.fromhex(s["hex"])
        if off + len(raw) <= len(data) and data[off:off + len(raw)] == raw:
            strength = "high" if len(raw) >= 4 else ("medium" if len(raw) >= 2 else "low")
            matches.append({**s, "match_offset": off, "confidence": strength})
    # RIFF subtype
    if data[:4] == b"RIFF" and len(data) >= 12:
        sub = data[8:12]
        submap = {b"WAVE": ("WAVE audio", "audio/wav"), b"AVI ": ("AVI video", "video/avi"),
                  b"WEBP": ("WebP image", "image/webp")}
        if sub in submap:
            matches.append({"offset": 0, "hex": data[:12].hex(), "name": submap[sub][0],
                            "mime": submap[sub][1], "family": "media", "confidence": "high",
                            "note": "RIFF subtype at offset 8."})
    probes = structural_probes(data, name)
    # choose best
    best = None
    for m in matches:
        if m["confidence"] == "high":
            best = m
            break
    if best is None and matches:
        best = matches[0]
    ext = Path(name).suffix.lower()
    expected = EXT_FAMILY.get(ext)
    mismatch = None
    if best and expected and best["family"] not in (expected, "ambiguous", "text") and not (
            expected == "archive" and best["family"] in ("archive", "package")):
        mismatch = {"extension": ext, "expected_family": expected, "detected_family": best["family"],
                    "detected": best["name"]}
    if not best and probes.get("text_like"):
        best = {"name": "Text (no binary signature)", "mime": "text/plain", "family": "text", "confidence": "medium"}
    if not best:
        best = {"name": "Unknown binary", "mime": "application/octet-stream", "family": "unknown", "confidence": "low"}
    return {
        "name": name, "size": len(data), "best_match": best,
        "signature_matches": matches[:25], "structural_probes": probes,
        "extension_mismatch": mismatch,
        "entropy": shannon_entropy(data[:65536]),
        "sha256": hash_bytes(data)["sha256"],
    }


def structural_probes(data: bytes, name: str = "") -> dict:
    out = {}
    if len(data) >= 64 and data[:4] == b"\x7fELF":
        cls = {1: 32, 2: 64}.get(data[4], 0)
        endian = {1: "little", 2: "big"}.get(data[5], "?")
        machine = u16(data, 18, "le" if data[5] == 1 else "be")
        machines = {0x03: "x86", 0x3E: "x86-64", 0x28: "ARM", 0xB7: "AArch64", 0x08: "MIPS",
                    0xF3: "RISC-V", 0x14: "PowerPC", 0x15: "PowerPC64", 0x02: "SPARC", 0x2B: "SPARC64",
                    0x53: "AVR", 0x4C: "ColdFire", 0x04: "68k", 0x16: "S390", 0xAA: "ARC"}
        out["elf"] = {"class_bits": cls, "endian": endian, "machine": machines.get(machine, hex(machine))}
    if data[:2] == b"MZ" and len(data) >= 0x40:
        pe_off = u32(data, 0x3C)
        if 0 < pe_off < len(data) - 24 and data[pe_off:pe_off + 4] == b"PE\x00\x00":
            machine = u16(data, pe_off + 4)
            machines = {0x14C: "x86", 0x8664: "x86-64", 0x1C0: "ARM", 0xAA64: "ARM64", 0x1C4: "ARMNT"}
            out["pe"] = {"machine": machines.get(machine, hex(machine)),
                         "bitness": 64 if u16(data, pe_off + 24) == 0x20B else 32}
    if data[:4] in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"):
        out["macho"] = {"endian": "little" if data[0] in (0xCE, 0xCF) else "big",
                        "bitness": 64 if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else 32}
    if data[:4] == b"PK\x03\x04":
        try:
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()[:40]
            kind = "zip"
            if any(n == "AndroidManifest.xml" for n in names):
                kind = "apk/aab"
            elif any(n == "META-INF/MANIFEST.MF" for n in names):
                kind = "jar"
            elif any(n.startswith("Payload/") and n.endswith(".app/Info.plist") for n in names):
                kind = "ipa"
            elif any(n.startswith("word/") for n in names):
                kind = "ooxml-word"
            elif any(n.startswith("xl/") for n in names):
                kind = "ooxml-excel"
            elif any(n.startswith("ppt/") for n in names):
                kind = "ooxml-powerpoint"
            elif any(n == "mimetype" for n in names):
                kind = "odf/epub"
            elif any(n == "manifest.json" for n in names):
                kind = "container-image-or-extension"
            out["zip"] = {"kind": kind, "entries_sample": names}
        except Exception as e:
            out["zip"] = {"error": str(e)}
    if data[:4] == b"\xca\xfe\xba\xbe" and len(data) >= 8:
        # Java class vs Mach-O fat: class major version at offset 6
        major = u16(data, 6, "be")
        if 45 <= major <= 70:
            out["java_class"] = {"major_version": major}
        else:
            out["macho_fat"] = {"candidate": True}
    if data[:8] in (b"dex\n035\x00", b"dex\n037\x00", b"dex\n038\x00", b"dex\n039\x00", b"dex\n040\x00", b"dex\n041\x00"):
        out["dex"] = {"version": data[4:7].decode()}
    if data[:4] == b"\x00asm":
        out["wasm"] = {"version": u32(data, 4)}
    if data[:16] == b"SQLite format 3\x00":
        ps = u16(data, 16, "be")
        out["sqlite"] = {"page_size": 65536 if ps == 1 else ps}
    if data[:4] == b"\x89PNG":
        out["png"] = {"width": u32(data, 16, "be"), "height": u32(data, 20, "be")}
    if data[:3] == b"\xff\xd8\xff":
        out["jpeg"] = True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        out["gif"] = {"width": u16(data, 6), "height": u16(data, 8)}
    if data[:4] == b"%PDF":
        out["pdf"] = {"version": data[5:8].decode("ascii", errors="replace")}
    if data[:4] == b"\xd0\xcf\x11\xe0":
        out["ole"] = True
    if data[:4] in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x34\xcd\xb2\xa1", b"\xa1\xb2\xcd\x34"):
        out["pcap"] = True
    if data[:4] == b"\x0a\x0d\x0d\x0a":
        out["pcapng"] = True
    if data[:4] == b"GGUF":
        out["gguf"] = {"version": u32(data, 4)}
    if data[4:8] == b"TFL3":
        out["tflite"] = True
    if data[:4] == b"\x07\x23\x02\x03":
        out["spirv"] = True
    if data[:4] == b"MDMP":
        out["minidump"] = True
    if data[:4] == b"regf":
        out["registry"] = True
    if data[:8] == b"ANDROID!":
        out["android_boot"] = True
    if data[:4] == b"\xd0\x0d\xfe\xed":
        out["dtb"] = {"totalsize": u32(data, 4, "be")}
    if data[:4] == b"hsqs" or data[:4] == b"sqsh":
        out["squashfs"] = {"endian": "little" if data[:4] == b"hsqs" else "big"}
    if len(data) > 1080 and data[1080:1082] == b"\x53\xef":
        out["ext"] = True
    if len(data) > 512 and data[512:520] == b"EFI PART":
        out["gpt"] = True
    if len(data) > 510 and data[510:512] == b"\x55\xaa":
        out["mbr_candidate"] = True
    if data[:4] == b"NES\x1a":
        out["nes"] = True
    if data[:4] == b"UPX!":
        out["upx"] = True
    if data[:4] in (b"IWAD", b"PWAD"):
        out["wad"] = {"kind": data[:4].decode()}
    if data[:4] == b"PACK":
        out["quake_pak"] = True
    if data[:4] == b"MPQ\x1a":
        out["mpq"] = True
    if data[:7] == b"UnityFS":
        out["unity"] = True
    if data[:4] in (b"\xc1\x83\x2a\x9e", b"\x9e\x2a\x83\xc1"):
        out["unreal"] = True
    if data[:4] == b"KDMV":
        out["vmdk"] = True
    if data[:4] == b"QFI\xfb":
        out["qcow2"] = True
    if data[:8] == b"conectix":
        out["vhd"] = True
    if data[:8] == b"vhdxfile":
        out["vhdx"] = True
    if data[:4] == b"\xed\xab\xee\xdb":
        out["rpm"] = True
    if data[:15] == b"!<arch>\ndebian":
        out["deb"] = True
    if data[:4] == b"xar!":
        out["xar"] = True
    if data[:4] == b"MSCF":
        out["cab"] = True
    if data[:4] == b"7z\xbc\xaf\x27\x1c"[:4]:
        out["7z"] = True
    if data[:6] == b"Rar!\x1a\x07":
        out["rar"] = True
    if data[:4] == b"\xac\xed\x00\x05":
        out["java_serialization"] = True
    if data[:1] == b"\x80" and len(data) > 2 and data[1] in (2, 3, 4, 5):
        out["pickle_candidate"] = True
    if data[:5] == b"<?xml" or data[:6] == b"\xef\xbb\xbf<?x":
        out["xml"] = True
    if data[:1] in (b"{", b"[") and data[:200].strip():
        out["json_candidate"] = True
    # text likeness
    if data:
        sample = data[:8192]
        printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
        out["text_like"] = printable / len(sample) > 0.92 and b"\x00" not in sample[:1024]
    return out


def cmd_identify(args, env, cfg):
    results = []
    for p in args.paths:
        path = validate_input_file(p, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        results.append(identify_bytes(data, path.name))
    env["result"] = {"files": results}
    env["inputs"]["paths"] = args.paths
    if any(r["extension_mismatch"] for r in results):
        env["warnings"].append("Extension/content mismatch detected; trust signatures and structure over names.")
    return env


def cmd_entropy(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    window = parse_int(args.window) if args.window else 1024
    stride = parse_int(args.stride) if args.stride else window
    if window < 16 or stride < 1:
        raise RetoolError("bad-window", "window must be >= 16 and stride >= 1", exit_code=EXIT_USAGE)
    wins = entropy_windows(data, window, stride, max_windows=cfg["limits"]["max_inventory_files"])
    overall = shannon_entropy(data)
    high = [w for w in wins if w["entropy"] >= 7.5]
    low = [w for w in wins if w["entropy"] < 1.0]
    interpretation = "structured/text-like" if overall < 5.0 else ("mixed" if overall < 7.2 else "compressed/encrypted/random-like")
    env["result"] = {
        "file": str(path), "size_read": len(data), "overall_entropy": overall,
        "window": window, "stride": stride, "windows": wins[:512],
        "high_entropy_regions": high[:64], "low_entropy_regions": low[:64],
        "interpretation": {"label": interpretation, "confidence": "medium",
                           "note": "Entropy alone cannot distinguish compression from encryption; corroborate with signatures, sections and strings."},
    }
    env["limitations"].append("Entropy is a heuristic; high entropy is not proof of encryption or packing.")
    return env


def extract_strings(data: bytes, min_len: int = 4, limit: int = 5000, encoding: str = "all") -> list:
    out = []
    if encoding in ("all", "ascii"):
        for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
            out.append({"offset": m.start(), "encoding": "ascii", "length": len(m.group()),
                        "value": m.group().decode("ascii")})
            if len(out) >= limit:
                return out
    if encoding in ("all", "utf16le"):
        for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, data):
            out.append({"offset": m.start(), "encoding": "utf16le", "length": len(m.group()) // 2,
                        "value": m.group().decode("utf-16le")})
            if len(out) >= limit:
                return out
    if encoding in ("all", "utf16be"):
        for m in re.finditer(rb"(?:\x00[\x20-\x7e]){%d,}" % min_len, data):
            out.append({"offset": m.start(), "encoding": "utf16be", "length": len(m.group()) // 2,
                        "value": m.group().decode("utf-16be")})
            if len(out) >= limit:
                return out
    out.sort(key=lambda s: s["offset"])
    return out[:limit]


def cmd_strings(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    min_len = parse_int(args.min) if args.min else cfg["limits"]["min_string_len"]
    limit = parse_int(args.limit) if args.limit else cfg["limits"]["max_strings"]
    if min_len < 2:
        raise RetoolError("bad-min", "min string length must be >= 2", exit_code=EXIT_USAGE)
    strings = extract_strings(data, min_len, limit, args.encoding)
    encoded = []
    for s in strings:
        v = s["value"].encode()
        if B64_RE.fullmatch(v) and len(v) >= 24:
            try:
                dec = base64.b64decode(v, validate=True)
                if dec and sum(32 <= b < 127 or b in (9, 10, 13) for b in dec) / len(dec) > 0.85:
                    encoded.append({"offset": s["offset"], "kind": "base64-candidate", "decoded_preview": dec[:80].decode("utf-8", errors="replace")})
            except Exception:
                pass
        if HEX_RE.fullmatch(v) and len(v) >= 24:
            encoded.append({"offset": s["offset"], "kind": "hex-candidate", "decoded_preview": bytes.fromhex(s["value"])[:80].decode("utf-8", errors="replace")})
    env["result"] = {"file": str(path), "count": len(strings), "strings": strings,
                     "encoded_candidates": encoded[:100],
                     "note": "Offsets are file offsets. Reachability is not proven; strings may be incidental data."}
    if len(strings) >= limit:
        env["warnings"].append(f"String limit {limit} reached; output truncated.")
    return env


def cmd_hexdump(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    size = path.stat().st_size
    off = parse_int(args.offset) if args.offset else 0
    length = parse_int(args.length) if args.length else min(size - off, 256)
    if off < 0 or length < 0 or off + length > size:
        raise RetoolError("bad-range", "offset/length out of range", exit_code=EXIT_USAGE)
    data = read_bytes(path, cfg, off, length)
    width = parse_int(args.width) if args.width else 16
    if not 4 <= width <= 64:
        raise RetoolError("bad-width", "width must be 4..64", exit_code=EXIT_USAGE)
    result = {"file": str(path), "offset": off, "length": length, "hexdump": hexdump(data, off, width)}
    if args.layout:
        layout = _resolve_layout(args.layout)
        result["annotations"] = annotate_layout(data, layout)
        result["layout"] = args.layout
    env["result"] = result
    return env


def _resolve_layout(spec: str) -> dict:
    if spec in load_json_asset("asset-layouts.json")["layouts"]:
        return load_json_asset("asset-layouts.json")["layouts"][spec]
    p = Path(spec)
    if p.is_file():
        import json
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise RetoolError("bad-layout", f"Layout file is not valid JSON: {e}", exit_code=EXIT_USAGE)
        if "layouts" in d:
            keys = list(d["layouts"])
            if not keys:
                raise RetoolError("bad-layout", "Layout file has no layouts", exit_code=EXIT_USAGE)
            return d["layouts"][keys[0]]
        if "fields" in d:
            return d
        raise RetoolError("bad-layout", "Layout file must contain 'fields' or 'layouts'", exit_code=EXIT_USAGE)
    raise RetoolError("bad-layout", f"Unknown layout name or missing file: {spec}", exit_code=EXIT_USAGE)


TYPE_SIZES = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8, "i64": 8,
              "f32": 4, "f64": 8}


def decode_field(data: bytes, off: int, field: dict, endian: str) -> tuple:
    t = field["type"]
    count = field.get("count", 1)
    endian = field.get("endian", endian)
    if t in ("bytes", "char"):
        raw = data[off:off + count]
        if len(raw) < count:
            raise RetoolError("truncated", f"Field {field['name']} truncated at offset {off}")
        val = raw.decode("utf-8", errors="replace").rstrip("\x00") if t == "char" else raw.hex()
        return val, count
    size = TYPE_SIZES[t]
    vals = []
    for i in range(count):
        raw = data[off + i * size:off + (i + 1) * size]
        if len(raw) < size:
            raise RetoolError("truncated", f"Field {field['name']}[{i}] truncated at offset {off}")
        if t.startswith("f"):
            vals.append(struct.unpack(("<" if endian == "le" else ">") + ("f" if t == "f32" else "d"), raw)[0])
        else:
            eb = "little" if endian in ("le", "<") else "big"
            vals.append(int.from_bytes(raw, eb, signed=t.startswith("i")))
    val = vals[0] if count == 1 else vals
    return val, size * count


def annotate_layout(data: bytes, layout: dict, base: int = 0) -> list:
    endian = layout.get("endian", "le")
    off = 0
    rows = []
    for field in layout["fields"]:
        try:
            val, consumed = decode_field(data, off, field, endian)
        except RetoolError as e:
            rows.append({"field": field["name"], "offset": base + off, "error": e.message})
            break
        row = {"field": field["name"], "offset": base + off, "type": field["type"],
               "size": consumed, "value": val}
        if field.get("desc"):
            row["desc"] = field["desc"]
        if field.get("bits") and isinstance(val, int):
            row["bits"] = {b["name"]: (val >> b["shift"]) & b["mask"] for b in field["bits"]}
        rows.append(row)
        off += consumed
    return rows


# ---------------------------------------------------------------------------
# Encoding detection / repair, unicode security
# ---------------------------------------------------------------------------

def detect_encoding(data: bytes) -> dict:
    bom = None
    if data.startswith(b"\xef\xbb\xbf"):
        bom = "utf-8-sig"
    elif data.startswith(b"\xff\xfe\x00\x00"):
        bom = "utf-32le"
    elif data.startswith(b"\x00\x00\xfe\xff"):
        bom = "utf-32be"
    elif data.startswith(b"\xff\xfe"):
        bom = "utf-16le"
    elif data.startswith(b"\xfe\xff"):
        bom = "utf-16be"
    candidates = []
    body = data
    if bom:
        skip = { "utf-8-sig": 3, "utf-32le": 4, "utf-32be": 4, "utf-16le": 2, "utf-16be": 2}[bom]
        body = data[skip:]
        try:
            body.decode(bom.replace("-sig", ""))
            candidates.append({"encoding": bom, "valid": True, "confidence": "high"})
        except UnicodeDecodeError as e:
            candidates.append({"encoding": bom, "valid": False, "error": str(e)})
    else:
        try:
            data.decode("ascii")
            candidates.append({"encoding": "ascii", "valid": True, "confidence": "high"})
        except UnicodeDecodeError:
            pass
        try:
            data.decode("utf-8")
            candidates.append({"encoding": "utf-8", "valid": True, "confidence": "high" if any(b > 127 for b in data) else "medium"})
        except UnicodeDecodeError as e:
            candidates.append({"encoding": "utf-8", "valid": False, "first_error_offset": e.start})
        nul = data.count(0)
        if nul > len(data) // 8:
            for enc in ("utf-16le", "utf-16be"):
                try:
                    data.decode(enc)
                    candidates.append({"encoding": enc, "valid": True, "confidence": "medium"})
                except UnicodeDecodeError:
                    pass
        try:
            data.decode("cp1252")
            candidates.append({"encoding": "cp1252", "valid": True, "confidence": "low",
                               "note": "Single-byte decode always succeeds; treat as fallback only."})
        except UnicodeDecodeError:
            pass
    text = None
    best = next((c for c in candidates if c["valid"] and c["confidence"] == "high"), None)
    if best:
        enc = best["encoding"].replace("-sig", "")
        text = data.decode(enc, errors="replace")
    mojibake = []
    if text:
        for pat in ("Ã", "Â", "â€", "ï¿½", "Ã©", "Ã¨", "Ã¼", "Ã¶", "Ã¤"):
            if pat in text:
                mojibake.append({"pattern": pat, "count": text.count(pat),
                                 "hint": "Possible UTF-8 decoded as Latin-1/CP1252 (mojibake)."})
    newlines = {"crlf": data.count(b"\r\n"), "lf": data.count(b"\n") - data.count(b"\r\n"),
                "cr": data.count(b"\r") - data.count(b"\r\n")}
    return {"bom": bom, "candidates": candidates, "best": best, "mojibake_indicators": mojibake,
            "newlines": newlines, "null_bytes": data.count(0),
            "double_encoding_hint": bool(text and ("Ã" in text or "Â" in text))}


def cmd_encoding(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    res = detect_encoding(data)
    res["file"] = str(path)
    if args.repair:
        fixed, changes = repair_encoding(data, args.repair)
        res["repair_plan"] = {"mode": args.repair, "changes": changes, "output_size": len(fixed),
                              "sha256": hash_bytes(fixed)["sha256"]}
        if args.out:
            from tools_core import write_new_bytes, require_approval
            require_approval(args, env, "repair-write")
            out = Path(args.out)
            write_new_bytes(out, fixed, approve=args.approve)
            res["repair_plan"]["written"] = str(out)
        else:
            res["repair_plan"]["note"] = "Dry-run only. Pass --out <new-file> --approve to write a repaired copy."
    env["result"] = res
    return env


def repair_encoding(data: bytes, mode: str) -> tuple[bytes, list]:
    changes = []
    out = data
    if mode == "mojibake-latin1":
        try:
            text = data.decode("utf-8")
            fixed = text.encode("latin-1").decode("utf-8")
            out = fixed.encode("utf-8")
            changes.append("Reinterpreted UTF-8 text that had been decoded as Latin-1 and re-encoded (classic mojibake fix).")
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            raise RetoolError("repair-not-supported", f"Mojibake round-trip is not lossless for this input: {e}", exit_code=EXIT_USAGE)
    elif mode == "newlines-lf":
        out = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        changes.append("Normalized CRLF/CR to LF.")
    elif mode == "newlines-crlf":
        out = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")
        changes.append("Normalized LF/CR to CRLF.")
    elif mode == "add-bom":
        if not data.startswith(b"\xef\xbb\xbf"):
            out = b"\xef\xbb\xbf" + data
            changes.append("Prepended UTF-8 BOM.")
    elif mode == "remove-bom":
        if data.startswith(b"\xef\xbb\xbf"):
            out = data[3:]
            changes.append("Removed UTF-8 BOM.")
        elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
            out = text.encode("utf-8")
            changes.append("Converted UTF-16 (with BOM) to UTF-8 without BOM.")
    else:
        raise RetoolError("bad-repair-mode", f"Unknown encoding repair mode: {mode}", exit_code=EXIT_USAGE)
    return out, changes


BIDI_CONTROLS = {"\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\u200e", "\u200f"}
INVISIBLE = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u180e"}
CONFUSABLES = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
               "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ѵ": "v", "ⅼ": "l", "ｌ": "l", "０": "0", "Ｏ": "O",
               "ο": "o", "ρ": "p", "α": "a", "ε": "e", "κ": "k", "ν": "v", "τ": "t", "υ": "u", "χ": "x"}


def cmd_unicode(args, env, cfg):
    if args.path:
        path = validate_input_file(args.path, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        text = data.decode("utf-8", errors="replace")
        source = str(path)
    elif args.text is not None:
        text = args.text
        source = "<text>"
    else:
        raise RetoolError("missing-input", "Provide a file path or --text", exit_code=EXIT_USAGE)
    findings = []
    scripts = {}
    for i, ch in enumerate(text):
        cp = ord(ch)
        if ch in BIDI_CONTROLS:
            findings.append({"offset": i, "codepoint": f"U+{cp:04X}", "kind": "bidi-control", "severity": "high",
                             "name": unicodedata.name(ch, "?")})
        elif ch in INVISIBLE:
            findings.append({"offset": i, "codepoint": f"U+{cp:04X}", "kind": "invisible", "severity": "medium",
                             "name": unicodedata.name(ch, "?")})
        elif ch in CONFUSABLES:
            findings.append({"offset": i, "codepoint": f"U+{cp:04X}", "kind": "confusable", "severity": "medium",
                             "looks_like": CONFUSABLES[ch], "name": unicodedata.name(ch, "?")})
        if ch.isalpha():
            try:
                name = unicodedata.name(ch)
                script = name.split()[0]
                scripts[script] = scripts.get(script, 0) + 1
            except ValueError:
                pass
    nfkc = unicodedata.normalize("NFKC", text)
    env["result"] = {
        "source": source, "length": len(text), "findings": findings[:500],
        "script_histogram": scripts, "mixed_script": len([s for s in scripts if s in ("LATIN", "CYRILLIC", "GREEK")]) > 1,
        "normalization": {"NFC": unicodedata.normalize("NFC", text) == text,
                          "NFKC_roundtrip_changes": nfkc != text,
                          "nfkc_preview": nfkc[:200]},
        "note": "Confusable mapping is a small built-in subset; full UTS #39 analysis requires the Unicode data files.",
    }
    env["limitations"].append("Confusable detection uses a built-in subset, not the full Unicode confusables database.")
    return env


# ---------------------------------------------------------------------------
# Binary-to-text codecs
# ---------------------------------------------------------------------------

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + (out or "")


def codec_decode_once(text: str, mode: str) -> tuple[bytes, str]:
    t = text.strip()
    try:
        if mode in ("hex", "base16"):
            return bytes.fromhex(re.sub(r"\s+", "", t)), "hex"
        if mode == "base32":
            return base64.b32decode(t.upper() + "=" * (-len(t) % 8)), "base32"
        if mode == "base58":
            return b58decode(t), "base58"
        if mode == "base64":
            return base64.b64decode(t + "=" * (-len(t) % 4), validate=False), "base64"
        if mode == "base85":
            return base64.b85decode(t), "base85"
        if mode == "ascii85":
            return base64.a85decode(t), "ascii85"
        if mode == "percent":
            from urllib.parse import unquote_to_bytes
            return unquote_to_bytes(t), "percent"
        if mode == "auto":
            return codec_auto(t)
    except (ValueError, binascii.Error) as e:
        raise RetoolError("decode-failed", f"{mode} decode failed: {e}", exit_code=EXIT_USAGE)
    raise RetoolError("bad-mode", f"Unknown decode mode: {mode}", exit_code=EXIT_USAGE)


def codec_auto(t: str) -> tuple[bytes, str]:
    compact = re.sub(r"\s+", "", t)
    if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", compact) and len(compact) % 2 == 0 and len(compact) >= 8:
        return bytes.fromhex(compact), "hex"
    if "%" in t and re.search(r"%[0-9a-fA-F]{2}", t):
        from urllib.parse import unquote_to_bytes
        return unquote_to_bytes(t), "percent"
    if re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact) and len(compact) >= 16 and len(compact) % 4 == 0:
        try:
            dec = base64.b64decode(compact, validate=True)
            return dec, "base64"
        except Exception:
            pass
    if re.fullmatch(r"[A-Z2-7]+={0,6}", compact) and len(compact) >= 16:
        try:
            return base64.b32decode(compact), "base32"
        except Exception:
            pass
    raise RetoolError("decode-failed", "Auto-detection found no confident encoding; specify --mode explicitly.", exit_code=EXIT_USAGE)


def cmd_codec(args, env, cfg):
    if args.input_file:
        path = validate_input_file(args.input_file, cfg)
        raw = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    elif args.text is not None:
        raw = args.text.encode("utf-8")
    else:
        raise RetoolError("missing-input", "Provide --text or --input-file", exit_code=EXIT_USAGE)
    layers = []
    if args.encode:
        to = args.encode
        if to == "hex":
            enc = raw.hex()
        elif to == "base32":
            enc = base64.b32encode(raw).decode()
        elif to == "base58":
            enc = b58encode(raw)
        elif to == "base64":
            enc = base64.b64encode(raw).decode()
        elif to == "base85":
            enc = base64.b85encode(raw).decode()
        elif to == "percent":
            from urllib.parse import quote
            enc = quote(raw, safe="")
        else:
            raise RetoolError("bad-mode", f"Unknown encode target: {to}", exit_code=EXIT_USAGE)
        env["result"] = {"operation": "encode", "to": to, "input_bytes": len(raw), "encoded": enc}
        return env
    mode = args.mode or "auto"
    data = raw
    max_layers = parse_int(args.layers) if args.layers else 4
    for i in range(max_layers):
        text = data.decode("utf-8", errors="strict") if i else raw.decode("utf-8", errors="replace")
        try:
            dec, used = codec_decode_once(text, mode if i == 0 else "auto")
        except RetoolError:
            if i == 0:
                raise
            break
        layers.append({"layer": i + 1, "mode": used, "output_bytes": len(dec)})
        data = dec
        if mode != "auto":
            break
    preview = data[:512]
    env["result"] = {
        "operation": "decode", "layers": layers, "output_bytes": len(data),
        "output_hex": preview.hex(), "output_text_preview": preview.decode("utf-8", errors="replace"),
        "output_sha256": hash_bytes(data)["sha256"],
    }
    if args.out:
        from tools_core import write_new_bytes
        write_new_bytes(Path(args.out), data, approve=getattr(args, "approve", False))
        env["result"]["written"] = args.out
    return env


# ---------------------------------------------------------------------------
# Numeric decoding
# ---------------------------------------------------------------------------

def decode_varints(data: bytes, off: int, kind: str, count: int) -> list:
    out = []
    pos = off
    for _ in range(count):
        if pos >= len(data):
            break
        if kind in ("uleb128", "uleb", "varint"):
            val = 0
            shift = 0
            start = pos
            while True:
                if pos >= len(data):
                    raise RetoolError("truncated-varint", f"Truncated varint at {start}", exit_code=EXIT_USAGE)
                b = data[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                if not b & 0x80:
                    break
                shift += 7
                if shift > 70:
                    raise RetoolError("varint-too-long", f"Varint exceeds 10 bytes at {start}", exit_code=EXIT_USAGE)
            out.append({"offset": start, "bytes": pos - start, "value": val, "kind": kind})
        elif kind in ("sleb128", "sleb"):
            val = 0
            shift = 0
            start = pos
            while True:
                if pos >= len(data):
                    raise RetoolError("truncated-varint", f"Truncated SLEB128 at {start}", exit_code=EXIT_USAGE)
                b = data[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
                if shift > 70:
                    raise RetoolError("varint-too-long", f"SLEB128 exceeds 10 bytes at {start}", exit_code=EXIT_USAGE)
            if shift < 64 and (b & 0x40):
                val |= -(1 << shift)
            out.append({"offset": start, "bytes": pos - start, "value": val, "kind": kind})
        elif kind == "zigzag":
            val = 0
            shift = 0
            start = pos
            while True:
                if pos >= len(data):
                    raise RetoolError("truncated-varint", f"Truncated zigzag at {start}", exit_code=EXIT_USAGE)
                b = data[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                if not b & 0x80:
                    break
                shift += 7
                if shift > 70:
                    raise RetoolError("varint-too-long", "zigzag varint too long", exit_code=EXIT_USAGE)
            out.append({"offset": start, "bytes": pos - start, "value": (val >> 1) ^ -(val & 1), "kind": kind})
        else:
            raise RetoolError("bad-varint-kind", f"Unknown varint kind: {kind}", exit_code=EXIT_USAGE)
    return out


def cmd_numbers(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    size = path.stat().st_size
    off = parse_int(args.offset) if args.offset else 0
    if off < 0 or off >= size:
        raise RetoolError("bad-range", "offset out of range", exit_code=EXIT_USAGE)
    count = parse_int(args.count) if args.count else 1
    data = read_bytes(path, cfg, off, min(size - off, cfg["limits"]["max_read_bytes"]))
    result = {"file": str(path), "offset": off}
    if args.varint:
        result["varints"] = decode_varints(data, 0, args.varint, count)
        env["result"] = result
        return env
    if args.spec:
        layout = _resolve_layout(args.spec)
        rows = annotate_layout(data, layout)
        result["layout"] = args.spec
        result["fields"] = rows
        env["result"] = result
        return env
    if not args.types:
        raise RetoolError("missing-types", "Provide --types (e.g. u32le,f32be), --varint, or --spec", exit_code=EXIT_USAGE)
    decodings = []
    for spec in args.types.split(","):
        spec = spec.strip().lower()
        m = re.fullmatch(r"([uif])(8|16|32|64|128)(le|be)?", spec)
        if not m:
            if spec == "bcd":
                chunk = data[:8]
                digits = "".join(f"{b:02x}" for b in chunk)
                decodings.append({"type": "bcd", "value": digits, "note": "Packed BCD digits; validate against format."})
                continue
            if spec.startswith("fixed"):
                mm = re.fullmatch(r"fixed(\d+)\.(\d+)(le|be)?", spec)
                if not mm:
                    raise RetoolError("bad-type", f"Bad fixed-point spec: {spec}", exit_code=EXIT_USAGE)
                ibits, fbits = int(mm.group(1)), int(mm.group(2))
                total = ibits + fbits
                nbytes = (total + 7) // 8
                endian = mm.group(3) or "le"
                raw = int.from_bytes(data[:nbytes], "little" if endian in ("le", "<") else "big", signed=True)
                decodings.append({"type": spec, "value": raw / (1 << fbits), "raw": raw})
                continue
            raise RetoolError("bad-type", f"Unknown numeric type: {spec}", exit_code=EXIT_USAGE)
        kind, bits, endian = m.group(1), int(m.group(2)), m.group(3) or "le"
        nbytes = bits // 8
        vals = []
        for i in range(count):
            raw = data[i * nbytes:(i + 1) * nbytes]
            if len(raw) < nbytes:
                break
            if kind == "f":
                if bits == 32:
                    vals.append(struct.unpack(("<" if endian == "le" else ">") + "f", raw)[0])
                elif bits == 64:
                    vals.append(struct.unpack(("<" if endian == "le" else ">") + "d", raw)[0])
                elif bits == 16:
                    vals.append(_f16(raw, endian))
                else:
                    raise RetoolError("bad-type", "float supports 16/32/64", exit_code=EXIT_USAGE)
            else:
                vals.append(int.from_bytes(raw, "little" if endian in ("le", "<") else "big", signed=(kind == "i")))
        decodings.append({"type": spec, "bytes_per_value": nbytes, "values": vals})
    result["decodings"] = decodings
    env["result"] = result
    return env


def _f16(raw: bytes, endian: str) -> float:
    v = int.from_bytes(raw, "little" if endian in ("le", "<") else "big")
    sign = -1 if v & 0x8000 else 1
    exp = (v >> 10) & 0x1F
    frac = v & 0x3FF
    if exp == 0:
        return sign * (frac / 1024.0) * (2 ** -14)
    if exp == 31:
        return float("inf") * sign if frac == 0 else float("nan")
    return sign * (1 + frac / 1024.0) * (2 ** (exp - 15))


# ---------------------------------------------------------------------------
# XOR / substitution
# ---------------------------------------------------------------------------

def _printable_score(data: bytes) -> float:
    if not data:
        return 0.0
    good = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return good / len(data)


def _english_score(data: bytes) -> float:
    if not data:
        return 0.0
    text = data.lower()
    freq = b"etaoinshrdlu"
    hits = sum(text.count(bytes([c])) for c in freq)
    spaces = text.count(b" ")
    return _printable_score(data) * 0.6 + (hits / len(data)) * 0.3 + min(spaces / max(len(data) / 6, 1), 1.0) * 0.1


def xor_with_key(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def cmd_xor(args, env, cfg):
    if args.path:
        path = validate_input_file(args.path, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        source = str(path)
    elif args.text is not None:
        data = args.text.encode()
        source = "<text>"
    else:
        raise RetoolError("missing-input", "Provide a file path or --text", exit_code=EXIT_USAGE)
    if args.key:
        key = bytes.fromhex(args.key)
        dec = xor_with_key(data, key)
        env["result"] = {"source": source, "mode": "known-key", "key_hex": key.hex(),
                         "printable_ratio": round(_printable_score(dec), 4),
                         "decoded_preview": dec[:512].decode("utf-8", errors="replace"),
                         "decoded_hex": dec[:256].hex(), "output_bytes": len(dec)}
        if args.out:
            from tools_core import write_new_bytes
            write_new_bytes(Path(args.out), dec, approve=getattr(args, "approve", False))
            env["result"]["written"] = args.out
        return env
    max_keylen = parse_int(args.max_keylen) if args.max_keylen else min(cfg["limits"]["max_xor_key_len"], 16)
    top = parse_int(args.top) if args.top else 5
    sample = data[:8192]
    candidates = []
    for k in range(1, 256):
        dec = xor_with_key(sample, bytes([k]))
        candidates.append({"key_hex": f"{k:02x}", "key_len": 1, "score": round(_english_score(dec), 4),
                           "printable_ratio": round(_printable_score(dec), 4),
                           "preview": dec[:96].decode("utf-8", errors="replace")})
    # repeating keys via per-position single-byte solve
    for klen in range(2, max_keylen + 1):
        key = bytearray()
        for pos in range(klen):
            col = sample[pos::klen]
            best_k, best_s = 0, -1
            for k in range(1, 256):
                s = _english_score(bytes(b ^ k for b in col))
                if s > best_s:
                    best_s, best_k = s, k
            key.append(best_k)
        dec = xor_with_key(sample, bytes(key))
        candidates.append({"key_hex": bytes(key).hex(), "key_len": klen, "score": round(_english_score(dec), 4),
                           "printable_ratio": round(_printable_score(dec), 4),
                           "preview": dec[:96].decode("utf-8", errors="replace")})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    # rotation / caesar for text
    rotations = []
    try:
        text = data.decode("utf-8")
        for shift in (13, 3, -3):
            rot = "".join(chr((ord(c) - (65 if c.isupper() else 97) + shift) % 26 + (65 if c.isupper() else 97))
                          if c.isalpha() and ord(c) < 128 else c for c in text)
            rotations.append({"shift": shift, "preview": rot[:120]})
    except UnicodeDecodeError:
        pass
    env["result"] = {"source": source, "mode": "key-recovery", "candidates": candidates[:top],
                     "rotation_candidates": rotations,
                     "note": "Scores are heuristics over a sample; validate any candidate against known plaintext or structure before use."}
    env["limitations"].append("XOR key recovery is heuristic and limited to short repeating keys; it is not cryptanalysis of modern ciphers.")
    return env


# ---------------------------------------------------------------------------
# Carving
# ---------------------------------------------------------------------------

CARVE_SIGS = [
    (b"\x7fELF", "elf"), (b"MZ", "pe-candidate"), (b"PK\x03\x04", "zip"), (b"\x1f\x8b\x08", "gzip"),
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"%PDF", "pdf"),
    (b"SQLite format 3\x00", "sqlite"), (b"\xca\xfe\xba\xbe", "class-or-fat"), (b"dex\n", "dex"),
    (b"\x00asm", "wasm"), (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07", "rar"),
    (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"), (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"), (b"GIF8", "gif"), (b"RIFF", "riff"),
    (b"\x1aE\xdf\xa3", "matroska"), (b"hsqs", "squashfs"), (b"\xd0\x0d\xfe\xed", "dtb"),
    (b"ANDROID!", "android-boot"), (b"GGUF", "gguf"), (b"\x07\x23\x02\x03", "spirv"),
]


def _carve_blob(data: bytes, off: int, kind: str, max_size: int) -> tuple[bytes, str]:
    if kind == "zip":
        eocd = data.rfind(b"PK\x05\x06", off)
        if eocd >= 0 and eocd + 22 <= len(data):
            comment_len = u16(data, eocd + 20)
            end = min(eocd + 22 + comment_len, len(data))
            return data[off:end], "EOCD-terminated"
    if kind == "png":
        pos = off + 8
        while pos + 8 <= len(data):
            ln = u32(data, pos, "be")
            typ = data[pos + 4:pos + 8]
            pos += 12 + ln
            if typ == b"IEND" or pos > off + max_size:
                break
        return data[off:min(pos, off + max_size)], "chunk-walk to IEND"
    if kind == "jpeg":
        end = data.find(b"\xff\xd9", off + 2)
        if end >= 0:
            return data[off:end + 2], "EOI-terminated"
    if kind == "pdf":
        end = data.rfind(b"%%EOF", off)
        if end >= 0:
            return data[off:end + 5], "EOF-marker"
    if kind == "gzip":
        try:
            d = zlib.decompressobj(31)
            out = d.decompress(data[off:], max_size)
            consumed = len(data) - off - len(d.unused_data)
            return data[off:off + consumed], "gzip stream consumed"
        except zlib.error:
            pass
    if kind == "sqlite":
        if off + 100 <= len(data):
            ps = u16(data, off + 16, "be")
            ps = 65536 if ps == 1 else ps
            pc = u32(data, off + 28, "be")
            if 512 <= ps <= 65536 and 0 < pc < 10_000_000:
                return data[off:min(off + ps * pc, off + max_size)], "page_size*page_count"
    if kind == "elf" and off + 64 <= len(data):
        endian = "le" if data[off + 5] == 1 else "be"
        is64 = data[off + 4] == 2
        if is64:
            shoff = u64(data, off + 0x28, endian)
            shentsize = u16(data, off + 0x3A, endian)
            shnum = u16(data, off + 0x3C, endian)
        else:
            shoff = u32(data, off + 0x20, endian)
            shentsize = u16(data, off + 0x2E, endian)
            shnum = u16(data, off + 0x30, endian)
        end = shoff + shentsize * shnum
        if off < end <= off + max_size:
            return data[off:end], "section-table extent"
    return data[off:min(off + max_size, len(data))], "bounded slice (unvalidated extent)"


def cmd_carve(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    max_hits = cfg["limits"]["max_carve_hits"]
    max_size = min(cfg["limits"]["max_extract_bytes"], cfg["limits"]["max_read_bytes"])
    hits = []
    seen = set()
    for sig, kind in CARVE_SIGS:
        for off in find_all(data, sig, limit=max_hits):
            if off in seen:
                continue
            seen.add(off)
            blob, method = _carve_blob(data, off, kind, max_size)
            ident = identify_bytes(blob, f"carved-{off:x}.{kind}")
            valid = ident["best_match"]["confidence"] in ("high", "medium") and ident["best_match"]["family"] != "unknown"
            hits.append({"offset": off, "kind": kind, "length": len(blob), "extent_method": method,
                         "valid_signature": valid, "identified_as": ident["best_match"]["name"],
                         "sha256": hash_bytes(blob)["sha256"]})
            if len(hits) >= max_hits:
                break
        if len(hits) >= max_hits:
            break
    hits.sort(key=lambda h: h["offset"])
    result = {"file": str(path), "hits": hits, "hit_count": len(hits),
              "note": "Signature hits are candidates; validate with parse before trusting. False positives are possible, especially for short signatures."}
    if args.out:
        outdir = Path(args.out)
        if outdir.exists() and any(outdir.iterdir()):
            from tools_core import require_approval
            require_approval(args, env, "repack-overwrite")
        outdir.mkdir(parents=True, exist_ok=True)
        written = []
        for i, h in enumerate(hits):
            if not h["valid_signature"]:
                continue
            blob, _ = _carve_blob(data, h["offset"], h["kind"], max_size)
            fp = outdir / f"carved-{h['offset']:08x}-{h['kind']}.bin"
            fp.write_bytes(blob)
            written.append(str(fp))
        result["written"] = written
        result["write_note"] = "Only signature-validated candidates were written; unvalidated hits remain report-only."
    env["result"] = result
    if len(hits) >= max_hits:
        env["warnings"].append("Carve hit limit reached; output truncated.")
    return env
