#!/usr/bin/env python3
"""caw_media - hashing, manifest, media inspection and DAT verification.

Implements the catalog's inspection and hash-verification capabilities with
pure local parsing. Every detector is a read-only header/container reader:
no extraction, no decryption, no writing to the inspected media.

Detectors included
------------------
``iNES / NES 2.0``, ``FDS (with and without copier header)``, ``SNES LoROM/HiROM``,
``Genesis / Mega Drive``, ``Game Boy / Game Boy Color``, ``Game Boy Advance``,
``Nintendo 64 (z64/v64/n64)``, ``ISO 9660``, ``ZIP``, ``PlayStation memory card``,
``raw save-memory sizes``, plus a generic entropy/magic fallback.
"""

from __future__ import annotations

import os
import zipfile

import caw_runtime as rt

HEAD_BYTES = 1 << 16   # 64 KiB is enough for every header table used here
FULL_READ_LIMIT = 8 << 20
DEFAULT_HASH_CAP = 512 << 20

GB_LOGO = bytes.fromhex(
    "CEED6666CC0D000B03730083000C000D0008111F8889000EDCCC6EE6DDDDD999BBBB67"
    "636E0EECCCDDDC999FBBB9333E"
)
GBA_LOGO_HEAD = bytes.fromhex("24FFAE51699AA221")
def _clean_ascii(raw):
    """Decode a fixed-width header field and drop padding/non-printable bytes."""
    text = raw.decode("ascii", "replace")
    return "".join(ch for ch in text if 32 <= ord(ch) < 127).strip()


N64_MAGICS = {
    bytes.fromhex("80371240"): ("big-endian", "z64"),
    bytes.fromhex("37804012"): ("byte-swapped", "v64"),
    bytes.fromhex("40123780"): ("little-endian", "n64"),
}


# ---------------------------------------------------------------------------
# Hashing / manifest
# ---------------------------------------------------------------------------
def manifest(paths, algorithms=("crc32", "md5", "sha1", "sha256"), recursive=True,
             extensions=None, limits=None, hash_cap=None, deadline=None):
    limits = dict(rt.DEFAULT_LIMITS, **(limits or {}))
    hash_cap = DEFAULT_HASH_CAP if hash_cap is None else int(hash_cap)
    exts = None
    if extensions:
        exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in extensions}
    entries = []
    skipped = []
    total_bytes = 0
    hashed = 0
    for raw in paths:
        root = rt.require_existing(raw, "hash path")
        if os.path.isfile(root):
            candidates = [(root, os.path.basename(root))]
        elif not recursive:
            candidates = [(os.path.join(root, e.name), e.name)
                          for e in sorted(os.scandir(root), key=lambda x: x.name) if e.is_file()]
        else:
            candidates = [(p, rel) for p, rel, _ in rt.walk_bounded(root, limits["max_files"],
                                                                   limits["max_depth"])]
        for abs_path, rel in candidates:
            if deadline is not None:
                import time
                if time.monotonic() > deadline:
                    skipped.append({"path": rel, "reason": "overall hashing deadline reached"})
                    continue
            if exts and os.path.splitext(rel.lower())[1] not in exts:
                continue
            try:
                size = os.path.getsize(abs_path)
            except OSError as exc:
                skipped.append({"path": rel, "reason": f"stat failed: {exc}"})
                continue
            rec = {"path": rel.replace(os.sep, "/"), "size": size, "hashes": {}, "partial": False}
            if hash_cap and size > hash_cap:
                rec["hashes"] = {}
                rec["note"] = f"skipped content hash: size exceeds hash cap {rt.human_bytes(hash_cap)}"
                skipped.append({"path": rel, "reason": "content hash cap exceeded"})
            else:
                try:
                    info = rt.hash_file(abs_path, algorithms, max_bytes=limits["max_hash_bytes"],
                                        deadline=deadline)
                except (OSError, TimeoutError) as exc:
                    skipped.append({"path": rel, "reason": f"hash failed: {exc}"})
                    continue
                rec["hashes"] = info["hashes"]
                rec["partial"] = info["partial"]
                hashed += 1
            total_bytes += size
            entries.append(rec)
            if len(entries) >= limits["max_files"]:
                break
    entries.sort(key=lambda e: e["path"])
    digest = rt.sha256_obj([{"path": e["path"], "size": e["size"], "hashes": e["hashes"]}
                            for e in entries])
    return {
        "algorithms": list(algorithms),
        "file_count": len(entries),
        "hashed_count": hashed,
        "total_bytes": total_bytes,
        "total_human": rt.human_bytes(total_bytes),
        "manifest_sha256": digest,
        "entries": entries,
        "entry_limit": limits["max_files"],
        "skipped": skipped[:200],
        "hash_cap_bytes": hash_cap,
    }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------
