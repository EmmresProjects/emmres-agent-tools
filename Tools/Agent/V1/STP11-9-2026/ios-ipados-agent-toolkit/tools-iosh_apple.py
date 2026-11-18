"""Apple artifact parsers used by the iOS/iPadOS Agent Skill Tool.

Everything here runs on the Linux inspection host with the standard library
only, so an inspection never requires Xcode. Supported formats:

* property lists, XML and binary (bplist00) -- full type preservation
* Mach-O thin and fat binaries -- headers, load commands, UUID, platform,
  minimum OS, encryption, dylibs, rpaths, code-signature entitlements
* ZIP/IPA health, payload discovery, bundle inventory, nested code walking
* embedded.mobileprovision CMS envelopes (XML payload extraction)
* crash reports: modern .ips (JSON) and legacy text .crash
* dSYM UUID indexing
"""

from __future__ import annotations

import io
import json
import plistlib
import re
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tools_iosh_core import (InputError, LimitError, Limits, Report, human_bytes, safe_relpath,
                             sha256_file, sniff_kind)

# ==========================================================================
# property lists
# ==========================================================================
_MAX_BPLIST_DEPTH = 64


class _BplistTrailer:
    __slots__ = ("offset_size", "ref_size", "num_objects", "top_object", "offset_table")


def _parse_bplist(data: bytes) -> Any:
    """Minimal but complete bplist00 parser preserving ints, reals, dates, data, uids."""
    if not data.startswith(b"bplist00"):
        raise ValueError("not a bplist00 payload")
    trailer = data[-32:]
    offset_size = trailer[6]
    ref_size = trailer[7]
    num_objects = struct.unpack(">Q", trailer[8:16])[0]
    top_object = struct.unpack(">Q", trailer[16:24])[0]
    table_off = struct.unpack(">Q", trailer[24:32])[0]
    if offset_size not in (1, 2, 3, 4, 8) or ref_size not in (1, 2, 3, 4, 8):
        raise ValueError("unsupported bplist offset/ref size")
    if num_objects == 0 or table_off + num_objects * offset_size > len(data):
        raise ValueError("corrupt bplist trailer")
    offsets = []
    for i in range(num_objects):
        start = table_off + i * offset_size
        offsets.append(int.from_bytes(data[start:start + offset_size], "big"))

    def read_int(start: int, size: int) -> int:
        return int.from_bytes(data[start:start + size], "big")

    def read_ref(start: int) -> int:
        return read_int(start, ref_size)

    def object_count(low: int) -> int:
        """Return the element count and advance past the integer marker + count bytes."""
        if low != 0xF:
            return low
        marker = data[pos[0]]
        if marker >> 4 != 0x1:
            raise ValueError("expected an integer count marker in bplist")
        marker_size = 1 << (marker & 0x0F)
        pos[0] += 1
        count = read_int(pos[0], marker_size)
        pos[0] += marker_size
        return count

    pos = [0]
    cache: Dict[int, Any] = {}
    in_progress: set = set()

    def parse(index: int, depth: int = 0) -> Any:
        if depth > _MAX_BPLIST_DEPTH:
            raise ValueError("bplist nesting too deep")
        if index >= num_objects:
            raise ValueError(f"object index {index} out of range")
        if index in cache:
            return cache[index]
        if index in in_progress:  # cyclic reference guard
            return None
        in_progress.add(index)
        try:
            value = parse_object(index, depth)
        finally:
            in_progress.discard(index)
        if isinstance(value, (dict, list)):
            cache[index] = value
        return value

    def parse_object(index: int, depth: int) -> Any:
        pos[0] = offsets[index]
        marker = data[pos[0]]
        pos[0] += 1
        high, low = marker >> 4, marker & 0x0F
        if high == 0x0:
            if low == 0x0:
                return None
            if low == 0x8:
                return False
            if low == 0x9:
                return True
            return None
        if high == 0x1:  # int: 1/2/4-byte are unsigned, 8-byte is signed (CFBinaryPlist)
            size = 1 << low
            if size > 8:
                return None
            return int.from_bytes(data[pos[0]:pos[0] + size], "big", signed=low >= 3)
        if high == 0x2:  # real
            size = 1 << low
            if size == 4:
                return struct.unpack(">f", data[pos[0]:pos[0] + 4])[0]
            if size == 8:
                return struct.unpack(">d", data[pos[0]:pos[0] + 8])[0]
            raise ValueError("unsupported real size")
        if high == 0x3:  # date (seconds since 2001-01-01)
            if low != 0x3:
                raise ValueError("unsupported date marker")
            secs = struct.unpack(">d", data[pos[0]:pos[0] + 8])[0]
            return datetime(2001, 1, 1) + timedelta(seconds=secs)
        if high == 0x4:  # data
            count = object_count(low)
            value = data[pos[0]:pos[0] + count]
            return value
        if high == 0x5:  # ascii string
            count = object_count(low)
            return data[pos[0]:pos[0] + count].decode("ascii", "replace")
        if high == 0x6:  # utf16be string
            count = object_count(low)
            return data[pos[0]:pos[0] + count * 2].decode("utf-16-be", "replace")
        if high == 0x8:  # uid
            return {"CF$UID": read_int(pos[0], low + 1)}
        if high in (0xA, 0xC):  # array / set
            count = object_count(low)
            base = pos[0]
            refs = [read_ref(base + i * ref_size) for i in range(count)]
            return [parse(ref, depth + 1) for ref in refs]
        if high == 0xD:  # dict
            count = object_count(low)
            base = pos[0]
            keys = [read_ref(base + i * ref_size) for i in range(count)]
            vals = [read_ref(base + (count + i) * ref_size) for i in range(count)]
            out: Dict[str, Any] = {}
            for k, v in zip(keys, vals):
                key = parse(k, depth + 1)
                if not isinstance(key, str):
                    key = str(key)
                out[key] = parse(v, depth + 1)
            return out
        raise ValueError(f"unsupported bplist object marker 0x{marker:02x}")

    return parse(top_object)


