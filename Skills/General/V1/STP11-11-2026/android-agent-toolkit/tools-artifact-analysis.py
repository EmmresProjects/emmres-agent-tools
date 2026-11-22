#!/usr/bin/env python3
"""android-agent-toolkit :: artifact analysis (module 3 of 7).

Pure-Python, standard-library-only analysis of Android artifacts:
  * sniff/detect       - magic-byte + container-content classification
                         (ZIP/APK/AAB/APKS/XAPK/APKM/JAR/DEX/OBB/ELF/PDF/media/keystore/...)
  * zip_health         - central directory, duplicates, traversal, methods, CRCs,
                         alignment (4-byte + 16 KB native page), truncation, nested payloads
  * AXML               - binary AndroidManifest.xml decode: full XML dump + manifest summary
  * ARSC               - resources.arsc structural summary (packages, types, configs)
  * DEX                - header parse with adler32/SHA-1 self-verification, multidex
                         inventory, capped string extraction with regex filter
  * ELF                - Elf32/Elf64 headers, sections, program headers, .dynamic
                         (DT_NEEDED/SONAME/RUNPATH/TEXTREL), dynsym (JNI surface),
                         build-id, ARM attributes, page-size readiness
  * signing            - v1 (JAR/PKCS#7 + minimal X.509) and v2/v3/v4 (APK Signing
                         Block) scheme detection, certificate summary and comparison
  * abi                - ABI coverage, 32/64-bit distinction, libc++ runtime, page size
  * media              - image/audio/video/texture/shader header inspection
  * inventory          - categorized entry listing with size/compression analytics
  * pkginfo            - reconciled package identity across APK/XAPK/APKM/APKS/AAB/dir
  * splits/normalize   - split resolution for a device spec, XAPK/APKM normalization

All functions raise core.ToolkitError with structured codes; nothing here executes
external processes or writes outside caller-validated paths.
"""

import hashlib
import json
import os
import re
import struct
import zlib

import aatk_core as core  # noqa: E402  (registered in sys.modules by the entry point)

# ---------------------------------------------------------------------------
# Magic table
# ---------------------------------------------------------------------------

ELF_MACHINES = {0x02: "SPARC", 0x03: "x86", 0x08: "MIPS", 0x14: "PowerPC",
                0x28: "ARM", 0x3E: "x86-64", 0xB7: "AArch64", 0xF3: "RISC-V"}
ELF_PT = {1: "LOAD", 2: "DYNAMIC", 3: "INTERP", 4: "NOTE", 6: "PHDR", 7: "TLS",
          0x6474E550: "GNU_EH_FRAME", 0x6474E551: "GNU_STACK", 0x6474E552: "GNU_RELRO",
          0x6474E553: "GNU_PROPERTY"}
SHT = {0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB", 4: "RELA", 5: "HASH",
       6: "DYNAMIC", 7: "NOTE", 8: "NOBITS", 9: "REL", 11: "DYNSYM", 14: "INIT_ARRAY",
       15: "FINI_ARRAY", 0x6FFFFFF5: "GNU_ATTRIBUTES", 0x6FFFFFF6: "GNU_HASH",
       0x6FFFFFFD: "GNU_VERNEED", 0x6FFFFFFE: "GNU_VERSYM", 0x70000001: "ANDROID_REL",
       0x70000003: "ANDROID_RELA"}
DT = {1: "NEEDED", 14: "SONAME", 15: "RPATH", 29: "RUNPATH", 22: "TEXTREL",
      30: "FLAGS", 0x6FFFFFFB: "FLAGS_1", 5: "STRTAB", 6: "SYMTAB", 10: "STRSZ",
      11: "SYMENT", 0x6FFFFEF5: "GNU_HASH"}

MAGICS = [
    (0, b"\x7fELF", "elf", "ELF binary"),
    (0, b"dex\n035\x00", "dex", "Dalvik DEX (035)"),
    (0, b"dex\n036\x00", "dex", "Dalvik DEX (036)"),
    (0, b"dex\n037\x00", "dex", "Dalvik DEX (037)"),
    (0, b"dex\n038\x00", "dex", "Dalvik DEX (038)"),
    (0, b"dex\n039\x00", "dex", "Dalvik DEX (039)"),
    (0, b"dey\n036\x00", "dex", "Dalvik optimized DEX (ODEX)"),
    (0, b"vdex", "dex", "Android VDEX container"),
    (0, b"%PDF", "document", "PDF document"),
    (0, b"SQLite format 3\x00", "database", "SQLite database"),
    (0, b"\x89PNG\r\n\x1a\n", "image", "PNG image"),
    (0, b"\xff\xd8\xff", "image", "JPEG image"),
    (0, b"GIF87a", "image", "GIF image (87a)"),
    (0, b"GIF89a", "image", "GIF image (89a)"),
    (0, b"BM", "image", "BMP image"),
    (0, b"fLaC", "audio", "FLAC audio"),
    (0, b"OggS", "audio", "OGG container"),
    (0, b"ID3", "audio", "MP3 audio (ID3v2 tag)"),
    (0, b"\x1f\x8b", "compressed", "gzip stream"),
    (0, b"BZh", "compressed", "bzip2 stream"),
    (0, b"\xfd7zXZ\x00", "compressed", "xz stream"),
    (0, b"7z\xbc\xaf\x27\x1c", "compressed", "7-Zip archive"),
    (0, b"Rar!\x1a\x07", "compressed", "RAR archive"),
    (0, b"\x28\xb5\x2f\xfd", "compressed", "Zstandard stream"),
    (0, b"\xabKTX 11\xbb\r\n\x1a\n", "texture", "Khronos KTX texture (v1)"),
    (0, b"\xabKTX 20\xbb\r\n\x1a\n", "texture", "Khronos KTX2 texture (Basis Universal)"),
    (0, b"DDS \x7c\x00\x00\x00", "texture", "DirectDraw Surface (DDS) texture"),
    (0, b"\x13\xab\xa1\x5c", "texture", "ASTC texture"),
    (0, b"\x03\x52\x56\x50", "texture", "PowerVR PVR v3 texture"),
    (0, b"\x03\x02\x23\x07", "shader", "SPIR-V shader module"),
    (0, b"UnityFS\x00", "gamepack", "Unity asset bundle (UnityFS)"),
    (0, b"UnityWeb\x00", "gamepack", "Unity web bundle (UnityWeb)"),
    (0, b"UnityRaw\x00", "gamepack", "Unity raw bundle (UnityRaw)"),
    (0, b"\xfe\xed\xfe\xed", "keystore", "Java JKS keystore"),
    (0, b"\xfe\xed\xfa\xce", "macho", "Mach-O 32-bit (big endian)"),
    (0, b"\xfe\xed\xfa\xcf", "macho", "Mach-O 64-bit (big endian)"),
    (0, b"\xcf\xfa\xed\xfe", "macho", "Mach-O 64-bit (little endian)"),
    (0, b"\xce\xfa\xed\xfe", "macho", "Mach-O 32-bit (little endian)"),
    (0, b"#!/perfetto-trace\n", "trace", "Perfetto trace (text-proto)"),
    (0, b"PK\x03\x04", "zip", "ZIP container"),
    (0, b"PK\x05\x06", "zip", "ZIP container (empty archive)"),
    (0, b"PK\x07\x08", "zip", "ZIP container (spanned)"),
]

ANDROID_URIS = {"http://schemas.android.com/apk/res/android": "android",
                "http://schemas.android.com/apk/prv/res/android": "android-prv",
                "http://schemas.android.com/apk/res-auto": "app",
                "http://schemas.android.com/tools": "tools"}

MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp3", ".ogg", ".wav",
              ".flac", ".m4a", ".aac", ".mp4", ".m4v", ".mkv", ".webm", ".ktx", ".dds",
              ".pvr", ".astc", ".spv", ".ttf", ".otf"}


def _head(path, n=4096):
    with open(path, "rb") as fh:
        return fh.read(n)


def sniff_bytes(head, size=None, name=None):
    """Classify from the first bytes. Returns (family, type_label, confidence)."""
    for off, magic, family, label in MAGICS:
        if head[off:off + len(magic)] == magic:
            return family, label, "high"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "WAV audio (RIFF)", "high"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "WebP image (RIFF)", "high"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video", "MP4-family video (ISO BMFF)", "high"
    if head[0:4] == b"\x1a\x45\xdf\xa3":
        return "video", "Matroska/WebM video (EBML)", "high"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "audio", "MP3 audio (MPEG frame sync)", "medium"
    if name and name.lower().endswith(".realm") and head[0:8] == b"\x00" * 8:
        return "database", "Realm database (heuristic)", "low"
    if head[0:2] == b"\x30\x82" and name and name.lower().endswith((".p12", ".pfx", ".keystore")):
        return "keystore", "PKCS#12 container (heuristic)", "medium"
    if head[0:4] == b"\x30\x82\x00\x00":
        return "unknown", None, "low"
    printable = sum(1 for b in head[:512] if 9 <= b <= 13 or 32 <= b <= 126)
    if head[:512] and printable / max(1, min(512, len(head))) > 0.9:
        text = head[:2048].decode("utf-8", "replace").lstrip()
        if text.startswith("<?xml") or text.startswith("<"):
            return "text", "XML/text document", "medium"
        if text.startswith("{") or text.startswith("["):
            return "text", "JSON/text document", "medium"
        return "text", "plain text", "medium"
    return "unknown", None, "low"


def classify_zip_container(names, path=None):
    """Classify a ZIP by its entry names. Returns (type, subtype, confidence, details)."""
    s = set(names)
    sample = names[:4000]
    has_manifest = "AndroidManifest.xml" in s
    has_dex = any(re.match(r"^classes\d*\.dex$", n) for n in sample)
    has_arsc = "resources.arsc" in s
    has_base_dir = any(n.startswith("base/") for n in sample)
    details = {}
    lower = {n.lower() for n in sample}
    if "bundleconfig.pb" in lower or (has_base_dir and any(n.startswith("base/manifest/") for n in sample)):
        modules = sorted({n.split("/")[0] for n in sample if "/" in n and not n.startswith(("META-INF",))})
        details["modules"] = modules[:50]
        details["asset_packs"] = [m for m in modules if m.startswith("asset_pack")]
        return "AAB", "Android App Bundle", "high", details
    if "manifest.json" in s and any(n.lower().endswith(".apk") for n in sample):
        return "XAPK", "APK set with manifest.json (XAPK)", "high", details
    if "toc.pb" in s and any(n.startswith("split") for n in sample):
        return "APKS", "bundletool APK set (toc.pb)", "high", details
    if "base.apk" in s and any(n.startswith("config.") for n in sample):
        subtype = "APKM split set" if ("info.json" in s or "icon.png" in s) else "split APK set (base + configs)"
        return "APKM" if "info.json" in s else "APKS", subtype, "medium" if "info.json" not in s else "high", details
    if any(n.startswith("splits/") and n.endswith(".apk") for n in sample):
        return "APKS", "APK set with splits/ layout", "medium", details
    if has_manifest and (has_dex or has_arsc):
        if "meta-inf/com/android/build/gradle/app-metadata.properties" in lower:
            details["built_with"] = "Android Gradle Plugin (app-metadata.properties present)"
        if any(n.startswith("lib/") for n in sample):
            details["abis"] = sorted({n.split("/")[1] for n in sample if n.startswith("lib/") and n.count("/") >= 2})
        if any(n.lower().endswith(".apk") for n in sample):
            details["nested_apks"] = [n for n in sample if n.lower().endswith(".apk")][:10]
        return "APK", "Android package", "high", details
    if has_manifest and not has_dex and not has_arsc:
        return "APK", "Android package (manifest present, no DEX/ARSC - unusual)", "medium", details
    if "meta-inf/manifest.mf" in lower:
        if any(n.startswith("WEB-INF/") for n in sample):
            return "WAR", "Java web archive", "high", details
        return "JAR", "Java archive", "high", details
    base = os.path.basename(path or "").lower()
    if base.endswith(".obb") or re.match(r"^(main|patch)\.\d+\..+\.obb$", base):
        return "OBB", "Android expansion file (ZIP container)", "medium", details
    return "ZIP", "generic ZIP archive", "medium", details


def detect_file(path, cfg):
    path = core.check_input_path(path, kind="file", cfg=cfg, label="artifact")
    size = os.path.getsize(path)
    head = _head(path, 8192)
    family, label, conf = sniff_bytes(head, size, path)
    result = {"path": path, "name": os.path.basename(path), "size_bytes": size,
              "declared_extension": os.path.splitext(path)[1].lower(),
              "family": family, "confidence": conf, "details": {}, "warnings": []}
    if family == "zip":
        try:
            with core.open_zip(path) as zf:
                names = zf.namelist()
                t, sub, c2, details = classify_zip_container(names, path)
                result.update({"type": t, "subtype": sub, "confidence": c2, "details": details,
                               "entry_count": len(names)})
                if t in ("XAPK", "APKM") and "manifest.json" in names:
                    try:
                        mj = json.loads(zf.read("manifest.json").decode("utf-8", "replace"))
                        result["details"]["xapk_manifest"] = {
                            k: mj.get(k) for k in ("name", "package", "version_code", "version_name",
                                                   "min_sdk_version", "target_sdk_version", "permissions")
                            if k in mj}
                    except (ValueError, KeyError):
                        result["warnings"].append("manifest.json present but unparseable")
                if t == "APKM" and "info.json" in names:
                    try:
                        ij = json.loads(zf.read("info.json").decode("utf-8", "replace"))
                        result["details"]["apkm_info"] = {k: ij.get(k) for k in
                                                          ("pkg", "versionName", "versionCode", "title") if k in ij}
                    except (ValueError, KeyError):
                        result["warnings"].append("info.json present but unparseable")
        except core.ToolkitError as exc:
            result.update({"type": "MALFORMED_ZIP", "subtype": "ZIP magic present but container unreadable",
                           "confidence": "high", "details": {"error": exc.message}})
            result["warnings"].append("Misleading extension risk: ZIP magic present, central directory broken.")
        return result
    if family == "dex":
        result["type"] = "DEX"
        result["subtype"] = label
        try:
            hdr = dex_header(head if size >= 0x70 else core.read_bytes(path, length=0x70), path)
            result["details"] = {"dex_version": hdr.get("version"), "class_defs": hdr.get("class_defs_size"),
                                 "method_ids": hdr.get("method_ids_size"), "file_size_field": hdr.get("file_size")}
        except core.ToolkitError as exc:
            result["warnings"].append("DEX header invalid: %s" % exc.message)
        return result
    if family == "elf":
        result["type"] = "ELF"
        try:
            info = elf_summary(path, cfg)
            result["subtype"] = "ELF %s %s (%s)" % (info["elf_class"], info["machine"], info["elf_type"])
            result["details"] = {"machine": info["machine"], "elf_class": info["elf_class"],
                                 "type": info["elf_type"], "soname": info.get("soname")}
        except core.ToolkitError as exc:
            result["subtype"] = label
            result["warnings"].append("ELF parse failed: %s" % exc.message)
        return result
    if family == "keystore":
        result["type"] = "KEYSTORE"
        result["subtype"] = label
        return result
    if label:
        result["type"] = family.upper()
        result["subtype"] = label
        if family in ("image", "audio", "video", "texture", "shader"):
            try:
                result["details"] = media_details(path, head)
            except core.ToolkitError:
                pass
        return result
    result["type"] = "UNKNOWN"
    result["subtype"] = "unrecognized binary (first bytes: %s)" % head[:8].hex()
    result["confidence"] = "low"
    return result