def _det_ines(head, size):
    if head[:4] != b"NES\x1a":
        return None
    prg = head[4]
    chr_ = head[5]
    f6, f7, f8, f9, f10, f11, f12, f13 = (head[i] for i in range(6, 14))
    nes2 = (f7 & 0x0C) == 0x08
    if nes2:
        mapper = ((f8 & 0x0F) << 8) | (f7 & 0xF0) | (f6 >> 4)
        submapper = f8 >> 4
    else:
        mapper = (f7 & 0xF0) | (f6 >> 4)
        submapper = 0
    trainer = bool(f6 & 0x04)
    expected = 16 + prg * 16384 + chr_ * 8192 + (512 if trainer else 0)
    warnings = []
    if size != expected:
        warnings.append(f"file size {size} differs from header-implied size {expected}")
    details = {
        "container": "NES 2.0" if nes2 else "iNES",
        "prg_rom_banks_16k": prg,
        "chr_rom_banks_8k": chr_,
        "mapper": mapper,
        "submapper": submapper,
        "mirroring": ["horizontal", "vertical"][f6 & 1] if not (f6 & 0x08) else "four-screen",
        "battery": bool(f6 & 0x02),
        "trainer": trainer,
        "console_type": {0: "NES/Famicom", 1: "VS System", 2: "PlayChoice-10",
                         3: "extended console"}.get((f11 >> 2) & 0x03, "unknown") if nes2 else
                        ("VS System" if f7 & 1 else "NES/Famicom"),
        "timing": {0: "NTSC", 1: "PAL", 2: "multi-region", 3: "dendy"}.get(f10 & 0x03) if nes2 else "NTSC",
        "header_implied_size": expected,
        "size_consistent": size == expected,
    }
    if not nes2 and (f7 & 0x0C) not in (0x00, 0x08):
        warnings.append("reserved bits 2-3 of the flags-7 byte are set; header may be non-standard")
    return ("ines", "NES / Famicom", 0.98, details, warnings)


def _det_fds(head, size):
    if head[:4] == b"FDS\x1a":
        sides = head[4]
        payload = size - 16
        blocks = payload // 65500
        return ("fds", "Famicom Disk System", 0.95, {
            "container": "fwNES (.fds) with copier header",
            "declared_sides": sides,
            "payload_bytes": payload,
            "whole_blocks_65500": blocks,
        }, [] if sides in (1, 2) else [f"declared side count {sides} is unusual"])
    if head[:4] == b"\x01\x2a\x4e\x49":
        return ("fds-raw", "Famicom Disk System", 0.9, {
            "container": "raw FDS disk image (no copier header)",
            "side_disk_blocks": size // 65500,
        }, [])
    return None