def parse_plist_bytes(data: bytes) -> Dict[str, Any]:
    """Parse XML or binary plist content into a dict plus format metadata."""
    fmt = "bplist" if data.startswith(b"bplist") else "xml"
    try:
        if fmt == "bplist":
            value = _parse_bplist(data)
        else:
            value = plistlib.loads(data)
    except Exception as exc:  # noqa: BLE001 - normalise to a tool error
        raise InputError(f"cannot parse property list ({fmt}): {exc}", code="plist-invalid",
                         remediation="verify the file is a complete plist; binary conversion is "
                                     "available with 'inspect-plist --convert xml'") from exc
    if isinstance(value, (bytes, bytearray)):
        value = {"value": value.hex()}
    if not isinstance(value, dict):
        value = {"root": _jsonable(value)}
    return {"format": fmt, "value": value}


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"data_hex_prefix": v[:64].hex(), "length": len(v)}
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, float):
        return v
    return v


def load_plist_file(path: Path, limits: Limits) -> Dict[str, Any]:
    if path.stat().st_size > limits.max_file_bytes:
        raise LimitError(f"plist {path.name} exceeds --max-file-bytes", code="file-too-large")
    parsed = parse_plist_bytes(path.read_bytes())
    parsed["path"] = path.as_posix()
    parsed["sha256"] = sha256_file(path, limits.max_file_bytes)
    return parsed


def convert_plist(data: bytes, target: str) -> bytes:
    """Convert between xml and binary without losing plist value types."""
    if target not in ("xml", "binary"):
        raise InputError("target format must be xml or binary", code="bad-format")
    parsed = parse_plist_bytes(data)["value"]
    if isinstance(parsed, dict) and set(parsed.keys()) == {"root"}:
        parsed = parsed["root"]
    fmt = plistlib.FMT_XML if target == "xml" else plistlib.FMT_BINARY
    try:
        return plistlib.dumps(parsed, fmt=fmt, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"cannot re-serialise property list as {target}: {exc}",
                         code="plist-convert-failed") from exc


# ==========================================================================
# Mach-O
# ==========================================================================
MH_MAGICS = {
    0xFEEDFACE: ("32", "<"), 0xFEEDFACF: ("64", "<"),
    0xCEFAEDFE: ("32", ">"), 0xCFFAEDFE: ("64", ">"),
}
FAT_MAGICS = {0xCAFEBABE, 0xCAFEBABF, 0xBEBAFECA, 0xBFBAFECA}

CPU_TYPES = {
    7: "x86", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64",
    0x0200000C: "arm64_32", 18: "ppc", 0x01000012: "ppc64",
}
CPU_SUBTYPES_ARM64 = {0: "", 1: "v8", 2: "e", 0x80000002: "e"}
FILE_TYPES = {1: "object", 2: "execute", 5: "core", 6: "dylib", 7: "dylinker", 8: "bundle",
              9: "dylib_stub", 10: "dsym", 11: "kext_bundle"}
PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 5: "bridgeos", 6: "maccatalyst",
             7: "ios-simulator", 8: "tvos-simulator", 9: "watchos-simulator", 10: "driverkit",
             11: "visionos", 12: "visionos-simulator"}

LC_SEGMENT = 0x1
LC_SYMTAB = 0x2
LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_SEGMENT_64 = 0x19
LC_UUID = 0x1B
LC_CODE_SIGNATURE = 0x1D
LC_REEXPORT_DYLIB = 0x8000001F
LC_ENCRYPTION_INFO = 0x21
LC_VERSION_MIN_IPHONEOS = 0x25
LC_MAIN = 0x80000028
LC_ENCRYPTION_INFO_64 = 0x2C
LC_BUILD_VERSION = 0x32
LC_DYLD_INFO_ONLY = 0x80000022
LC_RPATH = 0x8000001C
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_LAZY_LOAD_DYLIB = 0x20

_LC_NAMES = {LC_SEGMENT: "LC_SEGMENT", LC_SEGMENT_64: "LC_SEGMENT_64", LC_SYMTAB: "LC_SYMTAB",
             LC_LOAD_DYLIB: "LC_LOAD_DYLIB", LC_ID_DYLIB: "LC_ID_DYLIB",
             LC_LOAD_WEAK_DYLIB: "LC_LOAD_WEAK_DYLIB", LC_UUID: "LC_UUID",
             LC_CODE_SIGNATURE: "LC_CODE_SIGNATURE", LC_REEXPORT_DYLIB: "LC_REEXPORT_DYLIB",
             LC_ENCRYPTION_INFO: "LC_ENCRYPTION_INFO",
             LC_ENCRYPTION_INFO_64: "LC_ENCRYPTION_INFO_64",
             LC_VERSION_MIN_IPHONEOS: "LC_VERSION_MIN_IPHONEOS", LC_MAIN: "LC_MAIN",
             LC_BUILD_VERSION: "LC_BUILD_VERSION", LC_RPATH: "LC_RPATH",
             LC_DYLD_INFO_ONLY: "LC_DYLD_INFO_ONLY", LC_LOAD_UPWARD_DYLIB: "LC_LOAD_UPWARD_DYLIB",
             LC_LAZY_LOAD_DYLIB: "LC_LAZY_LOAD_DYLIB"}


def _version_string(value: int) -> str:
    return f"{value >> 16}.{(value >> 8) & 0xFF}.{value & 0xFF}"


@dataclass
class MachOSlice:
    arch: str
    cputype: int
    cpusubtype: int
    filetype: str
    bits: str
    uuid: Optional[str] = None
    platform: Optional[str] = None
    min_os: Optional[str] = None
    sdk: Optional[str] = None
    encrypted: bool = False
    cryptid: int = 0
    cryptoff: int = 0
    cryptsize: int = 0
    dylibs: List[str] = field(default_factory=list)
    weak_dylibs: List[str] = field(default_factory=list)
    rpaths: List[str] = field(default_factory=list)
    install_name: Optional[str] = None
    has_code_signature: bool = False
    code_signature_offset: int = 0
    code_signature_size: int = 0
    entry_point: Optional[int] = None
    segments: List[Dict[str, Any]] = field(default_factory=list)
    load_commands: List[str] = field(default_factory=list)
    offset: int = 0

    def as_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["segments"] = d["segments"][:12]
        d["load_commands"] = d["load_commands"][:64]
        return d