# ---------------------------------------------------------------------------
# ZIP health
# ---------------------------------------------------------------------------

def _find_eocd(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 66000))
        tail = fh.read()
    idx = tail.rfind(b"PK\x05\x06")
    if idx < 0:
        return None, size
    return size - len(tail) + idx, size


def zip_health(path, cfg, do_crc=True, do_align=True):
    path = core.check_input_path(path, kind="file", cfg=cfg, label="archive")
    size = os.path.getsize(path)
    max_entries = core.limit(cfg, "max_zip_entries", 200000)
    crc_cap = core.limit(cfg, "max_crc_bytes_per_entry", 256 << 20)
    issues = []
    report = {"path": path, "size_bytes": size, "issues": issues,
              "entry_count": 0, "healthy": True}

    eocd_off, _ = _find_eocd(path)
    if eocd_off is None:
        issues.append({"severity": "critical", "code": "no-eocd",
                       "message": "End-of-central-directory record not found; archive is truncated or not a ZIP."})
        report["healthy"] = False
        report["_text"] = ["CRITICAL: no end-of-central-directory record; not a usable ZIP."]
        return report
    with open(path, "rb") as fh:
        fh.seek(eocd_off)
        eocd = fh.read(22)
    disk_num, disk_cd, cnt_disk, cnt_total, cd_size, cd_off, comm_len = struct.unpack("<HHHHIIH", eocd[4:22])
    zip64 = cnt_total == 0xFFFF or cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF
    report["eocd"] = {"offset": eocd_off, "entries": cnt_total, "cd_size": cd_size,
                      "cd_offset": cd_off, "zip64": zip64, "comment_length": comm_len}
    if zip64 and eocd_off >= 20:
        with open(path, "rb") as fh:
            fh.seek(eocd_off - 20)
            loc = fh.read(20)
        if loc[0:4] == b"PK\x06\x07":
            z64_off = struct.unpack("<Q", loc[8:16])[0]
            fh = open(path, "rb")
            fh.seek(z64_off)
            z64 = fh.read(56)
            fh.close()
            if z64[0:4] == b"PK\x06\x06":
                cnt_total = struct.unpack("<Q", z64[32:40])[0]
                cd_size = struct.unpack("<Q", z64[40:48])[0]
                cd_off = struct.unpack("<Q", z64[48:56])[0]
                report["eocd"].update({"entries": cnt_total, "cd_size": cd_size, "cd_offset": cd_off})
    if cd_off + cd_size > size:
        issues.append({"severity": "critical", "code": "cd-truncated",
                       "message": "Central directory extends past end of file (truncation)."})
    # APK signing block check (must sit directly before the central directory)
    if cd_off >= 24:
        with open(path, "rb") as fh:
            fh.seek(cd_off - 16)
            magic = fh.read(16)
        report["apk_signing_block"] = magic == b"APK Sig Block 42"

    try:
        zf = core.open_zip(path)
    except core.ToolkitError as exc:
        issues.append({"severity": "critical", "code": "zip-unreadable", "message": exc.message})
        report["healthy"] = False
        return report
    with zf:
        infos = zf.infolist()
        report["entry_count"] = len(infos)
        if len(infos) != cnt_total and not zip64:
            issues.append({"severity": "warning", "code": "entry-count-mismatch",
                           "message": "EOCD says %d entries, directory lists %d." % (cnt_total, len(infos))})
        if len(infos) > max_entries:
            issues.append({"severity": "warning", "code": "entry-limit",
                           "message": "Entry count above configured limit %d; analysis continues but caps apply." % max_entries})
        seen = {}
        dupes, traversal, weird_methods, unaligned4, unaligned_page, nested, stored_so = [], [], set(), [], [], [], 0
        methods = {}
        compressed_total = 0
        crc_checked = crc_skipped = crc_bad = 0
        lh_fh = open(path, "rb") if do_align else None
        for i in infos:
            methods[i.compress_type] = methods.get(i.compress_type, 0) + 1
            compressed_total += i.compress_size
            n = i.filename
            if n in seen:
                dupes.append(n)
            seen[n] = True
            if n.startswith("/") or n.startswith("\\") or ".." in n.replace("\\", "/").split("/") or "\\" in n:
                traversal.append(n)
            if i.compress_type not in (0, 8):
                weird_methods.add(i.compress_type)
            if n.lower().endswith((".apk", ".jar", ".zip", ".dex", ".obb")):
                nested.append(n)
            if do_crc and i.file_size <= crc_cap and i.compress_type in (0, 8) and not i.is_dir():
                try:
                    with zf.open(i, "r") as fh:
                        crc = 0
                        while True:
                            b = fh.read(1 << 20)
                            if not b:
                                break
                            crc = zlib.crc32(b, crc)
                    crc_checked += 1
                    if (crc & 0xFFFFFFFF) != i.CRC:
                        crc_bad += 1
                        issues.append({"severity": "critical", "code": "crc-mismatch",
                                       "message": "CRC mismatch (corruption): %s" % n})
                except (zlib.error, OSError, EOFError) as exc:
                    crc_bad += 1
                    issues.append({"severity": "critical", "code": "decompress-failed",
                                   "message": "Cannot decompress %s: %s" % (n, exc)})
            elif do_crc and i.file_size > crc_cap:
                crc_skipped += 1
            # alignment: parse local header
            if lh_fh is not None and i.compress_type == 0 and not i.is_dir():
                lh_fh.seek(i.header_offset)
                lh = lh_fh.read(30)
                if len(lh) == 30 and lh[0:4] == b"PK\x03\x04":
                    nlen, elen = struct.unpack("<HH", lh[26:30])
                    data_off = i.header_offset + 30 + nlen + elen
                    if data_off % 4 != 0:
                        unaligned4.append(n)
                    if re.match(r"^lib/[^/]+/[^/]+\.so$", n):
                        stored_so += 1
                        if data_off % 16384 != 0 and data_off % 4096 == 0:
                            unaligned_page.append(n)
        if lh_fh is not None:
            lh_fh.close()
        if dupes:
            issues.append({"severity": "warning", "code": "duplicate-entries",
                           "message": "%d duplicate entry names (first wins on extract)." % len(dupes),
                           "entries": dupes[:20]})
        if traversal:
            issues.append({"severity": "critical", "code": "path-traversal",
                           "message": "%d entries with absolute, traversal or backslash names." % len(traversal),
                           "entries": traversal[:20]})
        if weird_methods:
            issues.append({"severity": "info", "code": "unusual-compression",
                           "message": "Compression methods beyond stored/deflate: %s" % sorted(weird_methods)})
        if crc_bad == 0 and crc_checked:
            report["crc"] = {"checked": crc_checked, "skipped_over_cap": crc_skipped, "bad": 0}
        else:
            report["crc"] = {"checked": crc_checked, "skipped_over_cap": crc_skipped, "bad": crc_bad}
        if unaligned4:
            issues.append({"severity": "warning", "code": "unaligned-4byte",
                           "message": "%d stored entries are not 4-byte aligned (zipalign needed before signing)." % len(unaligned4),
                           "entries": unaligned4[:20]})
        if unaligned_page:
            issues.append({"severity": "warning", "code": "unaligned-page-native",
                           "message": "%d stored native libs are 4 KB- but not 16 KB-page aligned." % len(unaligned_page),
                           "entries": unaligned_page[:20]})
        report["native_stored_libs"] = stored_so
        if nested:
            issues.append({"severity": "info", "code": "nested-payloads",
                           "message": "%d nested archive/dex payloads." % len(nested), "entries": nested[:10]})
        last_end = max((i.header_offset + i.compress_size for i in infos if not i.is_dir()), default=0)
        if last_end > size:
            issues.append({"severity": "critical", "code": "truncated-entry",
                           "message": "Entry data extends past end of file (truncation)."})
        report["compression_methods"] = {("stored" if k == 0 else "deflate" if k == 8 else "method-%d" % k): v
                                         for k, v in sorted(methods.items())}
        report["compressed_bytes_total"] = compressed_total
    report["healthy"] = not any(i["severity"] == "critical" for i in issues)
    summary = ["entries: %d, healthy: %s" % (report["entry_count"], report["healthy"])]
    for i in issues[:12]:
        summary.append("[%s] %s: %s" % (i["severity"].upper(), i["code"], i["message"]))
    if len(issues) > 12:
        summary.append("... %d more issues (use --json)" % (len(issues) - 12))
    if not issues:
        summary.append("no structural issues found")
    report["_text"] = summary
    return report


# ---------------------------------------------------------------------------
# AXML (binary XML) parser
# ---------------------------------------------------------------------------

CHUNK_STRING_POOL = 0x0001
CHUNK_RESOURCE_MAP = 0x0180
CHUNK_START_NS = 0x0100
CHUNK_END_NS = 0x0101
CHUNK_START_ELEMENT = 0x0102
CHUNK_END_ELEMENT = 0x0103
CHUNK_CDATA = 0x0104

TYPE_NULL, TYPE_REFERENCE, TYPE_ATTRIBUTE, TYPE_STRING = 0x00, 0x01, 0x02, 0x03
TYPE_FLOAT, TYPE_DIMENSION, TYPE_FRACTION = 0x04, 0x05, 0x06
TYPE_INT_DEC, TYPE_INT_HEX, TYPE_INT_BOOL = 0x10, 0x11, 0x12
TYPE_INT_COLOR_ARGB8, TYPE_INT_COLOR_RGB8 = 0x1C, 0x1D
TYPE_INT_COLOR_ARGB4, TYPE_INT_COLOR_RGB4 = 0x1E, 0x1F

DIM_UNITS = ["px", "dp", "sp", "pt", "in", "mm"]
RADIX_MULT = {0: 1.0, 1: 1.0 / (1 << 8), 2: 1.0 / (1 << 16), 3: 1.0 / (1 << 24)}

# resource-id fallbacks for the most important manifest attributes.
# Attribute names are normally present in the string pool; these ids are a
# safety net only, restricted to values that are stable public AOSP ids.
RES_ID_NAMES = {
    0x01010000: "theme", 0x01010001: "label", 0x01010002: "icon",
    0x01010003: "name", 0x01010006: "permission", 0x0101000F: "debuggable",
    0x01010010: "exported", 0x01010011: "process", 0x01010018: "authorities",
    0x0101020C: "minSdkVersion", 0x0101021B: "versionCode", 0x0101021C: "versionName",
    0x01010270: "targetSdkVersion", 0x01010271: "maxSdkVersion", 0x01010281: "glEsVersion",
}


class AxmlError(core.ToolkitError):
    def __init__(self, message):
        super().__init__("E_PARSE", "AXML parse error: %s" % message)


