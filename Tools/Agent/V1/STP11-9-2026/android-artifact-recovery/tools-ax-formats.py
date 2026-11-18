"""Parsers for resources.arsc, DEX, ELF, AAB protobuf metadata and hashing.

Standard library only. Every parser is read-only and bounded by an explicit size
limit so an untrusted artifact cannot exhaust memory. This module is loaded by
``tools-android-artifact-recovery.py`` through its hyphen-aware sibling loader.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from tools_ax_parsers import (  # registered under this alias by the loader
    ParseError,
    RES_STRING_POOL_TYPE,
    RES_TABLE_PACKAGE_TYPE,
    RES_TABLE_TYPE,
    RES_TABLE_TYPE_SPEC_TYPE,
    RES_TABLE_TYPE_TYPE,
    TYPE_STRING,
    _chunk_header,
    _decode_pool_strings,
    _u16,
    _u32,
    _u8,
    format_res_value,
)

MAX_PARSE_BYTES = 256 * 1024 * 1024

_TAG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")


# --------------------------------------------------------------------------
# resources.arsc
# --------------------------------------------------------------------------


@dataclass
class ArscEntry:
    resource_id: int
    type_name: str
    key_name: str
    config: str
    value_type: int
    value: Any
    is_complex: bool = False
    public: bool = False


@dataclass
class ArscTable:
    path: Path | None = None
    package_name: str = ""
    package_id: int = 0
    type_names: list[str] = field(default_factory=list)
    key_count: int = 0
    entries: list[ArscEntry] = field(default_factory=list)
    config_count: int = 0
    string_count: int = 0
    problems: list[str] = field(default_factory=list)

    def by_id(self, rid: int) -> list[ArscEntry]:
        return [e for e in self.entries if e.resource_id == rid]

    def summary(self) -> dict[str, Any]:
        types: dict[str, int] = {}
        for e in self.entries:
            types[e.type_name] = types.get(e.type_name, 0) + 1
        return {
            "package_name": self.package_name,
            "package_id": f"0x{self.package_id:02x}",
            "type_names": self.type_names,
            "key_count": self.key_count,
            "entry_count": len(self.entries),
            "config_count": self.config_count,
            "string_count": self.string_count,
            "entries_by_type": dict(sorted(types.items())),
            "problems": self.problems,
        }


_DENSITIES = {120: "ldpi", 160: "mdpi", 213: "tvdpi", 240: "hdpi",
              320: "xhdpi", 480: "xxhdpi", 640: "xxxhdpi",
              0xFFFE: "anydpi", 0xFFFF: "nodpi"}
_UI_MODE = {2: "desk", 3: "car", 4: "television", 5: "appliance",
            6: "watch", 7: "vrheadset"}
_SCREEN_SIZE = {1: "small", 2: "normal", 3: "large", 4: "xlarge"}


def decode_config(cfg: bytes) -> str:
    """Render ``ResTable_config`` into a resource-qualifier-like label."""
    if len(cfg) < 28:
        return "default"
    size = struct.unpack_from("<I", cfg, 0)[0]
    parts: list[str] = []
    mcc, mnc = struct.unpack_from("<HH", cfg, 4)
    if mcc:
        parts.append(f"mcc{mcc}")
    if mnc:
        parts.append(f"mnc{mnc}")
    lang = cfg[8:10].decode("ascii", "replace").strip("\x00")
    region = cfg[10:12].decode("ascii", "replace").strip("\x00")
    if lang:
        parts.append(lang + (f"-r{region}" if region else ""))
    orientation, _touch, density, keyboard, navigation = (
        cfg[12], cfg[13], struct.unpack_from("<H", cfg, 14)[0], cfg[16], cfg[17])
    if orientation == 1:
        parts.append("port")
    elif orientation == 2:
        parts.append("land")
    elif orientation == 3:
        parts.append("square")
    if density:
        parts.append(_DENSITIES.get(density, f"{density}dpi"))
    if keyboard == 2:
        parts.append("keyshidden")
    elif keyboard == 3:
        parts.append("keyssoft")
    if navigation == 3:
        parts.append("nonav")
    elif navigation == 2:
        parts.append("dpad")
    sdk = struct.unpack_from("<H", cfg, 24)[0]
    if sdk:
        parts.append(f"v{sdk}")
    if size >= 36:
        smallest_w = struct.unpack_from("<H", cfg, 34)[0]
        if smallest_w:
            parts.append(f"sw{smallest_w}dp")
    if size >= 40:
        wdp = struct.unpack_from("<H", cfg, 36)[0]
        hdp = struct.unpack_from("<H", cfg, 38)[0]
        if wdp:
            parts.append(f"w{wdp}dp")
        if hdp:
            parts.append(f"h{hdp}dp")
        scr_layout = cfg[28]
        if scr_layout == 1:
            parts.append("ldltr")
        elif scr_layout == 2:
            parts.append("ldrtl")
        ui_mode = cfg[32]
        if (ui_mode & 0x0F) in _UI_MODE:
            parts.append(_UI_MODE[ui_mode & 0x0F])
        night = (ui_mode >> 4) & 0x0F
        if night == 2:
            parts.append("night")
        elif night == 1:
            parts.append("notnight")
    if size >= 52 and cfg[48] in _SCREEN_SIZE:
        parts.append(_SCREEN_SIZE[cfg[48]])
    return "-".join(parts) if parts else "default"


def parse_arsc(path: Path, max_entries: int = 500_000) -> ArscTable:
    size = path.stat().st_size
    if size > MAX_PARSE_BYTES:
        raise ParseError(
            f"resources.arsc is {size} bytes, above the {MAX_PARSE_BYTES} byte parser limit")
    buf = path.read_bytes()
    table = ArscTable(path=path)
    ctype, hsize, total = _chunk_header(buf, 0)
    if ctype != RES_TABLE_TYPE:
        raise ParseError(f"not a resource table (chunk type {ctype:#06x})", 0)
    end = min(total, len(buf))
    strings: list[str] = []
    off = hsize
    while off + 8 <= end:
        ctype, chsize, csize = _chunk_header(buf, off)
        if csize == 0:
            table.problems.append(f"zero-size chunk at {off:#x}")
            break
        if ctype == RES_STRING_POOL_TYPE:
            strings, _ = _decode_pool_strings(buf, off)
            table.string_count = len(strings)
        elif ctype == RES_TABLE_PACKAGE_TYPE:
            _parse_package(buf, off, csize, chsize, table, strings, max_entries)
        off += csize
    return table


def _parse_package(buf: bytes, off: int, csize: int, chsize: int,
                   table: ArscTable, global_strings: list[str], max_entries: int) -> None:
    pkg_end = off + csize
    pkg_id = _u32(buf, off + 8)
    name = buf[off + 12:off + 12 + 256].decode("utf-16-le", "replace").split("\x00")[0]
    type_strings_off = _u32(buf, off + 268)
    key_strings_off = _u32(buf, off + 280)
    table.package_name = table.package_name or name
    table.package_id = table.package_id or pkg_id
    type_names: list[str] = []
    key_strings: list[str] = []
    if type_strings_off and off + type_strings_off + 8 <= len(buf):
        try:
            type_names, _ = _decode_pool_strings(buf, off + type_strings_off)
        except ParseError as exc:
            table.problems.append(f"type string pool: {exc}")
    if key_strings_off and off + key_strings_off + 8 <= len(buf):
        try:
            key_strings, _ = _decode_pool_strings(buf, off + key_strings_off)
        except ParseError as exc:
            table.problems.append(f"key string pool: {exc}")
    table.type_names = type_names
    table.key_count = len(key_strings)
    cur_type_name = ""
    configs: dict[str, int] = {}
    pos = off + chsize
    while pos + 8 <= pkg_end:
        ctype, tchsize, tcsize = _chunk_header(buf, pos)
        if tcsize == 0:
            table.problems.append(f"zero-size type chunk at {pos:#x}")
            break
        if ctype == RES_TABLE_TYPE_SPEC_TYPE:
            tid = _u8(buf, pos + 8)
            cur_type_name = type_names[tid] if tid < len(type_names) else f"type{tid}"
        elif ctype == RES_TABLE_TYPE_TYPE:
            tid = _u8(buf, pos + 8)
            entry_count = _u32(buf, pos + 12)
            entries_start = _u32(buf, pos + 16)
            cfg_size = struct.unpack_from("<I", buf, pos + 20)[0] if tcsize >= 24 else 0
            cfg = buf[pos + 20:pos + 20 + max(0, min(cfg_size, 64))]
            cfg_label = decode_config(cfg) if cfg else "default"
            configs[cfg_label] = configs.get(cfg_label, 0) + 1
            tname = cur_type_name or (
                type_names[tid] if tid < len(type_names) else f"type{tid}")
            offsets_at = pos + tchsize
            entries_at = pos + entries_start
            for i in range(entry_count):
                if len(table.entries) >= max_entries:
                    table.problems.append(f"entry limit {max_entries} reached; truncated")
                    return
                eo = _u32(buf, offsets_at + i * 4)
                if eo == 0xFFFFFFFF:
                    continue
                epos = entries_at + eo
                if epos + 8 > pos + tcsize:
                    table.problems.append(
                        f"entry offsets out of range in {tname}/{cfg_label}")
                    break
                esize = _u16(buf, epos)
                eflags = _u16(buf, epos + 2)
                public = bool(eflags & 0x0002)
                rid = (pkg_id << 24) | (tid << 16) | i
                if eflags & 0x0008:  # compact/sparse entry
                    key_index = esize
                    data_type = (eflags >> 8) & 0xFF
                    data = _u32(buf, epos + 4)
                    table.entries.append(ArscEntry(
                        resource_id=rid, type_name=tname,
                        key_name=key_strings[key_index] if key_index < len(key_strings) else "?",
                        config=cfg_label, value_type=data_type,
                        value=format_res_value(data_type, data, global_strings),
                        public=public))
                    continue
                key_index = _u32(buf, epos + 4)
                key_name = key_strings[key_index] if key_index < len(key_strings) else "?"
                if eflags & 0x0001:  # complex / bag
                    parent = _u32(buf, epos + 8)
                    count = _u32(buf, epos + 12)
                    table.entries.append(ArscEntry(
                        resource_id=rid, type_name=tname, key_name=key_name,
                        config=cfg_label, value_type=-1,
                        value={"bag": count, "parent": f"@0x{parent:08x}"},
                        is_complex=True, public=public))
                    continue
                vpos = epos + esize
                if vpos + 8 > pos + tcsize:
                    table.problems.append(f"value out of range for {tname}/{cfg_label}#{i}")
                    continue
                vtype = _u8(buf, vpos + 3)
                vdata = _u32(buf, vpos + 4)
                table.entries.append(ArscEntry(
                    resource_id=rid, type_name=tname, key_name=key_name,
                    config=cfg_label, value_type=vtype,
                    value=format_res_value(vtype, vdata, global_strings),
                    public=public))
        pos += tcsize
    table.config_count = len(configs)


# --------------------------------------------------------------------------
# DEX
# --------------------------------------------------------------------------


@dataclass
class DexFileInfo:
    name: str
    size: int
    version: str = ""
    checksum_declared: int = 0
    checksum_ok: bool | None = None
    signature_ok: bool | None = None
    file_size_declared: int = 0
    string_ids: int = 0
    type_ids: int = 0
    proto_ids: int = 0
    field_ids: int = 0
    method_ids: int = 0
    class_defs: int = 0
    data_size: int = 0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "size": self.size, "version": self.version,
            "checksum_declared": f"0x{self.checksum_declared:08x}",
            "checksum_ok": self.checksum_ok, "signature_ok": self.signature_ok,
            "file_size_declared": self.file_size_declared,
            "counts": {"strings": self.string_ids, "types": self.type_ids,
                       "protos": self.proto_ids, "fields": self.field_ids,
                       "methods": self.method_ids, "classes": self.class_defs},
            "data_size": self.data_size, "problems": self.problems,
        }


def parse_dex(name: str, buf: bytes) -> DexFileInfo:
    info = DexFileInfo(name=name, size=len(buf))
    if len(buf) < 0x70:
        raise ParseError("DEX header truncated", 0)
    if not buf.startswith(b"dex\n"):
        raise ParseError("missing dex\\n magic", 0)
    info.version = buf[4:7].decode("ascii", "replace")
    info.checksum_declared = struct.unpack_from("<I", buf, 8)[0]
    info.file_size_declared = struct.unpack_from("<I", buf, 32)[0]
    if info.file_size_declared != len(buf):
        info.problems.append(
            f"declared file_size {info.file_size_declared} != actual {len(buf)} "
            "(truncated or patched DEX)")
    try:
        info.checksum_ok = (zlib.adler32(buf[12:]) & 0xFFFFFFFF) == info.checksum_declared
    except zlib.error:  # pragma: no cover - defensive
        info.checksum_ok = None
    info.signature_ok = hashlib.sha1(buf[32:]).digest() == buf[12:32]
    (info.string_ids, info.type_ids, info.proto_ids, info.field_ids,
     info.method_ids, info.class_defs) = struct.unpack_from("<IIIIII", buf, 56)
    info.data_size = struct.unpack_from("<I", buf, 104)[0]
    if not info.checksum_ok:
        info.problems.append("adler32 checksum mismatch (bytes after header were modified)")
    if not info.signature_ok:
        info.problems.append("SHA-1 signature mismatch (header or body modified)")
    return info


def _uleb128(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            break
    return result, pos


def dex_class_names(buf: bytes, limit: int = 200_000) -> list[str]:
    """Resolve type descriptors from ``type_ids`` through ``string_ids``."""
    if len(buf) < 0x70:
        return []
    string_ids_size, string_ids_off = struct.unpack_from("<II", buf, 56)
    type_ids_size, type_ids_off = struct.unpack_from("<II", buf, 64)
    if (string_ids_off + string_ids_size * 4 > len(buf)
            or type_ids_off + type_ids_size * 4 > len(buf)):
        return []
    names: list[str] = []
    for i in range(min(type_ids_size, limit)):
        sidx = struct.unpack_from("<I", buf, type_ids_off + i * 4)[0]
        if sidx >= string_ids_size:
            continue
        soff = struct.unpack_from("<I", buf, string_ids_off + sidx * 4)[0]
        if soff >= len(buf):
            continue
        _, pos = _uleb128(buf, soff)
        end = buf.find(b"\x00", pos)
        if end < 0:
            continue
        names.append(buf[pos:end].decode("utf-8", "replace"))
    return names


# --------------------------------------------------------------------------
# ELF
# --------------------------------------------------------------------------

EM_386 = 3
EM_ARM = 40
EM_X86_64 = 62
EM_AARCH64 = 183
EM_RISCV = 243

ABI_BY_MACHINE = {
    (EM_ARM, 1): "armeabi-v7a",
    (EM_386, 1): "x86",
    (EM_AARCH64, 2): "arm64-v8a",
    (EM_X86_64, 2): "x86_64",
    (EM_RISCV, 2): "riscv64",
}

PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_SONAME = 14
DT_RPATH = 15
DT_RUNPATH = 29
SHT_NOTE = 7


@dataclass
class ElfSegment:
    p_type: int
    p_flags: int
    p_offset: int
    p_vaddr: int
    p_filesz: int
    p_memsz: int
    p_align: int


@dataclass
class ElfFileInfo:
    name: str
    size: int = 0
    class_bits: int = 0
    little_endian: bool = True
    e_type: int = 0
    e_machine: int = 0
    abi: str = "unknown"
    entry: int = 0
    segments: list[ElfSegment] = field(default_factory=list)
    needed: list[str] = field(default_factory=list)
    soname: str = ""
    rpath: list[str] = field(default_factory=list)
    build_id: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def max_load_align(self) -> int:
        return max((s.p_align for s in self.segments if s.p_type == PT_LOAD), default=0)

    def page16k_ready(self) -> tuple[bool, list[str]]:
        """Android 15+ 16 KB page-size heuristic for 64-bit shared objects."""
        notes: list[str] = []
        if self.abi not in ("arm64-v8a", "x86_64", "riscv64"):
            return True, ["32-bit library: the 16 KB rule applies to 64-bit ABIs only"]
        if self.e_type != 3:  # only ET_DYN shared objects are page-mapped
            return True, ["not a shared object; page-size rule not applicable"]
        loads = [s for s in self.segments if s.p_type == PT_LOAD]
        if not loads:
            return False, ["no PT_LOAD segments found"]
        ok = True
        for seg in loads:
            if seg.p_align < 16384:
                ok = False
                notes.append(
                    f"PT_LOAD at vaddr {seg.p_vaddr:#x} has p_align {seg.p_align} (< 16384)")
            elif (seg.p_vaddr - seg.p_offset) % seg.p_align != 0:
                ok = False
                notes.append(
                    f"PT_LOAD at vaddr {seg.p_vaddr:#x} violates "
                    "p_vaddr % p_align == p_offset % p_align")
        if ok:
            notes.append(
                f"all PT_LOAD segments align at >= 16 KB (max {self.max_load_align})")
        return ok, notes

    def to_dict(self) -> dict[str, Any]:
        ready, notes = self.page16k_ready()
        return {
            "name": self.name, "size": self.size, "class_bits": self.class_bits,
            "endian": "little" if self.little_endian else "big",
            "elf_type": {1: "rel", 2: "exec", 3: "dyn", 4: "core"}.get(self.e_type,
                                                                     self.e_type),
            "machine": self.e_machine, "abi": self.abi, "needed": self.needed,
            "soname": self.soname, "rpath": self.rpath, "build_id": self.build_id,
            "max_load_align": self.max_load_align,
            "load_segments": [
                {"offset": s.p_offset, "vaddr": s.p_vaddr, "filesz": s.p_filesz,
                 "align": s.p_align} for s in self.segments if s.p_type == PT_LOAD],
            "page_16k_ready": ready, "page_16k_notes": notes,
            "problems": self.problems,
        }


def parse_elf(name: str, buf: bytes) -> ElfFileInfo:
    info = ElfFileInfo(name=name, size=len(buf))
    if len(buf) < 52 or not buf.startswith(b"\x7fELF"):
        raise ParseError("missing ELF magic", 0)
    ei_class, ei_data = buf[4], buf[5]
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise ParseError("unsupported ELF class/endianness", 4)
    info.class_bits = 32 if ei_class == 1 else 64
    info.little_endian = ei_data == 1
    e = "<" if info.little_endian else ">"
    info.e_type, info.e_machine = struct.unpack_from(e + "HH", buf, 16)
    if info.class_bits == 64:
        info.entry, = struct.unpack_from(e + "Q", buf, 24)
        e_phoff, = struct.unpack_from(e + "Q", buf, 32)
        e_shoff, = struct.unpack_from(e + "Q", buf, 40)
        e_phentsize, e_phnum = struct.unpack_from(e + "HH", buf, 54)
        e_shentsize, e_shnum = struct.unpack_from(e + "HH", buf, 58)
    else:
        info.entry, = struct.unpack_from(e + "I", buf, 24)
        e_phoff, = struct.unpack_from(e + "I", buf, 28)
        e_shoff, = struct.unpack_from(e + "I", buf, 32)
        e_phentsize, e_phnum = struct.unpack_from(e + "HH", buf, 42)
        e_shentsize, e_shnum = struct.unpack_from(e + "HH", buf, 46)
    info.abi = ABI_BY_MACHINE.get(
        (info.e_machine, 1 if info.class_bits == 32 else 2), "unknown")
    dynamic: list[tuple[int, int]] = []
    for i in range(min(e_phnum, 4096)):
        base = e_phoff + i * e_phentsize
        if base + e_phentsize > len(buf) or e_phentsize < 32:
            info.problems.append("program header table truncated")
            break
        if info.class_bits == 64:
            p_type, p_flags = struct.unpack_from(e + "II", buf, base)
            p_offset, p_vaddr, _p_paddr, p_filesz, p_memsz, p_align = struct.unpack_from(
                e + "QQQQQQ", buf, base + 8)
        else:
            (p_type, p_offset, p_vaddr, _p_paddr, p_filesz, p_memsz, p_flags,
             p_align) = struct.unpack_from(e + "IIIIIIII", buf, base)
        info.segments.append(ElfSegment(p_type=p_type, p_flags=p_flags, p_offset=p_offset,
                                        p_vaddr=p_vaddr, p_filesz=p_filesz,
                                        p_memsz=p_memsz, p_align=p_align))
        if p_type == PT_DYNAMIC and p_offset + 8 <= len(buf):
            step = 16 if info.class_bits == 64 else 8
            pos = p_offset
            limit = min(p_offset + p_filesz, len(buf))
            while pos + step <= limit:
                if info.class_bits == 64:
                    tag, val = struct.unpack_from(e + "QQ", buf, pos)
                else:
                    tag, val = struct.unpack_from(e + "II", buf, pos)
                if tag == DT_NULL:
                    break
                dynamic.append((tag, val))
                pos += step

    def _vaddr_to_off(addr: int) -> int | None:
        for seg in info.segments:
            if seg.p_type == PT_LOAD and seg.p_vaddr <= addr < seg.p_vaddr + seg.p_filesz:
                return addr - seg.p_vaddr + seg.p_offset
        return None

    strtab_addr = next((val for tag, val in dynamic if tag == DT_STRTAB), None)
    strtab_off = _vaddr_to_off(strtab_addr) if strtab_addr is not None else None
    if strtab_off is not None:
        for tag, val in dynamic:
            if tag not in (DT_NEEDED, DT_SONAME, DT_RUNPATH, DT_RPATH):
                continue
            pos = strtab_off + val
            end = buf.find(b"\x00", pos, min(len(buf), pos + 4096))
            if 0 <= pos < len(buf) and end > 0:
                text = buf[pos:end].decode("utf-8", "replace")
                if tag == DT_NEEDED:
                    info.needed.append(text)
                elif tag == DT_SONAME:
                    info.soname = text
                else:
                    info.rpath.extend(text.split(":"))
    for i in range(min(e_shnum, 8192)):
        base = e_shoff + i * e_shentsize
        if base + e_shentsize > len(buf):
            break
        if info.class_bits == 64:
            _sh_name, sh_type = struct.unpack_from(e + "II", buf, base)
            sh_offset, sh_size = struct.unpack_from(e + "QQ", buf, base + 24)
        else:
            _sh_name, sh_type = struct.unpack_from(e + "II", buf, base)
            sh_offset, sh_size = struct.unpack_from(e + "II", buf, base + 16)
        if sh_type == SHT_NOTE and sh_size and sh_offset + sh_size <= len(buf):
            note = buf[sh_offset:sh_offset + sh_size]
            idx = note.find(b"GNU\x00")
            if idx >= 0 and idx + 16 <= len(note):
                namesz, descsz, ntype = struct.unpack_from(e + "III", note, idx)
                if ntype == 3 and namesz == 4:
                    desc_at = idx + 12 + ((namesz + 3) & ~3)
                    info.build_id = note[desc_at:desc_at + descsz].hex()
    return info


def elf_strings_scan(buf: bytes, needles: tuple[bytes, ...] = (
        b"Android", b"clang", b"ndk")) -> list[str]:
    """Find toolchain breadcrumbs inside an ELF without external tools."""
    hits: list[str] = []
    for needle in needles:
        start = 0
        while True:
            idx = buf.find(needle, start)
            if idx < 0:
                break
            lo, hi = max(0, idx - 48), min(len(buf), idx + 96)
            text = "".join(chr(c) if 32 <= c < 127 else " " for c in buf[lo:hi]).strip()
            if text and text not in hits:
                hits.append(text)
            start = idx + 1
            if len(hits) > 24:
                return hits
    return hits


# --------------------------------------------------------------------------
# protobuf (AAB metadata)
# --------------------------------------------------------------------------


def _varint(buf: bytes, pos: int) -> tuple[int | None, int]:
    shift = 0
    result = 0
    start = pos
    while pos < len(buf) and shift <= 63:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    return None, start


def protobuf_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """Decode protobuf wire format without guessing nesting.

    Returns ``(field_number, wire_type, value)`` where length-delimited values
    stay as ``bytes``. Schema-aware decoders call this again on nested payloads.
    """
    out: list[tuple[int, int, Any]] = []
    pos = 0
    n = len(buf)
    while pos < n:
        key, newpos = _varint(buf, pos)
        if key is None or newpos == pos:
            break
        pos = newpos
        field_no, wire = key >> 3, key & 7
        if field_no == 0:
            break
        if wire == 0:
            val, pos = _varint(buf, pos)
            if val is None:
                break
            out.append((field_no, wire, val))
        elif wire == 1:
            if pos + 8 > n:
                break
            out.append((field_no, wire, struct.unpack_from("<Q", buf, pos)[0]))
            pos += 8
        elif wire == 2:
            ln, pos = _varint(buf, pos)
            if ln is None or pos + ln > n:
                break
            out.append((field_no, wire, buf[pos:pos + ln]))
            pos += ln
        elif wire == 5:
            if pos + 4 > n:
                break
            out.append((field_no, wire, struct.unpack_from("<I", buf, pos)[0]))
            pos += 4
        else:
            break
    return out


def _first(fields: list[tuple[int, int, Any]], number: int, wire: int = 2) -> Any:
    for num, w, val in fields:
        if num == number and w == wire:
            return val
    return None


def _all(fields: list[tuple[int, int, Any]], number: int, wire: int = 2) -> list[Any]:
    return [val for num, w, val in fields if num == number and w == wire]


def _as_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if text and all(32 <= ord(c) < 127 or c in "\t\n\r" for c in text):
            return text
    return None


def _valid_tag_name(text: str) -> bool:
    return bool(text) and len(text) < 128 and bool(_TAG_RE.match(text)) and "://" not in text


def _find_name_field(fields: list[tuple[int, int, Any]],
                     candidates: tuple[int, ...] = (3, 2)) -> str:
    """Locate an element/attribute name field.

    AAPT2 has emitted two ``XmlElement`` layouts over time (name at field 2 in
    older builds, field 3 in newer ones). Rather than pinning a tool version, the
    decoder accepts the first candidate that decodes to a valid tag name.
    """
    for number in candidates:
        for num, wire, val in fields:
            if num == number and wire == 2 and isinstance(val, bytes):
                text = _as_text(val)
                if text and _valid_tag_name(text):
                    return text
    return ""


def _xml_node_element(node: bytes) -> bytes | None:
    """``XmlNode { element=1; text=2; source=3 }`` -> raw XmlElement bytes."""
    for num, wire, val in protobuf_fields(node):
        if num == 1 and wire == 2:
            return val
    return None


def build_xml_element(raw: bytes, depth: int = 0,
                      nsmap: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Decode an XmlElement (namespace declarations, name, attributes, children)."""
    if depth > 64:
        return None
    fields = protobuf_fields(raw)
    tag = _find_name_field(fields)
    if not tag:
        return None
    local_ns = dict(nsmap or {})
    for num, wire, val in fields:
        if num in (1, 3) and wire == 2 and isinstance(val, bytes):
            decl = protobuf_fields(val)
            prefix = _as_text(_first(decl, 1) or b"") or ""
            uri = _as_text(_first(decl, 2) or b"") or ""
            if uri:
                local_ns[prefix] = uri
    attrs: dict[str, str] = {}
    for num, wire, araw in fields:
        if num != 4 or wire != 2 or not isinstance(araw, bytes):
            continue
        afields = protobuf_fields(araw)
        aname = _find_name_field(afields, candidates=(2, 3))
        if not aname:
            continue
        aval = ""
        for candidate in (3, 2):
            raw_val = _first(afields, candidate)
            if isinstance(raw_val, bytes):
                text = _as_text(raw_val)
                if text is not None:
                    aval = text
                    break
        uri = _as_text(_first(afields, 1) or b"") or ""
        attrs[aname] = aval
        if uri:
            prefix = next((p for p, u in local_ns.items() if u == uri and p), "android")
            attrs[f"{prefix}:{aname}"] = aval
    children: list[dict[str, Any]] = []
    for num, wire, craw in fields:
        if num != 5 or wire != 2 or not isinstance(craw, bytes):
            continue
        child_el = _xml_node_element(craw)
        if child_el is None:
            continue
        child = build_xml_element(child_el, depth + 1, local_ns)
        if child:
            children.append(child)
    return {"tag": tag, "attributes": attrs, "children": children}