def parse_macho(data: bytes, *, path: str = "", max_slices: int = 8) -> Dict[str, Any]:
    """Parse a thin or universal Mach-O image from an in-memory byte buffer."""
    if len(data) < 32:
        raise InputError("buffer too small to be Mach-O", code="macho-truncated")
    magic_be = struct.unpack(">I", data[:4])[0]
    slices: List[MachOSlice] = []
    if magic_be in FAT_MAGICS:
        nfat = struct.unpack(">I", data[4:8])[0]
        fat64 = magic_be in (0xCAFEBABF, 0xBFBAFECA)
        entry = 32 if fat64 else 20
        if nfat > max_slices:
            nfat = max_slices
        for i in range(nfat):
            base = 8 + i * entry
            if base + entry > len(data):
                break
            if fat64:
                _cpu, _sub, off, size, _align, _res = struct.unpack(">IIQQII", data[base:base + 32])
            else:
                _cpu, _sub, off, size, _align = struct.unpack(">IIIII", data[base:base + 20])
            if off + size > len(data) or size < 32:
                continue
            sub = parse_macho(data[off:off + size], max_slices=1)
            for s in sub.get("slices", []):
                s["offset"] = off
                slices.append(MachOSlice(**{k: v for k, v in s.items() if k in MachOSlice.__annotations__}))
            if sub.get("fat"):
                continue
        return {"path": path, "fat": True, "slices": [s.as_dict() for s in slices],
                "architectures": [s.arch for s in slices]}

    magic_le = struct.unpack("<I", data[:4])[0]
    if magic_le not in MH_MAGICS:
        raise InputError(f"not a Mach-O magic: 0x{magic_le:08x}", code="macho-invalid")
    bits, endian = MH_MAGICS[magic_le]
    s = _parse_thin(data, bits, endian)
    return {"path": path, "fat": False, "slices": [s.as_dict()], "architectures": [s.arch]}


def _parse_thin(data: bytes, bits: str, endian: str) -> MachOSlice:
    hdr_fmt = endian + ("IIIIIIII" if bits == "64" else "IIIIIII")
    fields = struct.unpack(hdr_fmt, data[: struct.calcsize(hdr_fmt)])
    # fields[0] is the magic; the header is magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds
    cputype, cpusubtype, filetype, ncmds, sizeofcmds = fields[1], fields[2], fields[3], fields[4], fields[5]
    arch_base = CPU_TYPES.get(cputype, f"cpu{cputype}")
    subtype = cpusubtype & 0x00FFFFFF
    arch = arch_base
    if arch_base == "arm64" and subtype in CPU_SUBTYPES_ARM64 and CPU_SUBTYPES_ARM64[subtype]:
        arch = f"arm64{CPU_SUBTYPES_ARM64[subtype]}"
    sl = MachOSlice(arch=arch, cputype=cputype, cpusubtype=cpusubtype,
                    filetype=FILE_TYPES.get(filetype, str(filetype)), bits=bits)
    if sizeofcmds + 32 > len(data):
        raise InputError("Mach-O load command region is truncated", code="macho-truncated")
    off = 32 if bits == "64" else 28
    end = off + sizeofcmds
    while off + 8 <= end:
        cmd, cmdsize = struct.unpack(endian + "II", data[off:off + 8])
        if cmdsize < 8 or off + cmdsize > len(data):
            break
        body = data[off:off + cmdsize]
        name = _LC_NAMES.get(cmd, f"0x{cmd:08x}")
        sl.load_commands.append(name)
        try:
            if cmd == LC_UUID and cmdsize >= 24:
                sl.uuid = "-".join(body[8:24].hex()[i:i + 8] for i in (0, 8, 12, 16)).upper()
                raw = body[8:24].hex().upper()
                sl.uuid = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
            elif cmd == LC_BUILD_VERSION and cmdsize >= 24:
                platform, minos, sdk = struct.unpack(endian + "III", body[8:20])
                sl.platform = PLATFORMS.get(platform, str(platform))
                sl.min_os = _version_string(minos)
                sl.sdk = _version_string(sdk)
            elif cmd == LC_VERSION_MIN_IPHONEOS and cmdsize >= 16:
                version, sdk = struct.unpack(endian + "II", body[8:16])
                sl.platform = sl.platform or "ios"
                sl.min_os = _version_string(version)
                sl.sdk = _version_string(sdk)
            elif cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64) and cmdsize >= 20:
                cryptoff, cryptsize, cryptid = struct.unpack(endian + "III", body[8:20])
                sl.cryptoff, sl.cryptsize, sl.cryptid = cryptoff, cryptsize, cryptid
                sl.encrypted = cryptid != 0
            elif cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB,
                         LC_LAZY_LOAD_DYLIB):
                name_off = struct.unpack(endian + "I", body[8:12])[0]
                dylib = body[name_off:].split(b"\x00", 1)[0].decode("utf-8", "replace")
                (sl.weak_dylibs if cmd == LC_LOAD_WEAK_DYLIB else sl.dylibs).append(dylib)
            elif cmd == LC_ID_DYLIB:
                name_off = struct.unpack(endian + "I", body[8:12])[0]
                sl.install_name = body[name_off:].split(b"\x00", 1)[0].decode("utf-8", "replace")
            elif cmd == LC_RPATH:
                rp_off = struct.unpack(endian + "I", body[8:12])[0]
                sl.rpaths.append(body[rp_off:].split(b"\x00", 1)[0].decode("utf-8", "replace"))
            elif cmd == LC_CODE_SIGNATURE and cmdsize >= 16:
                dataoff, datasize = struct.unpack(endian + "II", body[8:16])
                sl.has_code_signature = True
                sl.code_signature_offset = dataoff
                sl.code_signature_size = datasize
            elif cmd == LC_MAIN and cmdsize >= 16:
                sl.entry_point = struct.unpack(endian + "Q", body[8:16])[0]
            elif cmd in (LC_SEGMENT, LC_SEGMENT_64):
                if cmd == LC_SEGMENT_64 and cmdsize >= 72:
                    segname = body[8:24].split(b"\x00", 1)[0].decode()
                    vmaddr, vmsize, fileoff, filesize = struct.unpack(endian + "QQQQ", body[24:56])
                elif cmdsize >= 56:
                    segname = body[8:24].split(b"\x00", 1)[0].decode()
                    vmaddr, vmsize, fileoff, filesize = struct.unpack(endian + "IIII", body[24:40])
                else:
                    raise ValueError
                if len(sl.segments) < 32:
                    sl.segments.append({"name": segname, "fileoff": fileoff, "filesize": filesize,
                                        "vmsize": vmsize})
        except (struct.error, ValueError):
            pass
        off += cmdsize
    return sl