class AxmlDocument(object):
    """Minimal, bounds-checked binary Android XML parser."""

    def __init__(self, data):
        self.data = data
        self.strings = []
        self.resource_ids = []
        self.namespaces = {}   # uri -> prefix
        self.root = None
        self.warnings = []
        self._parse()

    # -- string pool ---------------------------------------------------------
    def _parse_string_pool(self, base, hdr):
        try:
            (string_count, style_count, flags, strings_start,
             styles_start) = struct.unpack("<5I", self.data[base + 8:base + 28])
            utf8 = bool(flags & (1 << 8))
            if string_count > 1_000_000:
                raise AxmlError("implausible string count %d" % string_count)
            offs = (struct.unpack("<%dI" % string_count,
                                  self.data[base + 28:base + 28 + 4 * string_count])
                    if string_count else [])
        except struct.error as exc:
            raise AxmlError("string pool header unreadable: %s" % exc)
        pool_base = base + strings_start
        out = []
        for o in offs:
            p = pool_base + o
            try:
                if utf8:
                    n = self.data[p]
                    p += 2 if n & 0x80 else 1
                    blen = self.data[p]
                    p += 2 if blen & 0x80 else 1
                    if blen & 0x80:
                        blen = ((blen & 0x7F) << 8) | self.data[p - 1]
                    out.append(self.data[p:p + blen].decode("utf-8", "replace"))
                else:
                    n = struct.unpack_from("<H", self.data, p)[0]
                    p += 4 if n & 0x8000 else 2
                    if n & 0x8000:
                        n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", self.data, p - 2)[0]
                    out.append(self.data[p:p + 2 * n].decode("utf-16-le", "replace"))
            except (IndexError, struct.error, UnicodeDecodeError):
                out.append("")
                if len(self.warnings) < 5:
                    self.warnings.append("string pool entry out of bounds at offset %d" % o)
        return out

    # -- main walk ------------------------------------------------------------
    def _parse(self):
        d = self.data
        if len(d) < 8:
            raise AxmlError("file shorter than 8 bytes")
        ctype, hdr_size, file_size = struct.unpack("<HHI", d[0:8])
        if ctype != 0x0003:
            raise AxmlError("magic 0x%04x is not RES_XML_TYPE (0x0003); not binary Android XML" % ctype)
        if file_size > len(d):
            self.warnings.append("declared size %d exceeds available %d bytes" % (file_size, len(d)))
        pos = 8
        stack = []
        root_holder = {}
        guard = 0
        while pos + 8 <= len(d) and guard < 5_000_000:
            guard += 1
            ctype, chdr, csize = struct.unpack("<HHI", d[pos:pos + 8])
            if csize < 8 or pos + csize > len(d) + 0:
                break
            if ctype == CHUNK_STRING_POOL and not self.strings:
                self.strings = self._parse_string_pool(pos, csize)
            elif ctype == CHUNK_RESOURCE_MAP:
                cnt = (csize - 8) // 4
                self.resource_ids = list(struct.unpack("<%dI" % cnt, d[pos + 8:pos + 8 + 4 * cnt])) if cnt else []
            elif ctype == CHUNK_START_NS and csize >= 24:
                prefix, uri = struct.unpack("<II", d[pos + 16:pos + 24])
                if uri < len(self.strings) and prefix < len(self.strings):
                    self.namespaces[self.strings[uri]] = self.strings[prefix]
            elif ctype == CHUNK_START_ELEMENT and csize >= 36:
                ns_i, name_i = struct.unpack("<II", d[pos + 16:pos + 24])
                attr_start, attr_size, attr_count = struct.unpack("<HHH", d[pos + 24:pos + 30])
                node = {"tag": self._s(name_i), "ns": self._s(ns_i), "attrs": [], "children": [], "text": None,
                        "line": struct.unpack("<I", d[pos + 8:pos + 12])[0]}
                # attrExt starts at pos+16; attributeStart is relative to attrExt (normally 20)
                base = pos + 16 + (attr_start if 12 <= attr_start <= 0x100 else 20)
                for a in range(attr_count):
                    o = base + a * (attr_size or 20)
                    if o + 20 > len(d):
                        break
                    a_ns, a_name, a_raw = struct.unpack("<III", d[o:o + 12])
                    _sz, _res0, a_type = struct.unpack("<HBB", d[o + 12:o + 16])
                    a_data = struct.unpack("<I", d[o + 16:o + 20])[0]
                    raw_val = self._s(a_raw) if a_raw != 0xFFFFFFFF else None
                    node["attrs"].append({"ns": self._s(a_ns), "name": self._attr_name(a_name),
                                          "raw": raw_val, "type": a_type, "data": a_data,
                                          "name_index": a_name})
                parent = stack[-1] if stack else None
                if parent is None:
                    if "root" not in root_holder:
                        root_holder["root"] = node
                else:
                    parent["children"].append(node)
                stack.append(node)
            elif ctype == CHUNK_END_ELEMENT:
                if stack:
                    stack.pop()
            elif ctype == CHUNK_CDATA and csize >= 12:
                idx = struct.unpack("<I", d[pos + 8:pos + 12])[0]
                if stack:
                    stack[-1]["text"] = self._s(idx)
            pos += csize
        self.root = root_holder.get("root")
        if self.root is None:
            raise AxmlError("no root element found")

    def _s(self, idx):
        if idx == 0xFFFFFFFF or idx is None:
            return None
        return self.strings[idx] if 0 <= idx < len(self.strings) else None

    def _attr_name(self, name_index):
        name = self._s(name_index)
        if name:
            return name
        if name_index < len(self.resource_ids):
            rid = self.resource_ids[name_index]
            if rid in RES_ID_NAMES:
                return RES_ID_NAMES[rid]
        return "attr_0x%x" % name_index

    # -- rendering ------------------------------------------------------------
    def value_text(self, attr):
        t, data = attr["type"], attr["data"]
        if attr.get("raw") is not None and t == TYPE_STRING:
            return attr["raw"]
        if t == TYPE_STRING:
            return self._s(data) or ""
        if t == TYPE_INT_DEC:
            return str(struct.unpack("<i", struct.pack("<I", data))[0])
        if t == TYPE_INT_HEX:
            return "0x%x" % data
        if t == TYPE_INT_BOOL:
            return "true" if data != 0 else "false"
        if t == TYPE_REFERENCE:
            return "@0x%08x" % data
        if t == TYPE_ATTRIBUTE:
            return "?0x%08x" % data
        if t == TYPE_FLOAT:
            return repr(struct.unpack("<f", struct.pack("<I", data))[0])
        if t == TYPE_DIMENSION or t == TYPE_FRACTION:
            val = struct.unpack("<i", struct.pack("<I", data >> 8))[0] * RADIX_MULT[(data >> 4) & 0x3]
            unit = DIM_UNITS[data & 0xF] if t == TYPE_DIMENSION else "%"
            if val == int(val):
                val = int(val)
            return "%s%s" % (val, unit)
        if t in (TYPE_INT_COLOR_ARGB8, TYPE_INT_COLOR_RGB8, TYPE_INT_COLOR_ARGB4, TYPE_INT_COLOR_RGB4):
            return "#%08x" % data
        if t == TYPE_NULL:
            return ""
        return "0x%08x" % data

    def to_xml(self, indent=0):
        out = ['<?xml version="1.0" encoding="utf-8"?>']
        if self.warnings:
            out.append("<!-- parser warnings: %s -->" % "; ".join(self.warnings))
        self._render(self.root, 0, out, root=True)
        return "\n".join(out) + "\n"

    def _render(self, node, depth, out, root=False):
        pad = "  " * depth
        attrs = []
        if root:
            for uri, prefix in sorted(self.namespaces.items(), key=lambda kv: kv[1]):
                attrs.append('xmlns:%s="%s"' % (prefix, _xml_escape(uri)))
        for a in node["attrs"]:
            prefix = self._prefix_for(a["ns"])
            nm = (prefix + ":" if prefix else "") + a["name"]
            attrs.append('%s="%s"' % (nm, _xml_escape(self.value_text(a))))
        tag = node["tag"] or "unknown"
        line = "%s<%s%s" % (pad, tag, (" " + " ".join(attrs)) if attrs else "")
        if not node["children"] and not node["text"]:
            out.append(line + " />")
            return
        out.append(line + ">")
        if node["text"]:
            out.append("%s  %s" % (pad, _xml_escape(node["text"])))
        for c in node["children"]:
            self._render(c, depth + 1, out)
        out.append("%s</%s>" % (pad, tag))

    def _prefix_for(self, uri):
        if not uri:
            return ""
        if uri in self.namespaces:
            return self.namespaces[uri]
        if uri in ANDROID_URIS:
            return ANDROID_URIS[uri]
        return ""

    # -- queries --------------------------------------------------------------
    def find_all(self, tag, node=None):
        node = node or self.root
        out = []
        if node is None:
            return out
        if node["tag"] == tag:
            out.append(node)
        for c in node["children"]:
            out.extend(self.find_all(tag, c))
        return out

    def attr(self, node, name, default=None):
        for a in node["attrs"]:
            if a["name"] == name:
                return self.value_text(a)
        return default


def _xml_escape(s):
    if s is None:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def parse_axml(data):
    return AxmlDocument(data)


ANDROID_NS = "http://schemas.android.com/apk/res/android"

PROTECTION_LEVELS = {0: "normal", 1: "dangerous", 2: "signature", 3: "signatureOrSystem"}


def manifest_summary(doc, cfg=None):
    """Extract the operationally important manifest facts from a parsed AXML doc."""
    root = doc.root
    if root["tag"] != "manifest":
        return {"error": "root element is %s, not manifest" % root["tag"]}
    m = root
    summary = {
        "package": doc.attr(m, "package"),
        "split": doc.attr(m, "split"),
        "versionCode": doc.attr(m, "versionCode"),
        "versionName": doc.attr(m, "versionName"),
        "sharedUserId": doc.attr(m, "sharedUserId"),
        "installLocation": doc.attr(m, "install-location"),
    }
    sdk_nodes = doc.find_all("uses-sdk")
    if sdk_nodes:
        summary["minSdkVersion"] = doc.attr(sdk_nodes[0], "minSdkVersion")
        summary["targetSdkVersion"] = doc.attr(sdk_nodes[0], "targetSdkVersion")
        summary["maxSdkVersion"] = doc.attr(sdk_nodes[0], "maxSdkVersion")
    apps = doc.find_all("application")
    app = apps[0] if apps else None
    if app:
        summary["application"] = {
            "label": doc.attr(app, "label"),
            "debuggable": doc.attr(app, "debuggable"),
            "allowBackup": doc.attr(app, "allowBackup"),
            "fullBackupContent": doc.attr(app, "fullBackupContent"),
            "dataExtractionRules": doc.attr(app, "dataExtractionRules"),
            "extractNativeLibs": doc.attr(app, "extractNativeLibs"),
            "usesCleartextTraffic": doc.attr(app, "usesCleartextTraffic"),
            "networkSecurityConfig": doc.attr(app, "networkSecurityConfig"),
            "requestLegacyExternalStorage": doc.attr(app, "requestLegacyExternalStorage"),
            "appComponentFactory": doc.attr(app, "appComponentFactory"),
            "testOnly": doc.attr(app, "testOnly"),
        }
        summary["application"] = {k: v for k, v in summary["application"].items() if v is not None}
    perms = []
    for tag in ("uses-permission", "uses-permission-sdk-23"):
        for n in doc.find_all(tag):
            perms.append({"name": doc.attr(n, "name"), "tag": tag, "maxSdk": doc.attr(n, "maxSdkVersion")})
    summary["uses_permissions"] = perms
    summary["defines_permissions"] = []
    for n in doc.find_all("permission"):
        lvl = doc.attr(n, "protectionLevel")
        try:
            lvl_txt = PROTECTION_LEVELS.get(int(lvl, 0) & 0xF, "flags:0x%s" % lvl) if lvl else "normal"
        except ValueError:
            lvl_txt = lvl
        summary["defines_permissions"].append({"name": doc.attr(n, "name"), "protectionLevel": lvl_txt})
    summary["uses_features"] = []
    for n in doc.find_all("uses-feature"):
        gles = doc.attr(n, "glEsVersion")
        gles_txt = None
        if gles:
            try:
                v = int(gles, 0)
                gles_txt = "%d.%d" % (v >> 16, v & 0xFFFF)
            except ValueError:
                gles_txt = gles
        summary["uses_features"].append({"name": doc.attr(n, "name"),
                                         "required": doc.attr(n, "required", "true"),
                                         "glEsVersion": gles_txt})
    summary["uses_libraries"] = [{"name": doc.attr(n, "name"), "required": doc.attr(n, "required", "true")}
                                 for n in doc.find_all("uses-library")]
    components = {}
    launcher = []
    PLURAL = {"activity": "activities", "activity-alias": "activity_aliases",
              "service": "services", "receiver": "receivers", "provider": "providers"}
    for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
        lst = []
        for n in doc.find_all(tag):
            c = {"name": doc.attr(n, "name"), "exported": doc.attr(n, "exported"),
                 "permission": doc.attr(n, "permission")}
            if tag == "provider":
                c["authorities"] = doc.attr(n, "authorities")
                c["grantUriPermissions"] = doc.attr(n, "grantUriPermissions")
            filters = []
            for f in n["children"]:
                if f["tag"] != "intent-filter":
                    continue
                fi = {"actions": [doc.attr(a, "name") for a in f["children"] if a["tag"] == "action"],
                      "categories": [doc.attr(a, "name") for a in f["children"] if a["tag"] == "category"],
                      "data": [], "autoVerify": doc.attr(f, "autoVerify")}
                for dd in f["children"]:
                    if dd["tag"] == "data":
                        fi["data"].append({k: doc.attr(dd, k) for k in ("scheme", "host", "port", "path", "pathPrefix", "pathPattern", "mimeType")
                                           if doc.attr(dd, k)})
                fi["data"] = [x for x in fi["data"] if x]
                filters.append(fi)
                if "android.intent.action.MAIN" in fi["actions"] and "android.intent.category.LAUNCHER" in fi["categories"]:
                    launcher.append(c["name"])
            if filters:
                c["intent_filters"] = filters
            lst.append(c)
        if lst:
            components[PLURAL[tag]] = lst
    summary["components"] = components
    summary["launcher_activities"] = launcher
    queries = []
    for q in doc.find_all("queries"):
        for n in q["children"]:
            if n["tag"] == "package":
                queries.append({"package": doc.attr(n, "name")})
            elif n["tag"] == "intent":
                queries.append({"intent": [doc.attr(a, "name") for a in n["children"] if a["tag"] == "action"]})
            elif n["tag"] == "provider":
                queries.append({"provider": doc.attr(n, "authorities")})
    summary["queries"] = queries
    return summary


def _to_int(v):
    try:
        return int(str(v), 0)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# resources.arsc structural summary
# ---------------------------------------------------------------------------

def arsc_summary(path_or_bytes, name="resources.arsc"):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else core.read_bytes(path_or_bytes)
    out = {"name": name, "size_bytes": len(data), "packages": [], "warnings": []}
    try:
        ctype, hdr, size, pkg_count = struct.unpack("<HHII", data[0:12])
        if ctype != 0x0002:
            raise core.ToolkitError("E_PARSE", "chunk type 0x%04x is not RES_TABLE_TYPE" % ctype)
        out["package_count"] = pkg_count
        pos = hdr
        global_strings = []
        while pos + 8 <= len(data):
            t, h, cs = struct.unpack("<HHI", data[pos:pos + 8])
            if cs < 8 or pos + cs > len(data):
                out["warnings"].append("chunk at %d truncated; stopping" % pos)
                break
            if t == CHUNK_STRING_POOL and not global_strings:
                doclike = _StringPoolReader(data, pos)
                global_strings = doclike.strings
                out["global_string_count"] = len(global_strings)
            elif t == 0x0200:  # PACKAGE
                pkg = _parse_arsc_package(data, pos, cs, out)
                if pkg:
                    out["packages"].append(pkg)
            pos += cs
    except core.ToolkitError:
        raise
    except (struct.error, IndexError) as exc:
        out["warnings"].append("structural parse stopped early: %s" % exc)
    return out


class _StringPoolReader(object):
    def __init__(self, data, base):
        self.data = data
        self.strings = []
        try:
            string_count, style_count, flags, strings_start, styles_start = struct.unpack("<5I", data[base + 8:base + 28])
            if string_count > 2_000_000:
                return
            utf8 = bool(flags & (1 << 8))
            offs = struct.unpack("<%dI" % string_count, data[base + 28:base + 28 + 4 * string_count]) if string_count else []
            pool_base = base + strings_start
            for o in offs:
                p = pool_base + o
                if utf8:
                    n = data[p]
                    p += 2 if n & 0x80 else 1
                    blen = data[p]
                    if blen & 0x80:
                        blen = ((blen & 0x7F) << 8) | data[p + 1]
                        p += 2
                    else:
                        p += 1
                    self.strings.append(data[p:p + blen].decode("utf-8", "replace"))
                else:
                    n = struct.unpack_from("<H", data, p)[0]
                    p += 4 if n & 0x8000 else 2
                    if n & 0x8000:
                        n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p - 2)[0]
                    self.strings.append(data[p:p + 2 * n].decode("utf-16-le", "replace"))
        except (struct.error, IndexError, UnicodeDecodeError):
            pass


