"""Machine-code decoders and control-flow reconstruction.

Built-in decoders cover documented common subsets and never guess unknown
opcodes: they are emitted as raw bytes with kind 'unknown'. CFG, pseudocode,
value tracking, calling-convention and stack-frame heuristics operate on the
decoded stream. All analysis is static and read-only.
"""
from __future__ import annotations

import struct
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, read_bytes, validate_input_file, parse_int,
                        u16, u32, u64)
from tools_formats import parse_elf, parse_pe, parse_macho, detect_format

ARCHS = ["x86", "x64", "arm64", "riscv32", "riscv64", "mips32", "6502", "evm"]

# ---------------------------------------------------------------------------
# x86 / x86-64
# ---------------------------------------------------------------------------

R8 = ["al", "cl", "dl", "bl", "spl", "bpl", "sil", "dil", "r8b", "r9b", "r10b", "r11b", "r12b", "r13b", "r14b", "r15b"]
R16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di", "r8w", "r9w", "r10w", "r11w", "r12w", "r13w", "r14w", "r15w"]
R32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi", "r8d", "r9d", "r10d", "r11d", "r12d", "r13d", "r14d", "r15d"]
R64 = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]
CC = ["o", "no", "b", "ae", "e", "ne", "be", "a", "s", "ns", "p", "np", "l", "ge", "le", "g"]
GRP1 = ["add", "or", "adc", "sbb", "and", "sub", "xor", "cmp"]
GRP2 = ["rol", "ror", "rcl", "rcr", "shl", "shr", "sal", "sar"]
GRP3 = ["test", "test", "not", "neg", "mul", "imul", "div", "idiv"]
GRP5 = ["inc", "dec", "call", "callf", "jmp", "jmpf", "push", "?"]


class _Rd:
    def __init__(self, data, pos, base, bits):
        self.d = data
        self.p = pos
        self.base = base
        self.bits = bits
        self.err = None

    def take(self, n):
        if self.p + n > len(self.d):
            self.err = "truncated"
            return b""
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u(self, n, signed=False):
        b = self.take(n)
        if len(b) < n:
            return 0
        return int.from_bytes(b, "little", signed=signed)


def _reg(idx, size, rex=0):
    if size == 8:
        return R8[idx] if rex else ["al", "cl", "dl", "bl", "ah", "ch", "dh", "bh"][idx] if idx < 8 else R8[idx]
    if size == 16:
        return R16[idx]
    if size == 32:
        return R32[idx]
    return R64[idx]


def _modrm(rd: _Rd, opsize, rex, addrsize=32):
    """Return (reg_field, rm_string, extra_target_or_None)."""
    b = rd.u(1)
    if rd.err:
        return 0, "?", None
    mod, reg, rm = (b >> 6) & 3, (b >> 3) & 7, b & 7
    reg += ((rex >> 2) & 1) * 8
    rex_b = (rex & 1) * 8
    rex_x = ((rex >> 1) & 1) * 8
    target = None
    if mod == 3:
        return reg, _reg(rm + rex_b, opsize, rex), None
    parts = []
    base_reg = None
    if rm == 4:  # SIB
        sib = rd.u(1)
        scale = 1 << ((sib >> 6) & 3)
        index = ((sib >> 3) & 7) + rex_x
        base = (sib & 7) + rex_b
        if index != 4:
            parts.append(f"{_reg(index, addrsize)}*{scale}")
        if mod == 0 and (sib & 7) == 5:
            disp = rd.u(4, signed=True)
            parts.append(hex(disp & 0xFFFFFFFF))
        else:
            parts.append(_reg(base, addrsize))
    elif mod == 0 and rm == 5:
        disp = rd.u(4, signed=True)
        if rd.bits == 64:
            target = rd.base + rd.p + disp  # RIP-relative; p already advanced past disp
            parts.append(f"rip+{disp:#x}")
        else:
            parts.append(hex(disp & 0xFFFFFFFF))
    else:
        parts.append(_reg(rm + rex_b, addrsize))
    if mod == 1:
        d8 = rd.u(1, signed=True)
        parts.append(f"{d8:+#x}".replace("+-", "-"))
    elif mod == 2:
        d32 = rd.u(4, signed=True)
        parts.append(f"{d32:+#x}".replace("+-", "-"))
    size_prefix = {8: "byte", 16: "word", 32: "dword", 64: "qword"}.get(opsize, "")
    expr = ""
    for part in parts:
        if not expr:
            expr = part
        elif part.startswith(("+", "-")):
            expr += part
        else:
            expr += "+" + part
    return reg, f"{size_prefix} ptr [{expr}]", target


