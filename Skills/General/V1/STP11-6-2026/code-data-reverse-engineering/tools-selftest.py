"""Built-in regression suite and fixture generator.

Generates legal minimal fixtures for every supported format family and exercises
each major capability through the real command handlers, including valid,
invalid, and missing inputs, structured errors, and a recovery path.
"""
from __future__ import annotations

import io
import json
import os
import pickle
import shutil
import sqlite3
import struct
import sys
import tempfile
import types
import zipfile
import zlib
from pathlib import Path

from tools_core import (load_config, envelope, RetoolError, TOOL_VERSION, hash_bytes)
import tools_core as core
import tools_identify as ident
import tools_formats as fmt
import tools_disasm as dis
import tools_data as dat
import tools_code as cod
import tools_network as net
import tools_ops as ops
import tools_case as case


def A(**kw):
    return types.SimpleNamespace(**kw)


# ---------------------------------------------------------------------------
# Fixture builders (legal minimal files)
# ---------------------------------------------------------------------------

def _png_chunk(typ, body):
    return struct.pack(">I", len(body)) + typ + body + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF)


def build_fixtures() -> dict:
    F = {}
    # ELF64 minimal with .text + .shstrtab + symtab
    shstr = b"\x00.text\x00.shstrtab\x00.symtab\x00.strtab\x00"
    text = bytes.fromhex("554889e5b8010000005dc3")  # push rbp; mov rbp,rsp; mov eax,1; pop rbp; ret
    strtab = b"\x00_start\x00"
    sym = struct.pack("<IBBHQQ", 1, 0x12, 0, 1, 0x1000, 0)  # _start, GLOBAL FUNC, shndx 1
    sym += struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)
    shoff = 64
    sections = []
    # null
    sections.append(struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    # .text (after the ELF header + 5 section headers)
    text_off = 64 + 5 * 64
    sections.append(struct.pack("<IIQQQQIIQQ", 1, 1, 6, 0x1000, text_off, len(text), 0, 0, 16, 0))
    # .shstrtab
    shstr_off = text_off + len(text)
    sections.append(struct.pack("<IIQQQQIIQQ", 7, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    # .symtab
    sym_off = shstr_off + len(shstr)
    sections.append(struct.pack("<IIQQQQIIQQ", 17, 2, 0, 0, sym_off, len(sym), 4, 1, 8, 24))
    # .strtab
    str_off = sym_off + len(sym)
    sections.append(struct.pack("<IIQQQQIIQQ", 25, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0))
    eh = struct.pack("<4sBBBBB7xHHIQQQIHHHHHH", b"\x7fELF", 2, 1, 1, 0, 0,
                     2, 0x3E, 1, 0x1000, 0, shoff, 0, 64, 0, 0, 64, 5, 4)
    F["sample.elf"] = eh + b"".join(sections) + text + shstr + sym + strtab
    # PE32+ minimal
    dos = b"MZ" + b"\x00" * 58 + struct.pack("<I", 64)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x22)
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)          # PE32+ magic
    struct.pack_into("<I", opt, 4, 0x100)          # SizeOfCode
    struct.pack_into("<I", opt, 16, 0x1000)        # AddressOfEntryPoint
    struct.pack_into("<I", opt, 20, 0x1000)        # BaseOfCode
    struct.pack_into("<Q", opt, 24, 0x140000000)   # ImageBase
    struct.pack_into("<I", opt, 32, 0x1000)        # SectionAlignment
    struct.pack_into("<I", opt, 36, 0x200)         # FileAlignment
    struct.pack_into("<H", opt, 48, 6)             # MajorSubsystemVersion
    struct.pack_into("<I", opt, 56, 0x2000)        # SizeOfImage
    struct.pack_into("<I", opt, 60, 0x200)         # SizeOfHeaders
    struct.pack_into("<H", opt, 68, 3)             # Subsystem: console
    struct.pack_into("<I", opt, 108, 16)           # NumberOfRvaAndSizes
    opt = bytes(opt)
    sec = struct.pack("<8sIIIIIIHHI", b".text", 0x100, 0x1000, 0x100, 0x200, 0, 0, 0, 0, 0x60000020)
    pe = dos + b"PE\x00\x00" + coff + opt + sec + b"\x00" * 0x100
    F["sample.exe"] = pe
    # Mach-O 64
    seg = struct.pack("<II16sQQQQiiII", 0x19, 72, b"__TEXT", 0x1000, 0x1000, 0, 0x1000, 7, 5, 0, 0)
    mh = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 2, 1, len(seg), 0, 0)
    F["sample.macho"] = mh + seg
    # JVM class (minimal valid)
    cp = [None]
    def utf8(s):
        cp.append({"t": 1, "v": s}); return len(cp) - 1
    def cls(n):
        cp.append({"t": 7, "v": n}); return len(cp) - 1
    name_i = utf8("Test"); this_i = cls(name_i); obj_i = utf8("java/lang/Object"); super_i = cls(obj_i)
    pool = b""
    for e in cp[1:]:
        if e["t"] == 1:
            b = e["v"].encode(); pool += b"\x01" + struct.pack(">H", len(b)) + b
        elif e["t"] == 7:
            pool += b"\x07" + struct.pack(">H", e["v"])
    klass = b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 52, len(cp)) + pool
    klass += struct.pack(">HHH", 0x21, this_i, super_i) + struct.pack(">H", 0)  # interfaces
    klass += struct.pack(">H", 0) + struct.pack(">H", 0) + struct.pack(">H", 0)  # fields, methods, attrs
    F["Test.class"] = klass
    # DEX minimal
    strings = [b"Ljava/lang/Object;", b"LTest;"]
    str_data = b""
    str_offs = []
    for s in strings:
        str_offs.append(112 + 8 + 8 + len(str_data))
        str_data += bytes([len(s)]) + s + b"\x00"
    string_ids = b"".join(struct.pack("<I", o) for o in str_offs)
    type_ids = struct.pack("<II", 0, 1)
    class_def = struct.pack("<IIIIIIII", 1, 1, 0, 0, 0, 0xFFFFFFFF, 0, 0)
    data_off = 112 + len(string_ids) + len(type_ids) + len(class_def)
    dex = b"dex\n035\x00" + struct.pack("<I", 0) + b"\x00" * 20
    dex += struct.pack("<IIII", data_off + len(str_data), 112, 0x12345678, 0)
    dex += struct.pack("<II", 0, 0)  # link
    dex += struct.pack("<I", 0)  # map_off
    dex += struct.pack("<II", 2, 112)  # string ids
    dex += struct.pack("<II", 2, 112 + len(string_ids))  # type ids
    dex += struct.pack("<II", 0, 0)  # proto
    dex += struct.pack("<II", 0, 0)  # field
    dex += struct.pack("<II", 0, 0)  # method
    dex += struct.pack("<II", 1, 112 + len(string_ids) + len(type_ids))  # class defs
    dex += struct.pack("<II", len(str_data), data_off)  # data
    dex = dex.ljust(112, b"\x00")
    dex += string_ids + type_ids + class_def + str_data
    F["classes.dex"] = dex
    # WASM
    wasm = b"\x00asm\x01\x00\x00\x00"
    wasm += b"\x01\x05\x01\x60\x00\x01\x7f"  # type section: 1 func () -> i32
    wasm += b"\x07\x05\x01\x01\x66\x00\x00"  # export section: "f" func 0
    F["sample.wasm"] = wasm
    # ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hello.txt", "Hello, world!\n")
        z.writestr("dir/data.json", '{"a": 1, "b": [1,2,3]}')
    F["sample.zip"] = buf.getvalue()
    # TAR
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("readme.txt")
        content = b"tar fixture\n"
        info.size = len(content)
        t.addfile(info, io.BytesIO(content))
    F["sample.tar"] = buf.getvalue()
    # GZIP
    F["sample.gz"] = zlib.compress(b"gzip fixture content\n" * 10, 6)  # zlib, not gzip; fix below
    import gzip as _gz
    F["sample.gz"] = _gz.compress(b"gzip fixture content\n" * 10)
    # PNG
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))
    idat = zlib.compress(raw)
    F["sample.png"] = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    # JPEG
    F["sample.jpg"] = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                       b"\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00"
                       b"\xff\xd9")
    # GIF
    F["sample.gif"] = b"GIF89a" + struct.pack("<HH", 2, 2) + b"\x80\x00\x00" + b"\x00\x00\x00\xff\xff\xff" + b"\x3b"
    # RIFF WAVE
    fmt_chunk = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    data = b"\x00\x00" * 100
    riff = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk + b"data" + struct.pack("<I", len(data)) + data
    F["sample.wav"] = b"RIFF" + struct.pack("<I", len(riff)) + riff
    # MP4
    ftyp = b"ftyp" + b"isom" + struct.pack(">I", 0) + b"isom"
    ftyp = struct.pack(">I", len(ftyp) + 4) + ftyp
    mvhd = b"mvhd" + b"\x00" * 4 + struct.pack(">II", 1000, 5000) + b"\x00" * 80
    mvhd = struct.pack(">I", len(mvhd) + 4) + mvhd
    moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
    F["sample.mp4"] = ftyp + moov
    # PDF
    F["sample.pdf"] = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                       b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
                       b"3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n"
                       b"xref\n0 4\n0000000000 65535 f \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF")
    # DTB
    strings = b"compatible\x00model\x00"
    prop_val = b"test,board\x00"
    struct_blob = struct.pack(">I", 1) + b"board\x00\x00\x00"
    struct_blob += struct.pack(">II", 3, len(prop_val)) + struct.pack(">I", 0) + prop_val
    struct_blob += struct.pack(">I", 2) + struct.pack(">I", 9)
    off_struct = 40
    off_strings = off_struct + len(struct_blob)
    hdr = struct.pack(">10I", 0xD00DFEED, off_strings + len(strings), off_struct, off_strings, 40, 17, 16, 0, len(strings), len(struct_blob))
    F["sample.dtb"] = hdr + struct_blob + strings
    # MBR
    mbr = bytearray(512)
    mbr[446] = 0x80; mbr[450] = 0x83
    struct.pack_into("<II", mbr, 454, 2048, 10000)
    mbr[510:512] = b"\x55\xaa"
    F["sample.mbr"] = bytes(mbr)
    # Android boot image
    boot = b"ANDROID!" + struct.pack("<8I", 0x1000, 0x10000000, 0x2000, 0x11000000, 0, 0x12000000, 0x10000000, 4096)
    boot += struct.pack("<II", 0, (11 << 25) | (0 << 18) | (0 << 11) | (2021 - 2000) << 4 | 1)
    boot += b"\x00" * (1648 - len(boot))
    F["boot.img"] = boot
    # SquashFS
    sq = b"hsqs" + struct.pack("<IIIIHHHHHH", 10, 0, 4096, 0, 4, 12, 0, 1, 4, 0)
    sq += struct.pack("<QQQQQQQ", 0, 96, 0, 0, 0, 0, 0)
    F["sample.squashfs"] = sq.ljust(96, b"\x00")
    # NES ROM
    nes = b"NES\x1a" + bytes([2, 1, 0x01, 0x00, 0, 0, 0, 0, 0, 0, 0, 0])
    prg = b"\x00" * (2 * 16384)
    prg = prg[:-6] + struct.pack("<HHH", 0x8000, 0x8000, 0x8000)
    F["sample.nes"] = nes + prg + b"\x00" * (1 * 8192)
    # QCOW2
    qcow = b"QFI\xfb" + struct.pack(">IQIIQIIQQIIQ", 2, 0, 0, 16, 1 << 30, 0, 0, 0, 0, 0, 0, 0)
    F["sample.qcow2"] = qcow.ljust(72, b"\x00")
    # AXML (string pool + one element)
    strs = ["manifest", "package", "com.example"]
    sp = b""
    offs = []
    for s in strs:
        offs.append(len(sp))
        b = s.encode("utf-16le")
        sp += struct.pack("<H", len(s)) + b + b"\x00\x00"
    pool = struct.pack("<HHIIIIII", 0x0001, 28, 28 + len(strs) * 4 + len(sp), len(strs), 0, 0, 28 + len(strs) * 4, 0)
    pool += b"".join(struct.pack("<I", o) for o in offs) + sp
    elem = struct.pack("<HHIIIII", 0x0102, 16, 36, 1, 0xFFFFFFFF, 0, 0)
    elem += struct.pack("<IIHHH", 0xFFFFFFFF, 0, 0, 0, 0)  # ns,name,attrStart,attrSize,attrCount... simplified
    axml = struct.pack("<HHI", 0x0003, 8, 8 + len(pool) + len(elem)) + pool + elem
    F["AndroidManifest.xml"] = axml
    # GGUF
    gguf = b"GGUF" + struct.pack("<IQQ", 3, 1, 1)
    key = b"general.architecture"
    gguf += struct.pack("<Q", len(key)) + key + struct.pack("<I", 8) + struct.pack("<Q", len(b"llama")) + b"llama"
    tname = b"token_embd.weight"
    gguf += struct.pack("<Q", len(tname)) + tname + struct.pack("<I", 2) + struct.pack("<QQ", 4, 4) + struct.pack("<IQ", 0, 0)
    F["model.gguf"] = gguf
    # safetensors
    header = {"weight": {"dtype": "F32", "shape": [2, 2], "data_offsets": [0, 16]}, "__metadata__": {"format": "pt"}}
    hb = json.dumps(header).encode()
    F["model.safetensors"] = struct.pack("<Q", len(hb)) + hb + struct.pack("<4f", 1, 2, 3, 4)
    # pickle
    F["data.pkl"] = pickle.dumps({"a": 1, "b": [1, 2, 3]}, protocol=4)
    # SPIR-V
    spv = struct.pack("<5I", 0x07230203, 0x00010000, 0, 10, 0)
    spv += struct.pack("<I", (2 << 16) | 17) + struct.pack("<I", 1)  # OpCapability Shader
    spv += struct.pack("<I", (4 << 16) | 15) + struct.pack("<II", 1, 1) + b"main\x00\x00\x00\x00"  # OpEntryPoint
    F["shader.spv"] = spv
    # source map
    F["app.js.map"] = json.dumps({"version": 3, "file": "app.js", "sources": ["a.ts", "b.ts"],
                                  "names": ["x"], "mappings": "AAAA,SAASA,CAAC"}).encode()
    # WAD
    wad = b"IWAD" + struct.pack("<II", 1, 12 + 16)
    wad += struct.pack("<II", 12, 0) + b"DEMO\x00\x00\x00\x00"
    F["game.wad"] = wad + b"\x00" * 16
    # JSON / protobuf / msgpack / cbor / bson / der
    F["data.json"] = b'{"name": "test", "items": [1, 2, 3], "nested": {"ok": true}}'
    F["data.pb"] = bytes.fromhex("08960112026869")
    F["data.msgpack"] = bytes.fromhex("82a16101a162920102")
    F["data.cbor"] = bytes.fromhex("a1616101")
    F["data.bson"] = struct.pack("<i", 12) + b"\x10a\x00" + struct.pack("<i", 1) + b"\x00"
    F["cert.der"] = bytes.fromhex("300e02010506092a864886f70d01010b")
    # SQLite
    tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmpdb.close()
    con = sqlite3.connect(tmpdb.name)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO t (name) VALUES (?)", [("a",), ("b",), ("c",)])
    con.commit(); con.close()
    F["data.sqlite"] = Path(tmpdb.name).read_bytes()
    os.unlink(tmpdb.name)
    # PCAP with DNS query
    dns = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    dns += b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    udp = struct.pack(">HHHH", 12345, 53, 8 + len(dns), 0) + dns
    ip = struct.pack(">BBHHHBBH", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0) + bytes([10, 0, 0, 1]) + bytes([10, 0, 0, 2])
    ip = ip[:10] + b"\x00\x00" + ip[12:]  # checksum placeholder
    eth = bytes.fromhex("00112233445566778899aabb") + struct.pack(">H", 0x0800)
    pkt = eth + ip + udp
    pcap = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    pcap += struct.pack("<IIII", 1700000000, 0, len(pkt), len(pkt)) + pkt
    F["capture.pcap"] = pcap
    # HTTP
    F["request.http"] = b"GET /api/v1/users?id=42 HTTP/1.1\r\nHost: example.com\r\nAccept: application/json\r\n\r\n"
    # DNS message
    F["query.dns"] = dns
    # WebSocket text frame "Hello"
    F["frame.ws"] = bytes([0x81, 0x05]) + b"Hello"
    # EVM bytecode
    F["contract.evm"] = bytes.fromhex("608060405260043610156100105760003560e01c")
    # source / build fixtures
    F["app.py"] = b"import os\n\ndef add(a, b):\n    return a + b\n\ndef danger(x):\n    return eval(x)\n"
    F["Dockerfile"] = b"FROM ubuntu:latest\nRUN apt-get install -y curl\nUSER root\nENV SECRET_KEY=abc123456789\n"
    F["Makefile"] = b"CC=gcc\nall: main\nmain: main.o\n\t$(CC) -o main main.o\nclean:\n\trm -f main\n.PHONY: all clean\n"
    F["script.sh"] = b"#!/bin/bash\nrm -rf $DIR\neval \"$USER_INPUT\"\ncurl \"$URL\" | sh\n"
    F["ci.yml"] = b"on: pull_request_target\npermissions: write-all\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@main\n"
    F["bad.json"] = b'{"a": 1, "b": [1,2,}'
    F["empty.bin"] = b""
    return F


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_selftest(verbose=False) -> dict:
    cfg = load_config(None)
    fixtures = build_fixtures()
    tmp = Path(tempfile.mkdtemp(prefix="retool-selftest-"))
    results = []
    passed = failed = 0

    def check(name, fn):
        nonlocal passed, failed
        try:
            fn()
            results.append({"test": name, "ok": True})
            passed += 1
        except AssertionError as e:
            results.append({"test": name, "ok": False, "error": str(e)})
            failed += 1
        except Exception as e:
            results.append({"test": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            failed += 1

    # write fixtures
    fdir = tmp / "fixtures"
    fdir.mkdir()
    for name, data in fixtures.items():
        (fdir / name).write_bytes(data)

    def run(cmd_fn, args, command="test"):
        env = envelope(command, cfg)
        return cmd_fn(args, env, cfg)

    # --- identification / triage ---
    def t_identify():
        r = run(ident.cmd_identify, A(paths=[str(fdir / "sample.elf")]))
        assert r["result"]["files"][0]["best_match"]["family"] == "executable"
        r = run(ident.cmd_identify, A(paths=[str(fdir / "sample.png")]))
        assert r["result"]["files"][0]["best_match"]["name"] == "PNG image"
    check("identify-elf-png", t_identify)

    def t_mismatch():
        p = fdir / "fake.png"
        p.write_bytes(fixtures["sample.elf"])
        r = run(ident.cmd_identify, A(paths=[str(p)]))
        assert r["result"]["files"][0]["extension_mismatch"] is not None
    check("identify-extension-mismatch", t_mismatch)

    def t_entropy():
        r = run(ident.cmd_entropy, A(path=str(fdir / "sample.zip"), window="256", stride=None))
        assert r["result"]["overall_entropy"] > 0
        assert "interpretation" in r["result"]
    check("entropy-map", t_entropy)

    def t_strings():
        r = run(ident.cmd_strings, A(path=str(fdir / "sample.elf"), min="4", limit="100", encoding="all"))
        assert r["result"]["count"] >= 1
    check("strings-extraction", t_strings)

    def t_hexdump():
        r = run(ident.cmd_hexdump, A(path=str(fdir / "sample.png"), offset="0", length="64", width="16", layout="png-ihdr"))
        assert "89 50 4e 47" in r["result"]["hexdump"]
        assert any(a["field"] == "width" for a in r["result"]["annotations"])
    check("hexdump-annotated", t_hexdump)

    def t_encoding():
        r = run(ident.cmd_encoding, A(path=str(fdir / "app.py"), repair=None, out=None, approve=False))
        assert r["result"]["best"]["encoding"] in ("ascii", "utf-8")
    check("encoding-detect", t_encoding)

    def t_unicode():
        r = run(ident.cmd_unicode, A(path=None, text="pаypal\u202eevil"))
        assert any(f["kind"] == "bidi-control" for f in r["result"]["findings"])
        assert any(f["kind"] == "confusable" for f in r["result"]["findings"])
    check("unicode-security", t_unicode)

    def t_codec():
        r = run(ident.cmd_codec, A(input_file=None, text="48656c6c6f", mode="hex", encode=None, layers=None, out=None, approve=False))
        assert r["result"]["output_text_preview"] == "Hello"
        r = run(ident.cmd_codec, A(input_file=None, text="Hello", mode=None, encode="base64", layers=None, out=None, approve=False))
        assert r["result"]["encoded"] == "SGVsbG8="
    check("codec-hex-base64", t_codec)

    def t_numbers():
        p = fdir / "nums.bin"
        p.write_bytes(struct.pack("<If", 0x12345678, 3.5))
        r = run(ident.cmd_numbers, A(path=str(p), offset="0", types="u32le", varint=None, spec=None, count="1"))
        assert r["result"]["decodings"][0]["values"][0] == 0x12345678
        r = run(ident.cmd_numbers, A(path=str(p), offset="4", types="f32le", varint=None, spec=None, count="1"))
        assert abs(r["result"]["decodings"][0]["values"][0] - 3.5) < 1e-6
        p2 = fdir / "varint.bin"
        p2.write_bytes(bytes.fromhex("9601"))
        r = run(ident.cmd_numbers, A(path=str(p2), offset="0", types=None, varint="uleb128", spec=None, count="1"))
        assert r["result"]["varints"][0]["value"] == 150
    check("numbers-varint", t_numbers)

    def t_xor():
        plain = b"the quick brown fox jumps over the lazy dog " * 4
        key = b"\x5a"
        enc = bytes(b ^ key[0] for b in plain)
        p = fdir / "xored.bin"; p.write_bytes(enc)
        r = run(ident.cmd_xor, A(path=str(p), text=None, key=None, max_keylen="4", top="3", out=None, approve=False))
        assert r["result"]["candidates"][0]["key_hex"] == "5a"
    check("xor-key-recovery", t_xor)

    def t_carve():
        blob = b"JUNK" * 100 + fixtures["sample.png"] + b"TAIL" * 50
        p = fdir / "carve.bin"; p.write_bytes(blob)
        r = run(ident.cmd_carve, A(path=str(p), out=None, approve=False))
        assert any(h["kind"] == "png" and h["valid_signature"] for h in r["result"]["hits"])
    check("carve-embedded-png", t_carve)

    # --- formats ---
    def t_parse_elf():
        r = run(fmt.cmd_parse, A(path=str(fdir / "sample.elf"), format="auto"))
        assert r["result"]["format"] == "elf"
        assert r["result"]["header"]["machine"] == "x86-64"
        assert len(r["result"]["sections"]) >= 4
    check("parse-elf", t_parse_elf)

    def t_parse_pe():
        r = run(fmt.cmd_parse, A(path=str(fdir / "sample.exe"), format="auto"))
        assert r["result"]["format"] == "pe"
        assert r["result"]["coff"]["machine"] == "x86-64"
    check("parse-pe", t_parse_pe)

    def t_parse_macho():
        r = run(fmt.cmd_parse, A(path=str(fdir / "sample.macho"), format="auto"))
        assert r["result"]["format"] == "macho"
        assert r["result"]["header"]["cputype"] == "x86-64"
    check("parse-macho", t_parse_macho)

    def t_parse_class():
        r = run(fmt.cmd_parse, A(path=str(fdir / "Test.class"), format="auto"))
        assert r["result"]["format"] == "jvm-class"
        assert r["result"]["this_class"] == "Test"
        assert r["result"]["java_version"] == "8"
    check("parse-jvm-class", t_parse_class)

    def t_parse_dex():
        r = run(fmt.cmd_parse, A(path=str(fdir / "classes.dex"), format="auto"))
        assert r["result"]["format"] == "dex"
        assert r["result"]["header"]["version"] == "035"
    check("parse-dex", t_parse_dex)

    def t_parse_wasm():
        r = run(fmt.cmd_parse, A(path=str(fdir / "sample.wasm"), format="auto"))
        assert r["result"]["format"] == "wasm"
        assert len(r["result"]["exports"]) == 1
    check("parse-wasm", t_parse_wasm)

    def t_parse_media():
        for name, key in [("sample.png", "ihdr"), ("sample.jpg", "sof"), ("sample.gif", "width"),
                          ("sample.wav", "fmt"), ("sample.mp4", "boxes"), ("sample.pdf", "version")]:
            r = run(fmt.cmd_parse, A(path=str(fdir / name), format="auto"))
            assert key in r["result"], f"{name} missing {key}"
    check("parse-media", t_parse_media)

    def t_parse_firmware():
        for name, key in [("sample.dtb", "nodes"), ("sample.mbr", "partitions"), ("boot.img", "components"),
                          ("sample.squashfs", "compression"), ("sample.nes", "mapper"), ("sample.qcow2", "fields")]:
            r = run(fmt.cmd_parse, A(path=str(fdir / name), format="auto"))
            assert key in r["result"], f"{name} missing {key}"
    check("parse-firmware", t_parse_firmware)

    def t_parse_models():
        r = run(fmt.cmd_parse, A(path=str(fdir / "model.gguf"), format="auto"))
        assert r["result"]["metadata"]["general.architecture"] == "llama"
        r = run(fmt.cmd_parse, A(path=str(fdir / "model.safetensors"), format="auto"))
        assert r["result"]["tensor_count"] == 1
        r = run(fmt.cmd_parse, A(path=str(fdir / "data.pkl"), format="auto"))
        assert r["result"]["executed"] is False
        r = run(fmt.cmd_parse, A(path=str(fdir / "shader.spv"), format="auto"))
        assert r["result"]["format"] == "spirv"
    check("parse-models-shaders", t_parse_models)

    def t_sections_symbols():
        r = run(fmt.cmd_sections, A(path=str(fdir / "sample.elf")))
        assert len(r["result"]["sections"]) >= 4
        r = run(fmt.cmd_symbols, A(path=str(fdir / "sample.elf"), demangle=True))
        assert "imports" in r["result"]
    check("sections-symbols", t_sections_symbols)

    def t_structs():
        r = run(fmt.cmd_structs, A(path=str(fdir / "capture.pcap"), layout="pcap-global-header",
                                   offset="0", count="1", pointers=False, stride_scan=False))
        assert r["result"]["record_count"] == 1 and len(r["result"]["records"][0]["fields"]) == 7
        r = run(fmt.cmd_structs, A(path=str(fdir / "sample.elf"), layout=None, offset="0", count="1",
                                   pointers=True, stride_scan=True))
        assert "pointer_candidates" in r["result"] and "stride_candidates" in r["result"]
    check("structs-layout-pointers-stride", t_structs)

    # --- disassembly / CFG ---
    def t_disasm():
        r = run(dis.cmd_disasm, A(path=str(fdir / "sample.elf"), arch="x64", offset=str(64 + 5 * 64),
                                  length="11", base_addr="0x1000", max_insns="20", endian=None))
        mns = [i["mnemonic"] for i in r["result"]["instructions"]]
        assert mns[0] == "push" and "ret" in mns
    check("disasm-x64", t_disasm)

    def t_disasm_archs():
        p = fdir / "code.bin"
        p.write_bytes(bytes.fromhex("c0035fd61f2003d5"))  # ret; nop
        r = run(dis.cmd_disasm, A(path=str(p), arch="arm64", offset="0", length="8", base_addr="0", max_insns="4", endian=None))
        assert r["result"]["instructions"][0]["mnemonic"] == "ret"
        p.write_bytes(bytes.fromhex("1305000067800000"))  # li a0,0; ret
        r = run(dis.cmd_disasm, A(path=str(p), arch="riscv64", offset="0", length="8", base_addr="0", max_insns="4", endian=None))
        assert r["result"]["instructions"][1]["mnemonic"] == "ret"
        p.write_bytes(bytes.fromhex("a9018d002060"))  # lda #1; sta $2000; rts
        r = run(dis.cmd_disasm, A(path=str(p), arch="6502", offset="0", length="6", base_addr="0x8000", max_insns="4", endian=None))
        assert r["result"]["instructions"][0]["mnemonic"] == "lda"
        r = run(dis.cmd_disasm, A(path=str(fdir / "contract.evm"), arch="evm", offset="0", length=None, base_addr="0", max_insns="50", endian=None))
        assert r["result"]["instructions"][0]["mnemonic"] == "PUSH1"
        # RVC compressed encodings (verified against the RISC-V C-extension spec)
        p.write_bytes(bytes.fromhex("0145154506052e858280"))  # c.li a0,0; c.li a0,5; c.slli a0,1; c.mv a0,a1; c.jr ra
        r = run(dis.cmd_disasm, A(path=str(p), arch="riscv64", offset="0", length="10", base_addr="0", max_insns="8", endian=None))
        mns = [i["mnemonic"] for i in r["result"]["instructions"]]
        assert mns == ["c.li", "c.li", "c.slli", "c.mv", "c.jr"], mns
        # Newer EVM opcodes: PUSH0 / TLOAD / TSTORE / MCOPY
        p.write_bytes(bytes.fromhex("5f5c5d5e"))
        r = run(dis.cmd_disasm, A(path=str(p), arch="evm", offset="0", length="4", base_addr="0", max_insns="8", endian=None))
        assert [i["mnemonic"] for i in r["result"]["instructions"]] == ["PUSH0", "TLOAD", "TSTORE", "MCOPY"]
    check("disasm-arm64-riscv-6502-evm", t_disasm_archs)

    def t_cfg():
        r = run(dis.cmd_cfg, A(path=str(fdir / "sample.elf"), arch="x64", offset=str(64 + 5 * 64),
                               length="11", base_addr="0x1000", pseudocode=True, endian=None))
        assert "cfg" in r["result"] and "pseudocode" in r["result"]
        assert r["result"]["calling_convention"]["hypothesis"]
    check("cfg-pseudocode", t_cfg)

    # --- data ---
    def t_data_json():
        r = run(dat.cmd_data, A(path=str(fdir / "data.json"), format="auto"))
        assert r["result"]["valid"] is True
        r = run(dat.cmd_data, A(path=str(fdir / "bad.json"), format="json"))
        assert r["result"]["valid"] is False
    check("data-json-valid-invalid", t_data_json)

    def t_data_serial():
        for name, key in [("data.pb", "fields"), ("data.msgpack", "value"), ("data.cbor", "value"),
                          ("data.bson", "value"), ("cert.der", "tree")]:
            r = run(dat.cmd_data, A(path=str(fdir / name), format="auto"))
            assert key in r["result"], name
    check("data-serializers", t_data_serial)

    def t_data_sqlite():
        r = run(dat.cmd_data, A(path=str(fdir / "data.sqlite"), format="auto"))
        assert r["result"]["integrity_check"] == ["ok"]
        assert r["result"]["table_row_counts"]["t"] == 3
    check("data-sqlite", t_data_sqlite)

    # --- code / build / security ---
    def t_code():
        r = run(cod.cmd_code, A(path=str(fdir / "app.py"), lang="auto", mode="all"))
        assert len(r["result"]["outline"]["functions"]) == 2
        assert any(f["rule"] == "py-eval-exec" for f in r["result"]["security_findings"])
    check("code-outline-security", t_code)

    def t_regex():
        r = run(cod.cmd_regex, A(pattern=r"(a+)+$", test=["aaaa", "aaab"]))
        assert r["result"]["valid"] and r["result"]["redos_risk"] == "high"
    check("regex-redos", t_regex)

    def t_sql():
        r = run(cod.cmd_sql, A(path=None, statement="SELECT * FROM t; DELETE FROM t;"))
        assert r["result"]["statement_count"] == 2
        assert any(x["issue"] == "delete-without-where" for x in r["result"]["review"])
    check("sql-review", t_sql)

    def t_build():
        r = run(cod.cmd_build, A(path=str(fdir / "Dockerfile"), mode="auto", text=None, to=None))
        assert any(f["rule"] == "dk-latest" for f in r["result"]["findings"])
        r = run(cod.cmd_build, A(path=str(fdir / "Makefile"), mode="auto", text=None, to=None))
        assert len(r["result"]["targets"]) >= 2
        r = run(cod.cmd_build, A(path=str(fdir / "ci.yml"), mode="auto", text=None, to=None))
        assert any(f["rule"] == "ci-pull-request-target" for f in r["result"]["findings"])
    check("build-audits", t_build)

    def t_secrets():
        p = fdir / "leak.txt"
        p.write_bytes(b"aws_key=AKIAIOSFODNN7EXAMPLE\npassword='supersecret123'\n")
        r = run(cod.cmd_secrets, A(paths=[str(p)], purpose="defensive"))
        assert r["result"]["finding_count"] >= 1
        assert all("fingerprint" in f for f in r["result"]["findings"])
    check("secrets-redacted", t_secrets)

    def t_secrets_gate():
        try:
            run(cod.cmd_secrets, A(paths=[str(fdir / "app.py")], purpose=None))
            assert False, "should require purpose"
        except RetoolError as e:
            assert e.code == "purpose-required"
    check("secrets-purpose-gate", t_secrets_gate)

    # --- network ---
    def t_network():
        r = run(net.cmd_network, A(path=str(fdir / "capture.pcap"), mode="auto", text=None))
        assert r["result"]["packet_count"] == 1
        assert r["result"]["dns_messages"][0]["questions"][0]["name"] == "example.com"
        r = run(net.cmd_network, A(path=str(fdir / "request.http"), mode="auto", text=None))
        assert r["result"]["method"] == "GET" and r["result"]["path"] == "/api/v1/users"
    check("network-pcap-http", t_network)

    def t_websocket():
        r = run(net.cmd_network, A(path=str(fdir / "frame.ws"), mode="websocket", text=None))
        assert r["result"]["payload_preview"] == "Hello"
    check("websocket-frame", t_websocket)

    # --- ops ---
    def t_archive():
        r = run(ops.cmd_archive, A(path=str(fdir / "sample.zip"), mode="validate", out=None, approve=False))
        assert r["result"]["reopen_ok"] is True
        outdir = tmp / "extracted"
        r = run(ops.cmd_archive, A(path=str(fdir / "sample.zip"), mode="extract", out=str(outdir), approve=False))
        assert r["result"]["extracted"]["count"] == 2
        repack = tmp / "repacked.zip"
        r = run(ops.cmd_archive, A(path=str(fdir / "sample.zip"), mode="repack", out=str(repack), approve=False))
        assert r["result"]["repacked"]["reopen_crc_ok"] is True
    check("archive-validate-extract-repack", t_archive)

    def t_compress():
        r = run(ops.cmd_compress, A(path=str(fdir / "sample.gz"), mode="roundtrip"))
        assert r["result"]["valid"] is True
        assert r["result"]["decompressed_size"] > 0 and "content_sha256" in r["result"]
    check("compress-roundtrip", t_compress)

    def t_hash():
        r = run(ops.cmd_hash, A(paths=[str(fdir / "sample.zip")], algos="sha256,md5", fuzzy=True, crc_recon=None, width=None))
        assert len(r["result"]["files"][0]["sha256"]) == 64
        assert "fuzzy" in r["result"]["files"][0]
    check("hash-fuzzy", t_hash)

    def t_crc_recon():
        data = b"123456789"
        expected = zlib.crc32(data) & 0xFFFFFFFF
        p = fdir / "crc.bin"; p.write_bytes(data)
        r = run(ops.cmd_hash, A(paths=[str(p)], algos="sha256", fuzzy=False, crc_recon=hex(expected), width="32"))
        assert any(m["preset"] == "CRC-32/ISO-HDLC" for m in r["result"]["files"][0]["crc_reconstruction"]["matches"])
    check("crc-reconstruction", t_crc_recon)

    def t_diff():
        a = fdir / "a.bin"; b = fdir / "b.bin"
        a.write_bytes(b"hello world"); b.write_bytes(b"hello WORLD")
        r = run(ops.cmd_diff, A(a=str(a), b=str(b), mode="bytes"))
        assert r["result"]["identical"] is False
        assert r["result"]["differing_region_count"] >= 1
        r = run(ops.cmd_diff, A(a=str(fdir / "data.json"), b=str(fdir / "data.json"), mode="json"))
        assert r["result"]["json_diff"] == []
    check("diff-bytes-json", t_diff)

    def t_repair():
        r = run(ops.cmd_repair, A(path=str(fdir / "bad.json"), mode="json", out=None, approve=False))
        assert r["result"]["dry_run"] is True
        # recovery path: write repaired copy and reopen
        fixed = tmp / "fixed.json"
        r = run(ops.cmd_repair, A(path=str(fdir / "bad.json"), mode="json", out=str(fixed), approve=True))
        assert json.loads(fixed.read_bytes())["a"] == 1
    check("repair-json-recovery", t_repair)

    def t_patch():
        target = fdir / "patchme.bin"
        target.write_bytes(b"AAAA\x01\x00\x00\x00BBBB")
        spec = fdir / "patch.json"
        spec.write_text(json.dumps({"target_sha256": hash_bytes(target.read_bytes())["sha256"],
                                    "patches": [{"offset": 4, "expected_hex": "01000000", "new_hex": "02000000"}]}))
        r = run(ops.cmd_patch, A(path=str(target), spec=str(spec), apply=False, out=None, approve=False))
        assert r["result"]["applied"] is False
        out = tmp / "patched.bin"
        r = run(ops.cmd_patch, A(path=str(target), spec=str(spec), apply=True, out=str(out), approve=True))
        assert out.read_bytes()[4] == 2
        assert target.read_bytes()[4] == 1  # original unchanged
    check("patch-reversible", t_patch)

    def t_convert():
        out = tmp / "converted.json"
        r = run(ops.cmd_convert, A(path=str(fdir / "data.msgpack"), to="json", out=str(out), approve=False))
        assert json.loads(out.read_bytes())["a"] == 1
    check("convert-msgpack-json", t_convert)

    def t_fuzz():
        outdir = tmp / "fuzz"
        r = run(ops.cmd_fuzz, A(path=str(fdir / "sample.zip"), out=str(outdir), count="8", seed="42",
                                differential=True, run_builtins=False))
        assert r["result"]["case_count"] == 8
        assert all("parser_results" in c for c in r["result"]["cases"])
    check("fuzz-differential", t_fuzz)

    def t_spec():
        r = run(ops.cmd_spec, A(layout="pcap-global-header", out=None, emit_parser=None, approve=False))
        assert r["result"]["field_count"] == 7
        parser_path = tmp / "gen_parser.py"
        r = run(ops.cmd_spec, A(layout="pcap-global-header", out=None, emit_parser=str(parser_path), approve=False))
        import subprocess
        proc = subprocess.run([sys.executable, str(parser_path), "--self-test"], capture_output=True, text=True)
        assert proc.returncode == 0 and "self-test OK" in proc.stdout
    check("spec-emit-parser", t_spec)

    def t_external():
        r = run(ops.cmd_external, A(list=True, tool=None, run=None, check=False))
        assert "tools" in r["result"]
        # dynamic tool must be blocked
        r = run(ops.cmd_external, A(list=False, tool="gdb", run=None, check=False))
        if r["result"]["available"]:
            assert r["result"].get("execution") == "blocked"
    check("external-adapter", t_external)

    # --- case workflow ---
    def t_case_workflow():
        cdir = tmp / "case"
        r = run(case.cmd_intake, A(paths=[str(fdir / "sample.elf"), str(fdir / "sample.png")], case=str(cdir),
                                   note="selftest", source="selftest"))
        assert r["result"]["artifacts_added"] == 2
        r = run(case.cmd_authorize, A(case=str(cdir), purpose="research", statement="Self-test artifacts generated locally for validation.", actor="selftest"))
        assert r["result"]["authorization"]["purpose"] == "research"
        r = run(case.cmd_ledger, A(case=str(cdir), add=["fact", "ELF is x86-64"], ref=None))
        assert r["result"]["appended"]["kind"] == "fact"
        r = run(case.cmd_survey, A(path=str(fdir / "sample.elf"), case=str(cdir)))
        assert r["result"]["format"] == "elf"
        r = run(case.cmd_plan, A(template="static-survey", target=str(fdir / "sample.elf"), outdir=str(tmp), case=str(cdir), out=None, approve=False))
        assert r["result"]["template"] == "static-survey"
        r = run(case.cmd_report, A(case=str(cdir), out=None, approve=False))
        assert "Analysis Report" in r["result"]["markdown"]
        r = run(case.cmd_verify, A(case=str(cdir)))
        assert r["result"]["all_ok"] is True
        pkg = tmp / "preservation"
        r = run(case.cmd_preserve, A(case=str(cdir), out=str(pkg), zip=True, approve=False))
        assert r["result"]["file_count"] > 0
        # clean-state rerun: verify again from the preserved copy
        r = run(case.cmd_verify, A(case=str(pkg / "case")))
        assert r["result"]["all_ok"] is True
    check("case-workflow-preserve-verify", t_case_workflow)

    def t_timeline():
        cdir = tmp / "case"
        r = run(case.cmd_timeline, A(case=str(cdir), logs=[str(fdir / "request.http")], pcap=[str(fdir / "capture.pcap")]))
        assert r["result"]["event_count"] >= 2
    check("timeline-correlation", t_timeline)

    def t_compat():
        r = run(case.cmd_compat, A())
        assert r["result"]["platform"]["python"]
        assert "elf" in r["result"]["builtin_parsers"]
    check("compat-matrix", t_compat)

    # --- error handling ---
    def t_missing_input():
        try:
            run(ident.cmd_identify, A(paths=[str(fdir / "does-not-exist.bin")]))
            assert False
        except RetoolError as e:
            assert e.code == "input-missing"
    check("error-missing-input", t_missing_input)

    def t_invalid_input():
        try:
            run(fmt.cmd_parse, A(path=str(fdir / "empty.bin"), format="elf"))
            assert False
        except RetoolError as e:
            assert e.code in ("truncated-structure", "not-elf")
    check("error-invalid-input", t_invalid_input)

    def t_input_changes_output():
        r1 = run(ident.cmd_identify, A(paths=[str(fdir / "sample.png")]))
        r2 = run(ident.cmd_identify, A(paths=[str(fdir / "sample.gif")]))
        assert r1["result"]["files"][0]["best_match"]["name"] != r2["result"]["files"][0]["best_match"]["name"]
    check("input-changes-output", t_input_changes_output)

    # cleanup
    shutil.rmtree(tmp, ignore_errors=True)
    return {"tool_version": TOOL_VERSION, "passed": passed, "failed": failed,
            "total": passed + failed, "results": results,
            "all_passed": failed == 0}


def cmd_selftest(args, env, cfg):
    report = run_selftest(verbose=getattr(args, "verbose", False))
    env["result"] = report
    if not report["all_passed"]:
        env["errors"].append({"code": "selftest-failed", "message": f"{report['failed']} test(s) failed"})
    return env


def cmd_fixtures(args, env, cfg):
    fixtures = build_fixtures()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in fixtures.items():
        p = outdir / name
        if p.exists() and not getattr(args, "approve", False):
            raise RetoolError("approval-required", f"Fixture exists: {p}; pass --approve to overwrite.", exit_code=3)
        p.write_bytes(data)
        written.append({"name": name, "bytes": len(data), "sha256": hash_bytes(data)["sha256"]})
    env["result"] = {"outdir": str(outdir), "count": len(written), "fixtures": written,
                     "note": "Legal minimal fixtures for regression testing. Regenerate deterministically with this command."}
    return env