@dataclass
class AabManifest:
    package: str = ""
    version_code: str = ""
    version_name: str = ""
    split: str = ""
    elements: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def walk(self) -> Iterator[dict[str, Any]]:
        stack = list(self.elements)
        while stack:
            node = stack.pop()
            yield node
            stack.extend(node.get("children", []))


def decode_aab_manifest(buf: bytes) -> AabManifest:
    """Decode a proto XML manifest (``<module>/manifest/AndroidManifest.xml``).

    Wire layout used by bundletool / AAPT2::

        file       = XmlNode
        XmlNode    { element=1 (XmlElement); text=2 (string); source=3 }
        XmlElement { namespace_declaration=1; name (2 or 3);
                     attribute=4 (XmlAttribute); child=5 (XmlNode) }
        XmlAttribute { namespace_uri=1; name=2; value=3; source=4; resource_id=5 }

    The element-name field number differs between AAPT2 releases, so it is
    detected from the data (see ``_find_name_field``).
    """
    man = AabManifest()
    root_el = _xml_node_element(buf)
    if root_el is None:
        man.problems.append("manifest root is not an XmlNode with an element field")
        return man
    root = build_xml_element(root_el)
    if not root:
        man.problems.append("root XmlElement has no decodable name")
        return man
    man.elements = [root]
    for node in man.walk():
        if node["tag"] == "manifest":
            attrs = node["attributes"]
            man.package = attrs.get("package", "")
            man.version_code = attrs.get("versionCode", attrs.get("android:versionCode", ""))
            man.version_name = attrs.get("versionName", attrs.get("android:versionName", ""))
            man.split = attrs.get("split", "")
            break
    if not man.package:
        man.problems.append("package attribute not found in proto manifest")
    return man


