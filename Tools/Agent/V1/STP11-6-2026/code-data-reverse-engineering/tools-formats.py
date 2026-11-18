"""Binary/container/executable format parsers. All parsers are bounds-checked,
read-only, and report their depth limits honestly. Dispatch via cmd_parse."""
from __future__ import annotations

import io
import json
import plistlib
import re
import struct
import tarfile
import zipfile
import zlib
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, load_json_asset, read_bytes, validate_input_file,
                        shannon_entropy, u16, u32, u64, i32, cstring, align_up, hash_bytes, find_all)
from tools_identify import identify_bytes, structural_probes, annotate_layout, _resolve_layout, TYPE_SIZES


def need(data: bytes, off: int, n: int, what: str):
    if off < 0 or n < 0 or off + n > len(data):
        raise RetoolError("truncated-structure", f"{what}: need {n} bytes at offset {off}, have {max(0, len(data) - off)}",
                          exit_code=EXIT_USAGE)


def _u(data, off, n, endian="le", signed=False):
    need(data, off, n, "integer field")
    eb = "little" if endian in ("le", "little", "<") else "big"
    return int.from_bytes(data[off:off + n], eb, signed=signed)


# ---------------------------------------------------------------------------
# ELF
# ---------------------------------------------------------------------------

ELF_MACHINES = {0: "none", 2: "SPARC", 3: "x86", 4: "68k", 8: "MIPS", 0x14: "PowerPC", 0x15: "PowerPC64",
                0x16: "S390", 0x28: "ARM", 0x2A: "SuperH", 0x32: "IA-64", 0x3E: "x86-64", 0x53: "AVR",
                0x83: "AVR32", 0xB7: "AArch64", 0xF3: "RISC-V", 0x0102: "6502 (custom)"}
ELF_TYPES = {0: "NONE", 1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}
ELF_SH_TYPES = {0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB", 4: "RELA", 5: "HASH", 6: "DYNAMIC",
                7: "NOTE", 8: "NOBITS", 9: "REL", 11: "DYNSYM", 14: "INIT_ARRAY", 15: "FINI_ARRAY",
                0x6FFFFFF6: "GNU_HASH", 0x6FFFFFFF: "GNU_VERDEF", 0x6FFFFFFE: "GNU_VERNEED", 0x6FFFFFFD: "GNU_VERSYM"}
ELF_PT = {0: "NULL", 1: "LOAD", 2: "DYNAMIC", 3: "INTERP", 4: "NOTE", 6: "PHDR", 7: "TLS",
          0x6474E550: "GNU_EH_FRAME", 0x6474E551: "GNU_STACK", 0x6474E552: "GNU_RELRO", 0x6474E553: "GNU_PROPERTY"}
ELF_DT = {0: "NULL", 1: "NEEDED", 2: "PLTRELSZ", 3: "PLTGOT", 4: "HASH", 5: "STRTAB", 6: "SYMTAB",
          7: "RELA", 8: "RELASZ", 9: "RELAENT", 10: "STRSZ", 11: "SYMENT", 12: "INIT", 13: "FINI",
          14: "SONAME", 16: "SYMBOLIC", 20: "PLTREL", 23: "JMPREL", 25: "INIT_ARRAY", 27: "INIT_ARRAYSZ",
          30: "FLAGS", 0x6FFFFFFB: "FLAGS_1", 0x6FFFFFF0: "VERSYM", 0x6FFFFFFC: "VERDEF", 0x6FFFFFFD: "VERNEED",
          0x6FFFFFFE: "VERNEEDNUM", 0x6FFFFFFF: "VERDEFNUM", 0x6FFFFEF5: "GNU_HASH"}