def _snes_header_at(head, offset):
    if offset + 0x20 > len(head):
        return None
    h = head[offset:offset + 0x20]
    map_mode = h[0x15]
    checksum = h[0x1E] | (h[0x1F] << 8)
    complement = h[0x1C] | (h[0x1D] << 8)
    valid = (checksum ^ complement) == 0xFFFF and checksum != 0 and complement != 0xFFFF
    title = _clean_ascii(h[:21])
    if not valid or map_mode not in (0x20, 0x21, 0x22, 0x23, 0x25, 0x30, 0x35):
        return None
    return {
        "title": title,
        "map_mode_byte": f"0x{map_mode:02x}",
        "mapping": {0x20: "LoROM", 0x21: "HiROM", 0x22: "SDD-1 LoROM", 0x23: "SA-1 LoROM",
                    0x25: "ExHiROM", 0x30: "LoROM+FastROM", 0x35: "HiROM+FastROM"}.get(map_mode, "unknown"),
        "rom_size_byte": h[0x17],
        "sram_size_byte": h[0x18],
        "region_byte": h[0x19],
        "licensee_code": f"0x{h[0x1A]:02x}",
        "version": h[0x1B],
        "checksum": f"0x{checksum:04x}",
        "checksum_complement": f"0x{complement:04x}",
        "checksum_complement_consistent": True,
        "header_offset": f"0x{offset:06x}",
        "developer_id": h[0x14],
    }


def _det_snes(head, size):
    for offset, name in ((0x7FC0, "LoROM candidate"), (0xFFC0, "HiROM candidate"),
                         (0x40FFC0, "ExHiROM candidate")):
        hdr = _snes_header_at(head, offset)
        if hdr:
            return ("snes", "SNES / Super Famicom", 0.9, dict(hdr, header_location=name), [])
    return None


def _det_genesis(head, size):
    if 0x100 + 0x200 > len(head) or b"SEGA" not in head[0x100:0x110]:
        return None
    def txt(a, b):
        return _clean_ascii(head[a:b])

    rom_start = int.from_bytes(head[0x1A0:0x1A4], "big")
    rom_end = int.from_bytes(head[0x1A4:0x1A8], "big")
    warnings = []
    if rom_start != 0:
        warnings.append(f"ROM start offset field is {rom_start}, expected 0")
    return ("genesis", "Genesis / Mega Drive", 0.95, {
        "console_name": txt(0x100, 0x110),
        "domestic_name": txt(0x110, 0x120),
        "overseas_name": txt(0x120, 0x150),
        "serial": txt(0x180, 0x18E),
        "region_code": txt(0x1F0, 0x1F3),
        "rom_start": rom_start,
        "rom_end_field": rom_end,
        "device_support": " ".join(f"{b:02x}" for b in head[0x190:0x1A0]),
        "sram_start": int.from_bytes(head[0x1B4:0x1B8], "big"),
        "sram_end": int.from_bytes(head[0x1B8:0x1BC], "big"),
    }, warnings)


def _det_gameboy(head, size):
    if len(head) < 0x150:
        return None
    logo_ok = head[0x104:0x134] == GB_LOGO
    if not logo_ok:
        return None
    cart_type = head[0x147]
    cgb = head[0x143]
    # With a CGB flag the title field is 15 bytes; byte 0x143 is the flag itself.
    title = _clean_ascii(head[0x134:0x143] if cgb in (0x80, 0xC0) else head[0x134:0x144])
    computed = 0
    for b in head[0x134:0x14D]:
        computed = (computed - b - 1) & 0xFF
    stored = head[0x14D]
    warnings = []
    if computed != stored:
        warnings.append(f"header checksum mismatch: stored 0x{stored:02x}, computed 0x{computed:02x}")
    rom_sizes = {0: "32 KiB", 1: "64 KiB", 2: "128 KiB", 3: "256 KiB", 4: "512 KiB",
                 5: "1 MiB", 6: "2 MiB", 7: "4 MiB", 8: "8 MiB"}
    ram_sizes = {0: "none", 1: "2 KiB", 2: "8 KiB", 3: "32 KiB", 4: "128 KiB", 5: "64 KiB"}
    details = {
        "title": title,
        "cgb_flag": f"0x{cgb:02x}",
        "platform": {0x80: "Game Boy Color (enhanced)", 0xC0: "Game Boy Color (only)"}.get(cgb, "Game Boy"),
        "cartridge_type_byte": f"0x{cart_type:02x}",
        "has_battery": cart_type in (0x03, 0x06, 0x09, 0x0D, 0x0F, 0x10, 0x13, 0x1B, 0x1E, 0x22, 0xFF),
        "mbc": {0x00: "ROM only", 0x01: "MBC1", 0x02: "MBC1+RAM", 0x03: "MBC1+RAM+BATTERY",
                0x05: "MBC2", 0x06: "MBC2+BATTERY", 0x0F: "MBC3+TIMER+BATTERY",
                0x10: "MBC3+TIMER+RAM+BATTERY", 0x11: "MBC3", 0x13: "MBC3+RAM+BATTERY",
                0x19: "MBC5", 0x1B: "MBC5+RAM+BATTERY", 0x1E: "MBC5+RUMBLE+RAM+BATTERY"}.get(
                    cart_type, f"controller 0x{cart_type:02x}"),
        "rom_size": rom_sizes.get(head[0x148], f"code {head[0x148]}"),
        "ram_size": ram_sizes.get(head[0x149], f"code {head[0x149]}"),
        "destination_code": f"0x{head[0x14A]:02x}",
        "mask_rom_version": head[0x14C],
        "header_checksum_stored": f"0x{stored:02x}",
        "header_checksum_computed": f"0x{computed:02x}",
        "header_checksum_valid": computed == stored,
    }
    return ("gb", "Game Boy / Game Boy Color", 0.98, details, warnings)