@dataclass
class BundleConfigInfo:
    bundletool_version: str = ""
    compression: dict[str, Any] = field(default_factory=dict)
    raw_fields: int = 0
    problems: list[str] = field(default_factory=list)


def decode_bundle_config(buf: bytes) -> BundleConfigInfo:
    """Decode ``BundleConfig.proto``.

    ``BundleConfig { Bundletool bundletool=1; Compression compression=2;
    Optimizations optimizations=3; ...; Type type=8 }``
    ``Bundletool { Version version=1 }`` / ``Version { major=1; minor=2; patch=3 }``
    ``Compression { repeated string uncompressed_glob=1;
    UncompressedNativeLibraries uncompressed_native_libraries=2 }``
    """
    info = BundleConfigInfo()
    fields = protobuf_fields(buf)
    info.raw_fields = len(fields)
    bt = _first(fields, 1)
    if bt:
        bfields = protobuf_fields(bt)
        direct = _as_text(_first(bfields, 2) or b"")
        if direct:
            info.bundletool_version = direct
        else:
            version_raw = _first(bfields, 1)
            if version_raw:
                version = {n: val for n, w, val in protobuf_fields(version_raw) if w == 0}
                if version:
                    info.bundletool_version = ".".join(
                        str(version[i]) for i in (1, 2, 3) if i in version)
    comp = _first(fields, 2)
    if comp:
        cfields = protobuf_fields(comp)
        globs = [g for g in (_as_text(x) for x in _all(cfields, 1)) if g is not None]
        if globs:
            info.compression["uncompressed_glob"] = globs
        unl = _first(cfields, 2)
        if unl is not None:
            enabled = {n: val for n, w, val in protobuf_fields(unl) if w == 0}
            info.compression["uncompressed_native_libraries"] = (
                bool(enabled.get(1, 0)) if enabled else True)
    bundle_type = _first(fields, 8, wire=0)
    if bundle_type is not None:
        info.compression["bundle_type"] = {0: "REGULAR", 1: "APEX"}.get(
            bundle_type, str(bundle_type))
    return info