def parse_elf(data: bytes) -> dict:
    need(data, 0, 64, "ELF header")
    if data[:4] != b"\x7fELF":
        raise RetoolError("not-elf", "Missing ELF magic")
    is64 = data[4] == 2
    endian = "le" if data[5] == 1 else "be"
    e = lambda off, n, s=False: _u(data, off, n, endian, s)
    if is64:
        entry, phoff, shoff, flags = e(24, 8), e(32, 8), e(40, 8), e(48, 4)
        ehsize, phentsize, phnum, shentsize, shnum, shstrndx = e(52, 2), e(54, 2), e(56, 2), e(58, 2), e(60, 2), e(62, 2)
    else:
        entry, phoff, shoff, flags = e(24, 4), e(28, 4), e(32, 4), e(36, 4)
        ehsize, phentsize, phnum, shentsize, shnum, shstrndx = e(40, 2), e(42, 2), e(44, 2), e(46, 2), e(48, 2), e(50, 2)
    hdr = {
        "class": 64 if is64 else 32, "endian": endian, "osabi": data[7],
        "type": ELF_TYPES.get(e(16, 2), hex(e(16, 2))), "machine": ELF_MACHINES.get(e(18, 2), hex(e(18, 2))),
        "machine_raw": e(18, 2), "entry": entry, "phoff": phoff, "shoff": shoff,
        "flags": flags, "ehsize": ehsize, "phentsize": phentsize, "phnum": phnum,
        "shentsize": shentsize, "shnum": shnum, "shstrndx": shstrndx,
    }
    # program headers
    phs = []
    phoff, phentsize, phnum = hdr["phoff"], hdr["phentsize"] or (56 if is64 else 32), hdr["phnum"]
    if phnum == 0xFFFF and hdr["shoff"]:
        phnum = 0  # extended numbering handled via section 0 (rare); note limit
    for i in range(min(phnum, 128)):
        off = phoff + i * phentsize
        if off + phentsize > len(data):
            break
        if is64:
            p = {"type": ELF_PT.get(e(off, 4), hex(e(off, 4))), "flags": e(off + 4, 4),
                 "offset": e(off + 8, 8), "vaddr": e(off + 16, 8), "paddr": e(off + 24, 8),
                 "filesz": e(off + 32, 8), "memsz": e(off + 40, 8), "align": e(off + 48, 8)}
        else:
            p = {"type": ELF_PT.get(e(off, 4), hex(e(off, 4))), "offset": e(off + 4, 4),
                 "vaddr": e(off + 8, 4), "paddr": e(off + 12, 4), "filesz": e(off + 16, 4),
                 "memsz": e(off + 20, 4), "flags": e(off + 24, 4), "align": e(off + 28, 4)}
        p["permissions"] = "".join(c for c, b in (("r", 4), ("w", 2), ("x", 1)) if p["flags"] & b) or "---"
        phs.append(p)
    # sections
    sections = []
    shoff, shentsize, shnum = hdr["shoff"], hdr["shentsize"] or (64 if is64 else 40), hdr["shnum"]
    shstr = b""
    if shoff and shnum:
        first = shoff
        if is64:
            str_off = e(first + 24, 8)
            str_size = e(first + 32, 8)
        else:
            str_off = e(first + 16, 4)
            str_size = e(first + 20, 4)
        if hdr["shstrndx"] and hdr["shstrndx"] < shnum:
            so = shoff + hdr["shstrndx"] * shentsize
            str_off = e(so + (24 if is64 else 16), 8 if is64 else 4)
            str_size = e(so + (32 if is64 else 20), 8 if is64 else 4)
        if 0 <= str_off < len(data):
            shstr = data[str_off:str_off + min(str_size, 1 << 20)]
        for i in range(min(shnum, 512)):
            off = shoff + i * shentsize
            if off + shentsize > len(data):
                break
            if is64:
                s = {"name_off": e(off, 4), "type": ELF_SH_TYPES.get(e(off + 4, 4), hex(e(off + 4, 4))),
                     "flags": e(off + 8, 8), "addr": e(off + 16, 8), "offset": e(off + 24, 8),
                     "size": e(off + 32, 8), "link": e(off + 40, 4), "info": e(off + 44, 4),
                     "addralign": e(off + 48, 8), "entsize": e(off + 56, 8)}
            else:
                s = {"name_off": e(off, 4), "type": ELF_SH_TYPES.get(e(off + 4, 4), hex(e(off + 4, 4))),
                     "flags": e(off + 8, 4), "addr": e(off + 12, 4), "offset": e(off + 16, 4),
                     "size": e(off + 20, 4), "link": e(off + 24, 4), "info": e(off + 28, 4),
                     "addralign": e(off + 32, 4), "entsize": e(off + 36, 4)}
            end = s["name_off"]
            s["name"] = shstr[end:shstr.find(b"\x00", end)].decode("utf-8", errors="replace") if end < len(shstr) else ""
            if s["type"] != "NOBITS" and s["offset"] + s["size"] <= len(data):
                s["entropy"] = shannon_entropy(data[s["offset"]:s["offset"] + min(s["size"], 65536)])
            sections.append(s)
    out = {"format": "elf", "header": hdr, "program_headers": phs, "sections": sections}
    # dynamic
    dyn_sec = next((s for s in sections if s["type"] == "DYNAMIC"), None)
    if dyn_sec:
        tags = []
        ent = 16 if is64 else 8
        strtab = next((s for s in sections if s["name"] == ".dynstr"), None)
        dynstr = data[strtab["offset"]:strtab["offset"] + strtab["size"]] if strtab else b""
        for i in range(min(dyn_sec["size"] // ent, 512)):
            off = dyn_sec["offset"] + i * ent
            tag = e(off, 8 if is64 else 4)
            val = e(off + (8 if is64 else 4), 8 if is64 else 4)
            if tag == 0:
                break
            name = ELF_DT.get(tag, hex(tag))
            entry = {"tag": name, "value": val}
            if name in ("NEEDED", "SONAME") and val < len(dynstr):
                entry["string"] = cstring(dynstr, val)
            tags.append(entry)
        out["dynamic"] = tags
        out["needed"] = [t["string"] for t in tags if t["tag"] == "NEEDED" and "string" in t]
        soname = next((t["string"] for t in tags if t["tag"] == "SONAME" and "string" in t), None)
        if soname:
            out["soname"] = soname
    # notes / build id
    for s in sections:
        if s["type"] == "NOTE" and s["offset"] + s["size"] <= len(data):
            notes = _parse_elf_notes(data[s["offset"]:s["offset"] + s["size"]], endian)
            if notes:
                out.setdefault("notes", []).extend(notes)
    for p in phs:
        if p["type"] == "NOTE" and p["offset"] + p["filesz"] <= len(data):
            notes = _parse_elf_notes(data[p["offset"]:p["offset"] + p["filesz"]], endian)
            if notes:
                out.setdefault("notes", []).extend(notes)
    bid = next((n for n in out.get("notes", []) if n.get("type") == "GNU_BUILD_ID"), None)
    if bid:
        out["build_id"] = bid["desc_hex"]
    # symbols
    out["symbols"] = _elf_symbols(data, sections, is64, endian)
    # relocations summary
    relocs = []
    for s in sections:
        if s["type"] in ("RELA", "REL") and s["entsize"]:
            relocs.append({"section": s["name"], "count": s["size"] // s["entsize"], "kind": s["type"]})
    if relocs:
        out["relocations"] = relocs
    # eh_frame FDEs
    eh = next((s for s in sections if s["name"] == ".eh_frame"), None)
    if eh and eh["offset"] + eh["size"] <= len(data):
        out["eh_frame"] = _parse_eh_frame(data[eh["offset"]:eh["offset"] + eh["size"]], endian, is64)
    return out


def _parse_elf_notes(blob: bytes, endian: str) -> list:
    out = []
    pos = 0
    while pos + 12 <= len(blob):
        namesz = _u(blob, pos, 4, endian)
        descsz = _u(blob, pos + 4, 4, endian)
        ntype = _u(blob, pos + 8, 4, endian)
        pos += 12
        name = blob[pos:pos + namesz].rstrip(b"\x00").decode("ascii", errors="replace")
        pos = align_up(pos + namesz, 4)
        desc = blob[pos:pos + descsz]
        pos = align_up(pos + descsz, 4)
        tname = "GNU_BUILD_ID" if name == "GNU" and ntype == 3 else f"{name}:{ntype}"
        out.append({"name": name, "type": tname, "desc_hex": desc.hex(), "desc_size": descsz})
        if len(out) > 64:
            break
    return out


def _elf_symbols(data: bytes, sections: list, is64: bool, endian: str) -> dict:
    out = {"symtab": [], "dynsym": [], "imports": [], "exports": []}
    for kind, secname in (("symtab", "SYMTAB"), ("dynsym", "DYNSYM")):
        sec = next((s for s in sections if s["type"] == secname), None)
        if not sec or not sec["entsize"]:
            continue
        link = sec["link"]
        strsec = sections[link] if link < len(sections) else None
        strtab = data[strsec["offset"]:strsec["offset"] + strsec["size"]] if strsec else b""
        ent = sec["entsize"]
        for i in range(min(sec["size"] // ent, 4000)):
            off = sec["offset"] + i * ent
            if off + ent > len(data):
                break
            if is64:
                name_off = _u(data, off, 4, endian)
                info = data[off + 4]
                other = data[off + 5]
                shndx = _u(data, off + 6, 2, endian)
                value = _u(data, off + 8, 8, endian)
                size = _u(data, off + 16, 8, endian)
            else:
                name_off = _u(data, off, 4, endian)
                value = _u(data, off + 4, 4, endian)
                size = _u(data, off + 8, 4, endian)
                info = data[off + 12]
                other = data[off + 13]
                shndx = _u(data, off + 14, 2, endian)
            name = cstring(strtab, name_off) if name_off < len(strtab) else ""
            sym = {"name": name, "value": value, "size": size, "bind": info >> 4, "type": info & 0xF,
                   "visibility": other & 3, "shndx": shndx}
            out[kind].append(sym)
            if shndx == 0 and name:
                out["imports"].append(name)
            elif shndx != 0 and info >> 4 in (1, 2) and name:
                out["exports"].append(name)
    return out


def _parse_eh_frame(blob: bytes, endian: str, is64: bool) -> dict:
    cies, fdes = [], []
    pos = 0
    while pos + 4 <= len(blob):
        length = _u(blob, pos, 4, endian)
        if length == 0:
            break
        if length == 0xFFFFFFFF:
            break  # 64-bit DWARF format not supported
        start = pos
        pos += 4
        if pos + 4 > len(blob):
            break
        cie_id = _u(blob, pos, 4, endian)
        body_end = start + 4 + length
        if cie_id == 0:
            ver = blob[pos + 4] if pos + 4 < len(blob) else 0
            cies.append({"offset": start, "version": ver})
        else:
            cie_ptr = pos - cie_id
            pc_begin = _u(blob, pos + 4, 8 if is64 else 4, endian) if pos + 4 + (8 if is64 else 4) <= len(blob) else 0
            fdes.append({"offset": start, "cie_offset": cie_ptr, "pc_begin_raw": pc_begin})
        pos = body_end
        if len(fdes) > 2000:
            break
    return {"cie_count": len(cies), "fde_count": len(fdes), "fdes_sample": fdes[:50],
            "limitation": "FDE pc_begin shown raw (pointer encoding from CIE not applied); LSDA not decoded."}


# ---------------------------------------------------------------------------
# PE / COFF
# ---------------------------------------------------------------------------

PE_MACHINES = {0x14C: "x86", 0x8664: "x86-64", 0x1C0: "ARM", 0xAA64: "ARM64", 0x1C4: "ARMNT", 0x200: "IA64",
               0x166: "MIPS16", 0x266: "MIPSFPU", 0x366: "MIPSFPU16", 0x5032: "RISC-V32", 0x5064: "RISC-V64"}
PE_SUBSYSTEMS = {1: "native", 2: "windows-gui", 3: "windows-cui", 5: "os2-cui", 7: "posix-cui", 9: "windows-ce-gui",
                 10: "efi-application", 11: "efi-boot-service-driver", 12: "efi-runtime-driver", 13: "efi-rom",
                 14: "xbox", 16: "windows-boot-application"}
PE_DATA_DIRS = ["export", "import", "resource", "exception", "certificate", "base_relocation", "debug",
                "architecture", "global_ptr", "tls", "load_config", "bound_import", "iat", "delay_import",
                "clr_runtime_header", "reserved"]


def rva_to_off(rva: int, sections: list) -> int | None:
    for s in sections:
        if s["virtual_address"] <= rva < s["virtual_address"] + max(s["virtual_size"], s["raw_size"]):
            return s["raw_offset"] + (rva - s["virtual_address"])
    return None


def parse_pe(data: bytes) -> dict:
    need(data, 0, 0x40, "DOS header")
    if data[:2] != b"MZ":
        raise RetoolError("not-pe", "Missing MZ signature")
    pe_off = u32(data, 0x3C)
    need(data, pe_off, 24, "PE signature + COFF header")
    if data[pe_off:pe_off + 4] != b"PE\x00\x00":
        return {"format": "dos-mz", "note": "MZ header without PE signature; DOS executable or stub.",
                "e_lfanew": pe_off}
    machine = u16(data, pe_off + 4)
    num_sections = u16(data, pe_off + 6)
    timestamp = u32(data, pe_off + 8)
    size_opt = u16(data, pe_off + 20)
    characteristics = u16(data, pe_off + 22)
    opt_off = pe_off + 24
    need(data, opt_off, 2, "optional header magic")
    magic = u16(data, opt_off)
    pe32plus = magic == 0x20B
    out = {"format": "pe", "coff": {"machine": PE_MACHINES.get(machine, hex(machine)), "machine_raw": machine,
                                    "sections": num_sections, "timestamp": timestamp,
                                    "characteristics": characteristics,
                                    "dll": bool(characteristics & 0x2000), "executable": bool(characteristics & 0x2)}}
    if size_opt:
        need(data, opt_off, min(size_opt, 240), "optional header")
        entry = u32(data, opt_off + 16)
        image_base = u64(data, opt_off + 24) if pe32plus else u32(data, opt_off + 28)
        sect_align = u32(data, opt_off + 32)
        file_align = u32(data, opt_off + 36)
        size_image = u32(data, opt_off + 56)
        size_headers = u32(data, opt_off + 60)
        subsystem = u16(data, opt_off + 68)
        dll_chars = u16(data, opt_off + 70)
        num_dirs = u32(data, opt_off + (108 if pe32plus else 92))
        dirs_off = opt_off + (112 if pe32plus else 96)
        dirs = {}
        for i in range(min(num_dirs, 16)):
            rva = u32(data, dirs_off + i * 8)
            size = u32(data, dirs_off + i * 8 + 4)
            if rva or size:
                dirs[PE_DATA_DIRS[i]] = {"rva": rva, "size": size}
        out["optional"] = {"magic": hex(magic), "bitness": 64 if pe32plus else 32, "entry_rva": entry,
                           "image_base": image_base, "section_alignment": sect_align, "file_alignment": file_align,
                           "size_of_image": size_image, "size_of_headers": size_headers,
                           "subsystem": PE_SUBSYSTEMS.get(subsystem, subsystem),
                           "dll_characteristics": dll_chars,
                           "nx_compat": bool(dll_chars & 0x100), "aslr": bool(dll_chars & 0x40),
                           "high_entropy_aslr": bool(dll_chars & 0x20), "cfg": bool(dll_chars & 0x4000),
                           "data_directories": dirs}
    sections = []
    sec_off = opt_off + size_opt
    for i in range(min(num_sections, 256)):
        off = sec_off + i * 40
        if off + 40 > len(data):
            break
        name = data[off:off + 8].rstrip(b"\x00").decode("ascii", errors="replace")
        s = {"name": name, "virtual_size": u32(data, off + 8), "virtual_address": u32(data, off + 12),
             "raw_size": u32(data, off + 16), "raw_offset": u32(data, off + 20),
             "characteristics": u32(data, off + 36)}
        ch = s["characteristics"]
        s["permissions"] = "".join(c for c, b in (("r", 0x40000000), ("w", 0x80000000), ("x", 0x20000000)) if ch & b) or "---"
        if s["raw_offset"] + s["raw_size"] <= len(data) and s["raw_size"]:
            s["entropy"] = shannon_entropy(data[s["raw_offset"]:s["raw_offset"] + min(s["raw_size"], 65536)])
        sections.append(s)
    out["sections"] = sections
    dirs = out.get("optional", {}).get("data_directories", {})
    # imports
    if "import" in dirs:
        imp_off = rva_to_off(dirs["import"]["rva"], sections)
        if imp_off is not None:
            imports = []
            pos = imp_off
            while pos + 20 <= len(data) and len(imports) < 256:
                ilt_rva, ts, fwd, name_rva, iat_rva = struct.unpack_from("<IIIII", data, pos)
                if ilt_rva == 0 and name_rva == 0:
                    break
                noff = rva_to_off(name_rva, sections)
                dll = cstring(data, noff) if noff is not None else "?"
                funcs = []
                thunk_rva = ilt_rva or iat_rva
                toff = rva_to_off(thunk_rva, sections)
                if toff is not None:
                    ent = 8 if pe32plus else 4
                    ordinal_flag = 1 << (63 if pe32plus else 31)
                    p = toff
                    while p + ent <= len(data) and len(funcs) < 2000:
                        val = u64(data, p) if pe32plus else u32(data, p)
                        p += ent
                        if val == 0:
                            break
                        if val & ordinal_flag:
                            funcs.append({"ordinal": val & 0xFFFF})
                        else:
                            foff = rva_to_off(val & 0x7FFFFFFF, sections)
                            if foff is not None and foff + 2 < len(data):
                                funcs.append({"hint": u16(data, foff), "name": cstring(data, foff + 2)})
                imports.append({"dll": dll, "functions": funcs})
                pos += 20
            out["imports"] = imports
    # exports
    if "export" in dirs:
        exp_off = rva_to_off(dirs["export"]["rva"], sections)
        if exp_off is not None and exp_off + 40 <= len(data):
            nfuncs = u32(data, exp_off + 20)
            nnames = u32(data, exp_off + 24)
            addr_rva = u32(data, exp_off + 28)
            name_ptr_rva = u32(data, exp_off + 32)
            ord_rva = u32(data, exp_off + 36)
            dll_name_rva = u32(data, exp_off + 12)
            noff = rva_to_off(dll_name_rva, sections)
            exports = {"dll_name": cstring(data, noff) if noff is not None else "?", "functions": []}
            np_off = rva_to_off(name_ptr_rva, sections)
            ad_off = rva_to_off(addr_rva, sections)
            od_off = rva_to_off(ord_rva, sections)
            for i in range(min(nnames, 4000)):
                if np_off is None or ad_off is None or od_off is None:
                    break
                nrva = u32(data, np_off + i * 4)
                no = rva_to_off(nrva, sections)
                ordinal = u16(data, od_off + i * 2)
                frva = u32(data, ad_off + ordinal * 4)
                exports["functions"].append({"name": cstring(data, no) if no is not None else "?",
                                             "ordinal": ordinal, "rva": frva})
            out["exports"] = exports
    # debug / PDB
    if "debug" in dirs:
        doff = rva_to_off(dirs["debug"]["rva"], sections)
        if doff is not None:
            dbg = []
            for i in range(min(dirs["debug"]["size"] // 28, 32)):
                off = doff + i * 28
                if off + 28 > len(data):
                    break
                dtype = u32(data, off + 12)
                size = u32(data, off + 16)
                ptr = u32(data, off + 24)
                entry = {"type": dtype, "size": size}
                if dtype == 2 and ptr + 24 <= len(data):  # CODEVIEW
                    cv = data[ptr:ptr + size]
                    if cv[:4] == b"RSDS" and len(cv) >= 24:
                        guid = cv[4:20]
                        entry["pdb_guid"] = guid.hex()
                        entry["pdb_age"] = u32(cv, 20)
                        entry["pdb_path"] = cv[24:].split(b"\x00")[0].decode("utf-8", errors="replace")
                dbg.append(entry)
            out["debug"] = dbg
    # CLR
    if "clr_runtime_header" in dirs:
        coff = rva_to_off(dirs["clr_runtime_header"]["rva"], sections)
        if coff is not None and coff + 72 <= len(data):
            cb = u32(data, coff)
            major = u16(data, coff + 4)
            minor = u16(data, coff + 6)
            md_rva = u32(data, coff + 8)
            md_size = u32(data, coff + 12)
            flags = u32(data, coff + 16)
            entry_token = u32(data, coff + 20)
            out["clr"] = {"cb": cb, "runtime": f"{major}.{minor}", "metadata_rva": md_rva,
                          "metadata_size": md_size, "flags": flags, "entry_token": hex(entry_token),
                          "il_only": bool(flags & 1), "32bit_required": bool(flags & 2)}
            moff = rva_to_off(md_rva, sections)
            if moff is not None and moff + 20 <= len(data) and data[moff:moff + 4] == b"BSJB":
                vlen = u32(data, moff + 12)
                ver = data[moff + 16:moff + 16 + vlen].rstrip(b"\x00").decode("utf-8", errors="replace")
                streams_off = moff + 16 + align_up(vlen, 4) + 4
                nstreams = u16(data, streams_off - 2) if streams_off >= 2 else 0
                streams = []
                p = streams_off
                for _ in range(min(nstreams, 16)):
                    if p + 8 > len(data):
                        break
                    soff = u32(data, p)
                    ssize = u32(data, p + 4)
                    name = cstring(data, p + 8)
                    streams.append({"name": name, "offset": soff, "size": ssize})
                    p = align_up(p + 8 + len(name) + 1, 4)
                out["clr"]["metadata_version"] = ver
                out["clr"]["metadata_streams"] = streams
    # certificate table location (for signatures command)
    if "certificate" in dirs:
        out["certificate_table"] = dirs["certificate"]
    # relocations summary
    if "base_relocation" in dirs:
        out["relocations"] = {"rva": dirs["base_relocation"]["rva"], "size": dirs["base_relocation"]["size"],
                              "note": "Block-level relocation directory present; per-entry decode available via external tools."}
    return out


# ---------------------------------------------------------------------------
# Mach-O
# ---------------------------------------------------------------------------

MH_MAGICS = {0xFEEDFACE: (32, "be"), 0xFEEDFACF: (64, "be"), 0xCEFAEDFE: (32, "le"), 0xCFFAEDFE: (64, "le")}
MH_CPUTYPES = {7: "x86", 0x01000007: "x86-64", 12: "ARM", 0x0100000C: "ARM64", 18: "PowerPC", 0x01000012: "PowerPC64"}
LC_NAMES = {1: "SEGMENT", 0x19: "SEGMENT_64", 0x80000019: "SEGMENT_64", 2: "SYMTAB", 4: "THREAD", 5: "UNIXTHREAD", 6: "DYSYMTAB", 7: "LOAD_DYLIB",
            8: "ID_DYLIB", 9: "LOAD_DYLINKER", 0xB: "DYLD_INFO", 0xC: "LOAD_DYLIB", 0xD: "ID_DYLIB",
            0xE: "LOAD_DYLINKER", 0x1B: "UUID", 0x1D: "CODE_SIGNATURE", 0x1E: "SEGMENT_SPLIT_INFO",
            0x1F: "REEXPORT_DYLIB", 0x20: "LAZY_LOAD_DYLIB", 0x21: "ENCRYPTION_INFO", 0x22: "DYLD_INFO_ONLY",
            0x24: "VERSION_MIN_MACOSX", 0x25: "VERSION_MIN_IPHONEOS", 0x26: "FUNCTION_STARTS",
            0x27: "DYLD_ENVIRONMENT", 0x28: "MAIN", 0x29: "DATA_IN_CODE", 0x2A: "SOURCE_VERSION",
            0x2B: "DYLIB_CODE_SIGN_DRS", 0x2C: "ENCRYPTION_INFO_64", 0x2D: "LINKER_OPTION",
            0x2E: "LINKER_OPTIMIZATION_HINT", 0x2F: "VERSION_MIN_TVOS", 0x30: "VERSION_MIN_WATCHOS",
            0x32: "BUILD_VERSION", 0x80000028: "MAIN", 0x80000022: "DYLD_INFO_ONLY", 0x80000018: "LOAD_DYLINKER",
            0x8000001C: "LOAD_DYLIB", 0x8000000D: "ID_DYLIB", 0x8000001B: "UUID", 0x8000001D: "CODE_SIGNATURE"}


def parse_macho(data: bytes) -> dict:
    magic = u32(data, 0, "be")
    if magic in (0xCAFEBABE, 0xCAFEBABF, 0xBEBAFECA, 0xBFBAFECA):
        # fat binary (careful: CAFEBABE also Java class; check nfat sanity)
        endian = "be" if magic in (0xCAFEBABE, 0xCAFEBABF) else "le"
        nfat = _u(data, 4, 4, endian)
        if 0 < nfat < 32:
            slices = []
            for i in range(nfat):
                off = 8 + i * 20
                if off + 20 > len(data):
                    break
                cpu = _u(data, off, 4, endian)
                sub = _u(data, off + 4, 4, endian)
                offset = _u(data, off + 8, 4, endian)
                size = _u(data, off + 12, 4, endian)
                slices.append({"cpu": MH_CPUTYPES.get(cpu, hex(cpu)), "subtype": sub, "offset": offset, "size": size})
            return {"format": "macho-fat", "slices": slices,
                    "note": "Per-slice detail: run parse on the carved slice or use --format macho with offset."}
        raise RetoolError("not-macho", "CAFEBABE without valid fat header (may be a Java class)")
    if magic not in MH_MAGICS:
        raise RetoolError("not-macho", f"Unknown Mach-O magic {hex(magic)}")
    bits, endian = MH_MAGICS[magic]
    is64 = bits == 64
    cputype = _u(data, 4, 4, endian)
    cpusub = _u(data, 8, 4, endian)
    filetype = _u(data, 12, 4, endian)
    ncmds = _u(data, 16, 4, endian)
    sizeofcmds = _u(data, 20, 4, endian)
    flags = _u(data, 24, 4, endian)
    hdr_size = 32 if is64 else 28
    filetypes = {1: "OBJECT", 2: "EXECUTE", 3: "FVMLIB", 4: "CORE", 5: "PRELOAD", 6: "DYLIB", 7: "DYLINKER",
                 8: "BUNDLE", 9: "DYLIB_STUB", 10: "DSYM", 11: "KEXT_BUNDLE"}
    out = {"format": "macho", "header": {"bitness": bits, "endian": endian,
                                         "cputype": MH_CPUTYPES.get(cputype, hex(cputype)), "cpusubtype": cpusub,
                                         "filetype": filetypes.get(filetype, filetype), "ncmds": ncmds,
                                         "sizeofcmds": sizeofcmds, "flags": flags,
                                         "pie": bool(flags & 0x200000), "nx_heap": bool(flags & 0x1000000)}}
    commands = []
    segments = []
    sections = []
    symbols = []
    pos = hdr_size
    symtab_cmd = None
    for _ in range(min(ncmds, 512)):
        if pos + 8 > len(data):
            break
        cmd = _u(data, pos, 4, endian)
        cmdsize = _u(data, pos + 4, 4, endian)
        if cmdsize < 8:
            break
        name = LC_NAMES.get(cmd, hex(cmd))
        entry = {"cmd": name, "offset": pos, "size": cmdsize}
        if name in ("SEGMENT", "SEGMENT_64") or cmd in (1, 0x19):
            segname = data[pos + 8:pos + 24].rstrip(b"\x00").decode("ascii", errors="replace")
            if cmd == 0x19:  # SEGMENT_64
                vmaddr = _u(data, pos + 24, 8, endian)
                vmsize = _u(data, pos + 32, 8, endian)
                fileoff = _u(data, pos + 40, 8, endian)
                filesize = _u(data, pos + 48, 8, endian)
                maxprot = _u(data, pos + 56, 4, endian)
                initprot = _u(data, pos + 60, 4, endian)
                nsects = _u(data, pos + 64, 4, endian)
                sect_base = pos + 72
                sect_size = 80
            else:
                vmaddr = _u(data, pos + 24, 4, endian)
                vmsize = _u(data, pos + 28, 4, endian)
                fileoff = _u(data, pos + 32, 4, endian)
                filesize = _u(data, pos + 36, 4, endian)
                maxprot = _u(data, pos + 40, 4, endian)
                initprot = _u(data, pos + 44, 4, endian)
                nsects = _u(data, pos + 48, 4, endian)
                sect_base = pos + 56
                sect_size = 68
            seg = {"name": segname, "vmaddr": vmaddr, "vmsize": vmsize, "fileoff": fileoff,
                   "filesize": filesize, "initprot": _prot(initprot), "maxprot": _prot(maxprot)}
            segments.append(seg)
            for si in range(min(nsects, 128)):
                so = sect_base + si * sect_size
                if so + sect_size > len(data):
                    break
                sname = data[so:so + 16].rstrip(b"\x00").decode("ascii", errors="replace")
                sgname = data[so + 16:so + 32].rstrip(b"\x00").decode("ascii", errors="replace")
                if cmd == 0x19:
                    addr = _u(data, so + 32, 8, endian)
                    size = _u(data, so + 40, 8, endian)
                    offset = _u(data, so + 48, 4, endian)
                else:
                    addr = _u(data, so + 32, 4, endian)
                    size = _u(data, so + 36, 4, endian)
                    offset = _u(data, so + 40, 4, endian)
                sec = {"segment": sgname, "name": sname, "addr": addr, "size": size, "offset": offset}
                if offset + size <= len(data) and size:
                    sec["entropy"] = shannon_entropy(data[offset:offset + min(size, 65536)])
                sections.append(sec)
        elif name in ("LOAD_DYLIB", "ID_DYLIB", "REEXPORT_DYLIB", "LAZY_LOAD_DYLIB"):
            noff = _u(data, pos + 8, 4, endian)
            entry["dylib"] = cstring(data, pos + noff)
            entry["current_version"] = _u(data, pos + 16, 4, endian)
        elif name == "UUID":
            entry["uuid"] = data[pos + 8:pos + 24].hex()
        elif name == "CODE_SIGNATURE":
            entry["dataoff"] = _u(data, pos + 8, 4, endian)
            entry["datasize"] = _u(data, pos + 12, 4, endian)
        elif name in ("VERSION_MIN_MACOSX", "VERSION_MIN_IPHONEOS", "VERSION_MIN_TVOS", "VERSION_MIN_WATCHOS"):
            entry["version"] = _ver(_u(data, pos + 8, 4, endian))
            entry["sdk"] = _ver(_u(data, pos + 12, 4, endian))
        elif name == "BUILD_VERSION":
            entry["platform"] = _u(data, pos + 8, 4, endian)
            entry["minos"] = _ver(_u(data, pos + 12, 4, endian))
            entry["sdk"] = _ver(_u(data, pos + 16, 4, endian))
        elif name == "MAIN":
            entry["entryoff"] = _u(data, pos + 8, 8 if is64 else 4, endian)
        elif name == "SYMTAB":
            symtab_cmd = {"symoff": _u(data, pos + 8, 4, endian), "nsyms": _u(data, pos + 12, 4, endian),
                          "stroff": _u(data, pos + 16, 4, endian), "strsize": _u(data, pos + 20, 4, endian)}
        elif name in ("ENCRYPTION_INFO", "ENCRYPTION_INFO_64"):
            entry["cryptoff"] = _u(data, pos + 8, 4, endian)
            entry["cryptsize"] = _u(data, pos + 12, 4, endian)
            entry["cryptid"] = _u(data, pos + 16, 4, endian)
        commands.append(entry)
        pos += cmdsize
    out["load_commands"] = commands
    out["segments"] = segments
    out["sections"] = sections
    out["dylibs"] = [c["dylib"] for c in commands if "dylib" in c and c["cmd"] == "LOAD_DYLIB"]
    uuid_cmd = next((c for c in commands if c["cmd"] == "UUID"), None)
    if uuid_cmd:
        out["uuid"] = uuid_cmd["uuid"]
    cs = next((c for c in commands if c["cmd"] == "CODE_SIGNATURE"), None)
    if cs:
        out["code_signature"] = {"offset": cs["dataoff"], "size": cs["datasize"]}
    if symtab_cmd:
        strtab = data[symtab_cmd["stroff"]:symtab_cmd["stroff"] + symtab_cmd["strsize"]]
        ent = 16 if is64 else 12
        for i in range(min(symtab_cmd["nsyms"], 4000)):
            off = symtab_cmd["symoff"] + i * ent
            if off + ent > len(data):
                break
            strx = _u(data, off, 4, endian)
            ntype = data[off + 4]
            sect = data[off + 5]
            desc = _u(data, off + 6, 2, endian)
            value = _u(data, off + 8, 8 if is64 else 4, endian)
            symbols.append({"name": cstring(strtab, strx), "type": ntype, "section": sect, "desc": desc, "value": value})
        out["symbols"] = symbols
    # ObjC metadata hints
    objc_secs = [s for s in sections if s["name"].startswith("__objc")]
    if objc_secs:
        out["objc_sections"] = [s["name"] for s in objc_secs]
        meth = next((s for s in objc_secs if s["name"] == "__objc_methname"), None)
        if meth and meth["offset"] + meth["size"] <= len(data):
            blob = data[meth["offset"]:meth["offset"] + meth["size"]]
            sels = [x.decode("utf-8", errors="replace") for x in blob.split(b"\x00") if x][:500]
            out["objc_selectors_sample"] = sels
    swift = [s["name"] for s in symbols if s["name"].startswith(("_$s", "_$S"))]
    if swift:
        out["swift_symbols_sample"] = swift[:100]
    return out


def _prot(p: int) -> str:
    return "".join(c for c, b in (("r", 1), ("w", 2), ("x", 4)) if p & b) or "---"


def _ver(v: int) -> str:
    return f"{v >> 16}.{(v >> 8) & 0xFF}.{v & 0xFF}"


# ---------------------------------------------------------------------------
# ar archives
# ---------------------------------------------------------------------------

def parse_ar(data: bytes) -> dict:
    if not data.startswith(b"!<arch>\n"):
        raise RetoolError("not-ar", "Missing ar magic")
    members = []
    pos = 8
    longnames = b""
    while pos + 60 <= len(data):
        hdr = data[pos:pos + 60]
        if hdr[58:60] != b"`\n":
            break
        name = hdr[0:16].decode("ascii", errors="replace").strip()
        size = int(hdr[48:58].decode("ascii").strip() or 0)
        body_off = pos + 60
        if name == "//":
            longnames = data[body_off:body_off + size]
        elif name.startswith("/") and name[1:].isdigit() and longnames:
            idx = int(name[1:])
            end = longnames.find(b"\x00", idx)
            if end < 0:
                end = longnames.find(b"/\n", idx)
            name = longnames[idx:end].decode("ascii", errors="replace").rstrip("/")
        real_name = name.rstrip("/")
        member = {"name": real_name, "size": size, "offset": body_off,
                  "mtime": hdr[16:28].decode("ascii", errors="replace").strip(),
                  "uid": hdr[28:34].decode("ascii", errors="replace").strip(),
                  "gid": hdr[34:40].decode("ascii", errors="replace").strip(),
                  "mode": hdr[40:48].decode("ascii", errors="replace").strip()}
        body = data[body_off:body_off + min(size, 4096)]
        member["content_hint"] = identify_bytes(body, real_name)["best_match"]["name"] if body else "empty"
        members.append(member)
        pos = body_off + size + (size % 2)
        if len(members) > 4000:
            break
    return {"format": "ar", "members": members, "member_count": len(members),
            "is_import_lib": any(m["name"] in ("__.SYMDEF", "__.SYMDEF SORTED", "/") for m in members)}


# ---------------------------------------------------------------------------
# JVM class
# ---------------------------------------------------------------------------

CP_TAGS = {1: "Utf8", 3: "Integer", 4: "Float", 5: "Long", 6: "Double", 7: "Class", 8: "String",
           9: "Fieldref", 10: "Methodref", 11: "InterfaceMethodref", 12: "NameAndType", 15: "MethodHandle",
           16: "MethodType", 17: "Dynamic", 18: "InvokeDynamic", 19: "Module", 20: "Package"}


def parse_class(data: bytes) -> dict:
    need(data, 0, 10, "class header")
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise RetoolError("not-class", "Missing CAFEBABE magic")
    minor = u16(data, 4, "be")
    major = u16(data, 6, "be")
    cp_count = u16(data, 8, "be")
    cp = [None]
    pos = 10
    for _ in range(cp_count - 1):
        if pos >= len(data):
            raise RetoolError("truncated-structure", "constant pool truncated")
        tag = data[pos]
        pos += 1
        entry = {"tag": CP_TAGS.get(tag, tag)}
        if tag == 1:
            ln = u16(data, pos, "be")
            pos += 2
            entry["value"] = data[pos:pos + ln].decode("utf-8", errors="replace")
            pos += ln
        elif tag in (3, 4):
            entry["value"] = u32(data, pos, "be")
            pos += 4
        elif tag in (5, 6):
            entry["value"] = u64(data, pos, "be")
            pos += 8
            cp.append(entry)
            cp.append(None)  # long/double take two slots
            continue
        elif tag in (7, 8, 16, 19, 20):
            entry["index"] = u16(data, pos, "be")
            pos += 2
        elif tag in (9, 10, 11, 12, 17, 18):
            entry["index1"] = u16(data, pos, "be")
            entry["index2"] = u16(data, pos + 2, "be")
            pos += 4
        elif tag == 15:
            entry["kind"] = data[pos]
            entry["index"] = u16(data, pos + 1, "be")
            pos += 3
        else:
            raise RetoolError("bad-constant-pool", f"Unknown constant pool tag {tag} at {pos - 1}")
        cp.append(entry)

    def utf(idx):
        if 0 < idx < len(cp) and cp[idx] and cp[idx]["tag"] == "Utf8":
            return cp[idx]["value"]
        return f"#{idx}"

    def cls(idx):
        if 0 < idx < len(cp) and cp[idx] and cp[idx]["tag"] == "Class":
            return utf(cp[idx]["index"])
        return f"#{idx}"

    access = u16(data, pos, "be")
    this_class = cls(u16(data, pos + 2, "be"))
    super_class = cls(u16(data, pos + 4, "be"))
    pos += 6
    ifcount = u16(data, pos, "be")
    pos += 2
    interfaces = [cls(u16(data, pos + i * 2, "be")) for i in range(ifcount)]
    pos += ifcount * 2

    def read_members(pos):
        count = u16(data, pos, "be")
        pos += 2
        items = []
        for _ in range(count):
            acc = u16(data, pos, "be")
            name = utf(u16(data, pos + 2, "be"))
            desc = utf(u16(data, pos + 4, "be"))
            acount = u16(data, pos + 6, "be")
            pos += 8
            attrs = []
            for _ in range(acount):
                aname = utf(u16(data, pos, "be"))
                alen = u32(data, pos + 2, "be")
                attrs.append({"name": aname, "length": alen})
                pos += 6 + alen
            items.append({"access": acc, "name": name, "descriptor": desc, "attributes": attrs})
        return items, pos

    fields, pos = read_members(pos)
    methods, pos = read_members(pos)
    acount = u16(data, pos, "be")
    pos += 2
    attributes = []
    for _ in range(acount):
        aname = utf(u16(data, pos, "be"))
        alen = u32(data, pos + 2, "be")
        attributes.append({"name": aname, "length": alen})
        pos += 6 + alen
    java_ver = {45: "1.1", 46: "1.2", 47: "1.3", 48: "1.4", 49: "5", 50: "6", 51: "7", 52: "8",
                53: "9", 54: "10", 55: "11", 56: "12", 57: "13", 58: "14", 59: "15", 60: "16",
                61: "17", 62: "18", 63: "19", 64: "20", 65: "21", 66: "22", 67: "23", 68: "24"}
    return {"format": "jvm-class", "major_version": major, "minor_version": minor,
            "java_version": java_ver.get(major, f"unknown({major})"),
            "constant_pool_count": cp_count, "access_flags": access,
            "this_class": this_class, "super_class": super_class, "interfaces": interfaces,
            "fields": fields, "methods": methods, "attributes": attributes,
            "constant_pool_sample": [e for e in cp[1:40] if e],
            "limitation": "Method bytecode bodies are summarized by attributes; full CIL-style decode requires external tools."}


# ---------------------------------------------------------------------------
# DEX
# ---------------------------------------------------------------------------

def parse_dex(data: bytes) -> dict:
    need(data, 0, 112, "DEX header")
    if data[:4] != b"dex\n":
        raise RetoolError("not-dex", "Missing DEX magic")
    hdr = {"version": data[4:7].decode("ascii", errors="replace"), "checksum": hex(u32(data, 8)),
           "signature": data[12:32].hex(), "file_size": u32(data, 32), "header_size": u32(data, 36),
           "endian_tag": hex(u32(data, 40)), "map_off": u32(data, 52),
           "string_ids_size": u32(data, 56), "string_ids_off": u32(data, 60),
           "type_ids_size": u32(data, 64), "type_ids_off": u32(data, 68),
           "proto_ids_size": u32(data, 72), "proto_ids_off": u32(data, 76),
           "field_ids_size": u32(data, 80), "field_ids_off": u32(data, 84),
           "method_ids_size": u32(data, 88), "method_ids_off": u32(data, 92),
           "class_defs_size": u32(data, 96), "class_defs_off": u32(data, 100),
           "data_size": u32(data, 104), "data_off": u32(data, 108)}

    def uleb(off):
        val = 0
        shift = 0
        while True:
            b = data[off]
            off += 1
            val |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
        return val, off

    strings = []
    for i in range(min(hdr["string_ids_size"], 2000)):
        soff = u32(data, hdr["string_ids_off"] + i * 4)
        if soff >= len(data):
            break
        ln, p = uleb(soff)
        raw = data[p:p + ln * 3]
        end = raw.find(b"\x00")
        if end >= 0:
            raw = raw[:end]
        # MUTF-8 differs from UTF-8 only for NUL and supplementary chars; decode as UTF-8 with replacement.
        val = raw.decode("utf-8", errors="replace")
        strings.append(val)
    types = []
    for i in range(min(hdr["type_ids_size"], 2000)):
        idx = u32(data, hdr["type_ids_off"] + i * 4)
        types.append(strings[idx] if idx < len(strings) else f"#{idx}")
    classes = []
    for i in range(min(hdr["class_defs_size"], 1000)):
        off = hdr["class_defs_off"] + i * 32
        if off + 32 > len(data):
            break
        cidx = u32(data, off)
        access = u32(data, off + 4)
        super_idx = u32(data, off + 8)
        classes.append({"class": types[cidx] if cidx < len(types) else f"#{cidx}",
                        "access_flags": access,
                        "superclass": types[super_idx] if super_idx < len(types) else None})
    out = {"format": "dex", "header": hdr, "strings_sample": strings[:500], "types_sample": types[:500],
           "classes": classes,
           "limitation": "String/type/class tables decoded; Dalvik instruction bodies require external tools (baksmali)."}
    # map list
    if hdr["map_off"] and hdr["map_off"] + 4 <= len(data):
        n = u32(data, hdr["map_off"])
        items = []
        for i in range(min(n, 64)):
            off = hdr["map_off"] + 4 + i * 12
            if off + 12 > len(data):
                break
            items.append({"type": hex(u16(data, off)), "size": u32(data, off + 4), "offset": u32(data, off + 8)})
        out["map_list"] = items
    return out


# ---------------------------------------------------------------------------
# WebAssembly
# ---------------------------------------------------------------------------

WASM_SECTIONS = {0: "custom", 1: "type", 2: "import", 3: "function", 4: "table", 5: "memory", 6: "global",
                 7: "export", 8: "start", 9: "element", 10: "code", 11: "data", 12: "datacount"}


def _wasm_uleb(data, pos):
    val = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise RetoolError("truncated-structure", "wasm LEB128 truncated")
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
        if shift > 70:
            raise RetoolError("bad-varint", "wasm LEB128 too long")
    return val, pos


def parse_wasm(data: bytes) -> dict:
    need(data, 0, 8, "wasm header")
    if data[:4] != b"\x00asm":
        raise RetoolError("not-wasm", "Missing wasm magic")
    version = u32(data, 4)
    pos = 8
    sections = []
    types = []
    imports = []
    exports = []
    memories = []
    tables = []
    while pos < len(data):
        sid = data[pos]
        pos += 1
        size, pos = _wasm_uleb(data, pos)
        body = data[pos:pos + size]
        if len(body) < size:
            raise RetoolError("truncated-structure", f"wasm section {sid} truncated")
        entry = {"id": sid, "name": WASM_SECTIONS.get(sid, f"unknown({sid})"), "size": size}
        p = 0
        if sid == 0:
            nlen, p = _wasm_uleb(body, p)
            entry["custom_name"] = body[p:p + nlen].decode("utf-8", errors="replace")
        elif sid == 1:
            n, p = _wasm_uleb(body, p)
            for _ in range(min(n, 512)):
                form = body[p]
                p += 1
                np, p = _wasm_uleb(body, p)
                params = list(body[p:p + np])
                p += np
                nr, p = _wasm_uleb(body, p)
                results = list(body[p:p + nr])
                p += nr
                types.append({"params": params, "results": results})
        elif sid == 2:
            n, p = _wasm_uleb(body, p)
            for _ in range(min(n, 512)):
                ml, p = _wasm_uleb(body, p)
                mod = body[p:p + ml].decode("utf-8", errors="replace")
                p += ml
                nl, p = _wasm_uleb(body, p)
                nm = body[p:p + nl].decode("utf-8", errors="replace")
                p += nl
                kind = body[p]
                p += 1
                idx, p = _wasm_uleb(body, p)
                imports.append({"module": mod, "name": nm, "kind": {0: "func", 1: "table", 2: "memory", 3: "global"}.get(kind, kind), "index": idx})
        elif sid == 7:
            n, p = _wasm_uleb(body, p)
            for _ in range(min(n, 512)):
                nl, p = _wasm_uleb(body, p)
                nm = body[p:p + nl].decode("utf-8", errors="replace")
                p += nl
                kind = body[p]
                p += 1
                idx, p = _wasm_uleb(body, p)
                exports.append({"name": nm, "kind": {0: "func", 1: "table", 2: "memory", 3: "global"}.get(kind, kind), "index": idx})
        elif sid == 5:
            n, p = _wasm_uleb(body, p)
            for _ in range(min(n, 16)):
                flags = body[p]
                p += 1
                mn, p = _wasm_uleb(body, p)
                mx = None
                if flags & 1:
                    mx, p = _wasm_uleb(body, p)
                memories.append({"min_pages": mn, "max_pages": mx})
        elif sid == 4:
            n, p = _wasm_uleb(body, p)
            for _ in range(min(n, 16)):
                et = body[p]
                p += 1
                flags = body[p]
                p += 1
                mn, p = _wasm_uleb(body, p)
                mx = None
                if flags & 1:
                    mx, p = _wasm_uleb(body, p)
                tables.append({"elem_type": et, "min": mn, "max": mx})
        sections.append(entry)
        pos += size
        if len(sections) > 256:
            break
    return {"format": "wasm", "version": version, "sections": sections, "types": types,
            "imports": imports, "exports": exports, "memories": memories, "tables": tables,
            "limitation": "Section-level decode; instruction bodies require external wasm tooling."}


# ---------------------------------------------------------------------------
# ZIP / TAR / GZIP
# ---------------------------------------------------------------------------

def zip_safety(z: zipfile.ZipFile, cfg: dict) -> dict:
    issues = []
    total_uncompressed = 0
    names = {}
    for info in z.infolist():
        n = info.filename
        names[n] = names.get(n, 0) + 1
        if n.startswith("/") or n.startswith("\\") or ".." in Path(n).parts or re.match(r"^[A-Za-z]:", n):
            issues.append({"entry": n, "issue": "path-traversal-or-absolute", "severity": "high"})
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode & 0o170000 == 0o120000:
            issues.append({"entry": n, "issue": "symlink-entry", "severity": "medium"})
        total_uncompressed += info.file_size
        if info.compress_size and info.file_size / max(info.compress_size, 1) > cfg["limits"]["max_extract_ratio"]:
            issues.append({"entry": n, "issue": "high-expansion-ratio", "severity": "medium",
                           "ratio": round(info.file_size / max(info.compress_size, 1), 1)})
    for n, c in names.items():
        if c > 1:
            issues.append({"entry": n, "issue": "duplicate-name", "severity": "medium", "count": c})
    if total_uncompressed > cfg["limits"]["max_extract_bytes"]:
        issues.append({"issue": "total-uncompressed-exceeds-limit", "severity": "high",
                       "total": total_uncompressed, "limit": cfg["limits"]["max_extract_bytes"]})
    return {"issues": issues, "total_uncompressed": total_uncompressed, "entry_count": len(z.infolist())}


def parse_zip(data: bytes, cfg: dict) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RetoolError("bad-zip", f"ZIP cannot be opened: {e}")
    entries = []
    for info in z.infolist()[:cfg["limits"]["max_archive_entries"]]:
        entries.append({"name": info.filename, "size": info.file_size, "compressed": info.compress_size,
                        "method": info.compress_type, "crc32": f"{info.CRC:08x}",
                        "date_time": list(info.date_time), "is_dir": info.is_dir(),
                        "external_attr": info.external_attr})
    safety = zip_safety(z, cfg)
    names = [e["name"] for e in entries]
    kind = "zip"
    if "AndroidManifest.xml" in names:
        kind = "apk" if any(n.endswith(".dex") for n in names) else "aab-or-apk"
    elif "META-INF/MANIFEST.MF" in names:
        kind = "jar"
    elif any(n.startswith("Payload/") and ".app/" in n for n in names):
        kind = "ipa"
    elif any(n.startswith("word/") for n in names):
        kind = "ooxml-word"
    elif any(n.startswith("xl/") for n in names):
        kind = "ooxml-excel"
    elif any(n.startswith("ppt/") for n in names):
        kind = "ooxml-powerpoint"
    elif "mimetype" in names:
        kind = "odf-or-epub"
    elif "manifest.json" in names or "index.json" in names or "oci-layout" in names:
        kind = "oci-or-extension"
    # CRC verification (bounded)
    crc_results = []
    for info in z.infolist()[:200]:
        if info.is_dir() or info.file_size > cfg["limits"]["max_read_bytes"]:
            continue
        try:
            content = z.read(info)
            actual = zlib.crc32(content) & 0xFFFFFFFF
            crc_results.append({"name": info.filename, "ok": actual == info.CRC})
        except Exception as e:
            crc_results.append({"name": info.filename, "ok": False, "error": str(e)})
    bad_crc = [c for c in crc_results if not c["ok"]]
    return {"format": "zip", "kind": kind, "comment": z.comment.decode("utf-8", errors="replace"),
            "entries": entries, "safety": safety, "crc_checked": len(crc_results), "crc_failures": bad_crc,
            "reopen_ok": True}


def parse_tar(data: bytes, cfg: dict) -> dict:
    try:
        t = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as e:
        raise RetoolError("bad-tar", f"TAR cannot be opened: {e}")
    members = []
    issues = []
    total = 0
    for m in t.getmembers()[:cfg["limits"]["max_archive_entries"]]:
        members.append({"name": m.name, "size": m.size, "type": m.type.decode("latin1"),
                        "mode": oct(m.mode), "uid": m.uid, "gid": m.gid, "mtime": m.mtime,
                        "linkname": m.linkname})
        total += m.size
        if m.name.startswith("/") or ".." in Path(m.name).parts:
            issues.append({"entry": m.name, "issue": "path-traversal-or-absolute", "severity": "high"})
        if m.issym() or m.islnk():
            issues.append({"entry": m.name, "issue": "link-entry", "severity": "medium", "target": m.linkname})
    if total > cfg["limits"]["max_extract_bytes"]:
        issues.append({"issue": "total-size-exceeds-limit", "severity": "high", "total": total})
    return {"format": "tar", "members": members, "member_count": len(members), "safety_issues": issues,
            "total_uncompressed": total}


def parse_gzip(data: bytes, cfg: dict) -> dict:
    need(data, 0, 10, "gzip header")
    if data[:2] != b"\x1f\x8b":
        raise RetoolError("not-gzip", "Missing gzip magic")
    method = data[2]
    flags = data[3]
    mtime = u32(data, 4)
    os = data[9]
    pos = 10
    name = None
    if flags & 0x08:
        end = data.find(b"\x00", pos)
        name = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
    out = {"format": "gzip", "method": method, "flags": flags, "mtime": mtime, "os": os, "original_name": name}
    try:
        d = zlib.decompressobj(31)
        content = d.decompress(data, cfg["limits"]["max_extract_bytes"])
        out["decompressed_size"] = len(content)
        out["crc_ok"] = (zlib.crc32(content) & 0xFFFFFFFF) == u32(data, len(data) - 8) if len(data) >= 8 else False
        out["isize_ok"] = (len(content) & 0xFFFFFFFF) == u32(data, len(data) - 4) if len(data) >= 8 else False
        out["content_preview"] = content[:256].decode("utf-8", errors="replace")
        out["content_sha256"] = hash_bytes(content)["sha256"]
    except zlib.error as e:
        out["decompress_error"] = str(e)
    return out


# ---------------------------------------------------------------------------
# Images / media
# ---------------------------------------------------------------------------

def parse_png(data: bytes) -> dict:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RetoolError("not-png", "Missing PNG signature")
    chunks = []
    pos = 8
    info = {}
    while pos + 8 <= len(data):
        ln = u32(data, pos, "be")
        typ = data[pos + 4:pos + 8].decode("ascii", errors="replace")
        body = data[pos + 8:pos + 8 + ln]
        crc_stored = u32(data, pos + 8 + ln, "be") if pos + 12 + ln <= len(data) else 0
        crc_ok = (zlib.crc32(typ.encode() + body) & 0xFFFFFFFF) == crc_stored
        chunks.append({"type": typ, "length": ln, "offset": pos, "crc_ok": crc_ok})
        if typ == "IHDR" and ln >= 13:
            info = {"width": u32(body, 0, "be"), "height": u32(body, 4, "be"), "bit_depth": body[8],
                    "color_type": body[9], "compression": body[10], "filter": body[11], "interlace": body[12]}
        if typ in ("tEXt", "iTXt", "zTXt"):
            chunks[-1]["text_preview"] = body[:200].decode("utf-8", errors="replace")
        pos += 12 + ln
        if typ == "IEND" or len(chunks) > 1000:
            break
    return {"format": "png", "ihdr": info, "chunks": chunks,
            "crc_failures": [c for c in chunks if not c["crc_ok"]]}


def parse_jpeg(data: bytes) -> dict:
    if data[:3] != b"\xff\xd8\xff":
        raise RetoolError("not-jpeg", "Missing JPEG SOI")
    markers = []
    pos = 2
    info = {}
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            markers.append({"marker": f"0xFF{marker:02X}", "offset": pos})
            pos += 2
            continue
        ln = u16(data, pos + 2, "be")
        entry = {"marker": f"0xFF{marker:02X}", "offset": pos, "length": ln}
        if marker in (0xC0, 0xC1, 0xC2) and ln >= 8:
            info = {"precision": data[pos + 4], "height": u16(data, pos + 5, "be"),
                    "width": u16(data, pos + 7, "be"), "components": data[pos + 9]}
        if marker == 0xE1:
            entry["exif"] = data[pos + 4:pos + 10] == b"Exif\x00\x00"
        markers.append(entry)
        pos += 2 + ln
        if marker == 0xDA:  # start of scan; entropy-coded data follows
            break
        if len(markers) > 500:
            break
    return {"format": "jpeg", "sof": info, "markers": markers}


def parse_gif(data: bytes) -> dict:
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise RetoolError("not-gif", "Missing GIF signature")
    return {"format": "gif", "version": data[:6].decode(), "width": u16(data, 6), "height": u16(data, 8),
            "packed": data[10], "bg_color": data[11], "aspect": data[12],
            "gct_present": bool(data[10] & 0x80), "gct_size": 2 ** ((data[10] & 7) + 1) if data[10] & 0x80 else 0}


def parse_riff(data: bytes) -> dict:
    if data[:4] != b"RIFF":
        raise RetoolError("not-riff", "Missing RIFF")
    form = data[8:12].decode("ascii", errors="replace")
    chunks = []
    pos = 12
    info = {}
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4].decode("ascii", errors="replace")
        ln = u32(data, pos + 4)
        body = data[pos + 8:pos + 8 + ln]
        entry = {"id": cid, "size": ln, "offset": pos}
        if cid == "fmt " and ln >= 16:
            info = {"audio_format": u16(body, 0), "channels": u16(body, 2), "sample_rate": u32(body, 4),
                    "byte_rate": u32(body, 8), "block_align": u16(body, 12), "bits": u16(body, 14)}
        if cid == "LIST":
            entry["list_type"] = body[:4].decode("ascii", errors="replace")
        chunks.append(entry)
        pos += 8 + ln + (ln % 2)
        if len(chunks) > 500:
            break
    return {"format": "riff", "form_type": form, "chunks": chunks, "fmt": info}


def parse_mp4(data: bytes) -> dict:
    boxes = []

    def walk(pos, end, depth):
        while pos + 8 <= end and depth < 6:
            size = u32(data, pos, "be")
            typ = data[pos + 4:pos + 8].decode("ascii", errors="replace")
            hdr = 8
            if size == 1:
                size = u64(data, pos + 8, "be")
                hdr = 16
            elif size == 0:
                size = end - pos
            if size < hdr or pos + size > end:
                boxes.append({"type": typ, "offset": pos, "error": "invalid box size"})
                break
            entry = {"type": typ, "offset": pos, "size": size, "depth": depth}
            if typ == "ftyp":
                entry["major_brand"] = data[pos + 8:pos + 12].decode("ascii", errors="replace")
                entry["compatible"] = [data[pos + 16 + i:pos + 20 + i].decode("ascii", errors="replace")
                                       for i in range(0, min(size - 16, 64), 4)]
            if typ == "mvhd" and size >= 32:
                ver = data[pos + 8]
                if ver == 1:
                    entry["timescale"] = u32(data, pos + 28, "be")
                    entry["duration"] = u64(data, pos + 32, "be")
                else:
                    entry["timescale"] = u32(data, pos + 20, "be")
                    entry["duration"] = u32(data, pos + 24, "be")
            if typ == "hdlr" and size >= 24:
                entry["handler"] = data[pos + 16:pos + 20].decode("ascii", errors="replace")
            boxes.append(entry)
            if typ in ("moov", "trak", "mdia", "minf", "stbl", "udta", "edts"):
                walk(pos + hdr, pos + size, depth + 1)
            pos += size
            if len(boxes) > 1000:
                break

    walk(0, len(data), 0)
    return {"format": "iso-bmff", "boxes": boxes, "box_count": len(boxes)}


def parse_pdf(data: bytes) -> dict:
    if not data.startswith(b"%PDF"):
        raise RetoolError("not-pdf", "Missing PDF header")
    version = data[5:8].decode("ascii", errors="replace")
    text = data.decode("latin-1", errors="replace")
    flags = []
    for pat, label in [("/JavaScript", "javascript"), ("/JS", "javascript"), ("/OpenAction", "openaction"),
                       ("/AA", "additional-actions"), ("/EmbeddedFile", "embedded-file"), ("/Launch", "launch"),
                       ("/URI", "uri"), ("/SubmitForm", "submit-form"), ("/ObjStm", "object-streams"),
                       ("/XFA", "xfa-forms"), ("/AcroForm", "acroform")]:
        if pat in text:
            flags.append(label)
    obj_count = len(re.findall(r"\b\d+\s+\d+\s+obj\b", text))
    pages = len(re.findall(r"/Type\s*/Page\b", text))
    xref = "xref" in text
    trailer = "/Root" in text
    eof = text.rstrip().endswith("%%EOF")
    return {"format": "pdf", "version": version, "objects_approx": obj_count, "pages_approx": pages,
            "has_xref": xref, "has_trailer_root": trailer, "eof_marker": eof, "risk_flags": sorted(set(flags)),
            "limitation": "Object counts are regex-based; object streams and linearized files need deeper parsing."}


# ---------------------------------------------------------------------------
# OLE / CFB compound documents
# ---------------------------------------------------------------------------

def parse_cfb(data: bytes) -> dict:
    need(data, 0, 512, "CFB header")
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise RetoolError("not-cfb", "Missing CFB signature")
    sector_shift = u16(data, 30, "le")
    mini_shift = u16(data, 32, "le")
    sector_size = 1 << sector_shift
    num_fat = u32(data, 44, "le")
    first_dir = u32(data, 48, "le")
    first_mini_fat = u32(data, 60, "le")
    num_mini_fat = u32(data, 64, "le")
    first_difat = u32(data, 68, "le")
    num_difat = u32(data, 72, "le")
    difat = list(struct.unpack_from("<109I", data, 76))
    # extend DIFAT
    pos = first_difat
    for _ in range(min(num_difat, 1000)):
        if pos in (0xFFFFFFFE, 0xFFFFFFFF) or (pos + 1) * sector_size + 4 > len(data):
            break
        base = (pos + 1) * sector_size
        entries = struct.unpack_from(f"<{sector_size // 4 - 1}I", data, base)
        difat.extend(entries)
        pos = u32(data, base + sector_size - 4)
    fat_sectors = [s for s in difat[:num_fat] if s not in (0xFFFFFFFF, 0xFFFFFFFE)]
    fat = []
    for s in fat_sectors[:4096]:
        base = (s + 1) * sector_size
        if base + sector_size <= len(data):
            fat.extend(struct.unpack_from(f"<{sector_size // 4}I", data, base))

    def chain(start):
        out = []
        cur = start
        seen = set()
        while cur not in (0xFFFFFFFE, 0xFFFFFFFF) and cur < len(fat) and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = fat[cur]
            if len(out) > 100000:
                break
        return out

    def read_chain(start, size=None):
        parts = []
        for s in chain(start):
            base = (s + 1) * sector_size
            parts.append(data[base:base + sector_size])
        blob = b"".join(parts)
        return blob[:size] if size else blob

    dir_blob = read_chain(first_dir)
    entries = []
    for i in range(len(dir_blob) // 128):
        ent = dir_blob[i * 128:(i + 1) * 128]
        name_len = u16(ent, 64, "le")
        if name_len == 0:
            continue
        name = ent[:name_len - 2].decode("utf-16le", errors="replace")
        etype = ent[66]
        start = u32(ent, 116, "le")
        size = u64(ent, 120, "le")
        entries.append({"index": i, "name": name, "type": {0: "empty", 1: "storage", 2: "stream", 5: "root"}.get(etype, etype),
                        "start_sector": start, "size": size})
    streams = [e for e in entries if e["type"] == "stream"]
    macro_streams = [e["name"] for e in entries if "VBA" in e["name"].upper() or "_VBA_PROJECT" in e["name"].upper()]
    return {"format": "cfb-ole", "sector_size": sector_size, "mini_sector_size": 1 << mini_shift,
            "fat_sectors": len(fat_sectors), "directory_entries": entries[:500],
            "stream_count": len(streams), "macro_indicators": macro_streams,
            "limitation": "Directory and FAT walked; mini-stream content extraction is summarized, not fully decoded."}


# ---------------------------------------------------------------------------
# Device tree, partitions, firmware, ROMs, disks
# ---------------------------------------------------------------------------

def parse_dtb(data: bytes) -> dict:
    need(data, 0, 40, "DTB header")
    if u32(data, 0, "be") != 0xD00DFEED:
        raise RetoolError("not-dtb", "Missing FDT magic")
    totalsize = u32(data, 4, "be")
    off_struct = u32(data, 8, "be")
    off_strings = u32(data, 12, "be")
    version = u32(data, 20, "be")
    size_strings = u32(data, 32, "be")
    size_struct = u32(data, 36, "be")
    strings = data[off_strings:off_strings + size_strings]
    nodes = []
    pos = off_struct
    depth = 0
    path = []
    end = off_struct + size_struct
    while pos + 4 <= min(end, len(data)):
        token = u32(data, pos, "be")
        pos += 4
        if token == 1:  # BEGIN_NODE
            name = cstring(data, pos)
            pos = align_up(pos + len(name) + 1, 4)
            path.append(name or "/")
            depth += 1
            nodes.append({"node": "/".join(path).replace("//", "/"), "depth": depth, "props": []})
        elif token == 2:  # END_NODE
            if path:
                path.pop()
            depth -= 1
        elif token == 3:  # PROP
            ln = u32(data, pos, "be")
            nameoff = u32(data, pos + 4, "be")
            pos += 8
            val = data[pos:pos + ln]
            pos = align_up(pos + ln, 4)
            pname = cstring(strings, nameoff)
            if nodes:
                prop = {"name": pname, "length": ln}
                if pname in ("compatible", "model", "status", "device_type", "reg-names", "clock-names"):
                    prop["value"] = val.rstrip(b"\x00").decode("utf-8", errors="replace")
                elif ln == 4:
                    prop["value"] = u32(val, 0, "be")
                elif ln == 8:
                    prop["value"] = [u32(val, 0, "be"), u32(val, 4, "be")]
                else:
                    prop["value_hex"] = val[:64].hex()
                nodes[-1]["props"].append(prop)
        elif token == 4:  # NOP
            continue
        elif token == 9:  # END
            break
        else:
            break
        if len(nodes) > 2000:
            break
    return {"format": "device-tree", "version": version, "totalsize": totalsize,
            "node_count": len(nodes), "nodes": nodes[:500]}


def parse_mbr(data: bytes) -> dict:
    need(data, 0, 512, "MBR")
    parts = []
    for i in range(4):
        off = 446 + i * 16
        e = data[off:off + 16]
        if e[4] == 0:
            continue
        parts.append({"index": i, "bootable": e[0] == 0x80, "type": hex(e[4]),
                      "lba_start": u32(e, 8), "lba_size": u32(e, 12)})
    return {"format": "mbr", "signature_ok": data[510:512] == b"\x55\xaa", "partitions": parts,
            "disk_signature": data[440:444].hex()}


def parse_gpt(data: bytes) -> dict:
    need(data, 512, 92, "GPT header")
    if data[512:520] != b"EFI PART":
        raise RetoolError("not-gpt", "Missing EFI PART signature")
    hdr_size = u32(data, 524, "le")
    entry_lba = u64(data, 584, "le")
    num_entries = u32(data, 592, "le")
    entry_size = u32(data, 596, "le")
    entries = []
    for i in range(min(num_entries, 256)):
        off = entry_lba * 512 + i * entry_size
        if off + entry_size > len(data) or entry_size < 128:
            break
        tguid = data[off:off + 16]
        if tguid == b"\x00" * 16:
            continue
        name = data[off + 56:off + 128].decode("utf-16le", errors="replace").rstrip("\x00")
        entries.append({"index": i, "type_guid": _guid(tguid), "unique_guid": _guid(data[off + 16:off + 32]),
                        "first_lba": u64(data, off + 32, "le"), "last_lba": u64(data, off + 40, "le"), "name": name})
    return {"format": "gpt", "header_size": hdr_size, "current_lba": u64(data, 536, "le"),
            "backup_lba": u64(data, 544, "le"), "disk_guid": _guid(data[568:584]),
            "partitions": entries}


def _guid(b: bytes) -> str:
    if len(b) != 16:
        return b.hex()
    d1 = b[0:4][::-1].hex()
    d2 = b[4:6][::-1].hex()
    d3 = b[6:8][::-1].hex()
    return f"{d1}-{d2}-{d3}-{b[8:10].hex()}-{b[10:16].hex()}"


def parse_bootimg(data: bytes) -> dict:
    if data[:8] != b"ANDROID!":
        raise RetoolError("not-bootimg", "Missing ANDROID! magic")
    layout = _resolve_layout("android-bootimg-v0")
    fields = annotate_layout(data, layout)
    fmap = {f["field"]: f["value"] for f in fields if "value" in f}
    ver = fmap.get("header_version", 0)
    out = {"format": "android-boot", "header_version": ver, "fields": fields}
    page = fmap.get("page_size", 4096) or 4096
    if ver <= 2:
        ks = fmap.get("kernel_size", 0)
        rs = fmap.get("ramdisk_size", 0)
        ss = fmap.get("second_size", 0)
        out["components"] = {"kernel": {"offset": page, "size": ks},
                             "ramdisk": {"offset": page + align_up(ks, page), "size": rs},
                             "second": {"offset": page + align_up(ks, page) + align_up(rs, page), "size": ss}}
        osver = fmap.get("os_version", 0)
        if osver:
            a = (osver >> 25) & 0x7F
            b = (osver >> 18) & 0x7F
            c = (osver >> 11) & 0x7F
            y = ((osver >> 4) & 0x7F) + 2000
            m = osver & 0xF
            out["android_version"] = f"{a}.{b}.{c}"
            out["patch_level"] = f"{y}-{m:02d}"
    else:
        out["limitation"] = "Header v3+ uses a different layout (vendor boot separate); v0-v2 fields shown."
    return out


def parse_squashfs(data: bytes) -> dict:
    endian = "le" if data[:4] == b"hsqs" else "be"
    layout = _resolve_layout("squashfs-superblock")
    layout = dict(layout, endian=endian)
    fields = annotate_layout(data, layout)
    fmap = {f["field"]: f["value"] for f in fields if "value" in f}
    comp = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}.get(fmap.get("compression_id"), fmap.get("compression_id"))
    return {"format": "squashfs", "endian": endian, "fields": fields, "compression": comp,
            "limitation": "Superblock decoded; inode/directory extraction requires unsquashfs or equivalent."}


def parse_ext(data: bytes) -> dict:
    need(data, 1024, 256, "ext superblock")
    sb = data[1024:1024 + 256]
    if sb[56:58] != b"\x53\xef":
        raise RetoolError("not-ext", "Missing ext magic 0xEF53")
    log_block = u32(sb, 24)
    block_size = 1024 << log_block
    return {"format": "ext2-3-4", "inodes_count": u32(sb, 0), "blocks_count": u32(sb, 4),
            "free_blocks": u32(sb, 12), "free_inodes": u32(sb, 16), "block_size": block_size,
            "blocks_per_group": u32(sb, 32), "inodes_per_group": u32(sb, 40),
            "magic_ok": True, "feature_compat": hex(u32(sb, 92)), "feature_incompat": hex(u32(sb, 96)),
            "volume_name": sb[120:136].rstrip(b"\x00").decode("utf-8", errors="replace"),
            "last_mounted": sb[136:200].rstrip(b"\x00").decode("utf-8", errors="replace"),
            "limitation": "Superblock only; file extraction requires debugfs/e2fsprogs."}


def parse_fat(data: bytes) -> dict:
    need(data, 0, 512, "FAT boot sector")
    bps = u16(data, 11)
    spc = data[13]
    reserved = u16(data, 14)
    nfats = data[16]
    root_entries = u16(data, 17)
    total16 = u16(data, 19)
    spf16 = u16(data, 22)
    total32 = u32(data, 32)
    spf32 = u32(data, 36)
    oem = data[3:11].decode("ascii", errors="replace")
    fs_type = data[82:90].decode("ascii", errors="replace").strip() if spf16 == 0 else data[54:62].decode("ascii", errors="replace").strip()
    return {"format": "fat", "oem": oem, "bytes_per_sector": bps, "sectors_per_cluster": spc,
            "reserved_sectors": reserved, "fat_count": nfats, "root_entries": root_entries,
            "total_sectors": total16 or total32, "sectors_per_fat": spf16 or spf32,
            "fs_type_hint": fs_type, "signature_ok": data[510:512] == b"\x55\xaa"}


def parse_uefi(data: bytes) -> dict:
    if data[40:44] != b"_FVH":
        raise RetoolError("not-uefi-fv", "Missing _FVH signature at 0x28")
    layout = _resolve_layout("uefi-fv-header")
    fields = annotate_layout(data, layout)
    fmap = {f["field"]: f["value"] for f in fields if "value" in f}
    fv_len = fmap.get("fv_length", 0)
    hdr_len = fmap.get("header_length", 0)
    files = []
    pos = align_up(hdr_len, 8)
    while pos + 24 <= min(fv_len, len(data)) and len(files) < 500:
        guid = data[pos:pos + 16]
        if guid == b"\xff" * 16 or guid == b"\x00" * 16:
            break
        ftype = data[pos + 18]
        size = int.from_bytes(data[pos + 20:pos + 23], "le")
        if size < 24:
            break
        files.append({"guid": _guid(guid), "type": hex(ftype), "size": size, "offset": pos})
        pos = align_up(pos + size, 8)
    types = {0x01: "raw", 0x02: "freeform", 0x03: "sec-core", 0x04: "pei-core", 0x05: "dxe-core",
             0x06: "peim", 0x07: "driver", 0x08: "combined-peim-driver", 0x09: "application", 0x0B: "firmware-volume-image",
             0x0F: "ffs-pad"}
    for f in files:
        f["type_name"] = types.get(int(f["type"], 16), f["type"])
    return {"format": "uefi-fv", "fields": fields, "files": files, "file_count": len(files)}


def parse_rom(data: bytes) -> dict:
    if data[:4] == b"NES\x1a":
        fields = annotate_layout(data, _resolve_layout("nes-rom-header"))
        fmap = {f["field"]: f["value"] for f in fields if "value" in f}
        mapper = (fmap.get("flags6", 0) >> 4) | (fmap.get("flags7", 0) & 0xF0)
        return {"format": "nes-rom", "fields": fields, "mapper": mapper,
                "prg_bytes": fmap.get("prg_rom_pages", 0) * 16384, "chr_bytes": fmap.get("chr_rom_pages", 0) * 8192,
                "vectors": {"nmi": hex(u16(data, 16 + fmap.get("prg_rom_pages", 0) * 16384 - 6)),
                            "reset": hex(u16(data, 16 + fmap.get("prg_rom_pages", 0) * 16384 - 4)),
                            "irq": hex(u16(data, 16 + fmap.get("prg_rom_pages", 0) * 16384 - 2))} if fmap.get("prg_rom_pages") else {}}
    if len(data) > 0x150 and data[0x104:0x108] == b"\xce\xed\x66\x66":
        title = data[0x134:0x144].rstrip(b"\x00").decode("ascii", errors="replace")
        chk = 0
        for i in range(0x134, 0x14D):
            chk = (chk - data[i] - 1) & 0xFF
        return {"format": "gb-rom", "title": title, "cgb_flag": hex(data[0x143]), "sgb_flag": hex(data[0x146]),
                "cartridge_type": hex(data[0x147]), "rom_size_code": hex(data[0x148]), "ram_size_code": hex(data[0x149]),
                "header_checksum_ok": chk == data[0x14D], "header_checksum_computed": hex(chk)}
    if len(data) > 0xC0 and data[4:8] == b"\x24\xff\xae\x51":
        title = data[0xA0:0xAC].rstrip(b"\x00").decode("ascii", errors="replace")
        game_code = data[0xAC:0xB0].decode("ascii", errors="replace")
        chk = 0
        for i in range(0xA0, 0xBD):
            chk = (chk - data[i]) & 0xFF
        chk = (chk - 0x19) & 0xFF
        return {"format": "gba-rom", "title": title, "game_code": game_code,
                "header_checksum_ok": chk == data[0xBD]}
    if data[:4] in (b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"):
        order = {b"\x80\x37\x12\x40": "big", b"\x37\x80\x40\x12": "byteswapped", b"\x40\x12\x37\x80": "little"}[data[:4]]
        title = data[0x20:0x34].rstrip(b"\x00").decode("ascii", errors="replace")
        return {"format": "n64-rom", "byte_order": order, "title": title,
                "clock_rate": hex(u32(data, 4, "be")), "boot_address": hex(u32(data, 8, "be"))}
    if len(data) > 0x110 and data[0x100:0x104] == b"SEGA":
        system = data[0x100:0x110].decode("ascii", errors="replace")
        title_dom = data[0x120:0x150].rstrip().decode("ascii", errors="replace")
        return {"format": "genesis-rom", "system": system, "title_domestic": title_dom,
                "checksum": hex(u16(data, 0x18E, "be"))}
    if len(data) > 0x20 and data[0x1C:0x20] == b"\xc2\x33\x9f\x3d":
        return {"format": "gamecube-disc", "game_id": data[0:6].decode("ascii", errors="replace"),
                "title": data[0x20:0x60].rstrip(b"\x00").decode("ascii", errors="replace")}
    if len(data) > 0x1C and data[0x18:0x1C] == b"\x5d\x1c\x9e\xa3":
        return {"format": "wii-disc", "game_id": data[0:6].decode("ascii", errors="replace"),
                "title": data[0x20:0x60].rstrip(b"\x00").decode("ascii", errors="replace")}
    if data[:4] == b"\x00PBP":
        return {"format": "psp-pbp", "version": u32(data, 4),
                "param_sfo_offset": u32(data, 8), "icon0_offset": u32(data, 12),
                "data_psp_offset": u32(data, 24)}
    raise RetoolError("not-rom", "No supported ROM header found")


def parse_disk(data: bytes) -> dict:
    if data[:4] == b"QFI\xfb":
        fields = annotate_layout(data, _resolve_layout("qcow2-header"))
        return {"format": "qcow2", "fields": fields}
    if data[:8] == b"conectix" or data[-512:-504] == b"conectix":
        footer = data[-512:] if data[-512:-504] == b"conectix" else data[:512]
        fields = annotate_layout(footer, _resolve_layout("vhd-footer"))
        return {"format": "vhd", "fields": fields, "footer_at": "eof" if data[-512:-504] == b"conectix" else "start"}
    if data[:8] == b"vhdxfile":
        return {"format": "vhdx", "signature_ok": True,
                "limitation": "VHDX header signature verified; region table decode not implemented."}
    if data[:4] == b"KDMV":
        return {"format": "vmdk-extent", "version": u32(data, 4), "capacity_sectors": u64(data, 12),
                "grain_size": u64(data, 20)}
    if data[:20].startswith(b"# Disk DescriptorFile") or b"createType" in data[:2048]:
        text = data[:8192].decode("utf-8", errors="replace")
        fields = {}
        for line in text.splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                fields[k.strip()] = v.strip().strip('"')
        return {"format": "vmdk-descriptor", "fields": fields}
    if data[:4] == b"MDMP":
        return parse_minidump(data)
    raise RetoolError("not-disk", "No supported disk image header found")


def parse_minidump(data: bytes) -> dict:
    need(data, 0, 32, "minidump header")
    if data[:4] != b"MDMP":
        raise RetoolError("not-minidump", "Missing MDMP signature")
    nstreams = u32(data, 8)
    dir_rva = u32(data, 12)
    streams = []
    stypes = {0: "unused", 3: "thread-list", 4: "module-list", 5: "memory-list", 6: "exception",
              7: "system-info", 8: "thread-ex", 9: "memory-64-list", 10: "comment-a", 11: "comment-w",
              12: "handle-data", 13: "function-table", 14: "unloaded-module-list", 15: "misc-info",
              16: "memory-info-list", 17: "thread-info-list", 18: "handle-operation-list"}
    out = {"format": "minidump", "version": u32(data, 4), "timestamp": u32(data, 20),
           "flags": u64(data, 24), "streams": streams}
    for i in range(min(nstreams, 128)):
        off = dir_rva + i * 12
        if off + 12 > len(data):
            break
        stype = u32(data, off)
        size = u32(data, off + 4)
        rva = u32(data, off + 8)
        entry = {"type": stypes.get(stype, stype), "size": size, "rva": rva}
        if stype == 6 and rva + 168 <= len(data):  # exception stream
            tid = u32(data, rva)
            code = u32(data, rva + 8)
            flags = u32(data, rva + 12)
            addr = u64(data, rva + 24)
            entry["exception"] = {"thread_id": tid, "code": hex(code), "flags": flags, "address": hex(addr)}
        if stype == 7 and rva + 56 <= len(data):  # system info
            entry["system_info"] = {"processor_arch": u16(data, rva), "processor_count": data[rva + 8],
                                    "major_version": u32(data, rva + 12), "minor_version": u32(data, rva + 16),
                                    "build_number": u32(data, rva + 20)}
        if stype == 4 and rva + 4 <= len(data):  # module list
            nmods = u32(data, rva)
            mods = []
            pos = rva + 4
            for _ in range(min(nmods, 256)):
                if pos + 108 > len(data):
                    break
                base = u64(data, pos)
                size = u32(data, pos + 8)
                name_rva = u32(data, pos + 20)
                name = ""
                if name_rva + 4 <= len(data):
                    ln = u32(data, name_rva)
                    name = data[name_rva + 4:name_rva + 4 + ln].decode("utf-16le", errors="replace")
                mods.append({"base": hex(base), "size": size, "name": name})
                pos += 108
            entry["modules"] = mods
        streams.append(entry)
    return out


# ---------------------------------------------------------------------------
# Android binary XML and resources
# ---------------------------------------------------------------------------

def _axml_string_pool(data: bytes, off: int) -> list:
    # chunk header: type(2) headerSize(2) size(4)
    string_count = u32(data, off + 8)
    flags = u32(data, off + 16)
    strings_start = u32(data, off + 20)
    utf8 = bool(flags & (1 << 8))
    offsets = [u32(data, off + 28 + i * 4) for i in range(min(string_count, 65536))]
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        if p >= len(data):
            out.append("")
            continue
        if utf8:
            # u8 chars len (maybe 2 bytes), u8 bytes len (maybe 2 bytes)
            n = data[p]
            p += 1
            if n & 0x80:
                p += 1
            blen = data[p]
            p += 1
            if blen & 0x80:
                blen = ((blen & 0x7F) << 8) | data[p]
                p += 1
            out.append(data[p:p + blen].decode("utf-8", errors="replace"))
        else:
            n = u16(data, p)
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | u16(data, p)
                p += 2
            out.append(data[p:p + n * 2].decode("utf-16le", errors="replace"))
    return out


def parse_axml(data: bytes) -> dict:
    need(data, 0, 8, "AXML header")
    if u16(data, 0) != 0x0003:
        raise RetoolError("not-axml", "Missing Android binary XML chunk type 0x0003")
    pos = 8
    strings = []
    elements = []
    namespaces = []
    depth = 0
    while pos + 8 <= len(data):
        ctype = u16(data, pos)
        hsize = u16(data, pos + 2)
        csize = u32(data, pos + 4)
        if csize < 8 or pos + csize > len(data):
            break
        body = pos + hsize
        if ctype == 0x0001:  # string pool
            strings = _axml_string_pool(data, pos)
        elif ctype in (0x0100, 0x0101):  # start/end namespace
            prefix = u32(data, body + 8)
            uri = u32(data, body + 12)
            namespaces.append({"prefix": strings[prefix] if prefix < len(strings) else "",
                               "uri": strings[uri] if uri < len(strings) else ""})
        elif ctype == 0x0102:  # start element
            ns = u32(data, body + 8)
            name = u32(data, body + 12)
            attr_count = u16(data, body + 22)
            attrs = []
            for i in range(min(attr_count, 256)):
                ao = body + 24 + i * 20
                if ao + 20 > len(data):
                    break
                ans = u32(data, ao)
                aname = u32(data, ao + 4)
                raw = u32(data, ao + 8)
                vtype = data[ao + 15]
                vdata = u32(data, ao + 16)
                if vtype == 0x03:
                    val = strings[vdata] if vdata < len(strings) else f"@str/{vdata}"
                elif vtype == 0x01:
                    val = f"@ref/{vdata:08x}"
                elif vtype == 0x10:
                    val = vdata if vdata < 0x80000000 else vdata - 0x100000000
                elif vtype == 0x12:
                    val = bool(vdata)
                elif vtype == 0x02:
                    val = f"@attr/{vdata:08x}"
                else:
                    val = f"0x{vdata:08x}(type{vtype})"
                attrs.append({"name": strings[aname] if aname < len(strings) else f"#{aname}",
                              "value": val, "raw": strings[raw] if raw != 0xFFFFFFFF and raw < len(strings) else None})
            elements.append({"depth": depth, "tag": strings[name] if name < len(strings) else f"#{name}",
                             "attrs": attrs})
            depth += 1
        elif ctype == 0x0103:  # end element
            depth = max(0, depth - 1)
        pos += csize
        if len(elements) > 4000:
            break
    return {"format": "android-binary-xml", "string_count": len(strings), "strings_sample": strings[:200],
            "namespaces": namespaces, "elements": elements[:1000],
            "limitation": "Element/attribute tree decoded; style/resource resolution requires resources.arsc context."}


def parse_arsc(data: bytes) -> dict:
    need(data, 0, 12, "ARSC header")
    if u16(data, 0) != 0x0002:
        raise RetoolError("not-arsc", "Missing resources.arsc table chunk 0x0002")
    pkg_count = u32(data, 8)
    pos = u16(data, 2)
    packages = []
    for _ in range(min(pkg_count, 16)):
        if pos + 12 > len(data):
            break
        ctype = u16(data, pos)
        hsize = u16(data, pos + 2)
        csize = u32(data, pos + 4)
        if ctype != 0x0200:
            pos += csize
            continue
        pkg_id = u32(data, pos + 8)
        pkg_name = data[pos + 12:pos + 268].decode("utf-16le", errors="replace").rstrip("\x00")
        type_strings_off = u32(data, pos + 268)
        key_strings_off = u32(data, pos + 276)
        type_strings = _axml_string_pool(data, pos + type_strings_off) if type_strings_off else []
        key_strings = _axml_string_pool(data, pos + key_strings_off) if key_strings_off else []
        entries = []
        p = pos + hsize
        end = pos + csize
        while p + 8 <= end:
            ttype = u16(data, p)
            thsize = u16(data, p + 2)
            tcsize = u32(data, p + 4)
            if tcsize < 8:
                break
            if ttype == 0x0201:  # RES_TABLE_TYPE_TYPE
                tid = data[p + 8]
                entry_count = u32(data, p + 12)
                entries_start = u32(data, p + 16)
                offsets_base = p + thsize
                for i in range(min(entry_count, 2000)):
                    eo = offsets_base + i * 4
                    if eo + 4 > end:
                        break
                    eoff = u32(data, eo)
                    if eoff == 0xFFFFFFFF:
                        continue
                    ep = p + entries_start + eoff
                    if ep + 8 > end:
                        continue
                    esize = u16(data, ep)
                    eflags = u16(data, ep + 2)
                    key = u32(data, ep + 4)
                    name = key_strings[key] if key < len(key_strings) else f"#{key}"
                    value = None
                    if not (eflags & 1) and ep + esize + 8 <= end:
                        vsize = u16(data, ep + esize)
                        vtype = data[ep + esize + 3]
                        vdata = u32(data, ep + esize + 4)
                        if vtype == 0x03:
                            value = f"@string/{vdata}"
                        elif vtype == 0x10:
                            value = vdata
                        elif vtype == 0x12:
                            value = bool(vdata)
                        else:
                            value = f"0x{vdata:08x}"
                    entries.append({"resource_id": f"0x{pkg_id:02x}{tid:02x}{i:04x}",
                                    "type": type_strings[tid - 1] if 0 < tid <= len(type_strings) else f"type{tid}",
                                    "name": name, "value": value})
            p += tcsize
            if len(entries) > 8000:
                break
        packages.append({"id": pkg_id, "name": pkg_name, "type_count": len(type_strings),
                         "entry_count": len(entries), "entries_sample": entries[:500]})
        pos += csize
    return {"format": "android-resources", "package_count": pkg_count, "packages": packages,
            "limitation": "Common type/entry forms decoded; complex bags/overlays partially supported."}


# ---------------------------------------------------------------------------
# Models / shaders / web / games / packages
# ---------------------------------------------------------------------------

def parse_gguf(data: bytes) -> dict:
    if data[:4] != b"GGUF":
        raise RetoolError("not-gguf", "Missing GGUF magic")
    version = u32(data, 4)
    tensor_count = u64(data, 8)
    kv_count = u64(data, 16)
    pos = 24

    def rd_str(p):
        ln = u64(data, p)
        p += 8
        return data[p:p + ln].decode("utf-8", errors="replace"), p + ln

    def rd_val(p, t):
        if t == 0:
            return data[p], p + 1
        if t == 1:
            return int.from_bytes(data[p:p + 1], "le", signed=True), p + 1
        if t == 2:
            return u16(data, p), p + 2
        if t == 3:
            return int.from_bytes(data[p:p + 2], "le", signed=True), p + 2
        if t == 4:
            return u32(data, p), p + 4
        if t == 5:
            return i32(data, p), p + 4
        if t == 6:
            return struct.unpack_from("<f", data, p)[0], p + 4
        if t == 7:
            return bool(data[p]), p + 1
        if t == 8:
            return rd_str(p)
        if t == 9:
            at = u32(data, p)
            n = u64(data, p + 4)
            p += 12
            arr = []
            for _ in range(min(n, 256)):
                v, p = rd_val(p, at)
                arr.append(v)
            return arr, p
        if t == 10:
            return u64(data, p), p + 8
        if t == 11:
            return int.from_bytes(data[p:p + 8], "le", signed=True), p + 8
        if t == 12:
            return struct.unpack_from("<d", data, p)[0], p + 8
        raise RetoolError("bad-gguf-type", f"Unknown GGUF value type {t}")

    kv = {}
    for _ in range(min(kv_count, 512)):
        key, pos = rd_str(pos)
        t = u32(data, pos)
        pos += 4
        val, pos = rd_val(pos, t)
        kv[key] = val
    tensors = []
    for _ in range(min(tensor_count, 2000)):
        name, pos = rd_str(pos)
        ndim = u32(data, pos)
        pos += 4
        dims = [u64(data, pos + i * 8) for i in range(ndim)]
        pos += ndim * 8
        ttype = u32(data, pos)
        pos += 4
        offset = u64(data, pos)
        pos += 8
        tensors.append({"name": name, "dims": dims, "type": ttype, "offset": offset})
    return {"format": "gguf", "version": version, "tensor_count": tensor_count, "metadata_kv_count": kv_count,
            "metadata": kv, "tensors_sample": tensors[:200]}


def parse_safetensors(data: bytes) -> dict:
    need(data, 0, 8, "safetensors header length")
    hlen = u64(data, 0)
    if hlen > len(data) - 8 or hlen > 100_000_000:
        raise RetoolError("bad-safetensors", "Header length out of range")
    header = json.loads(data[8:8 + hlen].decode("utf-8"))
    tensors = {k: v for k, v in header.items() if k != "__metadata__"}
    data_start = 8 + hlen
    issues = []
    for name, t in tensors.items():
        s, e = t.get("data_offsets", [0, 0])
        if data_start + e > len(data):
            issues.append({"tensor": name, "issue": "offsets beyond file end"})
    return {"format": "safetensors", "header_bytes": hlen, "tensor_count": len(tensors),
            "metadata": header.get("__metadata__", {}), "tensors": tensors, "issues": issues,
            "note": "safetensors stores raw tensors with a JSON header; no executable pickle payload."}


def parse_pickle_safe(data: bytes) -> dict:
    import pickletools
    import io as _io
    ops = []
    dangerous = []
    try:
        for op, arg, pos in pickletools.genops(data):
            ops.append({"opcode": op.name, "arg": str(arg)[:120] if arg is not None else None, "pos": pos})
            if op.name in ("REDUCE", "GLOBAL", "STACK_GLOBAL", "INST", "OBJ", "NEWOBJ", "NEWOBJ_EX", "EXT1", "EXT2", "EXT4", "BUILD", "PERSID", "BINPERSID"):
                dangerous.append({"opcode": op.name, "arg": str(arg)[:120] if arg is not None else None, "pos": pos})
            if len(ops) > 5000:
                break
    except Exception as e:
        return {"format": "pickle", "parse_error": str(e), "ops_sample": ops[:100],
                "dangerous_opcodes": dangerous, "executed": False}
    return {"format": "pickle", "op_count": len(ops), "ops_sample": ops[:200],
            "dangerous_opcodes": dangerous[:100], "executed": False,
            "note": "Opcodes disassembled without executing the pickle. REDUCE/GLOBAL indicate code-execution capability on load."}


def parse_spirv(data: bytes) -> dict:
    need(data, 0, 20, "SPIR-V header")
    magic = u32(data, 0, "le")
    endian = "le"
    if magic != 0x07230203:
        magic = u32(data, 0, "be")
        endian = "be"
    if magic != 0x07230203:
        raise RetoolError("not-spirv", "Missing SPIR-V magic")
    version = u32(data, 4, endian)
    generator = u32(data, 8, endian)
    bound = u32(data, 12, endian)
    opnames = {1: "OpSource", 2: "OpSourceExtension", 3: "OpSourceContinued", 5: "OpName", 6: "OpMemberName",
               7: "OpString", 14: "OpMemoryModel", 15: "OpEntryPoint", 16: "OpExecutionMode", 17: "OpCapability",
               19: "OpTypeVoid", 20: "OpTypeBool", 21: "OpTypeInt", 22: "OpTypeFloat", 23: "OpTypeVector",
               32: "OpTypePointer", 33: "OpTypeFunction", 43: "OpConstant", 54: "OpFunction", 55: "OpFunctionParameter",
               56: "OpFunctionEnd", 57: "OpFunctionCall", 59: "OpLoad", 60: "OpStore", 61: "OpAccessChain",
               65: "OpVectorShuffle", 71: "OpDecorate", 77: "OpCompositeConstruct", 81: "OpCompositeExtract",
               124: "OpFAdd", 128: "OpFMul", 248: "OpLabel", 252: "OpBranch", 253: "OpBranchConditional", 254: "OpReturn",
               255: "OpReturnValue"}
    ops = {}
    entry_points = []
    names = {}
    pos = 20
    while pos + 4 <= len(data):
        word = u32(data, pos, endian)
        wc = word >> 16
        op = word & 0xFFFF
        if wc < 1:
            break
        ops[opnames.get(op, f"op{op}")] = ops.get(opnames.get(op, f"op{op}"), 0) + 1
        if op == 15 and wc >= 4:  # EntryPoint
            model = u32(data, pos + 4, endian)
            name = data[pos + 12:pos + wc * 4].split(b"\x00")[0].decode("utf-8", errors="replace")
            entry_points.append({"execution_model": model, "name": name})
        if op == 5 and wc >= 3:  # OpName
            target = u32(data, pos + 4, endian)
            name = data[pos + 8:pos + wc * 4].split(b"\x00")[0].decode("utf-8", errors="replace")
            names[str(target)] = name
        pos += wc * 4
        if sum(ops.values()) > 100000:
            break
    return {"format": "spirv", "endian": endian, "version": f"{version >> 16}.{(version >> 8) & 0xFF}",
            "generator": generator, "id_bound": bound, "opcode_histogram": ops,
            "entry_points": entry_points, "names_sample": dict(list(names.items())[:100]),
            "limitation": "Opcode-level summary; full type/CFG reconstruction requires spirv tools."}


def parse_dxbc(data: bytes) -> dict:
    if data[:4] != b"DXBC":
        raise RetoolError("not-dxbc", "Missing DXBC FourCC")
    chunks = []
    nchunks = u32(data, 28)
    for i in range(min(nchunks, 64)):
        off = 32 + i * 4
        if off + 4 > len(data):
            break
        coff = u32(data, off)
        if coff + 8 > len(data):
            break
        fourcc = data[coff:coff + 4].decode("ascii", errors="replace")
        size = u32(data, coff + 4)
        chunks.append({"fourcc": fourcc, "offset": coff, "size": size})
    return {"format": "dxbc", "digest": data[4:20].hex(), "chunks": chunks,
            "limitation": "Chunk container decoded; DXIL/SHEX instruction decode requires external tools."}


def parse_sourcemap(data: bytes) -> dict:
    try:
        sm = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RetoolError("bad-sourcemap", f"Source map is not valid JSON: {e}")
    if sm.get("version") != 3:
        raise RetoolError("bad-sourcemap", f"Unsupported source map version: {sm.get('version')}")
    mappings = sm.get("mappings", "")
    # decode VLQ segments
    B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    segments = 0
    lines = mappings.split(";")
    max_line = len(lines)
    sources_idx = set()
    for line in lines:
        if not line:
            continue
        for seg in line.split(","):
            if not seg:
                continue
            vals = []
            shift = 0
            val = 0
            for ch in seg:
                if ch not in B64:
                    break
                d = B64.index(ch)
                val |= (d & 31) << shift
                if d & 32:
                    shift += 5
                else:
                    vals.append((val >> 1) ^ -(val & 1))
                    val = 0
                    shift = 0
            if vals:
                segments += 1
                if len(vals) >= 4:
                    sources_idx.add(vals[1])
    return {"format": "source-map", "version": 3, "file": sm.get("file"),
            "source_count": len(sm.get("sources", [])), "sources": sm.get("sources", [])[:200],
            "sources_content_present": bool(sm.get("sourcesContent")),
            "names_count": len(sm.get("names", [])), "mapping_lines": max_line, "mapping_segments": segments,
            "sources_referenced": len(sources_idx),
            "limitation": "Segment counts decoded; full original-position reconstruction is available per segment on request."}


def parse_wad(data: bytes) -> dict:
    if data[:4] not in (b"IWAD", b"PWAD"):
        raise RetoolError("not-wad", "Missing WAD magic")
    num_lumps = u32(data, 4)
    dir_off = u32(data, 8)
    lumps = []
    for i in range(min(num_lumps, 8000)):
        off = dir_off + i * 16
        if off + 16 > len(data):
            break
        lumps.append({"name": data[off + 8:off + 16].rstrip(b"\x00").decode("ascii", errors="replace"),
                      "offset": u32(data, off), "size": u32(data, off + 4)})
    return {"format": "doom-wad", "kind": data[:4].decode(), "lump_count": num_lumps, "lumps": lumps[:1000]}


def parse_pak(data: bytes) -> dict:
    if data[:4] != b"PACK":
        raise RetoolError("not-pak", "Missing PAK magic")
    dir_off = u32(data, 4)
    dir_len = u32(data, 8)
    entries = []
    for i in range(min(dir_len // 64, 8000)):
        off = dir_off + i * 64
        if off + 64 > len(data):
            break
        entries.append({"name": data[off:off + 56].rstrip(b"\x00").decode("ascii", errors="replace"),
                        "offset": u32(data, off + 56), "size": u32(data, off + 60)})
    return {"format": "quake-pak", "entries": entries, "entry_count": len(entries)}


def parse_unity(data: bytes) -> dict:
    if not data.startswith((b"UnityFS", b"UnityWeb", b"UnityRaw")):
        raise RetoolError("not-unity", "Missing Unity bundle signature")
    sig = data.split(b"\x00")[0].decode()
    pos = len(sig) + 1
    version = u32(data, pos, "be")
    pos += 4
    player_ver = cstring(data, pos)
    pos += len(player_ver) + 1
    engine_ver = cstring(data, pos)
    pos += len(engine_ver) + 1
    size = u64(data, pos, "be") if sig == "UnityFS" else 0
    return {"format": "unity-bundle", "signature": sig, "format_version": version,
            "player_version": player_ver, "engine_version": engine_ver, "bundle_size": size,
            "limitation": "Bundle header decoded; block/asset extraction requires UnityPy or equivalent."}


def parse_rpm(data: bytes) -> dict:
    if data[:4] != b"\xed\xab\xee\xdb":
        raise RetoolError("not-rpm", "Missing RPM lead magic")
    major = data[4]
    name = data[10:76].rstrip(b"\x00").decode("ascii", errors="replace")
    pos = 96
    headers = []
    for _ in range(2):
        if pos + 16 > len(data) or data[pos:pos + 3] != b"\x8e\xad\xe8":
            break
        nindex = u32(data, pos + 8, "be")
        hsize = u32(data, pos + 12, "be")
        pos += 16
        index = []
        for i in range(min(nindex, 256)):
            off = pos + i * 16
            if off + 16 > len(data):
                break
            tag = u32(data, off, "be")
            typ = u32(data, off + 4, "be")
            offset = u32(data, off + 8, "be")
            count = u32(data, off + 12, "be")
            index.append({"tag": tag, "type": typ, "offset": offset, "count": count})
        headers.append({"index_count": nindex, "data_size": hsize, "index_sample": index[:50]})
        pos += hsize
        pos = align_up(pos, 8)
    return {"format": "rpm", "lead_major": major, "lead_name": name, "headers": headers,
            "limitation": "Header index decoded; full tag values and payload extraction require rpm tools."}


def parse_plist(data: bytes) -> dict:
    try:
        pl = plistlib.loads(data)
    except Exception as e:
        raise RetoolError("bad-plist", f"Cannot parse plist: {e}")
    return {"format": "plist", "kind": "binary" if data[:8] == b"bplist00" else "xml",
            "keys": list(pl.keys()) if isinstance(pl, dict) else None, "content": _plist_safe(pl)}


def _plist_safe(obj, depth=0):
    if depth > 8:
        return "<depth limit>"
    if isinstance(obj, dict):
        return {k: _plist_safe(v, depth + 1) for k, v in list(obj.items())[:100]}
    if isinstance(obj, list):
        return [_plist_safe(v, depth + 1) for v in obj[:100]]
    if isinstance(obj, bytes):
        return f"<{len(obj)} bytes>"
    return obj


def parse_oci_image(data: bytes, cfg: dict) -> dict:
    t = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    names = t.getnames()
    out = {"format": "oci-image", "entries": names[:200], "layers": [], "whiteouts": []}
    if "manifest.json" in names:
        man = json.loads(t.extractfile("manifest.json").read())
        out["manifest"] = man
        if isinstance(man, list) and man:
            out["layers"] = man[0].get("Layers", [])
            cfg_name = man[0].get("Config")
            if cfg_name and cfg_name in names:
                conf = json.loads(t.extractfile(cfg_name).read())
                out["config"] = {"architecture": conf.get("architecture"), "os": conf.get("os"),
                                 "history_count": len(conf.get("history", [])),
                                 "env": conf.get("config", {}).get("Env", [])[:20],
                                 "entrypoint": conf.get("config", {}).get("Entrypoint"),
                                 "cmd": conf.get("config", {}).get("Cmd")}
    if "index.json" in names:
        out["index"] = json.loads(t.extractfile("index.json").read())
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if base.startswith(".wh."):
            out["whiteouts"].append(n)
    return out


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

FORMAT_HINTS = ["elf", "pe", "macho", "ar", "class", "dex", "wasm", "zip", "tar", "gzip", "png", "jpeg",
                "gif", "riff", "mp4", "pdf", "cfb", "dtb", "mbr", "gpt", "bootimg", "squashfs", "ext",
                "fat", "uefi", "rom", "disk", "axml", "arsc", "gguf", "safetensors", "pickle", "spirv",
                "dxbc", "sourcemap", "wad", "pak", "unity", "rpm", "plist", "oci", "minidump"]


def detect_format(data: bytes, name: str) -> str:
    if len(data) < 8:
        return "unknown"
    probes = structural_probes(data, name)
    if "elf" in probes:
        return "elf"
    if "pe" in probes:
        return "pe"
    if data[:2] == b"MZ":
        return "pe"
    if "macho" in probes or "macho_fat" in probes:
        return "macho"
    if data[:8] == b"!<arch>\n":
        if data[:15] == b"!<arch>\ndebian":
            return "deb"
        return "ar"
    if "java_class" in probes:
        return "class"
    if "dex" in probes:
        return "dex"
    if "wasm" in probes:
        return "wasm"
    if data[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            names = z.namelist()
            if "manifest.json" in names and any(n.endswith(".tar.gz") or "/layer.tar" in n for n in names):
                return "oci"
            if "oci-layout" in names or "index.json" in names:
                return "oci"
        except Exception:
            pass
        return "zip"
    if data[257:262] == b"ustar":
        return "tar"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF":
        return "riff"
    if data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "cfb"
    if data[:4] == b"\xd0\x0d\xfe\xed":
        return "dtb"
    if len(data) > 520 and data[512:520] == b"EFI PART":
        return "gpt"
    if data[:8] == b"ANDROID!":
        return "bootimg"
    if data[:4] in (b"hsqs", b"sqsh"):
        return "squashfs"
    if len(data) > 1082 and data[1080:1082] == b"\x53\xef":
        return "ext"
    if data[40:44] == b"_FVH":
        return "uefi"
    if data[:4] in (b"NES\x1a", b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80", b"\x00PBP"):
        return "rom"
    if len(data) > 0x150 and (data[0x104:0x108] == b"\xce\xed\x66\x66" or data[4:8] == b"\x24\xff\xae\x51" or data[0x100:0x104] == b"SEGA"):
        return "rom"
    if len(data) > 0x20 and (data[0x1C:0x20] == b"\xc2\x33\x9f\x3d" or data[0x18:0x1C] == b"\x5d\x1c\x9e\xa3"):
        return "rom"
    if data[:4] in (b"QFI\xfb", b"KDMV", b"MDMP") or data[:8] in (b"conectix", b"vhdxfile"):
        return "disk"
    if len(data) > 512 and data[-512:-504] == b"conectix":
        return "disk"
    if len(data) > 8 and u16(data, 0) == 0x0003:
        return "axml"
    if len(data) > 12 and u16(data, 0) == 0x0002:
        return "arsc"
    if data[:4] == b"GGUF":
        return "gguf"
    if data[:4] == b"\x07\x23\x02\x03" or data[:4] == b"\x03\x02\x23\x07":
        return "spirv"
    if data[:4] == b"DXBC":
        return "dxbc"
    if data[:4] in (b"IWAD", b"PWAD"):
        return "wad"
    if data[:4] == b"PACK":
        return "pak"
    if data.startswith((b"UnityFS", b"UnityWeb", b"UnityRaw")):
        return "unity"
    if data[:4] == b"\xed\xab\xee\xdb":
        return "rpm"
    if data[:8] == b"bplist00" or data[:5] == b"<?xml":
        return "plist"
    if data[:1] == b"\x80" and len(data) > 2 and data[1] in (2, 3, 4, 5):
        return "pickle"
    if data[:1] == b"{" and b'"sources"' in data[:4096] and b'"mappings"' in data[:8192]:
        return "sourcemap"
    if data[:1] == b"{" and b'"__metadata__"' not in data[:64]:
        # safetensors has binary length prefix, not JSON start; handled below
        pass
    if len(data) > 8:
        hlen = u64(data, 0)
        if 0 < hlen < min(len(data) - 8, 100_000_000) and data[8:9] == b"{":
            try:
                json.loads(data[8:8 + hlen])
                return "safetensors"
            except Exception:
                pass
    if len(data) >= 512 and data[510:512] == b"\x55\xaa":
        return "mbr"
    return "unknown"


def cmd_parse(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    fmt = args.format if args.format and args.format != "auto" else detect_format(data, path.name)
    env["inputs"] = {"path": str(path), "format": fmt, "size": path.stat().st_size}
    if fmt == "unknown":
        ident = identify_bytes(data, path.name)
        env["result"] = {"format": "unknown", "identification": ident,
                         "note": "No dedicated parser matched; see identification and use survey/strings/hexdump."}
        env["limitations"].append("No dedicated parser for this format; identification is signature-based.")
        return env
    parsers = {
        "elf": parse_elf, "pe": parse_pe, "macho": parse_macho, "ar": parse_ar, "deb": parse_ar,
        "class": parse_class, "dex": parse_dex, "wasm": parse_wasm,
        "zip": lambda d: parse_zip(d, cfg), "tar": lambda d: parse_tar(d, cfg),
        "oci": lambda d: parse_oci_image(d, cfg), "gzip": parse_gzip,
        "png": parse_png, "jpeg": parse_jpeg, "gif": parse_gif, "riff": parse_riff, "mp4": parse_mp4,
        "pdf": parse_pdf, "cfb": parse_cfb, "dtb": parse_dtb, "mbr": parse_mbr, "gpt": parse_gpt,
        "bootimg": parse_bootimg, "squashfs": parse_squashfs, "ext": parse_ext, "fat": parse_fat,
        "uefi": parse_uefi, "rom": parse_rom, "disk": parse_disk, "minidump": parse_minidump,
        "axml": parse_axml, "arsc": parse_arsc, "gguf": parse_gguf, "safetensors": parse_safetensors,
        "pickle": parse_pickle_safe, "spirv": parse_spirv, "dxbc": parse_dxbc, "sourcemap": parse_sourcemap,
        "wad": parse_wad, "pak": parse_pak, "unity": parse_unity, "rpm": parse_rpm, "plist": parse_plist,
    }
    parser = parsers.get(fmt)
    if parser is None:
        raise RetoolError("unsupported-format", f"No parser registered for format: {fmt}", exit_code=EXIT_USAGE)
    result = parser(data)
    env["result"] = result
    if "limitation" in result:
        env["limitations"].append(result["limitation"])
    return env


def cmd_sections(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    fmt = detect_format(data, path.name)
    if fmt == "elf":
        parsed = parse_elf(data)
        rows = [{"name": s["name"], "type": s["type"], "addr": hex(s["addr"]), "offset": s["offset"],
                 "size": s["size"], "flags": s["flags"], "entropy": s.get("entropy")} for s in parsed["sections"]]
        segs = [{"type": p["type"], "offset": p["offset"], "vaddr": hex(p["vaddr"]), "filesz": p["filesz"],
                 "memsz": p["memsz"], "permissions": p["permissions"]} for p in parsed["program_headers"]]
        env["result"] = {"format": "elf", "sections": rows, "segments": segs}
    elif fmt == "pe":
        parsed = parse_pe(data)
        rows = [{"name": s["name"], "vaddr": hex(s["virtual_address"]), "vsize": s["virtual_size"],
                 "offset": s["raw_offset"], "raw_size": s["raw_size"], "permissions": s["permissions"],
                 "entropy": s.get("entropy")} for s in parsed["sections"]]
        env["result"] = {"format": "pe", "sections": rows,
                         "image_base": hex(parsed.get("optional", {}).get("image_base", 0)),
                         "entry": hex(parsed.get("optional", {}).get("entry_rva", 0))}
    elif fmt == "macho":
        parsed = parse_macho(data)
        rows = [{"segment": s["segment"], "name": s["name"], "addr": hex(s["addr"]), "offset": s["offset"],
                 "size": s["size"], "entropy": s.get("entropy")} for s in parsed["sections"]]
        segs = [{"name": s["name"], "vmaddr": hex(s["vmaddr"]), "vmsize": s["vmsize"], "fileoff": s["fileoff"],
                 "filesize": s["filesize"], "prot": s["initprot"]} for s in parsed["segments"]]
        env["result"] = {"format": "macho", "sections": rows, "segments": segs}
    else:
        raise RetoolError("unsupported-format", f"Section maps are supported for ELF/PE/Mach-O; detected {fmt}", exit_code=EXIT_USAGE)
    return env


# Itanium demangler subset (nested names + primitive params)
_ITANIUM_PRIM = {"v": "void", "w": "wchar_t", "b": "bool", "c": "char", "a": "signed char", "h": "unsigned char",
                 "s": "short", "t": "unsigned short", "i": "int", "j": "unsigned int", "l": "long",
                 "m": "unsigned long", "x": "long long", "y": "unsigned long long", "n": "__int128",
                 "o": "unsigned __int128", "f": "float", "d": "double", "e": "long double", "z": "..."}


def demangle_itanium(name: str) -> str | None:
    if not name.startswith("_Z"):
        return None
    s = name[2:]
    pos = 0

    def parse_name(s, pos):
        parts = []
        if pos < len(s) and s[pos] == "N":
            pos += 1
            # skip qualifiers
            while pos < len(s) and s[pos] in "rVK":
                pos += 1
            while pos < len(s) and s[pos] != "E":
                if s[pos].isdigit():
                    n = 0
                    while pos < len(s) and s[pos].isdigit():
                        n = n * 10 + int(s[pos])
                        pos += 1
                    parts.append(s[pos:pos + n])
                    pos += n
                elif s[pos] == "I":  # template args: skip to matching E
                    depth = 1
                    pos += 1
                    while pos < len(s) and depth:
                        if s[pos] == "I":
                            depth += 1
                        elif s[pos] == "E":
                            depth -= 1
                        pos += 1
                elif s[pos] in "CD":  # ctor/dtor
                    pos += 2
                    parts.append("~" + parts[-1] if s[pos - 2] == "D" and parts else parts[-1] if parts else "ctor")
                else:
                    break
            if pos < len(s) and s[pos] == "E":
                pos += 1
            return "::".join(parts), pos
        if pos < len(s) and s[pos].isdigit():
            n = 0
            while pos < len(s) and s[pos].isdigit():
                n = n * 10 + int(s[pos])
                pos += 1
            return s[pos:pos + n], pos + n
        return None, pos

    fname, pos = parse_name(s, pos)
    if fname is None:
        return None
    params = []
    while pos < len(s) and len(params) < 24:
        ch = s[pos]
        if ch in _ITANIUM_PRIM:
            params.append(_ITANIUM_PRIM[ch])
            pos += 1
        elif ch == "P":
            pos += 1
            inner, pos = _demangle_type(s, pos)
            params.append(inner + "*")
        elif ch == "R":
            pos += 1
            inner, pos = _demangle_type(s, pos)
            params.append(inner + "&")
        elif ch.isdigit():
            n = 0
            while pos < len(s) and s[pos].isdigit():
                n = n * 10 + int(s[pos])
                pos += 1
            params.append(s[pos:pos + n])
            pos += n
        elif ch == "N":
            inner, pos = parse_name(s, pos)
            params.append(inner or "?")
        else:
            break
    return f"{fname}({', '.join(params)})" if params else fname


def _demangle_type(s, pos):
    if pos < len(s) and s[pos] in _ITANIUM_PRIM:
        return _ITANIUM_PRIM[s[pos]], pos + 1
    if pos < len(s) and s[pos].isdigit():
        n = 0
        while pos < len(s) and s[pos].isdigit():
            n = n * 10 + int(s[pos])
            pos += 1
        return s[pos:pos + n], pos + n
    return "?", pos + 1


def cmd_symbols(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    fmt = detect_format(data, path.name)
    result = {"format": fmt}
    if fmt == "elf":
        parsed = parse_elf(data)
        syms = parsed["symbols"]
        result.update({"imports": sorted(set(syms["imports"])), "exports": sorted(set(syms["exports"])),
                       "symtab_count": len(syms["symtab"]), "dynsym_count": len(syms["dynsym"]),
                       "needed": parsed.get("needed", []), "soname": parsed.get("soname"),
                       "symbols_sample": syms["symtab"][:200]})
    elif fmt == "pe":
        parsed = parse_pe(data)
        result["imports"] = parsed.get("imports", [])
        result["exports"] = parsed.get("exports", {})
        result["clr"] = parsed.get("clr")
    elif fmt == "macho":
        parsed = parse_macho(data)
        result["symbols"] = parsed.get("symbols", [])[:500]
        result["dylibs"] = parsed.get("dylibs", [])
        result["objc_selectors_sample"] = parsed.get("objc_selectors_sample", [])
        result["swift_symbols_sample"] = parsed.get("swift_symbols_sample", [])
    elif fmt == "class":
        parsed = parse_class(data)
        result["class"] = parsed["this_class"]
        result["methods"] = [{"name": m["name"], "descriptor": m["descriptor"]} for m in parsed["methods"]]
        result["fields"] = [{"name": f["name"], "descriptor": f["descriptor"]} for f in parsed["fields"]]
    elif fmt == "dex":
        parsed = parse_dex(data)
        result["classes"] = parsed["classes"]
        result["strings_sample"] = parsed["strings_sample"][:200]
    elif fmt == "ar":
        parsed = parse_ar(data)
        result["members"] = parsed["members"][:500]
    else:
        raise RetoolError("unsupported-format", f"Symbol reports supported for ELF/PE/Mach-O/class/dex/ar; detected {fmt}", exit_code=EXIT_USAGE)
    if args.demangle:
        demangled = []
        names = []
        if fmt == "elf":
            names = [s["name"] for s in result.get("symbols_sample", [])] + result.get("exports", [])
        elif fmt == "macho":
            names = [s["name"] for s in result.get("symbols", [])]
        for n in names[:500]:
            d = demangle_itanium(n.lstrip("_"))
            if d:
                demangled.append({"mangled": n, "demangled": d})
        result["demangled_sample"] = demangled[:200]
        env["limitations"].append("Demangler covers common Itanium nested-name and primitive-parameter forms; complex templates are skipped.")
    env["result"] = result
    return env


def _layout_size(layout: dict) -> int:
    off = 0
    for f in layout["fields"]:
        t = f["type"]
        count = f.get("count", 1)
        if t in ("bytes", "char"):
            off += count
        else:
            off += TYPE_SIZES[t] * count
    return off


def cmd_structs(args, env, cfg):
    """Mine structures: decode a layout spec, scan pointer tables, or detect record strides."""
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    base = int(getattr(args, "offset", None) or "0", 0)
    count = int(getattr(args, "count", None) or "1")
    count = max(1, min(count, cfg["limits"].get("max_records", 1000)))
    result = {"file": str(path), "size": len(data), "offset": base}
    did = False
    if getattr(args, "layout", None):
        layout = _resolve_layout(args.layout)
        lsize = _layout_size(layout)
        result["layout"] = args.layout
        result["layout_size"] = lsize
        records = []
        for i in range(count):
            off = base + i * lsize
            if off + lsize > len(data):
                result["truncated_at_record"] = i
                break
            records.append({"index": i, "offset": off, "fields": annotate_layout(data[off:off + lsize], layout, base=off)})
        result["records"] = records
        result["record_count"] = len(records)
        did = True
    if getattr(args, "pointers", False):
        # Scan aligned 4/8-byte values that fall in plausible pointer ranges.
        cands = {32: [], 64: []}
        limit = cfg["limits"].get("max_scan_hits", 5000)
        for width, step in ((4, 4), (8, 8)):
            hits = []
            for off in range(base, len(data) - width + 1, step):
                v = int.from_bytes(data[off:off + width], "little")
                # plausible: inside file, or typical image-base / heap / stack ranges
                in_file = v < len(data)
                image = 0x10000 <= v <= 0x7FFFFFFFFFFF and (v & (width - 1)) == 0
                if in_file or image:
                    hits.append({"offset": off, "value": hex(v),
                                 "kind": "file-relative" if in_file else "absolute-aligned"})
                    if len(hits) >= limit:
                        break
            cands[width] = hits
        result["pointer_candidates"] = {"u32le": cands[32][:500], "u64le": cands[64][:500],
                                        "u32le_count": len(cands[32]), "u64le_count": len(cands[64]),
                                        "note": "Heuristic candidates only; validate against relocations/sections before treating as pointers."}
        did = True
    if getattr(args, "stride_scan", False):
        # Detect a repeated record stride via byte-equality autocorrelation on a bounded sample.
        sample = data[base:base + min(len(data) - base, 65536)]
        scores = []
        n = len(sample)
        for stride in range(2, min(512, n // 4) + 1):
            matches = 0
            total = 0
            for i in range(0, n - stride, stride):
                total += 1
                if sample[i] == sample[i + stride]:
                    matches += 1
            if total:
                scores.append({"stride": stride, "match_ratio": round(matches / total, 4)})
        scores.sort(key=lambda s: s["match_ratio"], reverse=True)
        result["stride_candidates"] = scores[:12]
        result["stride_note"] = "Higher match_ratio suggests a repeating record period; confirm with a layout decode."
        did = True
    if not did:
        raise RetoolError("no-mode", "Specify --layout, --pointers, and/or --stride-scan.", exit_code=EXIT_USAGE)
    env["result"] = result
    return env