@dataclass
class CodeSignature:
    identifier: Optional[str] = None
    team_id: Optional[str] = None
    entitlements: Optional[Dict[str, Any]] = None
    entitlements_der: bool = False
    flags: Optional[int] = None
    cd_hashes: List[str] = field(default_factory=list)
    slots: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def parse_code_signature(data: bytes, offset: int, size: int) -> CodeSignature:
    """Extract identifier/team/entitlements from an embedded code signature blob."""
    sig = CodeSignature()
    if offset <= 0 or size <= 12 or offset + size > len(data):
        return sig
    blob = data[offset:offset + size]
    magic, length, count = struct.unpack(">III", blob[:12])
    if magic not in (0xFADE0CC0, 0xFADE0C02):
        return sig
    count = min(count, 32)
    for i in range(count):
        base = 12 + i * 8
        if base + 8 > len(blob):
            break
        slot_type, slot_off = struct.unpack(">II", blob[base:base + 8])
        name = {0: "CSSLOT_CODEDIRECTORY", 1: "CSSLOT_INFOSLOT", 2: "CSSLOT_REQUIREMENTS",
                3: "CSSLOT_RESOURCEDIR", 5: "CSSLOT_ENTITLEMENTS", 7: "CSSLOT_DER_ENTITLEMENTS",
                0x10000: "CSSLOT_ALTERNATE_CODEDIRECTORIES"}.get(slot_type, f"slot-{slot_type}")
        sig.slots.append(name)
        if slot_off + 8 > len(blob):
            continue
        if slot_type == 0:
            _parse_code_directory(blob, slot_off, sig)
        elif slot_type == 5:
            smagic, slen = struct.unpack(">II", blob[slot_off:slot_off + 8])
            if smagic == 0xFADE7171:
                payload = blob[slot_off + 8: slot_off + slen]
                try:
                    sig.entitlements = parse_plist_bytes(payload)["value"]
                except InputError:
                    pass
        elif slot_type == 7:
            sig.entitlements_der = True
    return sig


def _parse_code_directory(blob: bytes, off: int, sig: CodeSignature) -> None:
    try:
        magic, length, version, flags = struct.unpack(">IIII", blob[off:off + 16])
        if magic != 0xFADE0C02:
            return
        sig.flags = flags
        ident_off = struct.unpack(">I", blob[off + 20:off + 24])[0]
        end = blob.find(b"\x00", off + ident_off)
        sig.identifier = blob[off + ident_off:end if end > 0 else off + ident_off + 255].decode("utf-8", "replace")
        if version >= 0x20200:
            team_off = struct.unpack(">I", blob[off + 48:off + 52])[0]
            if team_off:
                tend = blob.find(b"\x00", off + team_off)
                sig.team_id = blob[off + team_off:tend if tend > 0 else off + team_off + 64].decode("utf-8", "replace")
    except (struct.error, ValueError):
        return


def macho_entitlements(path: Path, limits: Limits) -> Dict[str, Any]:
    p = Path(path)
    size = p.stat().st_size
    if size > limits.max_file_bytes:
        raise LimitError(f"{p.name} is larger than --max-file-bytes", code="file-too-large",
                         remediation="raise --max-file-bytes to read a large binary")
    info = parse_macho(p.read_bytes(), path=p.as_posix())
    out: Dict[str, Any] = {"path": p.as_posix(), "slices": []}
    data = p.read_bytes()
    for s in info["slices"]:
        if not s["has_code_signature"]:
            out["slices"].append({"arch": s["arch"], "signed": False})
            continue
        sig = parse_code_signature(data, s["code_signature_offset"], s["code_signature_size"])
        out["slices"].append({"arch": s["arch"], "signed": True, **sig.as_dict()})
    return out


# ==========================================================================
# ZIP / IPA
# ==========================================================================
@dataclass
class ZipIssue:
    code: str
    level: str
    message: str
    entry: str = ""


