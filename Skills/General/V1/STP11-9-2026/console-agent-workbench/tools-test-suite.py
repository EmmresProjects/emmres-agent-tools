#!/usr/bin/env python3
"""tools-test-suite.py - integration tests for the console-agent-workbench Skill Tool.

Every test drives the real command-line interface (``tools-console-agent.py``) in a
subprocess, in its own temporary workspace, and asserts on exit codes and the JSON
envelope. Fixtures are synthetic files generated at run time, so no copyrighted
material, firmware, or key material is included anywhere in this package.

Usage (bash):
    python3 console-agent-workbench/tools-test-suite.py            # run all tests
    python3 console-agent-workbench/tools-test-suite.py -k scan    # run matching tests
    python3 console-agent-workbench/tools-test-suite.py --keep     # keep temp workspaces

Exit code 0 means every test passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "tools-console-agent.py")
PY = sys.executable


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
class Failure(AssertionError):
    pass


def run(*args, workdir=None, expect=None, timeout=120):
    if workdir:
        os.makedirs(workdir, exist_ok=True)
    cmd = [PY, ENTRY, *[str(a) for a in args]]
    if workdir:
        cmd += ["--workdir", workdir]
    proc = subprocess.run(cmd, cwd=workdir or os.getcwd(), capture_output=True, text=True,
                          timeout=timeout)
    payload = None
    stripped = proc.stdout.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
    if expect is not None and proc.returncode != expect:
        raise Failure(f"expected exit {expect}, got {proc.returncode}\n"
                      f"cmd: {' '.join(cmd)}\nstdout: {proc.stdout[:1500]}\n"
                      f"stderr: {proc.stderr[:800]}")
    return proc.returncode, payload, proc.stdout, proc.stderr


def must(payload, *keys):
    node = payload
    for key in keys:
        if not isinstance(node, dict):
            raise Failure(f"expected object before key {key!r}, got {type(node).__name__}")
        if key not in node:
            raise Failure(f"missing key {key!r} (have {sorted(node)})")
        node = node[key]
    return node


def data(rc_payload):
    return must(rc_payload, "data")


# ---------------------------------------------------------------------------
# Fixtures (synthetic, tiny, and free of third-party content)
# ---------------------------------------------------------------------------
def w(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def fx_ines(path, mapper=2, prg=8, chr_=0, flags6=None, flags7=0x00, nes2=False, extra=0):
    if flags6 is None:
        flags6 = (mapper & 0x0F) << 4 | 0x01
    head = bytearray(b"NES\x1a")
    head += bytes([prg & 0xFF, chr_ & 0xFF, flags6, flags7, 0, 0, 0, 0, 0, 0, 0, 0])
    assert len(head) == 16
    return w(path, bytes(head) + bytes(prg * 16384 + chr_ * 8192 + extra))


def fx_nes2(path, mapper=4, prg=2, chr_=1):
    flags6 = ((mapper & 0x0F) << 4) | 0x01
    flags7 = 0x08 | (mapper & 0xF0)
    head = bytearray(b"NES\x1a")
    head += bytes([prg, chr_, flags6, flags7, 0, 0, 0x00, 0x00, 0, 0, 0, 0])
    return w(path, bytes(head) + bytes(prg * 16384 + chr_ * 8192))


def fx_fds(path, sides=2):
    return w(path, b"FDS\x1a" + bytes([sides]) + bytes(11) + bytes(65500 * sides))


def fx_snes(path, title=b"TEST GAME", mapping=0x20):
    size = 0x8000
    buf = bytearray(size)
    off = 0x7FC0 if mapping in (0x20, 0x22, 0x23, 0x30) else 0xFFC0
    if off + 0x20 > size:
        buf = bytearray(size * 2)
        off = 0xFFC0
    buf[off:off + 21] = title.ljust(21, b" ")
    buf[off + 0x15] = mapping
    buf[off + 0x17] = 0x08           # 256 KiB
    buf[off + 0x18] = 0x03
    buf[off + 0x19] = 0x01           # region
    buf[off + 0x1A] = 0x33
    buf[off + 0x1B] = 0x00           # version 1.0
    checksum = 0x1234
    buf[off + 0x1C] = (checksum ^ 0xFFFF) & 0xFF
    buf[off + 0x1D] = (checksum ^ 0xFFFF) >> 8
    buf[off + 0x1E] = checksum & 0xFF
    buf[off + 0x1F] = checksum >> 8
    return w(path, bytes(buf))


def fx_genesis(path, region=b"JUE"):
    buf = bytearray(0x200 + 4096)
    buf[0x100:0x110] = b"SEGA MEGA DRIVE "
    buf[0x110:0x120] = b"TEST GAME (JP)      "[:16]
    buf[0x120:0x150] = b"TEST GAME (US/EU)".ljust(48)
    buf[0x180:0x18E] = b"GM TEST-0001-00"
    buf[0x1F0:0x1F3] = region
    return w(path, bytes(buf))


GB_LOGO = bytes.fromhex(
    "CEED6666CC0D000B03730083000C000D0008111F8889000EDCCC6EE6DDDDD999BBBB67"
    "636E0EECCCDDDC999FBBB9333E")


def fx_gameboy(path, title=b"TESTGB", cart=0x1B, cgb=0x80):
    buf = bytearray(0x8000)
    buf[0x100:0x104] = b"\x00\xc3\x50\x01"
    buf[0x104:0x134] = GB_LOGO
    buf[0x134:0x144] = title.ljust(16, b"\x00")
    buf[0x143] = cgb
    buf[0x147] = cart
    buf[0x148] = 0x05               # 1 MiB
    buf[0x149] = 0x02               # 8 KiB
    buf[0x14A] = 0x01
    buf[0x14C] = 0x00
    chk = 0
    for b in buf[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    buf[0x14D] = chk
    return w(path, bytes(buf))


def fx_gba(path, title=b"TESTGBA"):
    buf = bytearray(0x400)
    buf[0:8] = bytes.fromhex("24FFAE51699AA221")
    buf[0xA0:0xAC] = title.ljust(12, b"\x00")
    buf[0xAC:0xB0] = b"AXYZ"
    buf[0xB0:0xB2] = b"01"
    buf[0xBC] = 0
    buf[0xBD] = (-(sum(buf[0xA0:0xBD]) + 0x19)) & 0xFF
    return w(path, bytes(buf))


def fx_n64(path):
    buf = bytearray(0x1000)
    buf[0:4] = bytes.fromhex("80371240")
    buf[0x08:0x0C] = struct.pack(">I", 0x000F3C2A)
    buf[0x18:0x1C] = struct.pack(">I", 0x80000400)
    buf[0x20:0x34] = b"TEST GAME     ".ljust(20)
    buf[0x3B:0x3F] = b"NTES"
    return w(path, bytes(buf))


def fx_iso(path, volume=b"TESTDISC", system=b"PLAYSTATION "):
    sector = 2048
    sectors = 20
    buf = bytearray(sector * sectors)
    pvd = 16 * sector
    buf[pvd] = 1
    buf[pvd + 1:pvd + 6] = b"CD001"
    buf[pvd + 6] = 1
    buf[pvd + 8:pvd + 40] = system.ljust(32)
    buf[pvd + 40:pvd + 72] = volume.ljust(32)
    buf[pvd + 80:pvd + 84] = struct.pack("<I", sectors)
    buf[pvd + 128:pvd + 132] = struct.pack("<I", sector)
    return w(path, bytes(buf))


def fx_zip(path, nested=False):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("notes/readme.txt", "operator notes\n" * 10)
        if nested:
            inner = os.path.join(os.path.dirname(path), "_inner.zip")
            with zipfile.ZipFile(inner, "w") as iz:
                iz.writestr("inner.txt", "inner\n")
            zf.write(inner, "nested/inner.zip")
            os.unlink(inner)
    return path


def fx_memcard(path):
    buf = bytearray(128 * 1024)
    buf[0:2] = b"MC"
    return w(path, bytes(buf))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_help_and_capabilities(ws):
    rc, payload, out, _ = run("help", expect=0)
    assert "usage: tools-console-agent.py" in out, out[:300]
    rc, payload, out, _ = run("help", "scan", expect=0)
    assert "--mode" in out, out[:300]
    rc, payload, _, _ = run("capabilities", expect=0)
    fams = must(payload, "data", "implemented_families")
    assert len(fams) == 9, len(fams)
    assert must(payload, "data", "catalog")["skill_count"] == 491
    rc, payload, _, _ = run("capabilities", "--include-unimplemented", expect=0)
    assert must(payload, "data", "not_implemented"), "expected explicit non-implemented list"
    rc, payload, _, _ = run("help", "nope")
    assert rc == 2, rc


@test
def test_catalog(ws):
    rc, payload, _, _ = run("catalog", expect=0)
    assert must(payload, "data", "skills") == 491
    assert must(payload, "data", "categories") == 36
    rc, payload, _, _ = run("catalog", "--category", "17", expect=0)
    assert must(payload, "data", "match_count") == 13
    rc, payload, _, _ = run("catalog", "--search", "hash", "--limit", "5", expect=0)
    matches = must(payload, "data", "matches")
    assert 0 < len(matches) <= 5
    rc, payload, _, _ = run("catalog", "--skill", "Repeated-Read Comparator", expect=0)
    skill = must(payload, "data", "matches")[0]
    assert skill["implemented_by"] == "verify --crosscheck", skill
    rc, payload, _, _ = run("catalog", "--implemented-only", "--limit", "200", expect=0)
    assert must(payload, "data", "match_count") >= 29
    rc, payload, _, _ = run("catalog", "--category", "999")
    assert rc == 2, rc
    rc, payload, _, _ = run("catalog", "--list-categories", expect=0)
    assert len(must(payload, "data", "categories")) == 36


@test
def test_inspect_formats(ws):
    cases = {
        fx_ines(os.path.join(ws, "a.nes")): "ines",
        fx_nes2(os.path.join(ws, "b.nes")): "ines",
        fx_fds(os.path.join(ws, "c.fds")): "fds",
        fx_snes(os.path.join(ws, "d.sfc")): "snes",
        fx_genesis(os.path.join(ws, "e.md")): "genesis",
        fx_gameboy(os.path.join(ws, "f.gb")): "gb",
        fx_gba(os.path.join(ws, "g.gba")): "gba",
        fx_n64(os.path.join(ws, "h.z64")): "n64",
        fx_iso(os.path.join(ws, "i.iso")): "iso9660",
        fx_zip(os.path.join(ws, "j.zip"), nested=True): "zip",
        fx_memcard(os.path.join(ws, "k.mcr")): "psx-memcard",
    }
    for path, expected in cases.items():
        rc, payload, _, err = run("inspect", "--path", path)
        art = data(payload)["artifacts"][0]
        assert art["detected_format"] == expected, f"{os.path.basename(path)}: {art['detected_format']} != {expected}\n{err}"
        assert art["hashes"]["sha256"], f"{path}: no sha256 recorded"
    rc, payload, _, _ = run("inspect", "--path", os.path.join(ws, "a.nes"), expect=0)
    details = data(payload)["artifacts"][0]["details"]
    assert details["mapper"] == 2 and details["size_consistent"] is True, details
    rc, payload, _, _ = run("inspect", "--path", os.path.join(ws, "b.nes"), expect=0)
    d2 = data(payload)["artifacts"][0]["details"]
    assert d2["container"] == "NES 2.0" and d2["mapper"] == 4, d2
    rc, payload, _, _ = run("inspect", "--path", os.path.join(ws, "f.gb"), expect=0)
    gb = data(payload)["artifacts"][0]["details"]
    assert gb["header_checksum_valid"] is True, gb
    rc, payload, _, _ = run("inspect", "--path", os.path.join(ws, "g.gba"), expect=0)
    gba = data(payload)["artifacts"][0]["details"]
    assert gba["header_complement_valid"] is True, gba
    rc, payload, _, _ = run("inspect", "--path", os.path.join(ws, "j.zip"), expect=0)
    zip_details = data(payload)["artifacts"][0]
    assert zip_details["details"]["nested_archives"], "nested archive not reported"
    unknown = w(os.path.join(ws, "mystery.dat"), b"\x00\x01\x02" * 500)
    rc, payload, _, _ = run("inspect", "--path", unknown)
    assert rc == 6, rc
    assert data(payload)["artifacts"][0]["detected_format"] == "unknown"


@test
def test_hash_manifest(ws):
    payload_bytes = b"console-agent-workbench hash fixture\n" * 100
    path = w(os.path.join(ws, "data", "fixture.bin"), payload_bytes)
    w(os.path.join(ws, "data", "other.bin"), b"other")
    out = os.path.join(ws, "manifest.json")
    rc, payload, _, _ = run("hash", "--path", os.path.join(ws, "data"), "--out", out, expect=0)
    entries = {e["path"]: e for e in data(payload)["entries"]}
    assert set(entries) == {"fixture.bin", "other.bin"}, set(entries)
    rec = entries["fixture.bin"]
    assert rec["hashes"]["sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    assert rec["hashes"]["md5"] == hashlib.md5(payload_bytes).hexdigest()
    assert rec["hashes"]["sha1"] == hashlib.sha1(payload_bytes).hexdigest()
    assert rec["hashes"]["crc32"] == f"{zlib.crc32(payload_bytes) & 0xFFFFFFFF:08x}"
    assert rec["size"] == len(payload_bytes)
    assert os.path.isfile(out), "manifest not written"
    rc, payload, _, _ = run("hash", "--path", os.path.join(ws, "data"),
                            "--algorithm", "blake2b", expect=0)
    assert list(data(payload)["entries"][0]["hashes"]) == ["blake2b"]
    rc, payload, _, _ = run("hash", "--path", os.path.join(ws, "data"),
                            "--algorithm", "sha3-nope")
    assert rc == 2, rc
    # hash cap skips content hashing but still records size
    rc, payload, _, _ = run("hash", "--path", path, "--hash-cap", "10", expect=0)
    entry = data(payload)["entries"][0]
    assert entry["hashes"] == {} and entry["size"] == len(payload_bytes), entry


@test
def test_verify_dat(ws):
    good = w(os.path.join(ws, "dumps", "good.nes"), b"good-dump-content")
    other = w(os.path.join(ws, "dumps", "unknown.nes"), b"unknown-dump-content")
    info = {}
    for name, path in (("good", good), ("unknown", other)):
        rc, payload, _, _ = run("hash", "--path", path, expect=0)
        info[name] = data(payload)["entries"][0]
    dat = {
        "dat_id": "test-dat", "name": "Test DAT", "version": "1.0", "entries": [{
            "name": "Good Dump (USA)", "system": "NES", "region": "USA", "revision": "1.0",
            "status": "verified", "size": info["good"]["size"],
            "sha256": info["good"]["hashes"]["sha256"],
            "sha1": info["good"]["hashes"]["sha1"],
            "crc32": info["good"]["hashes"]["crc32"],
        }]}
    dat_path = os.path.join(ws, "dat.json")
    with open(dat_path, "w", encoding="utf-8") as fh:
        json.dump(dat, fh)
    rc, payload, _, _ = run("verify", "--dat", dat_path, "--path", os.path.join(ws, "dumps"), expect=0)
    results = {r["path"]: r for r in data(payload)["results"]}
    assert results["good.nes"]["status"].startswith("verified"), results["good.nes"]
    assert results["good.nes"]["match"]["name"] == "Good Dump (USA)"
    assert results["good.nes"]["match_method"] == "sha256"
    assert results["unknown.nes"]["status"] == "unknown", results["unknown.nes"]
    rc, payload, _, _ = run("verify", "--dat", dat_path, "--path", os.path.join(ws, "dumps"),
                            "--require-match")
    assert rc == 6, rc
    # size mismatch is reported, not silently verified
    dat["entries"][0]["size"] = info["good"]["size"] + 1
    with open(dat_path, "w", encoding="utf-8") as fh:
        json.dump(dat, fh)
    rc, payload, _, _ = run("verify", "--dat", dat_path, "--path", good, expect=0)
    assert data(payload)["results"][0]["status"] == "size-mismatch"
    bad_dat = w(os.path.join(ws, "bad.json"), b'{"entries": [{"name": "x"}]}')
    rc, payload, _, _ = run("verify", "--dat", bad_dat, "--path", good)
    assert rc == 2, rc


@test
def test_crosscheck(ws):
    base = bytes(range(256)) * 40
    a = w(os.path.join(ws, "read1.bin"), base)
    b = w(os.path.join(ws, "read2.bin"), base)
    rc, payload, _, _ = run("verify", "--crosscheck", "--path", a, "--path", b, expect=0)
    assert data(payload)["consistent"] is True
    assert data(payload)["comparisons"][0]["identical"] is True
    mutated = bytearray(base)
    mutated[2048 + 7] ^= 0xFF
    c = w(os.path.join(ws, "read3.bin"), bytes(mutated))
    rc, payload, _, _ = run("verify", "--crosscheck", "--path", a, "--path", c)
    assert rc == 6, rc
    cmp = data(payload)["comparisons"][0]
    assert cmp["first_difference_offset"] == 2048 + 7, cmp
    assert cmp["differing_blocks"] == 1, cmp
    rc, payload, _, _ = run("verify", "--crosscheck", "--path", a)
    assert rc == 2, rc


@test
def test_scan_guardrails(ws):
    restricted = os.path.join(ws, "package")
    # Trigger-shaped fixtures are assembled at runtime: this file never ships a literal
    # key block, key blob name, or credential-shaped token of its own.
    pem_marker = b"-----BEGIN " + b"PRIVATE KEY-----"
    fake_token = ("tok" + "en=abcd" + "12345678").encode()
    w(os.path.join(restricted, "prod" + ".keys"), b"key" + b"blob")
    w(os.path.join(restricted, "secret.pem"), pem_marker + b"\nabc\n")
    w(os.path.join(restricted, "notes.txt"),
      b"contact operator@example.test " + fake_token + b"\n")
    w(os.path.join(restricted, "game.nes"), b"NES\x1a")
    rc, payload, _, _ = run("scan", "--path", restricted, "--mode", "package")
    assert rc == 3, rc
    result = data(payload)
    assert result["verdict"] == "block", result["verdict"]
    rules = result["findings_by_rule"]
    assert "console-key-material" in rules, rules
    assert "private-key-material" in rules, rules
    assert "private-key-block" in rules, rules
    assert "account-credential" in rules, rules
    assert "cartridge-rom-image" in rules, rules
    clean = os.path.join(ws, "clean")
    w(os.path.join(clean, "report.md"), b"nothing restricted here\n")
    rc, payload, _, _ = run("scan", "--path", clean, "--mode", "package", expect=0)
    assert data(payload)["verdict"] == "clear", data(payload)
    rc, payload, _, _ = run("scan", "--path", restricted, "--mode", "report", expect=0)
    assert data(payload)["verdict"] == "review", data(payload)
    rc, payload, _, _ = run("scan", "--path", os.path.join(ws, "missing"))
    assert rc == 5, rc
    # output file form
    out = os.path.join(ws, "scan.json")
    rc, payload, _, _ = run("scan", "--path", restricted, "--mode", "package", "--out", out)
    assert os.path.isfile(out)
    with open(out, encoding="utf-8") as fh:
        assert json.load(fh)["command"] == "scan"


@test
def test_redact(ws):
    src = os.path.join(ws, "notes.md")
    fake_token = "api_" + "token = " + "supersecret" + "token123"
    w(src, ("Operator: a.operator@example.test\n"
            "SDK root: /home/operator/vendor-sdk/tools/bin\n"
            + fake_token + "\n"
            "host 192.168.1.44\n").encode())
    out = os.path.join(ws, "notes.clean.md")
    rc, payload, _, _ = run("redact", "--in", src, "--out", out, expect=0)
    hits = data(payload)["hits"]
    assert hits.get("email-address") == 1, hits
    assert hits.get("home-paths", 0) + hits.get("windows-paths", 0) >= 1, hits
    assert hits.get("ipv4-address") == 1, hits
    with open(out, encoding="utf-8") as fh:
        cleaned = fh.read()
    for secret in ("a.operator@example.test", "/home/operator",
                   "supersecret" + "token123", "192.168.1.44"):
        assert secret not in cleaned, f"{secret} survived redaction: {cleaned}"
    rc, payload, _, _ = run("redact", "--in", src, "--check")
    assert rc == 3, rc
    clean_src = w(os.path.join(ws, "clean.txt"), b"nothing to hide\n")
    rc, payload, _, _ = run("redact", "--in", clean_src, "--check", expect=0)
    assert data(payload)["clean"] is True


@test
def test_gate_authorization(ws):
    work = os.path.join(ws, "work")
    authz = os.path.join(work, "authz.json")
    rc, payload, _, _ = run("gate", "--action", "authorize", "--operator", "Test Operator",
                            "--material", "media:owned-cartridge-01",
                            "--attest", "--out", authz, workdir=work, expect=0)
    record = data(payload)["record"]
    assert record["record_type"] == "authorization"
    assert os.path.isfile(authz)
    rc, payload, _, _ = run("gate", "--action", "verify", "--record", authz,
                            "--requires", "media", workdir=work, expect=0)
    assert data(payload)["valid"] is True
    rc, payload, _, _ = run("gate", "--action", "verify", "--record", authz,
                            "--requires", "keys", workdir=work)
    assert rc == 3, rc
    assert data(payload)["valid"] is False
    # tampering is detected
    with open(authz, encoding="utf-8") as fh:
        tampered = json.load(fh)
    tampered["items"].append({"material": "keys", "reference": "smuggled"})
    tamper_path = os.path.join(work, "tampered.json")
    with open(tamper_path, "w", encoding="utf-8") as fh:
        json.dump(tampered, fh)
    rc, payload, _, _ = run("gate", "--action", "verify", "--record", tamper_path, workdir=work)
    assert rc == 3, rc
    assert any("record_sha256" in p for p in data(payload)["problems"]), data(payload)
    # attestation is mandatory
    rc, payload, _, _ = run("gate", "--action", "authorize", "--operator", "Test Operator",
                            "--material", "media:x", workdir=work)
    assert rc == 2, rc
    rc, payload, _, _ = run("gate", "--action", "list", "--dir", work, expect=0)
    assert data(payload)["count"] >= 1


@test
def test_provenance_and_ledger(ws):
    work = os.path.join(ws, "work")
    material = fx_ines(os.path.join(ws, "dumps", "title.nes"))
    record_path = os.path.join(work, "record.json")
    ledger = os.path.join(work, "ledger.jsonl")
    rc, payload, _, _ = run("provenance", "--action", "create", "--material", material,
                            "--operator", "Test Operator", "--media-type", "cartridge",
                            "--region", "USA", "--revision", "1.0", "--method", "reader",
                            "--reader", "TestReader", "--hardware", "handheld",
                            "--firmware", "1.0", "--reads", "2",
                            "--software-version", "reader=2.1",
                            "--ledger", ledger, "--out", record_path, workdir=work, expect=0)
    record = data(payload)["record"]
    assert record["record_type"] == "provenance"
    assert record["integrity"]["sha256"] == hashlib.sha256(open(material, "rb").read()).hexdigest()
    assert record["material"]["detected_format"] == "ines"
    assert data(payload)["schema_valid"] is True
    rc, payload, _, _ = run("provenance", "--action", "validate", "--record", record_path,
                            workdir=work, expect=0)
    assert data(payload)["valid"] is True
    # invalid record is rejected with a JSON-pointer style problem list
    with open(record_path, encoding="utf-8") as fh:
        bad = json.load(fh)
    bad["integrity"]["sha256"] = "not-a-hash"
    del bad["operator"]
    bad_path = os.path.join(work, "bad-record.json")
    with open(bad_path, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    rc, payload, _, _ = run("provenance", "--action", "validate", "--record", bad_path, workdir=work)
    assert rc == 2, rc
    problems = data(payload)["problems"]
    assert any("sha256" in p["path"] for p in problems), problems
    assert any("operator" in p["message"] for p in problems), problems
    rc, payload, _, _ = run("provenance", "--action", "append", "--record", record_path,
                            "--ledger", ledger, "--operator", "Test Operator", workdir=work, expect=0)
    assert data(payload)["seq"] == 2, data(payload)
    rc, payload, _, _ = run("provenance", "--action", "verify-ledger", "--ledger", ledger,
                            workdir=work, expect=0)
    assert data(payload)["valid"] is True and data(payload)["entries"] == 2
    rc, payload, _, _ = run("provenance", "--action", "list", "--ledger", ledger, workdir=work, expect=0)
    assert len(data(payload)["records"]) == 2
    # tampering with a ledger line breaks the chain
    with open(ledger, encoding="utf-8") as fh:
        lines = fh.readlines()
    edited = json.loads(lines[0])
    edited["appended_by"] = "someone-else"
    lines[0] = json.dumps(edited) + "\n"
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    rc, payload, _, _ = run("provenance", "--action", "verify-ledger", "--ledger", ledger, workdir=work)
    assert rc == 6, rc
    assert data(payload)["valid"] is False
    assert any("entry_sha256" in p["problem"] for p in data(payload)["problems"]), data(payload)


@test
def test_plan(ws):
    rc, payload, _, _ = run("plan", "--goal", "preserve a cartridge", "--platform", "nes",
                            "--risk", "low", expect=0)
    plan = data(payload)
    assert [s["stage"] for s in plan["stages"]] == [
        "acquire", "preserve", "identify", "inspect", "reproduce", "test", "compare",
        "package", "handoff"]
    assert plan["rollback"] and plan["stop_conditions"]
    assert any("mapper" in item for s in plan["stages"] for item in s["checklist"]), \
        "NES addendum was not applied"
    rc, payload, _, _ = run("plan", "--goal", "x", "--stage", "acquire", "--stage", "package", expect=0)
    assert [s["stage"] for s in data(payload)["stages"]] == ["acquire", "package"]
    rc, payload, _, _ = run("plan", "--decompose", "--goal", "handoff a PS5 build", expect=0)
    assert len(data(payload)["stages"]) == 9
    rc, payload, _, _ = run("plan", "--goal", "x", "--stage", "bogus")
    assert rc == 2, rc
    rc, payload, _, _ = run("plan")
    assert rc == 2, rc
    out = os.path.join(ws, "plan.json")
    rc, payload, _, _ = run("plan", "--goal", "preserve", "--out", out, expect=0)
    assert os.path.isfile(out)


@test
def test_route(ws):
    rc, payload, _, _ = run("route", "--list-profiles", expect=0)
    assert len(data(payload)["profiles"]) >= 8
    rc, payload, _, _ = run("route", "--platform", "nes")
    nes = data(payload)
    assert nes["selected_profile"]["profile_id"] == "physical-console-lab"
    assert nes["status"] in ("ready", "requirements-unmet")
    if rc == 6:
        assert any(u["status"] == "PHYSICAL HARDWARE REQUIRED" for u in nes["unmet_requirements"])
    rc, payload, _, _ = run("route", "--platform", "ps5")
    ps5 = data(payload)
    assert ps5["selected_profile"]["profile_id"] == "official-playstation-partner"
    assert ps5["official_program_required"] is True
    assert ps5["unmet_requirements"], "PS5 routing must report unmet official requirements"
    assert rc == 6, rc
    rc, payload, _, _ = run("route", "--require", "hashing", expect=0)
    assert data(payload)["selected_profile"]["profile_id"] == "linux-x86_64-core"
    rc, payload, _, _ = run("route", "--platform", "dreamcast", "--require", "optical-dumping")
    assert data(payload)["selected_profile"]["profile_id"] == "physical-console-lab"
    rc, payload, _, _ = run("route", "--platform", "not-a-console")
    assert rc == 2, rc


@test
def test_report(ws):
    work = os.path.join(ws, "work")
    material = fx_gameboy(os.path.join(ws, "dumps", "cart.gb"))
    record_path = os.path.join(work, "record.json")
    scan_out = os.path.join(work, "scan.json")
    insp_out = os.path.join(work, "inspect.json")
    run("provenance", "--action", "create", "--material", material, "--operator", "Test Operator",
        "--media-type", "cartridge", "--out", record_path, workdir=work, expect=0)
    run("inspect", "--path", material, "--out", insp_out, workdir=work, expect=0)
    clean = os.path.join(ws, "pkg")
    w(os.path.join(clean, "readme.md"), b"metadata only\n")
    run("scan", "--path", clean, "--mode", "package", "--out", scan_out, workdir=work, expect=0)

    md_path = os.path.join(work, "report.md")
    rc, payload, _, _ = run("report", "--input", record_path, "--input", insp_out,
                            "--input", scan_out, "--operator", "Test Operator",
                            "--goal", "verify a cartridge", "--title", "Cartridge report",
                            "--limitation", "single reader used",
                            "--out", md_path, workdir=work, expect=0)
    assert os.path.isfile(md_path)
    with open(md_path, encoding="utf-8") as fh:
        md = fh.read()
    assert "# Cartridge report" in md
    assert "{{" not in md, "unresolved template placeholder"
    assert "Artifacts inspected" in md and "Guardrail findings" in md
    assert "sha256" not in md.lower() or "SHA-256" in md
    assert "single reader used" in md
    # no empty sections may survive
    assert "_None recorded._" not in md, "an empty section leaked into the report"
    html_path = os.path.join(work, "report.html")
    rc, payload, _, _ = run("report", "--input", record_path, "--input", insp_out,
                            "--input", scan_out, "--report-format", "html",
                            "--title", "Cartridge report", "--out", html_path,
                            workdir=work, expect=0)
    with open(html_path, encoding="utf-8") as fh:
        page = fh.read()
    assert page.startswith("<!DOCTYPE html>") and "<table>" in page
    assert "http://" not in page and "https://" not in page, "report must not reference external URLs"
    json_path = os.path.join(work, "report.json")
    rc, payload, _, _ = run("report", "--input", record_path, "--report-format", "json",
                            "--out", json_path, workdir=work, expect=0)
    with open(json_path, encoding="utf-8") as fh:
        assert json.load(fh)["report_id"]
    # redaction is applied to rendered output
    leaky = os.path.join(ws, "leaky.json")
    with open(leaky, "w", encoding="utf-8") as fh:
        json.dump({"command": "scan", "data": {"verdict": "clear", "findings": [], "mode": "bundle",
                                               "files_scanned": 1,
                                               "note": "/home/secret-operator/sdk path"},
                   "warnings": ["operator email a.b@example.test"]}, fh)
    red_path = os.path.join(work, "redacted.md")
    rc, payload, _, _ = run("report", "--input", leaky, "--out", red_path, workdir=work, expect=0)
    with open(red_path, encoding="utf-8") as fh:
        body = fh.read()
    assert "/home/secret-operator" not in body and "a.b@example.test" not in body, body[:800]
    # a template with an unsupported placeholder is rejected
    tpl = w(os.path.join(ws, "tpl.md"), b"# {{title}}\n{{unknown_section}}\n")
    rc, payload, _, _ = run("report", "--template", tpl, "--out", os.path.join(work, "x.md"),
                            workdir=work)
    assert rc == 2, rc


@test
def test_retention_with_approval(ws):
    work = os.path.join(ws, "work")
    evidence = os.path.join(ws, "evidence")
    os.makedirs(evidence, exist_ok=True)
    old_log = w(os.path.join(evidence, "logs", "run.log"), b"old log\n")
    old_capture = w(os.path.join(evidence, "captures", "shot.png"), b"\x89PNG\r\n\x1a\n")
    fresh = w(os.path.join(evidence, "logs", "fresh.log"), b"fresh log\n")
    records = w(os.path.join(evidence, "records", "record.json"), b"{}\n")
    old = 1_600_000_000  # 2020-09-13
    for path in (old_log, old_capture):
        os.utime(path, (old, old))
    rc, payload, _, _ = run("retention", "--action", "policy", expect=0)
    assert data(payload)["retention"]["classes"]
    rc, payload, _, _ = run("retention", "--action", "evaluate", "--dir", evidence,
                            workdir=work, expect=0)
    ev = data(payload)
    due = {f["path"] for f in ev["files"] if f["status"] == "due"}
    assert due == {"logs/run.log", "captures/shot.png"}, due
    assert "records/record.json" not in due

    # apply without approval is refused
    rc, payload, _, _ = run("retention", "--action", "apply", "--dir", evidence, workdir=work)
    assert rc == 4, rc
    # approval over a different scope is refused
    token_path = os.path.join(work, "approval-wrong-scope.json")
    run("gate", "--action", "approve", "--gated-action", "retention-apply",
        "--operator", "Test Operator", "--scope", records, "--out", token_path,
        workdir=work, expect=0)
    rc, payload, _, _ = run("retention", "--action", "apply", "--dir", evidence,
                            "--approval", token_path, workdir=work)
    assert rc == 4, rc
    assert any("scope" in p for p in data(payload)["problems"]), data(payload)

    # correct approval quarantines reversibly
    token_path = os.path.join(work, "approval.json")
    rc, payload, _, _ = run("gate", "--action", "approve", "--gated-action", "retention-apply",
                            "--operator", "Test Operator", "--scope", evidence,
                            "--out", token_path, workdir=work, expect=0)
    rc, payload, _, _ = run("retention", "--action", "apply", "--dir", evidence,
                            "--approval", token_path, workdir=work, expect=0)
    applied = data(payload)
    assert applied["moved_count"] == 2, applied
    assert applied["mode"] == "quarantine"
    assert not os.path.exists(old_log), "due log was not quarantined"
    assert os.path.exists(fresh), "fresh log must stay"
    quarantine = applied["quarantine"]
    assert os.path.isfile(os.path.join(quarantine, "logs", "run.log"))

    # recovery path: restore returns the files byte for byte
    rc, payload, _, _ = run("retention", "--action", "restore", "--dir", quarantine,
                            workdir=work, expect=0)
    restored = data(payload)
    assert restored["restored_count"] == 2, restored
    assert os.path.isfile(old_log)
    with open(old_log, "rb") as fh:
        assert fh.read() == b"old log\n"

    # hard delete needs its own gated action name
    rc, payload, _, _ = run("retention", "--action", "apply", "--dir", evidence,
                            "--hard-delete", "--approval", token_path, workdir=work)
    assert rc == 4, rc


@test
def test_inventory_and_selftest(ws):
    work = os.path.join(ws, "work")
    rc, payload, _, _ = run("inventory", "--priority", "P0", "--no-hardware", workdir=work)
    caps = data(payload)["capabilities"]
    assert caps and all(c["priority"] == "P0" for c in caps), caps
    for cap in caps:
        for field in ("capability_id", "status", "command_or_probe", "detected_version",
                      "host_type", "architecture", "license_state", "hardware_state",
                      "required_user_material", "test_result", "evidence_path",
                      "recommended_action"):
            assert field in cap, f"{cap['capability_id']} missing contract field {field}"
        assert cap["status"] in (
            "FOUND", "MISSING", "WRONG VERSION", "BROKEN", "BIOS OR FIRMWARE REQUIRED FROM USER",
            "USER-OWNED MEDIA REQUIRED", "OFFICIAL SDK REQUIRED", "LICENSE REQUIRED",
            "WINDOWS HOST REQUIRED", "MACOS HOST REQUIRED", "PHYSICAL HARDWARE REQUIRED",
            "OFFICIAL DEVKIT REQUIRED", "GPU REQUIRED", "FPGA HARDWARE REQUIRED",
            "VIRTUALIZATION REQUIRED", "NETWORK REQUIRED"), cap["status"]
    out = os.path.join(work, "inventory.json")
    rc, payload, _, _ = run("inventory", "--priority", "P0", "--out", out, workdir=work)
    assert os.path.isfile(out)
    rc, payload, _, _ = run("inventory", "--max-probes", "1", workdir=work)
    probes = [c for c in data(payload)["capabilities"] if c["capability_class"] != "hardware"]
    assert len(probes) == 1, probes

    evidence = os.path.join(work, "evidence", "custom-selftest.json")
    rc, payload, _, _ = run("selftest", "--gaps", "--out", evidence, workdir=work, expect=0)
    summary = data(payload)["summary"]
    assert summary["failed"] == 0, data(payload)["checks"]
    assert os.path.isfile(evidence)
    with open(evidence, encoding="utf-8") as fh:
        body = json.load(fh)
    assert body["command"] == "selftest" and body["data"]["checks"]


@test
def test_error_handling(ws):
    rc, payload, _, _ = run("inspect", "--path", os.path.join(ws, "nope.nes"))
    assert rc == 5, rc
    assert payload["errors"], payload
    rc, payload, _, _ = run("hash", "--path", os.path.join(ws, "nope"))
    assert rc == 5, rc
    rc, payload, _, _ = run("inspect")           # missing required argument
    assert rc == 2, rc
    rc, payload, _, _ = run("gate", "--action", "verify")   # missing --record
    assert rc == 2, rc
    rc, payload, _, _ = run("gate", "--action", "authorize", "--operator", "x",
                            "--material", "media:x", "--attest", "--ttl-hours", "0")
    assert rc == 2, rc
    rc, payload, _, _ = run("provenance", "--action", "create", "--operator", "x")
    assert rc == 2, rc
    rc, payload, _, _ = run("provenance", "--action", "create", "--material", "/etc/hostname",
                            "--operator", "x", "--software-version", "not-a-pair")
    assert rc == 2, rc
    rc, payload, _, _ = run("route", "--require", "quantum-telemetry")
    assert rc == 2, rc
    rc, payload, _, _ = run("retention", "--action", "apply", "--dir", os.path.join(ws, "missing"))
    assert rc == 5, rc
    # errors stay structured JSON with a stable envelope
    assert set(payload) >= {"ok", "skill", "command", "exit_code", "errors", "data"}


@test
def test_no_state_leaks_into_skill_folder(ws):
    before = sorted(os.listdir(HERE))
    run("selftest", "--out", os.path.join(ws, "evidence.json"), workdir=os.path.join(ws, "work"),
        expect=0)
    run("inventory", "--priority", "P0", workdir=os.path.join(ws, "work"))
    after = sorted(os.listdir(HERE))
    assert before == after, f"commands wrote into the Skill Tool folder: {set(after) - set(before)}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Run the console-agent-workbench test suite.")
    parser.add_argument("-k", "--filter", default=None, help="only run tests containing this text")
    parser.add_argument("--keep", action="store_true", help="keep temporary workspaces")
    args = parser.parse_args()
    tests = [t for t in TESTS if not args.filter or args.filter in t.__name__]
    if not tests:
        sys.stderr.write(f"no tests matched {args.filter!r}\n")
        return 2
    failures = []
    root = tempfile.mkdtemp(prefix="caw-tests-")
    for test_fn in tests:
        ws = os.path.join(root, test_fn.__name__)
        os.makedirs(ws, exist_ok=True)
        try:
            test_fn(ws)
            print(f"PASS {test_fn.__name__}")
        except Exception as exc:                  # noqa: BLE001 - the runner reports everything
            failures.append((test_fn.__name__, exc))
            print(f"FAIL {test_fn.__name__}: {exc}")
            if os.environ.get("CAW_TEST_TRACE"):
                traceback.print_exc()
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} tests passed")
    if failures:
        print("\nfailures:")
        for name, exc in failures:
            print(f"  - {name}: {exc}")
    if args.keep:
        print(f"\nworkspaces kept in {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