def _parse_arsc_package(data, pos, csize, out):
    try:
        pkg_id = struct.unpack("<I", data[pos + 8:pos + 12])[0]
        name_raw = data[pos + 12:pos + 12 + 256]
        pkg_name = name_raw.decode("utf-16-le", "replace").split("\x00")[0]
        type_strings_off, last_pub_type, key_strings_off, last_pub_key = struct.unpack("<4I", data[pos + 268:pos + 284])
        pkg = {"id": pkg_id, "name": pkg_name, "types": [], "type_spec_count": 0, "type_chunk_count": 0}
        type_names = []
        if pos + type_strings_off + 28 <= pos + csize:
            tsr = _StringPoolReader(data, pos + type_strings_off)
            type_names = tsr.strings
        # walk inner chunks after the two string pools
        inner = pos + 288
        # skip typeStrings pool
        if inner + 8 <= pos + csize:
            t, h, cs = struct.unpack("<HHI", data[inner:inner + 8])
            if t == CHUNK_STRING_POOL:
                inner += cs
        # skip keyStrings pool
        if inner + 8 <= pos + csize:
            t, h, cs = struct.unpack("<HHI", data[inner:inner + 8])
            if t == CHUNK_STRING_POOL:
                inner += cs
        cfg_by_type = {}
        while inner + 12 <= pos + csize:
            t, h, cs = struct.unpack("<HHI", data[inner:inner + 8])
            if cs < 8 or inner + cs > pos + csize:
                break
            if t == 0x0202:  # TYPE_SPEC
                pkg["type_spec_count"] += 1
            elif t == 0x0201:  # TYPE
                pkg["type_chunk_count"] += 1
                tid = data[inner + 8]
                entry_count = struct.unpack("<I", data[inner + 12:inner + 16])[0]
                cfg_off = inner + 20
                density = None
                lang = ""
                try:
                    cfg_size = struct.unpack("<I", data[cfg_off:cfg_off + 4])[0]
                    if cfg_size >= 12:
                        lang_b = data[cfg_off + 8:cfg_off + 10]
                        lang = lang_b.decode("ascii", "replace").strip("\x00")
                        if cfg_size >= 16:
                            density = struct.unpack("<H", data[cfg_off + 14:cfg_off + 16])[0]
                except (struct.error, IndexError):
                    pass
                tname = type_names[tid - 1] if 0 < tid <= len(type_names) else "type-%d" % tid
                key = tname
                if key not in cfg_by_type:
                    cfg_by_type[key] = {"type": tname, "id": tid, "configs": 0, "densities": set(), "languages": set(), "entry_count_last": entry_count}
                cfg_by_type[key]["configs"] += 1
                if density:
                    cfg_by_type[key]["densities"].add(density)
                if lang:
                    cfg_by_type[key]["languages"].add(lang)
            inner += cs
        for v in cfg_by_type.values():
            pkg["types"].append({"type": v["type"], "id": v["id"], "config_count": v["configs"],
                                 "densities": sorted(v["densities"])[:20],
                                 "languages": sorted(v["languages"])[:40]})
        return pkg
    except (struct.error, IndexError, UnicodeDecodeError) as exc:
        out["warnings"].append("package chunk at %d skipped: %s" % (pos, exc))
        return None


# ---------------------------------------------------------------------------
# DEX
# ---------------------------------------------------------------------------

def dex_header(head, path=None):
    if len(head) < 0x70:
        raise core.ToolkitError("E_PARSE", "DEX header truncated (%d bytes)" % len(head))
    magic = head[0:8]
    if not (magic[0:4] == b"dex\n" and magic[7:8] == b"\x00"):
        raise core.ToolkitError("E_PARSE", "bad DEX magic: %r" % magic[:8])
    version = magic[4:7].decode("ascii", "replace")
    checksum = struct.unpack("<I", head[8:12])[0]
    sha1 = head[12:32].hex()
    (file_size, header_size, endian, link_size, link_off, map_off,
     str_size, str_off, type_size, type_off, proto_size, proto_off,
     field_size, field_off, method_size, method_off, class_size, class_off,
     data_size, data_off) = struct.unpack("<20I", head[32:112])
    hdr = {"version": version, "adler32_field": checksum, "sha1_field": sha1,
           "file_size": file_size, "header_size": header_size,
           "endian_tag": "0x%08x" % endian, "map_off": map_off,
           "string_ids_size": str_size, "type_ids_size": type_size,
           "proto_ids_size": proto_size, "field_ids_size": field_size,
           "method_ids_size": method_size, "class_defs_size": class_size,
           "data_size": data_size}
    if header_size != 0x70:
        raise core.ToolkitError("E_PARSE", "unexpected DEX header_size %d" % header_size)
    if endian != 0x12345678:
        raise core.ToolkitError("E_PARSE", "unexpected DEX endian tag 0x%08x" % endian)
    return hdr


def dex_verify(path, cfg):
    data = core.read_bytes(path, max_bytes=core.limit(cfg, "max_input_bytes"))
    hdr = dex_header(data[:0x70], path)
    actual = len(data)
    hdr["actual_size"] = actual
    hdr["warnings"] = [] if hdr["file_size"] == actual else \
        ["file_size field %d != actual %d" % (hdr["file_size"], actual)]
    hdr["adler32_ok"] = (zlib.adler32(data[12:]) & 0xFFFFFFFF) == hdr["adler32_field"]
    hdr["sha1_ok"] = hashlib.sha1(data[32:]).hexdigest() == hdr["sha1_field"]
    return hdr, data