@dataclass
class AabStructure:
    modules: dict[str, dict[str, int]] = field(default_factory=dict)
    manifest_packages: dict[str, str] = field(default_factory=dict)
    dex_files: list[str] = field(default_factory=list)
    native_libs: list[str] = field(default_factory=list)
    asset_packs: list[str] = field(default_factory=list)
    bundle_tool_version: str = ""
    compression: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modules": self.modules, "module_manifests": self.manifest_packages,
            "dex_files": self.dex_files, "native_libs": self.native_libs,
            "asset_packs": self.asset_packs,
            "bundle_tool_version": self.bundle_tool_version,
            "compression": self.compression, "problems": self.problems,
        }


_META_PREFIXES = ("BUNDLE-METADATA", "META-INF")


def analyse_aab(structure_names: list[str], reader) -> AabStructure:
    """Summarise an AAB from its entry list; ``reader(name) -> bytes``.

    ``reader`` must stay valid for the duration of the call (the caller owns the
    ZIP handle). Metadata prefixes are excluded from module accounting.
    """
    st = AabStructure()
    for name in structure_names:
        parts = name.split("/")
        if len(parts) < 2 or parts[0] in _META_PREFIXES:
            continue
        module = parts[0]
        bucket = st.modules.setdefault(module, {})
        if name.endswith(".dex"):
            bucket["dex"] = bucket.get("dex", 0) + 1
            st.dex_files.append(name)
        elif name.startswith(f"{module}/lib/") and name.endswith(".so"):
            bucket["native_libs"] = bucket.get("native_libs", 0) + 1
            st.native_libs.append(name)
        elif "/res/" in name:
            bucket["resources"] = bucket.get("resources", 0) + 1
        elif "/assets/" in name:
            bucket["assets"] = bucket.get("assets", 0) + 1
        elif name.endswith(".pb"):
            bucket["proto"] = bucket.get("proto", 0) + 1
        else:
            bucket["other"] = bucket.get("other", 0) + 1
        if name.endswith("/manifest/AndroidManifest.xml"):
            try:
                manifest = decode_aab_manifest(reader(name))
                st.manifest_packages[module] = manifest.package
                st.problems.extend(f"{name}: {p}" for p in manifest.problems)
            except ParseError as exc:
                st.problems.append(f"{name}: {exc}")
        if name.startswith("asset_pack/") and name.endswith(".pb"):
            st.asset_packs.append(name)
    if "BundleConfig.pb" in structure_names:
        try:
            cfg = decode_bundle_config(reader("BundleConfig.pb"))
            st.bundle_tool_version = cfg.bundletool_version
            st.compression = cfg.compression
            st.problems.extend(f"BundleConfig.pb: {p}" for p in cfg.problems)
        except ParseError as exc:
            st.problems.append(f"BundleConfig.pb: {exc}")
    st.asset_packs = sorted(set(st.asset_packs))
    return st


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def read_zip_member(path: Path, member: str, limit: int = MAX_PARSE_BYTES) -> bytes:
    import zipfile
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(member)
        if info.file_size > limit:
            raise ParseError(
                f"{member} is {info.file_size} bytes, above the {limit} byte limit")
        return zf.read(member)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