def analyze_zip(path: Path, limits: Limits) -> Dict[str, Any]:
    """Central-directory level health check that never extracts to disk."""
    issues: List[ZipIssue] = []
    info: Dict[str, Any] = {"path": path.as_posix(), "entries": 0, "uncompressed_bytes": 0,
                            "compressed_bytes": 0, "duplicates": [], "case_collisions": [],
                            "unsafe_paths": [], "compression_methods": {}, "nested_archives": [],
                            "max_depth": 0, "crc_verified": 0, "encrypted_entries": []}
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise InputError(f"not a readable ZIP container: {exc}", code="zip-invalid",
                         remediation="the archive may be truncated; re-download or re-export it") from exc
    with zf:
        entries = zf.infolist()
        info["entries"] = len(entries)
        if len(entries) > limits.max_entries:
            issues.append(ZipIssue("zip/entry-limit", "warn",
                                   f"{len(entries)} entries exceeds --max-entries {limits.max_entries}; "
                                   "only the first entries were classified"))
        names: Dict[str, int] = {}
        lower: Dict[str, str] = {}
        total = 0
        for entry in entries[: limits.max_entries]:
            total += entry.file_size
            method = {0: "stored", 8: "deflate", 12: "bzip2", 14: "lzma", 93: "zstd"}.get(
                entry.compress_type, str(entry.compress_type))
            info["compression_methods"][method] = info["compression_methods"].get(method, 0) + 1
            names[entry.filename] = names.get(entry.filename, 0) + 1
            low = entry.filename.lower()
            if low in lower and lower[low] != entry.filename:
                info["case_collisions"].append([lower[low], entry.filename])
            lower.setdefault(low, entry.filename)
            if entry.flag_bits & 0x1:
                info["encrypted_entries"].append(entry.filename)
            rel = safe_relpath(entry.filename)
            if rel is None:
                info["unsafe_paths"].append(entry.filename)
            depth = entry.filename.count("/")
            info["max_depth"] = max(info["max_depth"], depth)
            if depth > limits.max_depth:
                issues.append(ZipIssue("zip/depth", "warn",
                                       f"entry nesting depth {depth} exceeds --max-depth", entry.filename))
            if entry.file_size > limits.max_file_bytes:
                issues.append(ZipIssue("zip/entry-too-large", "warn",
                                       f"entry is {human_bytes(entry.file_size)} which exceeds "
                                       f"--max-file-bytes", entry.filename))
            if rel and rel.lower().endswith((".zip", ".ipa", ".aab", ".tar", ".gz")):
                info["nested_archives"].append(entry.filename)
            if len(entry.filename) > 255:
                issues.append(ZipIssue("zip/long-name", "warn", "entry name longer than 255 bytes",
                                       entry.filename[:80] + "..."))
        info["duplicates"] = [n for n, c in names.items() if c > 1]
        info["uncompressed_bytes"] = total
        info["compressed_bytes"] = path.stat().st_size

        if total > limits.max_total_bytes:
            issues.append(ZipIssue("zip/bomb-guard", "error",
                                   f"declared uncompressed size {human_bytes(total)} exceeds "
                                   f"--max-total-bytes {human_bytes(limits.max_total_bytes)}"))
        if total > 100 * max(info["compressed_bytes"], 1) and total > 50 << 20:
            issues.append(ZipIssue("zip/ratio", "warn",
                                   "compression ratio above 100:1; possible decompression bomb"))

        # CRC verification (bounded)
        verified = 0
        for entry in entries[: min(len(entries), 4000)]:
            if entry.is_dir():
                continue
            if entry.file_size > 8 << 20:
                continue
            try:
                with zf.open(entry) as fh:
                    while fh.read(1 << 20):
                        pass
                verified += 1
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                issues.append(ZipIssue("zip/crc", "error", f"CRC/decompression failure: {exc}",
                                       entry.filename))
        info["crc_verified"] = verified

    for dup in info["duplicates"][:20]:
        issues.append(ZipIssue("zip/duplicate-name", "warn",
                               "duplicate entry name; extractors disagree on the winner", dup))
    for pair in info["case_collisions"][:20]:
        issues.append(ZipIssue("zip/case-collision", "warn",
                               f"case-insensitive collision between '{pair[0]}' and '{pair[1]}'"))
    for bad in info["unsafe_paths"][:20]:
        issues.append(ZipIssue("zip/path-traversal", "error",
                               "entry escapes the extraction root; archive is unsafe to extract "
                               "automatically", bad))
    for nested in info["nested_archives"][:10]:
        issues.append(ZipIssue("zip/nested-archive", "warn",
                               "nested archive inside the container; inspect it separately", nested))
    if not issues:
        issues.append(ZipIssue("zip/healthy", "info", "central directory, CRCs and entry names are consistent"))

    info["issues"] = [i.__dict__ for i in issues]
    info["healthy"] = not any(i.level in ("error", "critical") for i in issues)
    return info