def _det_gba(head, size):
    if len(head) < 0xC0 or head[4:8] != GBA_LOGO_HEAD[4:] or head[:4] != GBA_LOGO_HEAD[:4]:
        return None
    computed = (-(sum(head[0xA0:0xBD]) + 0x19)) & 0xFF
    stored = head[0xBD]
    warnings = []
    if computed != stored:
        warnings.append(f"header complement mismatch: stored 0x{stored:02x}, computed 0x{computed:02x}")
    return ("gba", "Game Boy Advance", 0.95, {
        "title": _clean_ascii(head[0xA0:0xAC]),
        "game_code": _clean_ascii(head[0xAC:0xB0]),
        "maker_code": _clean_ascii(head[0xB0:0xB2]),
        "software_version": head[0xBC],
        "header_complement_stored": f"0x{stored:02x}",
        "header_complement_computed": f"0x{computed:02x}",
        "header_complement_valid": computed == stored,
    }, warnings)


def _det_n64(head, size):
    magic = head[:4]
    if magic not in N64_MAGICS:
        return None
    order, ext = N64_MAGICS[magic]
    data = head if order == "big-endian" else (
        bytes(reversed(head)) if order == "little-endian" else
        bytes(b for i in range(0, len(head) - 1, 2) for b in head[i:i + 2][::-1]))
    return ("n64", "Nintendo 64", 0.9, {
        "byte_order": order,
        "rom_name": _clean_ascii(data[0x20:0x34]),
        "game_code": data[0x3B:0x3F].decode("ascii", "replace"),
        "media_format": f"0x{data[0x3E]:02x}" if len(data) > 0x3E else "",
        "version": data[0x3F] if len(data) > 0x3F else 0,
        "clock_rate": int.from_bytes(data[0x08:0x0C], "big"),
        "entry_point": f"0x{int.from_bytes(data[0x18:0x1C], 'big'):08x}",
        "expected_extension": ext,
    }, [])


def _det_iso9660(head, size):
    off = 16 * 2048
    if len(head) < off + 2048:
        return None
    pvd = head[off:off + 2048]
    if pvd[0] != 1 or pvd[1:6] != b"CD001":
        return None
    volume_id = _clean_ascii(pvd[40:72])
    system_id = _clean_ascii(pvd[8:40])
    blocks = int.from_bytes(pvd[80:84], "little")
    block_size = int.from_bytes(pvd[128:132], "little")
    warnings = []
    if block_size not in (2048, 2336, 2352):
        warnings.append(f"unusual logical block size {block_size}")
    hint = "unknown"
    if "PLAYSTATION" in system_id.upper() or "PLAYSTATION" in volume_id.upper():
        hint = "PlayStation disc layout"
    elif "SEGA" in system_id.upper():
        hint = "Sega disc layout"
    return ("iso9660", "optical disc image", 0.9, {
        "filesystem": "ISO 9660",
        "system_identifier": system_id,
        "volume_identifier": volume_id,
        "volume_space_blocks": blocks,
        "logical_block_size": block_size,
        "declared_bytes": blocks * block_size,
        "size_matches_declared": (blocks * block_size) == size,
        "boot_record": pvd[7],
        "system_hint": hint,
        "image_has_system_area_prepended": size in (blocks * block_size, blocks * block_size + 0),
    }, warnings)