def _uleb128(data, off):
    result = 0
    shift = 0
    while off < len(data):
        b = data[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            break
    return result, off


def dex_strings(path_or_bytes, cfg, pattern=None, max_strings=None, is_bytes=False):
    data = path_or_bytes if is_bytes else core.read_bytes(path_or_bytes, max_bytes=core.limit(cfg, "max_input_bytes"))
    hdr = dex_header(data[:0x70])
    cap = max_strings or core.limit(cfg, "max_dex_strings", 200000)
    str_cap_bytes = core.limit(cfg, "max_dex_string_bytes", 512)
    rx = None
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise core.ToolkitError("E_INPUT", "Invalid filter regex: %s" % exc)
    n = min(hdr["string_ids_size"], cap)
    # string_ids_off lives at DEX header offset 0x3C
    str_ids_off = struct.unpack("<I", data[0x3C:0x40])[0]
    out = []
    matches = 0
    for i in range(n):
        p = str_ids_off + i * 4
        if p + 4 > len(data):
            break
        so = struct.unpack("<I", data[p:p + 4])[0]
        if so >= len(data):
            continue
        try:
            _ulen, q = _uleb128(data, so)
            end = data.find(b"\x00", q, q + str_cap_bytes + 1)
            if end < 0:
                end = min(len(data), q + str_cap_bytes)
            s = data[q:end].decode("utf-8", "replace")
        except (IndexError, struct.error):
            continue
        if rx:
            if rx.search(s):
                out.append(s)
                matches += 1
                if matches >= core.limit(cfg, "max_report_items", 500):
                    break
        else:
            out.append(s)
    return {"header": hdr, "scanned": n, "total_string_ids": hdr["string_ids_size"],
            "returned": len(out), "filter": pattern, "strings": out}


def dex_inventory_apk(zf, cfg):
    entries = sorted((n for n in zf.namelist() if re.match(r"^classes\d*\.dex$", n)),
                     key=lambda n: (len(n), n))
    out = []
    total_methods = 0
    for n in entries:
        data = zf.read(n)
        try:
            hdr = dex_header(data[:0x70])
            hdr["adler32_ok"] = (zlib.adler32(data[12:]) & 0xFFFFFFFF) == hdr["adler32_field"]
            hdr["sha1_ok"] = hashlib.sha1(data[32:]).hexdigest() == hdr["sha1_field"]
            hdr["entry"] = n
            hdr["size_bytes"] = len(data)
            hdr["over_64k_methods"] = hdr["method_ids_size"] > 65536
            total_methods += hdr["method_ids_size"]
            out.append(hdr)
        except core.ToolkitError as exc:
            out.append({"entry": n, "error": exc.message})
    return {"dex_count": len(entries), "total_method_refs": total_methods,
            "multidex": len(entries) > 1,
            "main_dex_over_64k": bool(out and out[0].get("method_ids_size", 0) > 65536),
            "dex_files": out}


# ---------------------------------------------------------------------------
# ELF
# ---------------------------------------------------------------------------

class ElfFile(object):
    def __init__(self, data):
        self.d = data
        if data[:4] != b"\x7fELF":
            raise core.ToolkitError("E_PARSE", "not an ELF file (bad magic)")
        self.is64 = data[4] == 2
        self.isle = data[5] == 1
        self.e = "<" if self.isle else ">"
        if len(data) < (64 if self.is64 else 52):
            raise core.ToolkitError("E_PARSE", "ELF header truncated")
        fmt = self.e + ("HHIQQQIHHHHHH" if self.is64 else "HHIIIIIHHHHHH")
        vals = struct.unpack_from(fmt, data, 16)
        (self.type, self.machine, self.version, self.entry, self.phoff, self.shoff,
         self.flags, self.ehsize, self.phentsize, self.phnum, self.shentsize,
         self.shnum, self.shstrndx) = vals

    def sections(self):
        secs = []
        if self.shoff == 0 or self.shnum == 0:
            return secs
        shstr_off = None
        for i in range(self.shnum):
            base = self.shoff + i * self.shentsize
            if base + self.shentsize > len(self.d):
                break
            if self.is64:
                name, stype, flags, addr, off, size, link, info, align, entsize = \
                    struct.unpack_from(self.e + "IIQQQQIIQQ", self.d, base)
            else:
                name, stype, flags, addr, off, size, link, info, align, entsize = \
                    struct.unpack_from(self.e + "IIIIIIIIII", self.d, base)
            secs.append({"name_off": name, "type": stype, "flags": flags, "addr": addr,
                         "offset": off, "size": size, "link": link, "info": info,
                         "align": align, "entsize": entsize, "index": i})
            if i == self.shstrndx:
                shstr_off = off
                shstr_size = size
        if shstr_off is not None:
            strtab = self.d[shstr_off:shstr_off + shstr_size]
            for s in secs:
                s["name"] = _cstr(strtab, s["name_off"])
        return secs

    def phdrs(self):
        phs = []
        for i in range(self.phnum):
            base = self.phoff + i * self.phentsize
            if base + self.phentsize > len(self.d):
                break
            if self.is64:
                ptype, flags, off, vaddr, paddr, filesz, memsz, align = \
                    struct.unpack_from(self.e + "IIQQQQQQ", self.d, base)
            else:
                ptype, off, vaddr, paddr, filesz, memsz, flags, align = \
                    struct.unpack_from(self.e + "IIIIIIII", self.d, base)
            phs.append({"type": ptype, "flags": flags, "offset": off, "vaddr": vaddr,
                        "filesz": filesz, "memsz": memsz, "align": align})
        return phs

    def read_section(self, sec, cap=8 << 20):
        size = min(sec["size"], cap)
        return self.d[sec["offset"]:sec["offset"] + size]


def _cstr(buf, off):
    if off >= len(buf):
        return ""
    end = buf.find(b"\x00", off)
    if end < 0:
        end = len(buf)
    return buf[off:end].decode("utf-8", "replace")


def elf_summary(path, cfg, want_symbols=False, sym_cap=4000):
    path = core.check_input_path(path, kind="file", cfg=cfg, label="ELF file")
    data = core.read_bytes(path, max_bytes=min(core.limit(cfg, "max_input_bytes"), 64 << 20))
    e = ElfFile(data)
    etype = {0: "NONE", 1: "REL", 2: "EXEC", 3: "DYN (shared object/PIE)", 4: "CORE"}.get(e.type, "0x%x" % e.type)
    out = {
        "path": path, "size_bytes": len(data),
        "elf_class": "64-bit" if e.is64 else "32-bit",
        "endianness": "little" if e.isle else "big",
        "elf_type": etype,
        "machine": ELF_MACHINES.get(e.machine, "0x%x" % e.machine),
        "machine_is_64bit": e.machine in (0x3E, 0xB7),
        "entry": "0x%x" % e.entry,
        "flags": "0x%x" % e.flags,
    }
    secs = e.sections()
    phs = e.phdrs()
    out["section_count"] = len(secs)
    out["program_headers"] = [{"type": ELF_PT.get(p["type"], "0x%x" % p["type"]),
                               "flags": "".join(c for b, c in ((4, "R"), (2, "W"), (1, "X")) if p["flags"] & b),
                               "align": p["align"], "filesz": p["filesz"], "memsz": p["memsz"]}
                              for p in phs]
    loads = [p for p in phs if p["type"] == 1]
    if loads:
        min_align = min(p["align"] for p in loads)
        out["load_segments"] = len(loads)
        out["min_load_align"] = min_align
        out["page_size_4k_ok"] = min_align >= 4096
        out["page_size_16k_ok"] = min_align >= 16384
    secnames = {s.get("name") for s in secs}
    out["stripped"] = ".symtab" not in secnames
    out["has_textrel_flag"] = False
    out["sections"] = [{"name": s.get("name", ""), "type": SHT.get(s["type"], "0x%x" % s["type"]),
                        "size": s["size"]} for s in secs if s.get("name")]
    # dynamic segment
    dynsec = next((s for s in secs if s["type"] == 6), None)
    dynstr = next((s for s in secs if s.get("name") == ".dynstr"), None)
    dynsym = next((s for s in secs if s.get("name") == ".dynsym"), None)
    needed, soname, runpath, rpath = [], None, None, None
    if dynsec and dynstr:
        strtab = e.read_section(dynstr)
        raw = e.read_section(dynsec)
        entsz = 16 if e.is64 else 8
        fmt = e.e + ("qQ" if e.is64 else "iI")
        for i in range(len(raw) // entsz):
            tag, val = struct.unpack_from(fmt, raw, i * entsz)
            nm = DT.get(tag, "0x%x" % tag)
            if tag == 0:
                break
            if tag == 1:
                needed.append(_cstr(strtab, val))
            elif tag == 14:
                soname = _cstr(strtab, val)
            elif tag == 29:
                runpath = _cstr(strtab, val)
            elif tag == 15:
                rpath = _cstr(strtab, val)
            elif tag == 22:
                out["has_textrel_flag"] = True
            elif tag == 30 and (val & 0x4):
                out["has_textrel_flag"] = True
    out["dynamic"] = {"needed": needed, "soname": soname, "runpath": runpath, "rpath": rpath}
    # build id
    for p in phs:
        if p["type"] == 4:
            note = data[p["offset"]:p["offset"] + min(p["filesz"], 4096)]
            try:
                namesz, descsz, ntype = struct.unpack_from(e.e + "III", note, 0)
                nm = note[12:12 + namesz].split(b"\x00")[0].decode("ascii", "replace")
                if nm == "GNU" and ntype == 3:
                    desc_start = 12 + ((namesz + 3) & ~3)
                    out["build_id"] = note[desc_start:desc_start + descsz].hex()
            except (struct.error, IndexError):
                pass
    # ARM attributes
    attrs = next((s for s in secs if s.get("name") == ".ARM.attributes"), None)
    if attrs:
        raw = e.read_section(attrs, 64 << 10)
        printable = re.findall(rb"[ -~]{4,}", raw)
        out["arm_attributes"] = [p.decode("ascii", "replace") for p in printable[:30]]
    # relocations
    rel = [s for s in secs if s["type"] in (4, 9) or s.get("name", "").startswith((".rel", ".rela"))]
    out["relocation_sections"] = [{"name": s.get("name", ""), "entries": (s["size"] // (24 if s["type"] == 4 else 16 if e.is64 else 8))} for s in rel]
    # symbols
    if dynsym and dynstr:
        strtab = e.read_section(dynstr)
        raw = e.read_section(dynsym)
        entsz = 24 if e.is64 else 16
        fmt = e.e + ("IBBHQQ" if e.is64 else "IIIBBH")
        exported, imported, jni = [], [], []
        n = len(raw) // entsz
        for i in range(min(n, sym_cap * 4)):
            if e.is64:
                nameoff, info, other, shndx, value, ssize = struct.unpack_from(fmt, raw, i * entsz)
            else:
                nameoff, value, ssize, info, other, shndx = struct.unpack_from(fmt, raw, i * entsz)
            nm = _cstr(strtab, nameoff)
            if not nm:
                continue
            bind = info >> 4
            styp = info & 0xF
            if styp != 2:  # FUNC only
                if not nm.startswith("Java_") and nm != "JNI_OnLoad":
                    continue
            if shndx == 0:
                imported.append(nm)
            else:
                if bind in (1, 2) and styp == 2:
                    exported.append(nm)
                if nm.startswith("Java_") or nm == "JNI_OnLoad":
                    jni.append(nm)
        out["symbols"] = {"scanned": min(n, sym_cap * 4), "total": n,
                          "exported_functions": sorted(set(exported))[:sym_cap],
                          "imported_undefined": sorted(set(imported))[:sym_cap],
                          "jni_entrypoints": sorted(set(jni))[:200]}
    elif want_symbols:
        out["symbols"] = {"note": "no .dynsym section"}
    return out


# ---------------------------------------------------------------------------
# ASN.1 / X.509 / PKCS#7 (minimal, bounds-checked)
# ---------------------------------------------------------------------------

OID_NAMES = {
    "2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "ST",
    "2.5.4.10": "O", "2.5.4.11": "OU",
    "1.2.840.113549.1.1.1": "rsaEncryption",
    "1.2.840.113549.1.1.4": "md5WithRSAEncryption",
    "1.2.840.113549.1.1.5": "sha1WithRSAEncryption",
    "1.2.840.113549.1.1.11": "sha256WithRSAEncryption",
    "1.2.840.113549.1.1.12": "sha384WithRSAEncryption",
    "1.2.840.113549.1.1.13": "sha512WithRSAEncryption",
    "1.2.840.10045.2.1": "ecPublicKey",
    "1.2.840.10045.4.3.2": "ecdsa-with-SHA256",
    "1.2.840.10045.4.3.3": "ecdsa-with-SHA384",
    "1.2.840.10040.4.3": "dsa-with-SHA1",
    "1.2.840.113549.1.7.1": "pkcs7-data",
    "1.2.840.113549.1.7.2": "pkcs7-signedData",
    "1.3.14.3.2.26": "sha1",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "1.2.840.10045.3.1.7": "secp256r1 (P-256)",
    "1.3.132.0.34": "secp384r1 (P-384)",
    "1.3.132.0.35": "secp521r1 (P-521)",
}


class DerError(core.ToolkitError):
    def __init__(self, msg):
        super().__init__("E_PARSE", "DER parse error: %s" % msg)


def der_read(data, off):
    """Return (tag_byte, constructed, header_end, content_end)."""
    if off >= len(data):
        raise DerError("unexpected end at %d" % off)
    tag = data[off]
    p = off + 1
    if p >= len(data):
        raise DerError("truncated tag")
    ln = data[p]
    p += 1
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4 or p + n > len(data):
            raise DerError("bad length at %d" % off)
        ln = int.from_bytes(data[p:p + n], "big")
        p += n
    if p + ln > len(data):
        raise DerError("content overruns buffer at %d" % off)
    return tag, bool(tag & 0x20), p, p + ln


def der_oid(data, start, end):
    if end - start < 1:
        return ""
    first = data[start]
    parts = [str(first // 40), str(first % 40)]
    val = 0
    for b in data[start + 1:end]:
        val = (val << 7) | (b & 0x7F)
        if not (b & 0x80):
            parts.append(str(val))
            val = 0
    return ".".join(parts)


def der_time(data, start, end, tag):
    s = data[start:end].decode("ascii", "replace")
    try:
        if tag == 0x17:  # UTCTime
            yy = int(s[0:2])
            year = 2000 + yy if yy < 50 else 1900 + yy
            return "%04d-%s-%sT%s:%s:%sZ" % (year, s[2:4], s[4:6], s[6:8], s[8:10], s[10:12])
        return "%s-%s-%sT%s:%s:%sZ" % (s[0:4], s[4:6], s[6:8], s[8:10], s[10:12], s[12:14])
    except (ValueError, IndexError):
        return s


def _name_to_str(data, start, end):
    out = []
    p = start
    while p < end:  # RDNSequence: SET of AttributeTypeAndValue
        tag, cons, hs, ce = der_read(data, p)
        p = ce
        if tag != 0x31:
            continue
        q = hs
        while q < ce:
            t2, c2, h2, e2 = der_read(data, q)
            q = e2
            if t2 != 0x30:
                continue
            t3, c3, h3, e3 = der_read(data, h2)
            oid = der_oid(data, h3, e3) if t3 == 0x06 else ""
            t4, c4, h4, e4 = der_read(data, e3)
            val = data[h4:e4].decode("utf-8", "replace")
            key = OID_NAMES.get(oid, oid)
            out.append("%s=%s" % (key, val))
    return ", ".join(out)


def x509_summary(cert_der):
    import hashlib
    out = {"sha256_fingerprint": hashlib.sha256(cert_der).hexdigest()}
    tag, cons, hs, ce = der_read(cert_der, 0)
    if tag != 0x30:
        raise DerError("certificate is not a SEQUENCE")
    t, c, h, e = der_read(cert_der, hs)  # TBSCertificate
    p = h
    t2, c2, h2, e2 = der_read(cert_der, p)
    if t2 == 0xA0:  # explicit version
        t3, c3, h3, e3 = der_read(cert_der, h2)
        out["version"] = cert_der[h3] + 1 if e3 - h3 == 1 else None
        p = e2
    t2, c2, h2, e2 = der_read(cert_der, p)  # serial
    out["serial"] = cert_der[h2:e2].hex()
    p = e2
    t2, c2, h2, e2 = der_read(cert_der, p)  # signature alg
    t3, c3, h3, e3 = der_read(cert_der, h2)
    out["signature_algorithm"] = OID_NAMES.get(der_oid(cert_der, h3, e3), der_oid(cert_der, h3, e3))
    p = e2
    t2, c2, h2, e2 = der_read(cert_der, p)  # issuer
    out["issuer"] = _name_to_str(cert_der, h2, e2)
    p = e2
    t2, c2, h2, e2 = der_read(cert_der, p)  # validity
    t3, c3, h3, e3 = der_read(cert_der, h2)
    out["not_before"] = der_time(cert_der, h3, e3, t3)
    t3, c3, h3, e3 = der_read(cert_der, e3)
    out["not_after"] = der_time(cert_der, h3, e3, t3)
    p = e2
    t2, c2, h2, e2 = der_read(cert_der, p)  # subject
    out["subject"] = _name_to_str(cert_der, h2, e2)
    p = e2
    t2, c2, h2, e2 = der_read(cert_der, p)  # SPKI
    t3, c3, h3, e3 = der_read(cert_der, h2)  # AlgorithmIdentifier
    t4, c4, h4, e4 = der_read(cert_der, h3)
    alg = der_oid(cert_der, h4, e4)
    out["public_key_algorithm"] = OID_NAMES.get(alg, alg)
    try:
        t5, c5, h5, e5 = der_read(cert_der, e3)
        params_oid = der_oid(cert_der, h5, e5) if t5 == 0x06 else None
        if params_oid:
            out["curve"] = OID_NAMES.get(params_oid, params_oid)
    except DerError:
        pass
    # RSA key size: BIT STRING after AlgorithmIdentifier inside SPKI
    try:
        tb, cb, hb, eb = der_read(cert_der, e3)
        if tb == 0x03:
            key_der = cert_der[hb + 1:eb]
            kt, kc, kh, ke = der_read(key_der, 0)
            if kt == 0x30:
                mt, mc, mh, me = der_read(key_der, kh)
                if mt == 0x02:
                    modulus = key_der[mh:me]
                    out["rsa_key_bits"] = (len(modulus) - (1 if modulus[0] == 0 else 0)) * 8
    except (DerError, IndexError):
        pass
    return out


def pkcs7_certificates(der):
    """Walk a DER blob (PKCS#7/CMS SignedData) and return X.509 certificate
    summaries for every embedded certificate."""

    def looks_like_certificate(data, hs, ce):
        # Certificate ::= SEQUENCE { tbsCertificate SEQUENCE {...}, algorithm, signature }
        try:
            tt, _, th, te = der_read(data, hs)
            if tt != 0x30:
                return False
            p = th
            t2, c2, h2, e2 = der_read(data, p)
            if t2 == 0xA0:  # explicit [0] version
                t2, c2, h2, e2 = der_read(data, e2)
            if t2 != 0x02 or not (0 < e2 - h2 <= 21):
                return False  # serialNumber INTEGER
            t3, c3, h3, e3 = der_read(data, e2)
            if t3 != 0x30:
                return False  # signature AlgorithmIdentifier
            t4, c4, h4, e4 = der_read(data, e3)
            if t4 != 0x30:
                return False  # issuer Name
            t5, c5, h5, e5 = der_read(data, e4)
            if t5 != 0x30:
                return False  # validity
            return True
        except DerError:
            return False

    certs = []

    def walk(data, start, end, depth=0):
        if depth > 14:
            return
        p = start
        while p < end:
            seq_start = p
            try:
                tag, cons, hs, ce = der_read(data, p)
            except DerError:
                return
            p = ce
            if tag == 0x30:
                if looks_like_certificate(data, hs, ce):
                    certs.append(data[seq_start:ce])
                    continue
                walk(data, hs, ce, depth + 1)
            elif cons or tag in (0xA0, 0xA1, 0xA2, 0xA3):
                walk(data, hs, ce, depth + 1)

    try:
        walk(der, 0, len(der))
    except DerError:
        pass
    good = []
    seen = set()
    for c in certs:
        try:
            summ = x509_summary(c)
        except (DerError, IndexError, struct.error):
            continue
        fp = summ["sha256_fingerprint"]
        if fp not in seen:
            seen.add(fp)
            summ["size_bytes"] = len(c)
            good.append(summ)
    return good


# ---------------------------------------------------------------------------
# APK signing schemes
# ---------------------------------------------------------------------------

SIG_BLOCK_IDS = {0x7109871A: "v2 (APK Signature Scheme v2)",
                 0xF05368C0: "v3 (APK Signature Scheme v3, key rotation lineage)",
                 0x1B93AD61: "v3.1 (rotation targeting SDK 28+)",
                 0x2B09189E: "source stamp v1",
                 0x6DFF800D: "source stamp v2",
                 0x42726577: "dependency metadata (Brew id)"}


def signing_report(path, cfg, compare_with=None):
    path = core.check_input_path(path, kind="file", cfg=cfg, label="APK")
    out = {"path": path, "schemes": {}, "warnings": []}
    eocd_off, size = _find_eocd(path)
    v2_ids = []
    if eocd_off is not None:
        with open(path, "rb") as fh:
            fh.seek(eocd_off + 4)
            eocd = fh.read(18)
        cd_off = struct.unpack("<I", eocd[12:16])[0]
        cnt = struct.unpack("<H", eocd[6:8])[0]
        if cd_off == 0xFFFFFFFF and cnt == 0xFFFF:
            out["warnings"].append("zip64 layout; signing block offsets need zip64 EOCD (adapter verify recommended)")
        if cd_off >= 24 and cd_off < size:
            with open(path, "rb") as fh:
                fh.seek(cd_off - 24)
                tail = fh.read(24)
            if tail[8:24] == b"APK Sig Block 42":
                blk_size = struct.unpack("<Q", tail[0:8])[0]
                block_start = cd_off - blk_size - 8
                if 0 <= block_start and blk_size <= 64 << 20:
                    with open(path, "rb") as fh:
                        fh.seek(block_start)
                        body = fh.read(blk_size + 8)  # first sizeOfBlock + pairs + trailing sizeOfBlock + magic
                    # id-value pairs live between the two sizeOfBlock fields: [8, len-24)
                    p, stop = 8, max(8, len(body) - 24)
                    while p + 12 <= stop:
                        plen = struct.unpack("<Q", body[p:p + 8])[0]
                        if plen < 4 or p + 8 + plen > stop + 24:
                            break
                        pid = struct.unpack("<I", body[p + 8:p + 12])[0]
                        v2_ids.append(pid)
                        p += 8 + int(plen)
    for pid in v2_ids:
        label = SIG_BLOCK_IDS.get(pid, "unknown id 0x%08x" % pid)
        out["schemes"].setdefault("apk_signing_block_ids", []).append(label)
        if pid == 0x7109871A:
            out["schemes"]["v2"] = True
        elif pid in (0xF05368C0, 0x1B93AD61):
            out["schemes"]["v3"] = True
    if os.path.isfile(path + ".idsig"):
        out["schemes"]["v4_idsig_file"] = True
    # v1 (JAR signing)
    try:
        with core.open_zip(path) as zf:
            names = zf.namelist()
            meta = [n for n in names if n.upper().startswith("META-INF/")]
            sf = [n for n in meta if n.upper().endswith(".SF")]
            mf = [n for n in meta if n.upper().endswith(".MF")]
            sigs = [n for n in meta if n.upper().endswith((".RSA", ".DSA", ".EC"))]
            out["schemes"]["v1"] = bool(sf and sigs)
            out["v1_files"] = {"manifest_mf": mf[:4], "signature_sf": sf[:4], "signature_blocks": sigs[:4]}
            if sf:
                try:
                    sf_text = zf.read(sf[0]).decode("utf-8", "replace")
                    out["v1_digest_algorithms"] = sorted(set(re.findall(r"(SHA-?1|SHA-?256|SHA-?512|MD5)-Digest", sf_text, re.I)))
                except (KeyError, OSError):
                    pass
            certs = []
            for s in sigs[:1]:
                der = zf.read(s)
                try:
                    certs = pkcs7_certificates(der)
                except (DerError, IndexError, struct.error) as exc:
                    out["warnings"].append("PKCS#7 parse failed for %s: %s" % (s, exc))
            out["certificates"] = certs
            out["certificate_count"] = len(certs)
    except core.ToolkitError as exc:
        out["warnings"].append("ZIP listing failed: %s" % exc.message)
    if compare_with:
        other = signing_report(compare_with, cfg)
        fps_a = {c["sha256_fingerprint"] for c in out.get("certificates", [])}
        fps_b = {c["sha256_fingerprint"] for c in other.get("certificates", [])}
        out["compare"] = {"other": other["path"],
                          "same_v1_identity": bool(fps_a and fps_a == fps_b),
                          "fingerprints_a": sorted(fps_a), "fingerprints_b": sorted(fps_b),
                          "v3_present_a": bool(out["schemes"].get("v3")),
                          "v3_present_b": bool(other["schemes"].get("v3")),
                          "note": ("Same v1 signing identity: updates can be compatible. Different identities require the "
                                   "original key or an authorized v3 rotation lineage; verify with apksigner before claiming "
                                   "update compatibility.")}
    scheme_list = [k for k in ("v1", "v2", "v3", "v4_idsig_file") if out["schemes"].get(k)]
    out["detected_schemes"] = scheme_list or ["none"]
    out["_text"] = ["schemes detected: %s" % (", ".join(out["detected_schemes"]) or "none")]
    for c in out.get("certificates", [])[:3]:
        out["_text"].append("cert: subject=%s issuer=%s valid %s..%s sha256=%s..." % (
            c.get("subject"), c.get("issuer"), c.get("not_before"), c.get("not_after"),
            c.get("sha256_fingerprint", "")[:16]))
    for w in out["warnings"]:
        out["_text"].append("warning: %s" % w)
    return out


# ---------------------------------------------------------------------------
# ABI / native library report
# ---------------------------------------------------------------------------

def abi_report(path, cfg):
    """ABI coverage for an APK (or a directory/ELF). Distinguishes packaged vs
    loadable; never claims tested support (that requires a device lane)."""
    path = core.check_input_path(path, kind="any", cfg=cfg, label="apk-or-dir")
    out = {"path": path, "abis": {}, "warnings": [], "notes": []}
    known_system = set(cfg.get("known_system_libs") or [])
    elfs = []  # (abi, entry_name, data_bytes, stored_flag)
    extract_native_libs = None
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.endswith(".so"):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, path)
                    parts = rel.split(os.sep)
                    abi = parts[1] if len(parts) >= 3 and parts[0] == "lib" else "extracted"
                    try:
                        data = core.read_bytes(full, max_bytes=64 << 20)
                    except OSError as exc:
                        out["warnings"].append("cannot read %s: %s" % (rel, exc))
                        continue
                    elfs.append((abi, rel, data, None))
    else:
        det = detect_file(path, cfg)
        if det["family"] != "zip":
            info = elf_summary(path, cfg)
            out["abis"] = {"single-elf": {"libs": [{"name": os.path.basename(path),
                                                    "class": info["elf_class"],
                                                    "machine": info["machine"],
                                                    "page16k_ok": info.get("page_size_16k_ok")}],
                                          "needed": info["dynamic"]["needed"],
                                          "classes": [info["elf_class"]], "machines": [info["machine"]],
                                          "lib_count": 1, "page16k_ready": bool(info.get("page_size_16k_ok")),
                                          "unknown_needed": sorted(set(info["dynamic"]["needed"]) - known_system),
                                          "libcxx_shared": os.path.basename(path) == "libc++_shared.so",
                                          "stored_uncompressed": None, "textrel": (["<file>"] if info.get("has_textrel_flag") else [])}}
            out["_text"] = ["single ELF: %s %s, 16KB-align ok: %s" % (info["elf_class"], info["machine"],
                                                                      info.get("page_size_16k_ok"))]
            return out
        with core.open_zip(path) as zf:
            names = zf.namelist()
            infos = {i.filename: i for i in zf.infolist()}
            for n in names:
                m = re.match(r"^lib/([^/]+)/([^/]+)$", n)
                if m and n.endswith(".so"):
                    try:
                        data = zf.read(n)
                    except (OSError, zlib.error) as exc:
                        out["warnings"].append("cannot read member %s: %s" % (n, exc))
                        continue
                    elfs.append((m.group(1), n, data, infos[n].compress_type == 0))
            if "AndroidManifest.xml" in names:
                try:
                    doc = parse_axml(zf.read("AndroidManifest.xml"))
                    ms = manifest_summary(doc)
                    extract_native_libs = (ms.get("application") or {}).get("extractNativeLibs")
                except (core.ToolkitError, IndexError, struct.error):
                    out["warnings"].append("manifest parse failed; extractNativeLibs unknown")
    out["extractNativeLibs"] = extract_native_libs
    packaged_basenames = {os.path.basename(entry) for _, entry, _, _ in elfs}
    for abi, entry, data, stored in elfs:
        a = out["abis"].setdefault(abi, {"libs": [], "classes": set(), "machines": set(),
                                         "min_load_align": None, "libcxx_shared": False,
                                         "stored_uncompressed": 0, "unknown_needed": set(),
                                         "textrel": []})
        try:
            e = ElfFile(data)
            phs = e.phdrs()
            loads = [p for p in phs if p["type"] == 1]
            m_align = min((p["align"] for p in loads), default=None)
            a["libs"].append({"name": entry, "size_bytes": len(data),
                              "class": "64-bit" if e.is64 else "32-bit",
                              "machine": ELF_MACHINES.get(e.machine, hex(e.machine)),
                              "min_load_align": m_align,
                              "page16k_ok": bool(m_align and m_align >= 16384),
                              "stored_uncompressed": stored})
            a["classes"].add("64-bit" if e.is64 else "32-bit")
            a["machines"].add(ELF_MACHINES.get(e.machine, hex(e.machine)))
            if m_align and (a["min_load_align"] is None or m_align < a["min_load_align"]):
                a["min_load_align"] = m_align
            if stored:
                a["stored_uncompressed"] += 1
            if entry.endswith("libc++_shared.so"):
                a["libcxx_shared"] = True
            secs = e.sections()
            dynsec = next((s for s in secs if s["type"] == 6), None)
            dynstr = next((s for s in secs if s.get("name") == ".dynstr"), None)
            if dynsec and dynstr:
                strtab = e.read_section(dynstr)
                raw = e.read_section(dynsec)
                entsz = 16 if e.is64 else 8
                fmt = e.e + ("qQ" if e.is64 else "iI")
                for i in range(len(raw) // entsz):
                    tag, val = struct.unpack_from(fmt, raw, i * entsz)
                    if tag == 0:
                        break
                    if tag == 1:
                        nm = _cstr(strtab, val)
                        if nm and nm not in known_system and nm not in packaged_basenames:
                            a["unknown_needed"].add(nm)
                    if tag == 22 or (tag == 30 and (val & 0x4)):
                        a["textrel"].append(entry)
        except (core.ToolkitError, struct.error, IndexError) as exc:
            a["libs"].append({"name": entry, "error": str(exc)})
    for abi, a in out["abis"].items():
        a["classes"] = sorted(a["classes"])
        a["machines"] = sorted(a["machines"])
        a["unknown_needed"] = sorted(a["unknown_needed"])[:40]
        a["lib_count"] = len(a["libs"])
        a["page16k_ready"] = bool(a["libs"]) and all(l.get("page16k_ok") for l in a["libs"] if "error" not in l)
        a["textrel"] = sorted(set(a["textrel"]))
    abi_names = sorted(out["abis"])
    out["abi_list"] = abi_names
    out["has_64bit"] = any("arm64-v8a" == a or "x86_64" == a for a in abi_names)
    out["has_32bit"] = any(a in ("armeabi-v7a", "armeabi", "x86") for a in abi_names)
    out["only_32bit"] = bool(abi_names) and out["has_32bit"] and not out["has_64bit"]
    if out["only_32bit"]:
        out["notes"].append("32-bit-only package: cannot run on 64-bit-only devices. A genuine arm64-v8a build "
                            "requires source or vendor libraries; binary-only 32-to-64 conversion is impossible.")
    dup_cxx = [a for a, v in out["abis"].items() if v.get("libcxx_shared")]
    if len(dup_cxx) > 0:
        out["notes"].append("libc++_shared.so packaged in: %s (ensure a single STL choice across all libraries)" % ", ".join(dup_cxx))
    textrel = {a: v["textrel"] for a, v in out["abis"].items() if v.get("textrel")}
    if textrel:
        out["notes"].append("TEXTREL relocations found (rejected by the linker for targetSdk>=23): %s" % textrel)
    out["_text"] = ["abis: %s" % (", ".join(abi_names) or "none"),
                    "64-bit packaged: %s | 32-bit packaged: %s" % (out["has_64bit"], out["has_32bit"])]
    for abi in abi_names:
        a = out["abis"][abi]
        out["_text"].append("  %s: %d libs, align16k-ready=%s, stored=%d, classes=%s" %
                            (abi, a.get("lib_count", 0), a.get("page16k_ready"), a.get("stored_uncompressed", 0),
                             ",".join(a.get("classes", []))))
    out["_text"].extend("note: %s" % n for n in out["notes"])
    return out


# ---------------------------------------------------------------------------
# Media inspection
# ---------------------------------------------------------------------------

def media_details(path, head=None):
    head = head if head is not None else _head(path, 65536)
    d = {}
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        w, h, bd, ct = struct.unpack(">IIBB", head[16:26])
        d.update({"format": "PNG", "width": w, "height": h, "bit_depth": bd,
                  "color_type": {0: "gray", 2: "rgb", 3: "palette", 4: "gray+alpha", 6: "rgba"}.get(ct, ct)})
    elif head[:3] == b"\xff\xd8\xff":
        d["format"] = "JPEG"
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                d.update({"width": w, "height": h, "progressive": marker in (0xC2,)})
                break
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            i += 2 + seg
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", head[6:10])
        d.update({"format": "GIF", "width": w, "height": h})
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        d["format"] = "WebP"
        fmt = head[12:16]
        if fmt == b"VP8 " and len(head) >= 30:
            w, h = struct.unpack("<HH", head[26:30])
            d.update({"width": w & 0x3FFF, "height": h & 0x3FFF, "codec": "VP8 (lossy)"})
        elif fmt == b"VP8L" and len(head) >= 25:
            b0, b1, b2, b3 = head[21], head[22], head[23], head[24]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            d.update({"width": w, "height": h, "codec": "VP8L (lossless)"})
        elif fmt == b"VP8X" and len(head) >= 30:
            w = 1 + int.from_bytes(head[24:27], "little")
            h = 1 + int.from_bytes(head[27:30], "little")
            d.update({"width": w, "height": h, "codec": "VP8X (extended)"})
    elif head[:4] == b"OggS":
        d["format"] = "OGG"
        if head[28:35] == b"\x01vorbis":
            d["codec"] = "Vorbis"
            try:
                d["sample_rate"] = struct.unpack("<I", head[40:44])[0]
                d["channels"] = head[39]
            except struct.error:
                pass
        elif head[28:36] == b"OpusHead":
            d["codec"] = "Opus"
            d["channels"] = head[37]
    elif head[:3] == b"ID3" or (len(head) > 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        d["format"] = "MP3"
        off = 0
        if head[:3] == b"ID3":
            sz = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) | ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
            off = 10 + sz
        for i in range(off, min(len(head) - 4, off + 8192)):
            if head[i] == 0xFF and (head[i + 1] & 0xE0) == 0xE0:
                ver_bits = (head[i + 1] >> 3) & 0x3
                layer = (head[i + 1] >> 1) & 0x3
                sr_i = (head[i + 2] >> 2) & 0x3
                rates = {(3, 0): 44100, (3, 1): 48000, (3, 2): 32000,
                         (2, 0): 22050, (2, 1): 24000, (2, 2): 16000,
                         (0, 0): 11025, (0, 1): 12000, (0, 2): 8000}
                if (ver_bits, sr_i) in rates:
                    d["sample_rate"] = rates[(ver_bits, sr_i)]
                    d["mpeg_version"] = {3: "MPEG1", 2: "MPEG2", 0: "MPEG2.5"}.get(ver_bits)
                    d["layer"] = {1: "III", 2: "II", 3: "I"}.get(layer)
                break
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        d["format"] = "WAV"
        i = 12
        while i + 8 < len(head):
            cid = head[i:i + 4]
            csz = struct.unpack("<I", head[i + 4:i + 8])[0]
            if cid == b"fmt ":
                fmt_tag, ch, sr = struct.unpack("<HHI", head[i + 8:i + 16])
                d.update({"channels": ch, "sample_rate": sr, "format_tag": fmt_tag})
                break
            i += 8 + csz + (csz & 1)
    elif head[:4] == b"fLaC":
        d["format"] = "FLAC"
        # STREAMINFO begins at offset 8 (4-byte magic + 4-byte block header):
        # min/max block (4+4), min/max frame (3+3), then 20-bit sample rate at 22
        if len(head) >= 26:
            d["sample_rate"] = int.from_bytes(head[22:25], "big") >> 4
            d["channels"] = ((head[24] >> 1) & 0x7) + 1
    elif head[4:8] == b"ftyp":
        brand = head[8:12].decode("ascii", "replace").strip("\x00")
        d.update({"format": "MP4/ISO-BMFF", "major_brand": brand})
    elif head[:4] == b"\x1a\x45\xdf\xa3":
        d["format"] = "Matroska/WebM (EBML)"
    elif head[:12] == b"\xabKTX 11\xbb\r\n\x1a\n":
        d["format"] = "KTX1"
        if len(head) >= 64:
            endian = struct.unpack("<I", head[12:16])[0]
            e = "<" if endian == 0x04030201 else ">"
            gl_fmt, gl_type = struct.unpack(e + "II", head[28:36]) if endian == 0x04030201 else struct.unpack(">II", head[28:36])
            w, h = struct.unpack(e + "II" if endian == 0x04030201 else ">II", head[36:44])
            d.update({"glInternalFormat": "0x%x" % gl_fmt, "width": w, "height": h})
    elif head[:12] == b"\xabKTX 20\xbb\r\n\x1a\n":
        d.update({"format": "KTX2 (Basis Universal)"})
        if len(head) >= 28:
            vk_fmt = struct.unpack("<I", head[12:16])[0]
            w, h = struct.unpack("<II", head[20:28])
            d.update({"vkFormat": vk_fmt, "width": w, "height": h,
                      "supercompression": struct.unpack("<I", head[44:48])[0] if len(head) >= 48 else None})
    elif head[:4] == b"DDS \x7c":
        h, w = struct.unpack("<II", head[12:20])
        d.update({"format": "DDS", "width": w, "height": h})
        fourcc = head[84:88]
        if fourcc[:4] in (b"DX10", b"DXT1", b"DXT3", b"DXT5", b"ATI2"):
            d["fourcc"] = fourcc.decode("ascii", "replace")
    elif head[:4] == b"\x13\xab\xa1\x5c":
        bx, by, xdim, ydim, zdim = struct.unpack("<5I", head[4:24])
        d.update({"format": "ASTC", "block": "%dx%d" % (bx, by),
                  "dims": [xdim & 0xFFFFFF, ydim & 0xFFFFFF, zdim & 0xFFFFFF]})
    elif head[:4] == b"\x03\x52\x56\x50":
        ver, flags = struct.unpack("<II", head[0:8])
        h, w = struct.unpack("<II", head[8:16])
        d.update({"format": "PVR v3", "width": w, "height": h,
                  "pixel_format": struct.unpack("<Q", head[24:32])[0] if len(head) >= 32 else None})
    elif head[:4] == b"\x03\x02\x23\x07":
        ver = struct.unpack("<I", head[4:8])[0]
        d.update({"format": "SPIR-V", "version": "%d.%d" % (ver >> 16, (ver >> 8) & 0xFF)})
    elif head[:8] == b"UnityFS\x00":
        d["format"] = "Unity asset bundle (UnityFS)"
    else:
        fam, label, conf = sniff_bytes(head, name=path)
        d.update({"format": label or fam, "confidence": conf})
    return d


def media_scan_apk(zf, cfg):
    cap = core.limit(cfg, "max_media_entries_scan", 5000)
    out = {"media": [], "scanned": 0}
    for i in zf.infolist()[:cap]:
        if i.is_dir():
            continue
        ext = os.path.splitext(i.filename)[1].lower()
        head = b""
        try:
            with zf.open(i) as fh:
                head = fh.read(256)
        except (OSError, zlib.error):
            continue
        fam, label, conf = sniff_bytes(head, i.file_size, i.filename)
        out["scanned"] += 1
        if fam in ("image", "audio", "video", "texture", "shader", "gamepack") or ext in MEDIA_EXTS:
            entry = {"name": i.filename, "family": fam, "label": label, "size_bytes": i.file_size}
            try:
                with zf.open(i) as fh:
                    entry.update(media_details(i.filename, fh.read(65536)))
            except (OSError, zlib.error, struct.error):
                pass
            if ext and fam == "unknown":
                entry["warning"] = "extension %s but content unrecognized (misleading extension)" % ext
            out["media"].append(entry)
    return out


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def categorize_entry(name):
    n = name.lower()
    if re.match(r"^classes\d*\.dex$", n):
        return "dex"
    if n.startswith("lib/"):
        return "native"
    if n.startswith("assets/"):
        return "assets"
    if n.startswith("res/"):
        return "resources"
    if n == "resources.arsc":
        return "arsc"
    if n.startswith("meta-inf/"):
        return "signing-meta"
    if n == "androidmanifest.xml":
        return "manifest"
    if n.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    if n.endswith((".mp3", ".ogg", ".wav", ".mp4", ".m4a")):
        return "media"
    return "other"


DENSITY_BUCKETS = ["ldpi", "mdpi", "tvdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi", "anydpi", "nodpi"]


def inventory(path, cfg, with_sizes=True):
    path = core.check_input_path(path, kind="file", cfg=cfg, label="archive")
    out = {"path": path, "categories": {}, "densities": {}, "largest": [], "total_entries": 0,
           "total_uncompressed": 0, "total_compressed": 0, "compression_ratio": None,
           "stored_entries": 0, "unusual": [], "nested_archives": []}
    with core.open_zip(path) as zf:
        infos = zf.infolist()
        out["total_entries"] = len(infos)
        rows = []
        for i in infos:
            cat = categorize_entry(i.filename)
            c = out["categories"].setdefault(cat, {"entries": 0, "uncompressed": 0, "compressed": 0})
            c["entries"] += 1
            c["uncompressed"] += i.file_size
            c["compressed"] += i.compress_size
            out["total_uncompressed"] += i.file_size
            out["total_compressed"] += i.compress_size
            if i.compress_type == 0:
                out["stored_entries"] += 1
            m = re.match(r"^res/([a-z0-9\-]+)/", i.filename)
            if m:
                for b in DENSITY_BUCKETS:
                    if b in m.group(1):
                        out["densities"][b] = out["densities"].get(b, 0) + 1
                        break
            if i.compress_type not in (0, 8):
                out["unusual"].append({"name": i.filename, "method": i.compress_type})
            if re.search(r"\.(apk|jar|zip|obb)$", i.filename.lower()):
                out["nested_archives"].append(i.filename)
            rows.append((i.file_size, i.compress_size, i.compress_type, i.filename))
        rows.sort(reverse=True)
        out["largest"] = [{"name": r[3], "uncompressed": r[0], "compressed": r[1],
                           "method": "stored" if r[2] == 0 else "deflate"} for r in rows[:20]]
    if out["total_uncompressed"]:
        out["compression_ratio"] = round(out["total_compressed"] / out["total_uncompressed"], 4)
    out["_text"] = ["entries: %d | uncompressed: %.1f MB | compressed: %.1f MB | ratio: %s" %
                    (out["total_entries"], out["total_uncompressed"] / 1048576.0,
                     out["total_compressed"] / 1048576.0, out["compression_ratio"])]
    for cat, c in sorted(out["categories"].items(), key=lambda kv: -kv[1]["uncompressed"]):
        out["_text"].append("  %-12s %5d entries  %10d B  (%d B compressed)" % (cat, c["entries"], c["uncompressed"], c["compressed"]))
    if out["largest"]:
        out["_text"].append("largest: " + ", ".join("%s (%d B)" % (l["name"], l["uncompressed"]) for l in out["largest"][:5]))
    return out


# ---------------------------------------------------------------------------
# Package info (APK / XAPK / APKM / APKS / AAB / decoded dir)
# ---------------------------------------------------------------------------

def manifest_from_dir(d, cfg):
    p = os.path.join(d, "AndroidManifest.xml")
    if not os.path.isfile(p):
        raise core.ToolkitError("E_NOT_FOUND", "No AndroidManifest.xml in directory: %s" % d,
                                hint="Point at an apktool-decoded directory or an APK file.")
    data = core.read_bytes(p, max_bytes=32 << 20)
    if data[:4] == b"\x03\x00\x08\x00" or data[0:2] == b"\x03\x00":
        doc = parse_axml(data)
        return manifest_summary(doc), "binary-axml"
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(data.decode("utf-8", "replace"))
    except ET.ParseError as exc:
        raise core.ToolkitError("E_PARSE", "Text manifest parse failed: %s" % exc)
    A = "{%s}" % ANDROID_NS
    summary = {"package": root.get("package"), "split": root.get("split"),
               "versionCode": root.get(A + "versionCode"), "versionName": root.get(A + "versionName"),
               "sharedUserId": root.get(A + "sharedUserId")}
    sdk = root.find("uses-sdk")
    if sdk is not None:
        summary["minSdkVersion"] = sdk.get(A + "minSdkVersion")
        summary["targetSdkVersion"] = sdk.get(A + "targetSdkVersion")
    app = root.find("application")
    summary["application"] = {}
    if app is not None:
        for k in ("label", "debuggable", "allowBackup", "extractNativeLibs", "usesCleartextTraffic",
                  "networkSecurityConfig", "requestLegacyExternalStorage", "testOnly"):
            v = app.get(A + k)
            if v is not None:
                summary["application"][k] = v
    summary["uses_permissions"] = [{"name": n.get(A + "name"), "tag": "uses-permission"}
                                   for n in root.findall("uses-permission")]
    summary["uses_features"] = [{"name": n.get(A + "name"), "required": n.get(A + "required", "true")}
                                for n in root.findall("uses-feature")]
    # Components are declared inside <application>; names may be relative (".Main") and
    # are expanded against the package, matching the binary AXML path.
    pkg = root.get("package") or ""
    PLURAL = {"activity": "activities", "activity-alias": "activity_aliases",
              "service": "services", "receiver": "receivers", "provider": "providers"}
    components = {}
    launcher = []
    if app is not None:
        for tag, plural in PLURAL.items():
            lst = []
            for n in app.findall(tag):
                name = n.get(A + "name") or ""
                if name.startswith("."):
                    name = pkg + name
                elif name and "." not in name and pkg:
                    name = pkg + "." + name
                c = {"name": name, "exported": n.get(A + "exported"), "permission": n.get(A + "permission")}
                filters = []
                for f in n.findall("intent-filter"):
                    actions = [a.get(A + "name") for a in f.findall("action")]
                    cats = [a.get(A + "name") for a in f.findall("category")]
                    filters.append({"actions": actions, "categories": cats})
                    if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in cats:
                        launcher.append(name)
                if filters:
                    c["intent_filters"] = filters
                lst.append(c)
            if lst:
                components[plural] = lst
    summary["components"] = components
    summary["launcher_activities"] = launcher
    return summary, "text-xml"


def pkginfo(target, cfg):
    """Reconciled package identity for APK/XAPK/APKM/APKS/AAB/decoded dir."""
    out = {"target": target, "sources": {}, "warnings": []}
    target = core.check_input_path(target, kind="any", cfg=cfg, label="package")
    out["target"] = target
    if os.path.isdir(target):
        summ, kind = manifest_from_dir(target, cfg)
        out["manifest"] = summ
        out["sources"]["dir-manifest(%s)" % kind] = True
        out["container"] = "decoded-directory"
        return out
    det = detect_file(target, cfg)
    out["detected_type"] = det.get("type")
    out["container"] = det.get("type")
    t = det.get("type")
    if t not in ("APK", "AAB", "APKS", "XAPK", "APKM", "JAR", "OBB", "ZIP", "MALFORMED_ZIP"):
        raise core.ToolkitError("E_INPUT", "Not an Android package container (detected %s): %s" % (t, target),
                                hint="pkginfo accepts APK, AAB, APKS, XAPK, APKM or an apktool-decoded directory.")
    if t == "MALFORMED_ZIP":
        raise core.ToolkitError("E_PARSE", "Malformed ZIP container: %s" % target,
                                hint="Run: artifact zip-health to see the structural damage.")
    with core.open_zip(target) as zf:
        names = zf.namelist()
        if t == "APK":
            if "AndroidManifest.xml" in names:
                doc = parse_axml(zf.read("AndroidManifest.xml"))
                out["manifest"] = manifest_summary(doc)
                out["sources"]["binary-manifest"] = True
            out["splits"] = [n for n in names if n.startswith("split_") and n.endswith(".apk")]
        elif t in ("XAPK", "APKM"):
            meta_name = "manifest.json" if "manifest.json" in names else ("info.json" if "info.json" in names else None)
            if meta_name:
                try:
                    meta = json.loads(zf.read(meta_name).decode("utf-8", "replace"))
                    out["container_metadata"] = meta
                    out["sources"][meta_name] = True
                except ValueError as exc:
                    out["warnings"].append("%s parse failed: %s" % (meta_name, exc))
            base = "base.apk" if "base.apk" in names else None
            out["member_apks"] = sorted(n for n in names if n.endswith(".apk"))
            out["obb_files"] = sorted(n for n in names if n.endswith(".obb"))
            if base:
                try:
                    doc = parse_axml(_read_member(zf, base, "AndroidManifest.xml"))
                    out["manifest"] = manifest_summary(doc)
                    out["sources"]["base.apk!AndroidManifest.xml"] = True
                except (core.ToolkitError, KeyError, struct.error, IndexError, OSError, zlib.error) as exc:
                    out["warnings"].append("base.apk manifest parse failed: %s" % exc)
        elif t == "APKS":
            out["member_apks"] = sorted(n for n in names if n.endswith(".apk"))
            base = next((n for n in out["member_apks"] if n.endswith(("base.apk", "split-base.apk"))), None)
            if base:
                try:
                    man = _read_member(zf, base, "AndroidManifest.xml")
                    out["manifest"] = manifest_summary(parse_axml(man))
                    out["sources"]["%s!AndroidManifest.xml" % base] = True
                except (core.ToolkitError, KeyError, struct.error, IndexError) as exc:
                    out["warnings"].append("base member manifest parse failed: %s" % exc)
        elif t == "AAB":
            out["modules"] = sorted({n.split("/")[0] for n in names if "/" in n and not n.startswith("META-INF")})
            out["warnings"].append("AAB manifests are protobuf; use the bundletool adapter for manifest details "
                                   "(tool run bundletool -- dump-manifest --bundle <aab>).")
    # reconciliation
    cm = out.get("container_metadata") or {}
    m = out.get("manifest") or {}
    conflicts = []
    for ck, mk in (("package", "package"), ("version_code", "versionCode"), ("version_name", "versionName"),
                   ("min_sdk_version", "minSdkVersion"), ("target_sdk_version", "targetSdkVersion")):
        a, b = cm.get(ck), m.get(mk)
        if a is not None and b is not None and str(a) != str(b):
            conflicts.append({"field": mk, "container": a, "manifest": b})
    out["reconciliation"] = {"conflicts": conflicts, "consistent": not conflicts}
    return out


def _read_member(outer_zf, member, inner_name):
    """Read inner_name from a nested APK member without writing to disk."""
    import io
    import zipfile as _zf
    data = outer_zf.read(member)
    with _zf.ZipFile(io.BytesIO(data)) as inner:
        return inner.read(inner_name)


# ---------------------------------------------------------------------------
# Split resolution / normalization / conversion advice
# ---------------------------------------------------------------------------

DENSITIES = {"ldpi": 120, "mdpi": 160, "tvdpi": 213, "hdpi": 240, "xhdpi": 320,
             "xxhdpi": 480, "xxxhdpi": 640, "anydpi": 0, "nodpi": -1}


def _density_rank(name):
    for k, v in DENSITIES.items():
        if k in name:
            return v
    m = re.search(r"(\d+)dpi", name)
    return int(m.group(1)) if m else None


def split_set_entries(names):
    base = [n for n in names if re.search(r"(^|/)(base|split-base)\.apk$", n)]
    configs = [n for n in names if re.search(r"(^|/)config\.[^/]+\.apk$", n)]
    features = [n for n in names if re.search(r"(^|/)split-[^/]+\.apk$", n) and n not in base and n not in configs]
    obbs = [n for n in names if n.lower().endswith(".obb")]
    return base, configs, features, obbs


def resolve_splits(target, spec, cfg):
    """spec: {abi, api, density(dpi int), locale}"""
    target = core.check_input_path(target, kind="any", cfg=cfg, label="apk-set")
    names = []
    if os.path.isdir(target):
        for root, dirs, files in os.walk(target):
            for f in files:
                names.append(os.path.relpath(os.path.join(root, f), target))
    else:
        with core.open_zip(target) as zf:
            names = zf.namelist()
    base, configs, features, obbs = split_set_entries(names)
    if not base and not configs:
        raise core.ToolkitError("E_INPUT", "No base/config APK members found in %s" % target,
                                hint="Expected an APKS, XAPK, APKM set or an extracted split directory.")
    selected = list(base)
    reasons = []
    abi = (spec.get("abi") or "").lower()
    dpi = _to_int(spec.get("density"))
    api = _to_int(spec.get("api"))
    locale = (spec.get("locale") or "").replace("_", "-").lower()
    lang = locale.split("-")[0] if locale else None
    for c in configs:
        cn = os.path.basename(c)[len("config."):-len(".apk")]
        cn_norm = cn.replace("_", "-").lower()
        if cn_norm in ("armeabi", "armeabi-v7a", "arm64-v8a", "x86", "x86-64"):
            want = abi.replace("_", "-").lower()
            if want == "x86_64":
                want = "x86-64"
            if want and (cn_norm == want
                         or (want.startswith("arm64") and cn_norm == "arm64-v8a")
                         or (want.startswith("armeabi") and cn_norm == "armeabi-v7a")):
                selected.append(c)
                reasons.append("%s: ABI match (%s)" % (c, cn))
            elif not want:
                reasons.append("%s: ABI split skipped (no --abi given)" % c)
            else:
                reasons.append("%s: ABI split does not match device (%s)" % (c, want))
            continue
        dr = _density_rank(cn)
        if dr is not None:
            if dpi is None:
                reasons.append("%s: density split skipped (no --density given)" % c)
            elif dr <= dpi:
                selected.append(c)
                reasons.append("%s: density %d <= device %d (best bucket wins)" % (c, dr, dpi))
            continue
        if re.match(r"^[a-z]{2}(-r[A-Z]{2})?$", cn, re.I) or re.match(r"^[a-z]{2,3}$", cn):
            if lang and cn.lower().replace("-r", "-").split("-")[0] == lang:
                selected.append(c)
                reasons.append("%s: locale match" % c)
            elif cn.lower() in ("en",) and lang and lang != "en":
                reasons.append("%s: base-language fallback kept" % c)
                selected.append(c)
            continue
        reasons.append("%s: unrecognized config split; include manually if needed" % c)
    # keep the highest density <= dpi only
    dens_sel = [c for c in selected if _density_rank(os.path.basename(c)[len("config."):-len(".apk")] or "") is not None]
    if len(dens_sel) > 1:
        best = max(dens_sel, key=lambda c: _density_rank(os.path.basename(c)[len("config."):-len(".apk")]) or 0)
        for c in dens_sel:
            if c != best:
                selected.remove(c)
                reasons.append("%s: superseded by higher matching density %s" % (c, os.path.basename(best)))
    plan = {"target": target, "device_spec": spec, "base": base, "selected": selected,
            "features_available": features, "obb_files": obbs, "selection_reasons": reasons,
            "install_command_shape": "adb install-multiple " + " ".join("<%s>" % os.path.basename(s) for s in selected)}
    if api and api < 21:
        plan["warnings"] = ["API < 21 cannot install split sets; a universal APK is required (see package convert-advice)."]
    plan["_text"] = ["base: %s" % (", ".join(base) or "none"),
                     "selected splits: %s" % (", ".join(os.path.basename(s) for s in selected) or "none"),
                     "features available: %d | obb files: %d" % (len(features), len(obbs))]
    return plan


def normalize_package(target, outdir, cfg, assume_yes=False, script=False):
    """XAPK/APKM -> extracted components + install-plan.json (+ optional adb script text)."""
    target = core.check_input_path(target, kind="file", cfg=cfg, label="xapk/apkm")
    det = detect_file(target, cfg)
    if det["type"] not in ("XAPK", "APKM", "APKS"):
        raise core.ToolkitError("E_INPUT", "normalize accepts XAPK, APKM or APKS containers (detected %s)" % det["type"],
                                hint="Run: artifact detect <file> to see what this container really is.")
    outdir = core.check_output_path(outdir, kind="dir", overwrite=assume_yes, label="output dir")
    if os.path.isdir(outdir) and os.listdir(outdir) and not assume_yes:
        raise core.ToolkitError("E_APPROVAL", "Output directory is not empty: %s" % outdir,
                                hint="Pass --yes to approve writing into a non-empty directory.")
    os.makedirs(outdir, exist_ok=True)
    extracted, obbs, meta = [], [], {}
    with core.open_zip(target) as zf:
        for i in zf.infolist():
            n = i.filename
            if n.endswith("/") or ".." in n.split("/") or n.startswith("/"):
                continue  # refuse traversal members
            if n.lower().endswith(".apk"):
                dest = os.path.join(outdir, os.path.basename(n))
                core.write_bytes(dest, zf.read(n))
                extracted.append(dest)
            elif n.lower().endswith(".obb"):
                dest = os.path.join(outdir, os.path.basename(n))
                core.write_bytes(dest, zf.read(n))
                obbs.append(dest)
            elif os.path.basename(n) in ("manifest.json", "info.json"):
                try:
                    meta[os.path.basename(n)] = json.loads(zf.read(n).decode("utf-8", "replace"))
                except ValueError:
                    pass
    names = [os.path.basename(x) for x in extracted]
    base = [x for x in names if x in ("base.apk", "split-base.apk")]
    plan = {"source": target, "container_type": det["type"], "output_dir": outdir,
            "metadata": meta, "base": base, "apk_members": names, "obb_files": [os.path.basename(o) for o in obbs],
            "steps": []}
    if obbs:
        pkg = (meta.get("manifest.json") or {}).get("package") or "<package>"
        for o in plan["obb_files"]:
            plan["steps"].append("push %s to /sdcard/Android/obb/%s/ (create the directory; authorized devices only)" % (o, pkg))
    plan["steps"].append("adb install-multiple %s" % " ".join(names))
    if not base:
        plan["warnings"] = ["No base.apk found in container; the set may be incomplete."]
    core.write_bytes(os.path.join(outdir, "install-plan.json"), (core.json_dumps(plan) + "\n").encode())
    if script:
        lines = ["#!/bin/sh", "# Generated install script - REVIEW before running; execution requires an approved adb adapter.",
                 "set -e"]
        pkg = (meta.get("manifest.json") or {}).get("package")
        for o in plan["obb_files"]:
            if pkg:
                lines += ["adb shell mkdir -p /sdcard/Android/obb/%s" % pkg,
                          "adb push \"%s\" /sdcard/Android/obb/%s/" % (o, pkg)]
        lines.append("adb install-multiple %s" % " ".join('"%s"' % n for n in names))
        core.write_bytes(os.path.join(outdir, "install-plan.sh"), ("\n".join(lines) + "\n").encode())
        plan["script"] = os.path.join(outdir, "install-plan.sh")
    plan["_text"] = ["extracted %d APK members and %d OBB files to %s" % (len(names), len(obbs), outdir),
                     "install plan written: install-plan.json" + (" + install-plan.sh" if script else "")]
    return plan


CONVERSION_ROUTES = {
    ("AAB", "APKS"): {"tool": "bundletool build-apks", "lawful": True,
                      "note": "Official route. Requires the AAB and (for release sets) the matching signing key."},
    ("AAB", "APK"): {"tool": "bundletool build-apks --mode=universal", "lawful": True,
                     "note": "Universal APK loses dynamic-feature on-demand delivery and asset-pack conditional delivery."},
    ("APKS", "APK"): {"tool": "bundletool build-apks --mode=universal or extract universal.apk", "lawful": True,
                      "note": "If the APKS contains universal.apk it can be extracted directly."},
    ("XAPK", "install"): {"tool": "package normalize + adb install-multiple", "lawful": True,
                          "note": "Keep split structure; pushing OBBs requires an authorized device."},
    ("APKM", "install"): {"tool": "package normalize + adb install-multiple", "lawful": True,
                          "note": "Same route as XAPK; metadata differs (info.json)."},
    ("APK", "AAB"): {"tool": "not generally possible", "lawful": False,
                     "note": "AAB requires the source project; repackaging an APK into an AAB is not supported by official tooling."},
    ("APK", "decoded"): {"tool": "apktool d", "lawful": True, "note": "Authorized artifacts only; keep the original preserved."},
    ("decoded", "APK"): {"tool": "apktool b + zipalign + apksigner", "lawful": True,
                         "note": "Re-signing requires the correct identity or an authorized rotation path; never claim update compatibility otherwise."},
}


def convert_advice(path, cfg, target_fmt=None):
    det = detect_file(path, cfg)
    src = det.get("type", "UNKNOWN")
    routes = []
    for (s, t), info in CONVERSION_ROUTES.items():
        if s == src and (target_fmt is None or t.lower() == target_fmt.lower()):
            routes.append({"from": s, "to": t, **info})
    if not routes and src == "APK":
        routes.append({"from": "APK", "to": "install", "tool": "adb install (approved device)", "lawful": True,
                       "note": "Direct install of a signed, ABI-compatible APK."})
    out = {"path": path, "detected_type": src, "routes": routes,
           "warnings": [] if routes else ["No known conversion route from %s%s." % (src, " to " + target_fmt if target_fmt else "")]}
    out["_text"] = ["detected: %s" % src] + ["route %s -> %s via %s (%s)" % (r["from"], r["to"], r["tool"],
                                                                             "lawful" if r.get("lawful") else "NOT supported")
                                              for r in routes]
    return out


def manifest_diff(a, b, cfg):
    ia, ib = pkginfo(a, cfg), pkginfo(b, cfg)
    ma, mb = ia.get("manifest") or {}, ib.get("manifest") or {}

    def flat(m):
        d = {}
        for k, v in m.items():
            if k == "application" and isinstance(v, dict):
                for k2, v2 in v.items():
                    d["application.%s" % k2] = v2
            elif k == "uses_permissions":
                d["permissions"] = sorted({p.get("name") for p in v if p.get("name")})
            elif k == "components":
                for ck, cv in v.items():
                    d["components.%s" % ck] = sorted({c.get("name") for c in cv if c.get("name")})
            elif k == "uses_features":
                d["features"] = sorted({f.get("name") for f in v if f.get("name")})
            else:
                d[k] = v
        return d

    fa, fb = flat(ma), flat(mb)
    keys = sorted(set(fa) | set(fb))
    diff = {}
    for k in keys:
        va, vb = fa.get(k), fb.get(k)
        if va != vb:
            if isinstance(va, list) and isinstance(vb, list):
                added = [x for x in vb if x not in va]
                removed = [x for x in va if x not in vb]
                diff[k] = {"added": added, "removed": removed}
            else:
                diff[k] = {"a": va, "b": vb}
    return {"a": ia["target"], "b": ib["target"], "differences": diff,
            "identical": not diff,
            "_text": ["%d differing fields between %s and %s" % (len(diff), os.path.basename(ia["target"]), os.path.basename(ib["target"]))] +
                     ["  %s: %s" % (k, json.dumps(v, ensure_ascii=False)[:160]) for k, v in list(diff.items())[:20]]}


# ---------------------------------------------------------------------------
# command handlers (dispatched by tools-android-agent-toolkit.py)
# ---------------------------------------------------------------------------

def _sniff_zip(path):
    with open(path, "rb") as fh:
        return fh.read(4) == b"PK\x03\x04"


def cmd_artifact_detect(args, cfg):
    data = detect_file(args.file, cfg)
    return core.ok("artifact detect", data)


def cmd_artifact_zip_health(args, cfg):
    data = zip_health(args.file, cfg, do_crc=not args.no_crc, do_align=args.align)
    return core.ok("artifact zip-health", data)


def cmd_artifact_pkginfo(args, cfg):
    return core.ok("artifact pkginfo", pkginfo(args.file, cfg))


def cmd_artifact_axml(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="apk-or-axml")
    if _sniff_zip(path):
        with core.open_zip(path) as zf:
            raw = zf.read("AndroidManifest.xml")
    else:
        raw = core.read_bytes(path, max_bytes=core.limit(cfg, "max_input_bytes"))
    doc = parse_axml(raw)
    data = manifest_summary(doc, cfg)
    xml = doc.to_xml()
    cap = 200000
    data["xml"] = xml[:cap] + ("\n<!-- truncated -->" if len(xml) > cap else "")
    return core.ok("artifact axml", data)


def cmd_artifact_arsc(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="apk-or-arsc")
    if _sniff_zip(path):
        with core.open_zip(path) as zf:
            if "resources.arsc" not in zf.namelist():
                raise core.ToolkitError("E_NOT_FOUND", "resources.arsc not present in %s" % path)
            return core.ok("artifact arsc", arsc_summary(zf.read("resources.arsc")))
    return core.ok("artifact arsc", arsc_summary(path))


def cmd_artifact_dex(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="apk-or-dex")
    if _sniff_zip(path):
        with core.open_zip(path) as zf:
            inv = dex_inventory_apk(zf, cfg)
            if args.strings_filter:
                strings = {}
                for n in sorted(x for x in zf.namelist() if re.match(r"^classes\d*\.dex$", x)):
                    strings[n] = dex_strings(zf.read(n), cfg, pattern=args.strings_filter, is_bytes=True)["strings"]
                inv["strings_filter"] = args.strings_filter
                inv["strings"] = strings
        return core.ok("artifact dex", inv)
    hdr, _ = dex_verify(path, cfg)
    data = {"header": hdr}
    if args.strings_filter:
        data["strings"] = dex_strings(path, cfg, pattern=args.strings_filter)
    return core.ok("artifact dex", data, [] if hdr["adler32_ok"] and hdr["sha1_ok"] else
                   ["DEX checksum or SHA-1 does not match the header: file may be modified or truncated."])


def cmd_artifact_elf(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="elf")
    return core.ok("artifact elf", elf_summary(path, cfg, want_symbols=args.symbols))


def cmd_artifact_signing(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="apk")
    other = core.check_input_path(args.compare, kind="file", cfg=cfg, label="compare-apk") if args.compare else None
    return core.ok("artifact signing", signing_report(path, cfg, compare_with=other))


def cmd_artifact_abi(args, cfg):
    path = core.check_input_path(args.target, kind="any", cfg=cfg, label="apk-or-dir")
    data = abi_report(path, cfg)
    if args.page_size:
        data = {"path": data.get("path"), "page_size": {k: {"page16k_ready": v.get("page16k_ready"),
                                                            "min_load_align": v.get("min_load_align"),
                                                            "libs": [{"name": l.get("name"), "min_load_align": l.get("min_load_align"),
                                                                      "page16k_ok": l.get("page16k_ok")} for l in v.get("libs", [])]}
                                                       for k, v in (data.get("abis") or {}).items()},
                "extractNativeLibs": data.get("extractNativeLibs")}
    return core.ok("artifact abi", data)


def cmd_artifact_media(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="media")
    if _sniff_zip(path):
        with core.open_zip(path) as zf:
            return core.ok("artifact media", media_scan_apk(zf, cfg))
    return core.ok("artifact media", media_details(path))


def cmd_artifact_inventory(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="archive")
    return core.ok("artifact inventory", inventory(path, cfg, with_sizes=args.sizes))


def cmd_package_splits(args, cfg):
    spec = {"abi": args.abi, "api": args.api, "density": args.density, "locale": args.locale}
    return core.ok("package splits-resolve", resolve_splits(args.target, spec, cfg))


def cmd_package_normalize(args, cfg):
    outdir = args.out or os.path.join(os.getcwd(), (cfg.get("paths") or {}).get("default_output_dir", "out"), "normalized")
    return core.ok("package normalize", normalize_package(args.target, core.expand_path(outdir), cfg,
                                                          assume_yes=args.yes, script=args.script))


def cmd_package_convert(args, cfg):
    return core.ok("package convert-advice", convert_advice(args.file, cfg))


def cmd_package_manifest_diff(args, cfg):
    return core.ok("package manifest-diff", manifest_diff(args.a, args.b, cfg))


COMMANDS = {
    ("artifact", "detect"): cmd_artifact_detect,
    ("artifact", "zip-health"): cmd_artifact_zip_health,
    ("artifact", "pkginfo"): cmd_artifact_pkginfo,
    ("artifact", "axml"): cmd_artifact_axml,
    ("artifact", "arsc"): cmd_artifact_arsc,
    ("artifact", "dex"): cmd_artifact_dex,
    ("artifact", "elf"): cmd_artifact_elf,
    ("artifact", "signing"): cmd_artifact_signing,
    ("artifact", "abi"): cmd_artifact_abi,
    ("artifact", "media"): cmd_artifact_media,
    ("artifact", "inventory"): cmd_artifact_inventory,
    ("package", "splits-resolve"): cmd_package_splits,
    ("package", "normalize"): cmd_package_normalize,
    ("package", "convert-advice"): cmd_package_convert,
    ("package", "manifest-diff"): cmd_package_manifest_diff,
}