def decode_x86(data: bytes, pos: int, base: int, bits: int) -> dict:
    start = pos
    rd = _Rd(data, pos, base, bits)
    rex = 0
    opsize = 32
    addrsize = bits
    rep = ""
    # prefixes
    while True:
        b = rd.u(1)
        if rd.err:
            break
        if b == 0x66:
            opsize = 16
        elif b == 0x67:
            addrsize = 32 if bits == 64 else 16
        elif b in (0xF2, 0xF3):
            rep = "repne " if b == 0xF2 else "rep "
        elif b in (0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65, 0xF0):
            pass
        elif bits == 64 and 0x40 <= b <= 0x4F:
            rex = b & 0xF
        else:
            rd.p -= 1
            break
    if rex & 8:
        opsize = 64
    op = rd.u(1)
    if rd.err:
        return {"address": base, "bytes": data[start:start + 1].hex(), "mnemonic": "db", "operands": "",
                "size": max(1, rd.p - start), "kind": "unknown", "semantics": "truncated instruction"}
    mn, ops, kind, target, sem = "?", "", "normal", None, ""
    two = False
    if op == 0x0F:
        two = True
        op = rd.u(1)
    if not two:
        if op < 0x40 and (op & 7) < 6 and op not in (0x0F,):
            grp = GRP1[op >> 3]
            sub = op & 7
            if sub < 2:
                sz = 8 if sub == 0 else opsize
                reg, rm, _ = _modrm(rd, sz, rex, addrsize)
                mn, ops = grp, f"{rm}, {_reg(reg, sz, rex)}"   # dest=r/m, src=reg
            elif sub == 2:
                reg, rm, _ = _modrm(rd, 8, rex, addrsize)
                mn, ops = grp, f"{_reg(reg, 8, rex)}, {rm}"    # dest=reg, src=r/m
            elif sub == 3:
                reg, rm, _ = _modrm(rd, opsize, rex, addrsize)
                mn, ops = grp, f"{_reg(reg, opsize, rex)}, {rm}"  # dest=reg, src=r/m
            elif sub == 4:
                mn, ops = grp, f"al, {rd.u(1):#x}"
            else:
                imm = rd.u(opsize // 8 if opsize >= 32 else 2, signed=True)
                mn, ops = grp, f"{_reg(0, opsize, rex)}, {imm:#x}"
            sem = f"{grp} operation; updates flags"
        elif 0x50 <= op <= 0x57:
            r = (op - 0x50) + ((rex & 1) * 8)
            mn, ops = "push", _reg(r, bits, rex)
            sem = "push register onto stack"
        elif 0x58 <= op <= 0x5F:
            r = (op - 0x58) + ((rex & 1) * 8)
            mn, ops = "pop", _reg(r, bits, rex)
            sem = "pop from stack into register"
        elif op == 0x68:
            v = rd.u(4 if opsize >= 32 else 2, signed=True)
            mn, ops = "push", hex(v & 0xFFFFFFFF)
        elif op == 0x6A:
            v = rd.u(1, signed=True)
            mn, ops = "push", hex(v & 0xFF)
        elif 0x70 <= op <= 0x7F:
            rel = rd.u(1, signed=True)
            target = rd.base + rd.p + rel
            mn, ops, kind = "j" + CC[op - 0x70], hex(target), "cond"
            sem = "conditional branch on flags"
        elif op in (0x80, 0x81, 0x83):
            sz = 8 if op == 0x80 else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            imm = rd.u(1 if op in (0x80, 0x83) else (opsize // 8 if opsize >= 32 else 2), signed=True)
            mn, ops = GRP1[reg & 7], f"{rm}, {imm:#x}"
            sem = "arithmetic/logic with immediate; updates flags"
        elif op in (0x84, 0x85):
            sz = 8 if op == 0x84 else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            mn, ops = "test", f"{rm}, {_reg(reg, sz, rex)}"
            sem = "bitwise AND for flags only"
        elif op in (0x88, 0x89, 0x8A, 0x8B):
            sz = 8 if op in (0x88, 0x8A) else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            if op in (0x88, 0x89):
                mn, ops = "mov", f"{rm}, {_reg(reg, sz, rex)}"
            else:
                mn, ops = "mov", f"{_reg(reg, sz, rex)}, {rm}"
            sem = "data move"
        elif op == 0x8D:
            reg, rm, tgt = _modrm(rd, opsize, rex, addrsize)
            mn, ops = "lea", f"{_reg(reg, opsize, rex)}, {rm}"
            target = tgt
            sem = "load effective address"
        elif op == 0x90:
            mn, ops = "nop", ""
        elif 0x91 <= op <= 0x97:
            mn, ops = "xchg", f"{_reg(0, opsize, rex)}, {_reg(op - 0x90 + ((rex & 1) * 8), opsize, rex)}"
        elif op == 0x98:
            mn = "cdqe" if opsize == 64 else "cwde"
        elif op == 0x99:
            mn = "cqo" if opsize == 64 else "cdq"
        elif op == 0x9C:
            mn = "pushf"
        elif op == 0x9D:
            mn = "popf"
        elif op in (0xA8, 0xA9):
            sz = 8 if op == 0xA8 else opsize
            v = rd.u(1 if sz == 8 else (opsize // 8 if opsize >= 32 else 2))
            mn, ops = "test", f"{_reg(0, sz, rex)}, {v:#x}"
        elif 0xB0 <= op <= 0xB7:
            mn, ops = "mov", f"{_reg(op - 0xB0 + ((rex & 1) * 8), 8, rex)}, {rd.u(1):#x}"
            sem = "load 8-bit immediate"
        elif 0xB8 <= op <= 0xBF:
            n = opsize // 8
            v = rd.u(n)
            mn, ops = "mov", f"{_reg(op - 0xB8 + ((rex & 1) * 8), opsize, rex)}, {v:#x}"
            sem = "load immediate into register"
        elif op in (0xC0, 0xC1):
            sz = 8 if op == 0xC0 else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            mn, ops = GRP2[reg & 7], f"{rm}, {rd.u(1):#x}"
        elif op == 0xC2:
            mn, ops, kind = "ret", hex(rd.u(2)), "ret"
        elif op == 0xC3:
            mn, ops, kind = "ret", "", "ret"
        elif op in (0xC6, 0xC7):
            sz = 8 if op == 0xC6 else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            v = rd.u(1 if sz == 8 else (opsize // 8 if opsize >= 32 else 2))
            mn, ops = "mov", f"{rm}, {v:#x}"
        elif op == 0xC9:
            mn, sem = "leave", "restore frame pointer and stack"
        elif op == 0xCC:
            mn, kind, sem = "int3", "normal", "breakpoint trap"
        elif op == 0xCD:
            mn, ops = "int", hex(rd.u(1))
        elif op in (0xD0, 0xD1, 0xD2, 0xD3):
            sz = 8 if op in (0xD0, 0xD2) else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            cnt = "1" if op in (0xD0, 0xD1) else "cl"
            mn, ops = GRP2[reg & 7], f"{rm}, {cnt}"
        elif op == 0xE8:
            rel = rd.u(4, signed=True)
            target = rd.base + rd.p + rel
            mn, ops, kind = "call", hex(target), "call"
            sem = "direct call"
        elif op == 0xE9:
            rel = rd.u(4, signed=True)
            target = rd.base + rd.p + rel
            mn, ops, kind = "jmp", hex(target), "jump"
        elif op == 0xEB:
            rel = rd.u(1, signed=True)
            target = rd.base + rd.p + rel
            mn, ops, kind = "jmp", hex(target), "jump"
        elif op in (0xF6, 0xF7):
            sz = 8 if op == 0xF6 else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            g = GRP3[reg & 7]
            imm = None
            if g == "test":
                imm = rd.u(1 if sz == 8 else (opsize // 8 if opsize >= 32 else 2))
            mn, ops = g, f"{rm}" + (f", {imm:#x}" if imm is not None else "")
        elif op == 0xF4:
            mn, kind, sem = "hlt", "normal", "halt (privileged)"
        elif op in (0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD):
            mn = {0xF8: "clc", 0xF9: "stc", 0xFA: "cli", 0xFB: "sti", 0xFC: "cld", 0xFD: "std"}[op]
        elif op in (0xFE, 0xFF):
            sz = 8 if op == 0xFE else opsize
            if op == 0xFF and rd.p < len(rd.d):
                peek_reg = (rd.d[rd.p] >> 3) & 7
                if peek_reg >= 2 and bits == 64:
                    sz = 64  # near call/jmp/push default to 64-bit in long mode
            reg, rm, tgt = _modrm(rd, sz, rex, addrsize)
            g = reg & 7
            if op == 0xFE:
                mn, ops = ("inc" if g == 0 else "dec"), rm
            else:
                mn = GRP5[g]
                ops = rm
                if mn in ("call", "callf"):
                    kind = "call"
                elif mn in ("jmp", "jmpf"):
                    kind = "jump"
                target = tgt
        else:
            mn, kind = "db", "unknown"
            ops = f"0x{op:02x}"
            sem = "opcode not in built-in subset; emitted as raw byte"
    else:
        if op == 0x05:
            mn, kind, sem = "syscall", "normal", "system call (Linux x86-64)"
        elif op == 0x0B:
            mn, kind, sem = "ud2", "unknown", "undefined instruction trap"
        elif op == 0x1E and rep == "rep ":
            b = rd.u(1)
            if b == 0xFA:
                mn, sem = "endbr64", "indirect branch tracking landing pad"
            else:
                mn, kind, ops = "db", "unknown", f"0x0f 0x1e 0x{b:02x}"
        elif op == 0x1F:
            reg, rm, _ = _modrm(rd, opsize, rex, addrsize)
            mn, sem = "nop", "multi-byte nop"
            ops = rm
        elif 0x80 <= op <= 0x8F:
            rel = rd.u(4, signed=True)
            target = rd.base + rd.p + rel
            mn, ops, kind = "j" + CC[op - 0x80], hex(target), "cond"
        elif 0x90 <= op <= 0x9F:
            reg, rm, _ = _modrm(rd, 8, rex, addrsize)
            mn, ops = "set" + CC[op - 0x90], rm
        elif op == 0xA2:
            mn, sem = "cpuid", "CPU identification"
        elif op == 0xAF:
            reg, rm, _ = _modrm(rd, opsize, rex, addrsize)
            mn, ops = "imul", f"{_reg(reg, opsize, rex)}, {rm}"
        elif op in (0xB6, 0xB7, 0xBE, 0xBF):
            srcsz = 8 if op in (0xB6, 0xBE) else 16
            reg, rm, _ = _modrm(rd, srcsz, rex, addrsize)
            mn = "movzx" if op in (0xB6, 0xB7) else "movsx"
            ops = f"{_reg(reg, opsize, rex)}, {rm}"
        elif op in (0x10, 0x11, 0x28, 0x29):
            reg, rm, _ = _modrm(rd, 32, rex, addrsize)
            base_mn = {0x10: "movups/movss", 0x11: "movups/movss", 0x28: "movaps", 0x29: "movaps"}[op]
            mn = (rep + base_mn).strip()
            ops = f"{rm}, xmm{reg}" if op in (0x11, 0x29) else f"xmm{reg}, {rm}"
        elif op in (0xC0, 0xC1):
            sz = 8 if op == 0xC0 else opsize
            reg, rm, _ = _modrm(rd, sz, rex, addrsize)
            mn, ops = "xadd", f"{rm}, {_reg(reg, sz, rex)}"
        else:
            mn, kind = "db", "unknown"
            ops = f"0x0f 0x{op:02x}"
            sem = "two-byte opcode not in built-in subset"
    size = rd.p - start
    if size <= 0:
        size = 1
    return {"address": base, "bytes": data[start:start + size].hex(), "mnemonic": mn, "operands": ops,
            "size": size, "kind": kind, "target": target, "semantics": sem or mn}


# ---------------------------------------------------------------------------
# AArch64
# ---------------------------------------------------------------------------

A64_CC = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]
A64_REGS = [f"x{i}" for i in range(31)] + ["sp"]


def _a64reg(n, sf, is_sp=False):
    if n == 31:
        return "sp" if is_sp else "xzr"
    return (f"x{n}" if sf else f"w{n}")


def decode_arm64(data: bytes, pos: int, base: int) -> dict:
    if pos + 4 > len(data):
        return {"address": base, "bytes": data[pos:].hex(), "mnemonic": ".word", "operands": "", "size": len(data) - pos,
                "kind": "unknown", "semantics": "truncated"}
    w = int.from_bytes(data[pos:pos + 4], "little")
    start = pos
    mn, ops, kind, target, sem = ".word", f"0x{w:08x}", "unknown", None, "opcode not in built-in AArch64 subset"
    sf = (w >> 31) & 1

    def reg(n):
        return _a64reg(n, sf)

    if w == 0xD503201F:
        mn, ops, sem = "nop", "", "no operation"
    elif (w & 0xFFFFFC1F) == 0xD65F0000:
        rn = (w >> 5) & 31
        mn, ops, kind, sem = "ret", f"x{rn}" if rn != 30 else "", "ret", "return via link register"
    elif (w & 0xFFFFFC1F) == 0xD61F0000:
        mn, ops, kind = "br", f"x{(w >> 5) & 31}", "jump"
    elif (w & 0xFFFFFC1F) == 0xD63F0000:
        mn, ops, kind = "blr", f"x{(w >> 5) & 31}", "call"
    elif (w & 0x7C000000) == 0x14000000:
        imm = w & 0x3FFFFFF
        if imm & 0x2000000:
            imm -= 0x4000000
        target = base + imm * 4
        bl = (w >> 31) & 1
        mn, ops, kind = ("bl" if bl else "b"), hex(target), ("call" if bl else "jump")
        sem = "direct branch" + (" with link" if bl else "")
    elif (w & 0xFF000010) == 0x54000000:
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        target = base + imm * 4
        mn, ops, kind = "b." + A64_CC[w & 15], hex(target), "cond"
    elif (w & 0x7E000000) == 0x34000000:
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        target = base + imm * 4
        rn = (w >> 5) & 31
        mn = "cbnz" if (w >> 24) & 1 else "cbz"
        ops, kind = f"{reg(rn)}, {hex(target)}", "cond"
    elif (w & 0x7E000000) == 0x36000000:
        imm = (w >> 5) & 0x3FFF
        if imm & 0x2000:
            imm -= 0x4000
        target = base + imm * 4
        bit = ((w >> 31) << 5) | ((w >> 19) & 31)
        mn = "tbnz" if (w >> 24) & 1 else "tbz"
        ops, kind = f"{reg(w & 31)}, #{bit}, {hex(target)}", "cond"
    elif (w & 0x7F800000) in (0x52800000, 0x72800000, 0x12800000, 0x32800000, 0x52A00000, 0x72A00000):
        opc = (w >> 29) & 3
        hw = (w >> 21) & 3
        imm = (w >> 5) & 0xFFFF
        rd = w & 31
        mn = {0: "movn", 2: "movz", 3: "movk"}.get(opc, "mov?")
        ops = f"{reg(rd)}, #{imm:#x}" + (f", lsl #{hw * 16}" if hw else "")
        sem = "move wide immediate"
    elif (w & 0x1F000000) == 0x11000000:
        sh = (w >> 22) & 1
        imm = (w >> 10) & 0xFFF
        rn, rd = (w >> 5) & 31, w & 31
        sub = (w >> 30) & 1
        s = (w >> 29) & 1
        mn = ("sub" if sub else "add") + ("s" if s else "")
        if mn == "subs" and rd == 31:
            mn, ops = "cmp", f"{_a64reg(rn, sf, True)}, #{imm:#x}"
        else:
            ops = f"{_a64reg(rd, sf, True)}, {_a64reg(rn, sf, True)}, #{imm:#x}" + (f", lsl #{12}" if sh else "")
        sem = "add/subtract immediate"
    elif (w & 0x1F200000) == 0x0B000000 or (w & 0x1F200000) == 0x0B200000:
        rn, rm, rd = (w >> 5) & 31, (w >> 16) & 31, w & 31
        sub = (w >> 30) & 1
        s = (w >> 29) & 1
        shift = (w >> 10) & 63
        mn = ("sub" if sub else "add") + ("s" if s else "")
        ops = f"{reg(rd)}, {reg(rn)}, {reg(rm)}" + (f", lsl #{shift}" if shift else "")
    elif (w & 0x3B000000) == 0x39000000:
        size = (w >> 30) & 3
        opc = (w >> 22) & 3
        imm = (w >> 10) & 0xFFF
        rn, rt = (w >> 5) & 31, w & 31
        scale = size
        off = imm << scale
        load = opc & 1
        rname = (f"x{rt}" if size == 3 else f"w{rt}")
        mn = "ldr" if load else "str"
        ops = f"{rname}, [{A64_REGS[rn]}, #{off}]"
        sem = "load/store unsigned offset"
    elif (w & 0x3C000000) == 0x28000000:
        opc = (w >> 30) & 3
        L = (w >> 22) & 1
        imm7 = (w >> 15) & 0x7F
        if imm7 & 0x40:
            imm7 -= 0x80
        rt2, rn, rt = (w >> 10) & 31, (w >> 5) & 31, w & 31
        scale = 3 if opc & 2 else 2
        off = imm7 << scale
        mode = (w >> 23) & 3
        pre = mode == 3
        post = mode == 1
        mn = "ldp" if L else "stp"
        rr = (lambda n: f"x{n}" if opc & 2 else f"w{n}")
        addr = f"[{A64_REGS[rn]}, #{off}]" + ("!" if pre else "")
        if post:
            addr = f"[{A64_REGS[rn]}], #{off}"
        ops = f"{rr(rt)}, {rr(rt2)}, {addr}"
        sem = "load/store pair"
    elif (w & 0x1F000000) == 0x10000000:
        immlo = (w >> 29) & 3
        immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        rd = w & 31
        adrp = (w >> 31) & 1
        if adrp:
            target = (base & ~0xFFF) + (imm << 12)
            mn, ops = "adrp", f"x{rd}, {hex(target)}"
        else:
            target = base + imm
            mn, ops = "adr", f"x{rd}, {hex(target)}"
        sem = "PC-relative address"
    elif (w & 0x7F800000) == 0x2A000000:
        rn, rm, rd = (w >> 5) & 31, (w >> 16) & 31, w & 31
        mn, ops = "orr", f"{reg(rd)}, {reg(rn)}, {reg(rm)}"
        if rn == 31:
            mn, ops = "mov", f"{reg(rd)}, {reg(rm)}"
    elif (w & 0x7F200000) == 0x6B000000:
        rn, rm = (w >> 5) & 31, (w >> 16) & 31
        mn, ops = "cmp", f"{reg(rn)}, {reg(rm)}"
    elif (w & 0xFFE0FC00) == 0xDAC00C00:
        mn, ops = "rbit", f"{reg(w & 31)}, {reg((w >> 5) & 31)}"
    elif (w & 0xFFFFFC00) == 0xDAC01000:
        mn, ops = "clz", f"{reg(w & 31)}, {reg((w >> 5) & 31)}"
    elif (w & 0xFFE0FFE0) == 0x9AC02000:
        mn, ops = "udiv", f"{reg(w & 31)}, {reg((w >> 5) & 31)}, {reg((w >> 16) & 31)}"
    elif (w & 0xFFE0FFE0) == 0x9AC02400:
        mn, ops = "sdiv", f"{reg(w & 31)}, {reg((w >> 5) & 31)}, {reg((w >> 16) & 31)}"
    elif (w & 0xFFE08000) == 0x9B000000:
        ra, rn, rm, rd = (w >> 10) & 31, (w >> 5) & 31, (w >> 16) & 31, w & 31
        mn, ops = "madd", f"{reg(rd)}, {reg(rn)}, {reg(rm)}, {reg(ra)}"
    return {"address": base, "bytes": data[start:start + 4].hex(), "mnemonic": mn, "operands": ops,
            "size": 4, "kind": kind, "target": target, "semantics": sem}


# ---------------------------------------------------------------------------
# RISC-V (RV32I/RV64I + M + common C)
# ---------------------------------------------------------------------------

RV_REGS = ["zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "s0", "s1", "a0", "a1", "a2", "a3",
           "a4", "a5", "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11",
           "t3", "t4", "t5", "t6"]
RV_C_REGS = ["s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5"]


def _sext(v, bits):
    if v & (1 << (bits - 1)):
        v -= 1 << bits
    return v


def decode_riscv(data: bytes, pos: int, base: int, xlen: int) -> dict:
    if pos + 2 > len(data):
        return {"address": base, "bytes": data[pos:].hex(), "mnemonic": ".half", "operands": "", "size": len(data) - pos,
                "kind": "unknown", "semantics": "truncated"}
    hw = int.from_bytes(data[pos:pos + 2], "little")
    if hw & 3 != 3:  # compressed
        return _decode_rvc(hw, base, xlen)
    if pos + 4 > len(data):
        return {"address": base, "bytes": data[pos:pos + 4].hex(), "mnemonic": ".word", "operands": "", "size": 4,
                "kind": "unknown", "semantics": "truncated"}
    w = int.from_bytes(data[pos:pos + 4], "little")
    op = w & 0x7F
    rd = (w >> 7) & 31
    f3 = (w >> 12) & 7
    rs1 = (w >> 15) & 31
    rs2 = (w >> 20) & 31
    f7 = (w >> 25) & 127
    mn, ops, kind, target, sem = ".word", f"0x{w:08x}", "unknown", None, "opcode not in built-in RISC-V subset"
    imm_i = _sext(w >> 20, 12)
    imm_s = _sext(((w >> 25) << 5) | ((w >> 7) & 31), 12)
    imm_b = _sext(((w >> 31) << 12) | (((w >> 7) & 1) << 11) | (((w >> 25) & 63) << 5) | (((w >> 8) & 15) << 1), 13)
    imm_u = w & 0xFFFFF000
    imm_j = _sext(((w >> 31) << 20) | (((w >> 12) & 255) << 12) | (((w >> 20) & 1) << 11) | (((w >> 21) & 1023) << 1), 21)
    if op == 0x37:
        mn, ops, sem = "lui", f"{RV_REGS[rd]}, {imm_u >> 12:#x}", "load upper immediate"
    elif op == 0x17:
        target = base + imm_u
        mn, ops = "auipc", f"{RV_REGS[rd]}, {imm_u >> 12:#x}"
    elif op == 0x6F:
        target = base + imm_j
        mn, ops, kind = "jal", f"{RV_REGS[rd]}, {hex(target)}", ("call" if rd == 1 else "jump")
    elif op == 0x67 and f3 == 0:
        if rd == 0 and rs1 == 1 and imm_i == 0:
            mn, ops, kind, sem = "ret", "", "ret", "return (jalr x0, 0(ra))"
        else:
            mn, ops, kind = "jalr", f"{RV_REGS[rd]}, {imm_i}({RV_REGS[rs1]})", ("call" if rd == 1 else "jump")
    elif op == 0x63:
        target = base + imm_b
        bmn = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}.get(f3, "b?")
        mn, ops, kind = bmn, f"{RV_REGS[rs1]}, {RV_REGS[rs2]}, {hex(target)}", "cond"
    elif op == 0x03:
        lmn = {0: "lb", 1: "lh", 2: "lw", 3: "ld", 4: "lbu", 5: "lhu", 6: "lwu"}.get(f3, "l?")
        mn, ops = lmn, f"{RV_REGS[rd]}, {imm_i}({RV_REGS[rs1]})"
    elif op == 0x23:
        smn = {0: "sb", 1: "sh", 2: "sw", 3: "sd"}.get(f3, "s?")
        mn, ops = smn, f"{RV_REGS[rs2]}, {imm_s}({RV_REGS[rs1]})"
    elif op == 0x13:
        if f3 == 1:
            mn, ops = "slli", f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {(w >> 20) & (63 if xlen == 64 else 31)}"
        elif f3 == 5:
            mn = "srai" if (w >> 30) & 1 else "srli"
            ops = f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {(w >> 20) & (63 if xlen == 64 else 31)}"
        else:
            amn = {0: "addi", 2: "slti", 3: "sltiu", 4: "xori", 6: "ori", 7: "andi"}[f3]
            mn, ops = amn, f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {imm_i}"
            if amn == "addi" and rs1 == 0:
                mn, ops = "li", f"{RV_REGS[rd]}, {imm_i}"
            if amn == "addi" and rd == 0 and rs1 == 0 and imm_i == 0:
                mn, ops = "nop", ""
    elif op == 0x1B:
        mn, ops = {0: "addiw", 1: "slliw", 5: "srliw/sraiw"}.get(f3, "opiw"), f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {imm_i}"
    elif op == 0x33:
        if f7 == 1:
            mmn = {0: "mul", 1: "mulh", 2: "mulhsu", 3: "mulhu", 4: "div", 5: "divu", 6: "rem", 7: "remu"}[f3]
            mn, ops = mmn, f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {RV_REGS[rs2]}"
        else:
            amn = {(0, 0): "add", (0x20, 0): "sub", (0, 1): "sll", (0, 2): "slt", (0, 3): "sltu",
                   (0, 4): "xor", (0, 5): "srl", (0x20, 5): "sra", (0, 6): "or", (0, 7): "and"}.get((f7, f3), "op")
            mn, ops = amn, f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {RV_REGS[rs2]}"
    elif op == 0x3B:
        mn, ops = {0: "addw/subw", 1: "sllw", 5: "srlw/sraw"}.get(f3, "opw"), f"{RV_REGS[rd]}, {RV_REGS[rs1]}, {RV_REGS[rs2]}"
    elif op == 0x0F:
        mn, sem = "fence", "memory ordering"
    elif op == 0x73:
        if w == 0x00000073:
            mn, ops, sem = "ecall", "", "environment call"
        elif w == 0x00100073:
            mn, ops, sem = "ebreak", "", "breakpoint"
        else:
            mn, ops = "csr", f"{RV_REGS[rd]}, csr{imm_i & 0xFFF}, {RV_REGS[rs1]}"
    return {"address": base, "bytes": data[pos:pos + 4].hex(), "mnemonic": mn, "operands": ops,
            "size": 4, "kind": kind, "target": target, "semantics": sem}


def _decode_rvc(hw: int, base: int, xlen: int = 64) -> dict:
    op = hw & 3
    f3 = (hw >> 13) & 7
    mn, ops, kind, target = ".half", f"0x{hw:04x}", "unknown", None
    size = 2
    rd = (hw >> 7) & 31
    rs2 = (hw >> 2) & 31
    if hw == 0x0001:
        mn, ops = "c.nop", ""
    # ---- Quadrant 1 (op=1) ----
    elif op == 1 and f3 == 0:  # C.ADDI / C.NOP
        imm = _sext(((hw >> 12) & 1) << 5 | ((hw >> 2) & 31), 6)
        if rd == 0:
            mn, ops = "c.nop", ""
        else:
            mn, ops = "c.addi", f"{RV_REGS[rd]}, {imm}"
    elif op == 1 and f3 == 1:  # C.JAL (RV32) / C.ADDIW (RV64)
        if xlen == 32:
            imm = ((hw >> 12) & 1) << 11 | ((hw >> 11) & 1) << 4 | ((hw >> 9) & 3) << 8 | ((hw >> 8) & 1) << 10 | \
                  ((hw >> 7) & 1) << 6 | ((hw >> 6) & 1) << 7 | ((hw >> 3) & 7) << 1 | ((hw >> 2) & 1) << 5
            imm = _sext(imm, 12)
            target = base + imm
            mn, ops, kind = "c.jal", hex(target), "call"
        else:
            imm = _sext(((hw >> 12) & 1) << 5 | ((hw >> 2) & 31), 6)
            mn, ops = "c.addiw", f"{RV_REGS[rd]}, {imm}"
    elif op == 1 and f3 == 2:  # C.LI
        imm = _sext(((hw >> 12) & 1) << 5 | ((hw >> 2) & 31), 6)
        mn, ops = "c.li", f"{RV_REGS[rd]}, {imm}"
    elif op == 1 and f3 == 3:  # C.LUI (rd!=2) / C.ADDI16SP (rd==2)
        if rd == 2:
            imm = ((hw >> 12) & 1) << 9 | ((hw >> 6) & 1) << 4 | ((hw >> 5) & 1) << 6 | \
                  ((hw >> 3) & 3) << 7 | ((hw >> 2) & 1) << 5
            imm = _sext(imm, 10)
            mn, ops = "c.addi16sp", f"sp, {imm}"
        else:
            imm = _sext(((hw >> 12) & 1) << 5 | ((hw >> 2) & 31), 6)
            mn, ops = "c.lui", f"{RV_REGS[rd]}, {imm}"
    elif op == 1 and f3 == 5:  # C.J
        imm = ((hw >> 12) & 1) << 11 | ((hw >> 11) & 1) << 4 | ((hw >> 9) & 3) << 8 | ((hw >> 8) & 1) << 10 | \
              ((hw >> 7) & 1) << 6 | ((hw >> 6) & 1) << 7 | ((hw >> 3) & 7) << 1 | ((hw >> 2) & 1) << 5
        imm = _sext(imm, 12)
        target = base + imm
        mn, ops, kind = "c.j", hex(target), "jump"
    elif op == 1 and f3 in (6, 7):  # C.BEQZ / C.BNEZ
        rs1p = RV_C_REGS[(hw >> 7) & 7]
        imm = ((hw >> 12) & 1) << 8 | ((hw >> 10) & 3) << 3 | ((hw >> 5) & 3) << 6 | ((hw >> 3) & 3) << 1 | ((hw >> 2) & 1) << 5
        imm = _sext(imm, 9)
        target = base + imm
        mn, ops, kind = ("c.bnez" if f3 == 7 else "c.beqz"), f"{rs1p}, {hex(target)}", "cond"
    # ---- Quadrant 2 (op=2) ----
    elif op == 2 and f3 == 0:  # C.SLLI
        imm = ((hw >> 12) & 1) << 5 | ((hw >> 2) & 31)
        mn, ops = "c.slli", f"{RV_REGS[rd]}, {imm}"
    elif op == 2 and f3 == 2:  # C.LWSP
        imm = ((hw >> 5) & 3) << 6 | ((hw >> 12) & 1) << 5 | ((hw >> 2) & 7) << 2
        mn, ops = "c.lwsp", f"{RV_REGS[rd]}, {imm}(sp)"
    elif op == 2 and f3 == 3 and xlen == 64:  # C.LDSP
        imm = ((hw >> 3) & 15) << 4 | ((hw >> 12) & 1) << 5 | ((hw >> 2) & 3) << 2
        mn, ops = "c.ldsp", f"{RV_REGS[rd]}, {imm}(sp)"
    elif op == 2 and f3 == 4:  # C.JR / C.MV / C.EBREAK / C.JALR / C.ADD
        bit = (hw >> 12) & 1
        if bit == 0 and rs2 == 0:
            if rd == 0:
                mn, ops, kind = "c.ebreak", "", "breakpoint"
            else:
                mn, ops, kind = "c.jr", f"{RV_REGS[rd]}", ("ret" if rd == 1 else "jump")
        elif bit == 0:
            mn, ops = "c.mv", f"{RV_REGS[rd]}, {RV_REGS[rs2]}"
        elif bit == 1 and rs2 == 0:
            mn, ops, kind = "c.jalr", f"{RV_REGS[rd]}", "call"
        elif bit == 1:
            mn, ops = "c.add", f"{RV_REGS[rd]}, {RV_REGS[rs2]}"
    elif op == 2 and f3 == 6:  # C.SWSP
        imm = ((hw >> 9) & 15) << 2 | ((hw >> 7) & 3) << 6
        mn, ops = "c.swsp", f"{RV_REGS[rs2]}, {imm}(sp)"
    elif op == 2 and f3 == 7 and xlen == 64:  # C.SDSP
        imm = ((hw >> 10) & 7) << 3 | ((hw >> 7) & 7) << 6
        mn, ops = "c.sdsp", f"{RV_REGS[rs2]}, {imm}(sp)"
    # ---- Quadrant 0 (op=0) ----
    elif op == 0 and f3 == 2:  # C.LW
        rdp = RV_C_REGS[(hw >> 2) & 7]
        rs1p = RV_C_REGS[(hw >> 7) & 7]
        imm = ((hw >> 10) & 7) << 3 | ((hw >> 6) & 1) << 2 | ((hw >> 5) & 1) << 6
        mn, ops = "c.lw", f"{rdp}, {imm}({rs1p})"
    elif op == 0 and f3 == 3 and xlen == 64:  # C.LD
        rdp = RV_C_REGS[(hw >> 2) & 7]
        rs1p = RV_C_REGS[(hw >> 7) & 7]
        imm = ((hw >> 10) & 7) << 3 | ((hw >> 5) & 3) << 6
        mn, ops = "c.ld", f"{rdp}, {imm}({rs1p})"
    elif op == 0 and f3 == 6:  # C.SW
        rs2p = RV_C_REGS[(hw >> 2) & 7]
        rs1p = RV_C_REGS[(hw >> 7) & 7]
        imm = ((hw >> 10) & 7) << 3 | ((hw >> 6) & 1) << 2 | ((hw >> 5) & 1) << 6
        mn, ops = "c.sw", f"{rs2p}, {imm}({rs1p})"
    elif op == 0 and f3 == 7 and xlen == 64:  # C.SD
        rs2p = RV_C_REGS[(hw >> 2) & 7]
        rs1p = RV_C_REGS[(hw >> 7) & 7]
        imm = ((hw >> 10) & 7) << 3 | ((hw >> 5) & 3) << 6
        mn, ops = "c.sd", f"{rs2p}, {imm}({rs1p})"
    return {"address": base, "bytes": hw.to_bytes(2, "little").hex(), "mnemonic": mn, "operands": ops,
            "size": size, "kind": kind, "target": target, "semantics": mn}


# ---------------------------------------------------------------------------
# MIPS32
# ---------------------------------------------------------------------------

MIPS_REGS = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
             "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]


def decode_mips(data: bytes, pos: int, base: int, endian="big") -> dict:
    if pos + 4 > len(data):
        return {"address": base, "bytes": data[pos:].hex(), "mnemonic": ".word", "operands": "", "size": len(data) - pos,
                "kind": "unknown", "semantics": "truncated"}
    w = int.from_bytes(data[pos:pos + 4], endian)
    op = w >> 26
    rs, rt, rd = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    shamt, funct = (w >> 6) & 31, w & 63
    imm = w & 0xFFFF
    simm = _sext(imm, 16)
    mn, ops, kind, target, sem = ".word", f"0x{w:08x}", "unknown", None, "opcode not in built-in MIPS subset; delay slot follows branches"
    if op == 0:
        fmap = {0x20: "add", 0x21: "addu", 0x22: "sub", 0x23: "subu", 0x24: "and", 0x25: "or", 0x26: "xor",
                0x27: "nor", 0x2A: "slt", 0x2B: "sltu"}
        if funct in fmap:
            mn, ops = fmap[funct], f"{MIPS_REGS[rd]}, {MIPS_REGS[rs]}, {MIPS_REGS[rt]}"
        elif funct in (0, 2, 3):
            mn = {0: "sll", 2: "srl", 3: "sra"}[funct]
            ops = f"{MIPS_REGS[rd]}, {MIPS_REGS[rt]}, {shamt}"
            if w == 0:
                mn, ops = "nop", ""
        elif funct == 8:
            mn, ops, kind = "jr", f"{MIPS_REGS[rs]}", ("ret" if rs == 31 else "jump")
        elif funct == 9:
            mn, ops, kind = "jalr", f"{MIPS_REGS[rd]}, {MIPS_REGS[rs]}", "call"
        elif funct in (0x18, 0x19):
            mn = "mult" if funct == 0x18 else "multu"
            ops = f"{MIPS_REGS[rs]}, {MIPS_REGS[rt]}"
        elif funct in (0x1A, 0x1B):
            mn = "div" if funct == 0x1A else "divu"
            ops = f"{MIPS_REGS[rs]}, {MIPS_REGS[rt]}"
        elif funct in (0x10, 0x12):
            mn = "mfhi" if funct == 0x10 else "mflo"
            ops = f"{MIPS_REGS[rd]}"
        elif funct == 0xC:
            mn, sem = "syscall", "system call"
    elif op in (2, 3):
        tgt = ((base + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
        target = tgt
        mn, ops, kind = ("jal" if op == 3 else "j"), hex(tgt), ("call" if op == 3 else "jump")
    elif op in (4, 5):
        target = base + 4 + (simm << 2)
        mn, ops, kind = ("beq" if op == 4 else "bne"), f"{MIPS_REGS[rs]}, {MIPS_REGS[rt]}, {hex(target)}", "cond"
    elif op in (6, 7):
        target = base + 4 + (simm << 2)
        mn, ops, kind = ("blez" if op == 6 else "bgtz"), f"{MIPS_REGS[rs]}, {hex(target)}", "cond"
    elif op == 1:
        target = base + 4 + (simm << 2)
        mn, ops, kind = ("bltz" if rt == 0 else "bgez"), f"{MIPS_REGS[rs]}, {hex(target)}", "cond"
    elif op in (8, 9):
        mn = "addi" if op == 8 else "addiu"
        ops = f"{MIPS_REGS[rt]}, {MIPS_REGS[rs]}, {simm}"
        if op == 9 and rs == 0:
            mn, ops = "li", f"{MIPS_REGS[rt]}, {simm}"
    elif op in (0xA, 0xB):
        mn = "slti" if op == 0xA else "sltiu"
        ops = f"{MIPS_REGS[rt]}, {MIPS_REGS[rs]}, {simm}"
    elif op in (0xC, 0xD, 0xE):
        mn = {0xC: "andi", 0xD: "ori", 0xE: "xori"}[op]
        ops = f"{MIPS_REGS[rt]}, {MIPS_REGS[rs]}, {imm:#x}"
    elif op == 0xF:
        mn, ops = "lui", f"{MIPS_REGS[rt]}, {imm:#x}"
    elif op in (0x20, 0x21, 0x23, 0x24, 0x25):
        mn = {0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu"}[op]
        ops = f"{MIPS_REGS[rt]}, {simm}({MIPS_REGS[rs]})"
    elif op in (0x28, 0x29, 0x2B):
        mn = {0x28: "sb", 0x29: "sh", 0x2B: "sw"}[op]
        ops = f"{MIPS_REGS[rt]}, {simm}({MIPS_REGS[rs]})"
    return {"address": base, "bytes": data[pos:pos + 4].hex(), "mnemonic": mn, "operands": ops,
            "size": 4, "kind": kind, "target": target, "semantics": sem}


# ---------------------------------------------------------------------------
# 6502 (full official opcode map)
# ---------------------------------------------------------------------------

_6502 = {}
def _t(op, mn, mode, size):
    _6502[op] = (mn, mode, size)
# Build table compactly
for op, mn, mode, size in [
    (0x00,"brk","imp",1),(0x01,"ora","izx",2),(0x05,"ora","zp",2),(0x06,"asl","zp",2),(0x08,"php","imp",1),
    (0x09,"ora","imm",2),(0x0A,"asl","acc",1),(0x0D,"ora","abs",3),(0x0E,"asl","abs",3),
    (0x10,"bpl","rel",2),(0x11,"ora","izy",2),(0x15,"ora","zpx",2),(0x16,"asl","zpx",2),(0x18,"clc","imp",1),
    (0x19,"ora","aby",3),(0x1D,"ora","abx",3),(0x1E,"asl","abx",3),
    (0x20,"jsr","abs",3),(0x21,"and","izx",2),(0x24,"bit","zp",2),(0x25,"and","zp",2),(0x26,"rol","zp",2),
    (0x28,"plp","imp",1),(0x29,"and","imm",2),(0x2A,"rol","acc",1),(0x2C,"bit","abs",3),(0x2D,"and","abs",3),(0x2E,"rol","abs",3),
    (0x30,"bmi","rel",2),(0x31,"and","izy",2),(0x35,"and","zpx",2),(0x36,"rol","zpx",2),(0x38,"sec","imp",1),
    (0x39,"and","aby",3),(0x3D,"and","abx",3),(0x3E,"rol","abx",3),
    (0x40,"rti","imp",1),(0x41,"eor","izx",2),(0x45,"eor","zp",2),(0x46,"lsr","zp",2),(0x48,"pha","imp",1),
    (0x49,"eor","imm",2),(0x4A,"lsr","acc",1),(0x4C,"jmp","abs",3),(0x4D,"eor","abs",3),(0x4E,"lsr","abs",3),
    (0x50,"bvc","rel",2),(0x51,"eor","izy",2),(0x55,"eor","zpx",2),(0x56,"lsr","zpx",2),(0x58,"cli","imp",1),
    (0x59,"eor","aby",3),(0x5D,"eor","abx",3),(0x5E,"lsr","abx",3),
    (0x60,"rts","imp",1),(0x61,"adc","izx",2),(0x65,"adc","zp",2),(0x66,"ror","zp",2),(0x68,"pla","imp",1),
    (0x69,"adc","imm",2),(0x6A,"ror","acc",1),(0x6C,"jmp","ind",3),(0x6D,"adc","abs",3),(0x6E,"ror","abs",3),
    (0x70,"bvs","rel",2),(0x71,"adc","izy",2),(0x75,"adc","zpx",2),(0x76,"ror","zpx",2),(0x78,"sei","imp",1),
    (0x79,"adc","aby",3),(0x7D,"adc","abx",3),(0x7E,"ror","abx",3),
    (0x81,"sta","izx",2),(0x84,"sty","zp",2),(0x85,"sta","zp",2),(0x86,"stx","zp",2),(0x88,"dey","imp",1),
    (0x8A,"txa","imp",1),(0x8C,"sty","abs",3),(0x8D,"sta","abs",3),(0x8E,"stx","abs",3),
    (0x90,"bcc","rel",2),(0x91,"sta","izy",2),(0x94,"sty","zpx",2),(0x95,"sta","zpx",2),(0x96,"stx","zpy",2),
    (0x98,"tya","imp",1),(0x99,"sta","aby",3),(0x9A,"txs","imp",1),(0x9D,"sta","abx",3),
    (0xA0,"ldy","imm",2),(0xA1,"lda","izx",2),(0xA2,"ldx","imm",2),(0xA4,"ldy","zp",2),(0xA5,"lda","zp",2),
    (0xA6,"ldx","zp",2),(0xA8,"tay","imp",1),(0xA9,"lda","imm",2),(0xAA,"tax","imp",1),(0xAC,"ldy","abs",3),
    (0xAD,"lda","abs",3),(0xAE,"ldx","abs",3),
    (0xB0,"bcs","rel",2),(0xB1,"lda","izy",2),(0xB4,"ldy","zpx",2),(0xB5,"lda","zpx",2),(0xB6,"ldx","zpy",2),
    (0xB8,"clv","imp",1),(0xB9,"lda","aby",3),(0xBA,"tsx","imp",1),(0xBC,"ldy","abx",3),(0xBD,"lda","abx",3),(0xBE,"ldx","aby",3),
    (0xC0,"cpy","imm",2),(0xC1,"cmp","izx",2),(0xC4,"cpy","zp",2),(0xC5,"cmp","zp",2),(0xC6,"dec","zp",2),
    (0xC8,"iny","imp",1),(0xC9,"cmp","imm",2),(0xCA,"dex","imp",1),(0xCC,"cpy","abs",3),(0xCD,"cmp","abs",3),(0xCE,"dec","abs",3),
    (0xD0,"bne","rel",2),(0xD1,"cmp","izy",2),(0xD5,"cmp","zpx",2),(0xD6,"dec","zpx",2),(0xD8,"cld","imp",1),
    (0xD9,"cmp","aby",3),(0xDD,"cmp","abx",3),(0xDE,"dec","abx",3),
    (0xE0,"cpx","imm",2),(0xE1,"sbc","izx",2),(0xE4,"cpx","zp",2),(0xE5,"sbc","zp",2),(0xE6,"inc","zp",2),
    (0xE8,"inx","imp",1),(0xE9,"sbc","imm",2),(0xEA,"nop","imp",1),(0xEC,"cpx","abs",3),(0xED,"sbc","abs",3),(0xEE,"inc","abs",3),
    (0xF0,"beq","rel",2),(0xF1,"sbc","izy",2),(0xF5,"sbc","zpx",2),(0xF6,"inc","zpx",2),(0xF8,"sed","imp",1),
    (0xF9,"sbc","aby",3),(0xFD,"sbc","abx",3),(0xFE,"inc","abx",3),
]:
    _t(op, mn, mode, size)


def decode_6502(data: bytes, pos: int, base: int) -> dict:
    if pos >= len(data):
        return {"address": base, "bytes": "", "mnemonic": "db", "operands": "", "size": 0, "kind": "unknown", "semantics": "end"}
    op = data[pos]
    if op not in _6502:
        return {"address": base, "bytes": f"{op:02x}", "mnemonic": "db", "operands": f"0x{op:02x}", "size": 1,
                "kind": "unknown", "semantics": "illegal/undocumented 6502 opcode"}
    mn, mode, size = _6502[op]
    if pos + size > len(data):
        size = len(data) - pos
    raw = data[pos:pos + size]
    ops, kind, target = "", "normal", None
    if mode == "imm":
        ops = f"#${raw[1]:02x}"
    elif mode == "zp":
        ops = f"${raw[1]:02x}"
    elif mode == "zpx":
        ops = f"${raw[1]:02x},X"
    elif mode == "zpy":
        ops = f"${raw[1]:02x},Y"
    elif mode == "abs":
        addr = raw[1] | (raw[2] << 8)
        ops = f"${addr:04x}"
        if mn in ("jmp", "jsr"):
            target = addr
            kind = "call" if mn == "jsr" else "jump"
    elif mode == "abx":
        ops = f"${raw[1] | (raw[2] << 8):04x},X"
    elif mode == "aby":
        ops = f"${raw[1] | (raw[2] << 8):04x},Y"
    elif mode == "ind":
        ops = f"(${raw[1] | (raw[2] << 8):04x})"
        kind = "jump"
    elif mode == "izx":
        ops = f"(${raw[1]:02x},X)"
    elif mode == "izy":
        ops = f"(${raw[1]:02x}),Y"
    elif mode == "rel":
        rel = raw[1] if raw[1] < 128 else raw[1] - 256
        target = base + 2 + rel
        ops = hex(target)
        kind = "cond"
    elif mode == "acc":
        ops = "A"
    if mn == "rts":
        kind = "ret"
    if mn == "rti":
        kind = "ret"
    return {"address": base, "bytes": raw.hex(), "mnemonic": mn, "operands": ops, "size": max(1, size),
            "kind": kind, "target": target, "semantics": f"6502 {mn} ({mode})"}


# ---------------------------------------------------------------------------
# EVM
# ---------------------------------------------------------------------------

EVM_OPS = {0x00: "STOP", 0x01: "ADD", 0x02: "MUL", 0x03: "SUB", 0x04: "DIV", 0x05: "SDIV", 0x06: "MOD",
           0x07: "SMOD", 0x08: "ADDMOD", 0x09: "MULMOD", 0x0A: "EXP", 0x0B: "SIGNEXTEND",
           0x10: "LT", 0x11: "GT", 0x12: "SLT", 0x13: "SGT", 0x14: "EQ", 0x15: "ISZERO", 0x16: "AND",
           0x17: "OR", 0x18: "XOR", 0x19: "NOT", 0x1A: "BYTE", 0x1B: "SHL", 0x1C: "SHR", 0x1D: "SAR",
           0x20: "SHA3", 0x30: "ADDRESS", 0x31: "BALANCE", 0x32: "ORIGIN", 0x33: "CALLER", 0x34: "CALLVALUE",
           0x35: "CALLDATALOAD", 0x36: "CALLDATASIZE", 0x37: "CALLDATACOPY", 0x38: "CODESIZE", 0x39: "CODECOPY",
           0x3A: "GASPRICE", 0x3B: "EXTCODESIZE", 0x3C: "EXTCODECOPY", 0x3D: "RETURNDATASIZE", 0x3E: "RETURNDATACOPY",
           0x3F: "EXTCODEHASH", 0x40: "BLOCKHASH", 0x41: "COINBASE", 0x42: "TIMESTAMP", 0x43: "NUMBER",
           0x44: "DIFFICULTY", 0x45: "GASLIMIT", 0x46: "CHAINID", 0x47: "SELFBALANCE", 0x48: "BASEFEE",
           0x50: "POP", 0x51: "MLOAD", 0x52: "MSTORE", 0x53: "MSTORE8", 0x54: "SLOAD", 0x55: "SSTORE",
           0x56: "JUMP", 0x57: "JUMPI", 0x58: "PC", 0x59: "MSIZE", 0x5A: "GAS", 0x5B: "JUMPDEST",
           0x5C: "TLOAD", 0x5D: "TSTORE", 0x5E: "MCOPY", 0x5F: "PUSH0",
           0xF0: "CREATE", 0xF1: "CALL", 0xF2: "CALLCODE", 0xF3: "RETURN", 0xF4: "DELEGATECALL",
           0xF5: "CREATE2", 0xFA: "STATICCALL", 0xFD: "REVERT", 0xFE: "INVALID", 0xFF: "SELFDESTRUCT"}
for i in range(1, 33):
    EVM_OPS[0x5F + i] = f"PUSH{i}"
for i in range(16):
    EVM_OPS[0x80 + i] = f"DUP{i + 1}"
    EVM_OPS[0x90 + i] = f"SWAP{i + 1}"
for i in range(5):
    EVM_OPS[0xA0 + i] = f"LOG{i}"


def decode_evm(data: bytes, pos: int, base: int) -> dict:
    op = data[pos]
    mn = EVM_OPS.get(op, f"INVALID(0x{op:02x})")
    size = 1
    ops = ""
    kind = "normal"
    target = None
    if 0x60 <= op <= 0x7F:
        n = op - 0x5F
        imm = data[pos + 1:pos + 1 + n]
        size = 1 + n
        ops = "0x" + imm.hex()
    if mn in ("JUMP", "JUMPI"):
        kind = "jump" if mn == "JUMP" else "cond"
    if mn in ("RETURN", "STOP", "REVERT", "SELFDESTRUCT", "INVALID"):
        kind = "ret"
    return {"address": base, "bytes": data[pos:pos + size].hex(), "mnemonic": mn, "operands": ops,
            "size": size, "kind": kind, "target": target, "semantics": f"EVM {mn}"}


# ---------------------------------------------------------------------------
# Stream decoding + CFG
# ---------------------------------------------------------------------------

def decode_stream(data: bytes, arch: str, base: int, offset: int, max_insns: int, endian="big") -> list:
    insns = []
    pos = offset
    addr = base
    decoders = {
        "x86": lambda p, a: decode_x86(data, p, a, 32),
        "x64": lambda p, a: decode_x86(data, p, a, 64),
        "arm64": lambda p, a: decode_arm64(data, p, a),
        "riscv32": lambda p, a: decode_riscv(data, p, a, 32),
        "riscv64": lambda p, a: decode_riscv(data, p, a, 64),
        "mips32": lambda p, a: decode_mips(data, p, a, endian),
        "6502": lambda p, a: decode_6502(data, p, a),
        "evm": lambda p, a: decode_evm(data, p, a),
    }
    dec = decoders[arch]
    while pos < len(data) and len(insns) < max_insns:
        ins = dec(pos, addr)
        if ins["size"] <= 0:
            break
        insns.append(ins)
        pos += ins["size"]
        addr += ins["size"]
        if ins["mnemonic"] in ("ret", "rts", "rti") and arch in ("6502",):
            pass  # continue; linear sweep
    return insns


def build_cfg(insns: list, arch: str) -> dict:
    if not insns:
        return {"blocks": [], "edges": [], "functions": [], "loops": []}
    by_addr = {i["address"]: i for i in insns}
    leaders = {insns[0]["address"]}
    for ins in insns:
        if ins["kind"] in ("jump", "cond", "call", "ret"):
            if ins["target"] is not None and ins["target"] in by_addr:
                leaders.add(ins["target"])
            nxt = ins["address"] + ins["size"]
            if nxt in by_addr:
                leaders.add(nxt)
    leaders = sorted(leaders)
    blocks = []
    for bi, start in enumerate(leaders):
        end = leaders[bi + 1] if bi + 1 < len(leaders) else (insns[-1]["address"] + insns[-1]["size"])
        body = [i for i in insns if start <= i["address"] < end]
        if not body:
            continue
        last = body[-1]
        blocks.append({"start": hex(start), "end": hex(end), "instruction_count": len(body),
                       "last": f"{last['mnemonic']} {last['operands']}".strip(),
                       "last_kind": last["kind"]})
    edges = []
    loops = []
    calls = []
    for ins in insns:
        src = ins["address"]
        if ins["kind"] in ("jump", "cond") and ins["target"] is not None:
            edges.append({"from": hex(src), "to": hex(ins["target"]), "type": ins["kind"]})
            if ins["target"] <= src:
                loops.append({"back_edge_from": hex(src), "to": hex(ins["target"])})
            if ins["kind"] == "cond":
                nxt = src + ins["size"]
                if nxt in by_addr:
                    edges.append({"from": hex(src), "to": hex(nxt), "type": "fallthrough"})
        elif ins["kind"] == "call":
            calls.append({"from": hex(src), "to": hex(ins["target"]) if ins["target"] is not None else ins["operands"],
                          "direct": ins["target"] is not None})
            nxt = src + ins["size"]
            if nxt in by_addr:
                edges.append({"from": hex(src), "to": hex(nxt), "type": "fallthrough"})
        elif ins["kind"] not in ("ret", "jump"):
            nxt = src + ins["size"]
            if nxt in by_addr and (nxt in leaders):
                edges.append({"from": hex(src), "to": hex(nxt), "type": "fallthrough"})
    # function candidates from prologues
    functions = []
    for idx, ins in enumerate(insns):
        prologue = False
        if arch in ("x86", "x64") and ins["mnemonic"] == "push" and ins["operands"] in ("rbp", "ebp"):
            if idx + 1 < len(insns) and insns[idx + 1]["mnemonic"] == "mov" and insns[idx + 1]["operands"].startswith(("rbp, rsp", "ebp, esp")):
                prologue = True
        if arch in ("x86", "x64") and ins["mnemonic"] == "endbr64":
            prologue = True
        if arch == "arm64" and ins["mnemonic"] == "stp" and "x29, x30" in ins["operands"]:
            prologue = True
        if arch in ("riscv32", "riscv64") and ins["mnemonic"] == "addi" and ins["operands"].startswith("sp, sp, -"):
            prologue = True
        if prologue:
            functions.append({"start": hex(ins["address"]), "evidence": "prologue pattern", "confidence": "medium"})
    return {"blocks": blocks, "edges": edges, "loops": loops, "calls": calls,
            "function_candidates": functions,
            "note": "Linear-sweep CFG over the decoded window; indirect targets and non-code data are not resolved."}


def track_values(insns: list, arch: str) -> dict:
    """Conservative intra-block constant tracking."""
    values = {}
    events = []
    for ins in insns:
        mn, ops = ins["mnemonic"], ins["operands"]
        if arch in ("x86", "x64"):
            if mn == "mov" and ", 0x" in ops:
                dst, src = ops.split(", ", 1)
                try:
                    values[dst] = int(src, 16)
                    events.append({"at": hex(ins["address"]), "reg": dst, "value": hex(values[dst])})
                except ValueError:
                    values.pop(dst, None)
            elif mn == "xor" and ops.split(", ")[0] == ops.split(", ")[-1]:
                reg = ops.split(", ")[0]
                values[reg] = 0
                events.append({"at": hex(ins["address"]), "reg": reg, "value": "0x0"})
            elif mn in ("add", "sub") and ", 0x" in ops:
                dst, src = ops.split(", ", 1)
                try:
                    v = int(src, 16)
                    if dst in values:
                        values[dst] = values[dst] + v if mn == "add" else values[dst] - v
                        events.append({"at": hex(ins["address"]), "reg": dst, "value": hex(values[dst])})
                except ValueError:
                    values.pop(dst, None)
            elif mn in ("call", "ret", "jmp") or ins["kind"] in ("cond", "jump"):
                values = {}
        elif arch == "arm64":
            if mn in ("movz", "mov") and ", #" in ops:
                dst, src = ops.split(", ", 1)
                try:
                    values[dst] = int(src.split(",")[0].replace("#", ""), 16 if "0x" in src else 10)
                except ValueError:
                    pass
            elif ins["kind"] in ("cond", "jump", "call", "ret"):
                values = {}
        elif arch in ("riscv32", "riscv64"):
            if mn == "li" or (mn == "addi" and ", " in ops):
                parts = ops.split(", ")
                if len(parts) == 3:
                    try:
                        values[parts[0]] = int(parts[2], 0)
                    except ValueError:
                        pass
            elif ins["kind"] in ("cond", "jump", "call", "ret"):
                values = {}
    return {"tracked_constants": events[:200], "final_known": {k: hex(v) for k, v in values.items()},
            "note": "Intra-block only; values are invalidated at branches/calls. Not a full data-flow analysis."}


def guess_convention(insns: list, arch: str) -> dict:
    used = set()
    for ins in insns[:200]:
        ops = ins["operands"]
        if arch in ("x86", "x64"):
            for r in ("rdi", "rsi", "rdx", "rcx", "r8", "r9", "edi", "esi", "edx", "ecx", "r8d", "r9d"):
                if r in ops.split(", ")[-1] or ops.endswith(r):
                    used.add(r)
        elif arch == "arm64":
            for i in range(8):
                if f"x{i}" in ops or f"w{i}" in ops:
                    used.add(f"x{i}")
        elif arch in ("riscv32", "riscv64", "mips32"):
            for i in range(8):
                if f"a{i}" in ops:
                    used.add(f"a{i}")
    if arch in ("x86", "x64"):
        sysv = used & {"rdi", "rsi", "edi", "esi"}
        win = used & {"rcx", "ecx"}
        if sysv and not win:
            return {"hypothesis": "System V AMD64 (rdi, rsi, rdx, rcx, r8, r9)", "confidence": "medium", "evidence": sorted(used)}
        if win and not sysv:
            return {"hypothesis": "Windows x64 (rcx, rdx, r8, r9)", "confidence": "medium", "evidence": sorted(used)}
        return {"hypothesis": "x86-64 (convention ambiguous from this window)", "confidence": "low", "evidence": sorted(used)}
    if arch == "arm64":
        return {"hypothesis": "AAPCS64 (x0-x7 arguments, x0 return)", "confidence": "medium", "evidence": sorted(used)}
    if arch in ("riscv32", "riscv64"):
        return {"hypothesis": "RISC-V ILP32/LP64 (a0-a7 arguments)", "confidence": "medium", "evidence": sorted(used)}
    if arch == "mips32":
        return {"hypothesis": "MIPS O32 (a0-a3 arguments, v0 return)", "confidence": "medium", "evidence": sorted(used)}
    return {"hypothesis": "not applicable", "confidence": "low", "evidence": []}


def recover_frame(insns: list, arch: str) -> dict:
    frame = {"pushed_registers": [], "frame_size_estimate": None, "evidence": []}
    if arch not in ("x86", "x64"):
        return {"note": "Frame recovery heuristic implemented for x86/x64 prologues."}
    for ins in insns[:60]:
        mn, ops = ins["mnemonic"], ins["operands"]
        if mn == "push":
            frame["pushed_registers"].append(ops)
            frame["evidence"].append(f"push {ops} at {hex(ins['address'])}")
        if mn == "sub" and ops.startswith(("rsp, ", "esp, ")):
            try:
                frame["frame_size_estimate"] = int(ops.split(", ")[1], 16)
                frame["evidence"].append(f"sub {ops} at {hex(ins['address'])}")
            except ValueError:
                pass
        if mn == "mov" and ops in ("rbp, rsp", "ebp, esp"):
            frame["frame_pointer"] = True
    if frame["pushed_registers"]:
        frame["saved_register_bytes"] = len(frame["pushed_registers"]) * (8 if arch == "x64" else 4)
    return frame


def pseudocode(insns: list, arch: str) -> list:
    lines = []
    for ins in insns:
        mn, ops, addr = ins["mnemonic"], ins["operands"], hex(ins["address"])
        if ins["kind"] == "unknown":
            lines.append(f"{addr}: /* unknown opcode {ins['bytes']} */")
        elif mn in ("mov", "li", "movz"):
            lines.append(f"{addr}: {ops.replace(', ', ' = ')};")
        elif mn in ("add", "sub", "and", "or", "xor", "addi"):
            parts = ops.split(", ")
            if len(parts) >= 2:
                opsym = {"add": "+", "addi": "+", "sub": "-", "and": "&", "or": "|", "xor": "^"}.get(mn, mn)
                if len(parts) == 3:
                    lines.append(f"{addr}: {parts[0]} = {parts[1]} {opsym} {parts[2]};")
                else:
                    lines.append(f"{addr}: {parts[0]} {opsym}= {parts[1]};")
        elif ins["kind"] == "cond":
            lines.append(f"{addr}: if ({mn[1:] if mn.startswith('j') else mn}) goto {ops.split(', ')[-1]};")
        elif ins["kind"] == "jump":
            lines.append(f"{addr}: goto {ops};")
        elif ins["kind"] == "call":
            lines.append(f"{addr}: call {ops};")
        elif ins["kind"] == "ret":
            lines.append(f"{addr}: return;")
        elif mn == "nop":
            continue
        else:
            lines.append(f"{addr}: {mn} {ops}; /* {ins['semantics']} */")
    return lines


def cmd_disasm(args, env, cfg):
    arch = args.arch
    if arch not in ARCHS:
        raise RetoolError("bad-arch", f"Unsupported built-in arch '{arch}'. Supported: {', '.join(ARCHS)}. Others: use external adapter + plan.", exit_code=EXIT_USAGE)
    path = validate_input_file(args.path, cfg)
    size = path.stat().st_size
    off = parse_int(args.offset) if args.offset else 0
    base = parse_int(args.base_addr) if args.base_addr else off
    length = parse_int(args.length) if args.length else min(size - off, 4096)
    if off < 0 or length < 0 or off + length > size:
        raise RetoolError("bad-range", "offset/length out of range", exit_code=EXIT_USAGE)
    max_insns = parse_int(args.max_insns) if args.max_insns else cfg["limits"]["max_disasm_instructions"]
    data = read_bytes(path, cfg, off, length)
    endian = args.endian or "big"
    insns = decode_stream(data, arch, base, 0, max_insns, endian)
    unknown = sum(1 for i in insns if i["kind"] == "unknown")
    result = {"file": str(path), "arch": arch, "base_addr": hex(base), "offset": off, "length": length,
              "instruction_count": len(insns), "unknown_count": unknown, "instructions": insns,
              "coverage_note": "Built-in decoder covers a documented common subset; unknown opcodes are emitted as raw bytes, never guessed."}
    if arch == "evm":
        selectors = []
        for i, ins in enumerate(insns):
            if ins["mnemonic"] == "PUSH4" and i + 1 < len(insns) and insns[i + 1]["mnemonic"] == "EQ":
                selectors.append(ins["operands"])
        result["function_selectors"] = selectors
        result["metadata_note"] = "Contract metadata (CBOR auxdata) may follow the code; see strings/parse."
    env["result"] = result
    if unknown:
        env["warnings"].append(f"{unknown} instructions outside the built-in subset; treat as data or decode with an external disassembler.")
    env["limitations"].append("Linear sweep; no recursive traversal, symbol-guided disassembly, or full ISA coverage. Validate with objdump/radare2/Ghidra via external/plan.")
    return env


def cmd_cfg(args, env, cfg):
    arch = args.arch
    if arch not in ARCHS:
        raise RetoolError("bad-arch", f"Unsupported built-in arch: {arch}", exit_code=EXIT_USAGE)
    path = validate_input_file(args.path, cfg)
    size = path.stat().st_size
    off = parse_int(args.offset) if args.offset else 0
    base = parse_int(args.base_addr) if args.base_addr else off
    length = parse_int(args.length) if args.length else min(size - off, 16384)
    if off < 0 or length < 0 or off + length > size:
        raise RetoolError("bad-range", "offset/length out of range", exit_code=EXIT_USAGE)
    max_insns = cfg["limits"]["max_cfg_instructions"]
    data = read_bytes(path, cfg, off, length)
    insns = decode_stream(data, arch, base, 0, max_insns, args.endian or "big")
    cfg_res = build_cfg(insns, arch)
    result = {"file": str(path), "arch": arch, "base_addr": hex(base), "instruction_count": len(insns),
              "cfg": cfg_res, "values": track_values(insns, arch),
              "calling_convention": guess_convention(insns, arch), "stack_frame": recover_frame(insns, arch)}
    if args.pseudocode:
        result["pseudocode"] = pseudocode(insns, arch)[:1000]
    # switch table candidates
    switches = []
    for ins in insns:
        if ins["kind"] == "jump" and "*" in ins["operands"] and "ptr" in ins["operands"]:
            switches.append({"at": hex(ins["address"]), "pattern": ins["operands"],
                             "note": "Indexed jump; table base/scale suggested by the operand. Validate bounds before reading the table."})
    if switches:
        result["switch_candidates"] = switches
    env["result"] = result
    env["limitations"].append("CFG is linear-sweep over the window; indirect edges, exceptions and tail calls are not fully resolved. Pseudocode is a conservative gloss, not decompilation.")
    return env