def _det_zip(path, head, size):
    if not zipfile.is_zipfile(path):
        return None
    warnings = []
    nested = []
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        for info in infos[:500]:
            if os.path.splitext(info.filename.lower())[1] in (
                    ".zip", ".7z", ".rar", ".tar", ".gz", ".xz", ".bz2"):
                nested.append(info.filename)
            if info.flag_bits & 0x1:
                warnings.append(f"entry is encrypted: {info.filename}")
        uncompressed = sum(i.file_size for i in infos)
    if nested:
        warnings.append("archive contains nested archives: " + ", ".join(nested[:5]))
    return ("zip", "archive container", 1.0, {
        "entries": len(infos),
        "uncompressed_bytes": uncompressed,
        "compression_ratio": round(size / uncompressed, 4) if uncompressed else None,
        "nested_archives": nested[:50],
    }, warnings)


def _det_psx_memcard(head, size):
    if size not in (128 * 1024, 128 * 1024 + 128) or head[:2] != b"MC":
        return None
    warnings = []
    if size == 128 * 1024 + 128:
        warnings.append("file has a 128-byte header; standard dumps are 131072 bytes")
        frame = head[128:128 + 128]
    else:
        frame = head[0:128]
    return ("psx-memcard", "PlayStation memory card", 0.95, {
        "directory_frame_magic": frame[:2].decode("ascii", "replace"),
        "frames_total": (size - (128 if size > 128 * 1024 else 0)) // 128,
        "format": "SCEA/SCEE/SCEI memory card image",
    }, warnings)


RAW_SAVE_SIZES = {
    512: "EEPROM 512 B (4 kbit)", 8192: "SRAM 8 KiB", 32768: "SRAM 32 KiB",
    131072: "Flash 128 KiB", 2048: "EEPROM 2 KiB (16 kbit)", 262144: "memory card 256 KiB",
}


def _det_raw_save(head, size):
    if size in RAW_SAVE_SIZES:
        return ("raw-save", "save memory", 0.4, {
            "probable_format": RAW_SAVE_SIZES[size],
            "note": "size-based guess only; confirm against the platform's documented size",
        }, ["format inferred from size alone"])
    return None


DETECTORS = (
    ("fds", lambda h, s, p: _det_fds(h, s)),
    ("ines", lambda h, s, p: _det_ines(h, s)),
    ("snes", lambda h, s, p: _det_snes(h, s)),
    ("gb", lambda h, s, p: _det_gameboy(h, s)),
    ("gba", lambda h, s, p: _det_gba(h, s)),
    ("genesis", lambda h, s, p: _det_genesis(h, s)),
    ("n64", lambda h, s, p: _det_n64(h, s)),
    ("psx-memcard", lambda h, s, p: _det_psx_memcard(h, s)),
    ("iso9660", lambda h, s, p: _det_iso9660(h, s)),
    ("zip", lambda h, s, p: _det_zip(p, h, s)),
    ("raw-save", lambda h, s, p: _det_raw_save(h, s)),
)