def _zip_read(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise LimitError(f"{name} is {human_bytes(info.file_size)}; exceeds --max-file-bytes",
                         code="file-too-large")
    with zf.open(info) as fh:
        return fh.read(limit + 1)[:limit]


PRIVACY_MANIFEST = "PrivacyInfo.xcprivacy"


def inspect_ipa(path: Path, limits: Limits) -> Dict[str, Any]:
    """Full structural inspection of an IPA (or any ZIP containing Payload/*.app)."""
    kind = sniff_kind(path)
    if not path.is_file():
        raise InputError("inspect-ipa expects a file (.ipa or .zip)", code="input-not-file")
    zip_health = analyze_zip(path, limits)
    result: Dict[str, Any] = {"container": {"kind": kind["kind"], "zip": zip_health},
                              "apps": [], "extensions": [], "frameworks": [],
                              "architectures": [], "encrypted": None, "signing": {}}
    findings: List[Tuple[str, str, str]] = []  # code, level, message

    with zipfile.ZipFile(path) as zf:
        names = [i.filename for i in zf.infolist()]
        apps = sorted({n.split("/")[1] for n in names
                       if n.startswith("Payload/") and n.count("/") >= 2 and n.split("/")[1].endswith(".app")})
        if not apps:
            findings.append(("ipa/no-payload", "error",
                             "no Payload/<name>.app directory found; this is not a launchable iOS app archive"))
        if len(apps) > 1:
            findings.append(("ipa/multiple-apps", "error",
                             f"{len(apps)} application bundles under Payload/; an IPA must contain exactly one"))
        result["apps"] = apps
        if not apps:
            result["findings"] = findings
            return result

        app = apps[0]
        app_prefix = f"Payload/{app}/"
        app_entries = [n for n in names if n.startswith(app_prefix) and n != app_prefix]
        top_level = {n[len(app_prefix):].split("/")[0] for n in app_entries}
        info_name = f"{app_prefix}Info.plist"
        info: Dict[str, Any] = {}
        if info_name in names:
            try:
                info = parse_plist_bytes(_zip_read(zf, info_name, limits.max_file_bytes))["value"]
            except InputError as exc:
                findings.append(("ipa/info-plist-invalid", "error", f"Info.plist unreadable: {exc.message}"))
        else:
            findings.append(("ipa/info-plist-missing", "error",
                             "Payload/<app>/Info.plist is missing; the bundle cannot install"))

        exe = info.get("CFBundleExecutable")
        exe_path = f"{app_prefix}{exe}" if exe else None
        if exe and exe_path not in names:
            findings.append(("ipa/executable-missing", "error",
                             f"CFBundleExecutable '{exe}' does not exist in the bundle"))
        elif exe:
            try:
                head = _zip_read(zf, exe_path, min(limits.max_file_bytes, 8 << 20))
                prog = parse_macho(head, path=exe_path)
                result["architectures"] = prog["architectures"]
                for s in prog["slices"]:
                    if s["uuid"]:
                        result["signing"].setdefault("main_uuid", {})[s["arch"]] = s["uuid"]
                enc = [s for s in prog["slices"] if s["encrypted"]]
                result["encrypted"] = bool(enc) if prog["slices"] else None
                if enc:
                    findings.append(("ipa/encrypted", "warn",
                                     "main executable carries a non-zero cryptid (App Store FairPlay "
                                     "encryption); it cannot be rebuilt or re-signed without "
                                     "authorized unencrypted sources"))
                result["platform"] = prog["slices"][0].get("platform")
                result["minimum_os"] = prog["slices"][0].get("min_os") or info.get("MinimumOSVersion")
                result["sdk"] = prog["slices"][0].get("sdk")
                result["macho"] = {"fat": prog["fat"],
                                  "slices": [{k: s[k] for k in ("arch", "platform", "min_os", "sdk",
                                                                "filetype", "encrypted")}
                                             for s in prog["slices"]]}
            except (InputError, LimitError) as exc:
                findings.append(("ipa/executable-unparsed", "warn",
                                 f"could not parse the main executable: {getattr(exc, 'message', exc)}"))

        # extensions / frameworks / nested code
        ext_dirs = sorted({n[len(app_prefix):].split("/")[1] for n in app_entries
                           if n[len(app_prefix):].startswith("PlugIns/") and
                           n[len(app_prefix):].split("/")[1].endswith(".appex")})
        fw_dirs = sorted({n[len(app_prefix):].split("/")[1] for n in app_entries
                          if n[len(app_prefix):].startswith("Frameworks/") and
                          n[len(app_prefix):].split("/")[1].endswith(".framework")})
        dylibs = sorted({n[len(app_prefix):].split("/")[1] for n in app_entries
                         if n[len(app_prefix):].startswith("Frameworks/") and
                         n[len(app_prefix):].split("/")[1].endswith(".dylib")})
        result["extensions"] = [f"{app_prefix}PlugIns/{e}/" for e in ext_dirs]
        result["frameworks"] = [f"{app_prefix}Frameworks/{f}/" for f in fw_dirs] + \
                               [f"{app_prefix}Frameworks/{d}" for d in dylibs]

        ext_details = []
        for e in ext_dirs:
            ep = f"{app_prefix}PlugIns/{e}/Info.plist"
            edata: Dict[str, Any] = {}
            if ep in names:
                try:
                    edata = parse_plist_bytes(_zip_read(zf, ep, limits.max_file_bytes))["value"]
                except InputError:
                    edata = {}
            point = (edata.get("NSExtension") or {}).get("NSExtensionPointIdentifier", "")
            ext_details.append({"bundle": e, "identifier": edata.get("CFBundleIdentifier", ""),
                                "extension_point": point,
                                "principal_class": (edata.get("NSExtension") or {}).get(
                                    "NSExtensionPrincipalClass", ""),
                                "deployment_target": edata.get("MinimumOSVersion", "")})
        result["extension_details"] = ext_details

        fw_details = []
        for f in fw_dirs:
            fname = f[:-len(".framework")]
            fw_head = f"{app_prefix}Frameworks/{f}/{fname}"
            detail: Dict[str, Any] = {"bundle": f, "binary": fw_head}
            if fw_head in names:
                try:
                    head = _zip_read(zf, fw_head, min(limits.max_file_bytes, 8 << 20))
                    prog = parse_macho(head, path=fw_head)
                    detail["architectures"] = prog["architectures"]
                    detail["min_os"] = prog["slices"][0].get("min_os")
                    detail["install_name"] = prog["slices"][0].get("install_name")
                    detail["rpaths"] = prog["slices"][0].get("rpaths", [])
                    detail["encrypted"] = any(s["encrypted"] for s in prog["slices"])
                except (InputError, LimitError) as exc:
                    detail["error"] = str(exc)
            else:
                detail["error"] = "framework binary missing (name does not match bundle)"
            fw_details.append(detail)
        result["framework_details"] = fw_details

        # nested signing order: deepest-first list of code objects
        code_objects = []
        for n in app_entries:
            base = n[len(app_prefix):]
            if base.count("/") == 2 and base.split("/")[0] in ("Frameworks", "PlugIns") and \
                    not base.endswith("/") and "." not in base.split("/")[1]:
                code_objects.append((base.count("/"), n))
        code_objects.sort(key=lambda t: -t[0])
        result["signing"]["nested_signing_order"] = [n for _, n in code_objects][:40]

        # privacy manifests
        manifests = [n for n in names if n.endswith(PRIVACY_MANIFEST)]
        result["privacy_manifests"] = manifests[:40]

        # resources and metadata summary
        result["bundle"] = {
            "app": app, "path": app_prefix, "entries": len(app_entries),
            "top_level": sorted(top_level)[:60],
            "info_plist": _jsonable(info),
            "bundle_id": info.get("CFBundleIdentifier", ""),
            "bundle_name": info.get("CFBundleName", ""),
            "display_name": info.get("CFBundleDisplayName", ""),
            "short_version": info.get("CFBundleShortVersionString", ""),
            "build_version": info.get("CFBundleVersion", ""),
            "minimum_os": info.get("MinimumOSVersion", ""),
            "platform": info.get("DTPlatformName", ""),
            "sdk": info.get("DTSDKName", ""),
            "device_family": info.get("UIDeviceFamily", []),
            "package_type": info.get("CFBundlePackageType", ""),
            "required_capabilities": info.get("UIRequiredDeviceCapabilities", []),
            "url_schemes": [u.get("CFBundleURLName", "") for u in info.get("CFBundleURLTypes", []) or []],
            "background_modes": info.get("UIBackgroundModes", []),
            "has_scene_manifest": "UIApplicationSceneManifest" in info,
            "localizations": info.get("CFBundleLocalizations", []),
            "executable": exe or "",
        }
        result["localized"] = _localized_summary(names, app_prefix)

    # ---- derived findings -------------------------------------------------
    bundle = result["bundle"]
    if not bundle["bundle_id"]:
        findings.append(("ipa/bundle-id-missing", "error", "CFBundleIdentifier is absent"))
    if not bundle["short_version"]:
        findings.append(("ipa/version-missing", "warn", "CFBundleShortVersionString is absent"))
    if not bundle["device_family"]:
        findings.append(("ipa/device-family-missing", "warn", "UIDeviceFamily is absent"))
    archs = result["architectures"]
    if archs and all(a in ("arm64", "arm64e", "arm64v8") for a in archs):
        findings.append(("ipa/arch-64", "info", f"arm64-only build: installable on iOS 11+ ({', '.join(archs)})"))
    if any(a in ("armv7", "armv7s") for a in archs):
        findings.append(("ipa/arch-32", "warn",
                         "32-bit slice present: it cannot run on iOS 11 or later; a 64-bit rebuild needs "
                         "source or equivalent authorized redevelopment"))
    if "x86_64" in archs or "i386" in archs:
        findings.append(("ipa/arch-simulator", "warn",
                         "Simulator slices are present; this build cannot run on a physical device"))
    result["findings"] = findings
    return result


def _localized_summary(names: Sequence[str], app_prefix: str) -> Dict[str, Any]:
    locs = set()
    for n in names:
        if n.startswith(app_prefix) and ".lproj/" in n:
            locs.add(n[len(app_prefix):].split(".lproj/")[0])
    strings = [n for n in names if n.startswith(app_prefix) and n.endswith(".lproj/InfoPlist.strings")]
    return {"languages": sorted(locs), "info_plist_strings": strings[:40]}


# ==========================================================================
# provisioning profiles
# ==========================================================================
def parse_mobileprovision(path: Path, limits: Limits) -> Dict[str, Any]:
    data = path.read_bytes()
    if len(data) > limits.max_file_bytes:
        raise LimitError("profile exceeds --max-file-bytes", code="file-too-large")
    out: Dict[str, Any] = {"path": path.as_posix(), "sha256": sha256_file(path, limits.max_file_bytes),
                           "cms": data[:4] == b"\x30\x82" or data[:1] == b"\x30",
                           "size": len(data)}
    # the CMS payload embeds an XML plist; extract the outermost plist element
    start = data.find(b"<?xml")
    if start < 0:
        start = data.find(b"<plist")
    end = data.rfind(b"</plist>")
    if start < 0 or end < 0:
        raise InputError("no XML property list payload found in the profile", code="profile-unparsable",
                         remediation="profiles are CMS-signed; verify the file is a .mobileprovision "
                                     "(macOS: security cms -D -i profile.mobileprovision)")
    parsed = parse_plist_bytes(data[start:end + len(b"</plist>")])
    profile = parsed["value"]
    out["parse_ok"] = True
    out["profile"] = _jsonable(profile)
    ents = profile.get("Entitlements", {})
    out["summary"] = {
        "uuid": profile.get("UUID", ""),
        "name": profile.get("Name", ""),
        "team_name": profile.get("TeamName", ""),
        "team_identifiers": profile.get("TeamIdentifier", []),
        "app_id_name": profile.get("AppIDName", ""),
        "application_identifier": ents.get("application-identifier", ""),
        "bundle_pattern": (ents.get("application-identifier", "").split(".", 1) + [""])[1]
        if ents.get("application-identifier") else "",
        "aps_environment": ents.get("aps-environment", ""),
        "beta_reports": bool(ents.get("beta-reports-active")),
        "get_task_allow": bool(ents.get("get-task-allow")),
        "provisions_all_devices": bool(profile.get("ProvisionsAllDevices")),
        "device_count": len(profile.get("ProvisionedDevices", []) or []),
        "platforms": profile.get("Platform", []),
        "creation_date": _dt(profile.get("CreationDate")),
        "expiration_date": _dt(profile.get("ExpirationDate")),
        "certificate_count": len(profile.get("DeveloperCertificates", []) or []),
        "entitlement_keys": sorted(ents.keys()),
        "distribution_method": _distribution_method(profile, ents),
    }
    exp = profile.get("ExpirationDate")
    if isinstance(exp, datetime):
        # plist dates carry no zone; Apple profiles record UTC, so compare in UTC
        aware = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        out["summary"]["days_until_expiry"] = (aware - datetime.now(timezone.utc)).days
    return out


def _dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


def _distribution_method(profile: Dict[str, Any], ents: Dict[str, Any]) -> str:
    if profile.get("ProvisionsAllDevices"):
        return "enterprise (in-house)"
    if ents.get("get-task-allow"):
        return "development"
    if profile.get("ProvisionedDevices"):
        return "ad-hoc"
    return "app-store"


# ==========================================================================
# crash reports
# ==========================================================================
def parse_crash_report(path: Path, limits: Limits) -> Dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > limits.max_file_bytes:
        raise LimitError("crash report exceeds --max-file-bytes", code="file-too-large")
    text = raw.decode("utf-8", "replace")
    out: Dict[str, Any] = {"path": path.as_posix(), "format": "unknown", "frames": [],
                           "binary_images": [], "uuid_map": {}, "exception": {},
                           "termination": {}, "process": {}, "os": {}}

    if text.lstrip().startswith("{"):
        # modern .ips: JSON header line then JSON body
        lines = [ln for ln in text.splitlines() if ln.strip()]
        header, body = {}, {}
        try:
            header = json.loads(lines[0])
        except (json.JSONDecodeError, IndexError):
            header = {}
        # real .ips bodies are one JSON object (often one long line); accept both
        body = {}
        try:
            parsed_body = json.loads("\n".join(lines[1:]))
            body = parsed_body if isinstance(parsed_body, dict) else {}
        except json.JSONDecodeError:
            for ln in lines[1:]:
                try:
                    candidate = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    body = candidate
                    break
        out["format"] = "ips"
        out["process"] = {"name": header.get("app_name") or body.get("procName", ""),
                          "bundle_id": header.get("bundleID", ""),
                          "version": header.get("app_version", ""),
                          "pid": body.get("pid")}
        out["os"] = {"version": header.get("os_version", ""), "build": header.get("build_version", ""),
                     "platform": header.get("platform", "")}
        out["incident"] = header.get("incident_id", "")
        out["hardware"] = {"model": header.get("modelCode") or body.get("modelCode", "")}
        exc = body.get("exception", {}) or {}
        out["exception"] = {"type": exc.get("type", ""), "signal": exc.get("signal", ""),
                            "codes": exc.get("codes", ""), "subtype": exc.get("subtype", "")}
        term = body.get("termination", {}) or {}
        out["termination"] = {"namespace": term.get("namespace", ""),
                              "code": term.get("code"),
                              "indicator": term.get("indicator", ""),
                              "reasons": term.get("reasons", [])}
        out["asi"] = (body.get("asi") or {}) if isinstance(body.get("asi"), dict) else {}
        for img in body.get("usedImages", []) or []:
            out["binary_images"].append({"name": img.get("name", ""), "uuid": (img.get("uuid") or "").upper(),
                                         "arch": img.get("arch", ""), "path": img.get("path", "")})
        for th in body.get("threads", []) or []:
            if th.get("triggered"):
                for fr in th.get("frames", []) or []:
                    out["frames"].append({
                        "image_index": fr.get("imageIndex"),
                        "image_offset": fr.get("imageOffset"),
                        "symbol": fr.get("symbol", ""),
                        "symbol_location": fr.get("symbolLocation"),
                        "source_file": fr.get("sourceFile", ""),
                        "source_line": fr.get("sourceLine"),
                    })
                out["triggered_thread"] = {"name": th.get("name", ""), "queue": th.get("queue", ""),
                                           "id": th.get("id")}
    else:
        out["format"] = "legacy-text"
        for key, pattern in (
                ("process", r"^Process:\s+(.+?)\s+\[(\d+)\]"),
                ("path", r"^Path:\s+(.+)$"),
                ("identifier", r"^Identifier:\s+(.+)$"),
                ("version", r"^Version:\s+(.+)$"),
                ("os", r"^OS Version:\s+(.+)$"),
                ("hardware", r"^Hardware Model:\s+(.+)$"),
                ("exception_type", r"^Exception Type:\s+(.+)$"),
                ("exception_codes", r"^Exception Codes:\s+(.+)$"),
                ("termination", r"^Termination Reason:\s+(.+)$"),
                ("incident", r"^Incident Identifier:\s+(.+)$"),
                ("crash_reporter", r"^CrashReporter Key:\s+(.+)$"),
        ):
            m = re.search(pattern, text, re.M)
            if m:
                out[key] = m.group(1).strip() if m.lastindex == 1 else m.groups()
        m = re.search(r"^Thread\s+(\d+)\s+(Crashed|.*):", text, re.M)
        if m:
            out["triggered_thread"] = {"index": m.group(1)}
        frames = re.findall(r"^\d+\s+(\S+)\s+(0x[0-9a-fA-F]+)\s+(.+)$", text, re.M)
        for mod, addr, rest in frames[:120]:
            out["frames"].append({"image": mod, "address": addr, "symbol": rest.strip()})
        for img in re.findall(r"^\s*0x[0-9a-fA-F]+\s+-\s+0x[0-9a-fA-F]+\s+(\S+)\s+(.+?)\s+"
                              r"(?:<?([0-9A-Fa-f\-]{36})>?)?\s*$", text, re.M)[:80]:
            out["binary_images"].append({"name": img[0], "path": img[1].strip(), "uuid": img[2].upper()})

    out["uuid_map"] = {img["name"]: img["uuid"] for img in out["binary_images"] if img.get("uuid")}
    return out


# ==========================================================================
# dSYM indexing and UUID matching
# ==========================================================================
def dsym_uuids(path: Path, limits: Limits) -> List[Dict[str, Any]]:
    """Return UUID/arch records for a .dSYM bundle, .dwarf file or Mach-O."""
    if path.is_dir():
        dwarf_dir = path / "Contents" / "Resources" / "DWARF"
        if dwarf_dir.is_dir():
            files = [p for p in dwarf_dir.iterdir() if p.is_file()]
        else:
            files = [p for p in path.rglob("*.dSYM")][:4]
            out: List[Dict[str, Any]] = []
            for f in files:
                out.extend(dsym_uuids(f, limits))
            return out
    else:
        files = [path]
    out = []
    for f in files:
        try:
            if f.stat().st_size > limits.max_file_bytes:
                continue
            info = parse_macho(f.read_bytes(), path=f.as_posix())
        except (InputError, OSError):
            continue
        for s in info["slices"]:
            if s["uuid"]:
                out.append({"uuid": s["uuid"], "arch": s["arch"], "file": f.as_posix(),
                            "size": f.stat().st_size, "mtime": int(f.stat().st_mtime),
                            "sha256_of_path": f.as_posix()})
    return out