def inspect_file(path, hash_cap=DEFAULT_HASH_CAP, do_hash=True):
    p = rt.require_existing(path, "inspect path", kind="file")
    size = os.path.getsize(p)
    with open(p, "rb") as fh:
        head = fh.read(min(HEAD_BYTES, size))
    result = {
        "path": p,
        "file_name": os.path.basename(p),
        "size": size,
        "size_human": rt.human_bytes(size),
        "entropy_bits_per_byte": rt.entropy(head[:8192]),
        "magic_hex": head[:16].hex(),
        "detected_format": "unknown",
        "platform": "unknown",
        "confidence": 0.0,
        "details": {},
        "warnings": [],
        "hashes": {},
        "next_steps": [],
    }
    for _name, fn in DETECTORS:
        try:
            hit = fn(head, size, p)
        except (OSError, zipfile.BadZipFile, ValueError, IndexError) as exc:
            result["warnings"].append(f"detector error: {exc}")
            continue
        if hit:
            fmt, platform, confidence, details, warnings = hit
            result.update(detected_format=fmt, platform=platform,
                          confidence=confidence, details=details)
            result["warnings"].extend(warnings)
            break
    if do_hash:
        if hash_cap and size <= hash_cap:
            info = rt.hash_file(p, ("crc32", "md5", "sha1", "sha256"))
            result["hashes"] = info["hashes"]
        else:
            result["warnings"].append(
                f"content hashes skipped: file exceeds hash cap {rt.human_bytes(hash_cap or 0)}")
    if result["detected_format"] == "unknown":
        result["next_steps"].append(
            "identify with the catalog's Console Artifact Auto-Detector workflow or supply the "
            "header offset documentation you are authorized to use")
    else:
        result["next_steps"].append("record this result with: provenance create --material <path>")
    if result["hashes"].get("sha256"):
        result["next_steps"].append("verify against a trusted DAT entry with: verify --dat <dat>")
    return result


# ---------------------------------------------------------------------------
# DAT verification
# ---------------------------------------------------------------------------
DAT_MATCH_ORDER = ("sha256", "sha1", "md5", "crc32+size")


def load_dat(path):
    dat = rt.load_json(path, "DAT")
    if not isinstance(dat, dict) or "entries" not in dat:
        raise rt.ValidationError("DAT must be a JSON object with an 'entries' array")
    if not isinstance(dat["entries"], list):
        raise rt.ValidationError("DAT 'entries' must be an array")
    for i, entry in enumerate(dat["entries"][:10000]):
        if not isinstance(entry, dict) or "name" not in entry:
            raise rt.ValidationError(f"DAT entry {i} must be an object with a 'name'")
        if not any(k in entry for k in ("sha256", "sha1", "md5", "crc32")):
            raise rt.ValidationError(f"DAT entry {i} ({entry['name']}) has no hash field")
    dat.setdefault("match_order", list(DAT_MATCH_ORDER))
    dat["_path"] = os.path.abspath(rt.resolve_input(path))
    return dat


def _find_matches(entry_hashes, size, dat):
    found = []
    for entry in dat["entries"]:
        for method in dat.get("match_order", DAT_MATCH_ORDER):
            if method == "crc32+size":
                if entry.get("crc32") and entry.get("size") is not None:
                    if str(entry["crc32"]).lower() == entry_hashes.get("crc32", "").lower() \
                            and int(entry["size"]) == size:
                        found.append((entry, method))
                        break
            elif entry.get(method) and entry_hashes.get(method):
                if str(entry[method]).lower() == entry_hashes[method].lower():
                    found.append((entry, method))
                    break
    return found


def verify_against_dat(paths, dat, limits=None, hash_cap=None):
    limits = dict(rt.DEFAULT_LIMITS, **(limits or {}))
    hash_cap = DEFAULT_HASH_CAP if hash_cap is None else int(hash_cap)
    results = []
    for raw in paths:
        target = rt.require_existing(raw, "verify path")
        if os.path.isdir(target):
            files = [(p, rel) for p, rel, _ in rt.walk_bounded(target, limits["max_files"],
                                                               limits["max_depth"])]
        else:
            files = [(target, os.path.basename(target))]
        for abs_path, rel in files:
            size = os.path.getsize(abs_path)
            rec = {"path": rel.replace(os.sep, "/"), "size": size, "status": "unknown",
                   "match": None, "match_method": None, "candidates": [], "note": ""}
            if hash_cap and size > hash_cap:
                rec["status"] = "not-verified"
                rec["note"] = f"file exceeds hash cap {rt.human_bytes(hash_cap)}"
                results.append(rec)
                continue
            info = rt.hash_file(abs_path, ("crc32", "md5", "sha1", "sha256"))
            rec["hashes"] = info["hashes"]
            matches = _find_matches(info["hashes"], size, dat)
            if not matches:
                rec["status"] = "unknown"
                rec["note"] = "no DAT entry matched by any hash"
            else:
                entry, method = matches[0]
                rec["match"] = {k: entry.get(k) for k in
                                ("name", "system", "region", "revision", "status", "size")}
                rec["match_method"] = method
                rec["candidates"] = [{"name": e["name"], "method": m} for e, m in matches[1:6]]
                entry_size = entry.get("size")
                if entry_size is not None and int(entry_size) != size:
                    rec["status"] = "size-mismatch"
                elif method == "crc32+size":
                    rec["status"] = "verified-crc32"
                else:
                    rec["status"] = "verified"
            results.append(rec)
    verified = sum(1 for r in results if r["status"].startswith("verified"))
    return {
        "dat_id": dat.get("dat_id"),
        "dat_name": dat.get("name"),
        "dat_version": dat.get("version"),
        "dat_path": dat["_path"],
        "dat_entries": len(dat["entries"]),
        "files_checked": len(results),
        "verified": verified,
        "unknown": sum(1 for r in results if r["status"] == "unknown"),
        "problems": sum(1 for r in results if r["status"] in ("size-mismatch", "not-verified")),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Repeated-read cross-check
# ---------------------------------------------------------------------------
def crosscheck(paths, block_size=1 << 20, max_diff_blocks=64):
    if len(paths) < 2:
        raise rt.ValidationError("crosscheck needs at least two dump files",
                                 ["provide two or more independent reads of the same media"])
    files = [rt.require_existing(p, "dump file", kind="file") for p in paths]
    sizes = [os.path.getsize(f) for f in files]
    groups = {}
    for f in files:
        digest = rt.hash_file(f, ("sha256",))["hashes"]["sha256"]
        groups.setdefault(digest, []).append(os.path.basename(f))
    reference = files[0]
    ref_size = sizes[0]
    comparisons = []
    for i, other in enumerate(files[1:], start=1):
        first_diff = None
        diff_blocks = 0
        compared = 0
        block = 0
        with open(reference, "rb") as a, open(other, "rb") as b:
            while True:
                da = a.read(block_size)
                db = b.read(block_size)
                if not da and not db:
                    break
                compared += min(len(da), len(db))
                if da != db:
                    if first_diff is None:
                        for j in range(min(len(da), len(db))):
                            if da[j] != db[j]:
                                first_diff = block * block_size + j
                                break
                    diff_blocks += 1
                    if diff_blocks >= max_diff_blocks:
                        break
                block += 1
        comparisons.append({
            "reference": os.path.basename(reference),
            "candidate": os.path.basename(other),
            "identical": diff_blocks == 0 and sizes[i] == ref_size,
            "size_reference": ref_size,
            "size_candidate": sizes[i],
            "size_equal": sizes[i] == ref_size,
            "bytes_compared": compared,
            "first_difference_offset": first_diff,
            "differing_blocks": diff_blocks,
            "difference_limit_reached": diff_blocks >= max_diff_blocks,
        })
    unstable = [c for c in comparisons if not c["identical"]]
    return {
        "files": [{"path": f, "size": s} for f, s in zip(files, sizes)],
        "identical_groups": [{"sha256": k[:16], "files": v} for k, v in groups.items()],
        "consistent": not unstable,
        "comparisons": comparisons,
        "interpretation": ("all reads agree; media and reader behaved consistently"
                           if not unstable else
                           "reads disagree: re-seat the media, clean contacts, and re-read before "
                           "recording provenance"),
        "block_size": block_size,
    }
